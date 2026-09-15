# Unified comparison pane and user guide

**Date:** 2026-09-10
**Work Item:** n/a
**Status:** In Progress

## Overview

Give the Milestone B comparison workflow a dedicated Compare tab: set up two pinned profiles, check readiness, run, inspect evidence, and explicitly save or reuse a choice. Reuse the existing runner and persistence contracts, improve the results presentation, and document the complete user journey in `docs/comparison.md`.

The user agreed to the dedicated tab and the three-phase outline on 2026-09-10. This agreement establishes the design direction; the detailed plan remains Draft pending review. The original request mentioned three concerns but described two; this plan covers consolidation/UX and documentation without inventing another requirement.

## Current State

References describe the uncommitted working tree inspected on 2026-09-10; recheck line positions before implementation.

- `src/mlx_tui/app/__init__.py:140` loads profiles and owns the selected pair, latest result, saved choice, and active/modified profile state. `_load_saved_choice()` restores the committed choice's result but never applies it automatically.
- `src/mlx_tui/app/__init__.py:187` swaps the other candidate when selecting a duplicate, maintaining a distinct pair. `refresh_profile_views()` currently refreshes Models and Metrics.
- `src/mlx_tui/app/__init__.py:214` implements `apply_coding_profile(profile_id, *, expected_fingerprint=None)`: validate catalogue fingerprint, resolve/verify pinned assets, then update model/config/Params. Ordinary changes mark the active profile modified.
- `src/mlx_tui/app/__init__.py:260` mounts Models, Chat, and Metrics. Comparison shutdown, busy state, Escape, and action availability refer to `MetricsPane` at lines 310, 602, 735, and 757. Metrics polling/history refresh must remain routed to Metrics.
- `src/mlx_tui/models_pane.py:56` contains the coding-profile selector and evidence summary. `_on_profile_selected()` at line 114 changes candidate A and selects the cached snapshot as the ordinary request target. Evidence assumes `local-m4-16gib`.
- `src/mlx_tui/metrics_pane.py:84` composes a collapsed comparison form ahead of the ordinary history graphs/table. This class owns preflight, the worker/task cancellation lifecycle, decisions, and reuse. `_result_text()` at line 213 displays counts and conclusion labels, not the A/B measurements.
- `src/mlx_tui/metrics_pane.py:445` commits the choice and applies the profile inside one error handler. An application failure after a successful commit can incorrectly report that the choice was not saved.
- `src/mlx_tui/comparison.py:148` defines `ComparisonInput`; `TrialResult` at line 181 contains payload, answer/reasoning, quality result, timings, token counts, RSS samples, and process identity. `ComparisonResult` at line 225 includes immutable profile snapshots and the run ID.
- `src/mlx_tui/comparison.py:948` provides strict `load_comparison(path)`. `commit_choice()` at line 1056 uses the existing pending-run → choice → finalized-run transaction; retain/reject write a decision without a profile, and reject requires a reason. These decisions replace the single saved choice, including an earlier kept profile.
- `src/mlx_tui/comparison.py:1249` summarizes first requests separately from five repeats. Repeat timing statistics include passing repeats only. Memory is always explicitly inconclusive in this implementation. The `repeats.attempted` value currently counts completed repeat slots, so the UI must derive attempted counts from trial states.
- `src/mlx_tui/comparison.py:1408` assigns ascending medians to `slower, faster`, then computes `(slower - faster) / slower`; this produces a nonpositive fraction and prevents the intended advantage label. The existing guard conditions and 5% product heuristic remain appropriate scope boundaries.
- `src/mlx_tui/comparison.py:1452` runs twelve sequential slots and checkpoints before progress notifications. An attempted slot identifies the active request; no extra timer or streaming controller is needed to show progress.
- `tests/integration/test_metrics_integration.py:190` contains three comparison integration tests and their local helpers. They cover Keep/next-chat settings, pair ownership/poll suppression, and Escape/checkpoints. They mostly call handlers directly; add a real keyboard journey rather than treating them as layout coverage.
- `README.md:105` describes the current Metrics flow; `ARCHITECTURE.md:198` explains implementation ownership. `docs/part3.md:151` and its comparison pillar still prescribe Metrics. `docs/compatibility/milestone-b.md:7` records that live qualification and the product gate remain unmet.

