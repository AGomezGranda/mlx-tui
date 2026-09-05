# Plan Review: Top Bar and Chat Transcript Polish (TA + CA)

**Date:** 2026-09-01
**Target:** docs/plans/2026-09-01-top-bar-and-chat-transcript-polish.md
**Review:** 1
**Verdict:** APPROVE

## Assessment

Tight, low-risk polish plan: pure CSS for the docked bar and `Text`/`Markdown`-only grouping inside `RichLog` — no new widgets, deps, or compose changes. Current-state citations at `src/mlx_tui/app/__init__.py:40-59`, `src/mlx_tui/chat_pane/__init__.py:92`, `src/mlx_tui/chat_pane/turn.py:184-194` verify clean against `HEAD` (247 passed, ruff clean, pyrefly 0 errors, `textual>=8.2.8`). Two geometry assumptions about `height:1 + border-bottom` and `dock:top + margin` and one missed write path for the cancelled line will make Phase 1 or Phase 2 silently not match their own manual success criteria as written.

## Cross-Cutting Themes

- **Textual dock/box-model geometry is unspiked.** Both Phase 1 deltas (`background`/`padding`/`border-bottom` on a `height:1` docked `Horizontal`, plus `TabbedContent{margin-top:1}` vs `#status-bar{margin-bottom:1}`) assume CSS that `dock:top` may ignore or that consumes the only row. The metrics precedent (`#metrics-sparkline` `src/mlx_tui/app/__init__.py:70-72` `height:3` + `border-bottom`) and `#app-log` `src/mlx_tui/app/__init__.py:59-63` `height:6` + `border-top` show borders live inside `height` — with `height:1` a `border-bottom` leaves 0 rows for content.
- **Transcript grouping leaks across write paths.** `complete_turn_ui` `src/mlx_tui/chat_pane/turn.py:184-195` is not the only transcript writer — `write_system_line` `turn.py:223-224` / `record_cancelled` `turn.py:198-220` and `_on_input_submitted` `chat_pane/__init__.py:92` are separate owners. Plan prefixes only two of three, so the "▎ cancelled — request aborted" manual check cannot pass without touching the third path.
- **Regression-only automation.** Both phases gate on `uv run pytest -q` staying green, not on the new visuals existing. A refactor that drops the `▎`/`─` or the `background` would still be green.

## Findings

### Critical

- **[Correctness] Phase 1: `height:1` + `border-bottom: solid $primary` leaves 0 content rows.** `src/mlx_tui/app/__init__.py:41-46` currently `height:1` with no border. Textual's box model (visible in `#app-log` height 6 + border-top and `#metrics-sparkline` height 3 + border-bottom) draws borders inside `height`. Adding `border-bottom` to a 1-row widget consumes the row, truncating `Static#status-dot|#status-model|#memory-bar|#memory-label|#status-port`. Implemented as written the top bar will render as a bare primary line or clip text on 80×24, violating Phase 1 Manual "Bar stays 1 row tall; no clipping". Fix: either keep `height:1` and use only `background:$surface` + `padding` (defer border), or bump to `height:2`/`height:3` if border is required, or use `border-bottom: heavy $primary` on a height 2 bar and spike visually before committing. At minimum the plan must state the chosen height and note the border-inside-height trade-off.
- **[Correctness] Phase 1: gap via `margin-top`/`margin-bottom` with `dock:top` is likely a no-op.** `src/mlx_tui/app/__init__.py:42` `dock:top` removes `#status-bar` from normal flow; `#status-bar`'s `margin-bottom` and `TabbedContent`'s `margin-top` are routinely ignored for docked siblings. Plan lists "try A then fallback B, pick the one that visibly creates 1-row gap" leaving the success criterion non-deterministic. If neither works, Manual "1-row gap above the Models|Chat|Metrics tab row" never appears and no automated check catches it. Fix: spike one Textual run and lock the plan to the working mechanism — `TabbedContent{padding-top:1}` or a 1-row `Static` spacer or `Horizontal#status-bar{height:2}` — and name it explicitly instead of "or … verify during impl".

### Major

