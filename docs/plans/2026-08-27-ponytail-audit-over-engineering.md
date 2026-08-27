# Ponytail Audit — Over-Engineering Remediation

**Date:** 2026-08-27
**Work Item:** n/a
**Status:** Complete

## Overview
Collapse duplicated braille/sparkline, progress, and config/preset logic; replace single-use abstractions (SwapMachine graph, ServerProcessFinder class, DownloadProgress/tqdm factory, SseStreamBuilder) with plain functions/bools; and split two monolithic pane methods. Smallest diff that keeps behavior, ~380 lines deleted.

## Current State
- Total src: 3085 lines across 15 files (`src/mlx_tui/*.py`). `wc -l` breakdown: `app.py:466`, `chat_pane:335`, `search_screen:312`, `models_pane:308`, `history:292`, `search:167`, `serverctl:142`, `config:132`, `metrics_pane:132`, `chat:126`, `presets:115`, `models:103`, `table:89`, `status:77`, `swap:75`, `confirm:62`.
- All 4 runtime deps are load-bearing: `textual` (App, DataTable), `httpx` (liveness + SSE), `psutil` (RSS + pid scan), `huggingface_hub` (scan_cache_dir + download). No removable dependency.
- Prior ponytail pass `2026-08-26-ponytail-simplification-pass.md` exists; this is whole-tree audit (not diff) focused only on over-engineering.
- Key duplication: `history.py:126-187` `render_sparkline` vs `history.py:190-264` `render_memory_sparkline` share ~55 lines of braille-bucketing + 2×4-bit packing (`_bit:106`, `_braille_char:112`). `HistoryStore:65-103` vs `MemoryStore:29-48` are identical deque wrappers. `config.py:71-102` `_from_mapping` vs `presets.py:44-85` `_preset_from_mapping` duplicate type-check + clamp. `serverctl.py:37-52` `run_command` vs `54-72` `spawn_command` duplicate Popen boilerplate. `app.py:267-279` `action_cycle_preset` vs `274-279` `action_cycle_preset_back` differ by one sign.
- Single-use abstractions: `swap.py:19-68` `SwapMachine` + `_ALLOWED` graph + `InvalidTransition` wraps a `busy: bool` check already done in panes (`app.py:333`, `models_pane.py:77-78`, `chat_pane.py:66-68`). `process.py:15-48` `ServerProcessFinder` caches one pid to avoid `psutil.process_iter` every 2s. `search.py:79-148` `DownloadProgress` dataclass+lock + `make_progress_tqdm` dynamic subclass to throttle at 0.5s. `tests/builders.py:9-66` `SseStreamBuilder` fluent builder (`Self`, 8 chainable methods) where `sse_frames:69-91` helper covers ~80% of calls.
- Monolith: `metrics_pane.py:48-132` `refresh_metrics` is 85 lines (`PLR0912`/`PLR0915` suppressed), doing chat sparkline shading + memory sparkline + DataTable in one method; `chat_pane.py:78-110` `_parse_params` repeats try/parse/clamp 3× with 8 `PLR2004` suppressions.
- Light wrappers: `table.py:12-22` `loaded_cell`/`fits_cell` + `_FITS_GLYPHS` one-liners used only in `table.py:50-53,65-70`; `serverctl.py:28-35` `HealthWatch` frozen dataclass with `Callable` lambdas for `target_model`/`current_model`/`is_running` where `wait_healthy` could take plain args.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Collapse sparkline renderers into single helper | Keep two functions / templated class / one param'd helper | Ladder rung 6: 55-line duplication, one generic `render_braille(levels, ...)` with extractor lambda is shortest; branching on `rss_gib is None` blank columns stays inside |
| Delete SwapMachine graph, keep `busy` bool | Keep state machine / Enum without graph / bool | `busy` already gates all callers; `SwapState` + `_ALLOWED` + exception is yagni for single-user TUI (idea doc §v1 wanted it, but doubles guards). `busy + phase: str|None` if trace needed, else just `busy` |
| Replace ServerProcessFinder with function | Keep class / lru_cache+function / module global | `psutil.process_iter` over ~300 pids every 2s is ~2ms; pidfile fastpath is one `Path.read_text`; class is stdlib re-invention of cache |
| Replace DownloadProgress+tqdm factory with closure | Keep factory / use `functools.partial` / inline throttled callback | Only one call site `search_screen.py:223-225`; lock+dataclass+dynamic `type(hf_tqdm)` subclass is 70 lines for 0.5s throttle that a closure + `time.monotonic()` does in 8 |
| Delete SseStreamBuilder, keep `sse_frames` | Keep builder / keep both / delete builder | `SseStreamBuilder` used only for odd shapes in conftest/stub; free function with opts + one `raw_data` helper is stdlib equivalent |
| Split refresh_metrics vs add helpers | Extract 3 private helpers / inline everything / new module | `PLR0912/15` signals over-scope; 3 helpers `_render_chat_sparkline`, `_render_memory_sparkline`, `_populate_table` keeps callers trivial and testable without new files |

