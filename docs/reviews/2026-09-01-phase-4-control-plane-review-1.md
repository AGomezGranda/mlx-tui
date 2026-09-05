# Plan Review: Phase 4 — Finish the control plane

**Date:** 2026-09-01
**Target:** docs/plans/2026-09-01-phase-4-control-plane.md
**Review:** 1
**Verdict:** COMMENT

## Assessment

Well-scoped ponytail plan: three stdlib+native slices answering "does visual headroom change a load decision?" without new deps. Current-state citations are accurate and baseline is green (238 passed, ruff clean, pyrefly 0 errors, `ProgressBar(total, show_bar, show_percentage, show_eta)` exists on textual 8.2.8). Two alias/test-harness claims and a `ProgressBar` CSS selector are wrong as written and must be fixed before implementation; otherwise the plan leaves a working app green each phase.

## Cross-Cutting Themes

- **Textual widget composition fragility.** Phases 2 and 3 replace a `Static` status bar and add a `ProgressBar` under chat input, but both propose CSS selectors that miss the inner `Bar` widget (`src/mlx_tui/app/__init__.py:39` + `src/mlx_tui/chat_pane/__init__.py:41`). The amber/red tint and the `Horizontal` layout will silently not apply, degrading the spec's color contract to "bar still fills" — not a crash, but a functional miss.
- **Spec vs codebase naming drift.** `docs/part2.md:56` asks for `AppConfig.max_context` (default 8192); the plan keeps `max_ctx` canonical and adds `max_context` alias to avoid 7-site churn. Reasonable YAGNI, but the alias wins logic in `src/mlx_tui/config.py:55` as sketched conflicts with `_KEY_TYPES` and will fail the plan's own success criterion.
- **Test harness gap for the estimated path.** Phase 4's prefill logic correctly gates on `usage.prompt_tokens` (`src/mlx_tui/sse.py:22`), but `tests/conftest.py:24` has no stub mode that omits `usage` — every mode returns `usage` or errors — so the second branch of the planned integration test cannot be exercised without a new mode or mock.

## Findings

### Critical
- **[Correctness] Phase 1 / Phase 4 — config alias wins broken as written.** Plan says add `"max_context": int` to `_KEY_TYPES` (`src/mlx_tui/config.py:55`) and compute `raw_max = data.get("max_context") if type(data.get("max_context")) is int else data.get("max_ctx")` (`config.py:90`). As written the loop will populate `found["max_context"]` separately, and `raw_max` bypasses `found` + `_clamp_int`. Verified against current code: `uv run python -c "from mlx_tui.config import _from_mapping; print(_from_mapping({'max_context':16384,'max_ctx':4096}).max_ctx)"` → `4096` (max_context ignored). The success criterion `max_context` wins (`max_context=16384, max_ctx=4096 → 16384`) will fail. Fix: do not add to `_KEY_TYPES`; handle alias *after* the loop: `raw = data.get("max_context"); if type(raw) is int: mc_raw = raw elif type(data.get("max_ctx")) is int: mc_raw = data["max_ctx"] else: mc_raw = None; mc = _clamp_int(mc_raw, ...) if mc_raw is not None else None`.
- **[Test Coverage] Phase 4 — no stub mode for the no-usage / est path.** Plan's second integration case expects `tok_in_str` contains `(est)` → `prefill_tok_s is None` via a stub that omits `usage`. `tests/conftest.py:55` `StubHandler.do_POST` always sends `usage` for `ok/length_cap/empty/slow` and never for `truncated/error500/html` (which error). The builder `tests/builders.py:12` `sse_frames(usage=None)` can omit it, but no server mode does. Implementing the test as written will hang or pass vacuously. Fix: add a new `StubServer` mode (e.g. `"no_usage"`) that returns `sse_frames(deltas=["Hi"], usage=None)` or mock `token_accounting`/`stream_turn` in the test.

