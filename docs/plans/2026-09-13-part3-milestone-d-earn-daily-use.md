# Milestone D — Earn Daily Use

**Date:** 2026-09-13
**Work Item:** n/a
**Status:** In Progress

> Waiver (2026-09-13): operator explicitly waived the Phase 0 B-gate/observed-obstacle precondition and authorized Phase 1 implementation on the Draft plan (skipping Approved). `docs/compatibility/milestone-d.md` still records the gate as blocked; product acceptance remains gated. Phase 1 proceeds as storage-contract work only, no UI changes.

## Overview

Let returning operators reopen a local conversation after restart, recover their drafts and interrupted attempts, and continue with an understandable request context. Start with session recovery; add editing improvements and selected file snapshots only where observed obstacles justify them.

This is a conditional implementation draft, not authorization to bypass the product gate. The user selected session recovery first and agreed to the phase order below. Implementation and adoption claims remain conditional.

Before Phases 3 and 4, record which observed obstacle each selected feature addresses. Defer a phase with no supporting observation. Do not interpret this plan as a commitment to build chat parity.

## Current State

- `docs/part3.md:335` defines the relevant workflow: local conversations with model revision, system prompt, effective profile, messages, attachments and attempt state; interrupted-write recovery; clear/delete/temporary controls; multiline editing, paste, copy, retry, and a picker. File contents must be snapshotted, context exclusions visible, and the complete transcript retained independently of the request window.
- `src/mlx_tui/chat_pane.py:81` keeps `messages`, the pending draft, and the active presentation widget only in memory. Submission at line 127 strips the outgoing prompt but preserves the exact draft. `_commit_success()` at line 610 inserts completed user/assistant pairs together; unsuccessful attempts remain presentation-only. `end_turn()` at line 671 restores a failed draft and releases the operation lease.
- `src/mlx_tui/chat.py:43` exposes final answer, reasoning, structured tool data, finish/completion state, usage and timing through `TurnResult`. `stream_turn()` at line 76 retains partial data locally; its existing activity callback mixes reasoning text with tool-count notices, so it cannot serve as a structured recovery record.
- `src/mlx_tui/chat_turn.py:9` renders an attempt independently of request history. Rehydrating its presentation must not call `_commit_success()` or add duplicate metrics.
- `src/mlx_tui/history/store.py:22` defines metrics-only `TurnRecord`; it cannot restore a conversation. `prepare_context()` in `src/mlx_tui/history/tokens.py:79` produces a separate request window. Its `excluded_turns` value actually counts messages, and the current bar does not include the unsent draft.
- `src/mlx_tui/comparison_persistence.py:36` establishes the XDG state convention; lines 595 and 652 demonstrate strict versioned loading, interrupted-state recovery, and atomic sibling-file replacement. Follow that small write pattern without making sessions depend on comparison schemas or errors.
- `src/mlx_tui/app/__init__.py:245` applies pinned profiles; line 353 cancels and awaits chat cleanup during shutdown. `restore_request_state()` at line 549 can restore request controls while invalidating readiness. Model selection, preset cycling and config editing change next-request settings; historical attempts must retain their original snapshots.
- `src/mlx_tui/models.py:129` resolves an exact cached revision without downloading. `CodingProfile` at `src/mlx_tui/profiles.py:72` contains revision, template, runtime, launch and request settings. Arbitrary attach targets do not provide equivalent verified identity.
- Composer migration has three production sites: `ChatInput`/submission/cleanup in `chat_pane.py`, `MlxTuiApp.set_operation_ui()` at `app/__init__.py:687`, and `ComparePane._go_chat()` at `compare_pane.py:1065`.
- `tests/conftest.py:283` and `tests/integration/conftest.py:14` create apps without shared state-directory isolation. Some comparison tests isolate state only after app construction. New persistence must not read or write the developer's actual sessions during tests.
- Installed Textual provides `TextArea`, `TextArea.Changed`, `load_text()`, `selected_text`, and `read_only`; these APIs were inspected locally. `App.copy_to_clipboard()` uses terminal OSC 52 and explicitly documents a macOS Terminal limitation. `/usr/bin/pbcopy` is available on this Mac.
- `README.md:136` and `ARCHITECTURE.md:145` contain stale claims that length-capped/empty responses enter request history. Current code excludes them. Update those paragraphs when documenting session recovery.

Research verification on the current, already-modified working tree:

| Command | Result |
|---------|--------|
| `rtk uv run pytest -q` | 569 passed, 9 skipped, 1 failed in 175.93s. Existing failure: `tests/integration/test_compare_integration.py:582`, `test_comparison_layout_adapts_and_reveals_results`, expects the results panel hidden but finds it visible. |
| `rtk uv run ruff check .` | Passed. |
| `rtk uv run pyrefly check` | Passed, 0 errors; 190 suppressed. |
| `rtk proxy uv run ruff format --check src tests` | Passed; 81 files already formatted. |
| `rtk proxy uv run mlx-tui --help` | Passed; attach/managed and host/port options exist. |

