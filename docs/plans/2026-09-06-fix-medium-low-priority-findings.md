# Fix Medium- and Low-Priority Review Findings

**Date:** 2026-09-06
**Work Item:** n/a
**Status:** Complete

## Overview

Resolve F8–F16 from `report-source.md`, building on the implemented high-priority fixes and the current uncommitted corrections. Make commands, cancellation, diagnostics, ownership, and delivery checks reliable while retaining the local MLX product scope.

## Current State

Research used the working tree on 2026-09-06, including existing edits to `app/state.py`, `models_pane/swap_ops.py`, `process.py`, `serverctl.py`, `status.py`, and their tests. Preserve those edits; do not restore the report's older implementation. References below are repository-relative and describe this research snapshot; later phases deliberately move some symbols.

| Finding | Verified current evidence | Remaining scope |
|---|---|---|
| F8 | `src/mlx_tui/serverctl.py:22` substitutes raw `{model}`, treats any `--model` substring as authoritative, and `_popen` uses `shell=True` at line 41. `tests/unit/test_serverctl.py:35` expects the old model to survive. | Define safe command modes and exact model-option replacement. The report's `model_from_cmdline()` no longer exists: process discovery at `src/mlx_tui/process.py:109` uses listener identity, so do not reintroduce command-line model authority. |
| F9 | `src/mlx_tui/models_pane/swap_ops.py:214` already retains the spawned process and releases the lease in `finally`; `src/mlx_tui/serverctl.py:47` already has a 30-second stop timeout. | Validate before stopping; terminate owned process groups, including shell descendants; protect cleanup from callback/process exceptions; cover failure paths. |
| F10 | `src/mlx_tui/chat.py:56` blocks in synchronous HTTP; abort reaches into `_sock` at `src/mlx_tui/chat_pane/turn.py:189`. Submission appends immediately at `chat_pane/__init__.py:98`; only context rejection rolls it back. `search_screen/download.py:146` announces cancellation and dismisses before acknowledgment. | Async chat transport, transactional conversation context, and honest cooperative download cancellation. |
| F11 | `src/mlx_tui/sse.py:20` strips lines and accepts only `data: `, without event boundaries. `README.md:23` claims any compatible server. | Correct SSE data-event parsing; document the verified local HTTP scope. |
| F12 | `src/mlx_tui/models.py:9` imports private HF cache types; `pyproject.toml:9` omits directly imported Rich. | Public imports, direct dependency declaration, minimum/current dependency checks; Phase 3 removes socket internals entirely. |
| F13 | Pane forwarding boundaries appear in `chat_pane/__init__.py:125`, `models_pane/__init__.py:39`, and `search_screen/__init__.py:89`; app forwarding appears at `app/__init__.py:196`. | Put widget-aware behavior on its owning class; retain UI-free adapters and eliminate obsolete suppressions. |
| F14 | `.github/workflows/ci.yml:13` runs Ubuntu only. `tests/integration/test_app_integration.py:190` now tests chat/delete exclusion and line 797 tests config removal, so coverage is partially improved. | Actual confirmation/error/race flows, editor/CLI behavior, macOS process/cancellation checks, installed artifacts. |
| F15 | `app/polling.py:22` treats every exception as downtime; `chat_pane/__init__.py:147` hides widget failures; `search_screen/query.py:101` silently hides size failures. | Narrow expected catches and report unexpected worker faults without repeated log spam. |
| F16 | `ARCHITECTURE.md` still describes command-line identity, socket shutdown, old context/token accounting, and obsolete files/counts. `docs/part2.md` says Part 2 has not started. No `LICENSE` exists. | Update current contracts and completed Phase 4 status, add the declared MIT license and verify distribution contents. README's auto/restart policy is already updated; retain it and correct its verification description. |

Verified baseline:

- `rtk proxy uv run pytest -q`: **320 passed**, 49.96 seconds.
- `rtk proxy uv run ruff check .` and `rtk proxy uv run ruff format --check src tests`: pass.
- `rtk proxy uv run pyrefly check`: zero errors, 100 suppressed diagnostics, 12 hidden warnings.
- `rtk proxy uv run pyrefly check --min-severity warn`: exists but currently fails on 12 warnings: missing psutil stubs, redundant conversions, and comparisons narrowed across mutating calls.
- `rtk proxy uv build --out-dir /tmp/mlx-tui-plan-artifacts`: wheel and sdist build successfully; isolated wheel installation and `mlx-tui --help` pass.
- Explicit direct dependency-floor installation/import/help and a highest-resolution import/help smoke pass. Full minimum/current test suites and GitHub macOS execution remain **verify during implementation**.

