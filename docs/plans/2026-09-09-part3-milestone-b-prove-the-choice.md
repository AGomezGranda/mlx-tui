# Part 3 — Milestone B: Prove the Choice

**Date:** 2026-09-09
**Work Item:** n/a
**Status:** In Progress

## Overview

Add an attach-mode workflow that compares two pinned coding profiles sequentially, explains the task-specific latency/memory evidence, and saves a reproducible choice for later use. Include the minimum Pillar 2 shortlist and recommendation lifecycle needed for that decision; managed installation remains Milestone C.

The user selected Milestone B, including the necessary Pillar 2 shortlist, and agreed to the four phases below. This is a plan against the current working tree, including its staged and unstaged work, not just HEAD. No implementation or inference runs are authorized by completion of this document alone.

## Current State

- `docs/part3.md:227` defines Pillar 2: a small pinned catalogue, per-tier evidence, an owner, 30-day expiry, and unknown rather than invented memory-fit claims. `docs/part3.md:410` requires five repeated trials per profile, separated from the first request, with median/range and retained failures.
- `docs/compatibility/milestone-a.md:10` pins MLX-LM commit `74e7cf931e84ef7c2f63e875adf414e20decc1c5` (0.32.0), MLX 0.32.2, and `local-m4-16gib` (Mac mini, Apple M4, 16 GiB). Its dependency freeze and real-request evidence are already checked in.
- `docs/compatibility/milestone-a.md:33` qualifies `mlx-community/Qwen3-1.7B-4bit@3b1b1768f8f8cf8351c712464f906e86c2b8269e` for the narrow request contract. `mlx-community/Qwen3.5-4B-MLX-4bit@32f3e8ecf65426fc3306969496342d504bfa13f3` is cached but unqualified. Use it as the second candidate, not as a recommendation. The larger Ornith candidate is unnecessary for this two-profile cycle.
- `docs/compatibility/milestone-a.md:148` explicitly blocks B entry on a second named, physically available memory tier. Do not invent that tier or imply the existing contract proves cross-tier suitability. Planning and model-free prototype checks can proceed; live qualification and participant validation remain gated.
- `tests/runtime/test_milestone_a.py` already uses `coding-check-v1`: prompt `Synthetic coding check v1. Return exactly this one-line Python function and nothing else: def answer(): return 42`, temperature 0, seed 7, thinking disabled, and 32 output tokens. The check strips surrounding whitespace and requires exactly `def answer(): return 42`; it never executes generated code. This is a synthetic reproduction task, not evidence of general coding ability.
- `src/mlx_tui/chat.py:43` and `:76` provide `TurnResult` and `stream_turn`: answer/reasoning/tool data, client first-output/answer/total timing, usage, cached-token counts, and stream completeness. Reuse this transport; response model equality is request-identity evidence, not independent proof of weights or residency.
- `src/mlx_tui/metrics_pane.py:26` renders chat history and memory rings. `src/mlx_tui/history/store.py` stores neither reproducible profile identity nor durable comparisons. The app's two-second polling in `src/mlx_tui/app/__init__.py` is too sparse to measure these short requests reliably, and its memory records have no process identity.
- `src/mlx_tui/presets.py:13` has request-only `Preset` values; `src/mlx_tui/config.py:16` has no seed, thinking toggle or saved-profile identity. `_apply_preset` in `src/mlx_tui/app/__init__.py:387` applies only system/temperature/top-p/output budget; `src/mlx_tui/chat_pane.py:294` constructs the chat payload independently.
- `src/mlx_tui/operations.py:17` serializes chat/load/restart/delete with one lease. `src/mlx_tui/app/__init__.py:577` and `:593` route and enable cancellation only for chat. Comparison must participate in both paths.
- `src/mlx_tui/models.py:17` aggregates disk size across revisions and derives quantization from names. `fits_headroom` at `:60` compares disk size plus 20% against available RAM; `src/mlx_tui/table.py:33` calls this “disk headroom.” Neither establishes runtime fit. Deletion currently protects only exact repository-string selection in `src/mlx_tui/models_pane.py:308` and `:363`; pinned snapshot paths must also protect their repository.
- `src/mlx_tui/search.py:159` already accepts `revision` for downloads. `src/mlx_tui/search_screen.py` looks up current repository metadata, so it needs a pinned-candidate entry path rather than silently replacing a requested revision with HEAD. Its binary size formatter currently says GB/MB instead of GiB/MiB.
- Existing tests use pytest, HTTP stubs in `tests/conftest.py`, Textual `AppHarness`, and opt-in real-server contracts under `tests/runtime`. Follow those patterns; no new dependency or test framework.

