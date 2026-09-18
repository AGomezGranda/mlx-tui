# Plan Review: Reorganize Comparison Modules into Packages

**Date:** 2026-09-18
**Target:** docs/plans/2026-09-18-comparison-package-reorganization.md
**Review:** 1
**Verdict:** REVISE

## Assessment

The plan has a sound move-only shape, accurate file sizes and module boundaries, and a healthy verification sequence. It needs two targeted corrections before implementation: explicitly migrate the integration tests that still use the facade, and keep the canonical `JSONValue` dependency in `mlx_tui.json_util` instead of routing managed code through the comparison package.

## Cross-Cutting Themes

- Import ownership is not fully enumerated: the plan correctly handles production lookup namespaces and unit-test monkeypatches, but leaves integration-test facade consumers implicit.
- The new comparison boundary should not absorb a generic JSON type that the current working tree has already moved to the dependency-leaf `json_util` module.

## Findings

### Critical

None.

### Major

- **[Correctness / Plan Mechanics] Phase 1–2, lines 54 and 78:** The listed integration-test edits only retarget `comparison_contracts` and `comparison_summary`, but the actual tests still import and use the facade. `tests/integration/test_compare_integration.py:23,36-38,54,135,472,564-566` uses `from mlx_tui import comparison` and `comparison.*`; `tests/integration/test_app_integration.py:595,628-665` does the same. After deleting `comparison.py`, these callers remain unhandled, and the Phase 1 stale-import gate at line 61 fails. Add explicit `contracts`/`runner`/`store` imports and usage mappings for both integration files; keep their `mlx_tui.compare.workflow.*` monkeypatch targets unchanged.

- **[Architecture & Patterns] Phase 1, lines 42, 51, and 54:** The plan routes `JSONValue` in `managed/inspect.py`, `managed/runtime.py`, and `tests/unit/test_managed.py` through `mlx_tui.comparison.contracts`. In the current working tree, `JSONValue` is canonical in `src/mlx_tui/json_util.py:12`, and `comparison_contracts.py:13` merely imports it from there; the uncommitted cleanup in `profiles.py` confirms that layering direction. This would create an unnecessary managed-to-comparison dependency and contradict the plan’s instruction to preserve the dirty cleanup. Import `JSONValue` from `mlx_tui.json_util` in those managed files/tests; only comparison-specific validation symbols should move to `comparison.contracts`.

### Minor

- **[Plan Mechanics] Current State, line 13:** The facade currently imports/re-exports from five sibling implementation modules (`contracts`, `decoding`, `encoding`, `runner`, and `store`), not seven; it does not re-export `summary` or `presenter`. Correct the count so the inventory is mechanically trustworthy.

### Suggestions

- Add one installed-artifact import assertion for `mlx_tui.comparison.contracts` and `mlx_tui.compare.presenter` to `tests/artifact_smoke.py`; the current smoke test covers resources and the CLI, which is probably sufficient here but does not state the new module boundary directly.

## Strengths

- The six-way headless split preserves existing responsibilities and explicitly protects JSON schema, persistence, runtime, and profile-resource behavior.
- The plan understands lookup-namespace monkeypatching and keeps `mlx_tui.compare.workflow.*` stable while moving implementation ownership.
- Phase gates are concrete, and the dirty-worktree warning identifies the exact overlapping files that need preservation.

## Recommended Changes

1. Expand the Phase 1/2 test changes with the two integration-test facade migrations and list the exact module aliases/call replacements; retain the existing `compare.workflow` string patch targets. (Major 1)
2. Change managed code and managed tests to import `JSONValue` from `mlx_tui.json_util`; leave `app/polling.py`’s comparison-specific imports pointed at `comparison.contracts`. (Major 2)
3. Correct the facade module count in Current State. (Minor 1)
4. Optionally add the explicit installed-artifact import assertions. (Suggestion)
