# Restore Truthful Server Control and Telemetry

**Date:** 2026-09-04
**Work Item:** n/a
**Status:** Complete

## Overview

Fix the seven high-priority findings in `report-source.md` so the TUI has one authoritative view of the configured MLX server, does not overlap conflicting lifecycle operations, and reports bounded requests and trustworthy measurements. The plan preserves the existing Textual/pure-function architecture while adding explicit identity, operation, swap-policy, context, metric, configuration, and download contracts.

## Current State

The repository is a local-first Textual control plane for one MLX server. The current working tree already contains the status memory bar, context bar, prefill/decode surface, and other recent changes; this plan must preserve those changes and address the remaining high-priority boundary defects.

- `src/mlx_tui/app/__init__.py:122-151` stores `host`, `port`, immutable `AppConfig`, `_tracked_model`, and a legacy `_SwapShim`; `on_mount()` creates one `httpx.AsyncClient` at `src/mlx_tui/app/__init__.py:172-186`.
- `src/mlx_tui/app/polling.py:22-65` gets `/v1/models` only to produce a liveness color, then independently calls `process.find_server_pid()` for RSS and model rendering.
- `src/mlx_tui/process.py:35-51` validates a pidfile, then a process-global cached PID, then returns the first matching process from `psutil.process_iter()`; `model_from_cmdline()` at `src/mlx_tui/process.py:54-63` only recognizes the split `--model value` form.
- `src/mlx_tui/app/state.py:19-51` gives `_tracked_model` precedence over command-line inspection, so warm swaps can override what the endpoint reports.
- `src/mlx_tui/models_pane/swap_ops.py:17-75` selects warm loading whenever cached liveness is green, marks `_tracked_model` after any successful `choices` response, and does not share a lifecycle lease with chat or delete. Restart orchestration is at `src/mlx_tui/models_pane/swap_ops.py:86-161`.
- `src/mlx_tui/serverctl.py:72-123` accepts any successful warm probe body and makes `wait_healthy()` compare the target to a callback rather than the `/v1/models` response itself.
- `src/mlx_tui/chat_pane/__init__.py:25-38` considers a turn live only after `_active_response` exists; `src/mlx_tui/chat_pane/turn.py:36-163` builds the request and releases the input in a worker lifecycle that is independent of swap/delete.
- `src/mlx_tui/models_pane/__init__.py:69-88` uses separate Textual worker groups for warm/boot and delete. `src/mlx_tui/models_pane/delete.py:15-86` checks the loaded marker but does not acquire shared operation state.
- `src/mlx_tui/history/tokens.py:24-37` trims message content only and intentionally keeps an oversized newest message. `src/mlx_tui/chat_pane/turn.py:62-81` adds the system message after trimming and sends `max_tokens` without reserving it in the context budget.
- `src/mlx_tui/sse.py:62-83` estimates output from the number of text-bearing SSE deltas and prompt tokens from only `user_chars`; `src/mlx_tui/chat.py:40-51` stores formatted token strings that `src/mlx_tui/chat_pane/turn.py:93-99` reparses with `_tok_int()`.
- `src/mlx_tui/config.py:12-26` defines the frozen `AppConfig`; `src/mlx_tui/chat_pane/params.py:72-76` applies only non-`None` values and leaves a removed system prompt active; `src/mlx_tui/app/presets_ctrl.py:17-37` reconstructs `AppConfig` without preserving `max_ctx`.
- `src/mlx_tui/search.py:18-20,45-51,136-148` uses `ALLOW_PATTERNS = ["*.safetensors", "*.json", "tokenizer*"]` for both size calculation and download, but `src/mlx_tui/search_screen/query.py:97-104` and `src/mlx_tui/search_screen/download.py:55-78` do not retain the `ModelInfo.sha` revision between those operations.
- Existing tests are concentrated in `tests/unit/test_process.py`, `tests/unit/test_serverctl.py`, `tests/unit/test_chat.py`, `tests/unit/test_sse.py`, `tests/unit/test_history.py`, `tests/unit/test_config.py`, `tests/unit/test_search.py`, `tests/integration/test_swap_integration.py`, `tests/integration/test_app_integration.py`, and `tests/integration/test_search_integration.py`. `tests/conftest.py:24-143` provides the stub HTTP server and its `ok`, `probe`, `html`, `no_usage`, and `slow` modes.
- Baseline verification on 2026-09-04 passed: `uv run ruff check .`, `uv run ruff format --check src tests`, `uv run pyrefly check` (0 errors; 84 suppressions and 10 hidden warnings), and `uv run pytest -q` (247 passed).

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Endpoint `/v1/models` is the only loaded-model authority | Keep `_tracked_model`; prefer command-line `--model`; use endpoint data only for liveness | The product promises truth about the active server. The endpoint is the only observation that identifies the server actually answering requests; process command lines and optimistic UI markers are secondary diagnostics. |
| `ServerIdentity` is coordinator-owned and contains endpoint, endpoint model, PID, and PID creation time | Separate unrelated fields on `MlxTuiApp`; use PID as the primary identity; add a backend abstraction | One immutable snapshot makes endpoint/model/PID generation changes observable and prevents RSS or chat state from silently coming from a different server. |
| Local RSS requires endpoint-correlated process evidence | Keep the first matching MLX process; trust any valid pidfile; disable all RSS | A validated pidfile or a listener on the configured loopback port gives useful local telemetry. Remote endpoints must show RSS unavailable rather than inventing a local association. |
| One operation coordinator rejects conflicting actions until cleanup completes | Keep per-pane worker groups; queue every request; use more boolean flags | A thread-safe `OperationCoordinator` (locked, releases marshalled via `call_from_thread`) makes `idle`, `chatting`, `loading`, `restarting`, and `deleting` explicit. Conflicting actions report that the current operation must finish or be cancelled; no queued request can start before the worker's `finally` releases the lease. |
| `auto` swap policy follows the documented restart contract | Always warm when green; always restart when commands exist; infer policy from cached status | `auto` restarts when both `start_cmd` and `stop_cmd` are configured, otherwise uses warm load only against a healthy endpoint, and uses a configured start command for cold start. Explicit `warm` and `restart` modes fail clearly when their prerequisites are unavailable. |
| Warm and restart success require endpoint confirmation | Treat a 2xx response with `choices` as success; trust the response's optional `model` only; update the marker optimistically | The probe response is useful as an early check, but a subsequent `/v1/models` read must identify the requested model before UI state changes. Unknown or mismatched identity is a failed swap. |
| Context uses a conservative request estimate and rejects an oversized newest message | Keep the current content-only estimate; silently truncate the newest message; add a tokenizer dependency | A pure estimator can include system/template overhead and reserve `max_tokens` without new dependencies. Explicit rejection preserves user text and prevents a green bar from accompanying an over-limit request. |
| Metrics carry numeric values plus provenance | Continue formatted strings and parse them; count SSE frames; hide estimates | Numeric fields and `prompt_estimated`/`out_estimated` flags let stamps and the Metrics table disclose uncertainty while estimating output from the complete response and prompt from the complete retained request. |
| `AppConfig` is the runtime source of truth | Keep `_system_prompt` and widget values independently authoritative; rebuild config field-by-field for presets | Widgets are views of the frozen runtime config. `dataclasses.replace()` preserves unrelated settings, and total application clears values removed from disk. |
| Mirror the supported MLX-LM file set and pin one snapshot revision per download flow | Keep only weights/JSON/tokenizer files; calculate current size but download a later revision; always use the moving default branch | The supported list from the report includes `*.safetensors`, `*.json`, `tokenizer*`, `*.py`, `*.tiktoken`, `tiktoken.model`, `*.txt`, `*.jsonl`, and `*.jinja`. The selected `ModelInfo.sha` must feed both size and download so “complete” has one meaning. |

