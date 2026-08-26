# Refactor app.py into functional-core modules + modernized test infra

**Date:** 2026-08-25
**Work Item:** n/a (follow-up to `docs/plans/2026-08-25-v0-status-bar-and-chat.md`)
**Status:** Complete

## Overview
Decompose the 363-line `src/mlx_tui/app.py` into four focused modules (`sse`, `status`, `process`, `chat`) behind an unchanged Textual shell, add docstrings/comments where logic is non-obvious, and modernize testing: pytest-asyncio native async tests, conftest fixtures, a shared SSE stream builder, and a new unit-test suite per extracted module. Behavior-preserving; all gates green at every phase.

## Current State
Baseline gates are green: `uv run ruff check .` ✓, `uv run pyrefly check` ✓ (strict over `src`/`tests`), `uv run pytest -q` → 3 passed.

- `src/mlx_tui/app.py` mixes four concerns:
  - **Pure SSE/token helpers** (app.py:21-76): `_iter_sse_data`, `_usage_from_chunk`, `_delta_content_from_chunk`, `_token_accounting`, plus `_FLUSH_INTERVAL_S = 0.1` (app.py:18). Zero dependencies, currently untested.
  - **psutil discovery** (app.py:104, 177-201): `_pid_cache` instance attr, `_find_server_pid` (cached pid → `is_running()` check → full `process_iter` scan on miss), `_model_from_cmdline` (`--model` argv parsing).
  - **Status logic** (app.py:152-213): `_classify_liveness` (HTTP fetch *and* classification mixed), `_arm_cold` (pure green→red→green state machine over three instance bools, app.py:106-108), `_render_status` (formatting mixed with `psutil.virtual_memory()`).
  - **Chat engine** (app.py:229-331): `_consume_stream` entangles httpx streaming with UI coupling (`self._active_stream` at app.py:286, `call_from_thread(self._update_stream, …)` at app.py:306); error→UI-message mapping in `_run_turn` (app.py:253-267); Esc-cancel with socket-shutdown workaround in `action_cancel_chat` (app.py:319-331).
- `tests/test_app_integration.py`: no conftest; `_StubServer`/`_StubHandler`/`_start_stub` (test_app_integration.py:23-99); `_wait_for` retry helper (test_app_integration.py:107-118); server start/shutdown try/finally copy-pasted across all 3 tests (test_app_integration.py:184-199); sync wrappers around `asyncio.run(_scenario_*())`.
- `main.py:1` is a shim importing `mlx_tui.app:main`; `[project.scripts] mlx-tui` points there (pyproject.toml:24).
- `pyproject.toml`: all tools live in `[project].dependencies` (pyproject.toml:7-14); ruff selects `E4/E7/E9/F/I/PL/UP/TID/ASYNC/DTZ` (pyproject.toml:31); pyrefly strict covers `src`+`tests` (pyproject.toml:33-39). No `[tool.pytest.ini_options]` section exists.
- `tests/__init__.py` exists (empty) → `tests` is a package; cross-module imports like `from tests.builders import …` resolve because pytest inserts the repo root on `sys.path`.

