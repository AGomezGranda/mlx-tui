# Plan Review: Milestone D — Earn Daily Use

**Date:** 2026-09-13
**Target:** docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md
**Review:** 1
**Verdict:** REVISE

## Assessment

The plan has the right product gate, a sound attempt-versus-request-history model, and unusually careful durability language. Its current-state claims and baseline commands largely match the working tree, but implementation as written would leave the full suite broken and several lock, recovery, and save-state transitions ambiguous or incorrect. Resolve the critical test migration and make the session state machine explicit before implementation.

## Cross-Cutting Themes

- The plan promises honest durable state, but ownership of `running`, dirty/saved settings, and destructive actions is split across storage, `ChatPane`, and app lifecycle text without one explicit transition contract.
- The schema and UI are mostly designed around the right facts, but a few presentation fields and cumulative snapshots add cost or coupling without helping recovery.
- The test inventory is broad, yet it misses the exact race/failure branches on which the durability claims depend.

## Findings

### Critical

- **[Test Coverage] Phase 3, composer test migration (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:127`):** the enumerated migration omits `tests/unit/test_params.py:76`, which queries `#chat-input` as `Input`. After `ChatInput` moves from `Input` to `TextArea`, that query cannot match, so implementing the listed changes literally leaves `rtk uv run pytest -q` failing. Add `tests/unit/test_params.py` to the migration list and change the query to the new composer type.

### Major

- **[Correctness] Phases 1–2, live versus interrupted attempts (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:73-74,97,99`):** `load_session()` maps every `running` attempt to `interrupted`, while a second TUI may open a lock-held session read-only. A held lock can mean the first TUI is still generating, so the viewer would falsely present active work as terminal and restore its draft. Preserve `running` while another process owns the edit lock; convert it to `interrupted` only after acquiring the lock and proving no writer remains. Add a two-app running/read-only test.

- **[Correctness / Architecture & Patterns] Phases 1–2, next-request settings synchronization (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:69,71,93-100`):** the session stores next-request settings, but checkpoints are specified only for draft, stream, submit, terminal, switch, clear, and shutdown changes. Today settings mutate through parameter edits (`src/mlx_tui/params.py:106`), profile application (`src/mlx_tui/app/__init__.py:245`), model selection (`src/mlx_tui/app/__init__.py:539`), presets (`src/mlx_tui/app/__init__.py:612`), and config reload (`src/mlx_tui/app/__init__.py:994`). A user can see `Saved locally`, change settings, restart, and recover stale values. Define one `capture_request_settings`/dirty path used by every mutation and both reconciliation choices; checkpoint `Continue with current settings` as well as `Use saved request settings`. Test change → Saved → restart without sending.

- **[Correctness] Phases 1–2, deleting the active locked session (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:72-73,98`):** `ChatPane` holds the session lock for its editable lifetime, yet `delete_session(session_id)` is also required to take the same nonblocking lock. On the target macOS host, a second `flock` through a separately opened descriptor blocks even in the same process, so the proposed API cannot reliably delete its own active session. Delete under the already-held lock token/handle; do not release and reacquire around deletion. Test owner deletion and second-process denial.

- **[Data/Migrations] Phases 1 and 4, persisted identity and bounds (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:68-75,147-149`):** strict validation does not explicitly require the filename UUID to equal the embedded session UUID or attempt UUIDs to be globally unique. A copied/renamed file could therefore be opened under one path and later saved/deleted under another identity, while duplicate attempt IDs make included-turn references ambiguous. Require filename/session-ID equality, unique attempt IDs, and unique prior-only successful references. On v2 load and save, also enforce the 1 MiB aggregate independently for each draft and attempt; `read_attachment(path)` can enforce only the per-file bound. Add crafted-file and multi-file aggregate tests.

- **[Security] Phase 4, attachment destination boundary (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:147-155`):** file contents can be sent to the app's unrestricted host over plaintext HTTP. The request URL is assembled from `host`/`port` in `src/mlx_tui/chat_pane.py:173`, and the existing product explicitly permits best-effort non-loopback hosts without TLS (`README.md:179`). The attachment preview shows content but not its destination. Show the destination in preview/send state and either reject attachments for non-loopback hosts or require explicit confirmation for that destination; add a no-POST regression for the rejected/unconfirmed path.

- **[Architecture & Patterns] Phase 2, progress snapshots (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:92,95`):** supplying copied cumulative answer/reasoning/tool snapshots after every decoded chunk repeatedly joins and copies growing output, making streaming quadratic. The current code accumulates fragments and joins answer text only on its throttled presentation path (`src/mlx_tui/chat.py:88-155`), and persistence is already capped at one checkpoint per second. Emit deltas for `ChatPane` to accumulate, or throttle cumulative immutable snapshots to checkpoint cadence plus terminal completion.

- **[Test Coverage] Phases 1–2, durability and recovery branches (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:74,95-102`):** the tests do not explicitly cover a delayed old save finishing after a newer revision, directory `fsync` failure after successful replacement, or every blocked-action branch after save failure. Add deterministic barriers around two revisions and assert final bytes plus the saved-status revision; inject post-replace directory-sync failure and assert new bytes remain but durability is unconfirmed. Name and test Retry save, explicit discard, and quit retry/quit-with-unsaved actions, including that Retry save sends no HTTP request.

- **[Plan Mechanics] Implementation phases, action sizing:** major bullets combine schema design, validation, projection, locking, atomic I/O, UI state, and lifecycle work far beyond the skill's 2–15 minute action target. Phase 2 in particular is an entire persistence state machine in ten bullets. Split the work into ordered runnable checks: schema codec; identity/reference validation; atomic writer; lock ownership; successful-history projection; draft checkpoint; pre-send checkpoint; progress/terminal checkpoint; switch/clear/delete; reconciliation; shutdown. This will expose dependencies and make phase-level rollback and verification credible.

### Minor

- **[Plan Mechanics] Session listing (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:71-72,97`):** UUID-only filenames and `list_sessions() -> list[Path]` cannot provide first-prompt labels, persisted `updated_at`, or per-file error state without opening each JSON file. Use the minimal honest contract: no session reads at app startup, then load/validate all candidates off-thread when the picker explicitly opens. Remove or narrow the later “lazy transcript loading” claim and test corrupt/newer entries during picker listing.

- **[Architecture & Patterns] Phase 1 schema (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:70`):** persisting rendered `notices` and `stamp` couples the UI-free schema to wording already derived from outcome, accounting, timing, finish reason, and skipped-frame facts in `src/mlx_tui/chat_pane.py:420-480`. Persist raw facts and derive presentation on reopen; add only a bounded structured error category/detail where the facts do not suffice.

- **[Security] Phase 1 storage root (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:72-74`):** rejecting symlinked session files does not protect a pre-existing symlinked sessions directory or lock path. Apply the existing fail-closed managed-root pattern before create, lock, save, or delete so private transcript writes cannot be redirected.

- **[Plan Mechanics] Gate placement (`docs/plans/2026-09-13-part3-milestone-d-earn-daily-use.md:11,66,84`):** the overview requires B evidence and a restart obstacle before Phase 1 implementation, while Phase 1 creates the evidence document and verifies the gate only in its manual criteria. Move the evidence-only step to an explicit Phase 0/precondition with a hard stop before source or test changes.

### Suggestions

- Refresh the small source-line drift in Current State: the Milestone D discussion begins around `docs/part3.md:335`, `ChatTurn` is declared at `src/mlx_tui/chat_turn.py:9`, and `TurnRecord` at `src/mlx_tui/history/store.py:22`.
- Keep the no-index approach unless measured picker latency justifies metadata sidecars; parsing on explicit picker open is the shorter honest implementation.

## Strengths

- The B/D product gates are explicit, preserve missing evidence as missing, and correctly defer Phases 3–4 until observed friction exists.
- The separation between all attempts and the successful-pair request projection matches current behavior and prevents partial/error output from silently entering future context.
- Atomic replacement, directory sync, private modes, per-session locking, and explicit unconfirmed durability establish a serious local persistence contract without adding a database.
- Reopening deliberately avoids model launch, download, runtime ownership restoration, network requests, and metrics duplication.
- Attachment snapshots are bounded, immutable, UTF-8-only, descriptor-checked, previewed through the same renderer used for sending, and never executed.
- Native Textual editing/clipboard capabilities and the existing context trimmer are reused; no speculative tokenizer, parser, cache, index, or title service is introduced.
- Automated and manual criteria are separated, and the baseline diagnosis is reproducible: the full suite currently reports 569 passed, 9 skipped, and the documented comparison-layout failure; the isolated test passes.

## Recommended Changes

1. Add the missing `tests/unit/test_params.py` composer migration.
2. Write a small explicit session state/lock transition table covering editable, locked read-only, interrupted, dirty, saving, saved, failed-save, temporary, and deleting states.
3. Make all next-request setting changes flow through one dirty/checkpoint path and persist both reconciliation choices.
4. Make active deletion consume the existing lock ownership; validate filename/session identity, unique attempt IDs/references, and aggregate attachment limits.
5. Change progress capture to deltas or throttled cumulative snapshots, and derive notices/stamps from raw persisted facts.
6. Put the attachment destination in the trust boundary and specify loopback-only or explicit remote confirmation behavior.
7. Add deterministic save-order, post-replace-sync, two-process-running, retry/discard/quit, and aggregate-attachment tests.
8. Split the large bullets into ordered, checkable implementation actions and move the evidence gate to Phase 0.