## Implementation Phases

### Phase 1: Establish authoritative server identity

Make one `/v1/models` probe carry both liveness and model identity, and make optional process telemetry prove that it belongs to the configured local endpoint.

**Changes:**

- `src/mlx_tui/status.py` — add frozen `ServerProbe(state: str, model_id: str | None)` and a pure `probe_from_response(status_code: int, body: object) -> ServerProbe`. Extract the first non-empty string `id` from a valid `data` list; keep `classify_liveness()` as a compatibility wrapper returning `probe.state` so existing pure liveness tests remain meaningful.
- `src/mlx_tui/status.py` — add frozen `ServerIdentity(host: str, port: int, model_id: str | None = None, pid: int | None = None, pid_create_time: float | None = None)`; this is the snapshot held by the app coordinator.
- `src/mlx_tui/process.py` — add frozen `ProcessIdentity(pid: int, create_time: float)`, a `LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})` constant, and replace global-first discovery with `find_server_process(host: str, port: int, pidfile: str | None = None) -> ProcessIdentity | None`. Return `None` immediately for non-loopback hosts. Validate pidfile/cached candidates by MLX command line, listening socket port, and unchanged `create_time`; otherwise inspect `psutil.net_connections(kind="tcp")` for a listener on the configured port and validate that process. Treat `(psutil.AccessDenied, PermissionError, RuntimeError)` from `net_connections()` as `None` and cache the permission-denied outcome to avoid a syscall storm on every 2s poll; short-circuit on a cached `(pid, create_time)` hit before scanning connections. Return `None` for remote endpoints, missing permissions, stale/recycled PIDs, and unmatched listeners. Delete `find_server_pid()` and `model_from_cmdline()` entirely so no active-model authority can linger; diagnostics must use the endpoint probe, not command lines.
- `src/mlx_tui/app/state.py` — remove `_tracked_model` precedence and command-line model fallback. Add `update_server_identity(app, probe: ServerProbe, process_identity: ProcessIdentity | None) -> None` using `dataclasses.replace()`; `effective_model(app)` returns only the model in the latest green endpoint probe. A changed endpoint or PID creation time replaces the snapshot rather than retaining a stale tracked model. Leave the temporary `_SwapShim` in place until Phase 2 removes the obsolete swap state surface.
- `src/mlx_tui/app/__init__.py` — initialize `self.server_identity = ServerIdentity(self.host, self.port)`, remove `_tracked_model`, and expose the endpoint snapshot through the existing `effective_model()` facade. Keep the temporary `swap_machine` shim until Phase 2 replaces it with `OperationCoordinator`; leave `host`/`port` as the configured client endpoint and preserve CLI/config host-port restart behavior.
- `src/mlx_tui/app/polling.py` — add `fetch_server_probe(app) -> ServerProbe`, retain `classify_liveness_for(app) -> str` as a state-only wrapper, and have `poll_tick()` use one probe plus `find_server_process(app.host, app.port, app.config.pidfile)`. Only scan for a process when the endpoint is loopback; render the probe model only when the endpoint is green, calculate RSS only from `ProcessIdentity.pid` (catching `NoSuchProcess`, `AccessDenied`, and `ZombieProcess`), and update `server_identity` before refreshing table markers, memory history, and metrics. `server_identity` tracks `app.host`/`app.port` (restart-required), not `app.config` host/port.
- `src/mlx_tui/app/swap_ctrl.py` — update cold-start process lookup to `find_server_process(...).pid` and remove assumptions that a process command line supplies the authoritative model.
- `src/mlx_tui/models_pane/delete.py` — use `effective_model()` only as the endpoint-derived deletion guard; remove `_tracked_model` cleanup because there will be no optimistic tracked marker.
- `tests/unit/test_status.py` — add probe parsing cases for valid IDs, missing IDs, multiple entries, malformed JSON shapes, and non-200 responses while preserving the existing `classify_liveness()` assertions.
- `tests/unit/test_process.py` — replace global-scan assumptions with fakes for `psutil.net_connections()` and process creation times. Cover two MLX processes on different ports, a valid pidfile on the wrong port, recycled PID creation time, remote host returning no process, `AccessDenied` from `net_connections()` returning no process, and no RSS authority when no endpoint listener is found.
- `tests/integration/test_app_integration.py` and `tests/conftest.py` — let `StubServer` expose a mutable `model_id`, then verify that polling, the loaded table marker, and the chat payload follow the configured server’s `/v1/models` ID even when a fake unrelated MLX process reports another command-line model. Add an external-restart case where the endpoint model changes and the next poll replaces the old identity.
- `tests/integration/test_swap_integration.py` — update process seams to `find_server_process()` and replace `_tracked_model` assertions with `server_identity.model_id`/`effective_model()` assertions.

