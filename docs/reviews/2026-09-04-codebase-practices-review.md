# `mlx-tui` Service Intent and Codebase Review

**Date:** 2026-09-04  
**Scope:** current working tree, including pre-existing uncommitted changes  
**Purpose:** identify bad practices and anti-patterns to address before adding features or publishing

## Executive summary

`mlx-tui` has a coherent purpose: an `htop`-like, one-screen control plane for one local MLX server. It shows liveness, model identity, unified-memory pressure, real chat performance, and one-key model swaps. Chat is mainly an instrumentation and load-generation surface, not the product by itself ([idea](../idea.md#L3-L18), [README](../../README.md#L3-L17)).

The codebase is compact and its automated baseline is healthy: lint and formatting pass, the strict type checker exits successfully, and all 247 tests pass. The main problems are boundary and lifecycle defects, not widespread broken code. Most importantly, the UI can combine HTTP health from one server with model/RSS state from another process. Several fallback paths also report model or token metrics with more confidence than the available evidence supports.

## Priority overview

| ID | Priority | Finding | Core effect |
|---|---|---|---|
| F1 | High | Endpoint and process identity are incoherent | UI can describe two different servers as one |
| F2 | High | Swap success is optimistic and policy differs from docs | loaded-model marker can lie |
| F3 | High | Operations are not serialized over their full lifecycle | chat/swap/delete races and stranded busy UI |
| F4 | High | Context limit does not bound the request | context errors despite a green bar |
| F5 | High | Fallback metrics count chunks, not tokens | misleading throughput, the product's key output |
| F6 | High | Config has competing, lossy sources of truth | removed values persist; `max_ctx` resets |
| F7 | High | Download set is narrower than MLX-LM's load set | “complete” downloads may fetch again or fail later |
| F8 | Medium | Command construction is ambiguous and partly unsafe | restart can launch the old model or interpolate shell text |
| F9 | Medium | Model lifecycle cleanup is incomplete | hangs/exceptions can leave controls disabled |
| F10 | Medium | Cancellation is narrower than the UI claim | work can continue after “cancelled” |
| F11 | Medium | Standards portability is overclaimed | valid SSE and secure remote endpoints are unsupported |
| F12 | Medium | Private APIs plus open-ended dependency ranges | fresh installs can break outside the lockfile |
| F13 | Medium | Package split fights ownership and typing | cycles, forwarding methods, 59 type suppressions |
| F14 | Medium | Target-platform and destructive-flow tests are missing | macOS-specific regressions can pass CI |
| F15 | Low | Broad exception swallowing obscures faults | defects look like ordinary “down” or missing UI state |
| F16 | Low | Primary docs and release metadata have drifted | maintainers and users receive conflicting contracts |

## Findings

### F1 — Endpoint and process identity are incoherent

Liveness comes from the configured host/port ([polling](../../src/mlx_tui/app/polling.py#L22-L32)), while RSS and command-line model state come from a pidfile, a global cached PID, or the first local process whose arguments resemble an MLX server ([process discovery](../../src/mlx_tui/process.py#L35-L50)). These identities are never correlated, and `/v1/models` data is discarded after green/amber classification.

With two MLX servers—or a remote `--host`—the dot can describe one endpoint while RSS, the loaded marker, deletion guard, health wait, and chat payload describe another. This undermines the core “truthful control plane for one server” promise.

**Recommendation:** introduce one `ServerIdentity` containing endpoint, model reported by `/v1/models`, optional validated PID, and PID creation time. Use the endpoint response as authoritative model identity. Require a validated pidfile or endpoint-aware listener lookup for RSS; show RSS unavailable for remote endpoints.

### F2 — Swap success is optimistic and differs from the documented policy

The README says that configuring both start and stop commands makes swaps restart the server ([README](../../README.md#L66-L83)). The code instead always takes the warm-request path when cached status is green and considers restart only when it is not ([swap selection](../../src/mlx_tui/models_pane/swap_ops.py#L17-L44)). The warm probe verifies only a 2xx JSON body with `choices`, then writes the selected repo into tracked state without re-reading the server ([probe](../../src/mlx_tui/serverctl.py#L72-L90), [commit](../../src/mlx_tui/models_pane/swap_ops.py#L56-L75)).

MLX-LM supports a request `model` field, so warm loading is a reasonable optimization, but an arbitrary completion is not proof that the target model became active ([official server documentation](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md)).

**Recommendation:** expose an explicit `warm`/`restart`/`auto` policy. Verify the response model and/or `/v1/models` before committing loaded state. If the endpoint cannot prove the target, report uncertainty instead of synthesizing authority.

### F3 — Chat, swap, and deletion lack one lifecycle coordinator

`has_live_turn` becomes true only after an HTTP response exists ([chat state](../../src/mlx_tui/chat_pane/__init__.py#L36-L38), [assignment](../../src/mlx_tui/chat_pane/turn.py#L82-L89)). A restart can begin during connect/request setup, and the warm path does not check or cancel chat ([swap](../../src/mlx_tui/models_pane/swap_ops.py#L26-L38)). Deletion uses a separate worker group without acquiring swap state or disabling the table ([worker](../../src/mlx_tui/models_pane/__init__.py#L80-L88), [delete](../../src/mlx_tui/models_pane/delete.py#L56-L86)).

**Recommendation:** create one UI-thread-owned operation coordinator. Mark chat active before launching its worker; serialize chat, load/restart, and delete; await cancellation and cleanup before beginning a conflicting operation.

### F4 — Context trimming does not enforce the configured limit

The trimmer budgets message content only and deliberately keeps the newest message even if it exceeds the budget ([trimmer](../../src/mlx_tui/history/tokens.py#L24-L37), [test](../../tests/unit/test_history.py#L69-L79)). The system prompt is prepended afterward, while `max_tokens` and chat-template overhead are not reserved ([payload](../../src/mlx_tui/chat_pane/turn.py#L62-L81)). The context bar excludes the same costs.

**Recommendation:** budget `system + template overhead + retained messages + output reserve <= max_ctx`. Prefer a model tokenizer; otherwise use a conservative estimate. Reject or explicitly truncate a single over-limit user message.

### F5 — Fallback metrics count SSE chunks, not tokens

Without server usage, output “tokens” and tok/s are based on the number of text-bearing deltas ([stream](../../src/mlx_tui/chat.py#L63-L104), [accounting](../../src/mlx_tui/sse.py#L62-L82)). One delta can contain a fragment or many tokens. Prompt fallback counts only the newest user message rather than the retained request ([turn](../../src/mlx_tui/chat_pane/turn.py#L69-L81)).

**Recommendation:** estimate from the complete response and complete retained payload. Carry typed numeric fields and an explicit provenance/estimated flag instead of parsing formatted strings with `_tok_int`. Label prefill as client-observed because TTFT also includes connection and scheduling latency.

### F6 — Configuration has competing and lossy sources of truth

State is duplicated across the file, `app.config`, parameter widgets, and `_system_prompt`. Applying config only updates non-`None` values, so deleting `system` does not clear the active prompt ([bridge](../../src/mlx_tui/chat_pane/params.py#L55-L75)). The next turn writes widget values back into config ([turn sync](../../src/mlx_tui/chat_pane/turn.py#L36-L60)). Applying a preset reconstructs `AppConfig` but omits `max_ctx`, resetting a custom limit to 8192 ([preset](../../src/mlx_tui/app/presets_ctrl.py#L17-L37)).

**Recommendation:** use one runtime settings object; make config application total, including clearing removed values; and use `dataclasses.replace` for preset changes so unrelated settings survive.

### F7 — The download filter is narrower than MLX-LM's loader

The TUI downloads `*.safetensors`, `*.json`, and `tokenizer*` only ([filter](../../src/mlx_tui/search.py#L18-L20), [download](../../src/mlx_tui/search.py#L136-L148)). Current MLX-LM loader patterns also include files such as `*.py`, `*.tiktoken`, `tiktoken.model`, `*.txt`, `*.jsonl`, and `*.jinja` ([official loader](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/utils.py)). Hugging Face defines `allow_patterns` as the snapshot filter ([official implementation](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/_snapshot_download.py)).

The TUI may therefore call a snapshot downloaded while omitting a template or tokenizer/model support file; model load can fetch again outside the TUI's estimate/progress or fail offline.

**Recommendation:** intentionally match MLX-LM's supported pattern set and revision. If custom Python is excluded for safety, expose that as an explicit compatibility limitation.

### F8 — Start-command construction is ambiguous and partly unsafe

Any command containing the substring `--model` is returned unchanged even when a different model is selected, and tests codify this behavior ([builder](../../src/mlx_tui/serverctl.py#L16-L24), [test](../../tests/unit/test_serverctl.py#L20-L42)). Command-line inspection recognizes `--model value`, not `--model=value` ([parser](../../src/mlx_tui/process.py#L54-L63)). `{model}` substitution is unquoted and commands run with `shell=True` ([process launch](../../src/mlx_tui/serverctl.py#L16-L38)).

**Recommendation:** parse argv and replace the exact model option in both forms. Make shell mode explicit; when used, quote placeholder values.

### F9 — Lifecycle cleanup is not guaranteed

Boot orchestration lacks an outer `try/finally`; controls are released only through anticipated branches ([boot](../../src/mlx_tui/models_pane/swap_ops.py#L86-L161)). `stop_cmd` has no timeout or cancellation ([runner](../../src/mlx_tui/serverctl.py#L41-L45)). Unexpected process/callback errors or a hung stop command can leave swap state busy and controls disabled.

**Recommendation:** give every operation one failure boundary and unconditional cleanup. Add stop/start deadlines, retain child-process handles, and marshal state transitions to the UI thread.

### F10 — Cancellation is narrower than the UI claim

Chat abort can close a socket only after `_active_response` exists ([abort](../../src/mlx_tui/chat_pane/turn.py#L166-L180)); before then, the blocking worker has no cooperative token. Failed/cancelled user messages remain in later context ([submit](../../src/mlx_tui/chat_pane/__init__.py#L81-L109), [errors](../../src/mlx_tui/chat_pane/turn.py#L139-L163)). Download cancellation is checked only during tqdm updates, but the modal immediately reports cancellation and closes ([progress](../../src/mlx_tui/search.py#L102-L130), [close](../../src/mlx_tui/search_screen/download.py#L137-L153)).

**Recommendation:** make transport cancellation cover connect/read/download; commit conversation turns transactionally; publish “cancelled” only after worker acknowledgement or say “cancellation requested.”

### F11 — Compatibility claims exceed HTTP/SSE support

The SSE parser requires the exact prefix `data: ` and treats each line as an independent JSON event ([parser](../../src/mlx_tui/sse.py#L10-L19)). The WHATWG standard permits an optional space and joins consecutive data fields until the blank event boundary ([standard](https://html.spec.whatwg.org/dev/server-sent-events.html)). URLs are always unauthenticated plain HTTP with no configurable base path ([client](../../src/mlx_tui/app/__init__.py#L172-L176), [chat](../../src/mlx_tui/chat_pane/turn.py#L80)).

**Recommendation:** narrow the claim to the verified MLX wire format, or adopt a compliant parser plus base URL, TLS, and optional auth. Warn on non-loopback plain HTTP.

### F12 — Private APIs are combined with open-ended dependency ranges

Code imports a private Hugging Face module even though the types are publicly exported, and cancellation reaches into `network_stream._sock` ([imports](../../src/mlx_tui/models.py#L7-L10), [socket workaround](../../src/mlx_tui/chat_pane/turn.py#L172-L177)). Runtime dependencies have lower bounds only, and Rich is used directly but declared only transitively ([metadata](../../pyproject.toml#L9-L15)).

**Recommendation:** use public exports, declare direct dependencies, isolate/version-check private fallbacks, and test minimum plus current dependency sets.

### F13 — The package split fights ownership and typing

Pane methods mostly forward to free functions that reach back into private widget/app state; local imports break resulting cycles ([chat facade](../../src/mlx_tui/chat_pane/__init__.py#L111-L190), [search facade](../../src/mlx_tui/search_screen/__init__.py#L88-L151), [models facade](../../src/mlx_tui/models_pane/__init__.py#L39-L88)). There are 59 source `type: ignore`/`pyrefly: ignore` directives. Pyrefly exits with zero errors while reporting 84 suppressions and 10 hidden warnings.

**Recommendation:** keep cohesive controller behavior on its class, or extract typed services/protocols that do not access widget-private fields. Make warnings visible and remove suppressions incrementally.

### F14 — CI misses target-platform and destructive-flow risk

macOS Apple Silicon is the meaningful target ([README](../../README.md#L19-L24)), but CI is Ubuntu-only ([workflow](../../.github/workflows/ci.yml#L11-L24)). The macOS/private-socket cancellation path, delete confirmation/orchestration, config-editor flow, CLI entry point, and built artifact lack focused CI coverage.

**Recommendation:** retain fast Linux checks and add a macOS job for process, cancellation, and Textual smoke tests. Add delete/config/CLI cases, then build and isolated-install wheel/sdist and run `mlx-tui --help`.

### F15 — Broad exception swallowing hides defects

There are 14 `except Exception` sites. Polling maps every exception to red ([polling](../../src/mlx_tui/app/polling.py#L22-L31)); context UI errors and config synchronization failures can disappear silently ([chat](../../src/mlx_tui/chat_pane/__init__.py#L94-L105), [turn](../../src/mlx_tui/chat_pane/turn.py#L36-L60)).

**Recommendation:** catch expected exceptions narrowly and keep one diagnostic top-level boundary per worker.

### F16 — Documentation and release metadata have drifted

Architecture docs still describe a single status string, 8000 fixed context, no prefill column, and 231 tests ([status](../../ARCHITECTURE.md#L16-L19), [context](../../ARCHITECTURE.md#L40-L43), [tests](../../ARCHITECTURE.md#L134-L141)). Part 2 says it is not started despite completed Phase 4 code ([status](../part2.md#L108-L110)). README documents a different swap policy, omits `max_ctx`, and points its license section to the wrong line. The project declares MIT, but no license file is tracked or included in the inspected wheel ([README](../../README.md#L131-L133), [metadata](../../pyproject.toml#L1-L7)).

**Recommendation:** regenerate architecture docs from current behavior, avoid volatile test counts/line claims, document the chosen swap/context contracts, and add/include an MIT `LICENSE`.

## Recommended repair sequence

1. Restore truthful endpoint/model/PID identity and add two-server/external-restart tests.
2. Introduce one lifecycle coordinator with unconditional cleanup and acknowledged cancellation.
3. Make swap policy explicit and verify the target before updating UI state.
4. Repair full-request context budgeting and response/request-based metric estimates.
5. Make config application total and preserve unrelated fields through presets.
6. Align download patterns/revisions with the supported MLX-LM contract.
7. Remove misleading state-machine/facade ceremony, broad catches, cycles, and suppressions.
8. Add macOS/artifact/destructive-flow CI, update docs, and add the license file.

## Strengths worth preserving

- clear product thesis and deliberate MLX-only scope;
- compact domain helpers that are straightforward to test;
- guarded polling and generally correct worker-to-UI mutation hops;
- lockfile plus a broad, fast integration suite;
- user-oriented error messages;
- sensible warnings rather than hard blocks for approximate memory/disk fit.

## Verification and limitations

```text
uv run ruff check .                         passed
uv run ruff format --check src tests        passed (54 files formatted)
uv run pyrefly check                        passed; 0 errors, 84 suppressed, 10 warnings hidden
uv run pytest -q                            passed; 247 tests in 38.60s
```

Read-only reproductions confirmed the existing-model command behavior, `--model=` detection gap, over-budget payload construction, multi-line SSE parsing gap, and chunk-based fallback accounting. The existing wheel contains all package subpackages and the console entry point but no license file; it predates the current working-tree changes.

No live model load, real multi-server test, network-stall cancellation test, or macOS UI render was performed. Those remaining gaps require real runtime scenarios rather than more source inspection.
