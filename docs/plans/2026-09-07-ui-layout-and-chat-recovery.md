# UI layout, persistent response rendering and chat recovery

**Date:** 2026-09-07
**Work Item:** n/a
**Status:** In Progress

**Approval:** User requested implementation on 2026-09-08 (Draft → Approved → In Progress).

## Overview
Repair the existing terminal UI before Part 3 Milestone A: reclaim space, expose contextual actions, keep each streaming answer in its final transcript position, and restore retryable drafts after failure or cancellation. Preserve Models / Chat / Metrics, the current request/operation contracts and local configuration workflow.

This plan follows `docs/reviews/2026-09-07-ui-ux-review.md`. The user accepted the design direction and explicitly selected persistent response rendering over a smaller, separate live preview. The four checkpoints below separate that larger rendering change from draft recovery. This is a draft implementation specification, not authorization to mark its checks complete.

## Current State

- `src/mlx_tui/params.py:52–100`: `ParamsPane(Collapsible)` overrides `compose()` and directly yields parameter rows. It omits the native title and Contents container; `collapsed=True` therefore does not hide the inputs. The installed Textual Collapsible accepts child widgets through its constructor and provides the correct composition itself.
- `src/mlx_tui/app/__init__.py:63–110`: the shell contains a StatusBar, TabbedContent and docked six-row RichLog. No Footer is composed. Existing bindings cover quit, cancellation, start, config and preset cycling.
- `src/mlx_tui/status_bar.py:17–37`: the status layout has fixed memory/port sections and a flexible model field. This plan may adjust geometry but not status meanings, units, model evidence or memory calculations.
- `src/mlx_tui/chat_pane.py:84–92`: Params, a separate live Static, RichLog transcript, Input and two context rows are siblings. The live answer appears above its user prompt, then is appended to RichLog on completion.
- The review's synthetic headless render at 80×24 left `chat-log` one row high. The composer spanned zero-based rows 17–19 while the log started at row 18. At 120×40 the transcript had 14 rows. These are layout observations, not real-server measurements.
- `src/mlx_tui/chat_pane.py:104–176`: submission acquires the CHATTING lease, clears/disables the input, displays the attempted prompt, then launches the worker. A worker-launch exception restores enabled state without restoring the draft.
- `src/mlx_tui/chat_pane.py:240–380`: the worker prepares context and streams before committing successful history. Failure and cancellation preserve earlier messages. Current successful empty replies commit only the user; successful length-capped replies commit user and partial answer. Preserve these contracts here; A deliberately changes them later.
- `src/mlx_tui/chat_pane.py:403–490`: `_commit_success()` owns history insertion; `_complete_turn_ui()` renders; `_write_system_line()` adds notices; `end_turn()` clears the live surface and normally focuses the input. Keep that separation of responsibility.
- `src/mlx_tui/chat.py:53–122`: `stream_turn()` sends cumulative text to a synchronous `on_flush` at approximately 100 ms intervals. It returns the authoritative final text separately, which may include text never delivered to the preview callback. No transport change is necessary for this plan.
- `src/mlx_tui/app/__init__.py:250–267,435–455`: presets currently announce themselves in the log. `log_app(message, style)` is the shared logging seam; `log_error_once()` deduplicates failures and `clear_error()` permits a later recurrence.
- `src/mlx_tui/table.py:25–29` and `src/mlx_tui/search_screen.py:32–49`: contextual load/delete/search/download/close bindings already exist. `src/mlx_tui/operations.py` provides the shared lease; it should remain UI-free.
- `tests/conftest.py:185–216`: AppHarness reads RichLog strips through `log_lines()` and `app_log_lines()`. Several tests query `#chat-stream` directly. The transcript rewrite must migrate these assertions in the same checkpoint; do not leave a hidden compatibility RichLog.
- `tests/integration/test_chat_cancellation.py` already exercises pre-start, pre-header, idle-stream, partial-response and repeated cancellation. Existing chat/context tests check failed-prompt exclusion, empty/length outcomes, and no POST for context rejection. Reuse these cases.

