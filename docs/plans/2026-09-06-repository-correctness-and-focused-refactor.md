# Repository Correctness and Focused Refactoring

**Date:** 2026-09-06
**Work Item:** n/a
**Status:** Complete

## Overview

Fix verified correctness gaps and reduce large modules along actual responsibility boundaries before adding functionality. Preserve the current product and CLI; file length is a review signal, not a hard acceptance limit. The user selected the seven-phase order below for this draft; implementation is not yet approved.

## Current State

Research covers the current working tree, including existing uncommitted edits. Preserve those changes. The completed `2026-09-06-fix-medium-low-priority-findings.md` supplies historical context, but some of its package paths have since been consolidated. References below describe the research snapshot and will move during implementation.

| Finding | Verified evidence and implication |
|---------|-----------------------------------|
| Size and responsibilities | `src/mlx_tui/app/__init__.py` has 603 lines, including CSS at 63–141 and status rendering at 364–404. `chat_pane.py` has 561 lines; `models_pane.py` has 499, including a 178-line boot worker at 232–409. These contain separable responsibilities. |
| Existing sound boundaries | `chat.py` owns HTTP streaming, `sse.py` protocol/accounting, `history/store.py` records, `history/tokens.py` context budgets, `history/sparkline.py` drawing, and `swap.py` pure policy. Preserve these boundaries. |
| F1: import cycle | `chat_pane.py:20` and `models_pane.py:17` import `app.operations`, executing `app/__init__.py:26–37` and importing the unfinished panes again. Fresh-process imports of either pane fail; app-first imports pass. `tests/conftest.py:17` imports the app first and masks this. |
| F2: healthy server killed by UI failure | `models_pane.py:362–394` verifies health and publishes UI changes in the same exception scope as failed-process termination. A mocked successful spawn/health followed by a refresh failure invoked cleanup on the healthy process. |
| F3: stale threaded search publication | `search_screen.py:109–139` has no submission identity. An event-controlled reproduction showed an older search overwriting newer results. At 194–205, a late size callback caches old metadata then updates a deleted row; a mounted reproduction raised `CellDoesNotExist`. |
| F4: stale listener cache | `process.py:123–134` rechecks PID, creation time and argv but not the listener. A mock reproduction returned the cached identity after its listener disappeared. `tests/unit/test_process.py:155` explicitly codifies skipping connection checks. |
| F5: nonfinite sampling values | `chat_pane.py:44–55` accepts `nan`, which JSON encoding rejects. Config/preset paths use different coercion: TOML NaN becomes a bound in `config.py:89–101`, rather than being treated as invalid. |
| F6: uncaught editor errors | `app/__init__.py:548–563` creates a template outside its try block and catches only `OSError` around editor execution. Mock reproductions confirmed uncaught template `PermissionError` and malformed `$EDITOR` quoting `ValueError`. |
| Parameter ownership | `app/__init__.py:333–337` accesses private ChatPane parsing/input methods. Controls and normalization need a public boundary. |
| Test organization | `tests/integration/test_app_integration.py` has 1,573 lines covering chat, cancellation, context, metrics, status, presets and editing. `test_swap_integration.py` has 1,043 covering boot, warm loading, deletion and rescan. `tests/unit/test_history.py` has 649 spanning several domain modules. |
| Documentation drift | `ARCHITECTURE.md` lists obsolete pane package initializer paths and MemoryStore terminology. Update it to the final layout without rewriting historical plans. |

Verified baseline:

- `rtk proxy uv run pytest -q`: **406 passed in 81.69 seconds**.
- `rtk proxy uv run ruff check .`: passes.
- `rtk proxy uv run ruff format --check src tests`: passes, 45 files formatted.
- `rtk proxy uv run pyrefly check --min-severity warn`: **0 diagnostics, 105 suppressed**. Suppressed code is not thereby proven correct.
- `rtk proxy uv run python tests/artifact_smoke.py`: wheel and rebuilt-sdist install/import/help checks pass outside the checkout.
- `rtk proxy uv run pytest --collect-only -q`: **406 collected**.
- Fresh-interpreter imports of `mlx_tui.chat_pane` and `mlx_tui.models_pane` fail; importing `mlx_tui.app` succeeds.