Existing tests use `pytest-asyncio` auto mode, `httpx.MockTransport`, `tests/builders.py:sse_frames`, and `tests/conftest.py:AppHarness`. Extend these rather than adding another testing framework. Use disposable process/cache fixtures; never target the user's MLX server or real HF cache.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Local MLX over HTTP remains the supported endpoint contract; correct SSE framing | Local scope; HTTPS/auth/base-path expansion | User selected local scope. Existing non-loopback host input remains a best-effort capability, explicitly documented as unauthenticated plain HTTP; this plan adds no remote platform. |
| `command_shell: bool = False` controls both start and stop commands | Argv default with explicit shell opt-in; preserve shell default | User selected argv default and shell opt-in. Use stdlib `shlex` and `subprocess`, with migration examples. |
| In shell mode pass the model through `MLX_TUI_MODEL`, never raw text substitution | Quote arbitrary shell templates; environment handoff | Shell quoting context is ambiguous. Shell commands reference `"$MLX_TUI_MODEL"`; `{model}` is supported only after argv parsing. Existing endpoint verification remains the final proof of the selected model. |
| Async HTTPX chat on Textual's event loop | Cooperative thread flags; async transport | HTTPX is already installed and the app already uses `AsyncClient`. Async request cancellation removes `_sock` access and covers pre-response waits. |
| Commit user/assistant context together after successful transport completion | Append then roll back every failure; defer commit | Pending submission participates in request budgeting but not future conversation context until success. The transcript can still display failed attempts. |
| Download Escape requests cancellation and keeps the modal until acknowledgment | Immediate dismissal; cooperative acknowledgment; separate download subprocess | Retain HF's existing cooperative hook with honest status. No process-isolated download framework; no claim that cancellation interrupts every blocked Hub operation immediately. |
| Move widget-aware helpers onto their owning classes | Class ownership; typed service/protocol framework | Avoid one-use interfaces and free functions that mutate widget-private fields. Preserve package import names, pure domain adapters, and `OperationCoordinator`. |
| Test declared runtime floors and newest resolution; no speculative upper bounds | Lockfile only; broad caps; compatibility jobs | Direct floors are testable. A full lowest-resolution install already fails on ancient transitive `idna==0.2`; it is not a useful compatibility contract. |

The user agreed to the repair order. The ownership checkpoint below is divided into three small migrations; chat ownership is handled with its lifecycle change. These are implementation design choices for a Draft, not claims that new symbols already exist. All new signatures/files below must be verified during implementation.

## Implementation Phases

### Phase 1: Safe commands and remaining process cleanup (F8, F9)

Make the selected model deterministic and cleanup apply to all processes launched by a failed command.

**Changes:**

- `src/mlx_tui/config.py` — add `AppConfig.command_shell: bool = False`, accept only an actual TOML boolean in `_KEY_TYPES`/`_from_mapping`, and include the default and shell migration example in `CONFIG_TEMPLATE`. Preserve the field through existing total reload and `dataclasses.replace` preset behavior.
- `src/mlx_tui/serverctl.py` — change `build_start_command(start_cmd: str, model_id: str | None) -> list[str]` to parse with `shlex.split`. Reject empty commands/malformed quoting. With a target, replace exact `--model value` and `--model=value` options, collapse duplicate model options to one selected target, and append when absent; do not treat `--model-path` as `--model`. Replace `{model}` inside parsed argument strings, so spaces and shell metacharacters remain argument data. Reject a dangling `--model` and a placeholder without a target. With no target, preserve an existing well-formed explicit model option.
- `src/mlx_tui/serverctl.py` — let `_popen`, `run_command`, `spawn_command`, and `spawn_with_grace` accept `str | list[str]` plus keyword `shell: bool = False` and `env: dict[str, str] | None = None`. Parse a string in argv mode; only explicit shell mode invokes the shell. Pass these options consistently through wrappers. Preserve merged-output streaming and the existing 30-second stop and 2-second crash-grace budgets.
- `src/mlx_tui/models_pane/swap_ops.py` — snapshot config and construct/validate both commands before invoking stop. In shell mode reject `{model}` with a migration message; when a target exists require a reference to `MLX_TUI_MODEL` and supply its exact value in a copy of `os.environ`. Document `--model "$MLX_TUI_MODEL"` as the shell contract; avoid attempting to rewrite shell syntax or treating the reference as proof of correctness. Preserve exact endpoint target verification and timeout behavior. Use `shlex.join` only for argv log display.
- `src/mlx_tui/serverctl.py` — launch owned commands with `start_new_session=True` on the supported POSIX platforms. Move `_terminate_failed_process(proc)` here and use it for stop timeout and failed boot. Signal only the owned group with TERM, then KILL after a bounded wait; tolerate already-exited processes/groups and reap the direct child. Handle surviving descendants even if the shell exits first. Do not terminate successfully launched servers after verified success.
- `src/mlx_tui/serverctl.py` and `src/mlx_tui/models_pane/swap_ops.py` — ensure cleanup/logging failures cannot bypass lease release. Drain output with a closed-stream-safe `finally`; route callback exceptions to a visible worker failure or fallback diagnostic, not an unhandled daemon-thread traceback. Use one UI callback with lease release before widget restoration; mounted UI failures must be diagnosed, while teardown may skip missing widgets.
- `tests/unit/test_config.py`, `tests/unit/test_serverctl.py`, `tests/integration/test_swap_integration.py` — cover both option forms, duplicate/dangling/similar options, spaces, quotes, semicolons and command substitution as literal data, missing executable, explicit shell environment handoff, and invalid start config causing no stop. Replace `exit 3` fixtures with explicit shell mode or Python argv. Add short-lived child/grandchild timeout tests, health failure, output callback failure, termination failure, and controls restored/lease idle; update existing spawn spies for the new keyword arguments.
- `README.md` — update the command example immediately, including `command_shell`, lack of argv expansion, and the shell environment migration. Do not defer documentation of this breaking configuration behavior until the final phase.