Research verification: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, and `rtk proxy uv run pyrefly check` passed. `rtk proxy uv run pytest -q` passed with **501 passed, 6 skipped** in 130.02 seconds. Runtime contracts are opt-in; skipped runtime tests do not establish compatibility.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Two baseline model profiles | Two models; two sampler variants of the qualified model; three models | Qwen3-1.7B versus the already identified Qwen3.5-4B candidate tests a useful model/memory choice without inventing an optimization claim. If the second candidate fails qualification, retain the failure and revise this decision before recruiting participants. |
| One fixed synthetic check | Reuse coding-check-v1; introduce a larger benchmark | Preserve A's exact check and its limitations. If users cannot derive value from it, revise the task in another validation cycle; do not quietly change its identity or broaden quality claims. |
| Versioned TOML profiles plus JSON results | Extend presets; replace configuration; add a database | Preserve ordinary presets and TOML editing. Strict profile records are separate from forgiving legacy preset parsing. JSON and atomic file replacement cover local results and the chosen profile. |
| One runner, integrated into Metrics | Reuse chat UI as a runner; new benchmark service; small async function | Reuse `stream_turn` without sending synthetic trials into the user's conversation. One lease covers the entire comparison. |
| First request plus five repeats per profile | Cold/load benchmarks; warm-load probe followed by trials | The first request includes any on-demand load and is retained separately. No preliminary generation hides its cost. Call it “first request; prior residency unknown”; “first after load” requires operator-recorded load evidence. Repeated requests are not proof of residency or cache hits. |
| Measurements or unknown for fit | Universal KV constant; disk-size multiplier; matching observations | Report per-revision disk/download size separately from sampled process RSS and available system memory. No universal working-set estimate or guaranteed-fit badge. |
| Conservative conclusions, user chooses | Weighted winner score; automatic selection; explicit tradeoffs | Both profiles remain selectable, with their failures visible. Only all-five passing repeats with comparable verified identities support an advantage label. Retaining the baseline or rejecting both is valid. |
| Attach ownership remains explicit | Reuse automatic restart policy; operator-managed restart | Compare and Keep never call configured start/stop commands. Explain launch changes and require fresh preflight before continuing. Existing manually invoked lifecycle controls remain governed by their existing policy. |

### Shared contracts for all phases

These new paths and symbols are proposed implementation work, not existing APIs; verify their final integration during implementation. Existing symbols cited above were inspected. Keep the new implementation to `src/mlx_tui/profiles.py`, `src/mlx_tui/comparison.py`, one packaged TOML catalogue, and edits to the existing panes; introduce another module only if the actual implementation needs it.

**Profile and evidence identity.** Define frozen `CodingProfile`, `RecommendationEvidence`, and `ProfileEntry` records in `profiles.py`. `CodingProfile` contains `id`, `name`, `repo_id`, full `revision`, `quantization`, `runtime_commit`, `mlx_version`, `template_sha256`, `template_assets`, canonical JSON-compatible `launch_settings`, `system`, `temperature`, `top_p`, `max_tokens`, `max_ctx`, `seed`, and `enable_thinking`; launch settings are evidence to verify, never a command to execute. `RecommendationEvidence` contains owner, status/revocation reason, tested/expiry timestamps, machine tier, task/check/prompt identities, runtime freeze identity, profile fingerprint, and result references/hashes. `ProfileEntry` pairs one profile with zero or more evidence records, so `load_coding_profiles(path: Path | None = None) -> list[ProfileEntry]` cannot discard catalogue evidence. A profile fingerprint is SHA-256 of canonical JSON over every effective profile field, including launch settings and all immutable template-asset hashes, while excluding only display names and machine-local paths. Asset hashes establish file identity, not which template the runtime used.