Planning verification: the focused comparison/profile/Metrics suite passed (24 tests); the full ordinary suite passed with **528 passed, 8 skipped in 143.73 seconds** using `rtk proxy uv run pytest -q`. `rtk proxy uv run ruff check .` passed; `rtk proxy uv run ruff format --check src tests` reported 68 files already formatted; `rtk proxy uv run pyrefly check` reported zero errors, with 185 existing suppressions and 6 warnings not shown. These checks establish a baseline, not verification of the proposed changes. No live inference or qualification is authorized by this plan.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Add Compare between Models and Chat; keep Models as the startup tab | Dedicated tab; full-screen wizard; existing Metrics section | The user accepted a dedicated tab. It keeps setup/results accessible while retaining the familiar app shell. |
| One scrollable pane with progressive disclosure | Separate wizard screens; nested tab hierarchy | Native Textual containers, DataTable, Select, Input, and Collapsible cover the flow without new dependencies. |
| Keep shared application state and extract the existing UI worker into `ComparePane` | New controller/service layer; pane-to-pane calls | The current coordinator already owns state needed by Chat and status. Move UI ownership without rebuilding the runner. |
| Split comparison code at existing responsibility seams behind the current import surface | Keep the 1,594-line module; introduce a controller/framework; convert to a package | Small sibling modules for contracts, persistence, summary, execution, and presentation make ownership visible without adding abstractions. Keep `mlx_tui.comparison` as the public compatibility facade. |
| Candidate selection affects only the comparison | Retain Models' selector and its request-target side effect | Ordinary model selection belongs to Models; applying tested settings is an explicit Compare action. |
| Retain explicit Check readiness then Run comparison | Automatically run after validation | Users can inspect blockers and conditions before the twelve requests. Rebuild/validate input when Run is pressed. |
| Show unknown evidence honestly; remove the hardcoded machine-tier default | Assume this developer's machine; infer a tier from RAM | Leave the tier blank initially, display unknown, and record it as unknown if omitted. A runnable experiment is distinct from qualified evidence. |
| Decisions refer to the displayed result's frozen profiles | Read the current setup selectors when keeping A/B | Editing the next experiment must never change what Keep A or Keep B means. Include names and run ID in result/decision labels. |
| Keep saves and applies; navigation to Chat is separate | Automatically navigate or automatically pick the faster profile | Preserve the existing explicit choice semantics and the user's focus. |
| Reopen results by an explicit file path | Full history browser; only the latest committed choice | `load_comparison()` already supports orphan/partial results. A path field and Open result action make checkpoints usable without adding a history subsystem. |
| Fix the latency fraction without changing schema or eligibility | Redesign scoring; leave the known bug | Correct the arithmetic and test both orders; never turn unqualified candidates into recommendations. |
| User guide in `docs/comparison.md`; concise entry points elsewhere | More README paragraphs; duplicate architecture narrative | One discoverable operational guide explains the new design while architecture remains technical. |

### User flow and layout contract

```text
[ Models ] [ Compare ] [ Chat ] [ Metrics ]

Compare coding profiles
Fixed coding-check-v1 · first request + 5 repeats per profile
A [profile]                      B [profile]
Pinned revision · cache status · evidence status
Order: A then B
▸ Task and exact settings
▸ Runtime evidence and test conditions
[Check readiness] [Run comparison] [Cancel]
Readiness / current profile and trial / terminal outcome

Result: <run ID> · <tested A name> then <tested B name>
Measurement                      A                   B
First-request total              —                   —
Passing repeats                  — / 5               — / 5
Repeat total median [range]      —                   —
Latency: <stored conclusion and explanation>
Memory: inconclusive · process RSS is not per-profile residency
▸ Individual trials and failures

Reason [...]
[Keep A: <name>] [Keep B: <name>]
[Keep current settings] [Reject both]
Saved choice: <decision and profile, or none>
Active profile: <name, modified marker, or none>
[Apply saved profile] [Go to Chat]
▸ Open saved result: [path] [Open result]
```

