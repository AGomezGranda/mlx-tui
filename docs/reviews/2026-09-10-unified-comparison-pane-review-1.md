# Plan Review: Unified comparison pane and user guide

**Date:** 2026-09-10
**Target:** docs/plans/2026-09-10-unified-comparison-pane.md
**Review:** 1
**Verdict:** APPROVE

## Assessment

The product direction and behavioral contracts are strong, and the stated baseline matches the current working tree. The plan needs revision because it relocates comparison ownership without sufficiently separating its existing persistence, scoring, execution, presentation, and integration-test responsibilities, and because result reopening leaves the decision write target ambiguous.

## Cross-Cutting Themes

- The dedicated tab is the right ownership boundary, but moving and then enlarging one pane would preserve the same concentration under a new filename.
- Structural refactoring should be behavior-preserving and precede the UI redesign, with the existing `mlx_tui.comparison` import surface retained as a compatibility facade.
- Opened-result identity must include the file actually opened; otherwise presentation and later mutation can refer to different artifacts.

## Findings

### Critical

None.

### Major

- **[Architecture & Patterns]** Phase 1 and Phase 2: the proposed `ComparePane` starts with roughly 350 lines moved from `MetricsPane` and then absorbs setup, validation, responsive layout, progress, result formatting, trial details, decisions, and result opening. Meanwhile `src/mlx_tui/comparison.py` remains a 1,594-line module containing contracts, JSON codecs, atomic storage, choice transactions, scoring, snapshot verification, process sampling, and execution. Split these along existing seams before the behavioral redesign; keep the pane responsible for Textual composition/events and worker lifetime, use pure presentation helpers for result rows/details, and do not add a controller/interface layer.
- **[Correctness]** Phase 2, “Open result”: `load_comparison(path)` at `src/mlx_tui/comparison.py:948` decodes the embedded `ComparisonInput.result_path` without binding it to the path that was actually opened. If a completed result was copied, a later Keep/Reject can update the embedded original rather than the displayed file. Make the opened path authoritative in memory (preferably in `load_comparison`) and add a copied-result decision regression, or explicitly make such results inspection-only.
- **[Architecture & Patterns / Test Coverage]** Phase 1 and Phase 2 keep and expand comparison tests in `tests/integration/test_metrics_integration.py` after Metrics no longer owns comparison. That leaves misleading test ownership and makes the Metrics suite grow around another pane. Move the existing comparison helpers/tests to `tests/integration/test_compare_integration.py`; leave only history/sparkline/table behavior in the Metrics test module.

### Minor

- **[Plan Mechanics]** Phase 2 says each item is localized, but several bullets combine multiple UI regions, state transitions, guards, and failure modes. Introduce a behavior-preserving module-split phase and express the UI phase by the owning module so each checkpoint has a narrow verification target.
- **[API/Compatibility]** A module split can silently break current imports and monkeypatches. `src/mlx_tui/app/__init__.py`, the runtime qualification test, and tests import through `mlx_tui.comparison`, while unit tests monkeypatch private names on that module. Preserve the public facade and retarget private test hooks to the defining internal module; do not preserve private aliases solely for tests.

### Suggestions

- **[Architecture & Patterns]** Use a few responsibility-based sibling modules rather than a package hierarchy: contracts, persistence/choice, summary, runner, and presentation are enough. Add another seam only if implementation reveals a genuine cycle.

## Strengths

- The plan correctly separates comparison from ordinary model selection and Metrics history.
- Frozen result profiles, explicit decision semantics, saved-versus-applied feedback, and busy/cancellation guards are specified carefully.
- The latency fix preserves qualification gates and includes meaningful edge-case coverage.
- The plan avoids new dependencies, polling, token streaming, a history subsystem, and speculative controller abstractions.
- Automated verification is real and reproducible: 528 tests passed with 8 skipped; Ruff and Pyrefly match the recorded baseline.

## Recommended Changes

1. Add an initial behavior-preserving phase that splits `comparison.py` behind its existing public facade into contracts, persistence/choice, summary, and runner modules, with focused import/monkeypatch updates.
2. Keep `ComparePane` limited to Textual UI state/events and worker lifetime; move pure result/table/detail formatting to one presentation module.
3. Move comparison integration coverage out of `test_metrics_integration.py` into `test_compare_integration.py` during extraction.
4. Define the opened file as the authoritative in-memory `result_path` and test opening/deciding a copied completed result without touching the original.
5. Preserve Draft status until the revised ownership and reopened-result semantics are reviewed.

## Re-Review (Pass 2)

**Verdict:** APPROVE

### Previous Findings

- **[Architecture & Patterns] ComparePane/headless concentration** — Resolved. The plan now starts with a behavior-preserving split into contracts, persistence/choice, summary, and runner modules behind `mlx_tui.comparison`, and assigns pure UI formatting to `comparison_presenter.py` without adding a controller layer.
- **[Correctness] Opened result can mutate a different path** — Resolved. The opened path becomes authoritative in memory, and the plan requires a copied-result regression proving the copy is updated while the original remains untouched.
- **[Architecture & Patterns / Test Coverage] Comparison tests remain under Metrics** — Resolved. Existing and new comparison integration coverage moves to `test_compare_integration.py`; Metrics retains only its own history/rendering tests.
- **[Plan Mechanics] Oversized localized edits** — Resolved. Structural extraction is a separate verified phase and the behavioral work is assigned by module ownership.
- **[API/Compatibility] Facade and private monkeypatch drift** — Resolved. The plan preserves production/runtime exports through the facade, retargets private test hooks to defining modules, and adds an import-surface check.

### New Issues Introduced

None. The added modules correspond to existing responsibilities in a 1,594-line implementation and do not introduce speculative interfaces, dependencies, or package hierarchy.