**Initial configurations.** IDs `qwen3-1.7b-baseline` and `qwen3.5-4b-baseline` use the two full revisions above, the same runtime pin, empty system prompt, temperature 0, top-p 1, seed 7, thinking false, max_tokens 32 and configured max_ctx 8192. The 8192 setting is a client budget, not a validated architectural/quality limit. Adding explicit top-p 1 to A's otherwise matching request requires B revalidation. Both cached configs were inspected during planning: 4-bit, group size 64; the second additionally specifies affine mode. The tokenizer-config SHA-256 values are `253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0` and `e98f1901ac6f0adff67b1d540bfa0c36ac1a0cf59eb72ed78146ef89aafa1182`, respectively. The second snapshot also contains `chat_template.jinja`; include its hash in the profile identity and verify which template the pinned runtime actually uses during qualification. Asset presence is not live compatibility evidence.

**Recommendation evidence.** Store `RecommendationEvidence` records beside their profiles in packaged `src/mlx_tui/coding_profiles.toml`. Start with candidates and no B-qualified recommendation. Name project maintainer Alvaro Gomez as the proposed owner; confirm ownership before publication. Expiry is at most 30 days after testing, evaluated in UTC at display and run time. Profile/runtime/template/launch/check changes, explicit incompatibility revocation, absent evidence or an unmatched machine tier yield unknown/withdrawn advice immediately. Missing or unknown evidence can never produce an advantage or recommendation label. Retain old results for inspection. Publication/revalidation is a reviewed repository edit, not an automatic background service.

The inspected Qwen3.5 `chat_template.jinja` SHA-256 is `a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`. Include a `template_assets: dict[str, str]` field in `CodingProfile` for these additional asset hashes; include it in the fingerprint. Recheck pinned assets before each run rather than assuming a snapshot directory cannot be locally modified.

**Comparison input and preflight.** Define frozen `ComparisonInput` in `comparison.py` with the normalized endpoint, exactly two `ProfileEntry` values, resolved snapshot paths, verified asset hashes, runtime/install/launch evidence plus provenance, process identity, isolation/restart-or-eviction evidence, machine tier, operator conditions, profile order, task/check/prompt identities, and result path. `run_comparison(comparison: ComparisonInput, *, on_progress: Callable[[ComparisonResult], None]) -> ComparisonResult` snapshots that complete value before its first await. For supported comparisons, accept only `http` URLs with an explicit port and an IP-literal host of `127.0.0.0/8` or `::1`; reject credentials, query, fragment, deceptive suffix hosts, unsupported paths, and `localhost` because supported process attribution needs an unambiguous local address. Resolve full revisions to existing HF snapshot directories and send those absolute paths as the request model; never send a mutable repository name for a pinned trial. Validate required assets and compare their hashes to the profile before any request. Reuse HF cache metadata and `download_snapshot(revision=...)`; all network downloads remain explicit, and fully cached preflight must make no Hub network call. `/health` and `/v1/models` do not report runtime versions. Record operator-supplied runtime environment/launch evidence and unknown fields; do not infer endpoint runtime from packages imported in the TUI. For supported qualification, verify the running process against the operator's pinned environment, immutable install metadata/freeze, and launch settings using A's evidence discipline. HTTP success alone cannot satisfy this gate. After an endpoint/process change, repeat preflight; unknown runtime settings permit only an exploratory result and never an advantage or recommendation label.

