# MLX TUI v0 — status bar + instrumented chat

**Date:** 2026-08-25
**Work Item:** n/a (implements §v0 of `docs/idea.md`)
**Status:** Complete

## Overview
Build v0 of the MLX TUI: a one-screen Textual app with an always-visible status bar (liveness dot, loaded model id, RSS, available memory, port) polling every 2s, and a chat pane streaming `/v1/chat/completions` with per-turn TTFT/tok-s instrumentation. Purpose: answer "is a permanently-visible status bar worth a terminal split?" by running it for a week in a tmux pane.

## Current State
- Repo is a fresh `uv` scaffold: Python 3.13 (`.python-version`), `textual>=8.2.8`, `ruff>=0.16.4`, `pyrefly>=1.2.0`, `pytest>=9.1.1` already in `pyproject.toml:7-12`. `httpx` and `psutil` are **not** yet dependencies.
- `src/` contains only a stray `src/__init__.py`; no application code exists.
- `main.py` is a hello-world stub; `README.md` contains only the text `mlx tui`.
- `pyrefly` runs **strict** over `src/` and `tests/` only (`pyproject.toml:21-26`) — all shipped code must live under those paths to be type-checked.
- Ruff line length 88, select includes `E4/E7/E9/F/I/PL/UP/TID/ASYNC/DTZ` (`pyproject.toml:14-19`).

### Verified server behaviour (research replacing local curl checks)
Against current `mlx-lm` (`mlx_lm/server.py` on main) — the three "verify before writing code" checks from the idea doc:
1. **On-demand load:** the request body accepts an optional `model` field; a differing id triggers a synchronous reload inside the server process (`server.py:369-378`). Irrelevant to v0 (no swap); good news for v1.
2. **Streamed usage:** sending `"stream_options": {"include_usage": true}` makes the server append one final SSE chunk with empty `choices` and populated `usage` (`prompt_tokens`/`completion_tokens`/`total_tokens`), followed by `data: [DONE]` (`server.py:1111, 1485-1495, 1523-1546`). No tokenizer needed against mlx-lm.
3. **mlx-vlm parity:** both servers expose `/v1/models` + `/v1/chat/completions` with SSE streaming; mlx-vlm adds extra endpoints (`/health`, `/unload`). v0 targets the common surface.

**Contradiction found vs idea doc:** mlx-lm's `/v1/models` lists the repos in the local HF cache that pass its `probably_mlx_lm()` heuristic (`config.json` + `model.safetensors.index.json` + `tokenizer_config.json` present), plus the startup model path — not the currently-loaded model (`server.py:1626-1662`). The status bar therefore derives "loaded model id" from the server process's `--model` CLI argument via `psutil`, not from `/v1/models`.

SSE wire format (confirmed): chunks are `data: {json}\n\n`, stream ends with `data: [DONE]\n\n`. **Keepalive frames exist:** during long prompt processing the server emits SSE comment frames `": keepalive {processed}/{total}\n\n"` (`server.py:1362-1367`) — parsers must ignore any line that is empty or does not start with `data: `. First delta may carry role only (no text).