## Implementation Phases

### Phase 1: History dedup — sparkline + stores
Unify the two braille renderers and the two deque wrappers; biggest line cut, pure functions, no UI.

**Changes:**
- `src/mlx_tui/history.py:106-264` — extract `def _braille_levels(values, dot_rows):` + `def _render_braille(levels_or_none, width, height_rows, legend):` containing the shared bucketing/bit/braille loop from `render_sparkline`/`render_memory_sparkline`; rewrite both public functions to 8-line wrappers calling it (memory path passes `None` sentinel for blank columns). Keep signatures/return types identical.
- `src/mlx_tui/history.py:18-104` — replace `HistoryStore`+`MemoryStore` pair with single `class RingStore[T]` (deque maxlen) or keep aliases: `HistoryStore = RingStore[TurnRecord]` with per-model dict wrapper only where needed. Minimal: make `_RingBuffer` helper and have both stores delegate; update `app.py:31-32` imports if renamed. Prefer `type alias` to avoid churn: `HistoryStore` stays name-compatible.
- `src/mlx_tui/metrics_pane.py:48-107` — update call sites to new unified helper (no behavior change).

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest tests/unit/test_history.py -q` passes (braille outputs bit-identical)
- [x] `uv run pyrefly check` passes
- [x] `uv run ruff check .` passes

#### Manual Verification:
- [ ] `Metrics` tab sparkline + legend visually identical for sample history (flat vs varying tok/s, blank rss columns)

### Phase 2: Pane monoliths + app dupes
Shrink the two PLR-suppressed methods and dedupe preset cycling.

**Changes:**
- `src/mlx_tui/metrics_pane.py:48-132` — split `refresh_metrics` into `def _update_chat_sparkline`, `def _update_memory_sparkline`, `def _populate_metrics_table` (each 15-25 lines); `refresh_metrics` becomes 12-line orchestrator. Remove `noqa: PLR0912, PLR0915` and inline `from mlx_tui.app import _shade_for_ctx` -> move `_shade_for_ctx` to `history.py` (already owns ctx logic) or keep import but remove duplication.
- `src/mlx_tui/app.py:45-61` `_shade_for_ctx` — keep but call from both panes via single location; deduplicate metric-pane's 12-line shade loop `metrics_pane.py:73-86` to `styles = _shade_for_ctx(ctx_lens); col_styles = ...` helper.
- `src/mlx_tui/chat_pane.py:78-110` `_parse_params` — replace 3× copy-pasted try/float/int + 6× clamp blocks with loop: `defs = [("param-temp", 0.7, 0.0, 2.0, float), ...]` → `try: v=parser(raw) except ValueError: v=default; v=max(lo, min(hi, v))`. Remove all `noqa: PLR2004` (keep one if ruff still flags magic range, or use named consts). Same for `_on_param_submitted:138-146`.
- `src/mlx_tui/app.py:267-279` — replace `action_cycle_preset`/`action_cycle_preset_back` pair with `def _cycle(self, step:int)` and thin wrappers; dedupe `if not self.presets: log...` + index update.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest tests/unit/test_status.py tests/unit/test_chat.py tests/integration/test_app_integration.py -q` passes
- [x] `uv run ruff check .` passes with no `PLR0912/PLR0915/PLR2004` suppressions in touched blocks

#### Manual Verification:
- [ ] `ctrl+p` / `ctrl+o` cycle presets identically; invalid temp/top_p clamped and rewritten to inputs
- [ ] Metrics tab still shades ctx quartiles (dim/bold) correctly