The failing layout test passed when rerun with `XDG_STATE_HOME` set to a fresh temporary directory before launching `uv run pytest -q tests/integration/test_compare_integration.py::test_comparison_layout_adapts_and_reveals_results` (1 passed in 1.02s). This implicates ambient saved state: app initialization loads a saved choice and comparison before the unisolated test runs. The complete suite was not rerun under isolation, so its baseline remains the result above.

No live inference or adoption validation was performed. Runtime skips are not acceptance evidence. All new files, types, methods and UI controls named below are proposed additions: verify during implementation against the then-current tree. Existing references above were read during research.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| One versioned JSON snapshot per session | JSON snapshot; append log; SQLite | Matches existing persistence and keeps recovery inspectable. No database, index, sync layer or migration framework. |
| Separate attempts from successful request history | Persist metrics; scrape widgets; typed session attempts | Only structured attempts can preserve unsuccessful output, exact drafts and settings without resending them accidentally. |
| Explicit reopening through a session picker | Automatically reopen the last file; picker | Avoids a second active-session pointer and implicit runtime changes. Show saved sessions when Chat is opened, without loading a model or submitting a request. |
| Save ordinary sessions locally; explicit temporary sessions | Always ephemeral; always persistent; selectable mode | Makes restart recovery useful while preserving a visible no-persistence option. Create a file only after meaningful content or a draft exists. |
| Per-session advisory write lock and atomic replacement | Last-writer-wins; global app lock; per-session lock | Two TUI instances may coexist, but cannot silently overwrite the same conversation. Use stdlib `fcntl` on the project's Unix platforms. |
| Small asynchronous checkpoints with honest save state | Write each token synchronously; save only on exit | Keep terminal interaction responsive and survive process interruption. Durable acknowledgements cover completed writes; unsaved edits and the latest stream tail are explicitly pending. |
| Restore transcript first; explicitly reconcile settings | Automatically apply saved model/runtime; retain current settings silently | Historical identity is not proof of present readiness. Reopening must not launch, stop, download, run saved commands or change ownership. |
| Native multiline editor and plain-text copy viewer | Custom editor; Markdown/code extraction parser | Textual already supplies editing, selection, paste and undo. Copy code by selecting its exact text in a read-only viewer; no new parser dependency. |
| Selected UTF-8 file snapshots, without automatic truncation | Directory indexing; live path references; bounded snapshots | The user can inspect exactly what will be sent. Reject oversize content and ask for a smaller selection instead of silently changing it. |
| Keep character estimates explicit | Add a tokenizer dependency now; claim architectural limits; retain the existing estimator | No verified same-runtime/template token-counting path exists in the inspected TUI transport. Tokenizer integration is a separate, evidence-gated follow-up. |

## Implementation Phases

### Phase 0: Evidence gate (precondition — no source or test changes)

Record the completed Milestone B qualification/product gate and at least one observed restart/recovery obstacle in `docs/compatibility/milestone-d.md` before any Phase 1 implementation. B is currently unachieved: the second physical tier, live qualification, and operator return-use evidence are missing. C's existing managed implementation is not evidence that B or D passed.

**Changes:**
- `docs/compatibility/milestone-d.md` — new evidence document. Record the B gate references, consenting participant IDs, concrete restart obstacle, and baseline observation window. Mark absent evidence as unobserved; do not manufacture participants or results.

**Success Criteria:**

#### Automated Verification:
- [ ] No source or test files changed in this phase (`git status --porcelain` shows only `docs/compatibility/milestone-d.md`, if anything).

#### Manual Verification:
- [ ] Review the consented obstacle/baseline record and confirm the B entry gate is backed by retained evidence before proceeding. Hard stop: do not start Phase 1 without it.

### Phase 1: Durable session records and isolated tests

Introduce the storage contract without changing the running chat UI. Phase 0 must be complete first.