**Success Criteria:**

#### Automated Verification:

- [x] Endpoint probe and process correlation tests pass: `uv run pytest -q tests/unit/test_status.py tests/unit/test_process.py tests/integration/test_app_integration.py`
- [x] No production callsite uses `_tracked_model`, `model_from_cmdline`, or `find_server_pid`: `rg -n "_tracked_model|model_from_cmdline|find_server_pid" src` (both helpers are deleted in this phase, so any match fails the gate).

Deviation: `src/mlx_tui/models_pane/swap_ops.py` also dropped its `set_tracked_model` calls (not listed in Phase 1) because the gate forbids any `_tracked_model` substring and `effective_model()` no longer reads the optimistic marker; warm/boot now rely on the next endpoint poll for the loaded marker until Phase 3 adds confirmed identity updates.

#### Manual Verification:

- [ ] With two local stub servers on different ports, the TUI pointed at server A shows server A’s model; changing server B’s model does not change the TUI, and RSS is absent when the configured endpoint is remote or has no correlated local listener.

### Phase 2: Serialize the full operation lifecycle

Replace per-pane busy assumptions with one coordinator that owns the complete lifetime of chat, load/restart, and delete operations and always restores the UI after worker exit.

**Changes:**

- `src/mlx_tui/app/operations.py` — add `OperationKind` with `IDLE`, `CHATTING`, `LOADING`, `RESTARTING`, and `DELETING` (failure returns to `IDLE` plus a log line; there is no stuck `FAILED` state), plus thread-safe `OperationCoordinator` with `current`, `try_acquire(kind) -> bool`, `release(kind) -> None`, and `is_busy`/`is_swap_busy` properties guarded by a `threading.Lock`. Reject a conflicting request without changing the current lease; release must be idempotent only for the owning kind so a late worker cannot clear a newer operation. Workers never mutate `current` directly: they release only via `app.call_from_thread(coordinator.release, kind)` so the owner check runs on the UI thread. Add a two-thread racing acquire/release unit test.
- `src/mlx_tui/app/__init__.py` — initialize `self.operations`, make `swap_busy` read `operations.is_swap_busy`, and remove `_cold_start_in_flight`, `self.swap_machine`, its setter, the `_SwapShim` import, and all `SwapState` imports. Rely solely on `coordinator.try_acquire()` (no dual in-flight flag). Keep the existing facades, but route chat/swap/delete guards through the coordinator.
- `src/mlx_tui/app/swap_ctrl.py` — rename the UI helper to `set_operation_ui(app, busy: bool)` and disable/restore the models table, chat input, and swap progress based on the coordinator. The coordinator is the sole writer of `#chat-input.disabled` for swap/delete; `end_turn()` may only re-enable input when `coordinator.current in (IDLE, CHATTING)`. Cold start/restart must acquire `RESTARTING` before health checks or worker launch and release only when the boot worker finishes; a conflicting chat must be cancelled explicitly and remain leased until `end_turn()` acknowledges cleanup.
- `src/mlx_tui/chat_pane/__init__.py` — add `_turn_active: bool`, make `has_live_turn` use it, acquire `CHATTING` before appending the submitted user message or starting `_run_turn`, and reject input while any other operation is active. Set `_turn_active` synchronously before the worker starts so connect/setup cannot race a swap.
- `src/mlx_tui/chat_pane/turn.py` — move response/active-state cleanup into the UI-thread `end_turn()` path, and release `CHATTING` via `call_from_thread(coordinator.release, CHATTING)` from the worker's unconditional `finally` only after the stream worker has stopped. Keep socket close/cancellation behavior, but do not publish “cancelled” or re-enable controls before the worker's terminal callback; `end_turn()` re-enables `#chat-input` only when the coordinator is `IDLE` or `CHATTING`.
- `src/mlx_tui/models_pane/swap_ops.py` — acquire `LOADING` for warm operations and `RESTARTING` for boot operations before setting progress or launching a worker; reject while chat/delete/another swap owns the coordinator. Wrap both `run_warm_swap_impl()` and `run_boot_impl()` in one `try/except/finally` boundary that resets operation state, clears progress, and logs unexpected failures on the UI thread.
- `src/mlx_tui/models_pane/delete.py` and `src/mlx_tui/models_pane/__init__.py` — acquire `DELETING` after confirmation and before cache mutation, disable the table/input for the whole delete worker, and release in `finally` on success, cancellation, guard failure, or exception. Keep the active-model recheck immediately before deletion.
- `src/mlx_tui/swap.py` — remove the unused `SwapState` enum after all callsites/tests move to `OperationKind` (callers: `app/__init__.py`, `app/swap_ctrl.py`, `models_pane/swap_ops.py`, `app/state.py`; `tests/unit/test_swap.py` only covers `health_timeout()` and needs no change); retain `BootPlan` and `health_timeout()`.
- `src/mlx_tui/serverctl.py` — add `run_command(cmd, *, on_line, timeout_s: float = 30.0)` raising `subprocess.TimeoutExpired` on expiry for `stop_cmd` used by lifecycle workers, and make `run_boot_impl()` own its spawned `proc` handle: `proc.terminate()` (then `kill()` after a 5s grace) on failed startup/health timeout only, never on success where the long-running server keeps running. Preserve streaming output and the existing successful long-running-server behavior.
- `tests/unit/test_operations.py` — create this new file with focused coordinator tests for acquisition/rejection, owner-only release, idle recovery, the distinction between `CHATTING`, `LOADING`, `RESTARTING`, and `DELETING`, and a two-thread racing acquire/release test.
- `tests/integration/test_app_integration.py` — add a pre-response chat test that attempts swap/delete while `_turn_active` is already true, and assert no conflicting worker starts. Assert controls are restored only after chat cancellation and after delete/swap worker completion.
- `tests/integration/test_swap_integration.py` — replace `SwapState` assertions with coordinator state assertions and cover unexpected worker exceptions, stop timeout, start timeout, and a failed health wait leaving the coordinator idle and controls enabled.

