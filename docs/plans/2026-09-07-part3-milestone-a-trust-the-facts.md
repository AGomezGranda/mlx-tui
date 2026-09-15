# Part 3 Milestone A — Trust the facts

**Date:** 2026-09-07
**Work Item:** n/a
**Status:** Complete

## Overview
Implement the first build-order milestone in `docs/part3.md`: establish real compatibility evidence for one pinned MLX-LM runtime and correct state, stream handling and metrics to match that evidence. Prepare the existing attach workflow for Milestone B without building Compare or managed installation.

The user agreed to the four-checkpoint outline below. That agreement does not approve implementation of this draft.

## Current State
Research used the current staged working tree, including the recent boot, status-bar, parameter and operations extractions. Preserve that work; references describe this baseline, not just HEAD.

- `src/mlx_tui/status.py:33` promotes a sole `/v1/models` catalogue entry to active model. `src/mlx_tui/app/__init__.py:144` polls only that catalogue; `update_server_identity` at line 210 and `effective_model` at line 237 propagate it into status, chat selection, memory labels and deletion guards.
- `src/mlx_tui/serverctl.py:256` sends a one-token load request. `wait_healthy` at line 280 accepts a matching singleton catalogue without generation verification. `src/mlx_tui/models_pane.py` `run_warm_swap` at line 183 falls back to catalogue verification at lines 200-208, logs “loaded” at line 222 and retains old identity after failed switches.
- `src/mlx_tui/process.py:133` checks listener port, process tokens, PID and creation time, but not listener address or ambiguous matches. Discovery is not ownership. `src/mlx_tui/boot.py:126` owns launch cleanup only until success; quitting merely closes HTTP (`app/__init__.py:141`).
- `src/mlx_tui/chat.py:52` retains answer text, usage and finish reason, discarding reasoning/tool output. `src/mlx_tui/sse.py:36` hides DONE termination and cannot distinguish clean EOF from a properly terminated stream. `usage_from_chunk` at line 90 accepts booleans and negative integers.
- `src/mlx_tui/chat_pane.py:304` labels prompt tokens / time to answer as prefill. `sse.py:130` divides all completion tokens by time after first answer text, potentially excluding reasoning time. Neither measures engine speed. `chat_pane.py:352` commits length-capped responses and can accept graceful premature EOF.
- `src/mlx_tui/history/store.py:20` stores metrics, not conversations. `src/mlx_tui/metrics_pane.py:66-76` and `history/sparkline.py:73-96` expose the misleading rates. `ColdTracker` (`status.py:57`) infers cold state from connectivity, which proves neither loading nor cache misses.
- `src/mlx_tui/status_bar.py:54` shows color without a state word and labels GiB values GB. `history/tokens.py:125` displays context without qualifying the character estimate.
- Tests use HTTPX MockTransport (`tests/unit/test_chat.py:49`), SSE builders (`tests/builders.py`) and an HTTPServer/Textual harness (`tests/conftest.py`). These cannot establish real MLX behavior.
- Planning machine: Apple M4, 16 GiB, arm64, macOS 26.6.2 build 25G83. The project environment has no MLX inference runtime installed.
- A clean sibling MLX-LM checkout was inspected at commit `74e7cf931e84ef7c2f63e875adf414e20decc1c5`, declaring version 0.32.0 and requiring MLX >=0.32.1 in `setup.py`. At that commit, `mlx_lm/server.py:1589` separates health and catalogue; health returns static OK, line 1310 emits `reasoning`, line 1294 emits cached usage, and line 1256 reports `requested_model`. The response model is request attribution, not residency telemetry. The different `6d21ce4…` snapshot in Part 3 was unavailable locally; do not equate them.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Existing modules plus opt-in runtime contracts | Focused corrections; runtime adapter; benchmark platform | HTTP evidence and explicit unknowns satisfy A without owning inference. |
| Candidate pin: `74e7cf931e84ef7c2f63e875adf414e20decc1c5` | Inspected commit; Part 3 snapshot; floating latest | Test inspected source and document the snapshot difference. Promote to supported only after real contracts pass. |
| Isolate test runtime from the TUI | Separate frozen environment; project dependency | Preserve the operator’s attach environment and keep inference outside Textual. |
| Separate selected model and response evidence | One effective-model field; distinct values | Catalogue, selection, request success and residency are different facts. |
| Readiness is scoped and dated | Health means ready; last generation evidence; telemetry | Show readiness unknown before generation and last generation succeeded afterwards; never promise current residency. |
| Remove prefill; use client latency and request-wide throughput | Rename proxy; remove; engine instrumentation | Completion usage / full request time is defensible when explicitly labeled client-observed, not decode speed. |
| Preserve reasoning/tool output without execution | Ignore fields; preserve/display; tool harness | Keep coding answers distinct without adding an agent runtime. |
| Preserve configured lifecycle commands | Remove; explicit operator controls; managed mode | Discovery never authorizes automatic ownership. Managed lifetime is outside A. |