Use existing pytest/pytest-asyncio, HTTPX mocks, Textual pilots, `tests/conftest.py:AppHarness`, and `tests/builders.py:sse_frames`. Control race tests with events rather than long sleeps. No actual model downloads, cache deletion, or existing-server termination in automated tests. Local passing checks do not establish current remote CI results or real MLX behavior.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Targeted fixes and focused splits | Correctness only; focused cleanup; blanket 500-line ceiling | The selected draft scope improves real boundaries without recreating the earlier widget-mutating helper/facade split. |
| Move operations outside app | Move shared module; lazy exports; relocate entire app class | A small module move breaks the demonstrated cycle while preserving `mlx_tui.app:main`. |
| UI-free boot function returns verified probe | Mixed worker; function; controller framework | Process success must be independent of subsequent rendering. Existing BootPlan plus data/callbacks suffice. |
| Dedicated parameter and status widgets | Widgets; helpers accepting whole panes; mixins | Each component owns its children and a narrow public API. No helpers mutate a supplied app or pane. |
| Per-widget DEFAULT_CSS | Owner-local CSS; external global stylesheet | Follow SearchScreen's existing convention, reduce coordinator noise, avoid new resource packaging. Preserve selector behavior. |
| Search generation integer | Rely on cancellation; generation checks; request manager | Cancelled threads can finish. Reject stale success/error callbacks before mutation. |
| Local cached-listener recheck | Trust PID; full global scan; per-process connections | Preserve the cache optimization while validating current endpoint ownership. |
| Invalid nonfinite values follow each boundary's existing policy | Reject/default; clamp; fail at request time | UI falls back to defaults, config treats the field as absent, presets skip the invalid entry. Finite out-of-range values still clamp. |
| Behavior-based test moves | Line-limit splitting; fixture rewrite; behavior groups | Retain assertions and collection, with no new framework or dependencies. |

New paths/signatures below are proposed specifications, **verify during implementation**, not claims that the symbols already exist. Existing APIs remain unchanged unless explicitly listed. Each change bullet is intended as a small edit; split larger test groups into individual moves within their phase.

## Implementation Phases

### Phase 1: Remove import-order coupling

Make panes and the operation primitive independently importable.

**Changes:**
- Move `src/mlx_tui/app/operations.py` to `src/mlx_tui/operations.py`, keeping `OperationKind` and `OperationCoordinator` behavior unchanged. Do not duplicate enum definitions or retain an eager importing facade.
- Update imports in `src/mlx_tui/app/__init__.py`, `src/mlx_tui/chat_pane.py`, `src/mlx_tui/models_pane.py`, `tests/unit/test_operations.py`, `tests/integration/test_app_integration.py`, and `tests/integration/test_swap_integration.py`. Search for remaining live references before removing the old file. Preserve the app class and CLI paths.
- In `tests/unit/test_operations.py`, add parameterized subprocess imports using `sys.executable`, each in a fresh interpreter: chat pane, models pane, operations, app. Assert importing operations does not load `mlx_tui.app` into `sys.modules`. This must bypass conftest's app-first initialization.

**Success Criteria:**