**Success Criteria:**

#### Automated Verification:

- [x] Coordinator and lifecycle tests pass: `uv run pytest -q tests/unit/test_operations.py tests/integration/test_app_integration.py tests/integration/test_swap_integration.py`
- [x] No high-priority operation path relies on the temporary shim or stale tracked flags as its authority: `rg -n "swap_machine|_cold_start_in_flight|_tracked_model" src/mlx_tui`

#### Manual Verification:

- [ ] Start a slow chat request and try load, restart, and delete: each conflicting action is refused or requests cancellation without starting concurrently; after the worker exits, the input and model table become usable again.
- [ ] Force a stop/start command failure and confirm the app remains interactive, the progress line clears, and a later operation can be started.

### Phase 3: Make swap policy and success evidence explicit

Align documented behavior with implementation and make every warm/restart result depend on the model that the endpoint actually reports.

**Changes:**

- `src/mlx_tui/config.py` — add `SwapPolicy = Literal["auto", "warm", "restart"]` and `AppConfig.swap_policy: SwapPolicy = "auto"`; accept only those three TOML strings, fall back to `"auto"` for invalid types/values, and add the key to `CONFIG_TEMPLATE`.
- `src/mlx_tui/serverctl.py` — add frozen `WarmLoadResult(response_model: str | None)`; have `warm_load(url, repo_id, *, timeout_s: float) -> WarmLoadResult` validate the JSON response and return the optional string `model` while still requiring non-empty `choices`. Change `wait_healthy(url, *, target_model: str | None, is_running: Callable[[], bool] | None, timeout_s: float, on_tick: Callable[[int], None] | None) -> ServerProbe | None`, parse each `/v1/models` response directly with `probe_from_response()`, and require `probe.state == "green"` plus `probe.model_id == target_model` when a target is supplied; remove only the `current_model` callback from the signature and keep `is_running`/`on_tick`/`timeout_s`.
- `src/mlx_tui/models_pane/swap_ops.py` — add a deterministic policy resolver with this priority: amber endpoint → refuse regardless of commands (proxy squatting); both `start_cmd` and `stop_cmd` configured → restart (even when green); green endpoint without restart commands → warm load (`auto` only; explicit `warm` requires green, explicit `restart` requires both commands); red endpoint with a `start_cmd` → cold-start path; otherwise refuse with a clear log. Use a short probe timeout for `warm_load()` plus the full `health_timeout()` budget for the subsequent `wait_healthy()` (do not hold `LOADING` for 2× the full deadline). After a warm probe, reject a present mismatching response model and always call `wait_healthy()` against `/v1/models`; only a returned `ServerProbe` matching the target may be applied to `server_identity` through `call_from_thread()`.
- `src/mlx_tui/app/swap_ctrl.py` — use the configured `swap_policy` when cold-start/restart is requested and apply confirmed `ServerProbe` data through the state helper rather than setting a tracked model.
- `README.md` — document `swap_policy = "auto" | "warm" | "restart"`, state that `auto` restarts when both commands exist, and remove the claim that all green-endpoint loads are implicitly warm. Include `swap_policy` in the configuration example.
- `tests/unit/test_config.py` — cover valid policies, invalid policy fallback, and default `auto`.
- `tests/unit/test_serverctl.py` — update warm-load return assertions and `wait_healthy()` tests to use endpoint model IDs rather than `current_model`; add a successful target probe, wrong endpoint model, missing target ID, and probe timeout case.
- `tests/integration/test_swap_integration.py` — cover each policy branch, verify both configured commands force restart under `auto`, verify warm success updates identity only after matching `/v1/models`, and verify a successful `choices` response followed by a wrong model leaves the old marker unchanged and logs failure.