### Empirical verifications backing this plan (performed 2026-08-25)
1. `pytest-asyncio==1.4.0` resolves alongside this repo's `pytest>=9.1.1` and runs native async tests using Textual's `run_test()` pilot, both via CLI flags and via `[tool.pytest.ini_options] asyncio_mode = "auto"` + `asyncio_default_fixture_loop_scope = "function"`.
2. `httpx.MockTransport` supports streamed responses consumed by `response.iter_lines()` (note: a trailing `\n\n` yields a final empty line — the parser already skips empties).
3. Ruff `PLR0913` fires at 6 arguments under this repo's config (verified: a 6-kwonly-param function fails `uv run ruff check`; 5 passes — matching existing `_token_accounting`). Hence the `MemorySnapshot` grouping below.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Functional-core module split: `sse.py`, `status.py`, `process.py`, `chat.py`, thin `app.py` | injected service classes (StatusMonitor/ChatClient DI container); minimal split (one helpers module) | user choice: plain functions/small classes are easy to unit test without ceremony at this code size; layering `sse ← chat ← app`, `status ← app`, `status ← process ← app`, no cycles |
| `MemorySnapshot(NamedTuple)` groups `avail_gib`/`total_gib`; defined in `sse.py`-free `status.py`, imported by `process.py` | 6-arg `format_status_line`; dataclass; define in `process.py` | verified PLR0913 fires at 6 args under repo config; NamedTuple keeps formatter pure and gives Phase 4's `memory_snapshot()` a ready-made return type; placing it in `status.py` keeps `status.py` stdlib-only and avoids cycles |
| `ColdTracker` class replaces the three loose bools `_ever_green/_red_since_green/_cold_pending` | keep bools on the App; module-level functions passing state tuples | the arming rule is a cohesive state machine; a class owns its invariants, is trivially unit-testable, and `consume_cold()` expresses the "capture then reset" contract from `_on_input_submitted` (app.py:223-224) |
| `ChatClient.stream_turn(..., on_flush=callback, flush_interval=0.1)` returns a frozen `TurnResult` dataclass; exposes `active_response` | keep `_consume_stream` on the App; pass the App in; futures/queue | UI-free streaming: flushes go through one callback (the App supplies `call_from_thread` wrapping), results are values, and `active_response` preserves the exact cancel seam `action_cancel_chat` uses today; `flush_interval` parameterized so throttle tests are deterministic |
| httpx exception → UI message mapping **stays in `app.py`'s worker** | move mapping into `chat.py` raising typed errors | UI strings belong to the UI layer; `chat.py` simply propagates httpx exceptions (unit test asserts propagation) |
| Dev dep `pytest-asyncio>=1.4` in a new `[dependency-groups] dev` table + `asyncio_mode = "auto"` | keep `asyncio.run` wrappers; anyio plugin | user-approved dep; auto mode removes marker noise; `uv sync` installs dev group by default; verified working with pytest 9.1.1 + textual pilot |
| `SseStreamBuilder` (fluent builder) shared by stub server and unit tests | literal frame lists per test; plain fixture returning canned bytes | user-approved builder pattern; single source of truth for the SSE wire format (`data: {json}\n\n`, `data: [DONE]\n\n`, `": keepalive n/total\n\n"` comments) across stub server and parser tests |
| `AppHarness` fixture bundles `app` + `pilot` + `server` + `wait_for`/`log_lines` helpers | separate app/pilot fixtures; module-level helpers | `run_test()` yields the Pilot exactly once — a bundle avoids re-entry problems and kills the copy-pasted teardown |
| Comments policy: module docstrings + function docstrings on all extracted public API; inline comments **only** where logic is non-obvious (listed per phase) | no comments (repo style today); exhaustive commenting | user requested explanatory comments "where it makes sense"; ruff/pyrefly config has no docstring rules, so no gate friction |

## Implementation Phases

### Phase 1: Test infra foundation
Native async tests, shared fixtures, and the SSE builder — no production-code changes.

**Changes:**
- `pyproject.toml` — append two sections (do not touch existing tables):
  ```toml
  [dependency-groups]
  dev = ["pytest-asyncio>=1.4"]

  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  asyncio_default_fixture_loop_scope = "function"
  ```
  Then run `uv sync` once to install.