**Changes (in order; each is one runnable check):**
- 1a. `tests/conftest.py` — add a function-scoped autouse fixture setting `XDG_STATE_HOME` to `tmp_path` before any app construction. Cover direct `MlxTuiApp` tests as well as both harnesses. Existing tests may override this isolated root to exercise persistence across two app lifetimes within one test.
- 1b. `src/mlx_tui/sessions.py` — new UI-free session model and persistence module. Define the following schema-v1 records using dataclasses and strict JSON validation:
  - `RequestSettings`: nullable requested model identifier (an unsent draft may have no selection); optional verified repository/revision; system prompt; normalized temperature, top-p, max tokens and configured context budget; optional seed/thinking flag; optional profile ID/fingerprint plus modified flag. Capture the known profile runtime/template/launch values with their provenance as requested or verified; unavailable values remain null. Store request settings only, never executable start/stop commands or runtime ownership.
  - `SessionTurn`: UUID, timestamp, exact original draft, exact sent user content, `RequestSettings`, included prior successful turn IDs, estimated input/reserved output and excluded-message count; answer, separate reasoning and structured tool-call data; response model, finish reason, skipped-frame count, stream-completion flag, outcome, plus raw failure/accounting facts and a bounded structured error category/detail. Do not persist rendered `notices` or `stamp`; derive presentation from raw facts on reopen. Request-window/accounting fields remain null when preparation never completed. Outcomes are `running`, `success`, `length_capped`, `tool_only`, `empty`, `incomplete`, `failed`, `cancelled`, `interrupted`.
  - `ChatSession`: schema version, UUID, created/updated timestamps, next-request `RequestSettings`, exact composer draft, ordered attempts. Derive picker labels from a short first-prompt excerpt and date; do not add a title-generation service. No index or metadata sidecar.
- 1c. `src/mlx_tui/sessions.py` — implement `session_dir() -> Path`, `load_session(path: Path) -> ChatSession`, `save_session(session: ChatSession) -> Path`, `list_sessions() -> list[Path]`, and `delete_session(session_id: str, lock_handle=None) -> None`. Generate filenames from validated UUIDs under `$XDG_STATE_HOME/mlx-tui/sessions`, falling back to `~/.local/state/mlx-tui/sessions`. Apply the existing fail-closed managed-root check to the sessions directory and lock path before create, lock, save, or delete, in addition to rejecting symlinked/non-regular session files. Reject invalid IDs, filename/session-UUID mismatch, duplicate attempt UUIDs, duplicate or non-prior-only successful references, unsupported schemas, invalid finite-number/range/type values, inconsistent successful attempts and references to missing/later/unsuccessful turns. On v2 load and save (Phase 4), enforce the 1 MiB aggregate independently for each draft and each attempt. Keep corrupt/newer files intact and report each independently. Contract: no session reads at app startup; on explicit picker open, load/validate all candidates off-thread when the picker opens.
- 1d. `src/mlx_tui/sessions.py` — follow the comparison writer's temporary-file, flush, `fsync`, replace, cleanup sequence, and sync the directory before acknowledging durability. Use private directory/file modes (0700/0600). Preserve the previous complete file when replacement fails; report post-replacement sync failures as unconfirmed durability (new bytes remain on disk but durability is unconfirmed). Never truncate the transcript to fit storage or context. `load_session()` preserves `running` while another process owns the edit lock; it maps `running` to `interrupted` in memory only after acquiring the lock and proving no writer remains, without rewriting during listing.
- 1e. `src/mlx_tui/sessions.py` — implement `lock_session(session_id: str)` as a context manager retaining a nonblocking exclusive `fcntl.flock` on a stable, content-free sibling lock file. Hold it while a session is editable; a locked session can be viewed read-only. Owner deletion consumes the already-held lock token/handle (`delete_session(..., lock_handle=...)`); do not release and reacquire around deletion and do not open a second descriptor for the same session in the owner (a second `flock` through a separately opened descriptor blocks even in the same process on macOS). Do not unlink lock files while another process may hold them. Document the explicit state/lock transition table in the module docstring: editable, locked read-only, interrupted, dirty, saving, saved, failed-save, temporary, deleting — including who may write, when `running` becomes `interrupted`, and which transitions require a held lock.
- 1f. `tests/unit/test_sessions.py` — new focused storage checks: Unicode/settings/tool-data round trip, strict schema rejection, corrupt-file isolation, interrupted-state recovery, write/replace failure preserving the prior bytes, permissions, invalid turn references and two independent lock holders. Verify recovery derives request messages only from `success` pairs; partial/error attempts never enter that derived history. Add: filename/UUID-mismatch and duplicate-attempt-ID/reference crafted-file rejections; per-draft and per-attempt 1 MiB aggregate tests; deterministic two-revision barrier test (older save finishing after a newer revision must not mark newer edits saved — assert final bytes plus the saved-status revision); post-replace directory-sync failure test (new bytes remain, durability unconfirmed); owner-delete-under-held-lock and second-process-denial tests; two-app running/read-only test (viewer preserves `running`, owner proves `interrupted` after lock acquisition).

**Success Criteria:**

#### Automated Verification:
- [ ] Storage recovery, failure injection and shared state isolation pass with the existing suite: `rtk uv run pytest -q`. The baseline comparison-layout failure must be resolved by isolation or diagnosed explicitly; do not weaken its assertion.
- [x] Types and lint pass: `rtk uv run pyrefly check` and `rtk uv run ruff check .`.