This is a content/order sketch, not a fixed pixel grid. At 80×24, stack candidate fields and decision buttons vertically, retain visible labels, and let the pane scroll. At 120×40, use compact horizontal rows where they fit. Keep the existing status bar, Activity, and footer. No horizontal page scrolling or clipped action labels; long revisions/paths remain readable in expanded details. Preserve visible keyboard focus and do not steal it during progress or completion.

1. **Setup:** show profile names/IDs, pinned revision, cache availability, and `ProfileEntry.status()` for the entered tier. A missing/broken catalogue displays its actual error and disables setup/run, while saved-result inspection remains available. Details show the exact task prompt and settings; comparison profiles are not editable here.
2. **Readiness:** check distinct profiles, loopback endpoint syntax, cached pinned assets, and process association using existing helpers. Do not describe this as proof that the runtime applied all settings. Unknown provenance is allowed but its effect on conclusions is shown. Reuse the pinned SearchScreen for explicit downloads and the existing app cold-start action for configured server recovery; label the latter Start server, since it is not a general restart operation.
3. **Run:** capture current request state and acquire the existing comparison lease. Disable setup, evidence edits, decisions, result opening, and profile application during comparison or another conflicting operation. Run becomes available only after successful readiness and while idle. Any setup/evidence edit, download, or server-start action invalidates readiness; editing the decision reason does not. Display the attempted slot's profile and first request/repeat number alongside overall completion counts. Tab navigation remains possible; Escape cancellation and shutdown cleanup retain their existing guarantees.
4. **Review:** display first-request timing, passing-repeat counts, median/range, and conclusion reasons. Count attempted/completed/passing separately from `TrialResult` states; absent data is unknown, never zero. Render a trial table with profile, first/repeat index, state, quality, and timings; selecting a row shows error/quality reason, answer/reasoning, response identity, cached tokens, and observed RSS sample range/count. Label that range sampled process RSS, never engine peak or per-profile memory. Render model/operator text literally, not as Rich markup or executable content.
5. **Choose:** allow decisions only for a completed result while idle. Keep refers to its frozen profile/fingerprint, saves first, then applies. If persistence fails, preserve current settings; if application fails after commit, report “Choice saved; profile not applied” with the actual reason and keep the saved choice visible. Keep current settings maps to existing `retain`; Reject both maps to `reject` and requires a nonblank reason. Explain that either records a decision without a reusable profile and replaces the prior saved choice; neither restores a historical baseline or changes current settings.
6. **Reuse:** startup displays the committed saved choice without applying it. Apply saved profile revalidates fingerprint/assets using the existing application method. Go to Chat only changes the tab and focuses the composer; it sends nothing and preserves the draft/history. Opening a result validates it first, then changes the displayed/latest result without changing the saved choice, current setup, or active settings. Failed loading preserves the currently displayed result. Loaded partial results are inspectable but not decidable; an on-disk running record displays interrupted through the existing decoder. Opening alone never rewrites a file or repairs a pending decision.

## Implementation Phases

### Phase 1: Split the headless comparison module without changing behavior

Separate the existing responsibilities before changing the UI. Keep `mlx_tui.comparison` as the stable import surface used by the app and runtime qualification; this phase is a mechanical move with no schema, scoring, runner-order, or transaction changes.

**Changes:**
- `src/mlx_tui/comparison_contracts.py` — **new file**, move the comparison constants, JSON/status/decision types, validation errors, endpoint parsing, and immutable input/result/trial/choice records. Keep validation in the records and endpoint parser; do not add interfaces or wrapper classes.
- `src/mlx_tui/comparison_persistence.py` — **new file**, move strict schema-v1 encoding/decoding, state paths, atomic checkpoint/choice storage, committed-choice validation, and the pending-run → choice → finalized-run transaction. Keep the transaction and rollback behavior unchanged.
- `src/mlx_tui/comparison_summary.py` — **new file**, move summary construction, unknown-evidence checks, and latency/memory conclusions. Do not fix the known latency arithmetic in this mechanical phase.
- `src/mlx_tui/comparison_runner.py` — **new file**, move payload/quality validation, pinned-snapshot verification, process sampling, trial transitions, and the sequential runner. Keep checkpoint-before-notify/request and cancellation behavior unchanged.
- `src/mlx_tui/comparison.py` — reduce to a documented compatibility facade that re-exports the public comparison API used by `src/mlx_tui/app/__init__.py`, UI code, and `tests/runtime/test_milestone_b.py`. Do not re-export private helpers solely to preserve tests.
- `tests/unit/test_comparison.py` and `tests/runtime/test_milestone_b.py` — keep behavior coverage and public imports; retarget private monkeypatches/helpers to the module that defines them. Add one import-surface check for the facade names used by production and qualification code.