## Implementation Phases

### Phase 1: Pin and probe a real runtime
Create reproducible evidence before choosing supported optional surfaces. New files and commands below are proposals, **verify during implementation**; no inference installation or model download was performed while planning.

**Changes:**
- `docs/compatibility/milestone-a.md` — new report, initially unverified. Record candidate commit, exact Python/MLX/MLX-LM/tokenizer dependencies, TUI revision and working-tree diff identity, launch argv, endpoint, hardware/OS, model repository/revision/quantization, tokenizer/template identity, request settings, test date and evidence locations. Label this machine `local-m4-16gib`.
- `docs/compatibility/milestone-a-runtime.txt` — new exact dependency freeze (`uv pip freeze` from the isolated installation; fall back to `pip freeze` only if `uv` is unavailable and record which was used) produced from an isolated installation of the candidate commit. Retain its immutable source reference. Step 1 of implementation is discovery: recreate the environment and record actual install/start commands in the report. Do not add MLX to project dependencies or change the operator’s environment.
- `tests/runtime/conftest.py` — new opt-in fixtures requiring `MLX_TUI_CONTRACT_URL` and `MLX_TUI_CONTRACT_MODEL`, with `MLX_TUI_CONTRACT_OUTPUT` for a new evidence directory. Create this file before any `tests/runtime` success criterion applies (`pytest` on a missing path exits 5, not skip). Missing env → `pytest.skip` with a clear reason; non-loopback `MLX_TUI_CONTRACT_URL` host → hard fail; finite deadlines and a dedicated operator-launched server required. Never start/kill/download/reconfigure an arbitrary attached server. Record versioned JSON metadata/results and synthetic timed SSE events without overwriting prior runs.
- `tests/runtime/test_milestone_a.py` — new real HTTPX/pytest contracts: health/catalogue before and after generation; text/reasoning/tool streams; usage and cached usage; identical versus changed prefixes; accepted and invalid sampler controls; two simultaneous requests (record per-request latency plus aggregate behavior); disconnect before and during output; failed model load and recovery. Bound concurrent work to two requests. Also verify missing-`model` request behavior (does the server require the field, apply a default, or reject) to settle the Phase 2 no-selection rule. Record unsupported cases as unavailable/unknown, not successful feature tests. Follow-up latency alone cannot prove engine cancellation.
- In the report, distinguish library support, server exposure, model compatibility and verified local effect for each capability. Accepted settings remain unverified unless their behavior is established. Record unsupported/unknown KV quantization, allocator peaks, prefill timing, residency and cancellation explicitly instead of adding instrumentation.
- In the report, choose two or three immutable model revisions from actual test resources, beginning by evaluating the README’s Qwen3-1.7B-4bit candidate. Verify hashes/assets during implementation. Record one synthetic coding prompt, fixed context/output budget and versioned deterministic expected-output check; do not execute arbitrary generated code. Name memory tiers from actual available hardware: a single-tier report on `local-m4-16gib` satisfies Phase 1; the missing second tier remains an explicit B-entry blocker, not simulated evidence. Record evidence checksums with `sha256sum` alongside locations.

**Success Criteria:**

#### Automated Verification:
- [x] Existing regression suite remains green: `rtk uv run pytest -q`.
- [x] With no opt-in environment, real tests clearly skip: `rtk uv run pytest -q tests/runtime` — **verify during implementation**, new path.
- [x] With the report’s exact environment variables exported, the same command generates all contract outcomes and fails any claimed-supported mismatch. Unsupported cases are explicitly reported; skips are not evidence of support. **Verify during implementation.**

#### Manual Verification:
- [x] Recreate the frozen environment and launch on `local-m4-16gib`; verify model pinning and isolation from the operator’s environment.
- [x] Review raw responses against claims; distinguish untested tiers and source-only capabilities from live results.

### Phase 2: Correct endpoint, model and memory state
Implement in this order, checking after each step; the checkpoint is complete only when all consumers agree on the new semantics. Order: (2a) `status.py` records → (2b) `app/__init__.py` probe/identity → (2c) `serverctl.py`/`boot.py` verification → (2d) `models_pane.py`/`swap.py` selection and deletion guards → (2e) `status_bar.py`/`table.py`/`process.py` display and attribution → (2f) unit then integration tests. Keep polling observational; generation probes occur only during explicit load/start or user requests.

