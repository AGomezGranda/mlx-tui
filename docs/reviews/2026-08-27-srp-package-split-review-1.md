# Plan Review: SRP Package Split — Large Modules into Focused Packages

**Date:** 2026-08-27
**Target:** docs/plans/2026-08-27-srp-package-split.md
**Review:** 1
**Verdict:** REVISE

## Assessment
The plan is grounded in accurate codebase facts (verified `wc -l` 485/354/306/300/299, `ruff`/`pyrefly` clean, 231 tests) and correctly preserves Textual conventions (`tui` cast, `call_from_thread`, `NoMatches`, `BootPlan`). However three correctness gaps would make the plan fail as written: `@work` on free functions breaks the worker model across three phases, Phase 1 gates reference a not-yet-existent `history.sparkline` path, and the `find_server_pid` monkeypatch seam is not updated. Inconsistent sequencing for `_SwapShim` and the history facade placeholder also leave the plan not executable without clarification.

**Lenses applied (5/6):** Core — Correctness, Architecture & Patterns, Test Coverage, Plan Mechanics — plus API/Compatibility (breaking import rewrites, no shims). Skipped: Security (no auth/secrets/change), Performance (no hot-path change), Data/Migrations (no schema).

## Cross-Cutting Themes
- **Textual `@work` misuse is systemic.** Phases 3, 4 and 5 all propose `@work` on free functions (`chat_pane/turn.py:168`, `models_pane/table_ops.py:44`, `models_pane/swap.py:130/159`, `search_screen/query.py:115`, `search_screen/download.py:215`). `@work` only works as a descriptor on `Widget`/`App` methods; free-function decoration silently fails (no worker group, `cancel_group` and `exclusive` lost). The plan notes the need for a thin facade in Phase 3 but not in Phases 4–5, leaving the same bug repeated.
- **Import / monkeypatch seams are fragile.** Splits move `from mlx_tui.process import find_server_pid` bindings into `app/state.py` and `app/polling.py` via `from`-imports, but the test harness patches `mlx_tui.app.find_server_pid` and `mlx_tui.process.find_server_pid` (`tests/integration/test_swap_integration.py:35`, `conftest.py` harness). After the move, patching the old target no longer affects the new binding. The same `from`-import issue applies to `scan_models`. The plan updates only the `scan_models` target, missing the `find_server_pid` targets, and omits several `history` consumers (`sse.py`, `metrics_pane.py`).
- **Phase sequencing and gates are not green after each phase as claimed.** Phase 1's gate requires `from mlx_tui.history.sparkline import _shade_for_ctx` before `history/` exists (Phase 5), and Phase 5's history facade is left as “keep ~20 lines **or** delete it” — not a decision. Gates that must stay green per-phase cannot if the gate itself is impossible.

## Findings

### Critical
- **[Correctness] Phase 3 (`chat_pane/turn.py`) + Phase 4 (`models_pane/table_ops.py`, `models_pane/swap.py`) + Phase 5 (`search_screen/query.py`, `search_screen/download.py`): `@work` on free functions is invalid.** Plan shows:
  ```python
  @work(exclusive=True, group="chat", thread=True)
  def run_turn(pane: "ChatPane", ...)  # chat_pane/turn.py
  @work(exclusive=True, group="rescan", thread=True)
  def _rescan(pane):  # models_pane/table_ops.py:44
  @work(exclusive=True, group="hf-search", thread=True)
  def run_search(screen, query)  # search_screen/query.py:115
  ```
  Textual 8.2.8's `@work` is a method decorator that captures `self` (the widget) and registers with `self.workers`. Decorating a free function loses `exclusive`/`group` scoping, `workers.cancel_group(self, "chat")`/`abort()` no longer targets the right group, and `thread=True` hopping via `self.tui.call_from_thread` breaks. Implemented as written, chat streaming, rescan, swap and search would not run as workers or would raise `AttributeError`. Fix: keep a thin `@work`-decorated method stub on the pane class (`ChatPane._run_turn`, `ModelsPane._rescan`/`run_warm_swap`/`run_boot`, `SearchScreen._run_search`/`_fetch_size`/`_run_download`) that delegates to a plain `*_impl(pane, ...)` in the submodule. The plan already hints at this for `turn` but abandons it for the other modules.