**Success Criteria:**

#### Automated Verification:
- [x] Existing codec, choice transaction, runner ordering, cancellation, identity, and qualification tests pass unchanged in behavior: `rtk proxy uv run pytest -q`.
- [x] No import cycles or stale/private facade imports: `rtk proxy uv run ruff check .` and `rtk proxy uv run pyrefly check`.
- [x] Formatting remains stable: `rtk proxy uv run ruff format --check src tests`.

#### Manual Verification:
- [ ] None beyond confirming the app starts; this phase intentionally has no user-visible change.

### Phase 2: Consolidate pane ownership and navigation

Move the existing working flow into its own pane before redesigning its contents. This checkpoint remains functional with the current comparison form.

**Changes:**
- `src/mlx_tui/compare_pane.py` — **new file**, define `ComparePane(VerticalScroll)` using the comparison imports, CSS, fields, compose content, and methods currently in MetricsPane. Preserve `refresh_profile_state()`, `has_live_comparison`, `cancel_requested`, `abort()`, `wait_for_cleanup()`, and `set_comparison_busy(busy: bool)` as the app-facing API. Keep the current comparison widget IDs to minimize migration risk. Do not subclass MetricsPane or duplicate worker logic.
- `src/mlx_tui/metrics_pane.py` — remove the comparison form, fields, imports, worker, and decision handlers. Retain `refresh_metrics()`, both sparkline renderers, the history DataTable, and their mounting logic.
- `src/mlx_tui/app/__init__.py` — import/mount ComparePane in `TabPane("Compare", id="compare")` with `id="compare-pane"`. Route profile refresh, comparison busy state, Escape, action availability, and unmount cleanup to it; keep all ordinary metrics refresh calls on MetricsPane. Retain application-owned profile/result/choice state and existing lease/poll behavior.
- `src/mlx_tui/models_pane.py` — delete the coding-profile selector, its details, handlers, and now-unused imports. Preserve the cache table, load/delete/download flow, snapshot identity protection, and ordinary selection behavior. Display migrated candidate/evidence information in ComparePane.
- `tests/integration/test_compare_integration.py` — **new file**, move the existing comparison helpers/tests from `tests/integration/test_metrics_integration.py`, then retarget them and their monkeypatch paths from MetricsPane/`mlx_tui.metrics_pane` to ComparePane/`mlx_tui.compare_pane`. Keep shared app fixtures where they are; do not add a comparison test framework.
- `tests/integration/test_metrics_integration.py` — retain only Metrics history, sparkline, and table coverage after the comparison tests move.
- `tests/integration/test_app_integration.py` — include Compare in tab/activity navigation coverage. Assert the four tab IDs/order, only Compare contains comparison controls, and candidate changes leave the ordinary selected target, config, Params, and chat draft/history unchanged.

**Success Criteria:**

