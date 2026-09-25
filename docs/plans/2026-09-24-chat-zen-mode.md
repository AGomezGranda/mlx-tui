---
date: 2026-09-24
title: Minimal chat Zen mode
status: in_progress
---

# Minimal chat Zen mode

Create a quiet, polished chat view, toggled with F4. The user approved three phases and clarified that information must remain a small, simple UI element. This is a presentation change to the existing chat, not a second chat implementation.

## Progress

- [x] Phase 1 — Toggle and continuity
- [x] Phase 2 — Refined layout and one compact information element
- [ ] Phase 3 — Interaction and layout verification (manual theme and real-provider checks pending)

## Current State

- `src/mlx_tui/app/app.py:196` composes the status bar, four tabs, Activity, and footer. Its bindings have F2/F3 but no F4. `on_resize` at line 211 applies compact styling below 32 rows.
- `src/mlx_tui/chat_ui/pane.py:166` composes parameters, transcript, context, session/recovery controls, attachments, and composer. `_queue_follow` at line 223 depends on the existing Chat tab remaining active. `_render_session` at line 269 restores the same composer and transcript.
- `src/mlx_tui/chat_ui/widgets.py:39` owns multiline editing: Enter inserts a newline, Ctrl+Enter sends, and Tab moves focus.
- `src/mlx_tui/chat_ui/context.py:273` calculates context for the draft; `update_ctx_bar` at line 292 displays estimated input plus reserved output, with exclusion warnings. Reuse this calculation.
- `src/mlx_tui/chat_ui/turns.py:544` formats detailed live statistics; `src/mlx_tui/chat_turn.py:139` formats restored statistics. Raw completion counts and elapsed time already exist; do not parse these display strings.
- `src/mlx_tui/chat_ui/persistence.py:280` updates save status; line 295 controls recovery rows and disabled inputs. `src/mlx_tui/app/ui.py:116` logs warnings that would otherwise disappear with Activity hidden.
- `tests/integration/test_app_integration.py:357` already checks mounted layout at 80×24 and 120×40. Reuse the existing `AppHarness` and local HTTP stub from `tests/conftest.py:230`.
- The working tree contains ongoing changes, including these app and test files. Implementation must reread their current contents and preserve unrelated work; references describe this planning snapshot.

## Implementation Note

Textual exposes the tab header as `ContentTabs`, and component CSS cannot hide the base tab widget. The Zen selectors for outer chrome and tab headers therefore live in the app stylesheet; ChatPane keeps the scoped styles for its own content.

## Design Decisions

### Visual contract

The resting screen contains the conversation, composer, and **one single-row metadata element**. No dashboard, cards, charts, full-width context meter, or repeated statistics beneath every answer.

Example metadata, using illustrative values:

```text
Qwen · 248 out · 3.2s · ctx ≈28%
```

- Center the existing ChatPane with a maximum width of 96 terminal cells; use available width on smaller terminals. Keep the transcript flexible and the composer anchored at the bottom.
- Use existing theme colors, muted metadata, clear speaker labels, a subtle composer border, and modest spacing between turns. Do not hard-code a dark theme or add animations.
- Put the compact `Ctrl+Enter send · F4 exit` hint in the composer's border subtitle; while generating, show `Esc stop · F4 exit`. Keep Send available as a small button.
- The metadata shows the shortened selected model, latest attempt's output count and elapsed time, and context percentage. Before a result, omit metrics; during a request, replace previous metrics with `Thinking…` or `Responding…`. Do not add a live clock or new polling loop.
- Context percentage uses estimated reserved tokens / configured limit, including the output allowance, and retains existing warning thresholds. A compact `N excluded` suffix appears only when relevant. The tooltip explains the estimate and reservation.
- On narrow terminals, elide the model before crowding context or actions; metadata stays one row. Full identity remains accessible through F3.
- Hide verbose stamps, reasoning text, and copy buttons in Zen. Keep answers, tool-only outcome notices, cancellation, and errors understandable. Full details and copy controls remain available on exiting Zen.
- With attachments, show a short `N files` indicator in the metadata; F4 returns to the existing attachment controls. Never send attached content invisibly.
- Save failures, read-only state, context errors, and settings reconciliation are exceptions to the quiet resting view. Show a concise explanation and retain existing recovery buttons when action is required. Long notices may wrap; never truncate the only explanation of a blocked send.

### Interaction and implementation