#### Automated Verification:
- [x] Fresh-interpreter regressions and existing tests pass: `rtk proxy uv run pytest -q`.
- [x] Imports pass lint/types: `rtk proxy uv run ruff check .`; `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Review runtime imports: operations depends on neither app nor widgets; the app entry point remains unchanged. No visual check is required for this module move.

### Phase 2: Separate boot execution from UI publication

Keep verified servers running when rendering fails, while cleaning up actual failed boots.

**Changes:**
- New `src/mlx_tui/boot.py`: define `PreparedCommands` with `start: str | list[str]`, `stop: str | list[str] | None`, `shell: bool`, `env: dict[str, str] | None`, and `display: str`. Define `prepare_commands(config: AppConfig, plan: BootPlan) -> PreparedCommands`, moving validation from `models_pane.py:251–314`. Validate both commands before stop; retain argv replacement, shell opt-in, environment handoff, and error wording.
- In `src/mlx_tui/boot.py`, define `execute_boot(plan: BootPlan, config: AppConfig, *, host: str, port: int, on_line: Callable[[str], None], on_tick: Callable[[int], None]) -> ServerProbe`. Move stop execution, spawn/grace, timeout calculation, and health checks here. Keep the owned process local and clean it up on failure before verified health. Returning the probe ends this cleanup responsibility. Log/progress callbacks are observational: their failures must not terminate a successful boot. No Textual/app/pane imports.
- In `src/mlx_tui/serverctl.py`, rename `_terminate_failed_process` to `terminate_failed_process` and update internal callers and `tests/unit/test_serverctl.py`; preserve group targeting, bounded waits and reaping. This becomes the boot module's supported project-internal cleanup API.
- In `src/mlx_tui/models_pane.py`, retain the decorated `run_boot` worker. Snapshot config/host/port once, adapt callbacks, and invoke `execute_boot`. Discover process identity and publish UI changes only after it returns; publication failures get a distinct diagnostic and never invoke process cleanup. Retain finally-based lease release and existing fallback when UI dispatch fails.
- New `tests/unit/test_boot.py`: test invalid commands execute no stop; failed stop executes no start; early crash/health failure cleans up; verified health does not clean up; log/progress callback exceptions do not kill a successful process. Use existing `BootPlan`, `AppConfig`, `ServerProbe` and mocked subprocess seams.
- In `tests/integration/test_swap_integration.py`, retarget moved dependency lookups and add successful-boot/UI-refresh-failure coverage: no termination, lease idle, controls restored. Preserve existing disposable process tests rather than replacing them with mocks.

**Success Criteria:**

#### Automated Verification:
- [x] Boot and publication regression cases plus existing suite pass: `rtk proxy uv run pytest -q`.
- [x] Module checks pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] On a disposable configured server, start/restart still streams logs/progress and restores controls; failed startup cleans up only the owned group.
- [ ] Review the success boundary: no post-health UI path can enter failed-process cleanup.

### Phase 3: Reject obsolete search and size callbacks

Make the latest submission authoritative despite late thread completion.

**Changes:**
- In `src/mlx_tui/search_screen.py`, add `_search_generation: int`, increment it for every submission including empty submissions, and invalidate it on unmount. Pass the captured generation through `_run_search`, `_fetch_size`, and success/error callbacks.
- Update `_populate`, `_size_failed`, `_fill_size_cell`, and search error publication to reject obsolete generations or unmounted screens before logging, caching or touching widgets. Size callbacks must also require the repo in the current result set. Keep decorators and worker ownership on the screen.
- Snapshot generation when starting metadata lookup; publish size/revision together after acceptance. Keep the captured revision used by download and its acknowledged cancellation contract unchanged.
- In `tests/integration/test_search_integration.py`, add event-controlled old-after-new completion, empty-submit invalidation, removed-row size completion, same-repo/new-generation metadata, and stale-error scenarios. Assert current rows/status/revisions stay intact and no UI/worker exception escapes. Update direct callback calls for the new argument.

**Success Criteria:**

#### Automated Verification:
- [x] Race regressions and existing download/cancel cases pass: `rtk proxy uv run pytest -q`.
- [x] Callback signatures pass lint/types: `rtk proxy uv run ruff check .`; `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Rapid searches, clearing the query, and closing/reopening while metadata is pending keep the latest results and a usable modal. Download cancellation still waits for acknowledgement.

### Phase 4: Revalidate cached process ownership

Make cached identity require a current listener.

**Changes:**
- In `src/mlx_tui/process.py:123–135`, inspect `proc.net_connections(kind="tcp")` on a matching PID/creation-time/argv cache hit. Require LISTEN on the configured port using existing `_conn_status` and `_conn_port`. Clear an invalid/uninspectable cache and follow existing discovery; never return an unverified cached PID. Preserve the non-loopback and permission-aware behavior.
- In `tests/unit/test_process.py`, give `FakeProcess`/`_install` per-process connection data. Change `test_cached_hit_short_circuits_connections` to require the local check while avoiding a global scan on valid hits. Add live-PID/listener-loss, replacement listener, and cached-inspection-denied cases. Retain PID reuse, pidfile, denied-global-scan fallback, and real disposable listener coverage.

**Success Criteria:**

#### Automated Verification:
- [x] Process and app identity regressions pass: `rtk proxy uv run pytest -q`.
- [x] Checks pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] On macOS, remove a disposable listener while keeping its process alive: RSS must stop being attributed to that endpoint. Denied inspection remains unavailable telemetry rather than a crash.

### Phase 5: Give parameter controls a single owner

Reduce ChatPane responsibility and remove private parameter access from the app.