**Success Criteria:**

#### Automated Verification:

- [x] Policy/config/server-control tests pass: `uv run pytest -q tests/unit/test_config.py tests/unit/test_serverctl.py tests/integration/test_swap_integration.py`
- [x] The README's swap policy and configuration text matches the parser and resolver: `rg -n "swap_policy" README.md src/mlx_tui/config.py src/mlx_tui/models_pane/swap_ops.py` plus `rg -n '"auto"|"warm"|"restart"' README.md src/mlx_tui/config.py src/mlx_tui/models_pane/swap_ops.py`

#### Manual Verification:

- [ ] With both commands configured, `auto` runs stop/start even when the endpoint is green; with no commands, `auto` uses warm load only when healthy; explicit incompatible policies show a clear failure.
- [ ] If the server answers the warm probe but `/v1/models` continues to report another model, the TUI does not show the selected row as loaded.

### Phase 4: Bound the complete chat request by context

Make the request estimate include every input and output reservation, and reject an individual over-limit message before any HTTP request is sent.

**Changes:**

- `src/mlx_tui/history/tokens.py` — add `ContextLimitError(ValueError)` carrying a `reason: str` for the actionable log line, conservative framing constants, frozen `ContextWindow(messages: tuple[Message, ...], input_tokens: int, reserved_tokens: int)` where `reserved_tokens = input_tokens + max_tokens`, `estimate_message_tokens()`, `estimate_prompt_tokens()`, and `prepare_context(messages, system_prompt, max_ctx, max_tokens)`. `estimate_*` helpers must call the shared `estimate_tokens()` (same `CHARS_PER_TOKEN_EST`) plus fixed per-message/template overhead. Count system plus retained messages, per-message/template overhead, and a response overhead; reserve the full configured `max_tokens`. Update `trim_for_context()` to use the same message estimate and raise `ContextLimitError` instead of retaining a newest message that cannot fit.
- `src/mlx_tui/chat_pane/turn.py` — replace the separate trim/system/payload assembly with `prepare_context()`, pass `list(window.messages)` to the request, use `window.input_tokens` for fallback prompt accounting, and use `window.reserved_tokens` for the context bar. Catch `ContextLimitError` before `stream_turn()`, remove the exact appended user-message object (by identity, via a UI-thread helper) so a raced submit cannot drop the wrong turn, log `exc.reason`, and release the operation without making an HTTP request.
- `src/mlx_tui/chat_pane/__init__.py` — make the pre-submit context preview call the same `prepare_context()` contract with the same `max_tokens` source as the send path (parsed widget params via `params.parse_params()`), display the reserved request length in `#ctx-progress`/`#ctx-bar`, and add a UI-thread rollback helper (removing by object identity) for a rejected newest user message. Preserve the existing amber/red thresholds. The context bar shows the *request reservation* (`input + max_tokens`); Metrics `ctx` stays *prompt depth* — keep the labels distinct.
- `src/mlx_tui/history/store.py` — keep `TurnRecord.ctx_len` as the retained input estimate/observed prompt count, so Metrics remains about prompt depth; do not use the output reservation as historical context depth.
- `tests/unit/test_history.py` — replace the test that expects a single huge message to be sent with `ContextLimitError` assertions; add system-prompt, framing-overhead, `max_tokens` reservation, complete-turn trimming, and boundary tests.
- `tests/integration/test_app_integration.py` — add a custom `max_ctx`/`max_tokens`/system-prompt case that captures the outgoing payload and proves its estimated reserved tokens do not exceed `max_ctx`; add an over-limit case that proves the stub server receives no POST, the user message is rolled back, and the input is re-enabled.