- **[Correctness] Phase 2: cancelled grouping writes through an unmodified path.** Plan changes `chat_pane/__init__.py:92` (prompt `▎ you ›`) and `turn.py:184-196` (`▎` stamp + `─` separator) but leaves `turn.py:223-224` `write_system_line` and `turn.py:218-219` `pane._write_system_line("cancelled — request aborted", "dim")` untouched. Manual Verification promises "`esc` cancelled turn also shows `▎ cancelled — request aborted` grouping" — that line will still be `Text("cancelled — request aborted", style="dim")` with no `▎`, so the check fails. Suggested fix: add `Text(f"▎ {message}", style=style)` in `write_system_line` (or a narrow wrapper for the cancelled path) and decide whether `server unreachable`/`server error` red lines should also be prefixed (proposed: leave red lines unprefixed, only prefix dim cancelled).
- **[Test Coverage] Phase 2: automation verifies no regressions, not new behavior.** Automated Verification lists `uv run pytest -v -k ...` and `uv run pytest -q` expecting 247 passed. Nothing asserts `▎ you ›`, `▎` stamp, `─` separator, or `#status-bar` CSS actually present. A later edit that removes the polish would still pass. Add minimal deterministic checks: (a) `assert "▎ you ›" in harness.log_lines()` after one turn, (b) `assert any("─" * 10 in t for t in lines)` for separator, (c) `assert "background: $surface" in MlxTuiApp.DEFAULT_CSS and "border-bottom" in ...` or a CSS-string snapshot. Without this the plan's own visual goals are manual-only and easy to regress.
- **[Plan Mechanics] Phase 2: separator order removes historical blank, behavior ambiguous for empty/capped turns.** `turn.py:192-193` currently does `log.write(Markdown(full_text)); log.write("")` (blank after assistant). Plan's snippet does `Markdown` → `notices` → `Text("─"*40)` → `Text("")`, dropping the immediate post-Markdown blank. For `harness.server.mode="empty"` (`tests/conftest.py:123-125`) `full_text==""` and `notices=["model returned no text"]`, the transcript becomes `▎ stamp` → `▎ model returned no text` → `─` rule → blank (no Markdown blank was expected anyway — okay), but for a normal turn the vertical rhythm changes from `Markdown` + blank to `Markdown` + notices + rule + blank. Tests like `tests/integration/test_app_integration.py:306-345` `test_chat_renders_markdown` (checks `not any(line.strip()=="# hi")`, `len(log.lines)>=2`) still pass, but the visual spec is ambiguous whether `─` should follow notices or follow Markdown when notices absent. Make the order explicit and preserve or intentionally replace the post-Markdown blank with a comment.
- **[Architecture & Patterns] Phase 1: `width:1fr` split of `#status-model` vs combined selector not specified.** `src/mlx_tui/app/__init__.py:56-58` groups `#status-dot, #status-model, #status-port { width:auto }`. Plan recommends `#status-model{width:1fr}` leaving dot/port auto, but does not give the exact two-rule replacement or its order, nor that `width:1fr` on a `Static` inside `Horizontal(layout:horizontal)` is the flex idiom (verified: it is, but order matters for CSS specificity). Leaving it as "change … OR keep …" invites a diff that leaves the old combined rule overriding the new one. Fix: spell the end-state CSS block verbatim.

### Minor

