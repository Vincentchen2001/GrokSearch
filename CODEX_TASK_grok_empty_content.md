# Codex Task — Root-cause grok-search empty `content` (and slowness)

> Handoff from a prior Claude investigation. The empty-content cause is already
> isolated to the **upstream grok2api**, NOT the client. Your job: get onto the
> remote node, find out **why grok2api emits empty responses**, prove it with
> logs, and propose/apply the fix. Do NOT redo the ruled-out items below.

## TL;DR of the bug
`mcp__grok-search__web_search` frequently returns empty `content`. Reproduced by
curling the remote grok2api **directly** (bypassing local MCP + SSH tunnel) at
`http://127.0.0.1:18000/v1/chat/completions`. grok2api keeps returning **empty
shells**: HTTP 200, but `choices[0].message.content == ""` AND
`reasoning_content == ""`, returned in ~0.8s, SSE with only 2 lines (role delta +
`[DONE]`). 0.8s is far too fast for a real grok generation (successful calls take
18–80s), so grok2api is almost certainly short-circuiting without a valid upstream
answer. Empty shells are **time-clustered** ("bad windows").

## Evidence already collected (remote-direct curl, same query)
| model | stream | result |
|---|---|---|
| `grok-4.20-fast` (reasoning) | true | 5 calls → 3 OK (18–80s, content 183–279 chars) + 2 empty shells (0.8–1.2s) |
| `grok-4.20-0309-non-reasoning` | false | 3 calls → 1 OK (15.9s, 83 chars) + 2 empty shells (0.8s) |
| `grok-4.20-0309-non-reasoning` | true | 1 call → empty shell (0.8s) |

## ALREADY RULED OUT — do NOT re-investigate
- NOT local MCP parsing — remote-direct curl is also empty.
- NOT stream vs non-stream — both produce empty shells.
- NOT model choice — reasoning and non-reasoning both empty.
- NOT the SSH tunnel — remote-direct (tunnel bypassed) is still empty.
- NOT the local fork's uncommitted changes (a non-stream switch).
- `web_fetch` does NOT use grok at all. It uses Tavily extract (60s) → Firecrawl
  scrape (90s + retries) in `server.py:web_fetch`. Its slowness is a SEPARATE
  chain; do not conflate it with the empty-content bug.

## System topology
- Local MCP server: `/Users/vincentchen/Documents/GitHub/GrokSearch` (editable
  `.venv`), launched by
  `/Users/vincentchen/Personal/Vivi自建家宽节点/deploy/grok-search/run-local-mcp-via-remote-api.sh`.
- That launcher opens an autossh tunnel: local random port → remote
  `127.0.0.1:18000`. SSH host alias = `vivitw-via-neburst`.
- Remote grok2api (chenyme deploy): listens on `127.0.0.1:18000`, deploy dir
  `/opt/vivi/grok2api/chenyme-deploy/`, config
  `/opt/vivi/grok2api/chenyme-deploy/data/config.toml` (OpenAI-compatible
  `app.api_key` is there). Launcher also sources `/opt/vivi/grok-search/mcp.env`.

## Your investigation (RUN ON THE REMOTE NODE)
Pre-check: `ssh -o BatchMode=yes -o ConnectTimeout=10 vivitw-via-neburst true`
must succeed. Then `ssh vivitw-via-neburst`.

1. **Reproduce + capture grok2api logs at the same time.** Get the key:
   ```
   KEY=$(python3 -c "import tomllib;print(tomllib.load(open('/opt/vivi/grok2api/chenyme-deploy/data/config.toml','rb'))['app']['api_key'])")
   ```
   Fire a burst until empty shells appear:
   ```
   for i in $(seq 1 10); do
     curl -s -m 90 http://127.0.0.1:18000/v1/chat/completions \
       -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
       -d '{"model":"grok-4.20-fast","messages":[{"role":"user","content":"用三句话介绍杭州西湖"}],"stream":false}' \
       | python3 -c "import sys,json;d=json.load(sys.stdin);m=(d.get('choices') or [{}])[0].get('message',{});print('content_len',len(m.get('content') or ''),'reason_len',len(m.get('reasoning_content') or ''))"
   done
   ```
2. **Find how grok2api runs and tail its logs.** Likely docker compose:
   `cd /opt/vivi/grok2api/chenyme-deploy && docker compose ps && docker compose logs --tail=300 -f`
   (fallback: `docker ps`, `systemctl status`, or a `logs/` dir). Correlate one
   empty-shell response with its log lines.
3. **Answer WHY the shell is empty** for at least one reproduced empty call:
   - Did grok2api actually call upstream grok, or short-circuit? (0.8s ⇒ likely no
     real upstream generation.)
   - What upstream status/body did grok2api receive? rate-limit / 401 / 403 /
     empty / safety refusal / token expired?
   - Which account/SSO token from the pool was used? Is the pool
     exhausted / cooling-down / all-invalid during the bad windows?
   - Does grok2api have a cooldown / circuit-breaker / concurrency cap that
     returns an empty 200 instead of a proper error?
4. **Check account/token health & config**: inspect `config.toml` and the chenyme
   grok2api web admin (token list, quota, ban/cooldown state).
5. **Report** the true cause with quoted log evidence, and whether it's transient
   (token cooldown / rate limit) or persistent (all tokens dead). Then propose the
   fix (refresh/rotate/add tokens? raise concurrency? make grok2api surface errors
   instead of empty 200?).

## Acceptance criteria
- A concrete, log-backed reason for the empty shells (quote grok2api log lines +
  upstream status for ≥1 reproduced empty shell).
- A fix or precise fix plan; verify by re-running the step-1 burst ~20× and
  reporting empty-shell rate before vs after.

## OPTIONAL client-side mitigations (apply only if the user asks; state a plan first)
In `/Users/vincentchen/Documents/GitHub/GrokSearch`:
- `src/grok_search/providers/grok.py`: make "HTTP 200 but empty content"
  retryable. `_is_retryable_exception` currently only retries network errors /
  5xx / 429. Add `class _EmptyUpstreamError(Exception)`; in both
  `_execute_stream_with_retry` and `_execute_non_stream_with_retry`, after building
  `content`, `if not content.strip(): raise _EmptyUpstreamError()`; add
  `isinstance(exc, _EmptyUpstreamError)` to `_is_retryable_exception`. Keep backoff
  + attempt cap (clustered bad windows won't be saved by retries). Non-stream:
  also `content = message.get("content") or message.get("reasoning_content") or ""`.
- Prefer streaming: the launcher forces `GROK_STREAM=false`
  (`run-local-mcp-via-remote-api.sh:114`); set the default back to `true` (faster
  first byte, progressive output). Non-stream gained nothing here.
- `server.py` `web_fetch` slowness: `_call_tavily_extract` (60s) runs serially
  before Firecrawl; shorten timeout or run them concurrently and take first success.
- `src/grok_search/logger.py`: `log_info` only writes to file when
  `is_debug=True`, so the log files are all 0 bytes. Make WARNING/ERROR always
  persist so this is debuggable next time.

## Separate issue (only relevant to "streaming felt unstable")
Many stale grok-search processes + autossh tunnels are accumulated, including a
7-day-old one (`-L 18000:127.0.0.1:18000`). If streaming disconnects, suspect the
tunnel layer, not the API. Cleaning stale tunnels/processes is worthwhile but is
unrelated to the empty-content root cause.
