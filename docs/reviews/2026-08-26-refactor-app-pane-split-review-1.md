# Plan Review: Refactor app.py into pane widgets + slim coordinator

**Date:** 2026-08-26
**Target:** docs/plans/2026-08-26-refactor-app-pane-split.md
**Review:** 1
**Verdict:** REVISE

## Assessment

Exceptionally well-researched plan: every `file:line` citation checked out against the codebase (all 40+ app.py references, every test-seam line number except one), and the baseline gates reproduce exactly as claimed (158 passed, ruff clean, pyrefly 0 errors). However, the two code snippets at the heart of Phase 2 were not traced against the swap state machine they drive, and both contain omissions that break every successful boot. Two further defects sit in the Phase 4/5 wiring instructions (a nonexistent widget API and a TYPE_CHECKING import used at runtime).

## Cross-Cutting Themes

- **Snippet-vs-machine gap**: the plan's inventories are meticulous, but its embedded code blocks were not executed mentally against the systems they touch (SwapMachine graph, Textual 8.2.8 API surface). All Critical/Major findings live there.
- **Phase-boundary correctness**: phases are individually green *as gated*, but two Phase 4 instructions would only fail at keypress/runtime in ways the stated gates do catch late rather than never — worth fixing on paper before implementation.

Lenses applied: **Correctness**, **Architecture & Patterns**, **Test Coverage**, **Plan Mechanics**. Skipped **Security** (no new inputs/secrets; shlex quoting moved verbatim) and **Performance** (pure refactor, no hot-path changes). API/Compatibility concerns are folded into Correctness/Test Coverage (monkeypatch targets, internal seam renames only).

## Findings

### Critical

