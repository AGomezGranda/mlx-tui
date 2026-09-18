# Remove Development Scaffolding and Historical Material

**Date:** 2026-09-17
**Work Item:** n/a
**Status:** Complete

## Overview
Keep the application, its unit/integration coverage, necessary packaging and configuration, and current user/developer documentation. Remove historical plans, reviews, milestone evidence, the opt-in runtime suite, and release bookkeeping that served the build process rather than everyday maintenance.

## Current State
- `src/mlx_tui/coding_profiles.toml:22` and `:63` embed dated milestone endorsements and references into `docs/compatibility/`. `src/mlx_tui/profiles.py:143` already represents absent evidence as `unknown`; no new runtime fallback is needed.
- `tests/unit/test_profiles.py:17` expects the packaged first profile to be qualified at the current wall-clock time. Its evidence expires on September 20, 2026. Other tests in that file mutate or index the packaged evidence directly.
- `src/mlx_tui/comparison_summary.py:93` prevents an advantage recommendation without qualified profile evidence. Clearing the bundled endorsements therefore changes the catalogue status and new conclusions, but does not prevent running comparisons or keeping a choice.
- `src/mlx_tui/comparison_persistence.py:246` reads evidence frozen into saved comparisons; `:369` reads the stored task ID. Retain these readers and schemas so previously saved results still open.
- `src/mlx_tui/comparison_contracts.py:142` gives new comparisons a milestone-specific task ID. The experiment itself is `coding-check-v1`.
- `src/mlx_tui/managed.py:46`, `:283`, and `:498` consume the packaged runtime freeze and build constraints. These `.txt` files are application inputs, not disposable notes. Runtime resource hashes are also recorded at `:329`.
- `src/mlx_tui/managed.py:159` enforces Darwin arm64, macOS 26.6.2, uv 0.12.7, and Python 3.13.1. Removing historical support documents must not imply broader runtime support.
- `tests/runtime/` contains a shared recorder and five opt-in milestone modules, including external OpenCode qualification and installed-artifact exercises. The user explicitly chose to delete this entire suite, accepting the loss of live-server regression coverage.
- `tests/artifact_smoke.py:97–240` checks actual package contents and installation. Its `:243–606` helpers retain historical artifacts and exercise predecessor upgrades; `main()` at `:609` exposes those optional modes. CI calls only the default smoke command.
- `.github/workflows/ci.yml:12` runs locked checks on Linux/macOS. Separate dependency-highest and dependency-floor jobs start at `:51` and `:75`; packaging starts at `:103`.
- Historical material includes `docs/idea.md`, `docs/part2.md`, `docs/part3.md`, 25 existing plans, 21 reviews, `docs/compatibility/` with committed wheels/sdists/logs/JSON, `todo`, and `report-source.md`. The last release plan is still labelled In Progress; this cleanup intentionally retires its remaining qualification/recruitment work, rather than claiming it was completed.
- Current documentation to retain: `README.md`, `ARCHITECTURE.md`, `docs/activation.md`, `docs/comparison.md`, and `docs/clients.md`. These currently mix user instructions with milestone gates and historical links.

### Research verification
Commands are run from the repository root, with the required `rtk` prefix:

| Command | Baseline |
| --- | --- |
| `rtk proxy uv run ruff check .` | Passed |
| `rtk proxy uv run ruff format --check src tests` | Passed; 92 files |
| `rtk proxy uv run pyrefly check --min-severity warn` | Passed; 0 diagnostics, 296 suppressed |
| `rtk proxy uv run pytest -q tests/unit/test_profiles.py tests/unit/test_comparison.py tests/unit/test_comparison_summary.py` | Passed; 27 cases |
| `rtk proxy uv run python tests/artifact_smoke.py` | Passed; wheel and rebuilt sdist installed and checked |
| `rtk proxy uv run pytest -q` | Passed; 19 opt-in cases skipped, two existing `os.fork()` deprecation warnings in session-lock tests |
| `rtk git diff --check` | Passed before drafting |