Rendering fact (verified empirically against installed textual 8.2.8): consecutive `RichLog.write()` calls with no trailing newline are **each rendered as their own line** — they never merge into one growing line. Per-delta writes would print one line per token, so streaming uses an accumulated-text `Static` instead (see Design Decisions and Phase 3).

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Loaded-model id from `psutil` cmdline (`--model` arg of the matched server process), fallback `—` | `/v1/models` first row; always show "unknown" | `/v1/models` lists the HF cache against mlx-lm — showing its first row would be exactly the kind of lie the app exists to prevent; cmdline is honest when the user starts the server themselves (the expected workflow) |
| Streamed reply rendered in a `Static(id="chat-stream")` fed with accumulated text, throttled to ~10 Hz; completed turn appended to `RichLog` scrollback | per-delta `RichLog.write(text)` | verified on textual 8.2.8: consecutive partial `RichLog.write()`s render as separate lines (one line per token); throttled accumulation gives one growing block at ~10 Hz, imperceptible for eyeballing speed |
| Liveness: red = connect error/timeout only; amber = any complete HTTP response whose body isn't valid MLX models JSON (200-with-junk, 404, 502 HTML, …); green = 200 + non-empty `data` list | non-200 always red; any HTTP response green | idea doc explicitly requires a 502-HTML proxy squatting on the port to read amber; red stays reserved for "no answer at all", matching what a real client would experience |
| Cold stamp arms **only** on a true green→red→green cycle (a green must have been observed before the red) | arm on every transition into green incl. app startup | first turn after TUI startup against an hours-warm server isn't cold; arming on the startup edge would mislabel it |
| Proper package: `src/mlx_tui/` + hatchling + `[project.scripts] mlx-tui` | keep everything in root `main.py`; single file at repo root | pyrefly strict only covers `src`/`tests`; tests must import the app; `uv run mlx-tui --port 8080` beats `python main.py` |
| Status poll = `async def` on `set_interval(2.0)` with in-flight guard, async `httpx` | thread worker for polls | no cross-thread widget writes for the status bar; psutil fast path is cheap once pid is cached; guard prevents socket pile-up on a wedged server (idea doc "Poll overlap") |
| Chat stream = sync `httpx` in `@work(exclusive=True, thread=True)` worker, UI updates via `call_from_thread` | async worker in event loop | mandated by idea doc ("Threading"); widgets are not thread-safe — this is the classic Textual hang, avoided by construction |
| Tokens from `stream_options.include_usage`; fallbacks labelled `(est)` | always `len/3.5` | usage is confirmed free on mlx-lm; estimate kept as fallback so the client stays OpenAI-generic |
| TTFT = clock at first chunk carrying non-empty `delta.content` | first byte; first chunk | role-only first deltas exist; definition fixed now per idea doc ("TTFT definition") so cold-vs-warm comparisons never move |
| One integration test file: real stub HTTP server on ephemeral port driven through Textual's `run_test()` pilot | unit tests with mocked internals; no tests | user requirement: high-level, low-coupling component test over a real HTTP boundary; zero new deps (`asyncio.run` inside sync test) |
| App logic concentrated in one module `src/mlx_tui/app.py` (~250 lines) | literal single file at repo root | honours the spirit of the idea doc's "~200 lines, everything fits on one screen" while keeping code inside pyrefly/ruff coverage |

## Implementation Phases

### Phase 1: Packaging + app skeleton
Make the repo installable and put a running, empty TUI on screen.

> Deviation: pyrefly `strict` requires an `@override` decorator on `compose()` — added (`from typing import override`).

**Changes:**
- `pyproject.toml` — add to `[project]` dependencies: `"httpx>=0.28"`, `"psutil>=7.0"`; add sections:
  ```toml
  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [tool.hatch.build.targets.wheel]
  packages = ["src/mlx_tui"]

  [project.scripts]
  mlx-tui = "mlx_tui.app:main"
  ```
- delete `src/__init__.py` (stray scaffold file; would make `src` importable as a confusing top-level package)
- create empty `src/mlx_tui/__init__.py`
- create `src/mlx_tui/app.py` with:
  - `class MlxTuiApp(App[None])` — `__init__(self, host: str = "127.0.0.1", port: int = 8080) -> None` storing both; `BINDINGS = [("ctrl+q", "quit", "Quit")]`
  - `compose()` returning: a `StatusBar(Static)` docked top (placeholder text `● :{port}`), and a `Vertical` containing `Static(id="chat-stream")` (in-flight streaming text; empty at start), above `RichLog(id="chat-log", markup=False, wrap=True)` (expand), above `Input(placeholder="message…", id="chat-input")`. Chat log uses `markup=False` because user/assistant text may contain Rich markup metacharacters (`[`, `]`); styled system lines are written as `rich.text.Text` objects instead. The status bar keeps `markup=True` (its content is fully app-controlled).
  - inline `CSS` for layout (status bar full width top; log takes remaining height)
  - `def main() -> None` — `argparse` with `--host` (default `"127.0.0.1"`) and `--port` (default `8080`, `type=int`), then `MlxTuiApp(host=..., port=...).run()`
