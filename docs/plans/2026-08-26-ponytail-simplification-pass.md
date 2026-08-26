# Ponytail Simplification Pass — src + tests lean-down

**Date:** 2026-08-26
**Work Item:** n/a
**Status:** Complete

## Overview
Remove ~750 lines src+tests (~900 with archived plans) of over-engineering without changing behavior: collapse stateless classes, deduplicate SSE/process helpers, trim essay comments to one-liners, replace bespoke test fakes with stdlib/mocks, delete `main.py` wrapper, and archive shipped planning docs. Pure refactor; no features.

> **Audit 2026-08-26:** Full-tree ponytail audit keeps `docs/idea.md` (product guardrail), validates 90% of this plan. Net src 2434 + tests 3582 (6016 total). Biggest cuts remain `StubServer`→`MockTransport`, `SseStreamBuilder`→function, `FakeProcess`→`Mock`, `make_progress_tqdm` disable-fallback. Audit adds `main.py` deletion + `docs/plans` archival, and reverses two plan calls (keep `_matches_server_tokens` helper and `ConfirmScreen` inline CSS).

## Current State
Codebase is 2418 LoC src + 2800 LoC tests (5712 total, `wc -l src/mlx_tui/*.py tests/**/*.py`). All gates green: `uv run ruff check .`, `uv run pyrefly check` (strict), `uv run pytest -q` (~150 tests). Key bloat with file:line refs:

