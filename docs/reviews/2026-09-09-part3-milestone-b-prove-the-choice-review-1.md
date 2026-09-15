# Plan Review: Part 3 — Milestone B: Prove the Choice

**Date:** 2026-09-09
**Target:** docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md
**Review:** 1
**Verdict:** REVISE

## Assessment

The plan is grounded in the current code and keeps unsupported MLX claims explicitly unknown. Its main weakness is that the experiment contract is less precise than the evidence it promises: required inputs cannot reach the runner, terminal states are ambiguous, and the proposed sampling can perturb or misattribute the measurements. Packaging and live-qualification checks also allow successful verification without proving the new catalogue or real contracts are present.

## Cross-Cutting Themes

- Experiment identity, preflight inputs, runtime conditions, persistence and summary eligibility need one coherent contract shared by the runner, UI and real-runtime test.
- Comparison measurements must control the TUI's own background work and distinguish server-process observations from per-profile memory evidence.
- Verification must fail closed: an omitted packaged catalogue or a skipped qualification contract cannot count as success.

## Findings

### Critical

None.

### Major

- **[Correctness, Architecture & Patterns] Shared contracts / Phase 2 (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:47`, `:51`, `:55`, `:57`, `:68`, `:94`, `:98`):** the types and runner signature cannot represent the evidence they promise. `CodingProfile` does not name a launch-settings field even though launch settings are qualification-critical and should invalidate its fingerprint. `load_coding_profiles() -> list[CodingProfile]` has no specified representation for the recommendation evidence stored beside a profile. `run_comparison(url, profiles, on_progress=...)` cannot receive the resolved snapshot paths, runtime/install/launch evidence, isolation status, or operator conditions it must snapshot and serialize. Define immutable `ProfileEntry`/`RecommendationEvidence` and `ComparisonInput` contracts (or equally small explicit types), include canonical launch settings and immutable template-asset hashes in the profile fingerprint, and make missing/unknown evidence prevent advantage/recommendation labels.
- **[Correctness, Test Coverage] Phase 2–3 terminal state machine (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:59`, `:94`, `:96`, `:111`, `:120`):** failure and cancellation semantics conflict. A load/transport/identity failure both stops one profile and requires recovery before the next, but the one-shot runner has no recovery/resume input. `stream_turn` propagates `CancelledError`; the plan does not say whether the runner checkpoints and returns a cancelled result or checkpoints then re-raises. Use the smaller rule: any terminal request failure stops the whole comparison, records the active attempt plus all remaining slots as not attempted, checkpoints, and requires a new explicit run after preflight. Specify cancellation and progress/write-failure ordering, then test that no later request is sent after either failure or cancellation.
- **[Correctness, Performance] Phase 2 measurement validity (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:97`, `:99`, `:121`):** the proposed experiment does not isolate itself from the app. The existing two-second poll sends `/health` and `/v1/models` and performs process/memory inspection during any operation (`src/mlx_tui/app/__init__.py:150`, `:188`, `:233`); `OperationCoordinator` does not pause it. Calling `find_server_process()` at 10 Hz would repeatedly inspect command lines and TCP connections (`src/mlx_tui/process.py:93`, `:133`) on the same event loop that measures `stream_turn`. More fundamentally, endpoint-attributed RSS is process-wide and can include the prior model/cache, so valid PID samples alone cannot establish a profile memory advantage while residency/eviction is unknown. Pause polling for `COMPARING` and force one refresh afterward; resolve/verify identity before a trial, sample cheap RSS/system values off the HTTP event loop, and reverify afterward. Label memory as “server process RSS while requested”; allow a profile memory advantage only with controlled restart/eviction evidence or matching order-independent observations. Treat materially different cached-token reuse as a latency confounder. Add tests for poll suppression, sampler cleanup and summary ineligibility under these confounders.
- **[Test Coverage] Phase 1 packaged catalogue (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:68`, `:69`, `:80`):** the plan loads `coding_profiles.toml` through `importlib.resources` but never verifies that build artifacts contain it. Checkout tests can pass while an installed wheel fails to load every profile. Extend `tests/artifact_smoke.py` to assert that wheel and rebuilt-sdist wheel installations contain the TOML and that `load_coding_profiles()` returns both IDs outside the checkout; add `rtk proxy uv run python tests/artifact_smoke.py` to Phase 1 and final automated criteria.
- **[Plan Mechanics, Test Coverage] Phase 4 qualification command (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:149`, `:158`):** plain `uv run pytest -q` exits successfully when runtime tests skip, so the stated command cannot prove B ran on either tier or order. Define exact B environment names and a qualification flag whose fixture fails when inputs are missing; give the exact targeted command and expected executed test/result count for each tier and order. Ordinary CI may continue to skip when the flag is absent.
- **[Plan Mechanics] Phase boundaries (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:63`, `:89`, `:114`, `:144`):** phases 1–3 each combine several multi-hour changes, and Phase 4 combines hardware qualification with a 14-day participant gate. Many change bullets exceed the create-plan requirement that each action take roughly 2–15 minutes and each phase fit one focused session. Keep the agreed four top-level phases, but split their bullets into ordered, checkable substeps and identify Phase 4's implementation checkpoint separately from its external qualification and product-gate checkpoints.