- **[Correctness] Phase 1: `$surface` contrast may be invisible on some Textual themes.** Plan cites `$surface/$panel/$boost` as available; `$surface` on default dark theme is often only 1 step off `$background`. The "muted surface background" manual check may be imperceptible. Verify on both light/dark or prefer `$panel` (slightly stronger) and keep `$surface` as fallback note. Location: `src/mlx_tui/app/__init__.py:40-115` `DEFAULT_CSS` edit.
- **[Correctness] Phase 2: `▎` (U+258E) glyph coverage.** Plan notes fallback `│` if `▎` renders blank, but code hard-codes `▎`. On some terminal fonts `▎` is missing (shows tofu/space). Grouping then degrades to bold + dim rule only — still acceptable per mitigation, but the plan could extract `_TURN_PREFIX="▎ "` as a constant to make the font swap one-line. Location: `chat_pane/__init__.py:92`, `turn.py:89,94,96`.
- **[Test Coverage] Phase 2: `startswith` → `in` loosens `test_cancel_closes_stream`.** `tests/integration/test_app_integration.py:172` `any(t.startswith("you ›")` → `any("you ›" in t ...)`. If an assistant Markdown happens to contain `you ›` the predicate could fire before the prompt line, making the test flaky (low probability but real). Prefer `any(t.lstrip("▎ ").startswith("you ›") for t in lines)` or `any(t.lstrip().endswith("you › hi") ...)` to preserve anchoring while allowing prefix. Same note for `_STAMP_RE` — `(?:▎ )?` literal is correct but `re` must handle the Unicode char; no escaping needed, confirm file is UTF-8 (it is).
- **[Plan Mechanics] Phase 1 & 2 lint/format/type checks are listed but not scoped to the app run.** `uv run pyrefly check` is `INFO 0 errors (84 suppressed)` on HEAD — good. After CSS/Text edits no new `Text`/`Markdown` imports are needed (`turn.py:11-13` already imports both), so type gate will stay green. Mention explicitly that no new imports/types are introduced, to avoid a future `from rich.text import Text` duplicate in `chat_pane/__init__.py` (already present at line 8).
- **[Performance/Data] No issues** — text writes are O(1) per turn, no hot path.

### Suggestions

