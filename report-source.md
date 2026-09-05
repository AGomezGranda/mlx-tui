# Deep Research: `mlx-tui` Service Intent and Codebase Review

**Audience:** project maintainer  
**Review date:** 2026-09-04  
**Scope:** current working tree, including pre-existing uncommitted changes  
**Decision supported:** which engineering risks should be fixed before adding more features or publishing the package

## Executive answer

`mlx-tui` has a clear and useful thesis: it is an `htop`-like, single-screen control plane for one local MLX server, with chat primarily serving as a load generator and measurement surface. The codebase is compact, its boundaries are understandable, and its automated baseline is healthy: Ruff and formatting pass, Pyrefly exits successfully, and all 247 tests pass.

The main weaknesses are nevertheless consequential because they affect the product's core promise of showing the truth about the active server. The highest-priority problems are:

1. HTTP health and OS-process telemetry can refer to different MLX servers.
2. warm swaps are accepted without verifying the server actually changed model, while the documented restart policy is not implemented.
3. chat, swap, delete, and cancellation operations do not share one lifecycle coordinator.
4. context trimming does not bound the actual request.
5. fallback performance metrics count SSE chunks as tokens.
6. config reloads and presets can preserve removed values or reset unrelated settings.
7. the TUI's Hugging Face download filter is narrower than MLX-LM's loader filter.

There are no observed failing quality gates and no evidence that the repository is generally unstable. The findings are design and boundary defects that the current suite either does not cover or explicitly codifies.

## Service intent