**Success Criteria:**

#### Automated Verification:

- [x] Context unit/integration tests pass, including the named rejection and reservation tests: `uv run pytest -q tests/unit/test_history.py::test_prepare_context_rejects_oversize_newest tests/unit/test_history.py::test_prepare_context_reserves_max_tokens tests/integration/test_app_integration.py -k "context or ctx_bar or over_limit"`
- [x] The request builder has one context-preparation path and no content-only trim in the send path: `rg -n "trim_for_context|prepare_context|max_tokens|system_prompt" src/mlx_tui/chat_pane/turn.py src/mlx_tui/history/tokens.py`

#### Manual Verification:

- [ ] A long system prompt plus a large `max_tokens` value visibly consumes the context budget; a message that cannot fit is rejected with an actionable log line and never reaches the server.
- [ ] Normal multi-turn chat still trims oldest complete turns and leaves the context bar synchronized with the request reservation.

### Phase 5: Make fallback metrics numerically trustworthy

Replace SSE-frame counting and formatted-string parsing with typed token accounting based on the complete request and response.

**Changes:**

- `src/mlx_tui/sse.py` — add frozen `TokenAccounting(prompt_tokens: int, completion_tokens: int, prompt_estimated: bool, completion_estimated: bool, tok_s: float)`. Change `token_accounting(*, prompt_tokens: int | None, completion_tokens: int | None, prompt_estimate: int, full_text: str, elapsed: float) -> TokenAccounting` to accept authoritative usage values when present, otherwise use the supplied complete-prompt `prompt_estimate` and `estimate_tokens(full_text)` for completion; remove `counted_deltas` and `user_chars` from the accounting contract.
- `src/mlx_tui/chat.py` — change `TurnResult` to delete `tok_in_str`/`tok_out_str`/`prefill_tok_s` and carry `accounting: TokenAccounting` instead; change `stream_turn(url, payload, *, prompt_estimate: int, on_flush, flush_interval, on_active)` to receive the prepared prompt estimate (passing `user_chars` is removed), accumulate `full_text`, and pass the complete response text to `token_accounting()`. Keep TTFT as the first text-bearing delta and decode rate as completion tokens divided by post-first-text elapsed time; the result must retain whether either count was estimated.
- `src/mlx_tui/chat_pane/turn.py` — remove `_tok_int` use, construct `TurnRecord` from numeric accounting fields and flags, compute prefill only from non-estimated prompt usage, and format `(est)` at the final presentation boundary. Keep the existing cold/finish-reason/skipped-frame notices.
- `src/mlx_tui/history/store.py` — extend `TurnRecord` with defaulted `prompt_estimated: bool = False` and `out_estimated: bool = False` fields after existing optional fields so existing test fixtures and in-memory callers remain constructible.
- `src/mlx_tui/metrics_pane.py` — preserve numeric table columns while marking estimated prompt/output cells with a consistent `~` suffix and retaining cancelled/cold styling. Do not parse display strings to recover numbers.
- `src/mlx_tui/history/tokens.py` — retain `estimate_tokens()` as the shared fallback estimator and remove `_tok_int()` once no caller remains.
- `tests/unit/test_sse.py` — update accounting tests to assert numeric fields/flags; add `test_token_accounting_single_long_delta` and `test_token_accounting_frame_count_independent` (single long delta vs multiple short deltas with identical full text) to prove output estimates depend on response length rather than frame count.
- `tests/unit/test_chat.py` — pass the complete prompt estimate to `stream_turn()`, assert usage-backed and fallback-backed `TokenAccounting`, and keep the active-response/stream error tests.
- `tests/unit/test_history.py` and `tests/integration/test_app_integration.py` — update `TurnRecord` shape assertions (field count grows by two), verify flags survive storage, assert fallback output is greater than one for a long response, and verify estimated cells/stamps remain visibly marked.