**Changes:**
- `src/mlx_tui/status.py` — extend existing records with proposed fields `selected_model: str | None`, `last_response_model: str | None`, `last_success_at: float | None` and `generation_state: Literal["unknown", "succeeded", "failed", "client_cancelled"]`. Keep transport state and catalogue separate. Catalogue parsing never sets observed model, even for one entry. New fields are **verify during implementation** against consumers.
- `src/mlx_tui/app/__init__.py` — update `_fetch_probe`, `_poll`, `update_server_identity` and `effective_model` in that order: independently probe health/catalogue; derive request selection from config/explicit load, never catalogue order. Retain selection for retries, invalidate generation evidence on endpoint/process-generation change, and preserve dated prior success only as historical evidence. Guard stale polls with a monotonic poll sequence so older in-flight polls cannot overwrite newer request results. `effective_model` becomes selected request target; update every caller’s interpretation. No selection (fresh multi-model attach) → chat is refused with an explicit-selection hint until Phase 1 proves safe missing-`model` server behavior; never silently send an unselected request.
- `src/mlx_tui/serverctl.py`, `src/mlx_tui/boot.py` — remove singleton/membership success shortcuts in `wait_healthy`. Explicit target verification requires a valid generation response, including target response identity where supplied. Missing/mismatched identity is unverified, not proof of target loading (boot fails; if Phase 1 proves the pinned runtime omits `model`, reject the pin rather than adding an operator override in A). Selection source of truth is config/explicit load: permit explicit model paths omitted by the catalogue and display catalogue disagreement without claiming residency. Maintain one bounded timeout budget and owned-launch failure cleanup.
- `src/mlx_tui/models_pane.py`, `src/mlx_tui/swap.py` — record selection and clear current-success implications before switching; update failure state on every failure. Replace “loaded/serving” claims with request-scoped success and residency unknown. Keep a reload/retry hint for the retained selection/previous model. Preserve operation leases and configured command policy. Guard deletion of selected/last-observed models both before confirmation and before mutation, explaining the conservative protection without claiming exhaustive knowledge of external clients.
- `src/mlx_tui/status_bar.py`, `src/mlx_tui/table.py` — add state words, selected and last-response labels, and a selected column replacing loaded. Keep status readable with long names in narrow terminals. Use GiB for binary values and label fits as disk-size headroom, not guaranteed runtime fit.
- `src/mlx_tui/process.py` — match endpoint address/port in initial and cached listener verification; treat ambiguous matches, uncertain localhost address-family attribution and permission denial as unknown. Preserve PID creation-time validation. RSS sampling in `app/__init__.py` stays observational and never writes generation evidence. Process-wide RSS must not be attributed to the selected model as exclusive model memory.
- `tests/unit/test_status.py`, `tests/unit/test_process.py`, `tests/unit/test_serverctl.py`, `tests/unit/test_boot.py`, `tests/unit/test_swap.py`, `tests/unit/test_table.py` — replace catalogue-as-loaded assertions; cover singleton/empty catalogue, health disagreement, response evidence, ambiguous listeners and PID reuse.
- `tests/conftest.py`, `tests/integration/test_app_integration.py`, `tests/integration/test_swap_integration.py`, `tests/integration/test_boot_integration.py`, `tests/integration/test_delete_integration.py` — distinguish health/catalogue stub routes; cover failed switch, stale poll, endpoint change, selection retention, deletion rechecks and unchanged explicit command policy.

**Success Criteria:**

#### Automated Verification:
- [x] State/lifecycle regressions pass: `rtk uv run pytest -q tests/unit/test_status.py tests/unit/test_process.py tests/unit/test_serverctl.py tests/unit/test_boot.py tests/unit/test_swap.py tests/integration/test_app_integration.py tests/integration/test_swap_integration.py tests/integration/test_boot_integration.py` (implementation result: 163 passed).
- [x] Full suite, including deletion/table regressions, passes: `rtk uv run pytest -q` (implementation result: 487 passed, 5 opt-in runtime tests skipped).
- [x] Static checks pass: `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, `rtk uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Inspect fresh attach, success, failed load and another client’s model request: none advertises current residency.
- [ ] At 80 columns and narrower, state words remain understandable; RSS shows sampled process memory in GiB or unknown. Quitting does not claim to stop an attached server.

### Phase 3: Preserve streams and correct metrics

**UI dependency/handoff:** Reuse the persistent `ChatTurn` transcript and
`_pending_draft`/`_commit_success()`/`end_turn()` recovery seams delivered by
`2026-09-07-ui-layout-and-chat-recovery.md`. Adapt these to A’s new outcome/commit
matrix rather than rebuilding them. Metrics, reasoning/tool handling, context
labels and real-runtime evidence remain owned by this milestone.