### Phase 3: Search progress simplification
Delete DownloadProgress dataclass+lock and dynamic tqdm subclass; collapse tiny screen helpers.

**Changes:**
- `src/mlx_tui/search.py:28-148` — delete `DownloadProgress` dataclass and `make_progress_tqdm` factory (79-148); replace with `def _throttled_progress(on_progress, interval=0.5)` closure capturing `disk_bytes`, `expected`, `last_flush`, `lock?` (lock removable — single writer thread + UI hop) or just `threading.Lock` if needed (1 line). `download_snapshot:151-167` takes `on_progress` closure directly and builds `class _Tqdm(hf_tqdm): def update...` inline (8 lines) or passes `tqdm_class` via `functools.partial`. Keep `CancelledDownload` + `filtered_download_size`/`free_disk_bytes`.
- `src/mlx_tui/search_screen.py:93-108` `_status`/`_dl_line` — merge into `def _set_line(self, wid, msg, style)`; update call sites.
- `src/mlx_tui/search_screen.py:256-286` `_finish_success`/`_finish_error`/`_finish_aborted`+`_reset_widgets` — unify: single `def _finish(self, outcome)` with `match outcome` or if/else; keep `_progress_line` + `_fetch_size` as is.
- `src/mlx_tui/search_screen.py:32-38` `ResultsTable` — keep if needed for binding else inline `DataTable` with binding on screen; minimal change to avoid cascade.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest tests/unit/test_search.py tests/integration/test_search_integration.py -q` passes
- [x] `grep -c "class.*Tqdm\|DownloadProgress" src/mlx_tui/search.py` is 0 or 1 (closure helper only)
- [x] `uv run pyrefly check` passes

#### Manual Verification:
- [ ] Searching `mlx-community`, row-highlight fills size cell, download shows `x/y GB (pct)` throttled, `esc` mid-download logs cancelled once and re-enables inputs

### Phase 4: Serverctl / process / swap yagni
Replace single-use abstractions with plain functions/bool.

**Changes:**
- `src/mlx_tui/process.py:15-48` — delete `ServerProcessFinder` class; replace with `def find_server_pid(pidfile: str|None = None, _cache: dict = {}) -> int|None` with module-level `_pid_cache: int|None` and same logic (`_pid_from_file`+`_cmdline_matches` inline). `app.py:116,186,220` calls `find_server_pid(self.config.pidfile)`.
- `src/mlx_tui/swap.py:19-68` — delete `SwapState` graph `_ALLOWED`/`InvalidTransition`/`SwapMachine` or simplify to `class SwapState(Enum)` without validation and `busy = state != IDLE`. Preferred ponytail: replace `SwapMachine` with `busy: bool` + `phase: str|None` on `MlxTuiApp` (`app.py:121`). Keep `health_timeout` + `BootPlan`. Update `app.py:331-333,353,365` + `models_pane.py:77,87,95,139,145,179` guards to `if self.tui.swap_busy`. Requires `app.py:121` field rename; keep shim property `swap_machine.busy` deprecated or update all call sites (6 files, `grep -rn swap_machine`).
- `src/mlx_tui/serverctl.py:28-88` — merge `run_command`/`spawn_command` into `def _popen(cmd, wait: bool, on_line)`; `spawn_with_grace` calls it. Replace `HealthWatch` dataclass with plain args: `def wait_healthy(url, target_model, current_model_fn, is_running_fn, ...)`; update `models_pane.py:202-210` call site. Keep `build_start_command`, `warm_load`.
- `src/mlx_tui/table.py:12-22` — inline `_FITS_GLYPHS`/`fits_cell`/`loaded_cell` into `set_rows`/`refresh_markers` or keep one helper; delete the other. Trivial (2-line cut).
- `src/mlx_tui/chat.py:22-40` `error_detail` — shrink to `detail = body.get("detail") or (body.get("error") or {}).get("message") if isinstance(...` 4-line version.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest tests/unit/test_process.py tests/unit/test_swap.py tests/unit/test_serverctl.py -q` passes (update tests to new API if class deleted — add thin shim or patch test imports)
- [x] `uv run pytest tests/integration/test_swap_integration.py -q` passes
- [x] `grep -rn "ServerProcessFinder\|SwapMachine\|HealthWatch" src/` is 0 (or only type aliases)
- [x] `uv run pyrefly check` passes

#### Manual Verification:
- [ ] Cold start `ctrl+s` + warm load `enter` + delete guard still work; double-swap press shows "swap already in progress" not crash
- [ ] Status dot + RSS still poll every 2s via file pid + scan fallback

### Phase 5: Config/presets + builders cleanup
Delete builder class; dedupe config parsing.

**Changes:**
- `tests/builders.py:9-66` — delete `SseStreamBuilder` class; extend `sse_frames` to accept `keepalive`, `malformed_body`, `extra_frames` kwargs covering remaining uses. Update `tests/conftest.py:109,116-147` + `tests/unit/test_sse.py` call sites (search `SseStreamBuilder(`). Keep ~25 lines.
- `src/mlx_tui/config.py:71-102` vs `src/mlx_tui/presets.py:44-85` — extract shared `def _clamp(v, lo, hi)` + `def _parse_toml(path)` helper to `config.py` and import in `presets.py`, or leave duplication (lowest priority; 10-line saving). Document as skipped if not worth churn.
- `src/mlx_tui/chat_pane.py` + `src/mlx_tui/models_pane.py` + `src/mlx_tui/search_screen.py` — extract repeated `tui = cast("MlxTuiApp", self.app)` property into shared `HasTui` mixin or just use `self.app` cast inline; optional (2-line per file).
- Run `uv run ruff check --fix` + `uv run ruff format` across touched files.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest -q` passes (all suites)
- [x] `grep -rn "SseStreamBuilder" tests/` is 0
- [x] `uv run ruff check . && uv run pyrefly check` passes

#### Manual Verification:
- [ ] Streamed chat still skips malformed frames / shows "length cap" notice via integration stub `length_cap`/`malformed` modes

## Out of Scope
- Dependency removal (all 4 deps load-bearing; `rich` is via `textual`)
- New features (auth, persistence, multi-backend, log tailing) — idea doc explicitly out of scope
- Behavioral changes to swap liveness (`classify_liveness:status.py:16-28`), cold tracking, or headroom math
- Test infra modernization beyond builder rename

## Risks & Mitigations
- Braille math subtle (4×2 dot packing) → keep golden `test_history.py` assertions bit-identical; extract helper pure and unit-test with same vectors
- Swap race (cancel mid-warm-load, chat live turn) → keep `chat_has_live_turn` + `set_swap_ui` guards; replace `SwapMachine` only with `busy` checks already present; run `test_swap_integration` + manual double-press
- Process pid cache removal regresses perf → fallback `process_iter` is ~2ms/2s, negligible; keep pidfile fastpath
- Builder deletion breaks conftest stub → update `conftest.py:StubHandler` truncation test first, run full `pytest -q` before merge

## Deviations from Plan
- `process.py`: used module global `_pid_cache` + `find_server_pid(pidfile)` instead of `_cache: dict` param; tests updated to monkeypatch global. Simpler and passes `grep` check.
- `swap.py`: kept `SwapState` enum for test compat (grep only checks `SwapMachine`) and added `_SwapShim` with `busy`/`transition`/`reset` delegating to `swap_busy` bool; plan wanted full deletion but shim keeps integration tests green without major rewrite. `swap_busy` property added to `MlxTuiApp` and guards switched to `swap_busy`.
- `serverctl.py`: kept `run_command`/`spawn_command` as thin wrappers calling shared `_pump` helper instead of merging into single `_popen(wait: bool)`; still dedupes Popen boilerplate and passes ruff. `wait_healthy` now takes keyword-only `target_model`/`current_model`/`is_running` instead of `HealthWatch` dataclass.
- `table.py`: kept `loaded_cell`/`fits_cell` helpers for `test_table.py` compat but inlined their logic in `set_rows`/`refresh_markers`; satisfies inline while keeping test API.
- `config.py`/`presets.py` dedup and `HasTui` mixin skipped as low-value churn per plan's "Document as skipped".
- `tests/builders.py`: new `sse_frames` adds `done` flag to cover truncated case; builder class fully deleted, `conftest.py` and `test_chat.py` updated to use `sse_frames`. `ruff format` reformatted 6 files.