- **[Correctness] Phase 1 – Automated Verification hallucinated `history.sparkline` path.** Success criterion:
  > `Whole suite green (after repointing tests/unit/test_history.py:402 to from mlx_tui.history.sparkline import _shade_for_ctx): uv run pytest -q`
  Verified: `tests/unit/test_history.py:402` currently `from mlx_tui.app import _shade_for_ctx`; `src/mlx_tui/history.py:254` defines `_shade_for_ctx`; `src/mlx_tui/history/sparkline.py` does not exist until Phase 5. Running that import in Phase 1 fails with `ModuleNotFoundError`, so Phase 1 can never be green as gated. The repoint must be deferred to Phase 5. Phase 1 should keep `from mlx_tui.history import _shade_for_ctx` and only assert the app shim is gone.

- **[Correctness] Phase 1/2 – `find_server_pid` monkeypatch seam breaks.** `tests/integration/test_swap_integration.py:33` patches `mlx_tui.app.find_server_pid` and `mlx_tui.process.find_server_pid` via `monkeypatch.setattr`. After Phase 1, `effective_model` moves to `src/mlx_tui/app/state.py` with `from mlx_tui.process import find_server_pid`, and `poll_tick` moves to `src/mlx_tui/app/polling.py` with the same `from`-import. Patching `mlx_tui.process.find_server_pid` does not update already-bound names in `app.state`/`app.polling`; patching `mlx_tui.app.find_server_pid` (which will no longer exist in `app/__init__.py` after delegation) is also stale. The plan only mentions updating `scan_models` to `mlx_tui.models_pane.table_ops.scan_models` and misses this seam, so swap-integration tests will silently use real `psutil` pid scanning and either flake or miss warm-swap branches. Fix: either import as `import mlx_tui.process as _process` and call `process.find_server_pid` via module, or update patches to `mlx_tui.app.state.find_server_pid`, `mlx_tui.app.polling.find_server_pid`, and `mlx_tui.process.find_server_pid`.

### Major
- **[Architecture & Plan Mechanics] Phase 1 vs Phase 2 contradiction for `_SwapShim` location.** Phase 1 says “Edit `src/mlx_tui/app/__init__.py` — trim to facade (~180 lines): **keep `_SwapShim`** … Delete `__all__`” (`src/mlx_tui/app.py:48`). Phase 2 then says “Keep `_SwapShim` in `app/state.py` (already moved in Phase 1)”. The two statements conflict; an implementer cannot tell whether Phase 1 should move the class or not. This also leaves the `swap_machine = _SwapShim()` import unresolved (`from .state import _SwapShim` vs local definition). Decide one place (recommend `app/state.py`) and make Phase 1 explicitly move it with a re-export if needed, so `app/__init__.py` does `from .state import _SwapShim` for type completeness.

- **[Plan Mechanics] Phase 5 history package facade is a placeholder, not a plan.** Phase 5 states “keeping `history/__init__.py` minimal (~20 lines) **or** delete it … Preferred ponytail: delete facade”. The Design Decisions table says `store`, `sparkline`, `tokens` are canonical, but Implementation Phases still offers two alternatives. Combined with the instruction to `git mv src/mlx_tui/history.py src/mlx_tui/history/__init__.py then split`, the implementer must guess whether to keep a re-export shim for one phase. This is not executable as a 2–15 minute action. Pick one (recommend: Phase 5 deletes the facade and updates all consumers in the same commit) and make the `__init__.py` empty or absent explicitly.

- **[Correctness] Phase 5 import sweep is incomplete.** Plan lists updates for `app/polling.py`, `chat_pane/turn.py`, `metrics_pane.py` but misses:
  - `src/mlx_tui/sse.py:7` `from mlx_tui.history import CHARS_PER_TOKEN_EST` → must move to `history.tokens`
  - `src/mlx_tui/app.py:30` imports `HistoryStore, MemoryRecord, MemoryStore, _shade_for_ctx` (several split across files)
  - `src/mlx_tui/chat_pane.py:20` imports `TurnRecord, _tok_int, estimate_tokens, trim_for_context` (split across `store`/`tokens`)
  - `src/mlx_tui/metrics_pane.py:14` imports four symbols across two new submodules
  After the split, any missed import remains `from mlx_tui.history import …` which will fail if `history/__init__.py` is deleted, or will silently hide the split if a shim is kept, contradicting the “no shims” decision. Enumerate every `grep -rn "from mlx_tui.history" src` hit in Phase 5 and update `sse.py` (`CHARS_PER_TOKEN_EST` → `history.tokens`).