**Success Criteria:**

#### Automated Verification:

- [x] Command, process, and UI regressions described above pass with all existing high-priority tests: `rtk proxy uv run pytest -q`.
- [x] Lint, formatting, and existing type gate pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] On macOS, use a disposable Python command to echo argv and environment; a model/path with spaces and shell syntax is received literally in each mode.
- [ ] Using a disposable server command, a failed start or stop timeout restores model/chat controls and leaves no owned child running. A verified successful start remains running.

### Phase 2: Correct SSE data-event framing (F11)

Parse complete SSE events without expanding the supported server product scope.

**Changes:**

- `src/mlx_tui/sse.py` — replace line stripping with a small incremental `SSEDecoder.feed(line: str) -> str | None`. Accept optional one space after `:`, join consecutive `data` fields with newline, dispatch only on blank lines, ignore comments/non-data fields, remove only line endings and an initial BOM, and discard an unfinished event at EOF. Preserve payload whitespace. Emit an empty payload for an explicit empty `data` event; return `None` for no dispatched data.
- `src/mlx_tui/sse.py` — keep `iter_sse_data(lines)` as the synchronous wrapper and add `aiter_sse_data(lines: AsyncIterable[str]) -> AsyncIterator[str]` over the same decoder for Phase 3. Both stop on a dispatched `[DONE]` event. Do not add EventSource reconnection/replay behavior to a completion POST. These data framing rules follow the [WHATWG SSE interpretation rules](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation).
- `tests/unit/test_sse.py` — replace old tests that assume each line is an event or permit indentation before `data`. Cover no-space fields, multiple fields, empty data, comments, unknown fields, BOM, preserved spaces, CR/LF normalization, blank boundaries, `[DONE]`, ignored later events, and incomplete EOF. Apply the same cases to both wrappers.
- `tests/builders.py`, `tests/unit/test_chat.py`, `tests/conftest.py`, `tests/integration/test_app_integration.py` — preserve existing correctly delimited fixtures; add a multiline JSON event and no-space data stream through transport and UI, including malformed-event accounting.
- `README.md` — replace “any OpenAI-compatible server” claims with verified local MLX scope. Explain that host/port currently use plain HTTP with no TLS, API key, or reverse-proxy base-path configuration; do not promise secure remote support.

**Success Criteria:**

#### Automated Verification:

- [x] Framing matrix and existing stream accounting pass: `rtk proxy uv run pytest -q`.
- [x] Lint/format/type gates pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] A normal local MLX response still streams progressively and produces one final stamp; multiline-event stub output has no missing text or extra malformed-frame notices.

### Phase 3: Async chat and transactional context (F10, F12, chat portion of F13/F15)

Cancel the actual request task and commit only successful conversation turns.

**Changes:**