**Verified baseline:** on 2026-09-07, `rtk uv run pytest -q` passed all 450 tests in 95.65 seconds. `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, and `rtk uv run pyrefly check --min-severity warn` passed. The latter is the command used by `.github/workflows/ci.yml`. All automated gate commands below have been run against the current tree; future test cases and proposed symbols remain to be verified during implementation.

**Working-tree constraint:** the repository contains existing staged changes and an untracked draft A plan. Implement against that tree; do not reset, bulk-stage, commit or rewrite unrelated work. Read the current files again before editing because the cited line numbers are research references, not fixed patch coordinates.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Keep the three-tab shell | New dashboard/sidebar; existing tabs | The accepted design and Part 3 both fit the existing navigation. |
| Native collapsed controls | Custom hide/show logic; constructor children for Collapsible | Reuse the installed widget's title, focus and collapse behavior rather than accessing its private fields. |
| One bounded Activity disclosure plus native Footer | Permanent log; automatic popup; new command palette | Recover space while retaining log access and existing keyboard bindings. No automatic focus changes on incoming events. |
| Persistent per-turn widgets in one scroll viewport | Separate bounded live preview; rebuilding the full transcript | The user explicitly chose responses that stay in place. Only the active body needs updating. |
| Existing Rich Markdown in Static | Textual Markdown with asynchronous update/stream queues; plain-text-only streaming | Static accepts the existing renderable and synchronous cumulative updates. Avoid a second stream lifetime and async render races. |
| Keep partial output with a visible unsuccessful outcome | Discard partial text; commit it to request history | The visible transcript documents the attempt; request history remains transactional. |
| Restore the original submitted draft on unsuccessful cleanup | Clear everything; automatic retry | Retrying remains an explicit user action, and earlier context is preserved. |
| Four checkpoints | Three with rendering/recovery combined | Isolate geometry, shell interaction, transcript migration and recovery into independently passing changes. |
| No new packages or configuration switches | Theme dependency, settings schema, transcript framework | Native Textual containers, Static, Rich Markdown, Footer and existing configuration suffice. |

## Implementation Phases

### Phase 1: Compact layout and genuinely collapsible parameters
Restore the native disclosure and establish explicit content bounds before adding shell controls or changing transcript widgets.

**Changes:**
- `src/mlx_tui/params.py` — construct the same three Horizontal rows as children passed to `Collapsible.__init__`; remove the broken `compose()` override. Preserve `ParamsPane`, input IDs, range normalization, `read_values()`, `apply_values()` and `apply_config()` so callers retain their contracts. Do not use `_title` or `_contents_list` from Textual.
- `src/mlx_tui/params.py` — remove decorative padding/borders that make the closed disclosure taller than one row. Bound the expanded Contents area to five rows with vertical scrolling, so all fields remain reachable without pushing the composer off-screen. Update the title to `Params · temp … · top-p … · max …` from normalized next-request values on mount, config/preset application and input changes. Display the summary without rewriting partially typed input; keep normalization-on-submit behavior. Preset identity/Modified tracking is deferred rather than inferred from partial presets.
- `src/mlx_tui/app/__init__.py` — give the main TabbedContent an explicit remaining-height allocation and remove unnecessary top padding. Ensure the tab pane/container chain uses the allocated height and does not extend behind bottom-docked content. Retain the current log temporarily, bounded to three rows at screen heights below 32 and six rows otherwise.
- `src/mlx_tui/status_bar.py` — compact padding and fixed geometry only as needed for the height budget. Keep status values and APIs unchanged. Long IDs must not displace the port or create horizontal overflow; ellipsize the model field and keep its full value available in a tooltip. A owns the later evidence-aware header redesign.
- `src/mlx_tui/chat_pane.py` — explicitly reserve composer/context heights and give the reading area the remaining height. Until Phase 3 removes it, cap the existing live Static's displayed height so a long cumulative response cannot consume the composer. This is an interim layout guard, not the final transcript architecture.
- `src/mlx_tui/app/__init__.py` — introduce `on_resize` handling and a screen class for height below 32, used only for compact geometry. Apply the same initial sizing on mount. Proposed handler/class details must be verified during implementation against native resize behavior.
- `tests/unit/test_params.py` — activate Chat and expand Params before the existing keyboard submission test focuses a parameter. Add a mounted collapse/expand check proving the title is accessible, hidden inputs cannot receive focus, and reopening retains values.
- `tests/integration/test_app_integration.py` — add parameterized layout checks using the existing stub server at 80×24 and 120×40. Assert the composer, context rows and log are within the screen and do not overlap. Check both parameter states and resizing in both directions; disable or stub real cache/process discovery for these geometry cases.
- `tests/integration/test_context_integration.py` — retain payload normalization coverage; verify collapsed title values track config application and changed fields without issuing a chat POST.

**Success Criteria:**

#### Automated Verification:
- [x] Native collapse, value retention and layout bounds are covered and all regression tests pass: `rtk uv run pytest -q`.
- [x] Lint passes: `rtk uv run ruff check .`.
- [x] Formatting passes: `rtk uv run ruff format --check src tests`.
- [x] CI type check passes: `rtk uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] At 80×24 and 120×40, Params starts as one line, opens with keyboard/click, and allows reaching each field by Tab without hiding the composer.
- [ ] Resize with Params open and closed; input/context/log remain reachable and no tab content paints behind the log.
- [ ] Long repository IDs and light/dark themes preserve legible text and focus indication.

