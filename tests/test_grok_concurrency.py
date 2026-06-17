import pytest

from grok_search import server
from grok_search.providers import grok


def test_default_grok_limiter_is_not_single_global_lock(monkeypatch):
    monkeypatch.delenv("GROK_GLOBAL_LOCK_ENABLED", raising=False)
    monkeypatch.delenv("GROK_MAX_CONCURRENT_REQUESTS", raising=False)

    assert grok.config.grok_global_lock_enabled is False
    assert grok.config.grok_max_concurrent_requests == 32


def test_file_slot_limiter_allows_multiple_concurrent_holders(tmp_path):
    lock_path = tmp_path / "grok.lock"
    first_fd, first_path = grok._acquire_file_slot(str(lock_path), slots=2, timeout_s=0.1, poll_s=0.01)
    second_fd, second_path = grok._acquire_file_slot(str(lock_path), slots=2, timeout_s=0.1, poll_s=0.01)
    try:
        assert first_path != second_path
        with pytest.raises(TimeoutError):
            grok._acquire_file_slot(str(lock_path), slots=2, timeout_s=0.05, poll_s=0.01)
    finally:
        grok._release_file_slot(second_fd)
        grok._release_file_slot(first_fd)

    third_fd, third_path = grok._acquire_file_slot(str(lock_path), slots=2, timeout_s=0.1, poll_s=0.01)
    assert third_path in {first_path, second_path}
    grok._release_file_slot(third_fd)


@pytest.mark.asyncio
async def test_web_search_returns_explicit_error_when_grok_fails(monkeypatch):
    monkeypatch.setenv("GROK_API_URL", "http://example.test/v1")
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    class FailingProvider:
        last_sources = []

        def __init__(self, *_args, **_kwargs):
            pass

        async def search(self, _query, _platform):
            raise TimeoutError("upstream saturated")

    monkeypatch.setattr(server, "GrokSearchProvider", FailingProvider)

    out = await server.web_search("world cup odds")

    assert out["content"].startswith("GROK_SEARCH_FAILED:")
    assert "upstream saturated" in out["content"]
    assert out["grok_error"].startswith("TimeoutError:")
    assert out["sources_count"] == 0