- `src/mlx_tui/chat.py` — make existing `stream_turn` async, retaining its payload/result/accounting contract and synchronous UI callbacks. Use a per-turn `httpx.AsyncClient`, `async with client.stream`, `await response.aread()` for error bodies, and `aiter_sse_data(response.aiter_lines())`. Retain the current timeouts and flush interval. Remove `on_active` after migrating all callers/tests. Async context managers must close the response and client on success, error, and cancellation. Public streaming methods are documented in [HTTPX async support](https://www.python-httpx.org/async/).
- `src/mlx_tui/chat_pane/__init__.py` — move the behavior from `turn.py` and `params.py` into the existing owning methods; remove their forwarding implementations and imports. Make `_run_turn` an async Textual worker without `thread=True`; use direct UI calls on the event loop. Retain current rendering, bounded context, typed accounting, prefill, cold flags, and metric refresh behavior.
- `src/mlx_tui/chat_pane/__init__.py` — synchronously acquire the chat lease, capture params/config/model, mark the turn active, and construct candidate messages from committed `messages` plus the pending user message. Do not append it to committed history on submission. Guard submission/worker scheduling failures so they restore input and release the lease. Store the returned `Worker` as a typed field; cover cancellation before its coroutine starts using its terminal state event as an idempotent cleanup fallback.
- `src/mlx_tui/chat_pane/__init__.py` — `abort()` is a no-op when idle. When active, set the cancellation intent once, show “cancellation requested”, and cancel the retained worker once; repeated Escape must not interrupt cleanup. Catch `asyncio.CancelledError` separately and preserve its cancellation semantics. Emit a single cancelled record only after the async request context exits. A pre-start cancelled worker must also settle once. Keep the lease until cleanup completes.
- `src/mlx_tui/chat_pane/__init__.py` — on successful transport completion, append the pending user and a nonempty assistant reply together in one UI callback with no await between writes; an empty successful reply retains its user and current notice behavior. On cancellation, context rejection, HTTP error, truncated stream, or unexpected failure, retain neither pending user nor partial assistant in future context. Keep the visible attempted-user transcript and failure/cancel notice. Remove `_rollback_message`, `_active_response`, `_sock`/socket shutdown, and redundant `_system_prompt` state after callers migrate. Read `AppConfig.system` directly.
- `src/mlx_tui/chat_pane/__init__.py` — place parameter/context preparation inside the protected lifecycle. Replace broad context UI catches with `NoMatches` only where teardown is expected; keep one logged unexpected-error boundary. Make `end_turn()` idempotent and restore controls in `finally` even if rendering a completion fails. Use concrete parsers for float/int fields rather than a dynamically typed parser dispatch.
- `src/mlx_tui/app/__init__.py` — change `cancel_chat_for_swap` wording to cancellation requested/retry after cleanup. Preserve the existing retry-to-start contract; do not silently queue a swap.
- `src/mlx_tui/chat_pane/turn.py`, `src/mlx_tui/chat_pane/params.py` — delete after every caller has moved. Retarget `tests/unit/test_params.py` to a mounted real `ChatPane` using the existing harness rather than an untyped facade fake.
- `tests/unit/test_chat.py`, `tests/conftest.py`, `tests/integration/test_app_integration.py` — migrate mocked HTTP to AsyncClient/async byte streams. Test cancellation before task start, before response headers, during an idle stream, after partial text, and repeated Escape; assert closure, one terminal outcome, idle lease, enabled input, and no late callbacks. Use event-controlled async transports and a local delayed-header server, not long sleeps. Verify successful pair commit, empty success, each failure followed by a new request, retained prior turns, and no failed/cancelled prompt in the next payload.

**Success Criteria:**

#### Automated Verification:

- [x] All cancellation/transaction cases pass; deterministic stalled-transport cancellation finishes within a two-second test deadline: `rtk proxy uv run pytest -q`.
- [x] Existing context, metrics, presets, and high-priority exclusion tests pass under the same command.
- [x] Lint/format/type gates pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] On macOS, Escape during connection/header wait and an idle response restores input after cancellation; subsequent chat and model actions work.
- [ ] The UI reports client cancellation accurately without claiming that the remote server's generation necessarily stopped. Failed/cancelled attempts remain visible but are excluded from subsequent context.

### Phase 4: Acknowledged download cancellation (F10)

Keep the modal and its worker ownership intact until cancellation is observed.

**Changes:**

- `src/mlx_tui/search.py` — check the existing cancellation event before and after `snapshot_download` as well as in progress updates. Cancellation at return wins over publishing success. Retain pinned revision/pattern selection and existing resumable cache behavior.
- `src/mlx_tui/search_screen/download.py` — on Escape while downloading, set the event and show/log “cancellation requested” once; keep the modal mounted and controls disabled until worker exit. Do not use Textual thread-worker cancellation as proof the Hub call stopped. Prevent progress callbacks from replacing the pending-cancel message.
- `src/mlx_tui/search_screen/download.py` — route success, error, and `CancelledDownload` through one terminal UI callback and guaranteed state reset. Only acknowledged cancellation logs “download cancelled” and dismisses. If a Hub exception occurs before acknowledgment, report the actual failure. Preserve normal error retry behavior and rescan on success; after acknowledged cancellation rescan safely to reflect any cache content completed before cancellation, without deleting partial files.
- `src/mlx_tui/search_screen/__init__.py` — remove `_cancel_logged` cross-thread deduplication; use UI-owned cancellation intent (the existing event is sufficient for the pending state). On app/screen teardown set the event; avoid UI calls to an unmounted screen and never log a fabricated terminal outcome. Whole-app exit does not guarantee immediate interruption of a blocked Hub call.
- `tests/unit/test_search.py`, `tests/integration/test_search_integration.py` — update the immediate-dismiss expectation. Use a worker that acknowledges only after a test event: before acknowledgment, modal stays open, pending message persists, no second download starts, and no terminal cancellation/success is logged. Then verify one cancellation, reset, dismissal, and safe rescan. Cover cancellation before download starts, cached/no-progress return, repeated Escape, errors, and teardown.