### Minor

- **[Security] Preflight URL validation (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:55`):** “loopback” needs an exact parsing rule because the runner posts model and environment metadata to a raw URL. Permit only `http`, explicit loopback IP literals and an explicit port; reject credentials, query/fragment data, deceptive suffix hosts and unsupported paths. Either reject `localhost` for supported memory attribution or state why it remains exploratory, matching `find_server_process()` (`src/mlx_tui/process.py:141`). Add adversarial URL tests.
- **[Architecture & Patterns] Candidate-pair ownership (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:119`, `:128`):** Models and Metrics both select candidates, but ownership is unspecified. Keep the selected pair in `MlxTuiApp`, consistent with its existing cross-pane coordinator role, and let each pane render/update that state without importing the other pane.
- **[Test Coverage] Offline and crash invariants (`docs/plans/2026-09-09-part3-milestone-b-prove-the-choice.md:55`, `:59`, `:100`, `:123`):** add an early test proving cached pinned preflight performs no Hub network call, explicit finite/non-negative timing rejection tests, and tests for both run→choice and choice→finalized-run crash boundaries. The current generic schema/rollback wording does not guarantee these stated invariants.
- **[Architecture & Patterns] Existing disk-fit state (`src/mlx_tui/models.py:23`, `:60`; plan Phase 1):** replacing the misleading table column should also remove the now-unused `ModelRow.fits`, `fits_headroom()` and related tests unless another real caller remains. Keeping dead fit machinery would preserve the claim the phase is meant to retire.

### Suggestions

None.

## Strengths

- Every cited existing path and symbol checked during review exists, and the current-state claims match the dirty working tree.
- The three recorded cached-asset hashes match their pinned files.
- The plan reuses `stream_turn`, the operation lease, stdlib TOML/JSON and atomic replacement instead of introducing a benchmark service, database or dependency.
- It separates first requests from repeats, retains failures, refuses general coding-quality claims and keeps managed runtime ownership out of attach mode.
- It treats response-model echo, accepted parameters, sampled RSS, cache reuse and server readiness with appropriately narrow language.
- Baseline verification passed: 501 tests passed and 6 opt-in runtime tests skipped; Ruff lint/format and Pyrefly also passed.

## Recommended Changes

1. Define the immutable profile/evidence/input contracts and update `run_comparison` so every persisted/qualified field has an explicit source.
2. Make terminal failure and cancellation stop/checkpoint behavior exact, including persistence callback failures and zero subsequent requests.
3. Pause app polling, move cheap sampling off the request event loop, reverify process identity after each trial, and prevent process-wide/cache-confounded data from producing profile advantage labels.
4. Add artifact installation coverage and exact fail-closed Milestone B qualification commands.
5. Break the four agreed phases into small ordered actions and distinguish implementation completion from hardware and 14-day validation gates.
6. Add exact loopback validation, app-owned candidate selection, offline/crash tests, and delete obsolete disk-fit code.