- replace `main.py` body with a shim: `from mlx_tui.app import main` / `if __name__ == "__main__": main()`
- replace `README.md` content with title, one-line pitch, and run instructions (`uv sync`, `uv run mlx-tui --port 8080`)

**Success Criteria:**

#### Automated Verification:
- [x] deps resolve and project installs: `uv sync`
- [x] script entry works: `uv run mlx-tui --help` prints usage with `--host`/`--port`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui` opens the TUI: status bar placeholder visible, empty chat log, input focused; `ctrl+q` quits

### Phase 2: Status bar polling
The app's reason to exist: liveness dot, loaded model id, RSS, available memory, refreshed every 2s.

**Changes (all in `src/mlx_tui/app.py`):**
- instance state: `self._http: httpx.AsyncClient` created in `on_mount` (with `aclose()` in `on_unmount`), `self._poll_in_flight: bool = False`, `self._pid_cache: int | None = None`, and liveness/cold bookkeeping — `self.status_state: str = "red"`, `self._ever_green: bool = False`, `self._red_since_green: bool = False`, `self._cold_pending: bool = False`
- `async def _poll(self) -> None` — wired via `self.set_interval(2.0, self._poll)` in `on_mount`:
  - overlap guard: if `self._poll_in_flight`, return immediately; set it `True`, reset in `finally`
  - classify liveness from `GET http://{host}:{port}/v1/models` with `timeout=0.5`:
    - exception raised (connect error, timeout, any transport failure — no response received) → `"red"`
    - a complete HTTP response arrived whose body is not valid JSON, or JSON lacks `data`, or `data` is not a non-empty list → `"amber"` regardless of status code (a proxy answering 502 HTML or an unrelated service squatting on the port reads amber; red stays reserved for "no answer at all")
    - 200 with parsed JSON whose `data` is a non-empty list → `"green"`
  - cold arming on each classified result: if green → set `_ever_green = True`; if `_red_since_green`, set `_cold_pending = True` and clear `_red_since_green`. If red → only when `_ever_green`, set `_red_since_green = True`. Amber changes nothing. (`_cold_pending` is consumed — reset to `False` — by the next submitted chat turn.)
  - pid discovery: `def _find_server_pid(self) -> int | None` — if `self._pid_cache` is set and that pid still exists (`psutil.Process(pid).is_running()`, catching `psutil.NoSuchProcess`), return it; else scan `psutil.process_iter(["pid", "cmdline"])` for the first process whose cmdline contains any token ending in `mlx_lm.server` or `mlx_vlm.server`, cache and return it, else cache `None` and return `None` (one full scan only when the cached pid dies — never every tick)
  - model id: from the cached process's `cmdline()` — element after `--model` if present, else `None`
  - memory: `rss_gib = psutil.Process(pid).memory_info().rss / 2**30` when pid alive; `vm = psutil.virtual_memory()` → `avail_gib`, `total_gib`
  - `status_state` attribute updated alongside render (test hook)
  - `_render_status(...)` writes one Rich-markup line into the `StatusBar`:
    `[green]●[/] {model|—} · RSS {rss:.1f} GB · avail {avail:.1f}/{total:.1f} GB · :{port}`
    with dot colour green/amber/red from classification; missing pieces render as `—`

**Success Criteria:**