### Major
- **[Correctness] Phase 2 & 3 — ProgressBar tint selector misses inner Bar.** Plan proposes `#ctx-progress.ctx-bar-amber > .bar--bar { color: $warning; }` and `#memory-bar.ctx-bar-amber > .bar--bar` (`src/mlx_tui/app/__init__.py:39` + plan Phase 3 CSS). `ProgressBar` composes `Bar(id="bar")` which itself renders `&> .bar--bar` (`_progress_bar.py:45` `Bar.DEFAULT_CSS`). Styles on `ProgressBar` do not directly expose `.bar--bar`; correct selector is `#ctx-progress.ctx-bar-amber Bar > .bar--bar` (or `ProgressBar.ctx-bar-amber Bar > .bar--bar`) and similarly `Bar`'s complete/indeterminate states use `bar--complete`/`bar--indeterminate`. As written amber/red never tint — plan acknowledges fallback but spec `docs/part2.md:56` requires green→amber>80%→red>95%.
- **[Correctness] Phase 2 — status bar CSS selector `#status-bar Horizontal` never matches.** Plan adds `#status-bar Horizontal { width:1fr; height:3; }` (`src/mlx_tui/app/__init__.py:39`). After compose change `with Horizontal(id="status-bar"):` the element *is* the Horizontal; there is no inner Horizontal. Layout will rely on default Horizontal sizing, not the plan's width rule, and `width:100%` + `height:3` on `#status-bar` plus `#memory-bar {width:24;height:1}` may clip on narrow terminals (Bar's default `width:32` in `_progress_bar.py:32`). Fix: drop `#status-bar Horizontal`, style `#status-bar` directly (`dock: top; height: 1; layout: horizontal;`) and set `#memory-bar { width: 16; height: 1; }` with `#memory-label, #status-dot, #status-model, #status-port { width: auto; }`.
- **[Correctness] Phase 2 — missing imports in status_bar.py.** Plan's `render_status` (`src/mlx_tui/app/status_bar.py:16`) uses `except NoMatches:` and `ProgressBar` and `memory_snapshot` fallback, but lists only `Static` import currently. Verified `status_bar.py:7` imports only `Static` plus `TYPE_CHECKING` `MlxTuiApp`. Missing `from textual.css.query import NoMatches` and `from textual.widgets import ProgressBar, Static` will raise `NameError` at runtime before poll tick. Add imports.
- **[Correctness] Phase 1 — _MAX_CONTEXT_TOKENS_EST constant left at 8000.** Plan bumps `AppConfig.max_ctx` default `8000→8192` (`src/mlx_tui/config.py:26`) and `_get_max_ctx` fallback `8000→8192` (`src/mlx_tui/chat_pane/turn.py:25`), but `turn.py:22` ` _MAX_CONTEXT_TOKENS_EST = 8_000` remains 8000. `_get_max_ctx` returns that constant when `max_ctx` is missing or non-int-positive. Mocked configs or missing attr will still clamp to 8000, diverging from spec default 8192 and from `ctx_bar_text` label (`ctx 0/8k` initially). Fix: bump constant to `8192`.
- **[Correctness] Phase 4 — MetricsPane column spec inconsistent.** `src/mlx_tui/metrics_pane.py:56` currently 7 columns `time,model,tok/s,TTFT,ctx,prompt,out`. Plan snippet in Phase 4 first says `table.add_column("prefill", key="prefill") after TTFT (before ctx)` keeping `tok/s`, then later snippet shows `add_column("prefill") + add_column("decode") + add_column("TTFT")` (8 columns, rename `tok/s→decode`). Integration test expects `row_count==1` and `prefill` cell not `"—"` but column count mismatch will break `refresh_metrics` or test cell lookup. Decide one: keep `tok/s` as `decode` and add `prefill` (final 8 columns: time,model,prefill,decode,TTFT,ctx,prompt,out) and update `metrics_pane.py:66` `_populate_metrics_table` to emit both `f"{r.prefill_tok_s:.0f}" if not None else "—"` and `f"{r.tok_s:.1f}"`.
- **[Architecture] Phase 1 — TurnResult field ordering YAGNI.** Adding `prefill_tok_s` to `TurnResult` (`src/mlx_tui/chat.py:41`) in Phase 1 as `None` placeholder is never used until Phase 4 (`turn.py:95` computes). It violates ponytail "add when needed" and forces dataclass-order reasoning (default after non-default `tok_s`). Prefer deferring `TurnResult.prefill_tok_s` to Phase 4, or add at end `prefill_tok_s: float | None = None` after `skipped_frames` to avoid middle-insertion.
- **[Plan Mechanics] Automated verification equation off.** Plan Phase 1 success says `uv run pytest -q (expect 238 + 5 new ≈ 243)` but baseline `uv run ruff format --check` currently fails (`tests/integration/test_app_integration.py:402` would be reformatted, verified via `uv run ruff format --check src tests`). Whole-suite green will still hold but lint gate should be `uv run ruff check .` (currently passes) and format either fixed or excluded. Phase 4 success adds `uv run ruff format --check src tests` without noting pre-existing format delta — implementer will see false failure.

