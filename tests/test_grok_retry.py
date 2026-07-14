"""Retry behavior for upstream content-encoding failures.

2026-07: the upstream proxy intermittently declares a Content-Encoding it does
not honor, so httpx raises DecodingError ("incorrect header check") and the
whole web_search fails hard. The fix retries such failures and downgrades the
request to Accept-Encoding: identity so the retry bypasses upstream
compression entirely. These tests pin both halves.
"""

import httpx
import pytest

from grok_search.providers import grok


def test_decoding_error_is_retryable():
    assert grok._is_retryable_exception(httpx.DecodingError("incorrect header check"))


def test_degrade_helper_sets_identity_encoding():
    headers = {"Authorization": "Bearer k"}
    grok._degrade_to_identity_encoding(headers)
    assert headers["Accept-Encoding"] == "identity"


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "hello from grok"}}]}


class _FlakyEncodingClient:
    """First post raises DecodingError; later posts succeed and record headers."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _FlakyEncodingClient.calls.append(dict(headers or {}))
        if len(_FlakyEncodingClient.calls) == 1:
            raise httpx.DecodingError("incorrect header check")
        return _FakeResponse()


@pytest.mark.asyncio
async def test_non_stream_retries_decoding_error_with_identity_encoding(monkeypatch):
    monkeypatch.setenv("GROK_RETRY_MULTIPLIER", "0")
    monkeypatch.setenv("GROK_RETRY_MAX_WAIT", "1")
    _FlakyEncodingClient.calls = []
    monkeypatch.setattr(grok.httpx, "AsyncClient", _FlakyEncodingClient)

    provider = grok.GrokSearchProvider("http://upstream.test/v1", "test-key")
    headers = {"Authorization": "Bearer test-key", "Content-Type": "application/json"}
    content = await provider._execute_non_stream_with_retry(
        headers, {"model": "m", "messages": [], "stream": False}
    )

    assert content == "hello from grok"
    assert len(_FlakyEncodingClient.calls) == 2
    assert "Accept-Encoding" not in _FlakyEncodingClient.calls[0]
    assert _FlakyEncodingClient.calls[1].get("Accept-Encoding") == "identity"