- create `tests/builders.py`:
  ```python
  """Builders composing canned protocol payloads for tests."""
  class SseStreamBuilder:
      """Fluent builder emitting OpenAI-style SSE chat-completion wire bytes.

      Wire format (matches mlx-lm): each frame is ``data: {json}\\n\\n``;
      the stream ends with ``data: [DONE]\\n\\n``; keepalives are SSE comment
      frames ``": keepalive {processed}/{total}\\n\\n"``.
      """
      def __init__(self) -> None: self._frames: list[bytes] = []
      def _add_json(self, chunk: dict[str, object]) -> Self   # appends f"data: {json.dumps(chunk)}\n\n".encode()
      def raw_data(self, body: str) -> Self                   # appends f"data: {body}\n\n".encode() — escape hatch
      def role_frame(self) -> Self                            # {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
      def delta(self, text: str) -> Self                      # {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
      def finish_frame(self) -> Self                          # {"choices": [{"delta": {}, "finish_reason": "stop"}]}
      def usage(self, prompt_tokens: int, completion_tokens: int) -> Self  # {"choices": [], "usage": {..., "total_tokens": sum}}
      def malformed(self) -> Self                             # raw_data("{not json")
      def keepalive(self, processed: int, total: int) -> Self # appends f": keepalive {processed}/{total}\n\n".encode() (no "data:" prefix)
      def done(self) -> Self                                  # raw_data("[DONE]")
      def build(self) -> bytes                                # b"".join(self._frames)
  ```
- create `tests/conftest.py`:
  - Move `StubServer` (was `_StubServer`), `StubHandler` (was `_StubHandler`) here unchanged except: `do_POST` builds its frame body via `SseStreamBuilder().role_frame().delta("Hello").delta(" world").delta(" this").delta(" is").delta(" MLX.").finish_frame().usage(12, 6).done().build()` (byte-equivalent semantics to the old hand-rolled list); `"slow"` mode behavior preserved exactly (write role frame, flush, `time.sleep(0.5)`, write the rest).
  - `def stub_server_factory() -> Iterator[Callable[[str], StubServer]]` — sync fixture; inner `start(mode: str) -> StubServer` binds `("127.0.0.1", 0)`, spawns a daemon `serve_forever` thread, records the server; teardown after yield: `shutdown()` + `server_close()` for every started server.
  - `@dataclass class AppHarness:` with fields `app: MlxTuiApp`, `pilot: Pilot[None]`, `server: StubServer`; methods:
    - `port` property → `int(self.server.server_address[1])`
    - `log_lines() -> list[str]` → `[strip.text for strip in self.app.query_one("#chat-log", RichLog).lines]` (moved verbatim from `_log_texts`, test_app_integration.py:102-104)
    - `async def wait_for(predicate: Callable[[MlxTuiApp], bool], attempts: int = 200) -> bool` → loop of `await pilot.pause()` + predicate check + `await asyncio.sleep(0.02)` (moved verbatim from `_wait_for`, test_app_integration.py:107-118)
  - `async def harness(stub_server_factory) -> AsyncIterator[AppHarness]` — starts `mode="ok"` server, constructs `MlxTuiApp(host="127.0.0.1", port=<assigned port>)`, `async with app.run_test() as pilot: yield AppHarness(...)`. Tests flip modes via `harness.server.mode = "html" | "slow"` before their first request (mode is read per-request, so post-mount switching works).
- rewrite `tests/test_app_integration.py` — keep `_STAMP_RE` and `_REPLY` as-is; replace `_scenario_*` + sync wrappers with three native async tests taking `harness` as a parameter, with **identical assertions**:
  - `test_status_green_and_chat_stamp_over_stub_http(harness)`: `await harness.app._poll()` → `status_state == "green"`, `_ever_green is True`; set input value `"hi"`, focus, `await harness.pilot.press("enter")`; `wait_for` stamp visible; assert reply line, single stamp, `_STAMP_RE.fullmatch`, `"12 in · 6 out"` in stamp, no `"(est)"`, no `"cold"`, `Static(id="chat-stream").content == ""`.
  - `test_non_mlx_http_shows_amber(harness)`: `harness.server.mode = "html"`, `await harness.app._poll()`, assert `status_state == "amber"`.
  - `test_cancel_closes_stream(harness)`: `harness.server.mode = "slow"`; submit `"hi"`; `wait_for` `you ›` line; press escape; `wait_for` `cancelled — request aborted` line; `wait_for` input re-enabled.
  - Delete `_start_stub`, `_log_texts`, `_wait_for`, `_scenario_*` (superseded).

**Success Criteria:**

