# Reorganize Comparison Modules into Packages

**Date:** 2026-09-18
**Work Item:** n/a
**Status:** Complete

## Overview

Move the headless comparison implementation into a dedicated `comparison/` namespace package, move its UI presenter into the existing `compare/` package, and remove the `comparison.py` re-export facade. This is a breaking, move-only reorganization: all imports and tests change to explicit module paths, while comparison behavior, persisted schema, profile catalogue location, and runtime behavior remain unchanged.

## Current State

- `src/mlx_tui/comparison.py:1-86` is a compatibility facade that re-exports contracts, encoding/decoding, runner, and store symbols from seven sibling modules.
- The headless comparison modules are scattered at the package root: `comparison_contracts.py` (213 lines), `comparison_encoding.py` (143), `comparison_decoding.py` (488), `comparison_store.py` (213), `comparison_runner.py` (397), and `comparison_summary.py` (191).
- `src/mlx_tui/compare/` already groups the Compare tab into `pane.py` (400 lines), `decisions.py` (175), `render.py` (379), and `workflow.py` (378). `src/mlx_tui/comparison_presenter.py:1-317` is the only Compare-specific presentation module outside that package.
- The facade is imported by `src/mlx_tui/app/app.py:30-31`, `src/mlx_tui/app/state.py:10-17`, and the four `src/mlx_tui/compare/*.py` modules. Direct contract imports also exist in `src/mlx_tui/app/polling.py:17`, `src/mlx_tui/managed/inspect.py:16`, and `src/mlx_tui/managed/runtime.py:16`.
- `tests/unit/test_comparison.py:16-47` imports the facade, runner, and store separately and asserts that private helpers live in their defining modules. Its later tests call facade functions such as `encode_comparison`, `decode_comparison`, `save_comparison`, `load_comparison`, and `run_comparison`.
- Compare integration tests patch lookup namespaces such as `mlx_tui.compare.workflow.run_comparison`, `resolve_cached_snapshot`, `verify_profile_snapshot`, and `process.find_server_process` (`tests/integration/test_compare_integration.py:81-108,196,239,412`; `tests/integration/test_app_integration.py:619-669`). These namespaces remain stable because `compare/` stays in place.
- `src/mlx_tui/profiles.py:393-404` loads `src/mlx_tui/coding_profiles.toml` through the `mlx_tui` package. `tests/artifact_smoke.py:77-80,179-200` verifies those packaged paths, so neither resource moves in this plan.
- The current working tree has unrelated uncommitted import cleanups. They are outside this plan and must remain intact.
- Baseline verification completed before this plan: `rtk uv run pytest -q`, `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, `rtk uv run pyrefly check --min-severity warn`, and `rtk uv run python tests/artifact_smoke.py` all pass.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Use `src/mlx_tui/comparison/` for headless comparison code | Keep flat `comparison_*.py` files; move only a subset; group all comparison code under one package | The six headless modules form one dependency cluster, and the existing `sessions/`, `history/`, and `compare/` namespace packages establish the repository convention. |
| Keep `src/mlx_tui/compare/` as the UI boundary | Rename it to `comparison/ui/`; leave it unchanged | The UI package is already cohesive and is referenced by app lifecycle code. Moving only its stranded presenter gives the useful boundary with less path churn. |
| Move `comparison_presenter.py` to `compare/presenter.py` | Leave it at the root; move it under `comparison/` | Its docstring and consumers identify it as Compare-pane presentation code, so `compare/presenter.py` is the clearest owner. |
| Delete `src/mlx_tui/comparison.py` without a replacement facade | Preserve a re-export shim; add package-level re-exports | The user requested a breaking change. Explicit imports make ownership visible and avoid recreating the current flat compatibility surface. |
| Keep `profiles.py` and `coding_profiles.toml` where they are | Move them into `comparison/` | Managed inspection and app startup consume the profile catalogue, and moving the resource would add packaging changes without improving the main comparison module layout. |
| Move files without merging or redesigning logic | Combine encoding/decoding; extract new abstractions; change persisted schemas | The request is organization, not behavior. Existing module responsibilities and persisted JSON remain unchanged. |

## Implementation Phases

### Phase 1: Move the Headless Comparison Package

Create the `comparison/` namespace package, move the six headless modules into responsibility-named files, remove the facade, and update every production and unit-test import to explicit module paths.

**Changes:**

- `src/mlx_tui/comparison/contracts.py` — move all contents of `comparison_contracts.py`, including `JSONValue`, comparison constants, validation/persistence/identity exceptions, `LoopbackEndpoint`, `SavedChoice`, `MemorySample`, `ComparisonInput`, `TrialResult`, `ComparisonResult`, and `parse_loopback_url`; retain the current signatures and validation behavior.
- `src/mlx_tui/comparison/encoding.py` — move `comparison_encoding.py` unchanged, including `_json_value`, process-identity encoding helpers, profile/input/trial/result encoders, and `encode_comparison`; update its contract import to `mlx_tui.comparison.contracts`.
- `src/mlx_tui/comparison/decoding.py` — move `comparison_decoding.py` unchanged, including strict schema-v1 decoding and nested validators; update imports to `mlx_tui.comparison.contracts` and `mlx_tui.comparison.encoding`.
- `src/mlx_tui/comparison/store.py` — move `comparison_store.py` unchanged, including `comparison_dir`, `choice_path`, `save_comparison`, `load_comparison`, `save_choice`, `load_choice`, `_decision_payload`, `choice_is_committed`, and `commit_choice`; update imports to the new `contracts`, `encoding`, and `decoding` paths.
- `src/mlx_tui/comparison/runner.py` — move `comparison_runner.py` unchanged, including `coding_payload`, `coding_check_v1`, `assert_coding_check_v1`, `verify_profile_snapshot`, `_verify_snapshots`, and `run_comparison`; update imports to the new `contracts`, `encoding`, `store`, and `summary` paths.
- `src/mlx_tui/comparison/summary.py` — move `comparison_summary.py` unchanged, including `_summary`, `_has_unknown`, and latency/memory conclusion helpers; update imports to `contracts` and `encoding`.
- `src/mlx_tui/comparison.py`, `src/mlx_tui/comparison_contracts.py`, `src/mlx_tui/comparison_encoding.py`, `src/mlx_tui/comparison_decoding.py`, `src/mlx_tui/comparison_store.py`, `src/mlx_tui/comparison_runner.py`, and `src/mlx_tui/comparison_summary.py` — delete the old flat modules after all callers are updated; do not add `comparison/__init__.py` re-exports.
- `src/mlx_tui/app/app.py` — import `ComparisonResult` and `SavedChoice` from `mlx_tui.comparison.contracts`.
- `src/mlx_tui/app/state.py` — import `choice_is_committed`, `choice_path`, `load_choice`, and `load_comparison` from `mlx_tui.comparison.store`; import `verify_profile_snapshot` from `mlx_tui.comparison.runner`; import `_PROFILE_COUNT` from `mlx_tui.comparison.contracts`.
- `src/mlx_tui/app/polling.py`, `src/mlx_tui/managed/inspect.py`, and `src/mlx_tui/managed/runtime.py` — retarget their direct contract/`JSONValue` imports to `mlx_tui.comparison.contracts`.
- `src/mlx_tui/compare/decisions.py`, `src/mlx_tui/compare/pane.py`, `src/mlx_tui/compare/render.py`, and `src/mlx_tui/compare/workflow.py` — replace facade imports with explicit imports from `comparison.contracts`, `comparison.runner`, and `comparison.store` according to the symbols each module currently uses; retarget `_PROFILE_COUNT` to `comparison.contracts`. Keep the existing `compare.*` module paths for the UI.
- `tests/unit/test_comparison.py` — replace `from mlx_tui import comparison, comparison_runner, comparison_store` with explicit aliases for `comparison.contracts`, `comparison.decoding`, `comparison.encoding`, `comparison.runner`, and `comparison.store`; update calls so contract types/constants come from `contracts`, serialization calls come from `encoding`/`decoding`, persistence and choice calls come from `store`, and execution calls come from `runner`. Replace the facade-re-export assertion with assertions that the moved modules own `_decision_payload` and `_verify_snapshots` and that the contracts module does not expose runner/store functions.
- `tests/unit/test_comparison_summary.py`, `tests/integration/test_app_integration.py`, `tests/integration/test_compare_integration.py`, and `tests/unit/test_managed.py` — update direct imports of `comparison_contracts` and `comparison_summary` to `comparison.contracts` and `comparison.summary`.

**Success Criteria:**

#### Automated Verification:

- [x] Headless comparison, managed, and profile consumers pass: `rtk uv run pytest tests/unit/test_comparison.py tests/unit/test_comparison_summary.py tests/unit/test_managed.py tests/unit/test_profiles.py -q`
- [x] No production or test code imports deleted flat comparison modules: `rtk rg -n 'from mlx_tui import comparison|import mlx_tui\.comparison([^.]|$)|mlx_tui\.comparison_(contracts|encoding|decoding|store|runner|summary)([^[:alnum:]_]|$)' src tests` returns no matches.
- [x] New comparison modules type-check: `rtk uv run pyrefly check --min-severity warn`
- [x] Lint passes: `rtk uv run ruff check src tests`

#### Manual Verification:

- [x] `rtk uv run python -c "from mlx_tui.comparison.contracts import ComparisonInput; from mlx_tui.comparison.runner import run_comparison; from mlx_tui.comparison.store import load_comparison; print('comparison imports ok')"` prints `comparison imports ok` and no old `mlx_tui.comparison_*` module is present under `src/mlx_tui`.

### Phase 2: Move Compare Presentation and Retarget UI Lookups

Put the remaining Compare-specific presenter beside the Compare pane and verify that UI imports and monkeypatches still target the namespaces where the UI looks up its dependencies.

**Changes:**

- `src/mlx_tui/compare/presenter.py` — move `comparison_presenter.py` unchanged, including `format_seconds`, `result_header`, `progress_text`, `measurement_rows`, `latency_text`, `memory_text`, `trial_table_rows`, `trial_detail_text`, and `keep_button_labels`; import comparison contracts from `mlx_tui.comparison.contracts`.
- `src/mlx_tui/comparison_presenter.py` — delete the old root-level presenter after consumers move.
- `src/mlx_tui/compare/render.py` and `src/mlx_tui/compare/workflow.py` — replace `mlx_tui.comparison_presenter` imports with `mlx_tui.compare.presenter`; keep the existing `mlx_tui.compare.workflow.*` lookup names used by integration tests.
- `tests/integration/test_compare_integration.py` and `tests/integration/test_app_integration.py` — preserve string monkeypatch targets under `mlx_tui.compare.workflow` for `run_comparison`, `resolve_cached_snapshot`, `verify_profile_snapshot`, and `process.find_server_process`; only update their imported summary/contract module paths from Phase 1.
- `tests/unit/test_comparison.py` — retarget monkeypatch objects to `mlx_tui.comparison.runner` and `mlx_tui.comparison.store`, preserving the existing lookup-namespace rule for `_verify_snapshots`, `stream_turn`, `process.find_server_process`, `save_choice`, and `save_comparison`.

**Success Criteria:**

#### Automated Verification:

- [x] Compare integration behavior passes with the new presenter location: `rtk uv run pytest tests/integration/test_compare_integration.py tests/integration/test_app_integration.py -q`
- [x] Comparison unit behavior passes: `rtk uv run pytest tests/unit/test_comparison.py tests/unit/test_comparison_summary.py -q`
- [x] No stale presenter imports remain: `rtk rg -n 'comparison_presenter' src tests` returns no matches.
- [x] Formatting passes: `rtk uv run ruff format --check src tests`

#### Manual Verification:

- [ ] The Compare tab still renders setup details, progress, result measurements, trial details, latency/memory conclusions, and Keep A/Keep B labels when exercised through the existing integration flow.

### Phase 3: Document the New Boundary and Run Full Gates

Update the authoritative architecture description and verify the complete repository after the breaking import migration.

**Changes:**

- `ARCHITECTURE.md:265-287` — replace the `comparison.py` facade description with the `comparison/` headless package boundary and identify `compare/presenter.py` as part of the Compare UI package; preserve the existing behavioral description.
- `ARCHITECTURE.md:321-337` — replace the flat comparison file list with the `comparison/` package entries (`contracts.py`, `encoding.py`, `decoding.py`, `store.py`, `runner.py`, `summary.py`) and list `compare/` as the UI package containing the presenter.

**Success Criteria:**

#### Automated Verification:

- [x] Full test suite passes: `rtk uv run pytest -q`
- [x] Lint passes: `rtk uv run ruff check .`
- [x] Format passes: `rtk uv run ruff format --check src tests`
- [x] Strict type checking passes: `rtk uv run pyrefly check --min-severity warn`
- [x] Wheel and sdist resource/entry-point smoke test passes: `rtk uv run python tests/artifact_smoke.py`
- [x] The architecture docs contain no deleted module paths in the current layout section: `rtk rg -n 'comparison(_contracts|_encoding|_decoding|_store|_runner|_summary|_presenter)?\.py|comparison\.py.*facade' ARCHITECTURE.md` returns no matches.

#### Manual Verification:

- [ ] Run `rtk uv run mlx-tui --help`, open the TUI, switch to Compare, and confirm the setup/readiness/result UI loads and exits cleanly; no profile or managed-runtime resource behavior changes.

## Out of Scope

- Moving `profiles.py` or `coding_profiles.toml`, changing their import/resource paths, or changing packaged profile evidence.
- Moving `src/mlx_tui/compare/` under `comparison/ui/`; only its presenter moves because the existing UI package is already a clear boundary.
- Changing comparison algorithms, trial ordering, endpoint validation, summary heuristics, cancellation, persistence semantics, or JSON schema.
- Preserving imports from `mlx_tui.comparison`, `mlx_tui.comparison_contracts`, `mlx_tui.comparison_runner`, `mlx_tui.comparison_store`, or `mlx_tui.comparison_presenter`; these paths are intentionally deleted.
- Splitting or reorganizing chat, app, sessions, history, models, or managed modules beyond import retargets required by the comparison move.
- Updating the historical completed plan `docs/plans/2026-09-17-refactor-oversized-modules.md`; its old paths document the earlier implementation state and are not the current architecture reference.

## Risks & Mitigations

- Import cycles after removing the facade → preserve the current dependency direction: `contracts` is the type leaf, `encoding`/`decoding` depend on contracts, `store` depends on codecs, `runner` depends on contracts/encoding/store/summary, and UI modules depend on these explicit layers; run Pyrefly and the focused tests after each phase.
- Tests patch the wrong namespace after modules move → retarget object patches to `mlx_tui.comparison.runner` or `mlx_tui.comparison.store`; keep `mlx_tui.compare.workflow.*` string targets because the Compare UI module remains in place.
- Old and new modules coexist accidentally → delete every old flat comparison file and run the stale-import search before the full suite.
- Packaging breaks because of the new namespace directory → follow the existing namespace-package layout used by `history/`, `sessions/`, and `compare/`; leave all package-root resources unchanged and run `tests/artifact_smoke.py`.
- Unrelated dirty-worktree edits are overwritten during moves → preserve the current changes in `src/mlx_tui/app/state.py`, `src/mlx_tui/chat_ui/*`, `src/mlx_tui/compare/*`, `src/mlx_tui/comparison_contracts.py`, `src/mlx_tui/comparison_presenter.py`, `src/mlx_tui/comparison_store.py`, and `src/mlx_tui/profiles.py`; resolve overlapping edits by carrying both the existing cleanup and the new import path.
