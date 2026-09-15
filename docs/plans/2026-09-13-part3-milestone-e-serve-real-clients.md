# Milestone E — Serve Real Clients: Gated Qualification

**Date:** 2026-09-13
**Work Item:** n/a
**Status:** In Progress

## Overview

Add inspectable client setup and reproducible qualification for both a standalone HTTP client and OpenCode, while correcting state claims that external requests can invalidate. Keep supported shared serving blocked on the pinned upstream runtime: completing this implementation does not achieve Milestone E without enforceable lifecycle behavior and observed endpoint reuse.

The user selected the gated qualification approach and requested both HTTP and coding-app coverage. OpenCode is the concrete coding-app target; Pi is an alternative for a later qualification pass. The three checkpoints are policy/evidence, endpoint UI and state correctness, then client contracts and observed use.

## Current State

- `docs/part3.md:378` defines Pillar 5: copyable base URL/model/example, real-client streaming, cancellation, errors and tool round trips. Lines 387–390 explicitly require gating shared serving when stock upstream cannot enforce the model-switch policy.
- `docs/compatibility/milestone-a.md` records the pinned MLX-LM commit `74e7cf931e84ef7c2f63e875adf414e20decc1c5`, MLX 0.32.2, and Qwen3-1.7B-4bit revision `3b1b1768f8f8cf8351c712464f906e86c2b8269e` on `local-m4-16gib`. Tools were text rather than structured calls; invalid `top_p` disconnected; engine cancellation remains unknown.
- Pinned upstream source was read locally at `/Users/alvarogomez/.cache/mlx-tui-contract-74e7cf9/lib/python3.13/site-packages/mlx_lm/server.py`. Request parsing at line 1112 accepts `model`, `draft_model` and `adapters`; `ModelProvider.load` at line 369 loads a changed tuple; `_load` at line 313 clears the old target before attempting replacement. The CLI at line 1720 has no model restriction flag. Reproduction source: [immutable server.py](https://github.com/ml-explore/mlx-lm/blob/74e7cf931e84ef7c2f63e875adf414e20decc1c5/mlx_lm/server.py).
- `src/mlx_tui/operations.py:34` coordinates only operations inside this TUI. `src/mlx_tui/managed.py:710` launches stock upstream, and offline settings at line 705 do not restrict switching among cached/local targets.
- `src/mlx_tui/managed.py:692` synthesizes success for a live child with the same launch target. External switching can invalidate that shortcut. Callers route through `src/mlx_tui/app/__init__.py:878`, including `src/mlx_tui/models_pane.py:222` and `src/mlx_tui/compare_pane.py:1040`.
- `src/mlx_tui/app/__init__.py:423` polls health independently from the catalogue. Selection and response observations are already separate (`select_model`, line 592; `record_generation_success`, line 626). `src/mlx_tui/status_bar.py:80` nevertheless labels a healthy endpoint “Ready”.
- `src/mlx_tui/text_screen.py:46` provides `TextPreviewScreen` with selection and exact copy; reuse it. App bindings are in `src/mlx_tui/app/__init__.py:82`.
- `src/mlx_tui/chat.py:86` provides `stream_turn`; tool fragments are preserved but a tool-only response is deliberately not a successful ordinary chat answer. External tool qualification must not weaken that rule.
- `tests/runtime/conftest.py:24` provides `EvidenceRecorder`; `tests/runtime/test_milestone_a.py:241` checks two raw HTTP requests and disconnect recovery, not repeated TUI plus coding-app workloads. `tests/runtime/test_milestone_c.py:61` demonstrates explicit opt-in input validation and retained runtime/model provenance.
- `src/mlx_tui/comparison_contracts.py:63` provides strict explicit-IP loopback URL validation. Its coding prompt/expected answer at line 25 can be reused without executing generated code.
- `tests/integration/test_app_integration.py` already covers explicit selection, external catalogue changes, status and keyboard journeys; `tests/unit/test_managed.py:248` covers owned-child lifecycle.
- OpenCode `1.18.28` is installed (`rtk proxy opencode --version`). `rtk proxy opencode run --help` verifies `--pure`, `--dir`, `--model`, `--format json`, and session continuation. No inference or user configuration changes were performed during research.
- OpenCode's documented custom provider uses `@ai-sdk/openai-compatible`, `options.baseURL` and a model map for Chat Completions. Verify the generated configuration against installed 1.18.28 during implementation; current online documentation may evolve. [Provider documentation](https://opencode.ai/docs/providers#custom-provider).
- Use isolated configuration and explicitly allow only the local provider. `OPENCODE_CONFIG` is an override, not complete isolation; global/project/inline/managed configuration can also apply. `--pure` disables external plugins, not tools. Generate a fail-closed permission policy for the exercise and verify it against installed 1.18.28. [Configuration documentation](https://opencode.ai/docs/config), [permission documentation](https://opencode.ai/docs/permissions/).

Baseline verification on the existing dirty working tree: `rtk proxy uv run ruff check .` passed; `rtk proxy uv run pyrefly check` reported 0 errors; `rtk proxy uv run pytest -q tests/unit/test_managed.py tests/unit/test_status.py` passed 28 tests; `rtk proxy uv run pytest -q tests/runtime` skipped all 9 opt-in contracts. `rtk proxy uv run pytest` completed with 623 passed, 9 skipped and two existing multithreaded-fork deprecation warnings in 202.28 seconds. Preserve all unrelated staged, unstaged and untracked work.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Gate supported shared serving | Cooperative client convention; proxy; patched server; explicit gate | User selected the gate. A convention cannot enforce isolation; a proxy or server patch introduces new lifecycle responsibilities. |
| Qualification is separate from support | Treat HTTP success as support; track implementation, contracts, enforcement and reuse independently | Happy-path tests cannot satisfy the missing enforcement or demand evidence. No UI action enables supported sharing. |
| Qualify HTTP and OpenCode 1.18.28 | HTTP only; OpenCode; Pi; all harnesses | User requested both classes. One named coding app bounds the compatibility claim. |
| Reuse a text preview | New tab/config wizard; existing modal | The endpoint needs an inspect/copy action, not another permanent workspace. |
| Same-target managed activation performs a real probe | Trust launch state; restart every time; probe existing child | A request provides fresh request-scoped evidence without unnecessary restarts. It can reload the target, so it must remain an explicit activation action. |
| Use existing httpx and pytest | Add an SDK/runtime dependency; raw client plus subprocess harness | Keep inference upstream and the standalone client independent of the TUI parser. OpenCode stays an external optional test dependency. |
| Structured tools are a separate required result | Accept JSON-looking text; verify tool-call ID/arguments and result round trip | A tool request accepted by HTTP is not evidence that a coding harness can use it. No arbitrary generated code is executed by the HTTP runner. |

## Implementation Phases

### Phase 1: Define the supported boundary and qualification record

Document exactly what can be investigated and why shared serving is blocked before adding client setup surfaces.

**Changes:**
- `docs/clients.md` — new guide: define the required policy as one pinned model/adapter/draft tuple for both clients; mismatched requests must be rejected before loading, and lifecycle changes must refuse or drain active work. State that stock `74e7cf9` cannot enforce either guarantee across external clients, and that an app-local lock, offline mode, client model allowlist or startup `--model` flag does not fix this. This is a product support gate, not a network access restriction on an operator's existing endpoint.
- `docs/clients.md` — describe isolated qualification only: same explicit snapshot target, no concurrent Compare/model activation/deletion, no background server lifetime, and explicit cancellation/recovery checks. Managed shutdown stops its child; attach shutdown does not. The operator ends external work before intentional restart; the TUI cannot detect all external work or promise a drain.
- `docs/compatibility/milestone-e.md` — new evidence record with initial values: implementation pending, HTTP qualification not run, OpenCode qualification not run, model enforcement blocked, tools unverified for E, engine cancellation unknown, user reuse not observed. Record runtime/model/client versions, source or artifact hashes, machine, launch settings, workload parameters, dates, all attempts/failures and retained evidence paths.
- `docs/compatibility/milestone-e.md` — include a consent-based observation table with anonymous participant ID, client/version, task, assistance, first use, later separate-session use, outcome, failure/recovery, reason to reuse or abandon, and consent scope. Leave it empty rather than inventing participants or numerical adoption thresholds.
- `docs/part3.md` and `README.md` — link the guide/evidence with an explicit “qualification only; shared serving blocked” status. Preserve B/C/D's outstanding validation gates. Describe the full E gate as enforcement plus repeated workloads plus target-user reuse; a completed implementation plan cannot substitute for any of these.

New file paths and newly proposed symbols in this plan are implementation targets, not existing files; verify them during implementation.

**Success Criteria:**

#### Automated Verification:
- [x] Existing behavior remains intact: `rtk proxy uv run pytest`.
- [x] Lint and type checks remain green: `rtk proxy uv run ruff check .` and `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] Guide distinguishes investigation, verified request features, enforceable shared serving and observed adoption; no checkbox silently upgrades support.
- [ ] Evidence tables explicitly retain missing facts and current B/C/D blockers.

### Phase 2: Expose endpoint details and remove stale readiness claims

Add a keyboard-accessible snapshot of endpoint facts and qualification examples without launching clients or changing configuration.

**Changes:**
- `src/mlx_tui/app/__init__.py` — add a visible `F3` “Endpoint” binding and proposed `action_endpoint_info(self) -> None`. Build the snapshot at invocation from `self.host`, `self.port`, `effective_model()`, `server_identity` and runtime mode; open `TextPreviewScreen` titled “Endpoint — shared serving unqualified”. Do not cache the snapshot across model/config changes.
- `src/mlx_tui/app/__init__.py` and `src/mlx_tui/managed.py` — canonicalize every actual app, preview and managed-probe HTTP URL by bracketing IPv6 literals before adding the port; test an `::1` app end to end, not only its preview text. Show base URL ending in `/v1`, exact selected request model (including snapshot paths), last response identity and the separately labelled last verified-success time, current reachability, residency unknown, shared-serving block reason, and attach/managed shutdown behavior. A mismatch after an older success must not present that older timestamp as the mismatched response time. With no model, show “select a model first” and omit runnable generation examples. Non-loopback endpoints get details and an unsupported-scope explanation, with no qualified local-client recipe. Validate generated loopback chat URLs using `parse_loopback_url`. Do not change existing attach connection support while doing this.
- `src/mlx_tui/app/__init__.py` — include an inspectable synthetic curl example with explicit model and bounded output, plus a JSON OpenCode configuration example for provider `mlx-tui-local` targeting the same model. Serialize JSON with `json.dumps` and shell arguments with `shlex.join`; hostile model strings must stay literal. Copying does not execute requests or write OpenCode settings. Label examples unqualified until matching retained evidence exists; no automatic support promotion is added.
- `src/mlx_tui/status_bar.py` — replace the healthy “Ready” word with “Reachable”; keep selected and last-response labels and failure/cancellation states. A health poll must not imply generation readiness, residency or a model lock.
- `src/mlx_tui/managed.py` — replace the same-target synthetic-success return in `ManagedRuntime.start` with the existing `serverctl.wait_healthy` probe against the explicit target, using retained child identity, the existing cancellation event and existing timeout. Recheck listener ownership after the probe. A failed/cancelled probe raises `ManagedRuntimeError` without returning success, preserves enough child ownership for later safe close/retry, and does not spawn another child. Preserve existing failed-new-child cleanup. Leave `current_model` as launch bookkeeping, not a residency claim.
- `tests/unit/test_managed.py` — extend the owned-child test pattern to repeat activation: fresh probe is required, success retains the same child, and failed/cancelled probe or listener mismatch cannot synthesize success. Always clean up the test child.
- `tests/integration/test_app_integration.py` — add focused endpoint-preview coverage: F3 works from existing tabs; Escape returns focus/draft; long model identifiers and 80×24 layout remain usable; absent target and non-loopback scope omit recipes; an actual `::1` app mounts its HTTP client and produces bracketed valid URLs; shell/JSON metacharacters stay literal; unhealthy and changed-catalogue states do not imply residency. Assert absent/present last-response identity and last-success timestamp rendering, freshness after a later successful response, the mismatch-after-success distinction, and “Reachable” for health-only success.

**Success Criteria:**

#### Automated Verification:
- [x] Managed activation and response classification pass: `rtk proxy uv run pytest -q tests/unit/test_managed.py tests/unit/test_status.py`; the full integration suite is the `Ready` → `Reachable` wording guard.
- [x] Endpoint keyboard, literal-copy and state regressions pass as part of `rtk proxy uv run pytest`.
- [x] Lint/type checks pass: `rtk proxy uv run ruff check .` and `rtk proxy uv run pyrefly check`.

#### Manual Verification:
- [ ] At 80×24 and 120×40, open Endpoint, select/copy URL/model/example, close it, and continue the unchanged draft.
- [ ] Reopen after selection/config changes and verify fresh values. A healthy endpoint with no successful generation reads “Reachable”, never a residency assertion.
- [ ] Managed repeated activation either obtains fresh request evidence or reports failure; no success is inferred from the original launch target.

### Phase 3: Qualify both clients and retain the limits

Add opt-in real-runtime contracts and a bounded OpenCode exercise, retaining every result without turning qualification into an enforcement claim.

**Changes:**
- `tests/runtime/test_milestone_e.py` — new opt-in suite following C's fixture style. Inputs: `MLX_TUI_E_QUALIFY=1`, `MLX_TUI_E_URL` (the full explicit-IP loopback `/v1/chat/completions` URL accepted by `parse_loopback_url`; derive the origin and `/v1` base from its canonical result), `MLX_TUI_E_MODEL` (verified absolute pinned snapshot), `MLX_TUI_E_RUNTIME_ROOT`, `MLX_TUI_E_MACHINE_TIER`, and absolute `MLX_TUI_E_OUTPUT`. Missing opt-in skips; opted-in missing/invalid inputs fail. Load the pinned `qwen3-1.7b-baseline` profile, verify its repository/revision with `verify_profile_snapshot`, and require `MLX_TUI_E_MODEL` to equal that verified entry's snapshot. Use `inspect_runtime` for runtime evidence. No default endpoint contact, downloads, server start/stop or environment installation.
- `tests/runtime/test_milestone_e.py` — bind the listener to that evidence before inference: require the live PID/create time and recorded argv to match, the interpreter path to be under `MLX_TUI_E_RUNTIME_ROOT`, and the exact `--model` argument to equal the verified snapshot, following Milestone B's process checks. Add a negative preflight test proving that an unrelated runtime root or model cannot qualify the listener.
- `tests/runtime/test_milestone_e.py` — use a locally isolated `MlxTuiApp.run_test()` session attached to the supplied live endpoint, temporary session/config data, explicit target, and real `ChatPane` submission. Run an independent raw-httpx external request concurrently with TUI generation for five bounded cycles. Include non-stream text, SSE completion, longer bounded output, cancellation on each client before/after first output, and follow-up recovery. Verify both requests actually overlap via events; do not infer overlap solely from simultaneous task creation. Use finite deadlines and retain timeout/error outcomes. Check TUI draft recovery, selection stability and honest observation labels; do not use `stream_turn` as the sole external client.
- `tests/runtime/test_milestone_e.py` — cover malformed JSON, invalid sampler, missing model and a nonexistent absolute model/adapter/draft target on a dedicated qualification server, then recover the pinned target. These intentionally disruptive cases run after mixed work settles. Record HTTP status versus protocol disconnect, not a fabricated clean error contract. Keep wrong-target enforcement explicitly failed/unavailable if upstream accepts switching; a 404 for a nonexistent target does not prove isolation.
- `tests/runtime/test_milestone_e.py` — test a fixed synthetic tool schema: preserve structured call ID, function name and JSON arguments; return a constant tool result using that ID; require a subsequent final answer. Exercise non-stream and streamed tool fragments. If the pinned model returns ordinary text instead, record unavailable and leave tool-dependent coding-app qualification blocked. Never execute model-written code or shell commands. Preserve ordinary TUI tool-only completion semantics.
- `tests/runtime/test_milestone_e.py` — retain schema-versioned metadata and per-attempt outcomes using `EvidenceRecorder` and `try/finally`. Include sequence/cycle/client IDs, timestamps, prompt/check identity, limits/sampling settings, terminal state, first-output/total timing, client-close timing and recovery result; request-echoed model is not independent residency proof. Store fixed synthetic prompts/outputs only, never actual user transcripts or credentials. Record raw protocol defects and unsuccessful trials before assertions terminate a test.
- `docs/clients.md` — add the standalone `uv run python`/httpx request example and generated curl recipe using the same explicit target. Document route scope: `/health`, `/v1/models`, and `/v1/chat/completions`; Responses, embeddings and universal OpenAI compatibility remain untested. Examples must be copied from the actual implemented/validated output, not manually divergent payloads.
- `docs/clients.md` — add OpenCode 1.18.28 setup for an isolated disposable coding directory: provider `mlx-tui-local`, `@ai-sdk/openai-compatible`, loopback `/v1` URL, exact model map, local-only enabled providers and explicit model selection. Use separate XDG config/data/state/cache directories and `--pure`; remove inherited provider credentials and config-injection variables (`OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_TUI_CONFIG`, and `OPENCODE_AUTO_SHARE`) before setting the harness-owned values. Disable sharing. Do not overwrite the operator's global configuration. Set main and auxiliary model selection to the qualified local model and verify effective routing. Any client installation/provider-package fetch is a separate setup step; verify offline inference after prerequisites are cached.
- `tests/runtime/test_milestone_e.py` — add a separately opted-in OpenCode case (`MLX_TUI_E_OPENCODE=1`). Check executable version is 1.18.28 before running. Generate isolated config with `permission["*"] = "deny"`, allow `read` only for the exact harmless fixture path, and explicitly deny external-directory access, edits/writes, shell, tasks/subagents, web access, skills, questions and any custom/MCP tools; do not use `--auto`. Verify installed 1.18.28 accepts the policy and that sharing is disabled. Launch `opencode run --pure --format json --dir` with explicit `--model` as argv, finite subprocess deadlines, bounded output retention and owned-process cleanup. This invocation and generated config are **verify during implementation**: only version/help were run during planning. Reject any emitted tool other than `read` or any read argument not resolving to the exact fixture before counting success; never approve or execute an unexpected call.
- `tests/runtime/test_milestone_e.py` — run five bounded OpenCode cycles, each retaining its own outcome: a small text probe followed by a real read-tool/result/final-answer exercise while the TUI serves bounded requests. Require the exact fixture content in the final answer; a text-only response, unexpected tool, or wrong read path fails coding-harness qualification. In separate retained attempts, cancel once before the first JSON event and once after the first event by terminating the owned OpenCode process/session, enforce deadlines, record exit/event/client-close timing, then start a fresh bounded recovery request and require success. Manual cancellation may supplement but not replace this automated protocol.
- `docs/compatibility/milestone-e.md` — record five mixed cycles for each client on the named Mac, plus the automated OpenCode before/after-first-event cancellations, TUI cancellation, retries, configured-model inspection and offline exercise. Record actual request behavior and auxiliary calls; absence of evidence stays unverified. Raw HTTP success cannot substitute for running OpenCode. Include CPU/power/concurrent-load observations without claiming per-request RSS or a new performance target.
- `docs/compatibility/milestone-e.md` — when consenting target users actually perform later work with the endpoint, record separate-session reuse, intervention and reasons. Qualification on an isolated server may continue while shared serving is gated; do not recruit users into an advertised supported shared mode. If enforcement cannot be supplied or tool/client qualification fails, retain the evidence and explicitly defer full E rather than adding a proxy in this phase.
- `docs/part3.md` and `README.md` — update only to achieved implementation/qualification facts. Keep the enforcement gate closed for stock upstream even if cooperative workloads pass. Pi receives no compatibility claim from OpenCode results.

**Success Criteria:**

#### Automated Verification:
- [x] Default runtime discovery remains model-free: `rtk proxy uv run pytest -q tests/runtime` (new E tests skip without opt-in).
- [x] The full regression suite passes: `rtk proxy uv run pytest`.
- [x] Lint/type checks pass: `rtk proxy uv run ruff check .` and `rtk proxy uv run pyrefly check`.
- [ ] With validated E environment supplied, run `rtk proxy uv run pytest -q tests/runtime/test_milestone_e.py` and retain all outputs. **Verify during implementation:** this is a proposed new test file, so the live command cannot run during planning. A structured-tool or coding-client failure leaves that capability unqualified; it is not converted to a skip or a pass. (2026-09-14: suite implemented, 9 tests skip without opt-in; no live endpoint was available in this environment, so the live run is still pending.)
- [x] OpenCode version/help remain available before optional execution: `rtk proxy opencode --version` and `rtk proxy opencode run --help`; retain the tested executable/version and generated config hash with live results. (2026-09-14: `1.18.28` plus `--pure`/`--dir`/`--model`/`--format json` confirmed; config hash retained only with live results, still pending.)

#### Manual Verification:
- [ ] Both an independent HTTP client and real OpenCode run alongside the TUI for the named, repeated workloads; no unexplained success, switch or lost request is hidden in the record.
- [ ] Five OpenCode text-plus-read cycles and both before/after-first-event cancellation cases complete with retained deadlines, process/session actions and fresh recovery requests, or the exact blocker is retained and coding-harness support remains unqualified.
- [ ] Managed quit behavior and attach non-ownership are visibly understood; intentional shutdown during client activity is recorded as interruption, not a graceful drain guarantee.
- [ ] Target-user separate-session reuse is observed with consent, or explicitly remains an unmet product gate.
- [ ] Final evidence still shows supported shared serving blocked until wrong-target rejection and lifecycle coordination are enforceable and independently tested.

## Out of Scope

- Enabling supported shared serving on the current unrestricted upstream server.
- A proxy, custom scheduler, inference loop, telemetry sidecar, server fork or automatic model routing.
- Background daemon lifetime, LAN/multi-user exposure, remote-code opt-in changes or broad release packaging.
- Pi qualification in addition to the selected OpenCode target; a later pass can reuse the protocol.
- Cloud credentials, analytics, automatic edits to external-client configuration, or collection of real prompt content by default.
- General agent execution inside the TUI, benchmarks/leaderboards, allocator attribution or verified engine cancellation without evidence.
- Claiming B/C/D validation or E adoption from implementation tests.

## Risks & Mitigations

- Cooperative clients pass while unrestricted switching remains possible → maintain an explicit enforcement gate independent of test outcomes; neither a green health check nor a client-side allowlist changes it.
- A fresh activation probe can reload the selected model and interfere with external work → make it an explicit activation action, keep shared serving gated, and document coordination limits; do not run hidden generation probes from polling or the endpoint preview.
- A managed same-target failure loses ownership metadata → test failure/cancellation/mismatch paths and preserve safe close/retry behavior without spawning another child.
- Qwen3-1.7B's tool behavior prevents useful OpenCode operation → retain the failed evidence; a different pinned tool-capable model requires a separate qualification choice, not an unsupported recommendation or automatic download.
- OpenCode reads inherited configuration, invokes an unexpected tool or uses an auxiliary remote model → isolate directories/environment, clear config-injection variables, use `--pure`, install a deny-by-default exact-read permission policy, disable sharing, validate every emitted tool argument, constrain provider/model selection, and verify offline use before claiming local-only operation.
- Live tests disrupt a user's work → require opt-in and a dedicated server; perform wrong-target/error cases only after workload completion and always run recovery. No live inference is part of creating this plan.
- UI state or copied model text becomes misleading/unsafe → build each preview from current facts, preserve timestamps and unknown residency, serialize values rather than interpolating executable shell text, and add literal-copy tests.
- Full milestone remains blocked after all implementation phases → report completed code separately from enforcement, qualification and reuse; do not mark E achieved or bypass Part 3's defer condition.