**Results and terminal states.** Define `ComparisonResult` in `comparison.py`, serialized with `schema_version=1`, run UUID, timestamps, every `ComparisonInput` field and provenance, full profiles/fingerprints, effective request payloads, trials, and exactly one status: `running`, `completed`, `failed`, `cancelled`, or `interrupted`. Each `TrialResult` records profile ID, first/repeat index, `not_attempted`/`attempted`/`completed`, error or cancellation, complete answer/reasoning/tool data, response identity, finish/completeness flags, quality pass/fail, usage and estimate flags, cached tokens, client timing, server-process RSS samples and sample count. A terminal load, transport, or identity failure stops the entire comparison: record the active attempt, mark every remaining slot not attempted, checkpoint, and require a new run through preflight. On cancellation, checkpoint the active attempt and remaining slots, then re-raise `CancelledError`; never send another request. If progress notification fails, record/checkpoint that callback error before stopping where possible; a durable checkpoint failure always stops immediately and preserves the last valid file. No generated code or tools are executed.

**Storage.** `comparison_dir() -> Path` uses `$XDG_STATE_HOME/mlx-tui/comparisons`, else `~/.local/state/mlx-tui/comparisons`. Each run owns `<uuid>.json`; the saved selection is `choice.json` in the parent `mlx-tui` state directory. Use same-directory temporary files, flush/fsync and `os.replace`. Save an initial running record before inference and checkpoint after every attempted trial and terminal transition; a leftover running record is displayed as interrupted on reopening. Validate schema/types plus finite, non-negative timings on both write and read; corrupt or newer-schema files get a visible per-file error without breaking other results. A write failure stops additional trials, preserves the last valid file and offers retry without re-running inference. Treat `choice.json` as committed only when its run ID matches a finalized run decision; both run→choice and choice→finalized-run crash boundaries recover to an uncommitted/pending decision. Do not rewrite config/preset files. Save only synthetic task outputs locally; add no export feature and store no ordinary chat content.

**Checkpoint ordering.** After each state change, validate and atomically save the result before invoking `on_progress`; a save failure sends no progress notification and no later request. A progress-callback failure becomes a terminal runner failure, which is checkpointed before returning when storage remains available. On `CancelledError`, mark the active attempt cancelled and remaining slots not attempted, attempt one final checkpoint, then re-raise the original cancellation even if that checkpoint fails; retain and surface the last valid record.

## Implementation Phases

### Phase 1: Define pinned profiles and the evidence-backed shortlist

Introduce strict profile parsing and candidate inspection without changing existing chat behavior or publishing unearned recommendations.

**Ordered implementation substeps:**
1. Add frozen `CodingProfile`, `RecommendationEvidence`, and `ProfileEntry` records plus canonical fingerprinting in `src/mlx_tui/profiles.py`.
2. Parse the catalogue with `tomllib`/`importlib.resources`; reject duplicate IDs, mutable revisions, bool-as-number values, invalid ranges/dates/hashes, and incomplete evidence rather than clamping experiment settings.
3. Add `src/mlx_tui/coding_profiles.toml` with both pinned candidates, exact launch/template asset identities, A evidence kept separate, and B status unqualified.
4. Add `tests/unit/test_profiles.py` for strict parsing, exact expiry, launch/template fingerprint invalidation, revocation, absent evidence, and unknown tiers.
5. Add pinned snapshot resolution and one repository/path identity matcher in `src/mlx_tui/models.py`; cover cached-only resolution with a test that fails if Hub networking occurs.
6. Remove `ModelRow.fits`, `fits_headroom()`, and their tests after confirming `rtk proxy rg -n "fits_headroom|\.fits\b" src tests` finds no remaining real caller; keep download size as a disk fact.
7. Replace the table's disk-to-RAM checkmark with explicit unknown runtime fit, label name-derived quantization as a hint, and use the shared identity matcher for markers.
8. Reuse that matcher in both `src/mlx_tui/models_pane.py` deletion guards and test selected/last-observed snapshot protection.
9. Add the compact candidate selector/details to Models, showing evidence only for the exact workload/tier and keeping direct repository/path selection explicitly unqualified.
10. Thread an optional revision through existing search metadata/download UI, retain legacy search behavior, and correct binary labels to GiB/MiB with unit/integration coverage.
11. Extend `tests/artifact_smoke.py` to assert the catalogue exists in the wheel and sdist-rebuilt wheel and to import `load_coding_profiles()` outside the checkout, requiring both IDs.