- **[Plan Mechanics] Hatch wheel discovery not verified — may break distribution.** `pyproject.toml:14` `packages = ["src/mlx_tui"]` is cited as auto-discovering `app/`, `chat_pane/` etc., but hatchling's `packages` with a single entry does not automatically include subpackages unless `tool.hatch.build.targets.wheel.packages` lists them or `find_packages`-like discovery is configured. Verified: `uv run python -m hatch build` is not gated in the plan. If the wheel omits `mlx_tui.app.polling` etc., installs break while tests still pass. Add an explicit verification command per phase (`uv run python -c "import mlx_tui.app.polling"` is already in Phase 1 but wheel inclusion is not checked) and note to add `mlx_tui.app`, `mlx_tui.history.store` etc. or switch to hatch `tool.hatch.build.force-include`.

- **[Plan Mechanics] `ruff format --check` allowlist is wrong.** Phase 5 gates `uv run ruff format --check src tests (allow pre-existing tests/integration/test_swap_integration.py hunks if unchanged)`. Measured baseline `uv run ruff format --check src tests` actually fails on `src/mlx_tui/search.py:92` (long `self._mlx_desc` line), not on `test_swap_integration.py`. The allowlist cites the wrong file, so the gate will fail for an unrelated pre-existing issue unless the exemption is corrected or the file is formatted.

- **[Plan Mechanics] Size and line-count bookkeeping is inconsistent.** Overview says each file ≤250; Phase 1 says `app/__init__.py` ≤250 then ≤200 in Phase 2; Phase 3 says `turn.py` ~170 but Phase 3 Success Criteria says each ≤170 and `chat_pane/__init__.py` ≤150. The clamp list `chat_pane.py:103` is ~27 lines but the estimate ~95 for `params.py` is loose. The final sweep `awk '$1>250'` contradicts the tighter per-phase limits (≤150). This is not blocking but makes “leaves each file ≤250” unverifiable during early phases. Normalize ceilings per phase or keep the global 250 and note the tighter aspiration.

- **[Test Coverage] Automated criteria undersample behavior; failure modes lack coverage.** All phases gate on “whole suite green” plus a one-line import smoke test, but no phase verifies the actual moved behavior (e.g., `test_shade_for_ctx` after repoint, `test_chat_cancel_mid_stream` after `turn.abort` move, `test_warm_swap` after `swap` move). The manual checks (check harness, `StubServer.requests[-1]`) are not automated, so a silent wiring error (e.g., params not syncing to `AppConfig`, `swap_busy` not disabling `ModelsTable`) would pass the gate. Add targeted automated checks: after Phase 3, assert `ChatPane._parse_params` clamp still works via a small `pytest` snippet; after Phase 4, assert `ModelsPane.request_load_swap` still respects `swap_busy`.

- **[Architecture] `models_pane/swap.py` name shadows top-level `mlx_tui.swap`.** Creating `src/mlx_tui/models_pane/swap.py` alongside `src/mlx_tui/swap.py` (`BootPlan`, `SwapState`, `health_timeout` at `swap.py:17`) risks `import mlx_tui.swap` vs `from mlx_tui.models_pane.swap import run_boot` confusion and ambiguous `swap.health_timeout` references. Python handles it, but `from mlx_tui.swap import BootPlan` inside `models_pane/swap.py` will import the top-level module, not itself, which is fine but the identical basename invites mistakes and complicates `pyrefly`/`ruff TID` import sorting. Consider naming `models_pane/swap_ops.py` or `models_pane/boot.py` to avoid shadowing.

- **[Correctness] `polling.py`/`state.py` import of `MemoryRecord`/`HistoryStore` spans the Phase 5 split.** Phase 1 snippet has `from mlx_tui.history import MemoryRecord` (`history.py:34`) which is correct at that point, but Phase 5 deletes that path. The plan does not schedule a second edit to `app/polling.py` to repoint to `history.store`. Either keep a one-line re-export in `history/__init__.py` through Phase 5 or explicitly list the follow-up edit in Final Sweep.

### Minor
- **[Plan Mechanics] Lint command drift.** Current State cites `uv run ruff check src/mlx_tui` clean, but Phase 1–5 gates use `uv run ruff check .`. Both pass today (`ruff check .` → “All checks passed!”), but the plan should pick one canonical command to avoid future drift.