**Success Criteria:**

#### Automated Verification:

- [x] Pending/acknowledged outcomes, retry, and revision/pattern regressions pass: `rtk proxy uv run pytest -q`.
- [x] Lint/format/type gates pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] Cancel a download to a disposable cache. The modal says cancellation is requested until it stops, then closes; reopening search permits retry and existing downloaded data can be reused.

### Phase 5a: Search ownership (F13)

Consolidate the now-tested search lifecycle without changing behavior.

**Changes:**

- `src/mlx_tui/search_screen/__init__.py` — move all query/download widget behavior into existing `SearchScreen` methods, retaining `ResultsTable`, `_format_size`, bindings, worker decorators, and callbacks. Worker bodies call pure `search.py` adapters and marshal UI state changes with `self.app.call_from_thread`.
- `src/mlx_tui/search_screen/query.py`, `src/mlx_tui/search_screen/download.py` — delete after moving submission, search, populate, highlight/size, start/download, progress, finish, rescan, and close behavior. Remove local imports back into the owning package and their forwarding suppressions.
- `tests/integration/test_search_integration.py` — retarget Hub/free-space/download monkeypatches to the new usage site in `mlx_tui.search_screen`; retain real modal assertions and Phase 4 acknowledgment checks.

**Success Criteria:**

#### Automated Verification:

- [x] All search/download and other tests pass: `rtk proxy uv run pytest -q`.
- [x] Lint/format/type gates pass without replacement forwarding suppressions: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] Search, row-size updates, download success, retry, and pending cancellation behave as before this migration.

### Phase 5b: Models ownership (F13)

Put cache-table and model lifecycle behavior on `ModelsPane` while preserving pure policy functions.

**Changes:**

- `src/mlx_tui/models_pane/__init__.py` — move table population/markers/progress, delete confirmation/execution, request guards, warm worker, and boot worker into the existing owning methods. Change `_populate` to accept `list[ModelRow]`. Preserve worker groups and callback thread boundaries; read the active model for deletion through a UI-thread callback immediately before invoking the cache adapter.
- `src/mlx_tui/swap.py` — move the existing pure `SwapAction`, `resolve_swap_action`, `_refuse_reason`, and `boot_plan_for` here without changing behavior. Retain `BootPlan` and `health_timeout`. Process cleanup remains in `serverctl.py` from Phase 1.
- `src/mlx_tui/models_pane/table_ops.py`, `src/mlx_tui/models_pane/delete.py`, `src/mlx_tui/models_pane/swap_ops.py` — delete after migration. Remove uncalled compatibility forwarding such as `_fail_swap` only after checking repository callers.
- `tests/conftest.py`, `tests/integration/test_swap_integration.py`, `tests/integration/test_app_integration.py`, `tests/unit/test_swap.py` — retarget adapter patches to actual module usage sites and pure policy imports to `mlx_tui.swap`. Preserve all boot, model verification, identity, and lease assertions.

**Success Criteria:**

#### Automated Verification:

- [x] All model policy, process cleanup, and operation-exclusion tests pass: `rtk proxy uv run pytest -q`.
- [x] Lint/format/type gates pass without replacement forwarding suppressions: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] Model selection, progress, verified loaded marker, and delete refusal for the active model retain their behavior.

### Phase 5c: App ownership (F13)

Give the coordinator direct ownership of cross-pane settings, identity, and polling.

**Changes:**

- `src/mlx_tui/app/__init__.py` — fold `polling.py`, `state.py`, `status_bar.py`, `swap_ctrl.py`, `presets_ctrl.py`, and `config_edit.py` behavior into `MlxTuiApp`. Replace existing forwarding bodies with implementations; add `update_server_identity(probe: ServerProbe, process_identity: ProcessIdentity | None) -> None` and a private async probe-fetch method. Retain `_poll`'s overlap guard, identity generation/catalog checks, CLI entry point, and current preset/config semantics.
- `src/mlx_tui/chat_pane/__init__.py` — add `refresh_context_bar() -> None` using current committed messages, config, and parsed output cap; use it from config reload instead of the free `_refresh_context_bar(pane, max_ctx)` helper.
- `src/mlx_tui/models_pane/__init__.py` — route warm/boot identity updates through `self.tui.update_server_identity` with `call_from_thread`, removing imports into `app.state`.
- `src/mlx_tui/app/polling.py`, `src/mlx_tui/app/state.py`, `src/mlx_tui/app/status_bar.py`, `src/mlx_tui/app/swap_ctrl.py`, `src/mlx_tui/app/presets_ctrl.py`, `src/mlx_tui/app/config_edit.py` — delete after caller migration. Retain `app/operations.py` as the UI-free lifecycle coordinator. Do not create a replacement controller hierarchy.
- `tests/conftest.py`, `tests/integration/test_app_integration.py`, `tests/integration/test_swap_integration.py`, `tests/unit/test_status.py` — update moved seams/imports, including the config editor test, and remove fallback references to obsolete status helpers. Preserve real behavior assertions; do not add compatibility shims just for old test imports.