> Phase 1 progress (2026-09-13): 1a–1f implemented (`tests/conftest.py` isolation fixture, `src/mlx_tui/sessions.py`, `tests/unit/test_sessions.py` 23 tests). `ruff check`, `pyrefly check`, `ruff format --check` pass. Full suite: 592 passed (569 baseline + 23 new), 9 skipped. The baseline comparison-layout failure is gone in two full runs (resolved by 1a isolation). One remaining failure, `test_chat_lease_blocks_swap_and_delete_until_cleanup` (`assert '' == 'hi'`), passes alone and at file level; a scratch repro proved the mechanism (escape landing after the stub's 0.5s slow-hold lets the turn succeed and clear the composer — pre-existing unguarded timing window, untouched by this phase; scratch file deleted). Suite box stays unchecked pending a green full run.

#### Manual Verification:
- [ ] Confirm Phase 0 evidence remains recorded; do not re-gate here.
- [ ] Inspect a generated test session: messages and unknown identity labels are understandable, and files contain no executable runtime-control configuration.

### Phase 2: Restart recovery and session controls

Connect durable records to the existing turn lifecycle, including failure and shutdown boundaries.

**Changes (in order; each is one runnable check):**
- 2a. `src/mlx_tui/chat.py` — add `TurnProgress` deltas (answer/reasoning/tool-call fragments plus observed response model), plus optional `on_progress: Callable[[TurnProgress], None] | None = None` on `stream_turn()`. `ChatPane` accumulates deltas; alternatively emit throttled cumulative immutable snapshots at checkpoint cadence (≤1/s) plus terminal completion only. Do not join/copy growing output after every decoded chunk. Keep existing callers valid. Final success still comes only from `TurnResult` and the pane's identity check, never from a progress notification.
- 2b. `src/mlx_tui/chat_pane.py` — own the active `ChatSession`, its lock handle, and a save revision counter. Keep `messages` as the successful-pair projection for current callers; make session attempts authoritative for restoring presentation. Implement one `capture_request_settings()`/mark-dirty path used by every next-request mutation (parameter edits, profile application, model selection, presets, config reload); both reconciliation choices checkpoint through it.
- 2c. `src/mlx_tui/chat_pane.py` — draft checkpoint: coalesce draft changes at 250 ms, serialize file writes with one async lock, offload filesystem work with `asyncio.to_thread`. Take the latest snapshot after acquiring the write lock; an older completed save must not mark newer edits saved. Show `Saving…`, `Saved locally`, `Save failed — retry save`, or `Temporary` explicitly.
- 2d. `src/mlx_tui/chat_pane.py` — pre-send checkpoint: capture normalized controls and the exact model at send, including historical profile metadata, through the single dirty path. Map included request pairs to their turn IDs. Checkpoint the draft and `running` attempt before starting HTTP. If saving fails, retain the composer and send no request. Keep every final outcome, identity mismatch, setup/context error, transport failure and cancellation in the session, with the exact draft and any partial progress. Capture progress directly from `TurnProgress` deltas; never infer reasoning from the existing tool-count activity string.
- 2e. `src/mlx_tui/chat_pane.py` — progress/terminal checkpoint: stream at most once per second plus immediate checkpoints at submit, terminal outcome, session switch, clear, and orderly shutdown. A completed generation whose save fails remains visible in memory and retains its outcome. Block further sending, clearing or switching until Retry save succeeds or the user explicitly discards unsaved work. Retry save performs no HTTP request. Keep the operation lease until network cleanup, and never promote a cancelled request to success.
- 2f. `src/mlx_tui/session_screen.py` — new `SessionScreen`, following the existing modal/picker conventions. On explicit picker opening only, read labels/timestamps off-thread and list sessions by updated time, including readable error/locked states; do not render transcripts until selected. Add Open and Delete; viewing a locked session offers no editing. Opening acquires the lock before replacing the active view (preserving `running` for a lock-held session viewed elsewhere; mapping to `interrupted` only after lock acquisition proves no writer remains). Corrupt/newer sessions remain available for explicit deletion without being auto-overwritten.
- 2g. `src/mlx_tui/chat_pane.py` and `src/mlx_tui/chat_turn.py` — add a compact session status/actions row with Sessions, New, New temporary, Clear, and Delete. Reuse `ConfirmScreen` for destructive clear/delete actions. Clear removes attempts/draft from the current session after successful replacement; Delete consumes the already-held lock handle and starts an empty one. Neither action clears comparison results or Metrics. Creating a temporary session never writes its draft, output or attachments, including to checkpoint/temp files. Leaving it requires explicit discard when it contains work.
- 2h. `src/mlx_tui/chat_turn.py` — re-render saved attempts from raw persisted facts (answer/reasoning/tool data, outcome, accounting, timing, finish reason, skipped frames, error category/detail), deriving notices/stamps at display time. Reopening adds no metrics and no network request. A recovered running attempt displays `Interrupted — not included in next request` and restores its original draft only if there is no newer saved composer draft.
- 2i. `src/mlx_tui/app/__init__.py` and `src/mlx_tui/chat_pane.py` — on reopen, display the transcript with current runtime state unchanged. If saved next-request settings differ, block Send until the user chooses Use saved request settings or Continue with current settings. Checkpoint both choices through the single dirty path. For the saved choice, verify any known exact cached revision/profile fingerprint first, merge only request fields into the current config, and use `restore_request_state()` to invalidate readiness. Missing/changed assets leave the transcript accessible and direct the user to existing Models/Compare recovery. Never restore endpoint credentials, commands, process identity or runtime ownership from session data.
- 2j. `src/mlx_tui/app/__init__.py` — connect normal quit to abort/await/flush before exit and offer Retry save versus explicit quit-with-unsaved-work on save failure. Keep `on_unmount()` as an idempotent best-effort flush for existing signal-driven exits, then release session locks and retain managed-child shutdown. SIGKILL/power loss recovers only acknowledged checkpoints; no zero-loss claim for pending text.
- 2k. `tests/unit/test_chat.py` and `tests/integration/test_sessions.py` — test partial answer/reasoning/tools through cancellation and errors; successful restart and continuation without duplicated context/metrics; interrupted recovery; draft autosave; save failures before HTTP and after completion; settings mismatch/missing model; locked/corrupt session access including corrupt/newer entries during picker listing; clear/delete; and zero session-content writes in temporary mode. Use two app instances sharing one isolated state root for restart scenarios. Add: change-settings → Saved → restart-without-sending test; delayed-old-save-after-newer-revision barrier test at pane level; post-replace directory-sync failure test (new bytes remain, durability unconfirmed); named Retry save / explicit discard / quit-retry / quit-with-unsaved tests (asserting Retry save sends no HTTP); every blocked-action branch after save failure.
- 2l. `README.md` and `ARCHITECTURE.md` — document session location, controls, save acknowledgements and limits. Correct stale empty/length-cap history claims to match current classification.

**Success Criteria:**

#### Automated Verification:
- [x] Restart, request-payload, interruption and storage-failure regressions pass without personal state access: `rtk uv run pytest -q`.
- [x] Lint, types and formatting pass: `rtk uv run ruff check .`, `rtk uv run pyrefly check`, and `rtk proxy uv run ruff format --check src tests`.

> Phase 2 progress (2026-09-13): 2a–2l implemented (`TurnProgress`/deltas in `chat.py`, session ownership/checkpoints/controls/reconcile/quit-flush in `chat_pane.py`, `render_session_turn()` in `chat_turn.py`, new `session_screen.py`, dirty-path hooks in `app/__init__.py`, 2 unit + 12 integration session tests, README/ARCHITECTURE updates). Verified: full suite 607 passed, 9 skipped; `ruff check`, `pyrefly check`, `ruff format --check` pass. Layout test required compacting the session row to height 1. Two fixed during implementation: pre-send failure return bypassed `end_turn` (lease stuck — now ends the turn), and spurious empty saves cleared pending failures (now preserved). Manual verification left to the operator.

#### Manual Verification:
- [ ] Complete a turn, type a draft, wait for Saved locally, close/reopen, choose the session and continue; verify the exact saved draft and transcript remain and no request was sent merely by reopening.
- [ ] Close during generation, reopen and distinguish partial output from a successful reply. Confirm client cancellation never claims engine cancellation or restored warm cache.
- [ ] Exercise an unwritable state directory and a second TUI viewing the same session; visible work remains intact and no save/conflict is silently ignored.
- [ ] Check session controls and focus at 80×24 and 120×40, including long model names and a corrupt saved file.

### Phase 3: Multiline editing, copying and explicit retry

Implement the editing features selected by observed task friction, preserving Phase 2 durability.

**Changes:**
- `src/mlx_tui/chat_pane.py` — replace `ChatInput(Input)` with `ChatInput(TextArea)` while retaining `#chat-input`. Add a `Submitted` message carrying the editor and exact text; route both an explicit Send button and Ctrl+Enter to one submission method. Enter inserts a newline, Tab moves focus, and paste never submits. Keep an editable 3–8-row bounded composer. Use `text`/`load_text()` and `TextArea.Changed` for restoration and saving; test emptiness with `strip()` but send the original text so code indentation is preserved.
- `src/mlx_tui/chat_pane.py` — Retry on the most recent unsuccessful attempt restores its original draft and settings for review without sending automatically. If a newer draft exists, retain it and offer explicit replacement. Previous attempts remain visible. A normal Send creates exactly one new attempt through the existing operation lease.
- `src/mlx_tui/text_screen.py` — new `TextPreviewScreen` with a read-only `TextArea` and Copy all/Copy selection actions. On macOS use `/usr/bin/pbcopy` through argv-only subprocess input, with timeout/error reporting and no temporary content file; elsewhere use the installed Textual clipboard method and label terminal support limitations. Do not show copy success after a failed command.
- `src/mlx_tui/chat_turn.py` — expose View/copy answer for completed or partial answers using `TextPreviewScreen`. Keep reasoning/tool data separate. Code can be selected and copied exactly, without stripping fences through a new parser or executing anything.
- `src/mlx_tui/app/__init__.py` and `src/mlx_tui/compare_pane.py` — migrate only chat-composer type queries to `ChatInput`, preserving busy-state disabling and Go to Chat focus. Leave parameter, search and comparison inputs as `Input`.
- Update chat-composer interactions in `tests/unit/test_params.py` (`:76` queries `#chat-input` as `Input` — change to the new `ChatInput`/`TextArea` type), `tests/integration/test_app_integration.py`, `test_boot_integration.py`, `test_chat_cancellation.py`, `test_chat_integration.py`, `test_compare_integration.py`, `test_config_integration.py`, `test_context_integration.py`, `test_delete_integration.py`, `test_metrics_integration.py`, and `test_swap_integration.py`. Replace composer-specific `.value`, Enter-to-send and direct `Input.Submitted` events; preserve unrelated `Input` uses. Extend `test_sessions.py` with exact multiline/Unicode draft recovery.
- `tests/integration/test_chat_integration.py` — exercise newline insertion, multiline paste containing blank lines/indentation, single submission from Send/Ctrl+Enter, retry preserving prior context, selection-copy contents, clipboard failure, focus and scrolling. Mock the clipboard subprocess in tests.
- `README.md` — update the keyboard table and recovery instructions with multiline sending and copy behavior. Do not reassign the existing Ctrl+N/Ctrl+O preset bindings.

**Success Criteria:**

#### Automated Verification:
- [x] Updated input events and all payload/cancellation/Compare focus regressions pass: `rtk uv run pytest -q`.
- [x] Type queries, imports and formatting pass: `rtk uv run pyrefly check`, `rtk uv run ruff check .`, and `rtk proxy uv run ruff format --check src tests`.

> Phase 3 progress (2026-09-13): Multiline `ChatInput`/TextArea submission, exact draft retry, read-only answer preview and clipboard actions are implemented. Composer-specific tests now cover paste/newlines, explicit Send/Ctrl+Enter submission, retry preservation, answer view, and clipboard failure. The compact layout uses a three-row composer so 80×24 remains non-overlapping. Verified: 611 passed, 9 skipped; `pyrefly check`, `ruff check`, and `ruff format --check` pass.

#### Manual Verification:
- [ ] Paste an indented code sample with blank lines; edit it, restart, and send once with exact whitespace retained.
- [ ] Copy an answer and a selected code fragment in macOS Terminal and one other available terminal. Check the actual system clipboard, not just the widget's internal clipboard.
- [ ] Retry a failed request while a different draft exists; neither draft is silently lost. Verify keyboard-only operation and visible Send at 80×24.

### Phase 4: Selected file snapshots and request-context preview

Implement only after an observation identifies repeated file-paste or context-understanding friction.

> Implementation note (2026-09-13): the user explicitly authorized continuing this phase despite the Phase 0 B/obstacle gate remaining unverified. No qualification or adoption evidence is inferred here; Phase 4 is recorded as implementation-only and the Phase 0/Phase 5 acceptance work remains open.

**Changes:**
- `src/mlx_tui/attachments.py` — new UI-free `AttachmentSnapshot` and `read_attachment(path: Path) -> AttachmentSnapshot`. Retain the user-selected path, resolved path, byte length, SHA-256 and exact UTF-8 content. Open only the explicitly selected file, verify the opened descriptor is a regular file, and bound reads before decoding. Reject NUL-containing/binary or invalid UTF-8 content, directories, special files, missing/unreadable files and files larger than 256 KiB. `read_attachment()` enforces only this per-file bound. Never walk directories, download, execute or modify source files.
- `src/mlx_tui/attachments.py` — define `render_user_content(prompt: str, attachments: tuple[AttachmentSnapshot, ...]) -> str` with one deterministic format: the exact prompt followed by numbered, labelled file blocks containing the exact snapshot text. Filenames/content are user data, never system instructions. Do not truncate. Preview and sending use this same function.
- `src/mlx_tui/sessions.py` — evolve writes to schema v2 with draft attachments and immutable per-attempt attachment snapshots. Accept v1 as empty attachment lists and upgrade in memory; write v2 only when saving. Enforce the 1 MiB aggregate independently for each draft and each attempt on v2 load and save. Keep previously recorded `sent user content` authoritative for replay. Validate snapshot hashes/byte lengths and refuse unexpected schema values. Temporary-session snapshots remain memory-only.
- `src/mlx_tui/chat_pane.py` — add explicit path input plus Add file, inspect/remove actions and an attachment summary showing path, bytes and estimated tokens. Show the request destination (host/port) in the attachment preview and send state. Attachments to non-loopback hosts require explicit per-destination confirmation, or are rejected for non-loopback hosts; without confirmation send no POST. Read file contents off the UI thread. Capture the snapshot once at attachment time; source edits/deletion must not affect restart, retry or later request reconstruction. Re-adding a changed file is an explicit new snapshot. Removing a draft attachment never edits an earlier attempt.
- `src/mlx_tui/chat_pane.py` — calculate the next request using successful message pairs plus the current draft and rendered attachments. Recompute on draft/file/parameter/system/model changes. Display estimated input, reserved output, configured limit and excluded **messages**, with a preview of exactly retained messages and file blocks through `TextPreviewScreen`. A request too large to fit shows the reason before sending and sends no POST.
- `src/mlx_tui/chat_pane.py` and `src/mlx_tui/params.py` — use one prepared window for send-time validation, persisted included-turn IDs and outgoing payload. Parameter edits trigger preview refresh. Recheck on Send so a stale preview cannot authorize different content; once submitted, freeze its settings/content. Server context rejection retains the draft, snapshots and full transcript and offers reducing context or output allowance, without automatic resubmission.
- `src/mlx_tui/history/tokens.py` — retain `prepare_context()` as the single trimming implementation; clarify the legacy `excluded_turns` field as a message count without renaming the persisted Metrics field. Keep character estimates and configured limits explicitly labelled, and keep transcript storage independent of trimming. Do not add tokenizer/model-limit claims.
- `tests/unit/test_attachments.py` — new bounded-read, UTF-8/hash, deterministic rendering, invalid/special-file and changed-source checks. Extend `tests/unit/test_sessions.py` for v1→v2 compatibility, attachment recovery, snapshot validation, and per-draft/per-attempt 1 MiB aggregate enforcement (including multi-file totals exceeding the bound).
- `tests/unit/test_tokens.py`, `tests/integration/test_context_integration.py`, and `tests/integration/test_sessions.py` — verify preview equals the actual POST, input plus output reservation includes all attachment content, oldest complete pairs leave only the request window, oversize sends no POST, and source modification/deletion after attachment cannot change resumed content. Verify the attachment preview shows the destination and that rejected/unconfirmed non-loopback sends issue no POST. Verify raw terminal/server errors do not cause prompt/file content to enter default diagnostic logging.
- `README.md` and `ARCHITECTURE.md` — document selected-file limits, immutable snapshots, temporary behavior, clear/delete removal, preview labels and the estimator's lack of tokenizer guarantees.

**Success Criteria:**

#### Automated Verification:
- [x] File-boundary, schema-upgrade, preview/payload and restart tests pass: `rtk uv run pytest -q`.
- [x] Lint, types and formatting pass: `rtk uv run ruff check .`, `rtk uv run pyrefly check`, and `rtk proxy uv run ruff format --check src tests`.

#### Manual Verification:
- [ ] Attach a small code file, inspect the complete preview, then edit/delete the source and restart; the session still shows and sends the selected snapshot.
- [ ] Explain which earlier messages were excluded and why; they remain readable in the transcript. Oversize input is rejected visibly, with the draft and files intact.
- [ ] Inspect a long filename and multiple files at 80×24; all inspect/remove controls remain keyboard-accessible. Temporary mode leaves no content checkpoints on disk.

### Phase 5: Real-task recovery and repeat-use evidence

Validate the chosen features against actual work, rather than marking D complete when persistence demos pass.

**Changes:**
- `docs/compatibility/milestone-d.md` — retain an implementation checklist separately from product acceptance. Before enabling each selected feature, record participant consent, anonymous ID, tier/runtime, task category, observed obstacle, existing workaround, baseline dates and separate-session use count. Do not collect ordinary prompts, answers or file contents in the research record.
- `docs/compatibility/milestone-d.md` — use matched 14-day baseline and follow-up windows as this draft's proposed observation protocol, not a threshold specified by Part 3. Record each participant's task opportunities, restarts, resumed tasks, completed tasks, assistance, saved/unsaved work loss, session-use counts, attributed value, dropouts and alternative-tool reasons. Retain the raw counts and denominators; disclose unequal observation windows instead of comparing them as equal exposure.
- `docs/compatibility/milestone-d.md` — run a named-Mac real-runtime recovery exercise using the existing pinned attach or managed setup: complete a coding turn, save a draft, restart the TUI, explicitly restore/reconcile settings and continue; repeat for client cancellation, endpoint failure, and context rejection. If Phase 4 was selected, include a snapshotted file whose source changes before restart. Record runtime/model identity, exact procedure, failures and retained evidence references. Verify during implementation against the available qualified hardware; no new generic benchmark runner is needed.
- `docs/compatibility/milestone-d.md` — mark D achieved only when returning users complete real tasks after restart without losing acknowledged saved work, observed repeat use improves over their own pre-feature baseline, and users attribute value to the added workflow. Examine failures/dropouts even if totals improve. Otherwise record a revise/defer decision and avoid funding the remaining optional scope solely because the implementation works.
- `docs/part3.md` — add a dated D implementation-versus-validation status entry, linking this plan and the observation record. Preserve B/C gate truth and avoid retroactively claiming adoption evidence.
- `README.md` and `ARCHITECTURE.md` — reconcile the shipped subset and limitations with the final selected phases. Explicitly state that conversation restoration does not restore a warm KV cache or prove model readiness.

**Success Criteria:**

#### Automated Verification:
- [x] Final regression suite passes: `rtk uv run pytest -q`.
- [x] Final static checks pass: `rtk uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, and `rtk uv run pyrefly check`.
- [x] CLI entry point remains available: `rtk proxy uv run mlx-tui --help`.

> Phase 5 progress (2026-09-13): The implementation/evidence record now
> separates shipped scope from product acceptance, defines the consented
> matched 14-day observation protocol with raw counts and denominators, records
> the named-Mac four-scenario recovery procedure, and defers D because no
> qualifying participant or runtime evidence exists. `docs/part3.md`, README,
> and architecture documentation link that status and preserve the warm-cache
> and readiness limitations. Verified: full suite passed with 9 opt-in runtime
> skips; `ruff check`, `ruff format --check`, `pyrefly check`, and
> `mlx-tui --help` pass. Manual evidence criteria remain open.

#### Manual Verification:
- [ ] Retain real-runtime restart/recovery evidence on named hardware, covering successful, cancelled, failed and context-rejected work with the selected feature subset.
- [ ] Review consented before/after observations and user explanations against Part 3's D gate; do not substitute the automated suite or a maintainer demo for returning-user evidence.
- [ ] Record the achieve/revise/defer decision and remaining limitations. Keep product acceptance open if observations are absent.

## Out of Scope

- Implementing Phases 1+ before the Phase 0 B entry gate and observed obstacle are documented; inventing fresh-install or return-use evidence.
- Durable metrics history, cloud sync, a database, background analytics, chat search, branching/regeneration of successful turns, automatic session summaries or automatic retries.
- Filesystem indexing, folders/globs, binary/PDF/image attachments, retrieval, embeddings, an agent harness, or tool execution.
- Tokenizer integration without a verified matching runtime/template path, inferred architectural context limits, persisted KV caches, optimization tuning or guaranteed fit claims.
- New inference loops, process ownership changes, background serving, external-client contention guarantees, packaging/upgrade work from E/F, or automatic model download during reopen.
- Guaranteed recovery of text still marked Saving after a hard kill/power loss. Private local permissions do not imply encrypted storage or secure erasure of OS backups.

## Risks & Mitigations

- **Scope outruns user evidence** → gate implementation on B plus a real obstacle; gate editing/files separately, and keep adoption status distinct from code completion.
- **Late checkpoints overwrite newer work** → one serialized writer, monotonically tracked revisions, immediate terminal/switch flushes and no false Saved label.
- **Two processes overwrite one conversation** → hold a per-session advisory lock across edit lifetime and consume it for owner deletion; allow read-only inspection elsewhere. Preserve `running` for lock-held sessions viewed from a second process.
- **Write failure loses a completed response or triggers duplicate inference** → preserve the in-memory attempt, retain the previous file, block destructive navigation and distinguish Retry save from Retry request.
- **Partial output reappears as valid context** → typed outcomes, authoritative success classification, successful-pair projection and restart payload assertions.
- **Old settings imply current readiness** → explicit reconciliation, exact revision/profile checks where known, request-only restoration and unknown readiness until new runtime evidence exists.
- **Snapshot growth slows daily use** → bounded attachments, off-thread serialized writes, no startup reads with off-thread full load only on explicit picker open (no lazy per-transcript paging, no index/sidecar unless measured picker latency justifies it) and a new-session action. Mark the full-file rewrite with a `ponytail:` comment explaining its O(session-size) write cost and an append-only format as the upgrade path if measured saves affect responsiveness. Do not silently truncate saved work.
- **Preview estimates are wrong for the model** → label them as character estimates, preserve full work on server rejection, and defer tokenizer claims until separately verified.
- **Terminal input/clipboard differences** → explicit Send fallback, native multiline editing, actual macOS clipboard checks and visible copy failures.
- **Persistence contaminates tests or exposes content in logs** → isolate state before app construction, mock clipboard operations, and test content-free diagnostic paths. Never export conversations/attachments by default.