#### Automated Verification:
- [x] `pytest-asyncio` installed via dev group: `uv sync` exits 0
- [x] all tests green as native async: `uv run pytest -v` (3 passed)
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] brief smoke: `uv run mlx-tui --help` prints usage (entry point untouched)

> **Deviation note:** pyrefly strict could not resolve the plan-mandated
> `from tests.builders import …` cross-module imports (its import root is
> `src/`). Fixed by adding the repo root to search-path:
> `search-path = [".", "src", "tests"]` in `[tool.pyrefly]`.

### Phase 2: Extract `mlx_tui/sse.py`
Wire-format parsing + token accounting become a pure, fully unit-tested module.

**Changes:**
- create `src/mlx_tui/sse.py` — move the four helpers from `app.py` verbatim, renamed public (drop leading underscore), plus docstrings:
  - Module docstring: documents the SSE surface — `data: {json}` frames, `[DONE]` sentinel, and why any line that is empty or lacks the `data: ` prefix is skipped (mlx-lm emits `: keepalive {processed}/{total}` comment frames during long prompt processing; upstream `mlx_lm/server.py:1362-1367`).
  - `iter_sse_data(lines: Iterable[str]) -> Iterator[str]` — docstring notes `[DONE]` terminates iteration.
  - `usage_from_chunk(chunk: object) -> tuple[int | None, int | None]` — docstring notes defensive `isinstance` narrowing: malformed frames degrade to `(None, None)`, never raise.
  - `delta_content_from_chunk(chunk: object) -> str | None` — docstring notes role-only first deltas and empty strings yield `None`.
  - `token_accounting(*, prompt_tokens: int | None, completion_tokens: int | None, counted_deltas: int, user_chars: int, elapsed: float) -> tuple[str, str, float]` — docstring documents the `(est)` labelling convention (attached to the number only) and `tok_s == 0.0` when `elapsed <= 0`.
- `src/mlx_tui/app.py` — delete the four helper definitions (app.py:21-76); add `from mlx_tui.sse import delta_content_from_chunk, iter_sse_data, token_accounting, usage_from_chunk`; update the two call sites (`_consume_stream` uses all four).
- create `tests/test_sse.py` (imports `from tests.builders import SseStreamBuilder` only where handy; mostly literal lines) — parametrized cases with concrete expectations:
  - `iter_sse_data`: happy path stops at `[DONE]` (`["data: {\"a\":1}\n", "data: [DONE]\n"]` → `['{"a":1}']`); skips empties, `": keepalive 3/10"`, and non-`data:` lines (`["", "\n", ": keepalive 3/10\n", "event: ping\n", "data: x\n"]` → `["x"]`); ignores frames after `[DONE]`; tolerates surrounding whitespace (`"  data: y  \n"` → `"y"`).
  - `usage_from_chunk`: populated usage → `(12, 6)`; `{}` → `(None, None)`; `{"usage": None}` → `(None, None)`; string tokens (`"prompt_tokens": "x"` with valid completion) → `(None, 6)`; non-dict chunk `"junk"` → `(None, None)`.
  - `delta_content_from_chunk`: `{"choices": [{"delta": {"content": "Hi"}}]}` → `"Hi"`; empty-string content → `None`; role-only delta → `None`; empty `choices` list → `None`; finish chunk `{"choices": [{"delta": {}, "finish_reason": "stop"}]}` → `None`; non-dict → `None`.
  - `token_accounting`: usage path `(12, 6, elapsed=2.0)` → `("12", "6", 3.0)`; estimate path `(None, None, counted_deltas=4, user_chars=70, elapsed=2.0)` → `("20 (est)", "4 (est)", 2.0)` (70/3.5 = 20.0 exactly); mixed (prompt estimated, completion known) → `("20 (est)", "6", 3.0)`; `elapsed=0.0` → tok_s `0.0`.

**Success Criteria:**

#### Automated Verification:
- [x] new unit tests pass: `uv run pytest -v tests/test_sse.py`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] brief smoke: `uv run mlx-tui --help` still works (imports intact)