#### Automated Verification:
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
One-time setup (Apple Silicon required):
```
uv tool install mlx-lm
mlx_lm.server --model mlx-community/Qwen3-1.7B-8bit --port 8080   # ~1.9 GB download on first start; any repo ≤ 8 GB works
```
- [ ] `uv run mlx-tui --port 8080` shows a solid green dot and the repo id `mlx-community/Qwen3-1.7B-8bit` within ~2s
- [ ] send one chat request (any client, e.g. curl) → RSS number climbs over the following requests (lazy-touch caveat from the idea doc)
- [ ] stop the server (Ctrl+C in its terminal) → dot turns red within ≤ ~2.5s, no crash, no socket pile-up
- [ ] restart server → dot recovers to green with correct model id
- [ ] amber path: run `python3 -m http.server 8081` and `uv run mlx-tui --port 8081` → dot is **amber** (HTTP OK but body isn't MLX JSON)

### Phase 3: Chat streaming + instrumentation
Chat as an instrument: streamed reply plus a stamped measurement line per turn.

**Changes (all in `src/mlx_tui/app.py`):**
- instance state: `self.messages: list[dict[str, str]] = []`, `self._cancel_requested: bool = False`, `self._active_stream: httpx.Response | None = None` (set inside the worker once the stream is open; cleared in its `finally`), plus the Phase 2 cold bookkeeping. Worker-local: `t_first_text: float | None`, accumulated reply parts, last-flush timestamp.
- `Input.Submitted` handler: append user message to `self.messages`, write `you › {text}` to the chat `RichLog` via `log.write(Text(...))` (plain text), reset `_cold_pending = False` **after** capturing its value for this turn, disable the `Input`, clear `_cancel_requested = False`, start the worker
- `@work(exclusive=True, group="chat", thread=True) def _run_turn(self, messages: list[dict[str, str]]) -> None`:
  - payload: `{"messages": messages, "stream": true, "max_tokens": 512, "stream_options": {"include_usage": true}}` (no `model` field — server serves its startup model)
  - `t_send = time.perf_counter()`; `httpx.Client` with `timeout=httpx.Timeout(connect=5.0, read=300.0, write=5.0, pool=5.0)`; `client.stream("POST", url, json=payload)`; on entering the stream context assign `self._active_stream = response`
  - iterate `response.iter_lines()`; for each line: skip empty lines and any line not starting with `data: ` (mlx-lm emits `": keepalive {n}/{total}"` SSE comments mid-prompt-processing — verified upstream `server.py:1362-1367`); strip the `data: ` prefix; `[DONE]` ends the loop; wrap each remaining line's `json.loads` in try/except and skip malformed lines rather than crashing (`exit_on_error` defaults to `True` — one bad frame would kill the app):
    - chunk with `usage` dict present (the final include-usage chunk has empty `choices`) → record `prompt_tokens`/`completion_tokens`
    - chunk whose `choices[0].delta.content` is non-empty text → first such chunk fixes TTFT (`perf_counter() - t_send`); append the text to worker-local reply parts
  - streaming UI update (worker side): after appending a delta, if ≥0.1 s since the last flush (or on the final flush), `self.call_from_thread(self._update_stream, "".join(reply_parts))`
  - completion: tok/s denominator is `now - t_first_text` (never `now - t_send`)
  - token accounting: use `usage` numbers when they arrived; otherwise output tokens = counted text-bearing deltas, prompt tokens = `len(user_text) / 3.5`
  - stamp written via `call_from_thread` as a dim line using the exact template:
    `{tok_in} in · {tok_out} out · {tok_s:.1f} tok/s · TTFT {ttft:.2f}s[ · cold]`
    where estimated counts render as `{n} (est)` attached to that number only (e.g. `12 (est) in · 6 (est) out · …`), ` · cold` appended iff the captured `_cold_pending` was `True` at turn start. Then the full assistant reply is appended to the `RichLog` scrollback via `log.write(full_text)` followed by `log.write("")` as a separator, and `self._update_stream("")` clears the streaming `Static`
  - re-enable the `Input` in every exit path (`finally` via `call_from_thread`; also clear `self._active_stream = None` there)
  - errors: `httpx.ConnectError`/timeouts → red system line `server unreachable :{port}` in the pane instead of a crash (written via `call_from_thread` as `Text("server unreachable :{port}", style="red")`)
- `def _update_stream(self, text: str) -> None` — `self.query_one("#chat-stream", Static).update(text)`; renders the growing reply as one block at ~10 Hz (per-delta `RichLog.write` was rejected: consecutive partial writes render as separate lines on textual 8.2.8)
- binding: `("escape", "cancel_chat", "Cancel")`; `action_cancel_chat`: set `self._cancel_requested = True`; cancel the `"chat"` worker group; **and** if `self._active_stream` is not None call `self._active_stream.close()`. Closing the response from the UI thread unblocks the worker's socket read immediately (thread workers are cooperative — `worker.cancel()` alone cannot interrupt a blocked `iter_lines()`). The worker's except path sees the close-induced `httpx.StreamClosed`/`httpx.ReadError`, checks `_cancel_requested`, writes dim `cancelled — request aborted`, and skips writing an error line; the `finally` re-enables the `Input`. The action itself writes nothing, so exactly one notice appears regardless of timing
- `exclusive=True` additionally guarantees a new submit can never interleave with a live stream (second line of defence behind the disabled Input)

**Success Criteria:**

#### Automated Verification:
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification (against the Phase 2 server):
- [ ] submit a prompt → reply streams onto one growing block (no per-token line breaks); stamp appears on completion with plausible numbers (`in` ≈ prompt size, `out` matches reply length, tok/s in the right ballpark for the machine)
- [ ] first turn after a server restart is stamped `cold`; subsequent turns are not
- [ ] second turn is visibly faster than the first (TTFT drops) — cold-vs-warm comparison works because the TTFT definition didn't move
- [ ] Esc mid-stream stops the reply cleanly with the cancellation notice; input re-enabled
- [ ] stop the server, submit a prompt → red `server unreachable` system line, TUI stays alive

### Phase 4: Integration test + quality gates
One high-level test exercising the app end-to-end over a real HTTP boundary; all gates green.

**Changes:**
- create `tests/test_app_integration.py`:
  - `class _StubServer` — `http.server.HTTPServer(("127.0.0.1", 0))` in a daemon `threading.Thread`; class attr `mode: str = "ok"` switches behaviour; actual port read back from `server.server_address[1]`; `shutdown()`+join in each test's teardown. Handler keeps `BaseHTTPRequestHandler`'s default `protocol_version = "HTTP/1.0"` (connection-per-request; do not opt into keep-alive, or the unread POST body desyncs framing):
    - `GET /v1/models` → `200`, `{"object": "list", "data": [{"id": "mlx-community/stub-test", "object": "model", "created": 0}]}` (mode `"html"`: `502` + `<html>proxy</html>` body — under the Phase 2 rules any complete response with an invalid body reads amber, matching the idea doc's 502-proxy example)
    - `POST /v1/chat/completions` → `Content-Type: text/event-stream`, then canned SSE: one role-only delta (`{"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}`), five text deltas (`"Hello"`, `" world"`, …), finish chunk, final usage chunk (`{"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}}`), `data: [DONE]`
  - `def test_status_green_and_chat_stamp_over_stub_http() -> None` — thin wrapper: `asyncio.run(_scenario_ok())`
    - `_scenario_ok` mounts `MlxTuiApp(host="127.0.0.1", port=<stub port>)` inside `async with app.run_test() as pilot:`; calls `await app._poll()` directly (deterministic — no waiting on the 2s timer), asserts `app.status_state == "green"` and `app._ever_green is True`; sets the `Input` value, `await pilot.press("enter")`; loops `pilot.pause()` until the stamp appears (bounded retries), then asserts:
      - the streamed sentence `"Hello world…"` appears in the chat `RichLog`
      - the streaming `Static(id="chat-stream")` renders empty after completion
      - the stamp line matches shape `r"\d+( \(est\))? in · \d+( \(est\))? out · [\d.]+ tok/s · TTFT [\d.]+s( · cold)?"` with exact usage numbers `12 in · 6 out` (no `(est)` — the stub sends usage)
      - no `cold` suffix (no red was ever observed: `_cold_pending` stayed `False`)
  - `def test_non_mlx_http_shows_amber() -> None` — same harness with `mode="html"`; asserts `app.status_state == "amber"` after a forced poll
  - optional third test `test_cancel_closes_stream()` — submit, wait for first delta via `pilot.pause()` loop, press `escape`, assert the input re-enables and the dim `cancelled — request aborted` line appears (bounded retries); skipped if flaky on CI-free local runs
  - note: no mlx process exists during tests → model id renders `—`; deliberately unasserted
- no production-code changes expected beyond what the tests expose

**Success Criteria:**

#### Automated Verification:
- [x] tests pass: `uv run pytest -v` (required tests green; optional cancel test green or explicitly skipped)
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

> Deviations:
> - The optional cancel test was implemented (not skipped) and passes deterministically: `_StubHandler` gained a `"slow"` mode that sends only the role frame, sleeps 0.5 s, then streams the rest — giving Esc a reliable mid-stream window.
> - Verified empirically on macOS: `response.close()` from the UI thread does **not** wake a blocked `iter_lines()` recv (worker stayed stuck; interpreter shutdown hung joining it). `action_cancel_chat` therefore also does `socket.SHUT_RDWR` on `response.extensions["network_stream"]->_sock` (guarded by try/except OSError), which unblocks the read in ~1 ms. Shutdown surfaces as clean EOF rather than an exception, so the worker additionally checks `_cancel_requested` right after the stream loop and takes the cancelled path there. Observable behaviour still matches the plan exactly: prompt abort, one dim notice, input re-enabled.

#### Manual Verification:
- [ ] full dry run of the week-trial setup: server in one tmux pane, `uv run mlx-tui` in another; both Phase 2 and Phase 3 manual criteria hold together (dot updates while a chat streams without stalling either)

## Out of Scope
- Everything the idea doc assigns to v1/v2/v3: models table, swap state machine, config file (`~/.config/mlx-tui/config.toml`), HF search/download, history sparkline
- Markdown rendering (raw text into `RichLog` only), params sidebar, system-preset cycling
- Multi-backend support, conversation persistence, agentic/tool-calling harness
- Server log tailing; context-limit bar (no known context window in v0 — stamps show absolute token counts)
- Unit tests of internal helpers (per user decision; the pilot integration test covers behaviour)

## Risks & Mitigations
- **Per-token line breaks** → eliminated by design: streaming renders through a throttled `Static(id="chat-stream")` with accumulated text; `RichLog` only ever receives complete turns (verified empirically that textual 8.2.8 never merges consecutive partial writes). Residual: ~10 Hz full-text repaint cost on very long replies — acceptable at v0 prompt sizes; drop to 5 Hz if it flickers
- **Thread-worker cancellation is cooperative in Textual** (`worker.cancel()` cannot interrupt a blocked socket read) → `action_cancel_chat` also closes the active `httpx.Response`, unblocking `iter_lines()` immediately; the worker's except path distinguishes cancel (`_cancel_requested`) from genuine failure so exactly one notice is written
- **SSE frames that aren't data** (mlx-lm keepalive comments during prompt processing) → parser skips empty/non-`data:` lines and guards `json.loads`; a malformed frame degrades to a skipped token, never an app crash
- **pyrefly strict friction on untyped libs** (psutil returns, parsed JSON) → narrow typed wrappers inside `app.py` (`-> int | None`, dataclass-free plain tuples), `cast` only where unavoidable; gates run every phase so drift is caught immediately
- **Rare `process_iter` scan blocks the event loop** (only when cached pid died) → accepted trade-off, bounded to one scan per server death; documented here rather than hidden
- **Ephemeral-port flakiness in tests** → bind port 0 and read the assigned port back; strict teardown via try/finally
- **mlx-vlm servers require the `model` field** (422 without it — verified against current mlx-vlm schemas) → v0 targets mlx-lm default-model behaviour; documented limitation, revisited only when a VLM is actually served
- **macOS `available` memory reads low (purgeable accounting)** → displayed as informational only in v0; nothing gates on it