**Success Criteria:**

#### Automated Verification:

- [x] Typed metrics tests pass, including the frame-independence tests: `uv run pytest -q tests/unit/test_sse.py::test_token_accounting_single_long_delta tests/unit/test_sse.py::test_token_accounting_frame_count_independent tests/unit/test_sse.py tests/unit/test_chat.py tests/unit/test_history.py tests/integration/test_app_integration.py -k "token or metric or prefill or stamp"`
- [x] No production code parses formatted token strings or counts deltas for fallback metrics: `rg -n "_tok_int|counted_deltas|user_chars|tok_in_str|tok_out_str" src`

#### Manual Verification:

- [ ] A server that sends one long text delta and no usage reports an estimated output proportional to the full response, not `1 (est)`; the stamp and Metrics table visibly disclose that it is estimated.
- [ ] A usage-backed turn continues to show exact prompt/output values and prefill/decode measurements.

### Phase 6: Make configuration and presets total and lossless

Use `AppConfig` as the single runtime settings object, clear values removed from disk, and preserve unrelated settings when applying presets.

**Changes:**

- `src/mlx_tui/chat_pane/__init__.py` — remove the independently authoritative `_system_prompt` writer; keep `_system_prompt` only as a plain cache synced totally from `cfg` in `apply_config_params()` (including clearing to `""` when `cfg.system is None`), and make `set_system_prompt()` update `app.config` with `dataclasses.replace()` for the presets path only. Do not add an app-coupled `system_prompt` property reaching into `self.tui.config` (it breaks headless unit tests).
- `src/mlx_tui/chat_pane/params.py` — make `apply_config_params()` total: write either the configured value or the documented input default into every parameter widget, and apply `cfg.system` exactly, including clearing it when `None`. Keep parsing/clamping at the existing input boundaries.
- `src/mlx_tui/chat_pane/turn.py` — read the system prompt from the runtime config and persist widget values with `replace(cfg, temperature=..., top_p=..., max_tokens=..., system=...)`; do not maintain a second prompt source.
- `src/mlx_tui/app/config_edit.py` — retain the previous config on parse failure; after a successful parse, replace the whole `app.config`, call the total `ChatPane.apply_config_params()`, reload presets, and refresh the context bar so removed keys cannot survive in widgets or prompt state.
- `src/mlx_tui/app/presets_ctrl.py` — replace the field-by-field `AppConfig(...)` reconstruction with `dataclasses.replace(app.config, temperature=temp, top_p=top_p, max_tokens=max_tok, system=preset.system.strip() or None)`, preserving model, endpoint, commands, pidfile, `max_ctx`, and `swap_policy`.
- `tests/unit/test_params.py` — create this new file with tests for applying a full config, applying `None` values over an existing prompt/parameter state, and preserving parser defaults in widgets.
- `tests/unit/test_config.py` — retain the current total parse/default tests and add a `dataclasses.replace()` preservation test for the full `AppConfig` field set.
- `tests/integration/test_app_integration.py` — extend preset cycling to set `max_ctx` to `32768` and assert it remains `32768` after every preset; add a config reload case that starts with `system`, reloads a file without `system`, and asserts the outgoing payload contains no stale system message.

**Success Criteria:**

#### Automated Verification:

- [x] Config/preset tests pass: `uv run pytest -q tests/unit/test_params.py tests/unit/test_config.py tests/integration/test_app_integration.py -k "config or preset or params"`
- [x] Preset application has one config update path and does not reconstruct `AppConfig`: `rg -n "AppConfig\(|_system_prompt" src/mlx_tui/app/presets_ctrl.py src/mlx_tui/chat_pane src/mlx_tui/app/config_edit.py` must show no `AppConfig(` construction (only the `system_prompt`/`_system_prompt` cache sync).

#### Manual Verification:

- [ ] Edit the config to remove `system`, save, and confirm the next request has no old system message; edit it back and confirm the prompt reappears.
- [ ] Set a custom context limit, cycle presets with and without optional chat fields, and confirm the context limit, endpoint, and lifecycle settings remain unchanged.

### Phase 7: Align downloads with the MLX-LM loader contract

Use the same supported file patterns and immutable revision for size estimates and downloads so the TUI’s “downloaded” state matches what the MLX loader can use.

**Changes:**

