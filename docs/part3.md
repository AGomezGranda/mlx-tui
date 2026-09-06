# MLX TUI — Part 3: The best way to run local models on a Mac

> Product direction for the next phase, following [idea.md](idea.md) and
> [part2.md](part2.md). Written 2026-09-06 against the current working tree.
> This is a vision and prioritization document, not an approved implementation
> plan. Proposed behavior and acceptance targets below are not shipped features
> or measured results.

## Goal

**Make MLX TUI the local inference tool that Mac users trust to choose a model,
run it well, and understand what is happening.**

The foundation answered: “Is my server up, and can I swap the model?”
The next phase should answer: “What should I run on this Mac, with these settings,
for this task — and can I rely on it every day?”

Our promise should be concrete:

- Get from a fresh installation to a useful response without assembling a server command.
- Choose a model and context budget with a credible explanation of the tradeoffs.
- Benefit from MLX optimizations without learning every runtime flag.
- Resume real work, including files and long conversations.
- Point another local application at the same dependable endpoint.
- Diagnose a slow response or failed load without guessing.

“Best” cannot mean fastest on every model, largest feature list, and easiest
interface for every person simultaneously. Start with **developers and technically
comfortable Mac users who want local models for daily work**. A terminal interface
is an advantage for that audience. Winning the broader Mac audience may eventually
need another interface; it does not require one in this phase.

## What we have, and what changes

The working tree is ahead of parts of the prose documentation and contains ongoing
uncommitted work. This inventory describes code inspected, not release status.

| Foundation | Current implementation | Next responsibility |
|---|---|---|
| Status and memory | Polling, endpoint-associated process identity, RSS/available memory and memory bar | Distinguish reachable, ready, selected and actually observed model state |
| Model lifecycle | HF cache table, search/download, warm/restart policies, serialized operations | A supported managed runtime and recovery that needs no shell editing |
| Chat | Async SSE, cancellation, rendered transcript, basic parameters and presets | Complete response handling, durable conversations and file context |
| Context | Configurable `max_ctx`, estimated framing, output reservation and bounded message trimming | Model-aware limits, tokenizer-backed counts where supported, visible trimming |
| Metrics | Per-model rings, context/memory displays and estimated-token labels | Correct metric definitions and reproducible comparisons |

Relevant implementation: [app](../src/mlx_tui/app/__init__.py),
[process identity](../src/mlx_tui/process.py),
[model operations](../src/mlx_tui/models_pane/swap_ops.py),
[chat transport](../src/mlx_tui/chat.py),
[chat pane](../src/mlx_tui/chat_pane/__init__.py),
[context preparation](../src/mlx_tui/history/tokens.py), and
[history records](../src/mlx_tui/history/store.py).

Three corrections to carry forward:

1. **Prefill speed:** the chat pane currently labels `prompt_tokens / TTFT` as
   prefill tok/s. That interval can include queueing, loading, cache lookup,
   reasoning and transport. Rename or remove the proxy before using it to tune
   anything. True prefill requires an engine measurement.
2. **Sessions:** persisting `TurnRecord` would save metrics, not conversations.
   It contains no messages. Resumable chat needs its own message records,
   including system prompt, model identity and effective settings.
3. **Context safety:** character estimates plus fixed overhead are useful hints,
   not guaranteed tokenizer bounds. A configured 8k budget also does not prove
   a model's architectural limit or useful long-context quality.

### Changes to the earlier thesis

| Earlier constraint | Decision for this phase |
|---|---|
| Control plane around a separately configured server | Keep attach mode; make managed setup the recommended new-user path |
| Chat only as a load generator | Make it good enough for daily work; retain instrumentation |
| Config is always an editor problem | Keep TOML and the editor; provide a short setup flow and selectable profiles |
| Optimizations parked until a user asks | Actively evaluate them; enable only demonstrated improvements |
| In-memory metrics only | Persist conversations and comparison results for separate purposes |
| No A/B comparison | Compare sequential runs in Metrics; avoid two resident models by default |
| No public distribution until a personal usage gate passes | Add external onboarding and reliability gates, then publish |
| Vision excluded | Later, explicit milestone; text reliability comes first |

Keep MLX-only, local operation, keyboard-first interaction and reuse of upstream
inference code. Drop arbitrary line-count budgets and “used by one person for a
week” as the only evidence that a feature matters.

## The competitive reality