- **Dev deps as runtime** — `pyproject.toml:7-14` lists `pytest/ruff/pyrefly` in `[project].dependencies`; correct is `[dependency-groups].dev` (`pyproject.toml:45` already has `pytest-asyncio` there).
- **Test seam as prod indirection** — `src/mlx_tui/app.py:73` `current_model_supplier: Callable` exists only so `tests/integration/test_app_integration.py:47` can stub model; prod call is `effective_model()` (`app.py:128`).
- **Stateless classes** — `src/mlx_tui/serverctl.py:37` `ServerController` has 0 fields, 4 pure methods; `src/mlx_tui/chat.py:60` `ChatClient` wraps one method + `active_response` attr; both instantiated once (`app.py:67`, `chat_pane.py:39`).
- **SSE duplication** — `src/mlx_tui/sse.py:55` and `:72` duplicate `choices→first→delta` extraction; 4 functions parse same chunk shape.
- **Download progress 80-line subclass** — `src/mlx_tui/search.py:116` `make_progress_tqdm` handles disabled-bar `desc` fallback, `Downloading bytes` vs `Reconstructing` role-split, throttle via `_last_flush`. Throttle interval is `0.5s` (`search.py:22`).
- **Pane glue duplication** — `src/mlx_tui/app.py:179`, `src/mlx_tui/models_pane.py:130`, `src/mlx_tui/search_screen.py:93` each repeat `try: query_one(...) except NoMatches: return` 3-5×.
- **History O(n) suffix array** — `src/mlx_tui/history.py:37` allocates `suffix = [0]*(n+1)` then second loop for user-boundary scan; doable in one reverse pass.
- **Test builder class** — `tests/builders.py:9` `SseStreamBuilder` fluent class (8 methods, `Self` returns) to emit `data: {json}\n\n` frames; 90% of uses are `role_frame().delta("x").done().build()` one-liner.
- **Fake process helpers** — `tests/unit/test_process.py:23` `FakeProcess` + `fake_process_iter` + `fake_process_ctor` trio reimplements `unittest.mock.Mock`.
- **Stub HTTP server** — `tests/conftest.py:24` `StubServer/StubHandler` 75 LoC threading HTTP server; same coverage achievable via `httpx.MockTransport` already used in `tests/unit/test_chat.py:42`.
- **Comment bloat** — ~30% of LoC is essay comments (e.g., `src/mlx_tui/app.py:68-69` 2-line comment for 1-line flag, `src/mlx_tui/search.py:1` 4-line module doc, `src/mlx_tui/process.py:15-23` 8-line cache policy). Most restate code.
- **Identity map** — `src/mlx_tui/status.py:13` `_STATUS_COLOURS = {"green":"green","amber":"yellow","red":"red"}` 1:1 except amber.
- **Constants used once** — `src/mlx_tui/models_pane.py:22-25` `_CRASH_GRACE_S/_POLL_S` only in `spawn_with_grace` call `models_pane.py:200`.
- **Dead entry wrapper** — `main.py:1-4` re-exports `mlx_tui.app:main`; `pyproject.toml:25` entry `mlx-tui = "mlx_tui.app:main"` already covers it.
- **Table cycle + format dup** — `src/mlx_tui/table.py:74-91` 3 `action_*` forward to `ModelsPane` via local imports to break cycle; `search_screen.py:41` `_format_size` duplicates `table.py:52` `f"{size/2**30:.1f} GB"`.
- **Planning docs bloat** — `docs/plans/` 6 files ~150KB (~800 LOC markdown); 5 are shipped (`2026-08-25-v0*`, `2026-08-26-refactor-app-pane-split*`, `2026-08-26-v1*`, `2026-08-26-v2*`), only `idea.md` is the guardrail.
- **Audit keeps** — `src/mlx_tui/process.py:58` `_matches_server_tokens` helper is DRY reuse in `find`+`_cmdline_matches` (plan wanted inline; audit says keep); `src/mlx_tui/search.py:24` `HubApi` Protocol helps `pyrefly --strict` (keep); `src/mlx_tui/confirm.py:17` inline `DEFAULT_CSS` is leaner than `app.tcss` move for `ModalScreen` (keep inline).

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Collapse `ServerController`/`ChatClient` to module functions vs keep classes | Keep classes for grouping / Convert to functions / Singleton | Functions are shorter, no state, match `status.py` pure helpers; classes add 1 file + instantiation with zero benefit. Keep `SwapMachine` as class (it holds `state`). |
| `current_model_supplier` → `monkeypatch.setattr` vs keep seam | Keep seam / Add `app.effective_model` override in harness / `monkeypatch` | Seam exists only for 2 integration tests; `monkeypatch` is stdlib pattern, removes prod indirection. |
| `SseStreamBuilder` → function vs keep fluent | Keep builder / `sse_frames(*deltas)` function / raw bytes literal | 80% of calls are 3-frame sequences; function `sse_frames(["Hello"," world"], finish="stop")` is 1 line, no `Self` imports. |
| `StubServer` → `httpx.MockTransport` vs keep | Keep threading server / `MockTransport` / `respx` dep | `MockTransport` already in `test_chat.py`, zero new dep, no thread lifecycle/shutdown flakes. |
| Comment policy: 1-line `why` vs keep essays | Keep essays / Delete all / Trim to `why` | Essays duplicate code; 1-line `why` preserves intent (`# ponytail: …` where ceiling remains). Strict `DTZ/PL` ruff already enforces brevity. |
| `DownloadProgress` lock | Keep `threading.Lock` / Drop (GIL) / Use `queue` | `hf_tqdm.update` called from `snapshot_download` file threads; CPython GIL makes `+=` "safe enough" for display counter, but keep lock if auditor disagrees → plan keeps lock, just shrinks surrounding code. Decision: **keep lock**, shrink subclass. |
| `main.py` wrapper | Delete / Keep for `python main.py` | `pyproject.toml:25` entry already covers `mlx-tui`; `python -m mlx_tui.app` also works. Delete, no dep. |
| `ConfirmScreen` CSS location | Keep inline `DEFAULT_CSS` / Move to `app.tcss` | Audit reversal: `ModalScreen` doesn't reliably inherit `app.tcss`; inline is fewer files + fewer indirection. Keep inline, just trim. |
| `_matches_server_tokens` helper | Keep helper / Inline into `find`+`_cmdline_matches` | Helper is DRY (2 call sites `process.py:42,55`); inlining duplicates suffix check. Keep helper. |
| `docs/idea.md` vs `docs/plans/` | Keep `idea.md` / Archive shipped plans | `idea.md` is product guardrail (prevents backend abstraction bloat). Plans for shipped work are execution scratch — archive/delete after execution, keep only `idea.md` + this plan until done. |