Extend the existing transport result using the actual Phase 1 wire contract. Work in this order, keeping each edit small: (3a) `sse.py` framing/terminal contract → (3b) `chat.py` result fields and `stream_turn` → (3c) `chat_pane.py` rendering and history commit → (3d) metrics/store/sparkline replacement → (3e) builders/unit tests → (3f) integration tests. Do not leave old metric constructors or labels behind.

**Changes:**
- `src/mlx_tui/sse.py` — retain framing helpers but return an explicit terminal with the payloads (`DONE_SEEN` vs `EOF` vs `TRUNCATED`/protocol error) so the transport can set `stream_complete` without guessing. Parse pinned-runtime `reasoning` separately from `content`; preserve indexed tool IDs/types/names/argument fragments without execution. Reject negative/boolean token counts (`bool` is an `int` subclass — check `type(x) is int`) and inconsistent cached counts. Add alternate reasoning keys only if captured evidence requires them.
- `src/mlx_tui/chat.py` — extend `TurnResult` with proposed `reasoning_text: str`, `tool_calls: tuple[dict[str, object], ...]` (minimal shape `{index, id, type, name, arguments}` matching the captured OpenAI shape), `response_model: str | None`, `first_output_s: float | None`, `answer_started_s: float | None`, `total_s: float`, `cached_prompt_tokens: int | None`, and `stream_complete: bool`. These new fields are **verify during implementation**. First output means first nonempty reasoning/answer/tool output, excluding role/usage frames; answer start means first answer text. Measure both and completion from one pre-request monotonic timestamp. No output means null latency.
- In `stream_turn`, merge tool fragments by index and retain argument strings. Missing expected terminal markers, malformed output frames and protocol failures make the result incomplete. Choose terminal requirements from Phase 1 captures; explicitly distinguish premature graceful EOF. Preserve HTTPX cleanup/cancellation guarantees. Add only the progress callback needed to show reasoning/tool activity before answer text.
- `src/mlx_tui/chat_pane.py` — render reasoning separately and show tool data as unexecuted. Publish response evidence to app state. Commit matrix: ordinary complete answers insert user+assistant into future request history; length-capped, damaged, tool-only, and empty outcomes display in the transcript without inserting either side into future history (pending user stays out so retry resends the same context). Restore the prompt after failure/cancellation and preserve prior history/operation leases.
- `src/mlx_tui/sse.py`, `src/mlx_tui/history/store.py`, `src/mlx_tui/chat_pane.py` — remove `prefill_tok_s`. Replace ambiguous TTFT with nullable first-output/answer/total timings and store an explicit outcome. Define rate as all server completion tokens / full request duration, labeled client request tok/s. Complete + missing usage → estimate all observed output and mark estimated; incomplete → unknown regardless of usage. Failed/cancelled counts/timings are unknown, never fabricated zero measurements.
- `src/mlx_tui/status.py`, `src/mlx_tui/app/__init__.py`, `src/mlx_tui/chat_pane.py`, `src/mlx_tui/history/store.py` — remove connectivity-derived cold claims and filtering. Ordinary chat has unknown load/cache state; only the controlled runtime experiment labels first-after-launch. Cached-token usage reports server reuse, not guaranteed residency.
- `src/mlx_tui/metrics_pane.py`, `src/mlx_tui/history/sparkline.py` — replace prefill/decode/TTFT with first output, answer start, total and client request rate. Show em dashes for unknowns and mark estimates. Filter unsuccessful/unknown rates consistently. Keep RSS sampling distinct from allocator peaks; latest unknown attribution must not silently display an older sample as current.
- `src/mlx_tui/history/tokens.py`, `src/mlx_tui/chat_pane.py` — label estimated input plus reserved output against configured context budget; show excluded prior-turn count. Keep current trimming and the full visible transcript; no tokenizer integration.
- `tests/builders.py`, `tests/unit/test_sse.py`, `tests/unit/test_chat.py`, `tests/unit/test_history.py`, `tests/unit/test_sparkline.py`, `tests/unit/test_tokens.py` — add captured-shape synthetic reasoning/tool, empty output, cached/missing/invalid usage, DONE/EOF and interruption cases. Use a controlled monotonic clock for exact timing assertions.
- `tests/conftest.py`, `tests/integration/test_chat_integration.py`, `tests/integration/test_chat_cancellation.py`, `tests/integration/test_metrics_integration.py`, `tests/integration/test_context_integration.py` — verify rendering, outcome filtering, retry retention, context notices and lease release after cleanup. Update old metric/cold constructors consistently across all test callers.