MLX alone is not a differentiator. LM Studio already supports it, Ollama has
announced an MLX preview, and projects such as oMLX and vllm-mlx explicitly pursue
Mac inference, caching and serving. Their existence invalidates the earlier
assumption that nobody addresses this territory. These are product descriptions,
not independently verified performance comparisons.
[LM Studio](https://lmstudio.ai/docs/app),
[Ollama](https://ollama.com/blog),
[oMLX](https://github.com/jundot/omlx),
[vllm-mlx](https://github.com/waybarrios/vllm-mlx).

Our proposed distinction is **an excellent terminal workflow with recommendations
backed by measurements on your own Mac**. The combination matters: a profile you
can understand, a runtime you can recover, and evidence explaining why one model
or configuration is better for your workload.

If users still choose another product after trying that workflow, investigate
why. Do not interpret a longer feature checklist as progress toward winning.

## What upstream gives us

Research snapshot: MLX-LM commit
[`6d21ce4`](https://github.com/ml-explore/mlx-lm/tree/6d21ce4b065a2e163fa6de76a9936c61aeb5784a),
committed 2026-09-05. Source inspection establishes availability in that revision,
not behavior in the user's installed version. No live inference benchmark or
compatibility test was performed for this document.

The inspected server implements on-demand loading, sampler controls, draft-model
and adapter selection, reasoning/tool responses, cached-token usage, prompt-cache
limits, prefill chunking and batching. Draft models and seeded requests restrict
batch eligibility. `/v1/models` enumerates available models; `/health` is separate.
No `response_format` handler or KV-quantization flag was found in this server.
These findings overturn several “probably CLI-only” assumptions in Part 2.
[Server source](https://github.com/ml-explore/mlx-lm/blob/6d21ce4b065a2e163fa6de76a9936c61aeb5784a/mlx_lm/server.py).

Generation APIs expose quantized KV caches, rotating caches, speculative generation
and engine timing/memory results. Their presence does **not** establish HTTP server
parity. Cache conversion also depends on the cache implementation.
[Generation source](https://github.com/ml-explore/mlx-lm/blob/6d21ce4b065a2e163fa6de76a9936c61aeb5784a/mlx_lm/generate.py).

Upstream already provides a benchmark command with prompt/generation lengths,
batch size, warmup, repeated trials and timing/memory output. Reuse it for engine
baselines; it does not replace testing the complete HTTP experience.
[Benchmark source](https://github.com/ml-explore/mlx-lm/blob/6d21ce4b065a2e163fa6de76a9936c61aeb5784a/mlx_lm/benchmark.py).

MLX exposes active, peak and cached allocation measurements and memory controls.
Those measurements belong to the allocating process. Importing MLX into the TUI
cannot reveal another process's allocator state.
[MLX memory API](https://ml-explore.github.io/mlx/build/html/python/metal.html).

**Rule:** distinguish library support, server exposure, model compatibility and
verified local behavior. An accepted request field is not proof that a feature
was applied. Present capabilities as verified, experimental, unavailable or
unknown; never silently present an unsupported optimization as enabled.

## Product shape

Keep Models, Chat and Metrics, the always-visible status area, and the shared
log pane. Add depth to those surfaces before adding more tabs.

Illustrative layout; values and labels are proposed, not measurements:

```text
● Ready · model-name · Balanced · Local :8080
Server RSS 7.2 GiB · available ~9.1 GiB · context ~6k + 2k reserved / 16k
[ Models ] [ Chat ] [ Metrics ]

Chat: Project notes                              Session saved locally
...conversation...
[file: notes.md · included]           [2 earlier turns excluded from request]
> multiline prompt

TTFT 0.8s · answer started 2.1s · 32 tok/s observed · cache reuse 84%
log: profile applied; server ready
```

Status always has a word as well as a color. Long model names, narrow terminals,
focus visibility, multiline paste and cancellation are part of quality, not final
polish. Advanced settings remain collapsed. A small action picker makes commands
discoverable without requiring users to memorize another key for every feature.

## Pillar 1 — A runtime users can trust

**Question:** can someone use the app without becoming their own server operator?

Offer two clear modes:

- **Managed:** the app launches a tested MLX-LM installation, owns the process,
  records its version and effective launch settings, and handles start, stop,
  reload and failure recovery.
- **Attach:** connect to an existing local server with honest limits on control
  and observability. Do not claim ownership of an arbitrary discovered process.

The initial managed implementation should launch upstream `mlx_lm.server` as a
subprocess. Keep inference out of Textual's process. Start with one resident target
model and one endpoint; a speculative draft is an explicitly budgeted companion.
A model switch should explain any loss of warm cache and drain or cancel known
work before changing the runtime.

Record process identity and lifecycle explicitly. Show port conflicts, missing
runtime, incompatible model, download failure and memory failure with a useful
next action. A failed switch must leave an accurate state and a way to reload the
previous model, not a stale success marker. Define whether quitting stops the
managed server; initially tie its lifetime to the TUI and make that visible.

Separate endpoint responsiveness, generation readiness and last observed model.
A catalogue entry or launch argument cannot establish current residency after
another client changes the model. Without runtime telemetry, label state as last
observed or unknown. Also distinguish “client request cancelled” from confirmed
cessation of engine work.

### Where HTTP stops being enough

Exact allocator memory, request phases and reliable residency may justify a small
runtime integration. First request or contribute structured upstream telemetry.
If a priority workflow still needs it, prototype a version-pinned integration in
the server process with a narrow status surface. Avoid parsing human logs into a
second, unofficial metrics API.

Adopt that integration only after proving it preserves upstream serving behavior
and remains cheap to maintain. Do not create a new scheduler or generation loop
just to expose a counter. Features that need missing instrumentation remain
explicitly unavailable until that integration is justified.

**Done when:** a new user starts, switches, cancels and recovers without editing
commands, and no failure produces a false ready/loaded indicator.

## Pillar 2 — Choose a useful model for this Mac

**Question:** can we replace trial-and-error downloads with a defensible choice?

The Models tab should help users answer “which one?” as well as “what is cached?”
Start with a small maintained set of recommendations for general chat, coding and
long-document work. Include a direct repository/path escape hatch. Recommend
specific revisions and quantizations, with the source and date of the advice.
Do not infer compatibility or quality from a repository name alone.

For each candidate, show download size, required runtime support, supported task,
context budget and memory-fit confidence. Explain a recommendation: “smaller
weights leave more room for your requested context.” Do not label a model the
best coder without quality evidence.

Move from a weights-only check toward a workload estimate:

```text
expected runtime footprint ≈ weights + KV/state + working buffers + optional draft
required headroom also includes space for macOS and the user's other applications
```

This is a planning model, not an exact additive accounting of macOS memory. RSS,
MLX allocations and system memory overlap and must not be summed as independent
usage. Keep disk size distinct from resident memory, and use consistent GiB/GB
units. Recognize uncertainty in compressed memory and available-memory estimates.

Use architecture-specific KV estimates only where validated. Dense attention,
sliding windows, hybrid recurrent state and MoE models do not justify one universal
bytes-per-token constant. Prefer measurements from matching model/profile runs;
otherwise show an estimate or unknown. Never describe a model as guaranteed to fit.

First-run flow: detect supported Apple Silicon/macOS/runtime conditions, select a
task and memory/context preference, show a small shortlist, download, load, send a
useful prompt. Reuse the HF cache and downloader. Make partial downloads, required
tokenizer files, gated repositories and disk exhaustion understandable. Loading a
fully cached model should work offline.

**Done when:** users can explain their choice and complete the flow without
external setup instructions. Recommendations cover tested memory tiers rather
than advertising support based only on our development machine.

## Pillar 3 — Make MLX optimizations useful and measurable

**Question:** can users obtain a better speed/memory/quality tradeoff without
becoming inference specialists?

A profile should collect a model revision, context budget, sampling/template
settings and runtime settings into a named, reproducible choice. Extend the
existing TOML/preset approach. Separate per-request changes from changes that
require reload. Show the effective settings and an easy return to the baseline.

Start with a baseline profile. Add “Responsive” or “Long context” only after tests
justify those names for a particular model and machine class. A universal “Fast”
switch is a promise we cannot defend.

| Opportunity | Proposed user benefit | Gate before recommending |
|---|---|---|
| Prefix reuse | Follow-up questions avoid unnecessary repeated work | Compare identical and changed prefixes; report observed reuse or unknown |
| Prefill chunk size | Process long prompts with a suitable peak-memory tradeoff | Measure latency and peak memory on the same workload |
| Quantized KV | More usable context within a memory budget | Verify serving exposure, cache architecture and long-context quality |
| Speculative decoding | Faster completion on suitable target/draft pairs | Include draft memory/load cost, compatibility and measured speedup |
| Batching/concurrency | Serve another local client without poor interactive latency | Measure per-request latency and aggregate throughput together |
| Sampler/template controls | Appropriate behavior for a particular model and task | Verify effective parameters and preserve model-specific defaults |

Prioritize prefix reuse and prefill behavior, then evaluate KV quantization and
speculation. Keep this evaluation separate from a commitment to ship every knob.
Interactions matter: a configuration good for one interactive request may be bad
for concurrency. A fixed seed is useful experimental metadata, not a guarantee of
identical output across runtimes or execution paths.

Track model load, first generation after load, and prompt-cache reuse separately.
“Warm” does not necessarily mean “cache hit.” Changes to the model, adapter,
tokenizer/template or cache format must not reuse incompatible state. Let upstream
own cache matching and eviction wherever possible.

Bound caches explicitly and explain their cost. Do not treat rotating context as
lossless memory savings, change global wired-memory settings automatically, or
promise that aggressive caching always improves responsiveness.

**Done when:** at least one optional profile has a repeatable benefit on a named
workload without an unacceptable quality or memory regression. Publish the cases
where the baseline wins too.

## Pillar 4 — Chat that supports real work

**Question:** does the user come back to continue a task, rather than to test a model?

Persist conversations locally with model revision, system prompt, effective
profile, messages, attachments and completion/cancellation state. Use a simple
versioned file format initially; handle interrupted writes and provide clear/delete
and temporary-session controls. Saving messages does not save a warm KV cache.

Provide multiline editing, reliable paste, copy response/code, retry and a simple
session picker. Preserve the user's prompt after failure. Keep model-emitted
reasoning separate from the answer and show that generation is progressing before
answer text appears. Preserve tool-call data correctly when supported; displaying
or serving a tool call does not require the TUI to execute tools.

File context should show exactly what was included, its size/token estimate and
any truncation before sending. Start with selected text/code files; snapshot the
included content so resuming does not silently substitute a changed file. Do not
index a user's filesystem as a side effect of attachment support.

Make context handling explicit: input, reserved output, configured limit and any
removed turns. Preserve the complete transcript even when the next request uses
only a window. Use the same tokenizer and chat template as the runtime where
available; otherwise label estimates and handle context rejection gracefully.
Summarizing old context is a later, explicit action because it changes the evidence
available to the model.

**Done when:** someone resumes yesterday's file-based task after a restart, knows
what context the model received, and recovers from cancellation without lost work.

## Pillar 5 — A local endpoint worth building on

**Question:** can another application rely on this runtime during everyday work?

Keep the serving API upstream and expose a copyable local base URL, model identifier
and tested request example. Verify streaming, cancellation, errors and tool-call
round trips with a real external client. State exactly which API routes and
features were tested; “OpenAI-compatible” is not universal client compatibility.

Start with one resident model. External requests for a different model need an
explicit policy so background work cannot silently invalidate the TUI's selection.
If the stock server cannot enforce that policy, document the limitation and gate
shared serving rather than pretending the TUI's operation lock covers other clients.

Later add deliberate background operation if users need the endpoint after closing
the TUI. That milestone needs ownership, reconnect, update and shutdown semantics.
It does not justify a daemon in the first managed-runtime release.

Remain loopback-first. Local inference should require no cloud account or analytics.
Separate model downloads from offline inference, keep prompt content out of default
diagnostic exports, and require explicit choice before enabling remote model code.
LAN exposure and multi-user serving require their own design.

**Done when:** an external client and the TUI coexist without unexplained switches,
lost requests or misleading status.

## Measurement is part of the product

Metrics should help users choose a model or setting. Build a small “Compare” action
inside the existing Metrics tab: pick two configurations, run sequentially, inspect
latency/memory and answer quality, then keep one. Sequential comparison avoids
requiring enough memory for both models at once.

Use a modest workload pack: short chat, a long prompt, repeated-prefix follow-ups,
a coding task with a checkable result, and a two-client contention case. Add a
long-context retrieval task when evaluating cache changes. Synthetic token tests
measure engine behavior; realistic prompts measure usefulness.

Proposed protocol:

- Record chip, RAM, macOS, MLX/MLX-LM versions, model revision, quantization,
  profile, prompt/template identity and actual token counts.
- Record power mode/source and relevant concurrent load where available; compare
  sustained runs on laptops as well as brief runs on a desktop.
- Report cold runs separately. Run at least five warm trials and show median plus
  range; collect a larger sample before claiming tail-latency improvements.
- Distinguish first generated output, first answer text, total completion time,
  engine prefill/decode and client-observed throughput. Missing values stay missing.
- Record cache state/reuse, process RSS, engine allocation peak where exposed,
  failures, cancellation and quality checks. Do not drop failed configurations
  from the result table.
- Store results locally with a schema version. Export enough metadata to reproduce
  a result; include prompt content only when the user chooses to share it.

For throughput, document numerator and interval, especially when usage includes
reasoning tokens that arrived before answer text. Do not divide all output tokens
by an answer-only interval. Allocator peaks must also identify their scope; a
process-wide peak under concurrency is not per-request memory.

Use upstream benchmarking for engine baselines and a small HTTP workload runner
for app behavior. Do not build a benchmarking platform or public leaderboard.
The first comparison only needs to answer one real model/profile decision.

## Build order

These milestones supersede unimplemented Part 2 priorities. Each should receive a
separate implementation plan after its scope is chosen; there are no calendar
estimates before runtime compatibility and maintenance costs are established.

| Milestone | Deliverable | Acceptance evidence | Defer if |
|---|---|---|---|
| **A — Trust the facts** | Capability snapshot, honest state labels, corrected metrics, richer stream handling | Real-server checks for catalogue vs residency, reasoning, cache usage, errors and cancellation | Never defer correctness; hide measurements we cannot substantiate |
| **B — Own the first run** | Managed upstream process, setup flow, small model shortlist and recovery | Five fresh-install testers across at least two memory tiers; four reach a response without developer intervention | An integration requires an inference-engine rewrite; keep the supported subprocess path |
| **C — Make it daily** | Durable sessions, multiline/file workflow and visible context management | Testers resume real work after restart and recover from interrupted turns | Extra session features do not improve that workflow |
| **D — Prove optimization** | Baseline comparison plus first validated performance profile | Reproducible speed/memory results and quality checks; easy rollback | Improvements disappear in realistic workloads |
| **E — Serve real clients** | Verified client setup, contention and model-switch policy | TUI plus one external client pass a repeated mixed-workload run | Required lifecycle guarantees cannot be enforced yet |
| **F — Ship a supported release** | Packaging, tested version matrix, diagnostics and upgrade recovery | Clean install/upgrade/offline-start checks, no orphaned managed process after shutdown, two-week external use | Reliability or onboarding gates remain open |

Suggested release targets, to validate rather than advertise now: warm interactive
latency within 5% of a matching direct upstream baseline over repeated batches;
no unhandled TUI crash or conversation loss in the release workload suite; every
failed load ends with an accurate state and a next action. Download duration is
reported separately from setup friction. Hardware-dependent latency is not a
single universal number.

Keep the existing automated checks for implementation work. Add focused real-MLX
contract tests on Apple Silicon for the tested runtime versions; HTTP stubs alone
cannot validate model loading, cache behavior, memory or server-side cancellation.
Run broader hardware trials for releases and optimization claims, not every small
UI change. No runtime code was changed or inference tests run for this document.

## Later, with evidence

| Direction | Why it could matter | Entry condition |
|---|---|---|
| Vision through MLX-VLM | Screenshots and document images are real Mac workflows | Text milestones hold; separately verify model loading, content/SSE formats, processor assets, memory and client compatibility |
| LoRA profile selection | Useful for users with task-specific adapters | Named users have adapters to use; loading and cache invalidation are tested |
| Constrained structured output | Dependable JSON for applications | Verified server-side enforcement; prompting for JSON alone does not qualify |
| Conversion/quantization workflow | A wanted model lacks a usable local artifact | Repeated demand; wrap upstream conversion with disk/memory checks and evaluation |
| Persistent prompt cache | Faster return to large repeated contexts | Measured reload cost justifies storage, invalidation and local-data handling |
| Multiple resident models | Avoid switching delays for recurring workloads | Clear demand and a measured memory/scheduling budget |
| Embeddings, retrieval or an agent harness | Broader application workflows | External clients cannot satisfy the demonstrated need with the local endpoint |

Custom Metal kernels, a general backend abstraction, distributed inference,
training infrastructure, automatic routing and a native GUI are outside this
phase. Contribute performance fixes upstream where appropriate. Taking full
advantage of MLX means delivering its useful capabilities reliably, not owning
all the layers beneath the UI.

## First action

Start with Milestone A: pin one supported MLX-LM version and write a short
compatibility report from real requests on a named Mac. Verify health/model-state
semantics, usage and reasoning/tool streams, cache reuse, sampler behavior,
concurrent requests and cancellation. Record unsupported surfaces explicitly.

Then plan the managed first-run experience around those findings. The next
release should make one complete promise believable: **choose a suitable model,
get useful work done locally, and know that the app is telling the truth.**