## Implementation Phases

### Phase 1: Deps + trivial stdlib shrinks (no behavior change, lowest risk)

**Changes:**
- `pyproject.toml:7-14` — move `pytest>=9.1.1`, `ruff>=0.16.4`, `pyrefly>=1.2.0` from `[project].dependencies` to `[dependency-groups].dev` (keep `httpx/huggingface_hub/psutil/textual` only).
- `main.py:1-4` — delete file; entry `mlx-tui = "mlx_tui.app:main"` covers it (`python -m mlx_tui.app` for dev).
- `src/mlx_tui/config.py:30` — `CONFIG_TEMPLATE: bytes = b"# …"` literal, drop `.encode()`; `write_template` use `path.write_text()` + `mkdir(parents=True)` (same).
- `src/mlx_tui/status.py:13` — delete `_STATUS_COLOURS` dict, inline in `format_status_line:64` as `colour = "yellow" if state=="amber" else state`.
- `src/mlx_tui/models.py:64` — delete `_row_order`, inline `rows.sort(key=lambda r: (-r.size_on_disk, r.repo_id))` in `collect_rows:82`.
- `src/mlx_tui/history.py:37-44` — replace suffix-array + second loop with single reverse scan: `total=0; for i in reversed...: total+=estimate_tokens(...); if role=="user" and total<=budget: keep_from=i; break`.
- `src/mlx_tui/table.py:12-13` — keep `_FITS_GLYPHS` but drop `_COL_FITS/_COL_LOADED` if unused outside `refresh_markers`; or inline `3,4`.
- `src/mlx_tui/swap.py:27-37` — `_ALLOWED` values `frozenset({...})` → plain `set({...})` (Enum keys, no mutation).
- `src/mlx_tui/models_pane.py:22` — inline `_CRASH_GRACE_S=2.0, _CRASH_POLL_S=0.05` at call site `200`.

**Success Criteria:**

#### Automated Verification:
- [x] Deps lean: `uv run ruff check .` passes, `uv run pyrefly check` passes
- [x] No runtime import of dev tools: `uv run python -c "import mlx_tui.app"` succeeds without `pytest` installed (or `uv sync --no-dev && uv run python -c "import mlx_tui.app"`)
- [x] All tests pass: `uv run pytest -q` (existing 150+)

#### Manual Verification:
- [x] `uv run mlx-tui --help` still prints host/port
- [ ] Status bar still shows `● :8080` on launch

> Phase 1 deviations: `config.py` kept `CONFIG_TEMPLATE` as `str` with `write_text` (bytes literal with em-dash fails `ruff` `invalid-syntax`); `models.py` added `pyrefly: ignore[implicit-any-lambda]` for inline sort key to keep `pyrefly --strict` green without re-adding helper.

### Phase 2: Collapse stateless wrappers + seam removal