No real-server qualification, model download, or managed installation is required for this cleanup. Stub tests do not establish real-server compatibility.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Delete historical documentation; retain current guides | Preserve everything, move it into another archive, delete it | User chose deletion. Git history already retains the tracked material; do not create another archive. |
| Delete all of `tests/runtime/` | Rename/simplify live tests, delete the whole opt-in suite | User explicitly chose deletion after the coverage tradeoff was explained. Keep ordinary unit/integration and package checks. |
| Remove bundled evidence, retain profile definitions and saved-evidence readers | Preserve stale references, move evidence into package resources, remove endorsements | `unknown` is already supported. Preserve actual profile settings, fingerprints, validation, and saved data. |
| Keep runtime resource files byte-for-byte | Delete `.txt` files, edit their contents, keep application inputs | They install/verify the runtime and contribute to identity hashes. Dependency pruning or even header cleanup belongs to a separate runtime revision. |
| Keep locked CI and basic package smoke checks | Keep the full dependency matrix, remove all CI, simplify CI | User accepted removing highest/floor jobs. Necessary lint, type, behavior, and packaging checks remain. |
| Preserve runtime restrictions and user-data compatibility | Relax platform pins/remove aliases, leave behavior intact | Those are active contracts, not development scaffolding. Avoid coupling cleanup to runtime upgrades or migrations. |
| Keep this cleanup plan while executing it | Delete every plan immediately, retain only the active plan | It is the current implementation checklist. Delete other plans explicitly; never recursively delete this plan's directory. |

## Implementation Phases

### Phase 1: Detach the app and tests from historical endorsements
Remove historical catalogue data using the existing no-evidence behavior, and keep tests independent of a real endorsement's expiry date.

**Changes:**
- `src/mlx_tui/coding_profiles.toml` — delete both `[[profiles.evidence]]` blocks. Keep every profile field, revision, asset hash, runtime pin, and request setting unchanged. Both entries should load with an empty evidence tuple and display `unknown` for every tier.
- `tests/unit/test_profiles.py` — change the packaged-shortlist test to assert empty evidence and `unknown`. Keep revision/boolean validation against the actual catalogue. Move malformed timestamp, expiry-boundary, revocation, and qualified-evidence behavior to synthetic evidence declared inside this test file with explicit dates and `now` arguments. Exercise TOML parsing for invalid timestamp/revocation cases, not only direct dataclass construction; reuse the loaded profile fingerprint when composing synthetic evidence. Do not create a general fixture framework or keep a copy of the historical endorsement.
- `src/mlx_tui/comparison_contracts.py` — change only `ComparisonInput.task_id`'s default from `milestone-b-coding-check` to `coding-check-v1`. Keep the prompt, `check_id`, result schema, and stored-value reader unchanged.
- `tests/unit/test_comparison_summary.py` — use the neutral task ID in `_qualified_entry`; retain synthetic qualified evidence and the test proving missing catalogue evidence blocks an advantage recommendation.
- `tests/unit/test_comparison.py` — extend existing serialization coverage to verify a new input uses the neutral default and an explicitly supplied historical task ID survives encoding/decoding unchanged. Include synthetic stored evidence in the round trip so removal from the catalogue does not accidentally eliminate coverage for saved endorsements.
- `README.md` — replace the statement that packaged candidates show dated endorsements with the current behavior: pinned candidates remain available, bundled qualification evidence is absent.
- `docs/comparison.md` — state that bundled profiles now have unknown evidence; comparisons, inspection, and explicit choices still work, but missing evidence keeps advantage conclusions inconclusive. Saved results retain their original facts.

**Success Criteria:**