**Changes:**
- New `src/mlx_tui/params.py`: define `ParamsPane(Collapsible)` owning current parameter rows, IDs, labels, tooltips, submit normalization, and CSS. Move `_parse_clamped_float` and `_parse_clamped_int` here. Expose `read_values() -> tuple[float, float, int]`, `apply_values(temperature: float | None, top_p: float | None, max_tokens: int | None) -> None` (None preserves current input), and `apply_config(config: AppConfig) -> None` (None resets to existing defaults). Keep the collapsed initial state and `params-collapsible` ID.
- In `src/mlx_tui/params.py`, use `math.isfinite` so nonfinite float text falls back to the existing default; preserve finite clamping. Stop the owned parameter submission event if necessary so it cannot reach the chat submission path.
- In `src/mlx_tui/chat_pane.py`, compose ParamsPane and use its public API for submission/context budgets. Keep `apply_config_params` as the existing app-facing bridge. Remove moved parsing/input/submit methods. Retain transcript, context, cancellation and worker state on ChatPane.
- In `src/mlx_tui/app/__init__.py`, `_apply_preset` uses ParamsPane's public apply/read API before replacing config, preserving partial-preset semantics. Remove calls to private ChatPane methods and move parameter CSS to ParamsPane.
- In `src/mlx_tui/config.py`, make `_coerce_float_strict` reject nonfinite numbers and integer-to-float overflow; reuse it for float config fields instead of duplicate coercion. Invalid config fields remain absent/defaulted, and finite values still clamp. `src/mlx_tui/presets.py` uses that existing helper and continues skipping invalid entries.
- Update `tests/unit/test_params.py`, `tests/unit/test_config.py`, `tests/unit/test_presets.py`, and `tests/integration/test_app_integration.py`. Test NaN/infinities across UI/TOML/presets, actual outgoing payloads, config reset, partial presets and context reservation. Update ownership assertions without weakening behavior checks.

**Success Criteria:**

#### Automated Verification:
- [x] Parameter validation and existing chat/config/preset tests pass: `rtk proxy uv run pytest -q`.
- [x] Checks pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Expand Params, enter invalid/out-of-range values, cycle a partial preset, reload config, and submit chat. Inputs/focus remain usable and nonfinite values no longer cause JSON failures.

### Phase 6: Isolate status rendering and close editor error paths

Reduce coordinator presentation code while retaining explicit polling/config ownership.

**Changes:**
- New `src/mlx_tui/status_bar.py`: define `StatusBar(Horizontal)` owning current status/memory children and CSS. Expose `show_status(*, state: str, model: str | None, port: int, can_start: bool, rss_gib: float | None, snapshot: MemorySnapshot) -> None`. Move formatting from `_render_status` unchanged. Do not read app config or inspect processes/memory in this widget.
- In `src/mlx_tui/app/__init__.py`, compose StatusBar with the existing ID. Keep `_render_status` as a narrow adapter, including its fallback snapshot and NoMatches handling, passing explicit values to StatusBar. Keep polling, identity, cross-pane coordination and shared logging here.
- Move existing owner-specific selectors from app DEFAULT_CSS into `src/mlx_tui/chat_pane.py`, `src/mlx_tui/metrics_pane.py`, and `src/mlx_tui/models_pane.py`. Preserve values/selectors; keep application layout/shared log CSS in the app. Do not combine with redesign.
- In `action_edit_config`, parse `$EDITOR` before suspension and reject an empty argv with a diagnostic. Catch malformed quoting `ValueError` and template-write/editor-launch `OSError`. Preserve config/presets and emit no successful reload on failure. Keep suspend/resume, nonzero-exit and TOML handling on the app.
- In `tests/integration/test_app_integration.py`, preserve mounted memory/status checks and add template-write failure, malformed quoting, and empty-editor cases using `_stub_editor`/`_stub_suspend`. Assert unchanged config, no false reload, and no editor invocation for invalid argv.

**Success Criteria:**

#### Automated Verification:
- [x] Status/editor regressions and existing suite pass: `rtk proxy uv run pytest -q`.
- [x] Checks pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Compare all tabs at narrow/normal widths: status text, memory bar, transcript height, context colors, parameter labels and sparklines retain their layout.
- [ ] Malformed editor settings/unwritable template paths leave the app usable with a clear error; valid editing restores the terminal.

### Phase 7: Organize tests and document final boundaries

Move test groups after behavior stabilizes and verify installed artifacts.