#### Automated Verification:
- [x] Existing comparison cancellation, saved-choice reuse, exact next-chat settings, lease exclusion, poll suppression, and single cleanup refresh pass after extraction: `rtk proxy uv run pytest -q`.
- [x] No stale pane imports or formatting issues: `rtk proxy uv run ruff check .` and `rtk proxy uv run ruff format --check src tests`.
- [x] App/pane API ownership remains type-correct: `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] Models still supports cache browsing and ordinary selection; Compare owns the complete old form; Chat and Metrics remain usable.
- [ ] Change the candidate order, leave Compare, return, and verify the order persists without changing the Chat target.
- [ ] Start a stub comparison, switch tabs, cancel with Escape, and quit during another run; cleanup remains functional and no later trial starts after cancellation.

### Phase 3: Improve setup, results, and decision feedback

Implement the flow/layout contract above using the extracted pane and focused comparison modules. Keep each change within its named ownership boundary and finish with runnable behavior checks rather than adding a generic wizard, controller, or result-model layer.

**Changes:**
- `src/mlx_tui/compare_pane.py` — replace the outer collapsed comparison form with visible setup/run/results/choice sections. Put task/settings, evidence/conditions, trial inspection, and result opening in separate native Collapsibles. Add responsive styles using the pane's width/resize handling; verify exact Textual event/CSS details during implementation against the installed version. Keep this module responsible for Textual composition, widget/event state, and the existing worker/cancellation lifetime.
- `src/mlx_tui/comparison_presenter.py` — **new file**, add pure builders for progress text, A/B measurement rows, trial rows, and selected-trial details from immutable comparison records. Return literal `Text`/row values for the pane to display; do not query widgets, mutate app state, or introduce a view-model class.
- `src/mlx_tui/compare_pane.py` — keep the two candidate selectors, show cache/evidence details, remove the assumed machine-tier value, and pass `unknown` for an omitted tier. Keep catalogue evidence separate from runtime verification and the local saved choice. Avoid expensive asset hashing on every Input.Changed event; use readiness/run validation for hashes.
- `src/mlx_tui/compare_pane.py` — relabel Preflight/Compare to Check readiness/Run comparison. In `_build_comparison_input()`, validate the endpoint with `parse_loopback_url()` before describing readiness; construct bracketed URLs for supported IPv6 loopback hosts. Keep all existing pinned-asset/process checks. Change broad input invalidation so only setup/evidence fields reset readiness; invalidate around Download/Start server and rebuild the input on Run.
- `src/mlx_tui/compare_pane.py` and `src/mlx_tui/app/__init__.py` — have `set_comparison_busy()` also account for the coordinator's busy state, readiness, result status, and saved-choice availability when enabling controls. Cancel is enabled only for this pane's live, not-yet-cancelled comparison. Guard decision/application/open-result handlers against busy state, including direct invocation. Preserve the existing worker/task and pre-start cancellation behavior.
- `src/mlx_tui/compare_pane.py` and `src/mlx_tui/comparison_presenter.py` — use `_on_progress()` and the attempted trial to display current profile/first-or-repeat index. Add the compact A/B table and trial table/details through the pure presentation builders. Retain terminal status, error, and result path; clear stale prior result/decision presentation when a new run begins. Derive attempted counts directly from trial states, render result names/order from `result.comparison.profiles`, and label missing/measured/estimated values honestly. Do not add polling requests, per-token UI updates, another worker, engine timings, peaks, or a memory winner.
- `src/mlx_tui/comparison_summary.py` — in the moved latency summary, assign ascending medians to `faster, slower`, then compute `(slower - faster) / slower` after guarding zero slower time. Preserve every eligibility condition, the threshold, the existing summary schema, and its heuristic disclaimer. Do not rewrite historical summaries or choice transactions on load.
- `tests/unit/test_comparison_summary.py` — **new file**, add a focused parametrized regression using eligible synthetic result metadata: medians 1 and 2 seconds in either order give 0.5 and `advantage_possible`; equal positive medians give zero/inconclusive; two zeros remain inconclusive; a sub-5% difference stays inconclusive; unqualified evidence still blocks the label even with a large timing difference. Use existing result/profile dataclasses and helpers.
- `src/mlx_tui/compare_pane.py` — bind Keep labels and `_commit_decision()` to the displayed result, split persistence and application error handling, and preserve successful saves when application fails. Rename Retain baseline to Keep current settings with the documented overwrite semantics; retain reject reason validation. Keep navigation separate through a Go to Chat button that only activates the tab and focuses `#chat-input`.
- `src/mlx_tui/comparison_persistence.py` and `src/mlx_tui/compare_pane.py` — add the explicit result-path Input and Open result action using `Path.expanduser()` and `load_comparison()`. Make the successfully opened path authoritative as the in-memory `ComparisonInput.result_path`, so a later decision updates the displayed file rather than a stale embedded path; opening alone does not write. Validate before replacing `last_comparison`; leave setup, saved choice, and active settings untouched. Keep partial/corrupt/pending files inspectable or visibly rejected according to existing decoder/transaction contracts, never automatically applied or repaired.
- `tests/integration/test_compare_integration.py` — extend focused comparison coverage with populated result records: visible A/B measurements and trial failures; changed setup selectors cannot relabel a prior result; readiness invalidation excludes the decision reason; Apply/Keep/Open are unavailable during chat/comparison; successful save followed by failed apply has truthful feedback; opening completed/partial/corrupt files preserves the correct independent state. Include a copied completed result whose embedded path differs, decide it, and assert the opened copy is updated while the original is untouched.
- `tests/integration/test_app_integration.py` — exercise the actual Compare tab and buttons with the existing Pilot at 80×24 and 120×40. Verify keyboard access to setup, disclosures, run/cancel, trial selection, Keep, reuse, and Go to Chat; verify resize and completion preserve focus and that the existing chat draft is never sent by navigation.