- **[Architecture]** Extract ` _PREFIX = "▎ "` and `_SEPARATOR = "─" * 40` in `chat_pane/turn.py` (or `chat_pane/__init__.py`) so tests and the three write sites share the glyph and Manual fallback (`│`) is one edit.
- **[Test Coverage]** Keep the 247-pass expectation but add a non-brittle CSS snapshot: `assert "#status-bar" in MlxTuiApp.DEFAULT_CSS and "background: $surface" in MlxTuiApp.DEFAULT_CSS` to prevent visual regression without rendering.
- **[Plan Mechanics]** Collapse Phase 1 alternative prose into a single deterministic end-state CSS block (include exact lines for `#status-bar`, `TabbedContent`, `#status-model`) and delete the "or if dock ignores margin … pick one" — spike once, write the winner.
- **[UX]** If `Text("─"*40, style="dim")` wraps on <60 cols, the dim rule appears as two lines (plan's Risk #4). Consider `Text("─" * min(40, pane.size.width - 4), style="dim")` only if wrapping observed; keep 40 as default and note the dynamic option as comment rather than code.

## Strengths

- Keeps `compose()` `src/mlx_tui/app/__init__.py:151-159` untouched — zero layout churn, minimal risk to `AppHarness` `tests/conftest.py:179` and `StubServer` modes.
- Preserves substring contracts `you ›` and `tok/s` so `harness.log_lines()` `tests/conftest.py:182` and `test_status_green_and_chat_stamp_over_stub_http` `tests/integration/test_app_integration.py:15,31-43` stay green with only two narrow test relaxations (`_STAMP_RE` optional prefix, `startswith`→`in`).
- Correctly reuses `Text`/`Markdown` inside `RichLog` `src/mlx_tui/chat_pane/__init__.py:68` — avoids the `RichLog→VerticalScroll` rewrite and streaming pattern `turn.py:84` `call_from_thread(_update_stream)` breakage.
- Risks & Mitigations section is thorough (dock margin, glyph, regex, wrapping, pyrefly) and Out of Scope is explicit (log pane, deps, history).

## Recommended Changes

1. **Fix Critical gap/border geometry first** — spike Textual on 80×24: prove whether `height:1` + `border-bottom` is viable; lock plan to either `height:1` no border or `height:2` with border, and replace the margin alternatives with the single working gap mechanism (`padding-top:1` on `TabbedContent` is the usual dock-safe choice). Update `src/mlx_tui/app/__init__.py:40-58` end-state CSS verbatim.
2. **Prefix the cancelled path** — edit `turn.py:223-224` `write_system_line` to `Text(f"▎ {message}", style=style)` for dim/cancelled (or introduce `write_cancelled_line`), or amend Manual Verification to not require `▎` on cancelled. Map to Finding M2.
3. **Add two automated visual assertions** — extend Phase 2 Automated Verification: check `▎ you ›` + `─` in `harness.log_lines()` after a stub turn, and check `MlxTuiApp.DEFAULT_CSS` contains `background: $surface` / `border-bottom`. Map to Finding M3.
4. **Spell exact CSS end-state** — replace the "Change `#memory-label … OR keep …`" paragraph with the final 3-rule block so implementer cannot leave the combined selector overriding `1fr`. Map to Finding M4.
5. **Tighten test relaxation** — use `lstrip("▎ ")` anchoring instead of bare `in` for `test_cancel_closes_stream` `tests/integration/test_app_integration.py:172`. Map to Minor M3.
6. **Extract prefix/separator constants** — optional but reduces future font-swap churn.

## Re-Review (Pass 2)
**Date:** 2026-09-01
**Verdict:** APPROVE
**Changes Applied:** All 6 Recommended Changes

### Previous Findings
- **[Correctness] Critical: `height:1` + `border-bottom` leaves 0 content rows** — Resolved. Plan now keeps `height:1` no border, notes border requires `height:2`, cites `#app-log`/`#metrics-sparkline` precedent `src/mlx_tui/app/__init__.py:59-63,70-72`, and gives exact end-state CSS block.
- **[Correctness] Critical: gap via `margin-top`/`margin-bottom` with `dock:top` is likely a no-op** — Resolved. Replaced ambiguous margin alternatives with deterministic `TabbedContent { padding-top: 1; }` (dock-safe) and removed "pick one during impl".
- **[Correctness] Major: cancelled grouping writes through unmodified path** — Resolved. Added `write_system_line` edit at `turn.py:223-224` (dim prefix `▎`, red unprefixed) covering `record_cancelled` `turn.py:218-219`; manual check now achievable.
- **[Test Coverage] Major: automation verifies no regressions, not new behavior** — Resolved. Phase 1 now has CSS snapshot `python3 -c '... assert "background: $surface"...'`; Phase 2 now has `lstrip("▎ ").startswith("you ›")` + `▎`/`─` assertions via `harness.log_lines()`.
- **[Plan Mechanics] Major: separator order ambiguous** — Resolved. Notes now state separator replaces old `turn.py:193` blank, documents `empty`/`length_cap` behavior, adds `max(20, pane.size.width-4)` fallback comment and ` _PREFIX`/`_SEPARATOR` constants guidance.
- **[Architecture & Patterns] Major: `width:1fr` split not specified** — Resolved. Exact 6-rule CSS block with `#status-dot, #status-port {width:auto}` / `#status-model {width:1fr}` now verbatim, no OR.
- **[Correctness] Minor: `$surface` contrast** — Resolved. Decision row and Phase 1 notes now mention fallback `$panel` and spike on light/dark.
- **[Correctness] Minor: `▎` glyph coverage** — Resolved. Extract `_PREFIX`/`_SEPARATOR` constants, note `│` fallback.
- **[Test Coverage] Minor: `startswith` → `in` loosens test** — Resolved. Now `lstrip("▎ ").startswith("you ›")`.
- **[Plan Mechanics] Minor: lint/format/type not scoped** — Resolved via "no new imports" note.
- **[Suggestions] Architecture/Test/Mechanics/UX** — Adopted: constants, CSS snapshot, deterministic block, dynamic separator comment all merged.

### New Issues Introduced
- None. All edits are narrow, preserve `compose()` `src/mlx_tui/app/__init__.py:151-159` untouched and substring contracts `you ›`/`tok/s` (`tests/conftest.py:182`, `tests/integration/test_app_integration.py:15`). No new deps or widget changes.

### Verdict Rationale
Criticals and Majors eliminated; remaining suggestions are optional and non-blocking. Plan is deterministic, spiked, and automates its own visuals. Ready for implementation — you may mark `Status: Draft` → `Approved` (that transition is yours).