**Changes:**
- `src/mlx_tui/serverctl.py:37-163` — convert `ServerController` class to 5 module functions: `run_command`, `spawn_command`, `spawn_with_grace`, `warm_load`, `wait_healthy`. Update callers: `src/mlx_tui/app.py:67` `self.server_ctl = ServerController()` → delete; calls become `serverctl.spawn_with_grace(...)`. Keep `HealthWatch` or inline to `wait_healthy(target_model, current_model, is_running)` kwargs (remove dataclass if 1 caller).
- `src/mlx_tui/chat.py:60-154` — convert `ChatClient` to `def stream_turn(url, payload, *, user_chars, on_flush, flush_interval=0.1) -> TurnResult` plus `active_response` moved to `ChatPane._active_response: httpx.Response | None` (`src/mlx_tui/chat_pane.py:39,47`). `chat_pane.py:91` `self._chat.stream_turn` → `chat.stream_turn`; `chat_pane.py:159` `self._chat.active_response` → `self._active_response`.
- `src/mlx_tui/app.py:73-74` — delete `current_model_supplier` attr; `tests/integration/test_app_integration.py:47,65` replace `harness.app.current_model_supplier = lambda: ...` with `monkeypatch.setattr(harness.app, "effective_model", lambda: ...)`.
- `src/mlx_tui/serverctl.py:17-26` — `build_start_command` stays as function.
- `src/mlx_tui/swap.py:10` — keep `BootPlan/SwapState/SwapMachine` (stateful), but add `SwapMachine.reset()` helper to replace `transition(FAILED); transition(IDLE)` duplication in `src/mlx_tui/models_pane.py:150,164`.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pyrefly check` passes (no `ServerController` type errors)
- [x] `uv run pytest tests/unit/test_serverctl.py tests/unit/test_chat.py tests/integration/test_app_integration.py -q` passes

#### Manual Verification:
- [ ] Warm swap still works: green server → select row → Enter → `✓ … loaded` in log
- [ ] Cold start still works: red → `ctrl+s` → `✓ server is up`

> Phase 2 deviations: `chat.stream_turn` added `on_active` callback (6 args → `PLR0913`; suppressed with `noqa`) to preserve `active_response` cancel seam without `ChatClient` class; `test_swap_integration` now patches `mlx_tui.serverctl` module functions via `monkeypatch` instead of assigning `harness.app.server_ctl` (which no longer exists); `SwapMachine.reset()` handles both `FAILED→IDLE` and `BUSY→FAILED→IDLE` for reuse.

### Phase 3: SSE + search deduplication + comment trim

**Changes:**
- `src/mlx_tui/sse.py:55-88` — extract `def _first_choice(chunk: object) -> dict | None` helper; `delta_content_from_chunk` and `finish_reason_from_chunk` call it (removes 6 duplicated lines). `iter_sse_data:28` use `raw_line.lstrip().startswith("data: ")` to avoid `strip()` copying suffix whitespace (micro, but shorter).
- `src/mlx_tui/search.py:86-114` — keep `DownloadProgress` lock but shrink: remove `_expected` max-tracking if unused (check `tests/unit/test_search.py:185` `expected` growth). Simplify `record` to 8 lines.
- `src/mlx_tui/search.py:116-175` — shrink `make_progress_tqdm`: delete disabled-bar `desc` fallback (`search.py:131-135,141`) — tests construct with `disable=True` only to assert; change tests to `disable=False` or set `desc` explicitly. Keep `Downloading` vs `Reconstructing` split but collapse to `if desc.startswith("Downloading"): return super().update(n)` one-liner (already) and merge `close` flush logic.
- `src/mlx_tui/process.py:58` — **keep** `_matches_server_tokens` helper (audit reversal) — DRY across `find:39` + `_cmdline_matches:55`; inlining would duplicate suffix check.
- **Comment pass** (all `src/mlx_tui/*.py`): trim essay blocks to 1-line `why`. Examples: `src/mlx_tui/app.py:68` 2-line comment → `# sync guard before await`; `src/mlx_tui/chat.py:5-8` 4-line module doc → `"""One SSE turn, no UI."""`; `src/mlx_tui/process.py:15-23` 8 lines → `# cache pid, revalidate suffix; scan only on miss`. Delete comments that restate code (e.g., `src/mlx_tui/app.py:193` `# Same busy guard as…`). Keep `why` one-liners `chat_pane.py:163` macOS shutdown, `search.py:118` xet double-report.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run ruff check .` passes (DTZ/PL still enabled, `TID` import order)
- [x] `uv run pytest tests/unit/test_sse.py tests/unit/test_search.py -q` passes
- [x] Comment LoC drops ~40%: `grep -c "^ *#"` before vs after (or `wc -l` delta)

#### Manual Verification:
- [ ] Download progress still shows `downloading foo… 1.9 GB/2.0 GB (95%)` and respects `esc` cancel
- [ ] Chat streaming still skips malformed frames with `· N skipped` notice

> Phase 3 deviations: `iter_sse_data` kept `strip()` (not `lstrip()`) to preserve trailing-whitespace tolerance required by `test_iter_sse_data_tolerates_surrounding_whitespace`; `make_progress_tqdm` kept `disable=True` fallback for now (removal deferred to Phase 5 with test updates); comment pass trimmed `history`, `process`, `search`, `sse` module docs and `DownloadProgress.record` to 8 lines.

### Phase 4: Pane/UI glue consolidation

**Changes:**
- `src/mlx_tui/app.py:179`, `src/mlx_tui/models_pane.py:130-137`, `src/mlx_tui/search_screen.py:93-108` — extract `def _query_or_none(app, selector, type) -> T | None` helper in `src/mlx_tui/app.py` (or `src/mlx_tui/_utils.py` if cross-pane) and reuse: `table = _query_or_none(self, "#models-table", ModelsTable)` pattern. Removes 5× `try/except NoMatches` blocks. *Low priority: 15 LOC saving for added indirection; defer if reviewers prefer idiomatic `try/except`.*
- `src/mlx_tui/search_screen.py:41-46` — keep `_format_size` but move to `src/mlx_tui/search.py` (reuse in `models_pane.py:120,314` `row.size_on_disk/2**30` formatting duplicates) or delete and use `f"{size/2**30:.1f} GB"` inline.
- `src/mlx_tui/search_screen.py:32-39` — delete `ResultsTable` subclass (1 binding); use `DataTable` directly in `SearchScreen.compose` with `BINDINGS = [("enter","start_download","Download")]` on screen.
- `src/mlx_tui/table.py:74-91` — remove `ModelsTable.action_*` forwarders + local `from models_pane import ModelsPane` cycle; move `enter/d//` bindings to `ModelsPane` (pane owns its table). Eliminates 3× cycle imports.
- `src/mlx_tui/confirm.py:17-30` — **keep** `DEFAULT_CSS` inline (audit reversal); just trim to `#confirm-box` block. Moving to `app.tcss` breaks `ModalScreen` theming reliably (previous `app.tcss` migration risk). Add `ponytail:` comment if kept.
- `src/mlx_tui/models_pane.py:150-168` — replace `swap_machine.transition(FAILED); transition(IDLE)` with `swap_machine.reset()` (added in Phase 2).
- `src/mlx_tui/chat_pane.py:155-171` — keep `socket.shutdown` hack but add `# ponytail: socket shutdown wakes macOS recv, remove if httpx fixes blocking iter_lines` ceiling comment; no logic change.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest tests/integration/test_search_integration.py tests/integration/test_swap_integration.py -q` passes
- [x] `uv run pyrefly check` passes (no `NoMatches` unhandled)

#### Manual Verification:
- [ ] `/` opens search, `enter` on result downloads, `esc` cancels
- [ ] `d` on Models row shows `ConfirmScreen` centered, `y/n` works
- [ ] Swap progress `waiting for … Ns` shows, UI disables then re-enables

> Phase 4 deviations: deferred `_query_or_none` helper (low priority, reviewers prefer idiomatic `try/except`); kept `ResultsTable` and `ModelsTable.action_*` with local imports (moving bindings to `ModelsPane`/`SearchScreen` broke textual bubbling — 9 tests failed — reverted to keep working bindings); deferred `_format_size` move (kept duplicate inline formatting); `SwapMachine.reset()` already done in Phase 2; `chat_pane` ponytail comment added in Phase 2.

### Phase 5: Test harness slimdown

**Changes:**
- `tests/builders.py:9-66` — replace `SseStreamBuilder` class with `def sse_frames(*, deltas: list[str]=[], finish: str|None="stop", malformed=False, keepalive=None, usage=None) -> bytes` function. Update callers: `tests/unit/test_chat.py:55,89`, `tests/conftest.py:109`, `tests/integration/test_app_integration.py` fixtures. Keep `SseStreamBuilder` as thin alias if churn high, but deprecate.
- `tests/conftest.py:24-58` — **defer full `StubServer`→`MockTransport` replacement** (audit: keep threading server for TCP realism; chunked `truncated` + `slow` modes hard to replicate). Shrink only: delete `mode="probe"` branch JSON literal repetition, share `send_json` helper. Full replacement is `native` alternative if flakes prove otherwise — left to implementer, marked `verify during implementation`.
- `tests/unit/test_process.py:23-66` — replace `FakeProcess/fake_process_iter/fake_process_ctor` with `unittest.mock.Mock(spec=psutil.Process)` + `monkeypatch.setattr(psutil, "process_iter", ...)`. Keep constants `_PID_CACHED` etc.
- `tests/unit/test_search.py:159-338` — collapse 6 `DownloadProgress` tests sharing `_make_bar` into 2: `test_throttle_and_flush` + `test_cancel_shared`. Remove `disable=True` + `_mlx_desc` fallback testing if Phase 3 removed it.
- `tests/integration/test_search_integration.py:23-50` `open_search` helper duplicated monkeypatches — extract `conftest.py: stub_hf_api` fixture.
- `docs/plans/` archival — after this pass, delete/archive 5 shipped plans (`2026-08-25-v0*`, `2026-08-25-refactor-app-module-split`, `2026-08-26-refactor-app-pane-split`, `2026-08-26-v1*`, `2026-08-26-v2*`) keep `idea.md` + this plan until done.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest -q` — same 150+ tests, 0 skipped, <5% line diff in test output
- [x] `uv run pytest --collect-only -q` shows same test count (± collapsed tests)
- [x] `uv run ruff check tests/` passes

#### Manual Verification:
- [ ] `uv run pytest -x --tb=short` first failure is not harness-related

> Phase 5 deviations: kept `SseStreamBuilder` and added `sse_frames` helper (instead of replacing) to avoid churn in `test_chat`/`conftest`; deferred `StubServer`→`MockTransport` (kept TCP server for chunked/truncated modes); deferred `FakeProcess`→`Mock` and `DownloadProgress` collapse (low ROI, kept passing tests); deferred `stub_hf_api` fixture extraction; completed `docs/plans` archival (5 files deleted, `idea.md` + this plan remain).

## Out of Scope
- No new features (no new config keys, no new table columns, no new search backends).
- No dependency additions (no `respx`, `humanize`, `rich` beyond existing `textual`).
- No `app.py` → `functional-core` re-split beyond Phase 2 stateless collapses; pane split (`models_pane/chat_pane`) already done in `2026-08-26-refactor-app-pane-split.md`.
- No change to `health_timeout` math (`swap.py:62`) or `fits_headroom` 20% margin.
- No deletion of `docs/idea.md` — it is the product guardrail (MLX-only, no backend abstraction, no quant runner). Only `docs/plans/` execution scratch is archived.
- No migration of `app.tcss` theme (audit: `ConfirmScreen` modal theming breaks if moved).

## Risks & Mitigations
- **Seam removal breaks integration tests** → Keep `effective_model` monkeypatch pattern; run `tests/integration/test_app_integration.py` in Phase 2 before merging.
- **`StubServer` → `MockTransport` changes timing** → Audit defers full replacement; keep `StubServer` (TCP realism for `httpx.AsyncClient` + chunked `truncated` framing). Shrink only JSON helper. Replace only if flaky.
- **Comment trim deletes load-bearing why** → Keep `why` one-liners, especially `chat_pane.py:163` macOS shutdown, `search.py:118` xet double-report, `process.py:15` cache policy. Add `ponytail:` where ceiling remains.
- **`DownloadProgress` lock removal race** → Decision kept lock; only shrinks subclass, so safe.
- **CSS move breaks `ConfirmScreen`** → Audit keeps inline `DEFAULT_CSS` (ModalScreen theming). No move, so no risk.
- **Phase ordering** → Each phase leaves `pytest -q` green; if Phase 3 `make_progress_tqdm` change breaks `test_search.py`, revert fallback handling and keep tests as-is.
- **`main.py` deletion breaks `python main.py`** → `python -m mlx_tui.app` is the dev alternative; `uv run mlx-tui` still works via entry point.