**Success Criteria:**

#### Automated Verification:
- [x] The latency regression, populated result rendering, frozen-result decisions, failed apply after save, readiness/busy guards, and checkpoint-opening cases pass with existing runner/persistence regressions: `rtk proxy uv run pytest -q`.
- [x] Lint and formatting pass: `rtk proxy uv run ruff check .` and `rtk proxy uv run ruff format --check src tests`.
- [x] New widget handlers and JSON/trial rendering are type-correct: `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] At 80×24 and 120×40, complete setup → readiness → run → inspect → Keep → Go to Chat with keyboard only; labels, focus, long profile names, and all actions remain readable/reachable.
- [ ] Missing snapshots offer an explicit pinned download route; unknown evidence produces understandable caveats; configured server start is explicit and never claims a restart occurred.
- [ ] Completion/failure/cancellation does not steal focus; terminal results explain attempted versus passing trials and show where the checkpoint lives. A failed or cancelled run needs a new readiness check before another run.
- [ ] First-request time is never called cold-load time; repeat statistics clearly include passing repeats only; sampled RSS never implies per-model memory or a guaranteed fit.
- [ ] Open an older or partial result without a saved choice, then an invalid path: the valid result remains inspectable, settings stay unchanged, and errors are visible. Startup/reopen never auto-applies a choice.
- [ ] Keep current settings and Reject both accurately explain that the single reusable saved choice is replaced. A missing snapshot during Keep yields “Choice saved; profile not applied” rather than a false persistence failure.

### Phase 4: Document and verify the complete journey

Write the user-facing guide against the completed UI, then update its entry points and the product description without claiming hardware qualification.

**Changes:**
- `docs/comparison.md` — **new file**, a standalone operational guide with: purpose and exact `coding-check-v1` prompt/expected answer; model versus pinned profile terminology; local attached-server/cache requirements; four-tab responsibilities; setup/run/review/choose/reuse walkthrough; keyboard/disclosure behavior; twelve-slot order and first-versus-repeat interpretation; quality/latency/cache/RSS limitations; readiness and recovery; state-file locations; opening partial results; choice overwrite/commit/apply distinctions; modified profiles; links to architecture and qualification status. Explain that generated code is not executed and this reproduction task does not establish general coding ability.
- `docs/comparison.md` — distinguish informational evidence from blockers: operator text is a declaration, not verification; unknown/expired/unqualified evidence cannot earn an advantage recommendation. Document the 5% latency rule as a product heuristic, the current always-inconclusive memory conclusion, and that older stored summaries are shown as recorded rather than silently recalculated after the arithmetic fix.
- `README.md` — add the Compare tab feature and guide link, update Models/Metrics responsibilities and tab order, replace the long old Metrics comparison walkthrough with a short accurate entry point, and update the keyboard/navigation prose to match shipped labels.
- `ARCHITECTURE.md` — update the comparison section and module inventory for the contracts/persistence/summary/runner facade split, ComparePane ownership, pure presentation helpers, app-level state, cancellation/poll suppression, result inspection, and distinct commit/application outcomes. Link to the user guide for operational details; retain storage/transaction contracts.
- `docs/part3.md` — update the product shape, comparison pillar, and references to comparison inside Metrics to describe the new Compare tab. Add a dated UX follow-up note linking this plan and the guide. Retain Milestone B's unmet qualification/product gates and preserve historical implementation-plan records.
- `tests/integration/test_app_integration.py` and `tests/integration/test_compare_integration.py` — complete any journey assertions exposed by writing the guide using existing harness/Pilot helpers, covering the documented control labels and final transition to an unchanged Chat draft. Avoid screenshot fixtures or a new documentation test framework.

**Success Criteria:**

#### Automated Verification:
- [x] Full ordinary regression and documented stub journey pass: `rtk proxy uv run pytest -q`.
- [x] Final lint, formatting, and type checks pass: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, and `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] Follow only `docs/comparison.md` from an attached setup through a saved/reused choice; each UI label and recovery action matches implementation.
- [ ] README links to the guide; guide links resolve to architecture and compatibility status; active product prose no longer directs users to Metrics for comparison.
- [ ] Guide explains the exact synthetic task, unknown conclusions, partial runs, saved-but-not-applied outcomes, modified profiles, and retain/reject overwrite behavior without requiring source-code reading.
- [ ] Final keyboard walkthrough passes in narrow and wide terminals; no unrelated model-management or chat behavior regresses.
- [ ] Documentation explicitly preserves the distinction between automated stub validation and unmet live Milestone B qualification/product gates.