- F4 enters Zen from any main tab and selects Chat. A second F4 restores the previous tab and its valid focus target. If entered from Chat, preserve its existing scroll position. Keep composer text, selection, attachments, session, and running worker intact.
- If the composer is disabled during generation, focus the transcript; preserve the existing end-of-turn focus behavior without forcing focus away from a reader.
- Do not toggle under a modal. Escape keeps its current cancellation/modal behavior; it does not exit Zen.
- Store a session-only `zen_mode: bool`, previous tab, and previous focused widget on `MlxTuiApp`. Apply a `zen` class on the base screen. Keep widgets mounted and use scoped CSS; no remount, second screen, replacement composer, config field, or session-schema change.
- Hide outer status, tab headers, Activity, footer, parameters, routine session controls, attachment toolbar, and existing context row with Zen-scoped rules. Disable F2 in Zen so it cannot invisibly alter Activity's restored state. Other existing shortcuts retain their behavior.
- Keep normal mode rendering unchanged. Scope all visual changes and extra metadata to Zen.

## Phase 1 — Toggle and continuity

### Overview

Add a reversible layout toggle over the existing widgets, preserving chat and navigation state.

### Changes

- In `src/mlx_tui/app/app.py`, add the F4 binding, session-only state, and `action_toggle_zen(self) -> None`, delegating to the existing UI helper module. Add outer Zen CSS and preserve the independent `compact` class.
- In `src/mlx_tui/app/ui.py`, add `action_toggle_zen(app: Any) -> None` and extend `check_action` to reject toggling under modals and Activity toggling in Zen. Save/restore the active tab and focus; focus only after layout, and only a mounted, visible, enabled widget.
- In `src/mlx_tui/chat_ui/pane.py`, add scoped layout rules and the composer hint. Keep recovery rows governed by their current state. Preserve scroll-follow intent across the layout change; do not force a reader to the bottom.
- In `app/ui.py:log_app`, forward warning/error messages to Textual's existing notification mechanism while Zen hides Activity, retaining literal text and the existing error deduplication. Normal events stay silent.

### Tests first

Add mounted integration tests in `tests/integration/test_zen_integration.py`, using the existing harness:

- `test_zen_toggle_preserves_chat_and_restores_view`: parameterize entry from each tab; check the same pane/composer instances, exact draft, attachments, previous tab, focus, and preserved collapse states after exit.
- `test_zen_toggle_respects_modal_and_stream`: F4 under a modal is inert; toggling during a controlled stream adds no request, cancels nothing, and Escape still cancels the existing attempt.

### Success criteria

- Automated: `rtk uv run pytest tests/integration/test_zen_integration.py tests/integration/test_chat_cancellation.py`.
- Manual: enter from Models and Chat, type a multiline draft, toggle twice, and verify draft/selection and normal layout survive. Scroll up during streaming and verify toggling does not pull the reader down.

### What we're NOT doing

No new chat lifecycle, persistence setting, slash-command parser, or changes to generation/cancellation semantics.

## Phase 2 — Refined layout and one compact information element

### Overview

Polish the centered conversation and consolidate useful information into a single muted row next to the composer.

### Changes

- In `src/mlx_tui/chat_ui/pane.py`, compose one Zen-only `Static` with ID `zen-info`, store `_zen_turn_stats: str`, and add `refresh_zen_info(self) -> None`. Render text literally, never as markup. Use the existing session flags and current model identity; give blocked states precedence over ordinary metadata.
- In `src/mlx_tui/chat_ui/context.py:update_ctx_bar`, update Zen context text from the same `ctx_len`, `max_ctx`, and `excluded` arguments as the ordinary bar. Store the compact context text on the pane for `refresh_zen_info`; avoid a second context calculation. Refresh the files indicator from `_refresh_attachment_ui`.
- In `src/mlx_tui/chat_ui/turns.py`, refresh the compact state at accepted submission, first answer/activity, terminal result, and cleanup. Clear previous-attempt statistics on accepted submission so failures cannot leave an old result looking current. Populate output count and total duration from `TurnResult`; retain `≈` for estimated counts and omit unavailable counts for incomplete streams.
- In `src/mlx_tui/chat_ui/pane.py:_render_session`, derive the latest attempt's compact statistics from raw `SessionTurn` fields, or clear them for an empty session. Treat restored completion counts conservatively as approximate because the saved record does not retain their estimation provenance. Temporary sessions use the live result path too.
- Put the small pure formatter `format_zen_stats(completion_tokens: int | None, total_s: float | None, *, estimated: bool) -> str` in `src/mlx_tui/chat_ui/pane.py`; use it for live and restored results. Omit missing fields instead of inventing zeroes.
- In `src/mlx_tui/chat_ui/persistence.py:_update_action_visibility`, refresh the compact blocked-state message alongside existing recovery controls. Keep the read-only reason visible even though the ordinary session toolbar is hidden.
- In `src/mlx_tui/app/polling.py:_render_status`, refresh the Zen model/state from existing polling results without extra network requests. Use `Unavailable`/`Unverified` where appropriate; do not equate endpoint health with a loaded model.
- Complete Zen-scoped styles in `chat_ui/pane.py`: 96-cell maximum width, restrained spacing, one-line metadata, compact composer, hidden detailed stamps/reasoning, and unchanged readable Markdown answers/notices. Set informative tooltips without adding visible panels.