- `src/mlx_tui/search.py` — replace `ALLOW_PATTERNS` with the supported MLX-LM pattern set from `report-source.md`: `*.safetensors`, `*.json`, `tokenizer*`, `*.py`, `*.tiktoken`, `tiktoken.model`, `*.txt`, `*.jsonl`, and `*.jinja` (document beside the constant that `*.py` pulls executable custom code, matching the loader contract). Add frozen `RepoSnapshot(revision: str | None, files: tuple[tuple[str, int], ...])` and `repo_snapshot(api, repo_id) -> RepoSnapshot`, using `ModelInfo.sha` (`None` sha → `None` revision passthrough); extend the `HubApi` protocol to expose `sha`. Keep `filtered_download_size()` consuming the shared patterns. Add `revision: str | None = None` to `download_snapshot()` and pass it to `snapshot_download()` whenever present.
- `src/mlx_tui/search_screen/__init__.py` — add `_revisions: dict[str, str | None]`, pass the selected revision through `_fetch_size()`/`_fill_size_cell()` and `_run_download()`, and clear `_revisions` (with `_sizes`) in `populate()` and on empty-submit clear so stale revisions cannot leak into a new result set.
- `src/mlx_tui/search_screen/query.py` — replace `repo_files_with_sizes()` with `repo_snapshot()`, calculate size from `snapshot.files`, and hand the snapshot revision to the UI-thread cell update.
- `src/mlx_tui/search_screen/download.py` — accept the selected revision in `run_download_impl()` and forward it to `download_snapshot()`; keep progress/cancellation behavior unchanged for this high-priority scope.
- `tests/unit/test_search.py` — update size tests for `RepoSnapshot` (extend `_StubApi.model_info()` to return `sha` alongside `siblings`), include every supported extension in filtered-size assertions, and assert `snapshot_download()` receives the exact selected revision and shared `ALLOW_PATTERNS` object.
- `tests/integration/test_search_integration.py` — update query/download fakes to return and capture revisions; assert a size lookup at revision `rev-a` downloads with `revision="rev-a"` and that the expanded file set is included in both size and download paths.

**Success Criteria:**

#### Automated Verification:

- [x] Search/download tests pass: `uv run pytest -q tests/unit/test_search.py tests/integration/test_search_integration.py`
- [x] The size and download paths share one pattern constant and one revision handoff: `rg -n "ALLOW_PATTERNS|revision|RepoSnapshot|snapshot_download|filtered_download_size" src/mlx_tui/search.py src/mlx_tui/search_screen`

#### Manual Verification:

- [ ] A model containing a Jinja template, tokenizer text/TikToken assets, JSONL metadata, or custom Python support file shows those files in the estimated download size and requests the same revision for the actual snapshot.
- [ ] A completed download causes the existing Models rescan without changing the modal’s cancellation/error behavior.

## Out of Scope

- The medium-priority command construction/security finding: shell quoting, argv parsing, `--model=value`, and explicit shell-mode configuration remain for a separate plan.
- The medium-priority standards compatibility finding: full WHATWG SSE parsing, HTTPS/auth/base-path support, and non-loopback plain-HTTP policy remain unchanged.
- The medium-priority dependency/private-API, package-split/type-suppression, macOS CI, artifact packaging, broad-exception, documentation regeneration, and license findings are not part of this seven-finding repair plan.
- Download cancellation semantics beyond preserving the existing worker/event behavior are not redesigned here.
- No new backend abstraction, persistent history store, tokenizer dependency, automatic model download, or multi-server management UI is introduced.

## Risks & Mitigations

- Endpoint-aware listener discovery can be incomplete on macOS or under restricted process permissions → treat RSS/PID as unavailable, retain endpoint model identity, and test stale/recycled/ambiguous process cases rather than falling back to a global process.
- A compatible server may accept a warm request but not expose the requested model ID → fail the warm operation honestly and leave the prior loaded marker unchanged; the explicit restart policy remains available.
- Coordinator state can be released from the wrong worker or before Textual UI callbacks run → guard `current` with a lock, release only via `call_from_thread` so the owner check runs on the UI thread, make all terminal transitions go through one `finally`, and test late callbacks plus a two-thread acquire/release race.
- Conservative context estimates can reject requests that a particular model tokenizer would accept → use an intentionally conservative fixed overhead, explain the rejection in the log, and leave tokenizer-specific optimization out of scope.
- Changing `TurnResult` and `TurnRecord` from formatted strings to typed fields touches stamps, Metrics rendering, and fixtures → update the transport, worker, store, and table in one phase and keep defaulted history flags for existing constructors.
- MLX-LM’s loader patterns can evolve → keep the pattern list in one named constant, document the supported upstream contract beside it, and make the unit test enumerate every supported extension so future changes are deliberate.
- The working tree contains unrelated uncommitted changes → implement each phase against the current files, avoid reverting existing edits, and run the focused test plus full quality gates after each phase.