**Changes:**
- Capture `pytest --collect-only -q` output immediately before moves and compare collected function/parameter names afterwards, ignoring changed file paths. Preserve every case/assertion; compare against the post-fix count, not just the original 406.
- New `tests/integration/test_chat_integration.py`: move payload, successful/empty/length/error/truncated reply, transcript/history and failed-prompt-exclusion tests from app integration, with `_STAMP_RE`/`_REPLY`. Keep the end-to-end status-plus-chat smoke in the app file; avoid duplicate cases.
- New `tests/integration/test_chat_cancellation.py`: move cancellation timing, repeated cancellation, partial-output and operation-exclusion tests, with `_current_op` and its local helpers.
- New `tests/integration/test_context_integration.py`: move `test_params_sidebar_sends_payload`, all `test_ctx_bar_*`, reservation and over-limit scenarios. New `tests/integration/test_metrics_integration.py`: move metrics-tab and prefill/decode table scenarios. Keep memory/status polling in the app file.
- New `tests/integration/test_config_integration.py`: move preset cycling, removed-system reload and editor tests with `_stub_editor`, `_stub_suspend`, `_config_model`. The remaining app integration file covers polling, identity, diagnostics and shared shell behavior.
- New `tests/integration/conftest.py`: move the existing `stub_harness` fixture from swap tests, preserving scope/behavior. New `tests/integration/model_helpers.py`: hold shared `ROW`, `_stub_rows`, `_no_process`, `_fake_process_4242`, `_select_row`, `_log_text` used by resulting model test files. Import helpers from this module, not collected test modules.
- New `tests/integration/test_boot_integration.py`: move cold-start/restart execution, command/shell validation and failed-boot cleanup scenarios. New `tests/integration/test_delete_integration.py`: move deletion confirmation/race/error tests with `_press_delete_and_wait_confirm` and `_controls_restored`. Keep warm loading/policy refusal/rescan in `test_swap_integration.py`. Move pure `test_resolve_swap_action_branches` to existing `tests/unit/test_swap.py`.
- Keep record/store tests in `tests/unit/test_history.py`. New `tests/unit/test_tokens.py` receives token estimation/trimming/preparation/context-bar tests and `u`/`a`. New `tests/unit/test_sparkline.py` receives chat/memory rendering/shade tests. Move `test_fallback_output_scales_with_response_length` to existing `tests/unit/test_sse.py`. Preserve assertions and update stale group docstrings.
- Update `ARCHITECTURE.md`: real file tree, operations import direction, boot success boundary, search generations, cached-listener verification, ParamsPane/StatusBar ownership, and current deque memory storage. Leave historical plans intact.
- Inspect obsolete suppressions only in touched lines and remove those justified by the changes. Do not lower strictness or bulk-add ignores. Run final checks and existing artifact smoke; no new dependencies or CI framework are needed.

**Success Criteria:**

#### Automated Verification:
- [x] Collection preserves every post-fix case/parameterization: `rtk proxy uv run pytest --collect-only -q` (compare captured before/after names ignoring moved paths).
- [x] Moved suite passes: `rtk proxy uv run pytest -q`.
- [x] Final gates pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check --min-severity warn`.
- [x] Wheel and rebuilt sdist install/import/help pass: `rtk proxy uv run python tests/artifact_smoke.py`.

#### Manual Verification:
- [ ] Review test moves as moves, with equivalent fixture scope and unchanged assertions; no missing or duplicate collection.
- [ ] Verify architecture against disk: each new production component has one owner, and no helper mutates a supplied app/pane.
- [ ] Complete Phases 2–6 terminal checks on a disposable local setup. Passing headless tests do not prove visual quality or real MLX behavior.

## Out of Scope

- Implementing this draft before approval, new functionality, product/backend expansion, releases or deployment.
- Hard line limits, one file per method, widget-mutating facade helpers, mixins, generic controllers, event buses or dependency injection frameworks.
- Splitting coherent serverctl/search/SSE/swap/history production modules solely by size.
- Rewriting chat transport/cancellation or extracting another worker state machine. Reassess further pure formatting extraction only after parameter ownership is clear.
- Broad optimization, a suppression purge, coverage-percentage targets, new dependencies or CI expansion.
- Destructive tests against real cache/server state or actual model downloads in automated tests.

## Risks & Mitigations

- Uncommitted work changes → record the starting diff and recheck references; preserve existing changes rather than restoring historical code.
- Repeating a harmful split → widgets own children; boot takes explicit data/callbacks and returns a result, never an app/pane.
- Enum identity breaks → one atomic move and every import updated; fresh-interpreter regressions.
- Boot cleanup changes → test pre-success failures and post-success publication failures separately; retain process-group tests.
- Generation guard misses a path → cover success/error, empty submit, unmount and same repo across generations before mutation.
- Listener checks vary by OS/permission → local check on cache hits and existing discovery fallback; validate on macOS.
- Parameter extraction changes event/preset semantics → preserve IDs and defaults and assert real outgoing payloads plus partial-preset behavior.
- CSS specificity changes → retain selectors first and compare mounted/terminal layouts without redesign.
- Test moves hide lost scenarios → before/after collection comparison and unchanged assertions, separated from behavior edits.
- New specifications need adjustments → new paths/signatures are explicitly verify during implementation; keep all callers consistent.

Requirement mapping: correctness → Phases 1–6; smaller focused production files → Phases 1/2/5/6; maintainable tests → Phase 7; architecture and delivery checks → Phase 7. Before approval, suggest the `review-plan` skill for an independent quality pass. Mark Approved only after explicit user confirmation.