The repository describes the service as a control plane for **one running MLX server**, intended to answer four questions: what is loaded, how much unified memory it consumes, how it performs at realistic context, and whether it can be swapped without manual stop/edit/start work ([idea](docs/idea.md#L3-L18), [README](README.md#L3-L17)). Chat is deliberately an instrument rather than the primary product ([idea](docs/idea.md#L164-L186)).

The intended scope is deliberately narrow:

- MLX (`mlx_lm.server` / `mlx_vlm.server`) and the Hugging Face cache, not a general backend abstraction.
- a local Textual UI, plain OpenAI-style HTTP, and OS inspection through `psutil`.
- in-memory conversation and metric history; no database or persistent chat client.
- user-controlled shell commands for optional stop/start orchestration.

The current architecture is a Textual coordinator with three pane packages, backed by mostly UI-free modules for HTTP/SSE, process inspection, model-cache operations, config, and history ([app](src/mlx_tui/app/__init__.py#L30-L143), [chat transport](src/mlx_tui/chat.py#L54-L125), [process adapter](src/mlx_tui/process.py#L16-L69), [cache adapter](src/mlx_tui/models.py#L13-L103)). That overall shape is appropriate for the service.

## Scope, assumptions, and method

- The executable code and current tests were treated as primary repository evidence. Historical plans/reviews were used only to understand intent or identify drift.
- The review targets the dirty working tree visible on 2026-09-04, not only commit `17843a5`.
- No live MLX model was started. Network contracts were checked against current first-party MLX-LM and Hugging Face sources.
- Findings were independently checked against exact code paths and, where useful, small read-only reproductions.
- Severity reflects the single-user/local-first product. Remote-network and publication risks are therefore lower than they would be for a hosted service.

## Prioritized findings

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

## Detailed analysis

### F1 — Endpoint and process identity are incoherent

**Evidence.** Liveness comes from `GET /v1/models` at the configured host/port ([polling](src/mlx_tui/app/polling.py#L22-L32)). RSS and command-line model state come from a pidfile, a global cached PID, or the **first** local process whose arguments contain an MLX server token ([process discovery](src/mlx_tui/process.py#L35-L50)). The two identities are never correlated by host, port, process start time, or ownership. The model data returned by `/v1/models` is reduced to a green/amber classification and discarded.

**Impact.** With two MLX servers, or when `--host` points away from the local machine, the status dot can describe one endpoint while RSS, the loaded marker, deletion guard, health wait, and chat payload describe another process. This contradicts the product's core “one server, truthful control plane” promise.

**Improve.** Introduce a `ServerIdentity` owned by the coordinator: endpoint, last `/v1/models` model ID, optional validated PID, and PID creation time. Treat the endpoint response as authoritative for model identity. Require a validated pidfile or endpoint-aware listener lookup for RSS; show RSS as unavailable for remote endpoints. Never use “first MLX process globally” as authoritative state.

**Confidence:** high. Existing process tests cover caching and stale PIDs, but not two concurrent servers or endpoint/PID disagreement ([tests](tests/unit/test_process.py#L73-L254)).

### F2 — Swap success is optimistic and policy differs from documentation

**Evidence.** The README says configured `start_cmd` plus `stop_cmd` makes swaps restart the server ([README](README.md#L66-L83)). The code instead always chooses a warm request when the cached status is green; restart commands are considered only when it is not green ([swap branch](src/mlx_tui/models_pane/swap_ops.py#L17-L44)). `warm_load()` checks only for a 2xx JSON response containing `choices` ([probe](src/mlx_tui/serverctl.py#L72-L90)), after which the selected repo is written into `_tracked_model` without querying the server ([commit](src/mlx_tui/models_pane/swap_ops.py#L56-L75)).

Current MLX-LM documentation confirms that a request may include a `model` field, so warm loading is a valid optimization, but it does not justify treating any response as proof of the selected model ([MLX-LM server documentation](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md)).

**Impact.** A server that ignores, normalizes, aliases, or fails to switch on the request model can make the UI assert a false loaded model. A user who configured restart commands does not receive the documented behavior.

**Improve.** Make the policy explicit (`warm`, `restart`, or `auto`). After a warm probe, validate the response's `model` and/or re-read `/v1/models`; only then commit tracked state. If the endpoint cannot prove the target, fail honestly rather than synthesize authority.

**Confidence:** high on repository behavior; medium on how often third-party “compatible” servers ignore the field.

### F3 — Chat, swap, and deletion do not share one operation lifecycle

**Evidence.** `has_live_turn` becomes true only after an HTTP response object exists ([chat state](src/mlx_tui/chat_pane/__init__.py#L36-L38), [response assignment](src/mlx_tui/chat_pane/turn.py#L82-L89)). A restart can therefore begin during connect/request setup; a warm swap does not check or cancel chat at all ([swap branch](src/mlx_tui/models_pane/swap_ops.py#L26-L38)). Deletion has its own worker group and does not acquire swap state or disable the table ([delete worker](src/mlx_tui/models_pane/__init__.py#L80-L88), [delete execution](src/mlx_tui/models_pane/delete.py#L56-L86)).

**Impact.** A warm load may contend with generation, a restart may cross a pre-response chat, and deletion may race a load of the same files. These are particularly risky on a memory-constrained, single-model local server.

**Improve.** Use one UI-thread-owned operation coordinator with explicit states such as `idle`, `chatting`, `loading`, `restarting`, and `deleting`. Mark a turn active synchronously before starting its worker. Serialize conflicting operations and await cancellation/cleanup before moving to the next state.

**Confidence:** high. The suite covers individual paths, not the important cross-operation exclusions.

### F4 — Context trimming does not enforce the configured limit

**Evidence.** `trim_for_context()` budgets only message content and deliberately keeps the newest message even when it alone exceeds the budget ([trimmer](src/mlx_tui/history/tokens.py#L24-L37), [codified test](tests/unit/test_history.py#L69-L79)). Payload construction adds the system prompt after trimming and reserves nothing for `max_tokens` or message-template overhead ([payload](src/mlx_tui/chat_pane/turn.py#L62-L81)). The displayed context estimate likewise excludes these additions.

**Impact.** A green context bar can accompany a request that exceeds the model's context window. Large system prompts and large output caps make this systematic rather than merely approximate.

**Improve.** Budget `system + message/template overhead + retained messages + output reserve <= max_ctx`. Prefer a model tokenizer when available; otherwise use a conservative estimator. Explicitly reject or offer to truncate one over-limit user message instead of silently sending it.

**Confidence:** high.

### F5 — Fallback performance metrics measure SSE framing rather than tokens

**Evidence.** When usage is absent, output “tokens” equal the number of text-bearing deltas, and decode rate equals delta count divided by elapsed time ([stream counter](src/mlx_tui/chat.py#L63-L104), [accounting](src/mlx_tui/sse.py#L62-L82)). An SSE chunk may contain a fragment, one token, or several tokens. Prompt fallback uses only the newest user's characters, not the complete payload ([turn](src/mlx_tui/chat_pane/turn.py#L69-L81)). A reproduction with one arbitrarily long delta returns `1 (est)` output token and `1.0 tok/s`.

**Impact.** The app's defining instrumentation can materially misstate output count, prompt count, and throughput for servers that omit usage. The `(est)` label discloses uncertainty but not that the estimator is unrelated to output length.

**Improve.** Estimate output from `full_text`, prompt from the full retained payload, and carry numeric values plus an `estimated` flag rather than parsing formatted strings through `_tok_int`. Label prefill as client-observed/approximate because TTFT also includes connection and scheduling latency.

**Confidence:** high.

### F6 — Configuration has competing and lossy sources of truth

**Evidence.** State is duplicated across the disk file, immutable `app.config`, parameter widgets, and `_system_prompt`. Config application updates only non-`None` fields and never clears an old system prompt when the key is removed ([bridge](src/mlx_tui/chat_pane/params.py#L55-L75)). The next turn writes widget state back into `app.config` ([turn sync](src/mlx_tui/chat_pane/turn.py#L36-L60)). Preset application reconstructs `AppConfig` field-by-field but omits `max_ctx`, resetting a custom value to 8192 ([preset](src/mlx_tui/app/presets_ctrl.py#L17-L37), [config field](src/mlx_tui/config.py#L12-L26)).

**Impact.** Reloading a file after removing values can leave them active; cycling a preset silently resets the context limit. The UI, file, and outgoing request can disagree.

**Improve.** Establish one runtime settings object. Apply config as a total operation, including explicit defaults and clearing `system=None`. Use `dataclasses.replace(app.config, ...)` for preset changes so unrelated fields survive. Add tests for removal and preservation.

**Confidence:** high; the 32768-to-8192 reset is directly reproducible.

### F7 — Download selection is narrower than the upstream loader's selection

**Evidence.** The TUI downloads only `*.safetensors`, `*.json`, and `tokenizer*` ([filter](src/mlx_tui/search.py#L18-L20), [download](src/mlx_tui/search.py#L136-L148)). Current MLX-LM loader code also allows model/tokenizer support files including `*.py`, `*.tiktoken`, `tiktoken.model`, `*.txt`, `*.jsonl`, and `*.jinja` ([MLX-LM loader](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/utils.py)). Hugging Face documents that `allow_patterns` defines which files form the downloaded snapshot ([snapshot download](https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/_snapshot_download.py)).

**Impact.** The TUI may call a snapshot complete while omitting a chat template, tokenizer assets, or trusted custom model code. Later model load can download additional files outside the TUI's size/progress estimate or fail offline.

**Improve.** Import or intentionally mirror MLX-LM's public/default pattern set for the supported version. Record the chosen revision and pass it consistently to size calculation and download. If custom Python is excluded for safety, surface that as an explicit compatibility limitation rather than “downloaded.”

**Confidence:** high on the filter mismatch; model-specific impact varies.

### F8 — Start-command construction is ambiguous and partly unsafe

**Evidence.** `build_start_command()` returns any command containing the substring `--model` unchanged, even when the user selected a different model ([builder](src/mlx_tui/serverctl.py#L16-L24)). Tests explicitly expect this behavior ([test](tests/unit/test_serverctl.py#L20-L42)). `model_from_cmdline()` recognizes only the split `--model value` form, not `--model=value` ([parser](src/mlx_tui/process.py#L54-L63)). `{model}` substitution is inserted unquoted and all configured commands run with `shell=True` ([builder/popen](src/mlx_tui/serverctl.py#L16-L38)).

**Impact.** A restart can launch the old configured model and wait until timeout for the selected one. Equals-form arguments can prevent model detection. A locally configured model/path containing shell syntax is interpolated into a shell command.

**Improve.** Parse commands as argv by default. Replace or append the exact `--model` option in both supported forms. If shell syntax is intentionally supported, make it an explicit mode and shell-quote placeholder values.

**Confidence:** high.

### F9 — Lifecycle cleanup is not guaranteed

**Evidence.** Boot orchestration has no outer `try/finally`; UI release exists only on enumerated branches ([boot](src/mlx_tui/models_pane/swap_ops.py#L86-L161)). `stop_cmd` has no deadline or cancellation ([command runner](src/mlx_tui/serverctl.py#L41-L45)). Several worker-thread paths mutate the state shim directly while the UI thread reads it.

**Impact.** A hanging stop command or unexpected `Popen`, callback, or health-check exception can leave the swap state non-idle and controls disabled for the rest of the session.

**Improve.** Give every lifecycle operation one `try/except/finally` boundary that releases UI and reports failure. Add stop/start timeouts, retain child-process handles, and marshal state transitions to the UI thread.

**Confidence:** high.

### F10 — Cancellation is narrower than the UI claim

**Evidence.** Chat abort can close a socket only after `_active_response` exists ([abort](src/mlx_tui/chat_pane/turn.py#L166-L180)); before then, a blocking worker has no cooperative cancellation token. Failed/cancelled user messages were already appended and remain in later context ([submit](src/mlx_tui/chat_pane/__init__.py#L81-L109), [error paths](src/mlx_tui/chat_pane/turn.py#L139-L163)). Download cancellation is checked only from tqdm `update()` while the modal immediately logs cancellation and dismisses ([progress hook](src/mlx_tui/search.py#L102-L130), [close](src/mlx_tui/search_screen/download.py#L137-L153)).

**Impact.** Work can continue through connect/read or a blocked Hub call after the user sees “cancelled.” Later chat requests may resend prompts whose turn visibly failed.

**Improve.** Use async/cancellable transport or explicit cancellation tokens across the whole request. Commit conversation turns transactionally. Do not publish the terminal cancellation state until the worker acknowledges it, or clearly distinguish “cancellation requested.”

**Confidence:** high for chat history/pre-response behavior; medium for exact Hugging Face worker behavior.

### F11 — Compatibility claims exceed implemented HTTP/SSE support

**Evidence.** `iter_sse_data()` accepts only the exact prefix `data: ` and parses each such line as a complete event ([parser](src/mlx_tui/sse.py#L10-L19)). The WHATWG standard permits an optional space after `:` and requires consecutive `data` fields to be accumulated until the blank-line event boundary ([WHATWG SSE standard](https://html.spec.whatwg.org/dev/server-sent-events.html)). All URLs are hard-coded as unauthenticated `http://host:port`, with no base path, TLS, or API-key support ([app client](src/mlx_tui/app/__init__.py#L172-L176), [chat URL](src/mlx_tui/chat_pane/turn.py#L80-L80)).

**Impact.** The local MLX happy path may work, but “any OpenAI-compatible server” is too broad. Valid SSE variants fail, and remote prompts are sent in cleartext.

**Improve.** Either narrow documentation to the verified MLX wire format or adopt a standards-compliant SSE parser plus configurable base URL, TLS, and auth headers. Warn or refuse when a non-loopback host uses plain HTTP.

**Confidence:** high on behavior; practical SSE frequency outside MLX is uncertain.

### F12 — Private APIs are coupled to open-ended dependency ranges

**Evidence.** Runtime code imports `_cache_manager` from a private Hugging Face module, although its types are publicly exported in the installed version ([model imports](src/mlx_tui/models.py#L7-L10)). Cancellation reaches into `network_stream._sock` ([abort workaround](src/mlx_tui/chat_pane/turn.py#L172-L177)). Runtime dependencies have lower bounds only, and `rich` is imported directly but declared only transitively through Textual ([project metadata](pyproject.toml#L9-L15)).

**Impact.** The lockfile protects this checkout, but a fresh package installation may resolve versions where these internals moved. Import failure would prevent startup; cancellation failure would be subtler.

**Improve.** Use public Hugging Face exports, declare direct dependencies, isolate the socket workaround behind a version-checked adapter, and test minimum plus current dependency sets. Upper bounds are useful only where incompatibility is known; automated compatibility is preferable to speculative pinning.

**Confidence:** high.

### F13 — The package split obscures ownership and suppresses type safety

**Evidence.** Pane classes contain forwarding methods into free-function modules, with those functions reaching back into private pane/app state. Runtime local imports are repeatedly used to break cycles ([chat facade](src/mlx_tui/chat_pane/__init__.py#L111-L190), [search facade](src/mlx_tui/search_screen/__init__.py#L88-L151), [models facade](src/mlx_tui/models_pane/__init__.py#L39-L88)). The source contains 59 `type: ignore` or `pyrefly: ignore` directives. Pyrefly reports zero errors but notes 84 suppressions and 10 hidden warnings.

**Impact.** “Strict” typing does not cover important facade boundaries, lifecycle invariants span several modules, and navigation is harder than the small codebase warrants.

**Improve.** Choose one of two coherent shapes: keep cohesive controller behavior on its owning class, or extract explicit typed services/protocols that do not know widget-private fields. Remove legacy forwarding surfaces incrementally and make warnings visible in CI.

**Confidence:** high. This is a maintainability issue, not a claim that the current split is functionally broken.

### F14 — CI misses target-platform and destructive-flow risk

**Evidence.** The meaningful target is macOS Apple Silicon ([requirements](README.md#L19-L24)), and chat cancellation contains a macOS-specific private-socket workaround, but CI runs only Ubuntu ([workflow](.github/workflows/ci.yml#L11-L24)). No focused test references the delete confirmation/orchestration, config editor flow, or CLI `main`; low-level cache deletion is tested but the guard/race/error behavior is not. CI also does not build and install the wheel.

**Impact.** Platform-specific process/cancellation behavior and destructive cache actions can regress while the 247-test Linux suite remains green. Packaging errors may appear only at release time.

**Improve.** Add a macOS job for process discovery, cancellation, and Textual smoke coverage. Add focused delete/config/CLI tests. Build wheel and sdist, install them into an isolated environment, and run `mlx-tui --help` in CI.

**Confidence:** high.

### F15 — Broad exception swallowing hides defects as ordinary state

**Evidence.** There are 14 `except Exception` sites in `src`. Examples turn every polling exception into “red” ([polling](src/mlx_tui/app/polling.py#L22-L31)), silently discard context UI errors ([chat pane](src/mlx_tui/chat_pane/__init__.py#L94-L105)), and suppress config-state sync failures with the redundant `except (AttributeError, Exception)` ([turn](src/mlx_tui/chat_pane/turn.py#L36-L60)).

**Impact.** Programmer errors can masquerade as server downtime, missing size data, or absent UI updates, making diagnosis much harder.

**Improve.** Catch expected transport, parsing, lifecycle, and `NoMatches` exceptions narrowly. Use one top-level defensive boundary per worker, with rate-limited diagnostics in the shared log.

**Confidence:** high.

### F16 — Documentation and release metadata have drifted

**Evidence.** `ARCHITECTURE.md` describes a single formatted status line, fixed 8000-token context, no prefill column, and 231 tests, while the current code has composed status widgets, configurable 8192 default context, prefill/decode, and 247 tests ([architecture claims](ARCHITECTURE.md#L16-L19), [context claim](ARCHITECTURE.md#L40-L43), [test claim](ARCHITECTURE.md#L134-L141)). `docs/part2.md` says Part 2 is not started despite completed Phase 4 code ([status](docs/part2.md#L108-L110)). README documents restart-first swap behavior that the code does not follow and omits `max_ctx`. It declares MIT but the repository and existing wheel contain no license file; the README points to the wrong metadata line ([README license](README.md#L131-L133), [project metadata](pyproject.toml#L1-L7)).

**Impact.** The next maintainer may “fix” code toward a stale contract or trust an already-resolved plan. A published wheel would have incomplete licensing evidence.

**Improve.** Rewrite `ARCHITECTURE.md` from current behavior and avoid volatile exact test counts/line references. Mark completed planning status, document the chosen swap/context contracts, add an MIT `LICENSE`, and verify its inclusion in artifacts.

**Confidence:** high.

## Recommended repair sequence

1. **Restore truthful server identity.** Parse and retain `/v1/models`; bind optional PID telemetry to the endpoint; clear tracked state on endpoint/PID generation changes. Add two-server and external-restart tests.
2. **Create one lifecycle coordinator.** Serialize chat, load/restart, and delete; make cleanup unconditional; add stop/start deadlines and acknowledged cancellation.
3. **Make swap results evidence-based.** Expose explicit warm/restart/auto policy and verify the target model before changing UI state.
4. **Repair context and metrics.** Budget the full request plus output reserve; use the complete request/response for estimates; carry typed numeric measurements and provenance flags.
5. **Make config updates total.** Use `dataclasses.replace`, clear removed values, and preserve unrelated fields through presets.
6. **Align downloads with supported MLX versions.** Match upstream load patterns/revisions and be explicit about trusted custom code.
7. **Reduce structural debt.** Remove the fake state-machine ceremony, forwarding/circular imports, broad catches, and unnecessary type suppressions.
8. **Close the delivery loop.** Add macOS and artifact smoke jobs, destructive-flow tests, current docs, and a real license file.

## Strengths worth preserving

- The product thesis is unusually clear and resists unnecessary backend abstraction.
- Pure functions for liveness, token-window selection, row mapping, and sparklines are easy to test.
- Poll overlap is guarded; blocking UI work is generally moved to workers; widget mutation usually returns to the UI thread.
- The project has a lockfile and a broad, fast test suite with realistic HTTP stream modes.
- Errors are usually surfaced in user language rather than raw tracebacks.
- The existing “warn, do not hard-block” approach to approximate memory/disk fit is appropriate.

## Verification and limitations

Commands run against the current working tree:

```text
uv run ruff check .                         passed
uv run ruff format --check src tests        passed (54 files formatted)
uv run pyrefly check                        passed; 0 errors, 84 suppressed, 10 warnings hidden
uv run pytest -q                            passed; 247 tests in 38.60s
```

Additional read-only checks reproduced the existing-model command behavior, `--model=` detection gap, over-budget payload construction, multi-line SSE parsing gap, and chunk-based fallback accounting. The existing wheel was inspected and contains all package subpackages and the console entry point, but no license file; it predates current working-tree changes.

No live model load, real multi-server process test, network-stall cancellation test, or macOS UI render was performed. Findings that depend on those conditions are marked accordingly. No visual artifact review was needed for this Markdown deliverable; structural links and headings were checked instead.

## Claim-to-source ledger

| Claim family | Primary source | Access notes |
|---|---|---|
| Product purpose/scope | `docs/idea.md`, repository author, current working tree | local file |
| User-facing behavior | `README.md`, repository author, current working tree | local file |
| Runtime architecture | `src/mlx_tui/**`, current working tree | local files with line-linked evidence above |
| Tests/CI | `tests/**`, `.github/workflows/ci.yml`, current working tree | local files and executed commands |
| MLX model request/server contract | “HTTP Model Server,” MLX-LM maintainers, current `main` | https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md |
| MLX download patterns | `mlx_lm/utils.py`, MLX-LM maintainers, current `main` | https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/utils.py |
| Hugging Face snapshot filtering | `_snapshot_download.py`, Hugging Face, current `main` | https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/_snapshot_download.py |
| SSE grammar/interpretation | “Server-sent events,” WHATWG HTML Living Standard | https://html.spec.whatwg.org/dev/server-sent-events.html |

## Research log and stop reason

Discovery covered all source modules, the complete test inventory, CI/package metadata, current README/architecture/idea/Part 2 documents, recent internal review history, and current first-party MLX-LM/Hugging Face contracts. Follow-up targeted conflicting swap semantics, MLX download patterns, SSE parsing rules, cancellation, config preservation, type suppressions, packaging, and platform coverage.

Research stopped because every high-impact finding has direct current-code evidence, external contract claims have first-party support, the full quality gate was executed, contradictions were either resolved or bounded, and additional broad searching was unlikely to change priorities. The main remaining evidence gaps require live MLX/macOS scenarios rather than more document retrieval.