**Execution note:** Native widget CSS is scoped; root selectors use the widget class. Verified 454 tests plus added field-reachability/long-ID checks, lint, format and types on 2026-09-08.

### Phase 2: Activity disclosure and discoverable keyboard actions
Replace permanent log space with an explicit disclosure and expose the relevant actions through the native binding system.

**Changes:**
- `src/mlx_tui/app/__init__.py` — wrap the existing `RichLog(id="app-log", markup=False, wrap=True)` in a native `Collapsible(id="activity", title="Activity", collapsed=True)`. Keep RichLog and `log_app()` for current callers/tests. Place Activity above a one-row `Footer(compact=True, show_command_palette=False)`. When collapsed, Activity takes one row; when open, its log takes three rows below height 32 and six otherwise. Keep log width allocated while hidden so logged content is available on expansion; verify native deferred RichLog rendering rather than assuming it.
- `src/mlx_tui/app/__init__.py` — add the proposed `action_toggle_activity()` bound to F2. It changes expansion only; it must not move focus away from a composer/table. The disclosure remains keyboard-focusable for explicit interaction. If collapsing while focus is inside the log, move focus to the Activity title instead of leaving focus hidden.
- `src/mlx_tui/app/__init__.py` — add a small `refresh_activity()` presentation method. Prefix the title with the existing operation kind while busy; otherwise show `Activity`. Include a one-line, width-bounded latest log event, rendered literally, with full text retained in RichLog. Preserve the latest red/yellow notice in the summary until the user opens Activity; label it `Last error`/`Last warning`, not an asserted current runtime condition. Ordinary subsequent log lines must not silently replace unseen attention. Do not change `log_error_once()` deduplication or create an event-history subsystem.
- `src/mlx_tui/app/__init__.py` and `src/mlx_tui/chat_pane.py` — call the presentation refresh after logging and existing operation UI transitions, chat start, cancellation request and cleanup. Preserve the lease lifetime; do not add notifications or callbacks to `OperationCoordinator`. Worker-originated updates continue through the existing UI-thread handoffs. `refresh_activity()` and binding refreshes must tolerate unmounted widgets so presentation cannot strand cleanup.
- `src/mlx_tui/chat_pane.py` — introduce a local `ChatInput(Input)` subclass overriding only the Enter binding metadata to expose `Send` while reusing native `action_submit`. Preserve `#chat-input`, the Input subtype contract and `Input.Submitted` handling. Do not implement another send path.
- `src/mlx_tui/app/__init__.py` — use native Binding metadata and `check_action(action: str, parameters: tuple[object, ...]) -> bool | None` for contextual visibility. Show Esc Cancel only for an active cancellable chat on the base screen; preserve modal Escape ownership. Keep F2 Activity, Ctrl+G Config and Ctrl+Q Quit discoverable. Preserve existing start/preset shortcuts and their action guards. Keep secondary shortcuts from crowding out model actions at 80 columns; expose their existing keys in expanded Params/README rather than adding a custom palette. Refresh native bindings when availability changes.
- `src/mlx_tui/table.py` — keep Enter, D and slash table-local, with concise visible labels `Load`, `Delete`, `Search`. Do not let them intercept those characters in Chat/Input. Existing action-level guards still explain refused operations; don't turn guarded actions into silent no-ops merely to dim a footer item.
- `tests/integration/test_app_integration.py` — verify collapsed logging retains full events, unseen warnings survive ordinary messages, expansion acknowledges the historical notice, F2 preserves focus, and Activity remains visible across tab changes. Extend geometry checks to include Footer and both Activity states. At 80×24 with Params and Activity collapsed, require at least six reading rows; with both expanded, require at least one and complete composer/context visibility.
- `tests/integration/test_config_integration.py`, `tests/unit/test_params.py` — verify native Send dispatches once, parameter Enter still normalizes without sending, and existing config/preset bindings still work. If hidden RichLog defers rendering, adjust `AppHarness.app_log_lines()` in `tests/conftest.py` to expose actual rendered log content through an explicit test-only expansion/pause; preserve synchronous callers by instead giving the log a stable hidden layout if needed. Resolve this within the phase without mocking log output or weakening log assertions.
- `tests/integration/test_search_integration.py` — add/extend the modal Escape regression: search consumes Escape and a download remains pending until acknowledgement. Ensure a main-shell binding does not close the wrong surface or invoke load/delete while typing.