**Implementation checkpoint:** packaged profile contracts, cache identity, shortlist display, search/download behavior, and obsolete disk-fit removal are complete; no recommendation is published.

Implementation notes: the existing `avail_gib` arguments remain accepted as ignored compatibility parameters while the disk-fit field and heuristic are removed. Existing search metadata already carried revisions, so Phase 1 adds the pinned-candidate path and exact-revision verification without changing legacy search behavior.

**Success Criteria:**

#### Automated Verification:
- [x] Profile validation, expiry, cache selection and existing download/delete behavior pass: `rtk proxy uv run pytest -q`.
- [x] Wheel and rebuilt-sdist-wheel installations contain and load both packaged profiles outside the checkout: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Source/tests lint and format pass: `rtk proxy uv run ruff check .` and `rtk proxy uv run ruff format --check src tests`.
- [x] Strict type checking passes: `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] Models distinguishes cached resources, unqualified candidates, historical results and current recommendations in words, including at 80×24 with long names.
- [ ] Selecting a pinned snapshot marks/protects its repository; arbitrary repository/path use is available without endorsement.
- [ ] An expired or revoked profile loses its recommendation immediately while its dated evidence remains readable; unknown memory fit is never a green guarantee.

### Phase 2: Run and persist sequential comparisons

Build the UI-independent experiment path and durable records; it is exercised through tests before wiring it to Metrics.

**Ordered implementation substeps:**
1. Add frozen `ComparisonInput`, `TrialResult`, and `ComparisonResult` records and strict schema-v1 JSON encode/decode in `src/mlx_tui/comparison.py`.
2. Add atomic save/load helpers and tests for corrupt/newer schemas, finite non-negative timings, interrupted records, and preservation of the last valid file.
3. Add exact loopback URL parsing tests for IPv4/IPv6 literals, explicit ports, credentials, queries/fragments, deceptive host suffixes, `localhost`, and unsupported paths.
4. Add `coding_payload()` and the pure `coding-check-v1` assertion using `prepare_context` and `stream_turn`; accept no repaired/fenced output, tool call, incomplete stream, or mismatched response path.
5. Build all twelve trial slots before sending requests and persist the initial `running` record.
6. Run first request plus repeats 1–5 for each profile strictly in the input order, with independent contexts and no warm-up or retry.
7. On any load/transport/identity failure, checkpoint the active attempt, mark every later slot not attempted, return `failed`, and send zero later requests.
8. On cancellation, checkpoint the active attempt and later slots as `cancelled`/not attempted, then re-raise `CancelledError`; test cancellation before and after first output and zero later requests.
9. Resolve and verify endpoint/PID/create-time association once before each trial, sample only cheap RSS/system values in a worker thread during the request, then reverify identity afterward; never call `find_server_process()` at 10 Hz on the request event loop.
10. Persist timestamped values as “server process RSS while requested”; invalidate memory comparison when identity changes, samples are missing, or scope differs.
11. Summarize first requests separately and repeats with median/range/success counts; materially different cached-token reuse makes latency inconclusive.
12. Require five passing repeats, matched budgets/protocol/identity, known evidence, and non-suspect conditions for any advantage label. Memory additionally requires controlled restart/eviction evidence or the same non-overlapping result in both profile orders; process-wide RSS alone never establishes per-profile residency. Keep the 5% latency rule as a labeled product heuristic with the required `ponytail:` ceiling comment.
13. Extend HTTP stubs through the real `stream_turn` path for ordering, payload identity, terminal failure, cancellation, sampler cleanup, and confounder-driven summary ineligibility; stub process attribution independently.

**Implementation checkpoint:** the headless runner produces durable, fail-closed results and sends no request after a terminal failure or cancellation; it is not yet exposed in the TUI.

**Success Criteria:**

#### Automated Verification:
- [x] Twelve-trial ordering, request identities, fail-closed terminal states, sampler cleanup, summary confounders, persistence failures and cancellation are checked in the suite: `rtk proxy uv run pytest -q`.
- [x] Lint, format and types pass: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] Inspect a saved stub result: each attempted failure, provenance/unknown field, actual payload and sample scope is understandable without the current TUI session.
- [ ] Interrupt a test run and reopen its file: completed checkpoints remain readable and unfinished work is marked interrupted, never complete.
- [ ] Review the summary wording: the synthetic check supports only this exact task; the 5% rule and RSS sampling do not imply statistical proof or engine measurements.

### Phase 3: Add Metrics comparison and saved-profile reuse

Connect the runner to keyboard-accessible controls, preserve the user's chat, and make Keep apply the actual tested configuration.

**Ordered implementation substeps:**
1. Add `OperationKind.COMPARING`, its busy rules, and focused lease/cancel tests in `src/mlx_tui/operations.py`.
2. Keep the selected candidate pair in `MlxTuiApp`; expose small update/render methods so Models and Metrics never import or own each other's state.
3. Add Metrics candidate selectors, preflight evidence/conditions, progress, saved-result inspection, and explicit download/restart actions using existing Textual widgets and the existing download modal.
4. Acquire one comparison lease before preflight and mirror chat's pre-start cancellation guard; release only after cancellation and persistence cleanup settle.
5. Extend app action gating/Escape/unmount behavior for comparisons and active downloads, preserving the last checkpoint and never stopping the attached server.
6. Suppress the app's two-second `/health`, `/v1/models`, and process/memory poll while `COMPARING`; force exactly one refresh after cleanup and test both suppression and refresh.
7. Snapshot pre-run selection plus effective saved/unsaved Params, build the complete `ComparisonInput`, and run one Metrics worker without applying trial settings to Chat.
8. Restore the pre-run selection/settings after completion, failure, or cancellation with generation state unknown; require a new explicit preflight/run after any terminal failure.
9. Add Keep A/B, Retain previous baseline, and Reject both with a reason. Never auto-select a winner or turn a local choice into a published recommendation.
10. Implement the pending run decision → atomic `choice.json` → finalized run decision sequence; add separate tests for crashes at the run→choice and choice→finalized-run boundaries and for write rollback.
11. Add optional `seed` and `enable_thinking` config fields with strict known-key parsing; snapshot only configured values into subsequent chat requests.
12. Add `apply_coding_profile()` to validate the snapshot, apply exact profile settings, update Params/selection/status, and leave start/stop/load ownership with the operator.
13. Load and display saved choices without applying them on startup; revalidate before reuse and mark the active profile modified after any ordinary setting change.
14. Verify comparison requests never enter chat messages/transcript and Keep/reuse preserves the user's draft/history while the next chat sends exact tested settings.
15. Document Compare/Keep/reuse, state-file locations, attach ownership, and unknown labels in `README.md` and `ARCHITECTURE.md`.

**Implementation checkpoint:** the complete stub-backed TUI workflow is implemented and documented; hardware qualification and participant validation remain separate gates.

**Success Criteria:**

#### Automated Verification:
- [x] Compare/Keep/reopen/chat works through the stub UI, with no overlapping operations or silent settings loss: `rtk proxy uv run pytest -q`.
- [x] Integration coverage proves background poll suppression plus one cleanup refresh, app-owned candidate state, both choice crash boundaries, and unchanged chat draft/history.
- [x] Existing and new code passes lint, format and types: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] At 80×24 and a wider terminal, complete candidate selection → preflight → Compare → inspect failures/results → Keep using only the keyboard. Long names and reason fields remain reachable with visible focus.
- [ ] Cancel before output and during output; controls recover, partial results remain and no automatic restart or next profile request occurs.
- [ ] Keep either profile, reopen the TUI, inspect the saved evidence, use the saved profile and send a chat request; effective settings match while existing draft/history behavior remains intact.
- [ ] Test Retain baseline, Reject both, missing/corrupt files, write denial, missing cached revision and operator-managed restart requirements. Each leaves an accurate state and a next action.

### Phase 4: Qualify the pair and validate decision value

Separate feature implementation from real-runtime qualification and the product gate. This phase can remain blocked without pretending Milestone B is complete.

Implementation note (2026-09-09): the opt-in two-order runtime contract and
the Milestone B qualification/product-gate report are implemented. No live B
execution was performed, no second physical tier was available, and the
five-operator gate has not started; those external criteria remain unchecked.

**Implementation checkpoint substeps:**
1. Add the second physical tier's observed name, chip, RAM, macOS, access, and owner to `docs/compatibility/milestone-a.md`; retain the original blocker/history until those facts exist.
2. Add `tests/runtime/test_milestone_b.py` using the same runner and preserving A's fixture behavior; it never starts, stops, downloads, or reconfigures the endpoint.
3. Define the exact gate: without `MLX_TUI_B_QUALIFY=1`, B runtime tests skip; with it, the fixture fails unless `MLX_TUI_B_URL`, `MLX_TUI_B_PROFILE_A_PATH`, `MLX_TUI_B_PROFILE_B_PATH`, `MLX_TUI_B_RUNTIME_ENV`, `MLX_TUI_B_LAUNCH_EVIDENCE`, `MLX_TUI_B_MACHINE_TIER`, and `MLX_TUI_B_OUTPUT` are all valid.
4. Parametrize the targeted runtime contract over `a-then-b` and `b-then-a`; each case writes one retained comparison result and fails on unsupported identity or missing evidence.
5. Add the exact shell invocation and its observed IDs/hashes to `docs/compatibility/milestone-b.md` only after it succeeds on real hardware.

Qualification command template for each tier (replace shell values with that tier's observed evidence; do not commit secrets):

```bash
rtk proxy env \
  MLX_TUI_B_QUALIFY=1 \
  MLX_TUI_B_URL="$MLX_TUI_B_URL" \
  MLX_TUI_B_PROFILE_A_PATH="$MLX_TUI_B_PROFILE_A_PATH" \
  MLX_TUI_B_PROFILE_B_PATH="$MLX_TUI_B_PROFILE_B_PATH" \
  MLX_TUI_B_RUNTIME_ENV="$MLX_TUI_B_RUNTIME_ENV" \
  MLX_TUI_B_LAUNCH_EVIDENCE="$MLX_TUI_B_LAUNCH_EVIDENCE" \
  MLX_TUI_B_MACHINE_TIER="$MLX_TUI_B_MACHINE_TIER" \
  MLX_TUI_B_OUTPUT="$MLX_TUI_B_OUTPUT" \
  uv run pytest -q tests/runtime/test_milestone_b.py