- **[Correctness] Phase 2, `_run_boot`: dropped state transitions make every successful boot raise `InvalidTransition`.** The graph in `src/mlx_tui/swap.py:16-26` allows `STOPPING → {STARTING, FAILED}` and `STARTING → {WAITING_HEALTH, FAILED}`; neither `STOPPING` nor `STARTING` may transition directly to `IDLE`. The original workers perform `transition(SwapState.STARTING)` after a successful stop (app.py:413) and `transition(SwapState.WAITING_HEALTH)` before `wait_healthy` (app.py:428, 506). The plan's merged `_run_boot` (plan lines 162–226) contains neither transition, yet calls `transition(SwapState.IDLE)` on success while the machine sits in `STOPPING` (restart entry, per plan line 227) or `STARTING` (cold-start entry). Result: the worker crashes mid-success, the success log line never lands, the UI stays locked, and the machine wedges non-IDLE so all later swaps log "swap already in progress" forever. This also falsifies the plan's claim that "Tests: no assertion changes required" — `test_restart_swap_streams_and_completes` and both cold-start happy paths wait on strings that will never be logged. Fix: add `self._swap_machine.transition(SwapState.STARTING)` immediately after the `stop_first` block succeeds, and `self._swap_machine.transition(SwapState.WAITING_HEALTH)` just before `wait_healthy`. (Failure paths through `_fail_swap` are coincidentally legal: `STOPPING→FAILED→IDLE`.)
- **[Correctness] Phase 2, `_run_boot`: success path never releases the UI.** Both original workers end with an unconditional `self.call_from_thread(self._set_swap_ui, False)` *outside* the if/else (app.py:461, 541). The merged `_run_boot` releases the UI only via `_fail_swap` on the two failure branches; the `ok` branch logs and refreshes markers but returns without clearing `#models-table.disabled` / `#chat-input.disabled`. Every successful boot/restart would leave the table and chat input permanently disabled. Fix: call `self.call_from_thread(self._set_swap_ui, False)` after the if/elif/else (matching today's control flow).

### Major

- **[Correctness] Phases 4/5: `Widget.call_from_thread` does not exist in textual 8.2.8 — pane worker bodies as written raise `AttributeError`.** Verified against the installed package: `hasattr(Widget, "call_from_thread")` is `False`; `call_from_thread` is defined on `App` only. The Design Decisions table asserts "Textual supports `@work` and `call_from_thread` on widgets" — true for `@work` (via `DOMNode.workers` → the app's `WorkerManager`; `workers.cancel_group(node, group)` is valid node-scoped cancellation), false for `call_from_thread`. Affected plan code: `ModelsPane._rescan`'s `self.call_from_thread(self._populate, rows)` and the verbatim-moved `ChatPane._run_turn`/stream callbacks. Fix is mechanical and lighter than the risk section's fallback ("keep that worker method on App"): route through `self.tui.call_from_thread(...)`. Update the Design Decisions row and the Risks entry so the implementer doesn't burn the mitigation budget on the wrong fix.
- **[Correctness] Phase 4, `table.py` rewiring: `query_one(ModelsPane)` under a `TYPE_CHECKING` import is a guaranteed runtime `NameError`.** The instruction switches the action bodies to `self.app.query_one(ModelsPane)...` with ModelsPane imported only under `if TYPE_CHECKING:`. `query_one` needs the class object at call time; a module-top runtime import instead creates a genuine cycle (`models_pane` imports `table` for `ModelsTable`). Fix: import `ModelsPane` locally inside each action method, or select by id (`self.app.query_one("#models-pane")`) to avoid the import entirely.
- **[Test Coverage] Phase 5 missed a seam: `tests/integration/test_swap_integration.py:361`.** The plan repoints line 351 (`_chat.active_response = object()`) but omits line 361 in the same test (`harness.app._chat.active_response = None`), which raises `AttributeError` once App loses `_chat`. Same mechanical fix as its neighbor.

### Minor

- **[Plan Mechanics] Phase 2**: instructs adding `import time` to `serverctl.py`, which already imports it (serverctl.py:7). No-op instruction; harmless but signals the snippet wasn't diffed against the target file.
- **[Plan Mechanics] Phase 1**: "(update its two internal uses in `estimate_tokens`)" — `_CHARS_PER_TOKEN_EST` has exactly one use (history.py:20).
- **[Plan Mechanics] Phase 5**: the substitution list for `_run_turn` omits `self.host/self.port → self.tui.host/self.tui.port` (URL construction) and `_write_system_line`'s `:{self.port}` messages. Panes have neither attribute, so this surfaces as `AttributeError` mid-turn; contradicts the "exactly the listed substitutions" contract. Add them to the list.
- **[Plan Mechanics] Phase 6**: the `ruff format --check src tests` criterion lists a `.md` file among "the two pre-existing offenders"; `ruff format` only checks Python files. Verified actual state: exactly one offending file (`tests/integration/test_swap_integration.py`, hunks at lines 247 and 269).
- **[Correctness] Phase 3**: `set_rows` recomputes the fits glyph via `fits_headroom(size_on_disk, avail_gib)` where the old code rendered the precomputed `row.fits` (scanned with an earlier `avail_gib` snapshot). In production the values coincide up to a sub-second poll race and self-correct at the next marker refresh, so this is cosmetic — but the manual "pixel-equivalent" claim holds only for the production flow, not for stubbed rows whose `.fits` disagrees with recomputation (e.g., injected rows with `avail=None` render "—"). One clarifying sentence would prevent confusion later.

## Strengths

- Factually airtight inventory work: all cited app.py ranges, constants, test-seam line numbers, and the baseline gate results (158/ruff/pyrefly) verified correct against the repo.
- Each phase leaves the tree independently green, with grep/wc-style gates that catch incomplete moves (`grep -n "_rows|request_load_swap|..."`).
- Behavior preservation is taken seriously: verbatim-string enumeration for the boot merge, explicit ordering note for `_fail_swap` (transitions-then-log), and the tripwire strategy of running the untouched swap-integration suite first.
- `BootPlan` as a frozen dataclass in `swap.py` (with the import-cycle reasoning spelled out) fits the repo's functional-core conventions; the patch-target convention (`mlx_tui.models_pane.scan_models`) matches how `test_swap_integration.py:39` already works.
- Honest risk register: layout-nesting risk (correctly identified as Models-tab-only, since Chat already wraps in `Vertical`), push_screen constraints, and monkeypatch staleness are all called out with mitigations.

## Recommended Changes

1. **Fix `_run_boot` (Critical ×2)**: insert the `STARTING` transition after a successful stop and the `WAITING_HEALTH` transition before `wait_healthy`; add the unconditional `set_swap_ui(False)` release after the final if/elif/else. Then soften the "no assertion changes required" claim accordingly (it remains true for strings, but the suite is the tripwire precisely because these omissions break it).
2. **Correct the Textual API claims (Major)**: change pane worker bodies to `self.tui.call_from_thread(...)`; amend the Design Decisions row and the `@work` risk entry to separate "`@work` works on widgets" (true) from "`call_from_thread` works on widgets" (false on 8.2.8).
3. **Fix the Phase 4 `table.py` import instruction (Major)**: local import inside the actions or query by `#models-pane` id.
4. **Add the missed seam (Major)**: `test_swap_integration.py:361` to the Phase 5 repoint list.
5. Apply the Minor cleanups (drop the `import time` instruction, fix "two internal uses", extend the Phase 5 substitution list with host/port, correct the Phase 6 format-offender count, scope the pixel-equivalence claim).