#### Automated Verification:
- [x] Profile validation, summary eligibility, and persistence tests pass: `rtk proxy uv run pytest -q tests/unit/test_profiles.py tests/unit/test_comparison.py tests/unit/test_comparison_summary.py`.
- [x] Lint passes: `rtk proxy uv run ruff check .`.
- [x] Formatting passes: `rtk proxy uv run ruff format --check src tests`.
- [x] Type checking passes: `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] In Compare, both candidates remain available and display unknown evidence without a catalogue error.
- [ ] The profile definitions and runtime resource bytes are unchanged apart from removal of evidence blocks; no saved user data is edited.
- [ ] Documentation makes the effect on new advantage conclusions explicit.

### Phase 2: Remove historical material and keep current documentation
Delete the approved obsolete material and replace its references with concise descriptions of the application as it exists.

**Changes:**
- `tests/runtime/conftest.py`, `tests/runtime/test_milestone_a.py`, `tests/runtime/test_milestone_b.py`, `tests/runtime/test_milestone_c.py`, `tests/runtime/test_milestone_e.py`, `tests/runtime/test_milestone_f.py` — delete all six files. Do not replace them with renamed suites or stub duplicates.
- `docs/compatibility/` — delete the entire tracked directory, including milestone A–F records, `milestone-a-runtime.txt`, evidence JSON/logs, manifests, wheels, and source archives. The packaged runtime freeze under `src/` stays intact.
- `docs/reviews/` — delete the entire tracked directory.
- `docs/plans/` — delete all 25 plans present before this cleanup plan was created. Preserve `2026-09-17-repository-cleanup.md` as the active checklist. If another plan appears after this baseline, inspect it rather than sweeping it into this deletion.
- `docs/idea.md`, `docs/part2.md`, `docs/part3.md`, `todo`, `report-source.md` — delete development proposals, roadmap notes, and the historical source review. Do not promote unfinished ideas into new requirements.
- `README.md` — remove milestone/recruitment/qualification language, fixed historical wheel hashes and evidence links, and the entire final-installed-qualification recipe. Keep quick start, feature descriptions, commands, config, diagnostics, storage/ownership behavior, and current limitations. Refer to the retained guides for detailed installation and client behavior.
- `docs/activation.md` — keep current installation, manual backup/upgrade/rollback, diagnostics, runtime inputs, and first-run ownership instructions. Remove candidate/predecessor exercise reports, hardcoded historical artifact hashes, `--retain-dir`/`--upgrade-from` recipes, live qualification commands, and recruitment references. Describe the exact enforced platform/runtime restrictions as current implementation constraints. Keep operator backups and restoration explicitly separate from ordinary installation.
- `docs/clients.md` — replace the milestone-stage taxonomy and disposable OpenCode qualification configuration with a short guide to the existing endpoint preview, its HTTP/curl request shape, and local-client limitations. Preserve the facts that there is no cross-client model/lifecycle coordination, an external client may change the server target, and managed shutdown stops its child. Do not claim newly supported shared serving or add an untested replacement client setup.
- `docs/comparison.md` — remove the Milestone B wording/link and describe limitations directly; retain the actual workflow, measurements, evidence rules, and storage instructions.
- `ARCHITECTURE.md` — remove milestone D/A links and live-suite instructions. Keep application ownership and storage contracts; explain that automated behavior coverage now uses the local HTTP stub, supplemented by installed-package smoke checks.
- `.gitignore` — add `.runtime-evidence/` so any local output from previous qualification runs stays untracked. Do not delete ignored local evidence, local `dist/` artifacts, environments, caches, or user state.

**Success Criteria:**

#### Automated Verification:
- [x] Remaining unit/integration suite passes: `rtk proxy uv run pytest -q`.
- [x] Packaging still builds and installs without the historical tree: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Lint and type checks pass: `rtk proxy uv run ruff check .` and `rtk proxy uv run pyrefly check --min-severity warn`.
- [x] Patch has no whitespace errors: `rtk git diff --check`.

#### Manual Verification:
- [ ] Review remaining Markdown links in the five retained documents; none points to deleted compatibility records, reviews, plans, or runtime tests.
- [ ] Read installation and client guides as a new user: no release milestones, specific-machine exercise setup, or deleted evidence is required to follow them.
- [ ] The Git diff deletes only the listed historical material; ordinary unit/integration tests, license, lockfile, project metadata, and runtime resources remain.
- [ ] Documented managed restrictions match the unchanged `_preflight` implementation; removal of evidence is not presented as wider compatibility.

### Phase 3: Reduce release tooling to routine package verification
Keep the checks needed to ship the app and remove the optional historical artifact-management path.

**Changes:**
- `tests/artifact_smoke.py` — keep `_project_version`, package/license/resource inspection, wheel/sdist builds, rebuilding the sdist, isolated installations outside the checkout, import-path checks, `--help`, `--version`, diagnostics, and packaged-profile checks.
- `tests/artifact_smoke.py` — delete `_sha256`, `_source_provenance`, `_retain_artifacts`, `_tree_digest`, `_upgrade_env`, `_tool_python`, `_install_tool`, `_run_tool_python`, and `_run_upgrade`; remove `_run_status`, which only serves upgrade installation. Remove argparse flags/branches for retention and upgrades from `main()` plus unused `argparse`, `hashlib`, `platform`, and `PYTHON_VERSION`.
- `tests/artifact_smoke.py` — change `_install_and_smoke` to return `None`; remove the final dependency-version JSON subprocess and `SMOKE_PACKAGES`, since their only consumer was retained manifests. In `main()`, call both install checks without collecting versions. Keep the final success message. Do not add a replacement release CLI or new dependencies.
- `.github/workflows/ci.yml` — remove entire `compat-highest` and `compat-floor` jobs. Keep the existing Ubuntu/macOS locked checks matrix, read-only permissions, timeouts, Linux packaging job, and macOS artifact check. Keep the commands aligned with local verification.
- `ARCHITECTURE.md` — update Verification to describe only the remaining locked CI matrix and wheel/sdist smoke checks. Add the existing `uv run python tests/artifact_smoke.py` command beside other developer checks. Do not imply that declared dependency floors or newest resolutions are still tested.

**Success Criteria:**

#### Automated Verification:
- [x] Simplified package smoke passes for wheel and rebuilt sdist: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Full remaining suite passes: `rtk proxy uv run pytest -q`.
- [x] Lint passes: `rtk proxy uv run ruff check .`.
- [x] Formatting passes: `rtk proxy uv run ruff format --check src tests`.
- [x] Type checking passes: `rtk proxy uv run pyrefly check --min-severity warn`.
- [x] Final diff has no whitespace errors: `rtk git diff --check`.

#### Manual Verification:
- [ ] The smoke script no longer accepts release-retention/upgrade flags or writes permanent repository evidence; its temporary installations still exercise the installed package.
- [ ] Workflow review confirms only the locked checks matrix and packaging job remain. Hosted CI execution must be verified during implementation when a CI run is available; local success is not a hosted-run result.
- [ ] Final repository review finds current app code, meaningful tests, necessary tool/runtime configuration, current guides, and this active plan, with no replacement archive or new cleanup framework.

## Out of Scope
- Implementing this plan during the planning task; the document remains Draft until explicit approval.
- Broad architectural refactors, private-API cleanup, dependency upgrades, UI redesign, or changing the Compare experiment.
- Relaxing OS/uv/Python pins, changing the managed runtime freeze (including its test-related packages), removing its build constraints, or changing its versioned directory identity.
- Removing user-config aliases, stored session/comparison schema support, persistence validation, process ownership checks, or diagnostics.
- Deleting the two profile definitions or changing their asset hashes/fingerprints.
- Git-history rewriting, deleting ignored local files, moving historical material into another repository, or publishing a release.
- Adding new live tests, replacing removed dependency-matrix jobs, or creating new documentation/testing frameworks.

## Risks & Mitigations
- Deleted documents are referenced by packaged endorsements → remove those endorsements first; preserve validated profile definitions and saved-evidence readers.
- Removing endorsements changes recommendation eligibility → explicitly document unknown status and retain the fail-closed summary test. Do not manufacture replacement qualification data.
- Historical task IDs exist in saved JSON → change only the default for new comparisons and verify stored values round-trip unchanged.
- Profile tests currently depend on a real expiry date → synthetic evidence with explicit timestamps preserves validation coverage without a calendar deadline.
- Live coverage and dependency-range coverage are lost → this is the user's accepted scope; keep unit/integration, locked CI, and installed-artifact checks and state their limits honestly.
- Runtime files look like scaffolding but define install identity → preserve their bytes and all related version/ownership checks. Pruning the frozen environment requires a separately verified runtime revision.
- Removing the old release plan could look like completing its unfulfilled gates → retire the plan and its claims, without marking its qualification/adoption work complete.
- Bulk deletion could remove the executing plan or local data → delete tracked baseline paths explicitly, preserve this checklist, and never use a broad workspace cleanup command.