## Out of Scope

- New tasks, editable prompts, extra candidates, general coding benchmarks, generated-code execution, broad rankings, and automated winners.
- Changes to sampling protocol, runtime ownership, automatic download/restart, memory scoring, or live qualification/recruitment.
- A result-history browser, database, search/filter/export subsystem, profile editor, or migration/recalculation of historical JSON records.
- Chat persistence, new ordinary Params controls, inference changes, and unrelated working-tree cleanup.
- Changes to the single-choice transaction or new baseline-restoration semantics; retain/reject behavior is clarified rather than reinvented.
- A third unspecified user concern. Incorporate it only if subsequently supplied.

## Risks & Mitigations

- Extensive staged/unstaged Milestone A/B work already exists → edit only listed files, re-read current code before extracting, and never reset or absorb unrelated changes.
- Moving the UI can leave Escape/unmount/busy calls pointing at Metrics → migrate every comparison caller, preserve API names, and run the full suite at each checkpoint.
- Splitting the headless module can break facade imports or test monkeypatches → preserve production/runtime public exports, retarget private test hooks to their defining modules, and make the split a behavior-only checkpoint.
- All controls in one pane can overwhelm a small terminal → visible core setup, collapsed details, stacked narrow layout, native scrolling, keyboard/resize verification.
- Setup profiles and displayed historical profiles can diverge → render/decide using the result's immutable profile snapshots and show the run ID; disable decisions while running.
- New results may still be inconclusive despite corrected latency arithmetic → retain strict evidence/quality/protocol guards, explain the reason, and show measurements without claiming qualification.
- Saving and applying are separate operations → distinct feedback preserves a successful choice record and explains a failed application without mutating prior settings.
- Old summaries include the historical latency bug → retain stored evidence as recorded, document the limitation, and require an explicit new run for newly calculated conclusions.
- A copied result can embed its former path → bind a loaded result to the file actually opened before any later decision; opening remains read-only.
- Free-form profile, operator, and generated text may resemble markup → render it literally through existing Rich Text/Textual controls; never execute or interpret generated code.
- Plan expands slightly beyond relocation to explicit result reopening → limit this to one path field and the existing loader; no listing/indexing infrastructure.

Before changing this plan to Approved, suggest the `review-plan` skill for an independent pass over ownership, small-terminal behavior, decision semantics, and the latency regression. Mark Approved only on explicit approval of this detailed document.