**Success Criteria:**

#### Automated Verification:
- [x] Full suite exercises the new stream/metric/context cases and passes: `rtk uv run pytest -q` (implementation result: 502 passed, 5 opt-in runtime tests skipped).
- [x] Static checks pass: `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, `rtk uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Real reasoning output progresses before the answer; tools stay unexecuted, and incomplete output cannot appear as successful coding evidence.
- [ ] Compare displayed timing with timed SSE evidence. No inferred engine speed/cold state remains; cancellation says client request cancelled, engine state unknown.
- [ ] Cancel/retry and trigger context trimming: prior messages survive and estimated budget/excluded turns are visible.

### Phase 4: Revalidate and publish the compatibility result
Close A with evidence about the corrected client and an explicit handoff to B.

**Changes:**
- `tests/runtime/test_milestone_a.py` — rerun contracts through corrected `stream_turn` where applicable, alongside raw HTTP evidence. Keep inference opt-in; ordinary CI remains model-free and skip-only.
- `docs/compatibility/milestone-a.md` — publish dated results, exact reproduction commands, evidence locations/checksums, failures, unsupported surfaces and unknowns. Record the candidate pin as tested or rejected; retain initial failures when rerunning. State the precise supported request/stream subset.
- `README.md`, `ARCHITECTURE.md` — in separate checkable edits, correct catalogue/health, selection/response, ownership, completion handling, client metric definitions and GiB/estimate claims. Link the report; distinguish stub coverage from real-server verification.
- `docs/part3.md` — add a dated A/report pointer and evidence-based corrections while retaining historical research dates. State B readiness: pinned model revisions, runtime, fixed coding check and two named available memory tiers must be resolved. Missing hardware or rejected runtime remains a visible blocker.

**Success Criteria:**

#### Automated Verification:
- [x] Standard gates pass: `rtk uv run pytest -q`, `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, `rtk uv run pyrefly check --min-severity warn`. (implementation result: 502 passed, 6 skipped; ruff check/format clean on 63 files; pyrefly 0 diagnostics).
- [x] `rtk uv run pytest -q tests/runtime` passes for all claimed-supported surfaces with the report’s exported configuration on `local-m4-16gib` (maintainer-owned gate, not CI). Skipped inference does not satisfy this acceptance gate. (implementation result: 6 passed on `local-m4-16gib` with `contract-v1-20260908T183324Z-480180d2`; ordinary `pytest -q tests/runtime` still skips model-free with 6 skips).

Implementation note: the thinking-enabled reasoning probe consumes its 96-token budget server-side (finish `length`, no answer text), so `test_corrected_client_stream_turn` asserts the corrected client preserves 402 reasoning chars yet reports `stream_complete` false — matching the commit matrix — instead of forcing a success.

#### Manual Verification:
- [ ] Reproduce on the named Mac and inspect failure, external-client and cancellation behavior; unknown surfaces remain unknown.
- [ ] Review every support claim against a real result. B receives exact model/settings/check identities and explicit hardware constraints, not a premature recommendation.

## Out of Scope
- Compare UI, chosen-profile persistence, recommendation publication and recruitment (B).
- Managed installation/shutdown, daemon operation and broad runtime/version matrix (C/E/F).
- Durable chat, attachments, tokenizer integration and tool execution (D or later).
- Allocator/engine telemetry adapters, log scraping, optimization controls, scheduler work or a benchmark platform.
- Runtime installation, model downloads or inference during this planning task.

## Risks & Mitigations
- Candidate runtime/model support is not established → test before promotion; reject the pin if required contracts fail.
- Second tier/model revisions are unavailable during planning → select from actual implementation resources; keep B blocked until resolved.
- Response model echoes the request and external clients may switch immediately → retain dated request-scoped evidence and residency unknown.
- State changes affect selection, load, deletion and memory together → update all consumers in Phase 2 and run lifecycle regressions.
- Stream shapes/usage vary by model → use captured contracts and hide metrics with incomplete accounting.
- Strict completion changes permissive behavior → test normal pinned endings and premature graceful EOF separately.
- Runtime probes consume unified memory → dedicated endpoint, finite deadlines and bounded workloads; record failures rather than forcing memory pressure.
- Staged work may change before execution → reconcile this draft against the then-current tree without reverting unrelated changes.

Planning verification: lint passed; formatting passed (60 files); strict type check passed (0 diagnostics, 108 suppressed); focused state/lifecycle suite passed (153 tests). Full suite passed: 450 tests in 95.02 seconds. No real inference verification occurred.