### Phase 3: Extract `mlx_tui/status.py`
Liveness classification, cold-cycle tracking, and status-line rendering become pure, unit-tested domain code.

**Changes:**
- create `src/mlx_tui/status.py` (stdlib imports only — no httpx, no psutil):
  ```python
  """Status-bar domain logic: liveness classification, cold tracking, rendering."""


  class MemorySnapshot(NamedTuple):
      avail_gib: float
      total_gib: float


  _STATUS_COLOURS = {"green": "green", "amber": "yellow", "red": "red"}


  def classify_liveness(status_code: int, body: object) -> str:
      """Pure classifier; 'red' is reserved for transport failure and is
      decided by the caller's exception path — this function never sees one.
      green ⇔ 200 AND body parses to a dict whose `data` is a non-empty list;
      every other complete HTTP response (junk JSON, wrong shape, 502 HTML
      proxy squatting on the port) is 'amber'."""
  ```
  - body of `classify_liveness` is the tail of today's `_classify_liveness` (app.py:157-165) with `status_code == 200` replacing `resp.status_code == httpx.codes.OK` (identical value).
  - `class ColdTracker:` — docstring explains the arming rule: a cold stamp is justified **only** by a true green→red→green cycle (a green must have been observed before the red), so the first turn after TUI startup against an hours-warm server is never mislabelled cold. Methods:
    - `__init__` → `ever_green = False`, `red_since_green = False`, `cold_pending = False`
    - `observe(self, state: str) -> None` — exact logic of `_arm_cold` (app.py:167-175)
    - `consume_cold(self) -> bool` — returns `cold_pending` and resets it to `False` (the capture-then-reset from `_on_input_submitted`, app.py:223-224)
  - `format_status_line(*, state: str, model: str | None, rss_gib: float | None, memory: MemorySnapshot, port: int) -> str` — body of today's `_render_status` formatting (app.py:204-212), returning the string instead of writing it; docstring documents the em-dash placeholders for missing pieces. 5 keyword-only params (passes PLR0913).
- `src/mlx_tui/app.py`:
  - `__init__`: replace the three bools with `self._cold_tracker = ColdTracker()`.
  - `_classify_liveness`: keep the `try/except Exception → "red"` GET and `ValueError → body = None` JSON parse; delegate to `classify_liveness(resp.status_code, body)`.
  - `_poll`: replace `self._arm_cold(state)` with `self._cold_tracker.observe(state)`.
  - `_on_input_submitted`: `cold = self._cold_tracker.consume_cold()`.
  - `_render_status`: build `MemorySnapshot(vm.available / 2**30, vm.total / 2**30)` from `psutil.virtual_memory()`, then `self.query_one("#status-bar", Static).update(format_status_line(state=self.status_state, model=model, rss_gib=rss_gib, memory=snapshot, port=self.port))`.
  - Delete `_arm_cold`; add the `mlx_tui.status` import.
- `tests/test_app_integration.py` — update one internal hook: `assert h.app._ever_green is True` → `assert h.app._cold_tracker.ever_green is True` (runtime behavior unchanged).
- create `tests/test_status.py`:
  - `test_classify_liveness` parametrize: `(200, {"data": [{"id": "m"}]}) → "green"`; `(200, {"data": ["x"]}) → "green"` (non-empty list of anything); `(200, {"data": []}) → "amber"`; `(200, {}) → "amber"`; `(200, None) → "amber"`; `(200, "junk") → "amber"`; `(502, "<html>proxy</html>") → "amber"`; `(500, {"data": [{"id": "m"}]}) → "amber"`.
  - `ColdTracker` transition tests: fresh `consume_cold()` is `False`; red-before-any-green arms nothing (then a later green sets only `ever_green`, no cold); full cycle green→red→green → `consume_cold()` `True`, second consume `False`, `red_since_green` cleared; double red between greens arms cold once; amber inert (green→amber→red→amber→green still arms cold).
  - `test_format_status_line`: all-present green case → exact string `"[green]●[/] m · RSS 1.9 GB · avail 8.0/16.0 GB · :8080"`; missing pieces → `"[red]●[/] — · RSS — GB · avail 8.0/16.0 GB · :8080"`; amber maps to yellow dot.