### Minor
- **[Correctness] Phase 2 — ProgressBar.update two calls.** Plan does `bar.update(total=...)` then `bar.update(progress=...)` (`src/mlx_tui/app/status_bar.py:16`). `ProgressBar.update` accepts both at once (`_progress_bar.py:update`). Two calls work but reset ETA twice; single call `bar.update(total=..., progress=...)` is cleaner and avoids intermediate 0% flash.
- **[Correctness] Phase 3 — duplicate ctx_bar_style call.** `update_ctx_bar` (`src/mlx_tui/chat_pane/__init__.py:122`) calls `ctx_bar_style` once for bar and again for label; deduplicate.
- **[Plan Mechanics] Phase 1 test filter typo.** Success criterion `uv run pytest -v tests/unit/test_history.py -k "prefill or max_turns or nine"` includes `max_turns` (unrelated to this phase) and misses `max_ctx`/`max_context`. Should be `-k "prefill or nine_fields or max_ctx"`.
- **[Plan Mechanics] Phase 2 success widget import check misleading.** `uv run python -c "from textual.widgets import ProgressBar; print(ProgressBar(total=16))"` will print `ProgressBar()` but not assert height/width; fine but not strong signal.
- **[Architecture] Phase 4 stamp format drift.** `docs/part2.md:57` stamp spec is `TTFT 0.8s · prefill 84 tok/s · decode 14.8 tok/s` (TTFT first). Plan Phase 4 stamp puts prefill/decode before TTFT (`f"{tok_in} in · {tok_out} out · {prefill} prefill tok/s · {decode} decode tok/s · TTFT {ttft:.2f}s"`). Either is readable but drift from spec may confuse docs/manual verification.

### Suggestions
- **[Style] Keep history stdlib-only invariant explicit.** Plan already notes `history/tokens.py` stays pure and `history/store.py` stdlib+deque — good; add `pyrefly` check that neither imports `textual`/`rich` (already implicit via `src` strict preset).
- **[Style] Consider single helper for ctx threshold.** Reuse `ctx_bar_style` for context bar only; memory bar stays uncolored as spec says — plan's decision to reserve `memory-bar.ctx-bar-amber` CSS unused is fine YAGNI.
- **[Docs] Note config file migration in README.** New default 8192 only affects missing key; existing `config.toml` with `max_ctx=8000` keeps working — plan's mitigation is correct; add one line to `README` or `CONFIG_TEMPLATE` comment that both keys work and `max_context` wins.

## Strengths
- Verified baseline: `uv run pytest -q` 238 passed, `ruff check` clean, `pyrefly` 0 errors, and `ProgressBar(total, show_bar, show_percentage, show_eta)` signature match — gives high confidence the plan starts from reality.
- Each phase leaves app green and UI working (domain→memory→context→prefill) with independent revert (Phase 2 Horizontal can revert to unicode without touching Phase 1).
- Keeps `format_status_line` pure for unit tests (`tests/unit/test_status.py:77`) while `render_status` becomes widget composition — preserves existing test pin.
- Correctly delegates prefill compute to `turn.py` (has `ttft` + `tok_in_str`) instead of expanding `token_accounting` (`src/mlx_tui/sse.py:62`) — smaller diff, `sse.py` stays untouched.
- No new deps, no auto-detect, no KV-GB math until bars prove read — faithful to `docs/part2.md:59` kill line.