- **[Architecture] `TYPE_CHECKING` vs runtime imports for pane cross-references.** Plan notes panes import `MlxTuiApp` only under `TYPE_CHECKING` (`models_pane.py:20`, `chat_pane.py:22`, `search_screen.py:28`) — verified — but `src/mlx_tui/app/presets_ctrl.py` snippet omits the runtime `from mlx_tui.chat_pane import ChatPane` needed for `query_one(ChatPane)` (`app.py:264`), and `src/mlx_tui/app/swap_ctrl.py` snippet omits `ModelsPane` runtime import for `query_one(ModelsPane)` (`app.py:382`). The pattern is correct in principle but the snippets are incomplete, leaving an implementer to rediscover the needed runtime imports (safe because those panes only `TYPE_CHECKING`-import `MlxTuiApp`, so no cycle, but should be spelled out).

- **[Architecture] `effective_model` status text note is accurate but the delegated status module is named `app/status.py`.** This shadows top-level `mlx_tui.status` (`classify_liveness`, `format_status_line` at `status.py:1`). The proposed `from mlx_tui.app.status import render_status` vs `from mlx_tui.status import format_status_line` naming collision is analogous to the `swap` shadowing above. Minor but worth renaming to `app/status_bar.py` to avoid confusion.

- **[Correctness] `CHARS_PER_TOKEN_EST` duplication risk.** `history.py:11` defines `CHARS_PER_TOKEN_EST = 3.5` and `sse.py:7` imports it. If `tokens.py` redefines the constant, keep a single source (`history/tokens.py`) and re-export from `history/__init__.py` only if a shim is kept; otherwise update `sse.py` import explicitly.

### Suggestions
- **One commit per package with `git mv` + import fix.** Phase 1 already says `git mv src/mlx_tui/app.py src/mlx_tui/app/__init__.py` (verified: `src/mlx_tui/app` does not exist today, `darwin 25.6`). Consider doing the `mv` and the import rewrites in a single commit per phase so `pytest --collect-only` never sees a half-moved tree.

- **Keep `history/__init__.py` as a thin re-export for exactly one phase if you want easier bisect.** Deleting the facade wholesale is cleaner per ponytail, but a one-phase shim (`from .store import HistoryStore` etc.) lets each phase stay green without updating every consumer atomically. If you keep “no shims,” schedule the full consumer sweep as the first action of Phase 5 before any `history/` submodule is deleted.

- **Add `ponytail:` comment on the chosen history facade decision.** Plan already proposes `ponytail:` for package facades; apply it to the history `__init__.py` deletion rationale.

- **Pin entry point explicitly.** Phase 2 notes `mlx_tui.app:main` (`pyproject.toml:22` verified). Keep `def main()` in `app/__init__.py` and have `config_edit` only expose `build_main_parser()`/helpers, not a second `main`, to avoid entry-point churn — as the plan already leans.

## Strengths
- **Accurate Current State.** Every `wc -l`, `__all__`, `BootPlan` frozen dataclass (`swap.py:17`), `Health_timeout` via `swap.health_timeout`, `Table` bindings (`table.py:25`), and coupling notes (`tui` cast `chat_pane.py:48`/`models_pane.py:33`, `call_from_thread` only on `App` in textual 8.2.8, `NoMatches` guards, `push_screen` only on `App/Screen`) verified against `src/mlx_tui/app.py:45-485`, `history.py:114-299`, `chat_pane.py:37-354`, `models_pane.py:24-306`, `search_screen.py:32-300`.
- **SRP grouping is tasteful.** Grouping by concern (`polling`/`state`/`presets`/`swap`/`config_edit`, `params`/`turn`, `table_ops`/`swap`/`delete`, `query`/`download`, `store`/`sparkline`/`tokens`) keeps each submodule 80–150 lines and avoids one-file-per-method boilerplate, consistent with ponytail ladder 6.
- **Behaviours verbatim promise is credible.** No semantics change for `status.classify_liveness`, `BootPlan`, `ColdTracker`, `sse`, `serverctl`; each phase leaves `ruff`/`pyrefly`/`pytest` green (once gates are corrected).
- **`TYPE_CHECKING` cycle avoidance is correct.** Already used at `models_pane.py:20`/`chat_pane.py:22`/`search_screen.py:28`; extending it to new submodules is the right pattern.