```

**External qualification checkpoint substeps:**
1. On each named physical tier, run the qualification command template above; require exactly `2 passed`, zero skipped, and two result files, one for each order.
2. Record exact TUI/runtime/install/launch/profile/template/task identities, first/repeat trials, failures, cache reuse, process-RSS scope, changed conditions, and result hashes under `docs/compatibility/evidence/milestone-b/`.
3. Compare both orders on both tiers; if latency changes with materially different cached-token reuse, or memory lacks controlled restart/eviction evidence and an order-independent result, publish it as inconclusive.
4. Review the evidence, confirm the recommendation owner, and only then add matching tier/workload evidence plus tested/expiry dates to `src/mlx_tui/coding_profiles.toml`.

**Product-gate checkpoint substeps:**
1. Add the consent-based five-operator observation sheet to `docs/compatibility/milestone-b.md`: anonymous participant ID, tier, completion, assistance, choice/reason, evidence explanation, follow-up/return action, and dropout/alternative reason.
2. Require four unassisted completions, three evidence-backed explanations, and three separate-session returns within 14 days; count failures/dropouts even when thresholds pass.
3. Update `docs/part3.md` with implementation, qualification, and product-gate status/links. Mark B achieved only after both external checkpoints pass; otherwise record the focused next validation question rather than advancing to C/D.

**Success Criteria:**

#### Automated Verification:
- [x] All ordinary tests pass and real contracts stay opt-in without inputs: `rtk proxy uv run pytest -q`.
- [x] Final build artifacts contain and load the packaged catalogue: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Lint, format and type checks pass: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, `rtk proxy uv run pyrefly check`.
- [x] With `MLX_TUI_B_QUALIFY` absent, `rtk proxy uv run pytest -q tests/runtime/test_milestone_b.py` skips all B contracts for ordinary CI.
- [ ] On each physical tier, the fully populated `MLX_TUI_B_*` environment plus `rtk proxy uv run pytest -q tests/runtime/test_milestone_b.py` reports exactly `2 passed`, zero skipped, and creates one retained result per profile order. Record the exact command, IDs, counts, and hashes in the report. No live B execution was performed during planning.

#### Manual Verification:
- [ ] The second physical tier exists, both candidates have been attempted on both tiers and supported profile/runtime/template identities are verified; unknowns and failures are retained.
- [ ] Fully cached comparison and saved-result inspection work offline; no attached environment/process is silently replaced or stopped.
- [ ] The maintainer reviews reverse-order results, memory attribution, expiry and narrow task-quality claims before recommending either candidate.
- [ ] Five-operator completion, choice and 14-day return-use gates are met with consent and recorded reasons, or explicitly reported as unmet with a focused next validation question.

## Out of Scope

- Managed installation, runtime ownership, packaging expansion and automated updates (C/F).
- General coding benchmarks, generated-code execution, broad rankings or public leaderboards.
- Extra models, optimization-labelled profiles, KV quantization, speculation, batching and cache instrumentation.
- Universal KV/runtime-memory estimators, allocator peaks, engine prefill/decode/load timing, residency and engine-cancellation claims without new evidence.
- Durable conversations, file attachments, tool execution, background serving and external-client guarantees.
- Runtime discovery that executes arbitrary operator-supplied commands, background recommendation refresh, telemetry and automated participant outreach.

## Risks & Mitigations

- Only one tier is currently evidenced → keep live B entry/qualification and recruitment gated; do not invent access or silently weaken the two-tier target.
- Second candidate may fail under the pinned runtime/settings → retain the attempted result, withhold recommendation and revise the pair explicitly before user trials. Do not upgrade the runtime silently to make it pass.
- coding-check-v1 may be too trivial to inform a worthwhile choice → describe exactly what passes; assess decision value with operators, and version a different task only in a subsequent explicit validation cycle.
- Endpoint echoes model paths and accepts parameters without proving implementation → distinguish observed response, installed/runtime evidence and operator declarations; unknown identity/settings cannot earn supported advice.
- Very short requests and macOS permissions limit RSS sampling → persist sample counts/provenance, separate first requests, and return unknown memory advantage rather than inferring from disk size.
- External requests/cache state/power changes confound trials → record order/reuse/conditions, require isolated qualification runs, and label suspect or inconsistent results inconclusive.
- Pinned snapshot paths bypass repository-string deletion/marker logic → use one shared identity match in both delete checks and marker rendering, with regression coverage.
- Saved choice can disagree with applied controls or partially written files → write before applying, preserve the prior state on failure, revalidate on reuse and mark manually changed settings modified.
- Existing working tree contains substantial uncommitted changes → re-read affected files and rerun the baseline before implementation; do not revert or absorb unrelated work.

Final plan review should check phase boundaries, runnable criteria, settings identity and failure/cancellation paths. Run the review-plan skill before requesting approval; keep this document Draft until the user explicitly approves it.
