# Plan Review: Part 3 Milestone C — Own activation

**Date:** 2026-09-12
**Target:** docs/plans/2026-09-12-part3-milestone-c-own-activation.md
**Review:** 1
**Verdict:** REVISE

## Assessment

The plan is grounded in the current code and preserves the unresolved Milestone B gate, process-ownership boundaries, and existing comparison experiment. Two gaps need targeted changes before implementation: managed profile activation must participate in the shared operation lease, and live qualification must execute the exact distributable being delivered. No implementation or plan-status changes were made during this review.

## Review Scope and Verification

Lenses: Correctness, Test Coverage, Architecture & Patterns, Plan Mechanics, Security, and API/Compatibility. Security covers installation, repair, and subprocess ownership; compatibility covers pinned tooling, packaged resources, and attach behavior. Performance and Data/Migrations were not separate lenses: concurrency and repair concerns are covered above, with no new database migration or performance claim.

Read the complete plan and checked its current-state references against the supplied working tree, including the existing boot caller, process helpers, configuration, comparison/profile flow, artifact smoke, runtime contract, and A/B evidence. No earlier review of this plan exists. Existing cited paths/symbols and the documented B blocker match the code; proposed files and commands are identified as future work.

| Baseline command | Review result |
|---|---|
| `rtk proxy uv run ruff check .` | Passed |
| `rtk proxy uv run ruff format --check src tests` | Passed; 76 files |
| `rtk proxy uv run pyrefly check --min-severity warn` | Exit 1; the same seven documented unnecessary `str()` warnings |
| `rtk proxy uv run python tests/artifact_smoke.py` | Passed |
| `rtk proxy uv run pytest -q` | Passed: 548 passed, 8 skipped in 170.07 seconds; opt-in live contracts skipped |

Local uv reports 0.12.7 and its help exposes the proposed explicit interpreter, build-constraint, strict-validation, and configuration-disabling options. Explicit environment targeting is also supported by the [official environment documentation](https://docs.astral.sh/uv/pip/environments/) and [CLI reference](https://docs.astral.sh/uv/reference/cli/). The locally installed pinned MLX-LM source confirms prior-model release and the named launch flags. No runtime installation, inference, recruitment, or future C contract was executed; reconstruction and live acceptance remain gated implementation checks.

## Findings

### Critical

None.

### Major

- **M1 — [Correctness / Test Coverage] Hold the shared lease during Keep/Apply activation.** Phase 4, plan line 154, changes profile application into an awaited managed launch but does not require acquiring `OperationKind.RESTARTING`. Both existing callers only check whether work is busy (`src/mlx_tui/compare_pane.py:892`, `:953`); the shared application method owns no lease (`src/mlx_tui/app/__init__.py:215`). Chat admits work using that lease (`src/mlx_tui/chat_pane.py:140`). Once activation yields for a stop/start, chat or another operation can enter against a server being replaced. The manager's start/stop serialization does not serialize chat or comparison. Require the shared activation entry point to acquire the existing lease before yielding, freeze the target/fingerprint, and release only after startup or failure cleanup and settings/state updates. Add a delayed-activation check proving chat, Run, and repeated Apply cannot enter; retain the already-specified saved-choice-on-failure behavior.

- **M2 — [Test Coverage / API/Compatibility] Bind live qualification to the final wheel.** Phase 5, plan lines 177–181, records an artifact hash but runs `uv run pytest` against the project environment, whose package source is editable (`uv.lock:217`). That can qualify checkout code while attributing the result to different wheel bytes. The existing outside-checkout smoke only exercises help, imports, and packaged profiles (`tests/artifact_smoke.py:97`); the planned managed installed-artifact smoke is optional. Require a final build after the implementation phases, install that retained wheel into an isolated qualification environment, and run the live contracts outside the checkout against that installation. Assert the imported package path and distribution version, retain the actual wheel SHA, and deliver those same bytes. The Phase 1 wheel check can remain an intermediate packaging checkpoint.

### Minor

- **m1 — [Plan Mechanics / Architecture & Patterns] Define asset validation before its first consumer.** Phase 3 requires verifying target assets before stopping the previous child (plan line 120), but the common completeness validator and tests are introduced in Phase 4 (line 146). Move their implementation into Phase 3 and keep comparison integration in Phase 4. Also clarify the division of responsibility: `verify_cached_assets(snapshot)` can check generic completeness, while `verify_profile_snapshot(entry, snapshot)` already obtains profile-specific asset names/hashes from the entry (`src/mlx_tui/comparison_runner.py:134`). Avoid requiring the path-only helper to infer a profile's tokenizer/template requirements.

- **m2 — [Security / Plan Mechanics] Specify ownership evidence for an incomplete installation.** Phase 2, plan line 94, specifies a completion marker written only after success, requires ownership evidence before repair, and promises repair after cancellation/restart. It does not define the ownership evidence that survives a first installation interrupted before completion. Define a small ownership record before provisioning, outside the venv that repair may rebuild, distinct from readiness. Keep the install lock outside the replaced directory as well. Add a check that cancels before completion, constructs a fresh installer instance, and repairs the owned partial directory while refusing an unrelated unmarked directory. This fills an underspecified recovery step; it does not call for another installer abstraction.

### Suggestions

None beyond the targeted changes above.

## Strengths

- Milestone B qualification, physical tiers, owner confirmation, and user observations remain explicit gates; implementation is not presented as product acceptance.
- The concrete manager retains the actual child, rejects foreign listeners, handles startup/shutdown races, and tests foreign-process survival without introducing a serving wrapper.
- The plan reuses the existing operation coordinator, pinned downloads, comparison runner, and persistence patterns.
- Comparison request count, uncertainty labels, and saved-choice failure semantics remain intact.
- Installed runtime inspection, offline cached startup, cancellation, and real fresh-install observations are separately specified rather than inferred from stub tests.

## Recommended Changes

1. Add shared-lease acquisition and a delayed-activation regression check to the Keep/Apply contract (M1).
2. Make final installed-wheel qualification mandatory and tie its evidence to the delivered SHA (M2).
3. Move completeness validation into Phase 3 and separate generic checks from profile-specific hashes (m1).
4. Define persistent ownership evidence and a stable external lock for interrupted-install repair (m2).

The plan and its Draft status remain unchanged. These are proposed edits for the user to select under the review-plan workflow; no findings have yet been accepted as tradeoffs or resolved.