**Success Criteria:**

#### Automated Verification:
- [x] Activity visibility/retention, keyboard dispatch and updated geometry tests pass with the full suite: `rtk uv run pytest -q`.
- [x] Lint passes: `rtk uv run ruff check .`.
- [x] Formatting passes: `rtk uv run ruff format --check src tests`.
- [x] CI type check passes: `rtk uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Discover load/search/delete from Models, send from Chat, and cancellation during generation without consulting the README; the essential hints fit at 80 columns.
- [ ] Expand/collapse Activity from the composer and while reading logs; incoming events do not switch tabs or steal focus.
- [ ] A failed operation leaves a visible historical notice while logs are collapsed. Inspecting it reveals the full message, including literal brackets and long text.
- [ ] Use search and deletion confirmation; their keys and focus return still work. Ordinary slash, D and punctuation remain typeable in Chat.

**Execution note:** The collapsed Activity clips an allocated Contents region rather than hiding its layout; RichLog renders real events while its focus is disabled. No harness event buffer is needed. Verified 457 tests and all static gates on 2026-09-08.

### Phase 3: Persistent chronological transcript
Replace RichLog plus detached stream with one scrollable sequence of persistent turn views. This is the user-selected rewrite; do not implement the rejected separate-preview alternative.

**Changes:**
- `src/mlx_tui/chat_turn.py` — **new file; proposed symbols, verify during implementation**. Add `ChatTurn(VerticalGroup)` as a presentation-only widget. Its constructor accepts the submitted prompt. It creates and retains references to literal user text, assistant role, response body, measurement stamp and notice Static widgets before mounting. Use stable classes `.chat-turn`, `.chat-text`, `.chat-response`, `.chat-stamp`, `.chat-notices` for styling and behavioral tests; no global response ID shared across turns.
- `src/mlx_tui/chat_turn.py` — add `update_response(text: str) -> None`, `finish_response(text: str, stamp: str, notices: list[str]) -> None`, and `add_notice(message: str, style: str) -> None`. Render cumulative response text using the existing `rich.markdown.Markdown` inside the same body Static during streaming and at completion. Show `Waiting for output…` before first content; remove it when text arrives or an outcome ends the attempt. Put measurement text after the answer; preserve its current calculation and wording for A to replace. Join notices as Rich Text, not interpolated markup.
- `src/mlx_tui/chat_pane.py` — replace `RichLog#chat-log` and `Static#chat-stream` with `VerticalScroll(id="chat-transcript")`. Only that viewport scrolls; keep Params, composer and context outside it. Hold `_active_turn: ChatTurn | None` for the current view, with no second message/history store. Remove obsolete stream/log CSS and `_SEPARATOR` if no longer used.
- `src/mlx_tui/chat_pane.py` — after successfully acquiring the existing CHATTING lease, create one ChatTurn, mount it once and retain its reference before starting the worker. Route `_update_stream()` to that instance. Direct child references must allow updates before the mounting pass finishes; do not query children that may not yet be mounted or introduce a render worker. Wrap preparation/mount/start failures in cleanup so they cannot leave CHATTING acquired.
- `src/mlx_tui/chat_pane.py` — keep `_commit_success()` as the sole history insertion seam. `_complete_turn_ui()` updates the same active view with the returned final text, stamp and notices; never append a second answer on success. `_write_system_line()` routes notices to the current attempt. Keep `end_turn()` callable and idempotent with no active view, as existing swap tests use it. End the pending presentation and clear the active reference without clearing the completed or partial response body.
- `src/mlx_tui/chat_pane.py` — on failure/cancellation, retain already-displayed partial text in that view, with the existing outcome text plus `Not included in next request`. Do not fabricate a successful measurement stamp or add failed content to `messages`. If cancellation occurs before any text, replace the waiting placeholder with the outcome. Successful empty and length-capped responses retain today's history policy in this phase, including their notices.
- `src/mlx_tui/chat_pane.py` — before changing transcript content, capture whether the viewport is at its vertical end. Follow appended/growing content only for that case; perform the scroll after layout, without animation. Explicitly sending a new prompt may reveal the new turn. Scrolling up, tab changes, resize and incoming notices must not force a return to bottom. Coalesce/defer follow callbacks and recheck user scroll intent so an already-scheduled follow cannot override a later upward scroll. Use native scroll state/events; verify event and layout ordering during implementation rather than relying on token timing.
- `tests/conftest.py` — migrate `AppHarness.log_lines()` to inspect rendered `.chat-text` Static children in transcript order. Use each widget's actual rendered strips after layout, or render its actual Rich content at the allocated width; do not return `ChatPane.messages` or a test-only transcript string. Keep the helper name to minimize unrelated changes. `app_log_lines()` continues targeting the application RichLog.
- `tests/integration/test_chat_integration.py` — migrate RichLog and `#chat-stream` assertions to turn bodies/outcomes. Verify the same response-widget identity across multiple cumulative updates and completion, one user/assistant view per attempt, final text never duplicated, and final-result text wins even if no preview callback ran. Replace the standalone `_complete_turn_ui()` rendering test with a mounted ChatTurn test, while retaining the assertion that rendering alone does not commit request history.
- `tests/integration/test_chat_integration.py` — verify Markdown headings, emphasis, fenced code and literal user markup; long multiline output scrolls inside the transcript without growing the composer region. Test following at bottom, preserving scrollback during updates/completion, and rapid tab/size changes. Assert rendered geometry and widget identity, not only source content.
- `tests/integration/test_chat_cancellation.py`, `tests/integration/test_app_integration.py` — replace detached-stream clearing assertions with stopped waiting state, visible unsuccessful partial output where present, and no active turn/lease after cleanup. Preserve existing transport-close, no-request-before-start and repeated-cancel assertions.
- `tests/integration/test_metrics_integration.py` — retain existing metric semantics and text assertions through the migrated harness; update assumptions about stamp position only. Do not fix prefill/TTFT definitions in this checkpoint.