## Recommended Changes
1. Fix config alias wins (Critical #1): rewrite `_from_mapping` alias handling after loop, do not add `max_context` to `_KEY_TYPES`, add 3 config tests (default 8192, alias wins, bool rejected + clamp).
2. Add stub `no_usage` mode (Critical #2): `tests/conftest.py:55` new branch `if mode=="no_usage": body=sse_frames(deltas=["Hi"], usage=None)`; update Phase 4 integration test to use it for the est branch.
3. Fix ProgressBar tint selector (Major #1): change CSS to `ProgressBar.ctx-bar-amber Bar > .bar--bar { color: $warning; }` etc., verify against `_progress_bar.py:32` Bar structure; add manual check note for `> .bar--complete`.
4. Fix status bar CSS and imports (Major #2+3): replace `#status-bar Horizontal` with direct `#status-bar` styling, set `#memory-bar` width consistent with Bar (16–24), and add `NoMatches` + `ProgressBar` imports to `status_bar.py:7`.
5. Bump `_MAX_CONTEXT_TOKENS_EST` to 8192 (Major #4) alongside `AppConfig.max_ctx` and `_get_max_ctx` fallback.
6. Disambiguate MetricsPane columns (Major #5): choose final 8-column order and update `metrics_pane.py:56`/`metrics_pane.py:66` and Phase 4 test cell key accordingly.
7. Defer `TurnResult.prefill_tok_s` to Phase 4 or place at end (Major #6).
8. Run `uv run ruff format` on `tests/integration/test_app_integration.py` (already flagged) before gating on `format --check`.

## Re-Review (Pass 2)
**Date:** 2026-09-01
**Verdict:** COMMENT

### Previous Findings
- **[Correctness] Phase 1 / Phase 4 — config alias wins broken as written — Resolved.** Plan now keeps `_KEY_TYPES` unchanged and handles alias after loop with `type(...) is int` + `_clamp_int` (max_context wins, bool rejected).
- **[Test Coverage] Phase 4 — no stub mode for no-usage / est path — Resolved.** Added `tests/conftest.py` `no_usage` mode (`sse_frames(usage=None)`) and updated Phase 4 test + manual verification to use it.
- **[Correctness] Phase 2 & 3 — ProgressBar tint selector misses inner Bar — Resolved.** CSS fixed to `#ctx-progress.ctx-bar-amber Bar > .bar--bar` (and red) matching `_progress_bar.py:45` `Bar > .bar--bar`.
- **[Correctness] Phase 2 — status bar CSS selector `#status-bar Horizontal` never matches — Resolved.** Removed spurious selector; `#status-bar` now `dock: top; height: 1; layout: horizontal;` + `#memory-bar { width: 16; }`.
- **[Correctness] Phase 2 — missing imports in status_bar.py — Resolved.** Now lists `NoMatches` and `ProgressBar, Static`.
- **[Correctness] Phase 1 — _MAX_CONTEXT_TOKENS_EST constant left at 8000 — Resolved.** Bumped to `8192` in `turn.py:22` alongside `AppConfig` and `_get_max_ctx`.
- **[Correctness] Phase 4 — MetricsPane column spec inconsistent — Resolved.** Fixed to 8 columns `time, model, prefill, decode, TTFT, ctx, prompt, out` in both `on_mount` and `refresh_metrics`.
- **[Architecture] Phase 1 — TurnResult field ordering YAGNI — Resolved.** Deferred to Phase 4 at end after `skipped_frames`.
- **[Plan Mechanics] Automated verification equation off — Partially resolved.** Test filter typo fixed (`prefill or nine`); `ruff format --check` gate still in Phase 4 — pre-existing format delta should be fixed beforehand (`uv run ruff format src tests`) before gating.
- **[Correctness] Phase 2 — ProgressBar.update two calls — Resolved.** Single `bar.update(total=..., progress=...)`.
- **[Correctness] Phase 3 — duplicate ctx_bar_style call — Resolved.** Style computed once.
- **[Plan Mechanics] Phase 1 test filter typo — Resolved.**
- **[Architecture] Phase 4 stamp format drift — Still present (Suggestions).** Plan keeps `prefill · decode · TTFT` vs spec `TTFT · prefill · decode`; intentional drift noted, not blocking.

### New Issues Introduced
- None. Edits were surgical (alias logic, CSS selectors, imports, constant bump, column order) — no new placeholders, no broad rewrites, types remain consistent.

### Verdict Rationale
No Critical remain; no Major remain. Only Minor/Suggestions (stamp order, docs note) left → **COMMENT** per rules (only Minor/Suggestions). Plan is implementation-ready; you can mark `Status: Draft → Approved` when ready.