**Success Criteria:**

#### Automated Verification:
- [x] new unit tests pass: `uv run pytest -v tests/test_status.py`
- [x] whole suite green (incl. updated integration hook): `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] brief smoke: `uv run mlx-tui` opens with the status bar placeholder rendering (`●` visible) — confirms formatter output feeds the widget correctly

> **Deviation note:** plan's literal `status_code == 200` trips ruff
> `PLR2004` under repo config; replaced with module constant
> `_HTTP_OK = 200` (behavior identical).

### Phase 4: Extract `mlx_tui/process.py`
OS introspection gets a seams-friendly home with an encapsulated pid cache.

**Changes:**
- create `src/mlx_tui/process.py`:
  - Module docstring: locating the mlx server process by scanning cmdlines for tokens ending in `mlx_lm.server` / `mlx_vlm.server`; constant `_SERVER_TOKEN_SUFFIXES = ("mlx_lm.server", "mlx_vlm.server")`.
  - `class ServerProcessFinder:` — docstring documents the cache policy: a full `psutil.process_iter` scan happens **only** when the cached pid died or nothing is cached, never every tick (accepted trade-off from the v0 plan). Methods:
    - `__init__` → `self.pid_cache: int | None = None`
    - `find(self) -> int | None` — body of `_find_server_pid` (app.py:177-191) with `self.pid_cache` replacing `self._pid_cache`.
  - `def model_from_cmdline(proc: psutil.Process) -> str | None` — body of app.py:193-201; docstring notes `NoSuchProcess`/`AccessDenied` degrade to `None`.
  - `def memory_snapshot() -> MemorySnapshot` — `vm = psutil.virtual_memory()`; `return MemorySnapshot(vm.available / 2**30, vm.total / 2**30)`; imports `MemorySnapshot` from `mlx_tui.status` (documented decision: keeps `status.py` stdlib-only, acyclic).
- `src/mlx_tui/app.py`:
  - `__init__`: drop `_pid_cache`; add `self._process_finder = ServerProcessFinder()`.
  - `_poll`: `pid = self._process_finder.find()`; model/RSS block unchanged otherwise; replace the inline `psutil.virtual_memory()` in `_render_status` with `memory = memory_snapshot()`.
  - Delete `_find_server_pid` and `_model_from_cmdline`; add the `mlx_tui.process` import.
- create `tests/test_process.py` — fake psutil objects via the built-in `monkeypatch` fixture (patches `psutil.process_iter` / `psutil.Process` attributes globally; auto-reverted per test):
  ```python
  class FakeProcess:
      def __init__(
          self,
          pid: int,
          cmdline: list[str] | None = None,
          running: bool = True,
          cmdline_error: bool = False,
      ) -> None: ...
      def is_running(self) -> bool: ...
      def cmdline(
          self,
      ) -> list[str]: ...  # raises psutil.NoSuchProcess(pid) when cmdline_error
      def memory_info(self) -> SimpleNamespace: ...  # .rss attribute
  ```
  - `test_find_scans_then_caches`: one matching fake (`cmdline=["python", "-m", "mlx_lm.server", "--model", "m"]`, pid 100) → `find() == 100`; replace `process_iter` with a counting function returning `[]` → second `find()` still `100` with **zero** additional scans (cache hit calls patched `psutil.Process(100).is_running()`).
  - `test_find_suffix_matching`: token `/opt/bin/mlx_vlm.server` matches; bare `mlx_lm.server` matches; `mlx_lm.serverx` does **not** (endswith semantics).
  - `test_cache_dead_rescans`: cached pid 100 with `running=False` → rescan finds pid 200.
  - `test_no_match_returns_none_and_keeps_scanning`: no match → `None`; a later scan containing a match → found (cache `None` never sticks).
  - `test_model_from_cmdline`: `--model` followed by value → value; `--model` as last token → `None`; no flag → `None`; `cmdline_error=True` → `None`.
  - `test_memory_snapshot`: patch `psutil.virtual_memory` returning fake with `total=32*2**30, available=8*2**30` → `MemorySnapshot(avail_gib=8.0, total_gib=32.0)`.

**Success Criteria:**

#### Automated Verification:
- [x] new unit tests pass: `uv run pytest -v tests/test_process.py`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] brief smoke: `uv run mlx-tui` opens; status bar shows `—` for model/RSS (no mlx process running) instead of crashing — proves the psutil paths survive the extraction

### Phase 5: Extract `mlx_tui/chat.py` + final sweep
Streaming becomes a UI-free client returning values; full-gate finale.

**Changes:**
- create `src/mlx_tui/chat.py`:
  ```python
  """UI-free streaming chat client consuming one SSE turn."""

  _FLUSH_INTERVAL_S = 0.1


  @dataclass(frozen=True)
  class TurnResult:
      full_text: str
      ttft: float  # seconds to first text-bearing delta (never first byte/role frame)
      tok_in_str: str
      tok_out_str: str
      tok_s: float  # denominator is last-chunk time minus first-text time, never t_send
  ```
  - `class ChatClient:` — `__init__` sets `self.active_response: httpx.Response | None = None` (docstring: read by the App's cancel action from the UI thread while the worker thread writes it — same cross-thread pattern as the old `_active_stream`, app.py:96, 286; the **App** clears it in its worker `finally`, not the client).
  - `stream_turn(self, url: str, payload: dict[str, object], *, user_chars: int, on_flush: Callable[[str], None], flush_interval: float = _FLUSH_INTERVAL_S) -> TurnResult` — body of `_consume_stream` (app.py:272-317) with three mechanical substitutions: assign `self.active_response = response`; invoke `on_flush("".join(parts))` directly instead of `call_from_thread`; compare against `flush_interval` instead of the module constant. Inline comments to carry over/add:
    - why the flush is throttled (~10 Hz): consecutive partial `RichLog.write()`s render as separate lines on textual 8.2.8, so streaming repaints one accumulated `Static` block instead;
    - why `json.loads` failures are skipped: one malformed frame degrades to a lost token, never a crashed app;
    - TTFT/tok-s denominator definitions (mirrors the `TurnResult` field docs).
  - httpx exceptions propagate — deliberately no try/except (docstring says so).
- `src/mlx_tui/app.py`:
  - `__init__`: drop `_active_stream` declaration (app.py:96) and assignment (app.py:110); add `self._chat = ChatClient()`.
  - `_run_turn`: `result = self._chat.stream_turn(url, payload, user_chars=user_chars, on_flush=lambda text: self.call_from_thread(self._update_stream, text))`; unpack `result.full_text/ttft/tok_in_str/tok_out_str/tok_s` for the existing stamp/cancel/complete logic; `finally` clears `self._chat.active_response = None` instead of `self._active_stream`. Error-mapping `except` clauses (app.py:253-267) stay exactly as they are.
  - `action_cancel_chat`: `response = self._chat.active_response`; everything else byte-identical — including the socket-shutdown workaround with its comment: closing the response alone does **not** wake a blocked `iter_lines()` recv on macOS (verified empirically in v0), so `socket.SHUT_RDWR` on `response.extensions["network_stream"]->_sock` (guarded `except OSError`) unblocks the read in ~1 ms.
  - Delete `_consume_stream` and the local `_FLUSH_INTERVAL_S`; add the `mlx_tui.chat` import.
- create `tests/test_chat.py` — `httpx.MockTransport` handlers returning `SseStreamBuilder(...).build()` bodies with header `Content-Type: text/event-stream`:
  - `test_happy_path_with_usage`: role + 3 deltas + finish + `usage(12, 6)` + done; `flush_interval=0.0` (deterministic: every delta flushes); collect `on_flush` texts in a list; assert `TurnResult.full_text == "Hello world this"`, `tok_in_str == "12"`, `tok_out_str == "6"`, `0 <= ttft`, `tok_s > 0`, and the 3 flushes are exactly the growing prefixes of `full_text`.
  - `test_estimate_fallback_without_usage`: role + 4 deltas + done only; `user_chars=70` → `tok_in_str == "20 (est)"`, `tok_out_str == "4 (est)"`.
  - `test_skips_malformed_keepalive_and_stops_at_done`: frames `malformed()`, `keepalive(3, 10)`, role, `delta("A")`, `done()`, then a trailing `delta("LATE")` → `full_text == "A"` (nothing after `[DONE]` consumed).
  - `test_exceptions_propagate`: handler raises `httpx.ConnectError("boom")` → `pytest.raises(httpx.ConnectError)` around `stream_turn` (documents that error→message mapping lives in the App).
  - `test_active_response_exposed`: after a successful `stream_turn`, `client.active_response is not None` (the App clears it in its worker `finally`; asserted here to pin the cancel seam).
- Final sweep (no code edits expected): run every gate command listed below.

**Success Criteria:**

#### Automated Verification:
- [x] new unit tests pass: `uv run pytest -v tests/test_chat.py`
- [x] entire suite (unit + integration) green: `uv run pytest -v` (expect: tests from the 4 new unit-test modules (`test_sse`, `test_status`, `test_process`, `test_chat`) plus the original 3 integration scenarios, all passing)
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`
- [x] script entry intact: `uv run mlx-tui --help` prints `--host`/`--port`