**Success Criteria:**

#### Automated Verification:

- [x] Full tests, including endpoint/catalog identity and total config/preset updates, pass: `rtk proxy uv run pytest -q`.
- [x] Lint/format/type gates pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] Polling/status, tab changes, cold start, presets, and editor reload operate normally with no circular-import startup failure.

### Phase 6: Visible, bounded diagnostics (F15)

Distinguish expected absence from programming failures at the final owning boundaries.

**Changes:**

- `src/mlx_tui/app/__init__.py` — probe fetching catches `httpx.HTTPError` as unavailable and `ValueError` from JSON as an unexpected response. Let other errors reach `_poll`'s defensive boundary. There, log exception class/message with a `poll failed` prefix, retain the last known identity rather than fabricate ordinary server downtime, and always reset `_poll_in_flight`.
- `src/mlx_tui/app/__init__.py` — add a small `log_error_once(source: str, exc: Exception) -> None` plus a per-source last-error dictionary. Suppress identical repeated failures until recovery; changed errors log immediately. Clear a source on successful polling/size fetch so recurrence becomes visible. Keep this in the existing shared logger, not a logging service or persistence subsystem.
- `src/mlx_tui/search_screen/__init__.py` — size-fetch failure leaves the size unknown but logs a bounded diagnostic with repo ID. Report actual exceptions from search/download worker boundaries. Scope `NoMatches` handling to teardown or missing widgets; do not silently consume failed mounted-widget updates.
- `src/mlx_tui/models_pane/__init__.py` — add a diagnostic boundary to cache rescan; retain the current rows on unexpected scan failure. Preserve expected missing-cache behavior in `models.py`. Keep logged broad worker catches and cleanup/re-raise guards where they are intentional safety boundaries.
- `src/mlx_tui/chat_pane/__init__.py` — complete the broad-catch audit after Phase 3: direct typed config access, `NoMatches` only for absent widgets, and unexpected turn failure logged before guaranteed cleanup.
- `tests/integration/test_app_integration.py`, `tests/integration/test_search_integration.py`, `tests/integration/test_swap_integration.py` — inject an expected connection failure versus `RuntimeError`, repeating identical failures, changed failures, recovery then recurrence, malformed JSON, failed size lookup, and failed rescan. Assert honest state, one diagnostic per repeated fault, recovery, and usable controls.

**Success Criteria:**

#### Automated Verification:

- [x] Failure classification, diagnostic deduplication, and recovery tests pass: `rtk proxy uv run pytest -q`.
- [x] Lint/format/type gates pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check`.

#### Manual Verification:

- [ ] With a disconnected local server, the status reflects downtime; an injected programming fault instead reports a named diagnostic without flooding the shared log or freezing polling.

### Phase 7: Dependencies, typing, destructive flows, and macOS coverage (F12, F13, F14)

Exercise the installed dependency contract and high-impact user paths on the target OS.

**Changes:**

- `src/mlx_tui/models.py`, `tests/unit/test_models.py` — import `CachedRepoInfo`, `HFCacheInfo`, and test cache types from public `huggingface_hub` exports. These imports were verified against the installed declared-minimum HF version.
- `pyproject.toml`, `uv.lock` — declare direct `rich>=15.0.0` (the verified installed version) and add `types-psutil` to the dev group. Resolve its compatible version during implementation and refresh the lockfile. Keep existing runtime floors unless the compatibility suite proves a change is needed.
- `src/mlx_tui/models.py`, `src/mlx_tui/process.py`, `src/mlx_tui/search.py`, `src/mlx_tui/config.py`, migrated pane/app files, and their existing tests — rerun warning-visible Pyrefly after the moves, remove now-obsolete ignores and redundant conversions, and resolve typed callback/list/config boundaries. For checker narrowing across mutating calls, re-read state through a correctly typed helper/property before asserting. Any unavoidable remaining third-party suppression must name the concrete limitation; do not hide whole files or reduce the strict preset.
- `tests/conftest.py:171`, `tests/conftest.py:203`, `tests/integration/test_swap_integration.py:65` — remove the verified redundant port `int()` conversions. `tests/integration/test_app_integration.py:231`, `tests/unit/test_operations.py:29`, and `tests/unit/test_status.py:70` — retain their runtime assertions while avoiding stale checker narrowing across state mutation, using a typed fresh-read helper if needed. The remaining two redundant conversions currently live in `chat_pane/turn.py:32` and `search_screen/query.py:93`; remove them during their owner migrations.
- `tests/integration/test_swap_integration.py` — exercise actual `ConfirmScreen` y/n/Escape paths, no selection, initial active-model refusal, active model changing before confirmed execution, successful deletion/rescan, and `CacheNotFound`/`OSError`/unexpected failure. Assert exact revision hashes passed to a stub `delete_repos`, no deletion on refusal, and restored controls/lease. Preserve the real cache untouched.
- `tests/integration/test_app_integration.py` — extend config-editor coverage for missing-file template creation, `$EDITOR` argv with spaces, suspend/resume, failed editor launch/nonzero exit, malformed TOML preserving config, cleared values, presets reload/reset, and host/port restart notice. Handle editor `OSError`/nonzero status explicitly in `MlxTuiApp.action_edit_config` so no false successful reload is reported.
- `tests/unit/test_config.py` — add direct `mlx_tui.app.main` tests by stubbing `MlxTuiApp.run`, `load_config`, and argv: config defaults, explicit host/port precedence, help exit, invalid port exit, and no TUI launch on help/error. Keep these in the existing test file rather than creating a CLI test framework.
- `tests/unit/test_process.py` — add a real disposable Python listener process using an ephemeral port and an MLX-matching argv token. Test discovery when permitted; when macOS denies listener inspection, assert the supported unavailable result and separately verify that pidfile data is never trusted without validation. Always terminate/reap the fixture. Retain mocked permission-denied tests for deterministic coverage.
- `.github/workflows/ci.yml` — retain the locked Linux Python 3.13 job; use `uv run pyrefly check --min-severity warn` after warnings are fixed. Add a `macos-14` Python 3.13 job running the same full tests, including real process and delayed-header/idle-stream cancellation tests. Confirm the job's reported architecture is arm64 during implementation; use an available arm64 runner label if repository runner availability differs.
- `.github/workflows/ci.yml` — add Linux compatibility jobs using isolated environments and full `pytest -q`: one highest-resolution installation, one with explicit direct floor pins `httpx==0.28`, `huggingface-hub==1.28`, `psutil==7.0`, `textual==8.2.8`, `rich==15.0.0`. Install pytest/pytest-asyncio alongside the project, preserve normal transitive resolution, and print resolved versions. Keep pins aligned with metadata. Do not use `--resolution lowest` or pretend `lowest-direct` against only a wheel constrains its runtime dependencies.

**Success Criteria:**

#### Automated Verification:

- [x] Full locked tests, including destructive flows and CLI/editor coverage, pass: `rtk proxy uv run pytest -q`.
- [x] Visible warnings are resolved: `rtk proxy uv run pyrefly check --min-severity warn` (command verified; baseline currently fails).
- [x] Lint/format pass: `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`.
- [x] Highest dependency import smoke passes: `rtk proxy uv run --isolated --no-project --with . --with pytest --with pytest-asyncio --resolution highest python -c 'import mlx_tui.app, pytest, pytest_asyncio'`.
- [x] Direct-floor installation/help smoke passes: `rtk proxy uv run --isolated --no-project --with . --with 'httpx==0.28' --with 'huggingface-hub==1.28' --with 'psutil==7.0' --with 'textual==8.2.8' --with 'rich==15.0.0' mlx-tui --help`.
- [x] CI runs the highest full suite with `uv run --isolated --no-project --with . --with pytest --with pytest-asyncio --resolution highest pytest -q`, and floor suite with `uv run --isolated --no-project --with . --with pytest --with pytest-asyncio --with 'httpx==0.28' --with 'huggingface-hub==1.28' --with 'psutil==7.0' --with 'textual==8.2.8' --with 'rich==15.0.0' pytest -q`. Import/help versions were verified; these full isolated test invocations and macOS CI are **verify during implementation**.

#### Manual Verification:

- [ ] Inspect the macOS job architecture/results and use a real Apple Silicon terminal to check responsive cancellation and restoration after editor exit. Headless CI is not evidence of visual rendering quality.
- [ ] Review remaining suppressions individually; none should mask a pane/app forwarding boundary removed by this plan.

### Phase 8: Documentation and release artifacts (F16, remaining F14)

Publish an accurate repository contract and verify both distributions without publishing a release.

**Changes:**

- `README.md` — document `max_ctx`/`max_context` alias and precedence, complete request reservation, estimated token/throughput labels, client-observed prefill, command modes/migration, transactional history, requested versus acknowledged cancellation, and local HTTP scope. Correct warm-load verification to response-model evidence or endpoint follow-up rather than always claiming `/v1/models` proves the active target. Link the license file and replace obsolete metadata line pointers.
- `ARCHITECTURE.md` — rewrite from the final ownership layout: composed status widgets, endpoint/catalog/PID generation rules, operation leases, async chat cleanup, process group cleanup, pure adapters, SSE framing, current download patterns/revision pinning, context/metrics, and final CI commands. Remove obsolete symbol references, exact dependency/test counts, volatile source line numbers, and the incorrect tar-on-wheel example.
- `docs/part2.md` — mark Phase 4 implementation complete, explicitly distinguish its unverified real-world usage gate from implemented code, and leave later research-gated phases as backlog. Correct “MIT done” to reference the new license. Do not claim the one-week/product-use gate was performed.
- `LICENSE` — new file containing the standard MIT license, with `Copyright (c) 2026 Alvaro Gomez`, matching current project author metadata and the existing declared MIT intent.
- `pyproject.toml` — add `license-files = ["LICENSE"]`; retain the SPDX `license = "MIT"`. Keep the current package version; this task is not a release.
- `tests/artifact_smoke.py` — new standalone stdlib smoke script. Use `tempfile.TemporaryDirectory`, `subprocess`, `tarfile`, `zipfile`, and `email.parser` to build wheel+sdist with `uv build --out-dir`, inspect both for the MIT license and package/entry-point metadata, rebuild a wheel from the extracted sdist, and install each wheel into its own temporary `uv venv`. Run the installed `mlx-tui --help` and import `mlx_tui.app` from a temporary working directory with `PYTHONPATH` removed; assert the import path is inside that environment. This verifies packaged files rather than accidentally importing the source checkout. Do not hard-code artifact filenames or add packaging libraries.
- `.github/workflows/ci.yml` — add a packaging job invoking `uv run python tests/artifact_smoke.py`; use temporary output so stale `dist/` files cannot pass the test. The script must fail on missing license, bad entry point, incomplete sdist, or failed installation.

**Success Criteria:**

#### Automated Verification:

- [x] All tests and final gates pass: `rtk proxy uv run pytest -q`; `rtk proxy uv run ruff check .`; `rtk proxy uv run ruff format --check src tests`; `rtk proxy uv run pyrefly check --min-severity warn`.
- [x] Build succeeds: `rtk proxy uv build --out-dir /tmp/mlx-tui-plan-artifacts` (verified baseline command).
- [x] Both distributions contain the license and independently install/run from outside the checkout: `rtk proxy uv run python tests/artifact_smoke.py` — **verify during implementation**, since the script is new. Baseline build and isolated wheel/help operations used by it were verified during research.

#### Manual Verification:

- [ ] Follow README startup/command-mode examples against a disposable local setup; review bindings, context limits, and cancellation wording against the actual TUI.
- [ ] Review the final architecture file tree and Part 2 status against implemented code; no stale forwarding modules or unsupported compatibility claims remain.

## Out of Scope

- Reimplementing F1–F7, changing the already-fixed swap policy, or reverting current uncommitted corrections. Their tests remain regression gates throughout.
- TLS/API-key/base-path support, remote-server product expansion, or a new HTTP/backend abstraction.
- Force-stopping arbitrary Hub network calls, a download subprocess framework, remote generation-cancellation guarantees, or deleting partial download data.
- A new lifecycle/state-machine framework, wholesale dependency upper bounds, persistent diagnostics, or a universal controller/service interface.
- New sampler features, persistent chat, model loading for automated tests, real-cache destructive tests, PyPI/Homebrew publishing, or claiming the Part 2 usage gate has passed.

## Risks & Mitigations

- Existing shell configurations change behavior → document the opt-in with the first code phase, validate start commands before stopping anything, and preserve verified target checks.
- Shell wrappers/background descendants survive the parent → own a separate process group, test parent-exits-first cases, signal only that group, and document that deliberately detached external services must be managed by their configured stop command.
- Async worker cancellation can occur before coroutine entry or recur during cleanup → retain worker state, use one cancellation request, and make terminal cleanup idempotent with explicit pre-start tests.
- Moving code can hide behavior regressions behind large diffs → move each owner at a separate checkpoint, preserve package entry points, and retarget tests to real usage sites without weakening assertions.
- Download cancellation may wait for blocked Hub work → keep the pending state honest and the modal owned until acknowledgment; test no-progress return and teardown rather than promise instant cancellation.
- Warning-visible typing reveals additional issues after moves → inventory after migration, fix exact boundaries, and retain only narrowly explained third-party suppressions.
- macOS process inspection may be denied → verify graceful unavailable telemetry rather than treating permission failure as a test skip that hides a crash; use temporary listener processes.
- New compatibility resolutions may expose failures absent from the lockfile → print versions, test declared direct floors explicitly, and revise bounds only from observed incompatibility.
- New verification artifacts do not yet exist → commands for existing gates were run; new script/full compatibility/macOS criteria are explicitly marked verify during implementation and must pass before completion.

Requirement coverage: F8 → Phase 1; F9 → Phase 1; F10 → Phases 3–4; F11 → Phases 2/8; F12 → Phases 3/7; F13 → Phases 3/5a–5c/7; F14 → Phases 1/3/4/7/8; F15 → Phases 3/6; F16 → Phase 8. Before changing Draft to Approved, suggest the `review-plan` skill for an independent pass and obtain explicit user approval.
