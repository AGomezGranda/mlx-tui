# Plan Review: SRP Package Split — Large Modules into Focused Packages

**Date:** 2026-08-27
**Target:** docs/plans/2026-08-27-srp-package-split.md
**Review:** 2
**Verdict:** REVISE
**Previous Review:** docs/reviews/2026-08-27-srp-package-split-review-1.md — REVISE (3 Critical, 8 Major)

## Assessment

Re-review after the plan incorporated all 10 recommendations from review-1. Facts remain accurate (`wc -l` 485/354/306/300/299 verified via `src/mlx_tui/app.py:45-485`, `chat_pane.py:37`, `models_pane.py:24`, `search_screen.py:32`, `history.py:11-299`; `uv run ruff check src/mlx_tui` 0 errors, `uv run pyrefly check` 0 errors, 231 tests `uv run pytest -q` vs. plan's 130+). The three prior Criticals are fixed: `@work` now stays on Widget via thin stubs (`chat_pane/__init__.py:168`, `models_pane/__init__.py:44`, `search_screen/__init__.py:116`), Phase 1 gate no longer hallucinates `history.sparkline` until Phase 5, and `find_server_pid` seam uses `import mlx_tui.process as process` with updated patch targets (`app/state.py:64`, `app/polling.py:83`). Renames `app/status_bar.py` and `models_pane/swap_ops.py` eliminate shadowing of `mlx_tui.status:1`/`mlx_tui.swap:17`. No new architectural flaw — remaining gaps are wiring incompleteness and one import-cycle ordering risk that would make Phase 5 fail as written, all fixable with <30 min of plan edits.

**Lenses applied (5/6):** Core — Correctness, Architecture & Patterns, Test Coverage, Plan Mechanics — plus API/Compatibility (breaking import rewrites with no shims). Skipped: Security (no auth/secrets), Performance (no hot-path change), Data/Migrations (no schema).

## Cross-Cutting Themes

- **Prior systemic fixes landed cleanly; residual wiring is now the bottleneck.** The `@work`, `find_server_pid`, and shadowing fixes are applied consistently across Phases 1–5. Remaining fragility is import enumeration/order, not Textual semantics.
- **History split decisiveness improved but not finished.** Phase 5 now says "deleting the facade in the same commit" yet still hedges "may be deleted or left empty with a ponytail comment" (`docs/plans/2026-08-27-srp-package-split.md:402`) — an implementer still must choose.
- **Verification commands are now credible, with one tool-name drift.** `ruff`/`pyrefly`/`pytest` baselines were re-checked; only `hatch build` vs `uv build` remains stale.

## Findings

### Critical
*None — no change that is guaranteed to break as written after the prior Criticals were fixed. The two Major findings below would break at import/test time if the plan is followed literally without the suggested ordering fix.*

### Major
- **[Correctness + Architecture] Phase 5 `search_screen/download.py` circular import for `_format_size`.** Plan creates `src/mlx_tui/search_screen/__init__.py` keeping `ResultsTable:32` + `_format_size:41` + `SearchScreen:48`, and `download.py:187-241` does top-level `from mlx_tui.search_screen import _format_size` (`docs/plans/2026-08-27-srp-package-split.md:387`). `__init__.py` must also import `download` to expose the `@work` stub `def _run_download(...): return download.run_download_impl(...)` (`docs/plans/2026-08-27-srp-package-split.md:398-400`). Top-level parent→child + child→parent is a classic package-init cycle. It can work only if `__init__.py` defines `_format_size` *before* importing `download`; the plan does not specify order, so a naïve top-first `from . import download, query` before the function definition yields `ImportError: cannot import name '_format_size' from partially initialized module 'mlx_tui.search_screen'`. Fix: either (a) move `_format_size` to `search_screen/_format.py` (or `mlx_tui.search:41` already has formatting) and import from there in both places, or (b) keep it in `__init__.py` but document "define `_format_size` before `from . import query, download`" and/or make `download.py` import lazily inside `run_download_impl`/`progress_line`.

- **[Plan Mechanics] Phase 5 history facade still has a placeholder alternative.** Text now: "`bash: mkdir -p src/mlx_tui/history && git mv src/mlx_tui/history.py src/mlx_tui/history/__init__.py` then split, **deleting the facade** in the same commit (ponytail: `# ponytail: history package — no shim...`). All callers are updated atomically...; no `history/__init__.py` re-exports remain (file may be deleted or left empty with a `ponytail` comment explaining deletion)." (`docs/plans/2026-08-27-srp-package-split.md:402`). The `git mv` to `__init__.py` followed by "delete it" is contradictory, and "may be deleted or left empty" is still two options — the prior review's placeholder is narrowed but not resolved. Fix: pick one in the plan (recommended: delete `history/__init__.py` entirely and have `history/store.py`+`sparkline.py`+`tokens.py` be the only files; `git mv` step becomes `cp` history.py contents into the three new files without ever creating `__init__.py`, or create then `git rm` in same commit). Remove the "or left empty" alternative.

- **[Correctness] Phase 5 import sweep is still vague for `app/__init__.py` `HistoryStore`/`MemoryStore`.** Sweeps list: "`src/mlx_tui/app/polling.py`: `MemoryRecord` → `from mlx_tui.history.store`" and "`src/mlx_tui/app/state.py` / `src/mlx_tui/app/__init__.py` if any history imports remain" (`docs/plans/2026-08-27-srp-package-split.md:406-407`). But `src/mlx_tui/app.py:30` currently imports `HistoryStore, MemoryRecord, MemoryStore, _shade_for_ctx` (`app.py:30`) — after Phase 1 only `_shade_for_ctx` is removed, while `HistoryStore`/`MemoryStore`/`MemoryRecord` stay in `app/__init__.py`. After the Phase 5 deletion, `from mlx_tui.history import HistoryStore` will fail. The "if any" hedging means an implementer could miss it. Fix: enumerate explicitly: "`src/mlx_tui/app/__init__.py:30` `HistoryStore, MemoryStore` → `from mlx_tui.history.store import HistoryStore, MemoryStore` (and `MemoryRecord` already covered via `polling.py`)".

- **[Correctness] Phase 3 facade omits `set_system_prompt` retention.** Phase 3 says trim `chat_pane/__init__.py` to `__init__`+`tui`+`has_live_turn`+`compose`+`_on_input_submitted` (`docs/plans/2026-08-27-srp-package-split.md:197`) then delegates `_parse_params`/`_apply_params_to_inputs`/`apply_config_params` to `params.py` (`docs/plans/2026-08-27-srp-package-split.md:246`). But `chat_pane.py:152` `def set_system_prompt(self, text: str)` is still called by `src/mlx_tui/app/presets_ctrl.py:138` `pane.set_system_prompt(preset.system)` (Phase 2). If the facade list is taken literally, `set_system_prompt` would be removed with the heuristically-trimmed bodies. Fix: add to the keep-list: "`set_system_prompt` stays on `ChatPane` (2 lines, not delegated)".

### Minor
- **[Plan Mechanics] Hatch build verification command is stale.** Phase 5 gates `uv run python -m hatch build && tar tzf dist/*.whl | grep -E "mlx_tui/(app|history)"` (`docs/plans/2026-08-27-srp-package-split.md:427`). Verified: `hatch` is not on PATH (`uv run hatch build` → No such file), but `uv build --wheel` succeeds and hatchling *does* auto-discover subpackages (`packages = ["src/mlx_tui"]` in `pyproject.toml:14` already includes `mlx_tui.app`/`history` — verified with `/tmp/testpkg` containing `app/__init__.py`+`app/foo.py`). Fix: change gates to `uv build --wheel && tar tzf dist/*.whl | grep -E "mlx_tui/(app|history)"` (or `uv run python -m hatchling`).

- **[Plan Mechanics] `ruff format --check` wording still informal.** Phase 5 says "`Format check: uv run ruff format --check src tests (pre-format src/mlx_tui/search.py:92 or allow that single hunk; not test_swap_integration.py)`" (`docs/plans/2026-08-27-srp-package-split.md:424`). The parenthetical "pre-format ... or allow" is a note, not a runnable gate. Verified baseline fails on `src/mlx_tui/search.py:92:39` (long `_mlx_desc` line) vs. the prior incorrect allowlist `test_swap_integration.py`. Better: make the gate "`uv run ruff format --check src tests` (format `src/mlx_tui/search.py:92` in a prior commit or add one-line exemption)".

- **[Architecture] `models_pane/table_ops.py` `rescan` helper is redundant.** Plan defines both `def rescan(pane) -> None: pane._rescan()` and `def _rescan_impl(pane)` (`docs/plans/2026-08-27-srp-package-split.md:279-280`) while `ModelsPane.rescan` already does `self._rescan()` (`models_pane.py:41`). The extra `table_ops.rescan` adds indirection without value. Keep only `_rescan_impl`/`populate`/`refresh_markers`/`progress_line`; let the pane's `rescan()` call its own `@work` stub directly.

- **[Plan Mechanics] Size ceilings still slightly inconsistent in wording.** Overview says global ≤250, aspirational per-phase ≤180; Phase 1 gate says `each ≤180, app/__init__.py ≤250`, Phase 2 says `app/__init__.py ≤200`, Phase 4 says `each ≤150` (`docs/plans/2026-08-27-srp-package-split.md:116,186,257,340`). All satisfy the global 250, but the per-phase numbers drift. Normalize: keep global 250 as the hard gate and call the tighter numbers "aspirational" everywhere, or make them consistent (e.g., Phase 4 `each ≤180` like the others).

- **[Test Coverage] Phase 1 still has no targeted behavior gate beyond "shim removed".** Phases 3–5 now have targeted checks (`chat_pane.params` clamp, `swap_integration::test_warm_swap_happy_path`, `history.sparkline` shade), but Phase 1 only checks imports and shim. Adding a one-liner `uv run python -c "from mlx_tui.app.state import effective_model; print(effective_model)"` or re-running `test_app_integration` poll-related tests would catch a wiring error before Phase 2.

### Suggestions
- **Keep `_SwapShim` import example explicit.** Phase 1 already says `app/__init__.py` may `from .state import _SwapShim` for typing; add the line to the facade snippet so future editors don't reintroduce a local copy.
- **Document import order in `search_screen/__init__.py`.** If keeping `_format_size` in the facade, add comment `# define _format_size before importing submodules to avoid circular import` and show `from . import query, download` after the function.
- **Consider a single `history/__init__.py` deletion commit with `grep` sweep listed.** Listing the exact sweep as `grep -rn "from mlx_tui.history" src` hits (`app.py:30`, `chat_pane.py:20`, `metrics_pane.py:14`, `sse.py:7`, `process`? no) in the Phase 5 step makes the 2–15 minute action concrete.
- **Pin `CHARS_PER_TOKEN_EST` single source.** Phase 5 already moves `CHARS_PER_TOKEN_EST` (`history.py:11`) to `history/tokens.py:12` and updates `sse.py:7`; ensure `history/__init__.py` deletion does not leave a stale `CHARS_PER_TOKEN_EST` re-export path.

## Strengths

- **All three prior Criticals resolved with correct Textual patterns.** `@work` stubs stay on the widget (`chat_pane/__init__.py:168`, `models_pane/__init__.py:44`, `search_screen/__init__.py:115`), `call_from_thread` stays on `App` via `self.tui`, `NoMatches` guards preserved, and `TYPE_CHECKING` cycle avoidance (`models_pane.py:20`/`chat_pane.py:22`/`search_screen.py:28`) is now spelled out for new submodules with runtime `from mlx_tui.chat_pane import ChatPane`/`ModelsPane` only where safe (`app/presets_ctrl.py:133`, `app/swap_ctrl.py:151`).
- **SRP grouping remains tasteful and minimal-file.** Polling/state/status_bar, params/turn, table_ops/swap_ops/delete, query/download, store/sparkline/tokens each 40–135 lines; no one-method-per-file boilerplate, consistent with ponytail ladder 6.
- **Shadowing risks eliminated.** `app/status_bar.py` (`docs/plans/2026-08-27-srp-package-split.md:94`) and `models_pane/swap_ops.py` (`docs/plans/2026-08-27-srp-package-split.md:290`) avoid colliding with `mlx_tui.status:1`/`mlx_tui.swap:17`.
- **Import-sweep completeness greatly improved.** Phase 5 now enumerates `sse.py:7` `CHARS_PER_TOKEN_EST`, `metrics_pane.py:14` split across store/sparkline, `app/polling.py` repoint, and `tests/unit/test_history.py:402` final `history.sparkline` path — far beyond review-1's incomplete list.
- **Gates are now executable per phase.** Phase 1's hallucinated `history.sparkline` gate is fixed to `from mlx_tui.history import _shade_for_ctx` single-file canonical, `ruff format` allowlist corrected to `src/mlx_tui/search.py:92`, and `find_server_pid`/`scan_models` patch targets list both module-import propagation and explicit `mlx_tui.app.state`/`polling`/`table_ops` paths.

## Recommended Changes

1. **Fix `search_screen/download.py` import cycle (Major).** In `docs/plans/2026-08-27-srp-package-split.md:379-401`, either move `_format_size` (`search_screen.py:41`) to `search_screen/_format.py` and import from there in both `__init__.py` and `download.py`, or keep it in `__init__.py` but order the file: define `_format_size` before `from . import query, download` and make `download.py` do `from mlx_tui.search_screen import _format_size` only after parent is initialized (document the order).

2. **Make history facade decision single-valued (Major).** Replace the "may be deleted or left empty" sentence in Phase 5 (`docs/plans/2026-08-27-srp-package-split.md:402-405`) with: "Delete `history/__init__.py` in the same commit; no re-exports remain. Callers atomically repoint to `history.store`/`sparkline`/`tokens`."

3. **Explicitly list `app/__init__.py` history repoint (Major).** Change the sweep bullet from "if any history imports remain" to: "`src/mlx_tui/app/__init__.py:30` `HistoryStore, MemoryStore` → `from mlx_tui.history.store import HistoryStore, MemoryStore`".

4. **Add `set_system_prompt` to Phase 3 keep-list (Major).** Amend the facade line (`docs/plans/2026-08-27-srp-package-split.md:197`) to include `set_system_prompt` alongside `apply_config_params` delegation.

5. **Correct wheel verification command (Minor).** Change `docs/plans/2026-08-27-srp-package-split.md:415,427` from `uv run python -m hatch build` to `uv build --wheel`.

6. **Normalize `ruff format` gate wording (Minor).** Replace the parenthetical with a runnable instruction: pre-format `src/mlx_tui/search.py:92` before the check.

7. **Remove redundant `table_ops.rescan` (Minor).** Keep only `_rescan_impl` in `docs/plans/2026-08-27-srp-package-split.md:279`; `ModelsPane.rescan` can call `self._rescan()` directly.

## Previous Findings — Resolution Delta

- **[Critical] `@work` on free functions** — **Resolved.** All three phases now keep thin `@work` stubs on the pane and delegate to `*_impl`/`run_*_impl` plain functions.
- **[Critical] Phase 1 hallucinated `history.sparkline` gate** — **Resolved.** Gate now uses `from mlx_tui.history import _shade_for_ctx` until Phase 5, where it becomes `history.sparkline`.
- **[Critical] `find_server_pid` monkeypatch seam** — **Resolved.** Submodules now use `import mlx_tui.process as process` and call `process.find_server_pid`; patch targets list `mlx_tui.process` plus `mlx_tui.app.state`/`polling` for explicit seams.
- **[Major] `_SwapShim` location contradiction** — **Resolved.** `_SwapShim` moved to `app/state.py` in Phase 1, `app/__init__.py` only re-exports for typing.
- **[Major] History facade placeholder** — **Partially resolved.** Now says "deleting the facade" but still hedges "or left empty" — needs the decisiveness fix above.
- **[Major] Import sweep incomplete (`sse.py`, `metrics_pane`, `app.py:30`)** — **Mostly resolved.** `sse.py:7`, `metrics_pane.py:14`, `polling.py`, `chat_pane/turn.py` now listed; only `app/__init__.py` `HistoryStore`/`MemoryStore` remains vague (see Recommended Change 3).
- **[Major] Hatch wheel discovery** — **Resolved with command drift.** Verification via `tar tzf` added; only the `hatch build` vs `uv build` name is stale.
- **[Major] `ruff format` allowlist wrong file** — **Resolved.** Now correctly allows `src/mlx_tui/search.py:92`.
- **[Major] Size bookkeeping inconsistent** — **Mostly resolved.** Global 250 ceiling now explicit; per-phase 150–180 variations remain but are now labeled aspirational.
- **[Major] Test coverage undersamples** — **Partially resolved.** Added targeted checks for chat params, warm-swap, and shade; Phase 1 still lacks a behavior gate.
- **[Major] `models_pane/swap.py` shadowing** — **Resolved.** Renamed to `swap_ops.py`.
- **[Major] `polling.py` repoint after history split** — **Resolved.** Phase 5 explicitly repoints `app/polling.py` to `history.store.MemoryRecord` and `chat_pane/turn.py` to `history.store`/`tokens`.
- **[Minor] Lint command drift** — **Resolved.** Canonical `ruff check src/mlx_tui` now noted.
- **[Minor] `TYPE_CHECKING` snippet completeness** — **Resolved.** Runtime `ChatPane`/`ModelsPane` imports now shown.
- **[Minor] `status.py` shadowing** — **Resolved.** Renamed to `status_bar.py`.

## Re-Review (Pass 2)
**Verdict:** REVISE (4 Major remain, no Critical)
**Previous Findings:** 3 Critical → Resolved (3/3), 8 Major → Resolved 5/8, Partially 3/8, 4 Minor → Resolved 4/4
**New Issues Introduced:** 1 Major (search_screen circular import) introduced by the package split ordering not previously flagged; history facade decisiveness and app/__init__ sweep remain partially open from review-1