**Success Criteria:**

#### Automated Verification:
- [x] All migrated rendering/history/cancellation tests and new identity/scroll/layout cases pass: `rtk uv run pytest -q`.
- [x] Lint passes: `rtk uv run ruff check .`.
- [x] Formatting passes: `rtk uv run ruff format --check src tests`.
- [x] CI type check passes: `rtk uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] During a long answer, text grows below its prompt and stays in the same response view at completion; the composer stays anchored at both terminal sizes.
- [ ] Scroll to an earlier turn while output arrives, then switch tabs and return. The app preserves reading position; reaching the bottom resumes following output.
- [ ] Cancel before output and after partial output. Each attempt ends once, retains any displayed partial answer with an unsuccessful label, and never appears as a completed answer with a speed stamp.
- [ ] Markdown and code remain legible in light/dark themes and at narrow widths. A long response does not cause obvious input/cancellation lag. If full cumulative Markdown rendering regresses responsiveness at the existing flush cadence, address only the active widget's rendering cost within this phase; no transcript framework or new transport is implied.

**Execution note:** Added a presentation-only `end_attempt()` seam. Follow callbacks are coalesced and invalidated by upward scroll/target changes, resizing and tab activation. Verified 461 tests and all static gates on 2026-09-08.

### Phase 4: Draft recovery, focus and roadmap handoff
Restore editable input only when an attempt did not commit, and verify the rewritten transcript leaves request history and operation cleanup intact.

**Changes:**
- `src/mlx_tui/chat_pane.py` — add `_pending_draft: str | None`, captured from the original `Input.Submitted.value` after acquiring the lease and before clearing input. Continue sending the current stripped content. Clear the pending draft when `_commit_success()` has committed the current outcome, including today's empty/length outcomes. Use the pending value as the single recovery marker; do not add a parallel outcome enum that competes with A.
- `src/mlx_tui/chat_pane.py` — in `end_turn()`, restore a remaining pending draft into an empty composer, then clear the recovery marker once handled. Do not overwrite any newer nonempty input. Cover context rejection, HTTP/transport/unexpected errors, cancellation before/during the worker, and synchronous setup/start exceptions through the same cleanup path. Keep the attempted prompt and outcome in the transcript. Never automatically resubmit.
- `src/mlx_tui/chat_pane.py` — make cleanup robust with unmounted widgets and repeated calls; release the lease before refreshing controls. If an unrelated model operation now owns the lease, keep chat disabled until the existing model-operation completion restores it. Draft restoration must not independently enable an action that is still blocked.
- `src/mlx_tui/chat_pane.py` and `src/mlx_tui/app/__init__.py` — focus the restored composer only when Chat is still the active base pane and focus has not deliberately moved to another visible control or modal. Preserve an explicit `focus composer on return` intent only if needed to restore focus lost by disabling Input; do not focus Chat when the user navigated to Models, Metrics or Search. Refresh the Activity/footer state after settled cleanup.
- `tests/integration/test_chat_integration.py` — extend error500, truncated-stream, empty, length-cap and failed-prompt-exclusion cases with composer assertions. Add a start-failure case and exact-draft restoration (including surrounding whitespace). Verify success leaves an empty composer, failure restores without changing committed messages, a new draft is not overwritten, and an explicit retry contributes exactly one copy of the retried prompt to the request payload.
- `tests/integration/test_chat_cancellation.py` — extend the existing pre-start/pre-header/idle/partial/repeated cases to prove the draft returns exactly once after cleanup, no automatic HTTP retry occurs, and the lease/transport contracts remain unchanged. Verify navigating away during cancellation does not steal focus back.
- `tests/integration/test_context_integration.py` — extend over-limit rejection to assert the draft is restored and no POST occurs. Keep configured-budget and trimming calculations untouched.
- `tests/integration/test_swap_integration.py` — preserve the direct `end_turn()` regression with no pending draft/view, and add coverage that model-operation UI does not accidentally re-enable/focus Chat while cleanup is pending.
- `README.md` — update the log/Params/keybinding/chat descriptions to match the final shell and persistent transcript. Explain partial unsuccessful attempts versus committed request history, explicit retry and in-memory-only drafts. Do not copy the incorrect blanket statement that all “truncated” responses are excluded: distinguish failed transport from a successful token-cap response under current behavior.
- `ARCHITECTURE.md` — document ChatTurn's presentation-only role, the single transcript viewport, the existing worker/history owner, draft lifecycle and Activity presentation. Remove descriptions of the detached live stream/RichLog transcript that no longer apply.
- `docs/plans/2026-09-07-part3-milestone-a-trust-the-facts.md` — add a short dependency/handoff note to its Phase 3: reuse this persistent transcript and recovery seam; adapt them to A's new outcome/commit matrix rather than rebuilding them. Its metrics, reasoning/tool handling, context labels and real-runtime requirements remain in A. Preserve that plan's status and unchecked criteria.

**Success Criteria:**

#### Automated Verification:
- [x] Full suite covers recovery and focus across the existing failure/cancellation cases and passes: `rtk uv run pytest -q`.
- [x] Lint passes: `rtk uv run ruff check .`.
- [x] Formatting passes: `rtk uv run ruff format --check src tests`.
- [x] CI type check passes: `rtk uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Trigger failure, edit the restored draft and resend; the retry uses prior successful context with no automatic retry or duplicate failed prompt in the payload.
- [ ] Cancel, change tabs while cleanup finishes, and return. Input is recoverable and neither cancellation nor a late completion steals focus.
- [ ] Recheck 80×24 and 120×40 with Params/Activity open and closed, long output, long IDs and modal search. Composer/context/footer remain fully visible and controls are reachable by keyboard.
- [ ] The README matches the delivered bindings and behavior. No Compare, managed-runtime setup, new metrics or conversation persistence has appeared in this UI pass.