#### Manual Verification (Apple Silicon + installed mlx-lm server, as in the v0 plan):
- [ ] `uv run mlx-tui --port 8080` against a running `mlx_lm.server`: green dot + repo id within ~2s; one prompt streams onto one growing block; stamp appears with plausible numbers; Esc mid-stream aborts cleanly with the dim notice and re-enables input — i.e., the refactor changed nothing observable

## Out of Scope
- Everything assigned to v1/v2/v3 of the idea doc (models table, swap state machine, config file, HF search, sparkline, markdown rendering, persistence).
- Changing public surface: `MlxTuiApp(host, port)` signature, `mlx-tui` CLI flags, `main.py` shim, `[project.scripts]` entry.
- Relocating existing `[project].dependencies` entries (pytest/ruff/pyrefly) into dependency groups — only `pytest-asyncio` is added, to a new `dev` group.
- Coverage tooling, CI workflows, mutation testing.
- Performance work (flush cadence, poll interval tuning).
- Renaming/moving anything not explicitly listed above (e.g. `main()` stays in `app.py`).

## Risks & Mitigations
- **Ruff `PLR0913` on new signatures** → verified empirically that 6 args fail and 5 pass under repo config; `format_status_line` capped at 5 via `MemorySnapshot` grouping; if future additions push a signature past 5, prefer grouping params over relaxing lint config.
- **pytest-asyncio × Textual `run_test()` interplay** → verified working (pytest-asyncio 1.4.0 + pytest 9.1.1 + textual 8.x, auto mode, function-scoped loops) before planning; if an upgrade regresses, fallback is `asyncio_mode = "strict"` + explicit `@pytest.mark.asyncio` markers — no architectural dependence on auto mode.
- **`MockTransport` streaming differs subtly from real sockets** (e.g. trailing empty line from `\n\n`) → the parser under test already skips empty/non-`data:` lines, and the stub-server integration tests continue exercising the real socket path end-to-end, so both layers cover each other.
- **Global monkeypatching of `psutil` in `tests/test_process.py`** → confined to the built-in `monkeypatch` fixture (auto-reverted per test); no production code changes needed for seams.
- **Cancel-path regression risk** (the most delicate code: cooperative worker cancel + socket shutdown) → the `slow`-mode integration test is preserved with identical assertions, and `ChatClient.active_response` reproduces the old `_active_stream` cross-thread seam exactly; `test_active_response_exposed` pins it at the unit level.
- **Cross-phase drift of moved code** → every extraction is specified as "move verbatim + rename + docstring"; behavioral edits are prohibited inside a moving phase, and the unchanged integration suite is the tripwire at every phase boundary.