### Tests first

- Unit: `tests/unit/test_zen.py::test_format_zen_stats`, parameterized for known, estimated, missing, zero-token, and missing-duration inputs.
- Mounted integration: `test_zen_info_tracks_latest_attempt_and_context` checks draft context, reserved output, exclusions, streaming state, completion, and subsequent failed attempt. Check temporary and restored-session paths without duplicating formatter assertions.
- Mounted integration: `test_zen_blockers_remain_actionable` checks read-only, save failure, reconciliation, and a warning logged before submission starts. Recovery controls remain reachable; a successful recovery clears the blocking presentation. Attached files remain indicated.

### Success criteria

- Automated: `rtk uv run pytest tests/unit/test_zen.py tests/integration/test_zen_integration.py tests/integration/test_context_integration.py tests/integration/test_sessions.py`.
- Manual: inspect an empty chat, long Markdown answer, thinking-only interval, tool-only result, attached draft, and saved session. Metadata remains visually secondary and occupies one row in ordinary states.

### What we're NOT doing

No memory/GPU dashboard, throughput chart, live timing loop, custom theme system, expandable metrics panel, or new dependency. No duplicate statistics on every response.

## Phase 3 — Interaction and layout verification

### Overview

Verify the complete keyboard journey and terminal geometry; document the toggle and compact information semantics.

### Changes

- In `tests/integration/test_zen_integration.py`, add parameterized `test_zen_layout_and_resize` at 80×24, 120×40, and 160×48, including resize in both directions. Assert the centered width limit, single metadata row, nonoverlapping composer/transcript, visible exit hint, and hidden controls absent from keyboard focus traversal.
- Add `test_zen_keyboard_journey` using the local HTTP stub: enter Zen, type, submit exactly once, receive output, open/close F3, and exit. Reuse existing cancellation and session suites for underlying lifecycle behavior.
- In `docs/usage.md`, add F4 to the keyboard table and a short Zen paragraph covering F4 exit, unchanged send/cancel keys, compact context estimation including output reservation, and access to advanced controls after exit.
- In `README.md`, mention Zen in the Chat feature bullet, add F4 to the quick-start keyboard summary, and link to the Zen explanation in `docs/usage.md`.
- Ship both documentation updates with the feature. Explain that Zen preserves the conversation and draft, restores the previous view on exit, and is not persisted across app launches. Document F2 being unavailable in Zen and the continued visibility of actionable warnings/recovery controls.
- Adjust only scoped styles or focus handling exposed by these checks; keep existing normal-mode layout tests unchanged.

### Tests first

Write the two mounted tests above before final layout adjustments. No new end-to-end framework or screenshot-golden infrastructure; the existing stub-backed journey is sufficient automated top-level coverage.

### Success criteria

- Automated:
  - `rtk uv run ruff check .`
  - `rtk uv run ruff format --check src tests`
  - `rtk uv run pyrefly check --min-severity warn`
  - `rtk uv run pytest -q`
- Manual: inspect dark and light themes at the three tested sizes; check keyboard-only recovery, code blocks, long model names, scrolling during output, and no clipped input or warning. Run one real MLX conversation to confirm appearance during genuine streaming; the HTTP stub cannot establish provider compatibility.
- Documentation: follow the README and usage-guide instructions against the implemented UI; verify the F4/send/cancel shortcuts, return behavior, metadata descriptions, and local documentation link. The feature is incomplete until both documents match the shipped behavior.

### What we're NOT doing

No general redesign of normal mode, additional themes, persistent Zen preference, or unrelated cleanup.

## Risks & Mitigations

- Hidden chrome can hide a blocking explanation → keep existing recovery rows and surface warning/error notifications; test disabled-composer states explicitly.
- Resizing or tab activation can reset focus/follow intent → retain mounted widgets and test a reader scrolled away from the end during streaming.
- Existing compact CSS can override Zen geometry → use explicit Zen/compact selectors and assert actual widget regions after resize.
- Compact numbers can imply false precision → preserve estimation markers, use the existing reservation calculation, and omit unavailable measurements.
- Long model names or notices can crowd the input → elide routine model text, allow actionable notices to wrap, and verify small-terminal geometry.
- Concurrent local changes can invalidate line references → reread affected functions before editing; do not reset or overwrite the working tree.

## Delivery and Rollback

The implementation, automated checks, and documentation for phases 1–3 are complete. Manual verification of dark and light themes at all three sizes and one real MLX conversation remains. No endpoint is listening at `127.0.0.1:8080`; the real-provider check was not run.