**Execution note:** Draft recovery via `_pending_draft` with empty-composer restore, focus gating, and README/ARCHITECTURE/handoff docs present. Verified 479 tests plus lint, format and types on 2026-09-08.

## Out of Scope
- Milestone A's endpoint/readiness/residency vocabulary, units, memory-fit policy, timing/accounting corrections, reasoning/tool stream semantics, outcome matrix and context-label changes.
- Milestone B comparison, saved reproducible profiles and benchmark results; C managed setup; D multiline editor, copy/retry controls beyond restored Input, sessions/files; E external-client setup.
- Named-preset/Modified tracking, model table redesign, new model recommendations and broad search-modal restyling. Existing presets still work and collapsed Params exposes their effective next-request numeric values.
- New runtime processes, downloads, real inference experiments, dependencies, a custom command palette, session storage, transcript virtualization or a general UI state framework.
- Fixing unrelated staged code, bulk formatting, or widening the supported terminal-size contract below 80×24. Smaller terminals may degrade; the named sizes are the explicit verification targets.

## Risks & Mitigations
- **The transcript rewrite touches many assertions** → migrate the harness and every `chat-log`/`chat-stream` reference in Phase 3; preserve behavior-level history/transport tests, add actual layout/rendering checks, and remove obsolete compatibility widgets.
- **Mount timing and cumulative updates** → create child references before mounting, update only the active widget synchronously, and use final TurnResult text even without preview flushes. Keep one existing transport worker.
- **Late follow-scroll overrides reading position** → capture user intent before mutation and recheck it after layout; cover scrollback plus rapid updates/tab changes with pilot tests.
- **Hidden RichLog defers output rendering** → verify this in Phase 2 and preserve meaningful log assertions; never replace them with a duplicate event buffer just to keep tests green.
- **Params plus Activity consumes short-screen height** → cap their expanded scrolling regions; enforce geometric composer/context/footer bounds and minimum reading rows at the two target sizes.
- **Footer intercepts typing or modal actions** → reuse native Input submission and focus-scoped model bindings; avoid priority overrides and plain-letter global shortcuts; verify search/deletion Escape behavior.
- **Retained partial output looks successful** → keep a visible unsuccessful outcome, omit a successful stamp, and retain the current request-history exclusion rules. A will further refine semantic outcomes.
- **Draft restoration races with navigation/new input/model operations** → restore only an uncommitted draft into an empty composer, keep cleanup idempotent, respect the current lease, and condition focus restoration on the active UI.
- **Overlap with the draft A plan** → preserve transport/accounting contracts now and add an explicit handoff note; A adapts the rendering/recovery seams to its stronger outcomes later.
- **Repeated full Markdown parsing becomes expensive** → reuse the existing 100 ms flush cadence and update only the active response, never rebuild prior turns. Verify long-output responsiveness before considering a more complex renderer.

**Requirement coverage:** compact Params/layout → Phase 1; idle log and visible shortcuts → Phase 2; user-selected persistent transcript and chronological streaming → Phase 3; failure/cancellation recovery and final keyboard/size regression coverage → Phase 4. Implementation authorized by the user on 2026-09-08.