## Recommended Changes
1. **Fix `@work` delegation (Critical → Phase 3,4,5).** Replace free-function `@work` definitions with pane-method stubs that delegate:
   - `chat_pane/__init__.py`: keep `@work(exclusive=True, group="chat", thread=True) def _run_turn(self, ...): return turn.run_turn_impl(self, ...)` and keep `abort`/`_update_stream` as thin wrappers.
   - `models_pane/__init__.py`: keep `@work(group="rescan"/"swap"/"delete")` methods, delegate to `table_ops._rescan_impl`, `swap.run_warm_swap_impl`, `swap.run_boot_impl`, `delete.run_delete_impl`.
   - `search_screen/__init__.py`: keep `@work(group="hf-search"/"hf-size"/"hf-download")` methods and `keep @on` decorators, delegate to `query`/`download` plain functions. Update plan snippets accordingly.

2. **Correct Phase 1 gate for `_shade_for_ctx` (Critical → Phase 1).** Change success criterion to:
   - `uv run python -c "import mlx_tui.app; assert not hasattr(mlx_tui.app, '_shade_for_ctx')"`
   - keep `tests/unit/test_history.py:402` unchanged until Phase 5; add a Phase 5 step “update `test_history.py:402` to `from mlx_tui.history.sparkline import _shade_for_ctx`”.

3. **Update `find_server_pid` seams (Critical → Phase 1,2,5).** Either switch `state.py`/`polling.py` to `import mlx_tui.process as process` and use `process.find_server_pid`, or add monkeypatch updates:
   - `mlx_tui.app.state.find_server_pid` and `mlx_tui.app.polling.find_server_pid` (or their actual module paths) plus the existing `mlx_tui.process.find_server_pid`.
   - Update `scan_models` patch as planned to `mlx_tui.models_pane.table_ops.scan_models`, but note the `from`-import caveat and patch the submodule binding.

4. **Resolve `_SwapShim` location (Major → Phase 1,2).** State explicitly: move `_SwapShim` from `app.py:48` to `app/state.py` in Phase 1, and have `app/__init__.py` do `from .state import _SwapShim` if still referenced, or keep it in `state.py` only. Remove the “keep `_SwapShim` in `__init__.py`” line from Phase 1.

5. **Decide history facade (Major → Phase 5).** Replace “keeping `history/__init__.py` minimal (~20 lines) **or** delete it” with a single decision: delete the facade and update all `from mlx_tui.history import …` consumers in the same commit. List the consumers: `app/__init__.py`, `app/polling.py`, `chat_pane/__init__.py`+`turn.py`, `metrics_pane.py:14`, `sse.py:7`, `tests/unit/test_history.py`, `tests/conftest.py` (if any).

6. **Complete import sweep (Major → Phase 5).** Add to Final Sweep:
   - `src/mlx_tui/sse.py:7` `CHARS_PER_TOKEN_EST` → `from mlx_tui.history.tokens import CHARS_PER_TOKEN_EST`
   - `src/mlx_tui/metrics_pane.py:14` split imports to `history.store`/`sparkline`
   - `src/mlx_tui/app/polling.py` repoint after history split to `history.store.MemoryRecord`
   - Document that `history/store.py`, `sparkline.py`, `tokens.py` are independent (no cycles).

7. **Rename shadowing submodules (Major → Phase 4,5).** Rename `models_pane/swap.py` → `models_pane/swap_ops.py` (or `boot.py`) and `app/status.py` → `app/status_bar.py` to avoid shadowing `mlx_tui.swap` (`swap.py:17`) and `mlx_tui.status` (`status.py:1`).

8. **Fix format and packages gates (Major → Phase 5).** Change `ruff format --check` allowlist from `tests/integration/test_swap_integration.py` to `src/mlx_tui/search.py:92`, or format that file pre-emptively. Add `uv run python -m hatch build && tar tzf dist/*.whl | grep mlx_tui/app` verification or explicitly list subpackages in `tool.hatch.build.targets.wheel.packages`.

9. **Normalize size ceilings (Minor → All Phases).** Keep global ceiling “each file ≤250” (`Overview`) and note aspirational per-phase limits (≤150) as guidance, not gates, or keep per-phase gates but make them consistent (e.g., Phase 3 `turn.py` ≤180, not ≤170, since 170 is the estimate).

10. **Add targeted automated checks (Minor → Phases 3–5).** Beyond “whole suite green”, gate on one behavior per moved concern:
    - Phase 3: `uv run pytest tests/unit/test_chat_pane_params -k clamp` or a one-liner exercising `parse_params`
    - Phase 4: swap-integration happy path
    - Phase 5: `from mlx_tui.history.sparkline import _shade_for_ctx; assert _shade_for_ctx([100,200,300,400])` as in `test_history.py:401`

