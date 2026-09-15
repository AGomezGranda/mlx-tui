# MLX TUI — Part 3: An evidence-first MLX workflow for Apple Silicon

> Product direction for the next phase, following [idea.md](idea.md) and
> [part2.md](part2.md). Written 2026-09-06 against the current working tree.
> This is a vision and prioritization document, not an approved implementation
> plan. Proposed behavior and acceptance targets below are not shipped features
> or measured results.

## Goal

**Help existing MLX operators on Apple Silicon choose between two supported
coding configurations on their own Mac, understand the tradeoff, and run the
choice through a local endpoint whose status they can trust.**

The first audience is developers already running MLX-LM who are comfortable with
a terminal and want evidence for a model/profile decision without abandoning
their setup. Their primary job is: **choose and operate the right local model on
this Mac for a small coding workload with a checkable result**.

The first validation cycle uses attach mode: one task, two pinned configurations,
sequential runs, an understandable result, and a “keep this profile” action.
Success means the evidence informs a choice and the user returns to use it.
A faster configuration need not win if its answer fails the task or its memory
cost is unacceptable.

Managed installation is an acquisition expansion after this workflow proves
valuable. Durable chat, files and broader workloads follow observed needs.
First-time local-model users and the broader Mac audience are not the initial
beachhead; their onboarding and support needs require separate validation.

## What we have, and what changes

The working tree is ahead of parts of the prose documentation and contains ongoing
uncommitted work. This inventory describes code inspected, not release status.

| Foundation | Current implementation | Next responsibility |
|---|---|---|
| Status and memory | Polling, endpoint-associated process identity, RSS/available memory and memory bar | Distinguish reachable, ready, selected and actually observed model state |
| Model lifecycle | HF cache table, search/download, warm/restart policies, serialized operations | Truthful attach-mode control and recovery; managed setup after validation |
| Chat | Async SSE, cancellation, rendered transcript, basic parameters and presets | Complete response handling for comparison; persistence and files when needed |
| Context | Configurable `max_ctx`, estimated framing, output reservation and bounded message trimming | Model-aware limits, tokenizer-backed counts where supported, visible trimming |
| Metrics | Per-model rings, context/memory displays and estimated-token labels | Keep ordinary history visible; comparison setup and decisions live in Compare |

Relevant implementation: [app](../src/mlx_tui/app/__init__.py),
[process identity](../src/mlx_tui/process.py),
[model operations](../src/mlx_tui/models_pane.py),
[chat transport](../src/mlx_tui/chat.py),
[chat pane](../src/mlx_tui/chat_pane.py),
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
| Control plane around a separately configured server | Validate in attach mode first; add managed setup after the comparison gate |
| Chat only as a load generator | Support the coding comparison first; earn investment in daily-work features |
| Config is always an editor problem | Keep TOML and the editor; provide a short setup flow and selectable profiles |
| Optimizations parked until a user asks | Evaluate changes needed for the first decision; expand only with evidence |
| In-memory metrics only | Persist comparison results and chosen profiles first; conversations later |
| No dedicated comparison tab | Compare sequential runs in Compare; avoid two resident models by default |
| No public distribution until a personal usage gate passes | Add external onboarding and reliability gates, then publish |
| Vision excluded | Later, explicit milestone; text reliability comes first |

Keep MLX-only, local operation, keyboard-first interaction and reuse of upstream
inference code. MLX-only is an internal scope constraint: it should enable more
accurate Apple-Silicon guidance and inspectable runtime behavior. Users must value
those outcomes; the engine name is not itself a reason to choose the app.
Drop arbitrary line-count budgets. Retain the personal one-week real-use gate,
and add external activation, decision-value and return-use evidence.

## The competitive reality

Snapshot checked 2026-09-06. MLX support and terminal access are already available
elsewhere. LM Studio documents MLX, model downloads, document chat, local APIs,
headless operation and CLI control. [LM Studio documentation](https://lmstudio.ai/docs/app).
Ollama has published further MLX releases and improvements since its March preview;
calling it only an announcement understates the alternative. [Ollama blog](https://ollama.com/blog).
oMLX documents model lifecycle and persistent caching, while vllm-mlx documents
Apple-Silicon serving, batching and developer integrations.
[oMLX](https://github.com/jundot/omlx),
[vllm-mlx](https://github.com/waybarrios/vllm-mlx).
These are documented capabilities, not independent performance or usability tests.

Compare alternatives on the same job: **choose and operate the right local model
on this Mac for this coding workload**. The proposed advantage is an integrated
choice backed by local evidence. Every advantage below remains a hypothesis.

| Dimension | Alternative to compare against | MLX TUI hypothesis and evidence needed | Behavior that would falsify the advantage |
|---|---|---|---|
| Time to first useful response | Existing MLX-LM setup; LM Studio downloads/chat; Ollama model workflows | An operator reaches a useful comparison quickly; observe elapsed time and interventions on the same task, separating downloads | Users finish sooner in their existing workflow and abandon comparison as overhead |
| Explanation of fit | Users' existing model advice and settings in their preferred app | Local quality, latency and memory evidence makes the choice understandable; ask users to explain the tradeoff | Users ignore the evidence or cannot explain their choice |
| Observability and truthfulness | Direct MLX-LM output; oMLX and vllm-mlx serving information | Verified/estimated/unknown labels reduce mistaken conclusions; observe diagnosis of identical failures | Users still misread readiness or metrics, or diagnose more reliably elsewhere |
| Reproducibility | Existing scripts, saved settings and CLI workflows | A saved comparison/profile makes rerunning the decision easier; observe a later rerun with matching metadata | Users must reconstruct settings manually or prefer their existing scripts |
| Recovery | Current server restart workflow and competing lifecycle controls | Accurate failure state and next actions shorten recovery; observe failed load and cancellation cases | Users return to another tool to recover or distrust the displayed state |
| Terminal workflow | Direct MLX-LM commands, LM Studio CLI, Ollama and serving-project workflows | Compare, inspect and keep a profile in one keyboard flow reduces effort; observe task completion and return use | Users keep their CLI or GUI because this flow adds steps without value |

Record why participants stay with or return to an alternative, including when it
already solves the job. Feature count and unsupported claims about competitors'
missing capabilities are not evidence of an advantage.

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

Keep Models, Compare and Chat, plus the Metrics history tab, the always-visible
status area, and the shared log pane. The Compare tab owns two configurations,
readiness, results and “keep this profile”; Metrics remains history-only. The broader chat layout below is a later
possibility, conditional on daily-use evidence; it is not Milestone B scope.

Illustrative layout; values and labels are proposed, not measurements:

```text
● Ready · model-name · Balanced · Local :8080
Server RSS 7.2 GiB · available ~9.1 GiB · context ~6k + 2k reserved / 16k
[ Models ] [ Compare ] [ Chat ] [ Metrics ]

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

**Question:** can an existing operator trust what the app says about their runtime?

Use attach mode for the first validation cycle. Managed mode follows only after
Milestone B proves decision value and return use. The ownership boundary is:

- **Attach:** connect to an existing local server with honest limits on control
  and observability. Do not claim ownership of an arbitrary discovered process.
- **Managed:** the app launches a tested MLX-LM installation, owns the process,
  records its version and effective launch settings, and handles start, stop,
  reload and failure recovery.

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

**Done when:** an attached operator can distinguish reachable, ready and last
observed state, including after failure or cancellation. The later managed gate
adds startup, switching and recovery without command editing.

## Pillar 2 — Choose a useful model for this Mac

**Question:** can we replace trial-and-error downloads with a defensible choice?

The Models tab should help users answer “which one?” as well as “what is cached?”
Start with one job: small coding tasks with a deterministic check. Bound the
initial catalogue to two or three pinned model revisions, one tested MLX-LM
runtime and two named memory tiers. Milestone A selects the exact revisions and
tiers from available test hardware; untested combinations remain unknown.
Include a direct repository/path escape hatch without implying a recommendation.
Do not infer compatibility or quality from a repository name alone.

The project maintainer owns recommendation freshness and must name an owner for
each entry before publication. Each entry records model revision, quantization,
runtime/template/profile identity, tested Mac and memory tier, task/check version,
results, test date and expiry date. Test it with the same coding comparison on
each claimed tier, including load failures and memory observations. Publish both
quality failures and cases where the baseline wins.

Initially expire advice after 30 days, or invalidate it immediately when a pinned
dependency or profile changes or a reproducible incompatibility is reported.
Withdraw invalid advice from the recommended shortlist; retain dated results for
inspection and label the combination unknown pending revalidation. Revalidation
reruns the same checks on every claimed tier and renews the date only after review.
If maintenance capacity is insufficient, shrink the catalogue rather than extend
untested claims. This is ongoing product work, not a one-time UI feature.

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

The first attach flow checks the runtime, offers two supported coding profiles,
runs the comparison and saves a choice. If launch settings require a restart,
show the operator the required change and verify it before continuing; never
silently take ownership of the attached server.

The later managed first-run flow detects supported Apple Silicon/macOS/runtime
conditions, asks for a memory/context preference, shows the coding shortlist,
downloads and loads models, then reaches the same comparison. Reuse the HF cache and downloader. Make partial downloads, required
tokenizer files, gated repositories and disk exhaustion understandable. Loading a
fully cached model should work offline.

**Done when:** target users complete the comparison, explain an evidence-backed
choice and later reuse it, meeting Milestone B's gates. Every active recommendation
has an owner and current evidence for its claimed memory tiers.

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

These are later evaluation options, not prerequisites for the first comparison.
Start with two baseline coding configurations. Investigate prefix reuse or prefill
behavior only when it addresses an observed tradeoff, then consider KV quantization
and speculation. Keep this evaluation separate from a commitment to ship every knob.
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

After Milestone B, select session features from observed return-use obstacles.
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

**Done when:** returning target users resume real coding work after restart,
understand the supplied context and recover without lost work. Compare their
return use before and after the selected session features; a successful demo alone
does not justify adding the rest of the chat/file scope.

### Milestone D status (2026-09-13)

The session, multiline/copy/retry, and selected-file snapshot subset in the
[Milestone D implementation plan](plans/2026-09-13-part3-milestone-d-earn-daily-use.md)
is implemented and automated checks pass. Product validation remains deferred:
the [Milestone D evidence record](compatibility/milestone-d.md) contains no
consented baseline/follow-up observations or qualifying named-Mac recovery
exercise. Milestones B and C remain unvalidated; implementation does not claim
adoption, restore a warm KV cache, or prove model readiness.

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

### UX follow-up (2026-09-10)

The dedicated Compare tab and its complete operational journey are now described
in the [comparison guide](comparison.md) and implemented in the
[unified comparison pane plan](plans/2026-09-10-unified-comparison-pane.md).
This follow-up updates the product shape; it does not change Milestone B's unmet
live qualification or product gates.

## Measurement is part of the product

The Compare tab should help users choose a model or setting. Pick two pinned
configurations, check readiness, run sequentially, inspect latency/memory and
answer quality, then keep one. Metrics remains a history view. Sequential
comparison avoids requiring enough memory for both models at once.

The first decision is: **which of two pinned coding profiles offers the better
latency/memory tradeoff on this Mac while passing one deterministic coding check?**
Use one fixed prompt and check, sequential runs and the same context/output budget.
A passing check supports this task only; it is not evidence of general coding quality.

Keep only evidence needed for that decision:

- Record chip/RAM, macOS, MLX/MLX-LM versions, pinned model and effective profile,
  prompt/template/check identity, and any settings that could not be verified.
- Separate first-after-load from repeated runs. Use five warm trials per profile
  with median and range; distinguish warm residency from verified cache reuse.
  Flag changed power conditions or concurrent load that make the comparison suspect.
- Show client-observed first-output and completion latency, sampled server RSS
  when process attribution is verified, quality pass/fail and all failed runs.
  Missing memory or engine timing stays unknown; sampled RSS is not an allocator
  peak. An incomplete comparison may support a limited choice but cannot establish
  a memory advantage.
- Save versioned results and the chosen profile locally. Let the user keep either
  profile, retain the baseline or reject both; record their reason during research.
  Prompt content enters diagnostic exports only by explicit choice.

Do not rank a failed quality check as a speed win. Small or inconsistent differences
should produce an inconclusive result. Document timing intervals if throughput is
shown; all output tokens must not be divided by an answer-only interval. “Keep this
profile” saves a reproducible choice, applies supported settings and makes any
operator-managed restart explicit.

Defer the broad protocol to a future optimization implementation plan: short chat,
long prompts, repeated prefixes, long-context retrieval, two-client contention,
sustained power/load trials, engine prefill/decode, cache instrumentation and
allocator peaks. That plan must define token accounting and measurement scope,
including process-wide versus per-request memory. Reuse upstream benchmarking
for engine baselines and a small HTTP runner for app behavior. A benchmark platform
or public leaderboard is outside this strategy.

## Build order

These milestones supersede unimplemented Part 2 priorities. Each should receive a
separate implementation plan after its scope is chosen; there are no calendar
estimates before runtime compatibility and maintenance costs are established.

| Milestone | Deliverable | Acceptance evidence | Defer if |
|---|---|---|---|
| **A — Trust the facts** | One pinned-runtime compatibility report, honest state labels, corrected metrics and stream handling needed for the coding task | Real-server checks on a named Mac; no unsupported state or metric presented as fact in the comparison path | Never defer correctness; hide measurements we cannot substantiate |
| **B — Prove the choice** | Attach-mode comparison: one coding task, two pinned profiles, sequential results and “keep this profile” | Five existing MLX operators across two named memory tiers: at least four complete a trustworthy comparison without external instructions or developer intervention, at least three make and explain an evidence-backed choice, and at least three return in a separate session within 14 days to inspect, compare or run the choice | Decision value or return use is absent; revise the workflow or job before funding C/D |
| **C — Own activation** | Minimum install artifact, pinned managed runtime, version detection, coding shortlist and recovery | Five fresh-install target users across two tiers; at least four reach the same comparison without manual server setup or developer intervention; verify ownership and shutdown | B is unproven, or runtime integration requires an inference-engine rewrite |
| **D — Earn daily use** | Only session, file and context features that address observed obstacles | Returning users complete real tasks after restart without lost work; observed repeat use improves from their pre-feature baseline and users attribute value to the added workflow | Persistence passes a demo but does not change return use |
| **E — Serve real clients** | Verified client setup, contention and explicit model-switch policy | TUI plus one external client pass repeated mixed workloads; target users actually reuse that endpoint | Required lifecycle guarantees cannot be enforced or client demand is absent |
| **F — Ship a supported release** | Broader packaging/version matrix, diagnostics, upgrades and recovery | Clean install/upgrade/offline-start checks, no orphaned managed process, personal one-week use and two-week real use by multiple external target users | Reliability or adoption gates remain open |

These are proposed decision thresholds, not measured results or population-level
proof. Recruit beyond the maintainer's own usage. With participants' consent,
record completion, assistance, decision/reason, later use and reasons for choosing
an alternative through observed sessions and follow-up; no background analytics
is required. Count dropouts and failures, and examine reasons even when the numeric
gate passes. Confirming the baseline is a valid decision if the evidence explains
it. A failed B gate triggers another focused validation cycle, not automatic
expansion into managed setup or chat parity.

Milestone C must include one documented, versioned TUI installation artifact and
one reproducible way to obtain its pinned MLX-LM runtime in an app-owned isolated
environment. Its implementation plan must select the exact artifact and resolver
before recruiting fresh-install testers. Detect versions before startup, report
mismatches with a recovery action and never overwrite the operator's attach-mode
environment. Tie the managed subprocess lifetime to the TUI. Auto-update, release
channels, broad compatibility and upgrade recovery remain in F. This minimum
packaging work is part of activation, not something deferred until release polish.

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

Then plan Milestone B around those findings and recruit existing MLX operators
for the attach-mode coding comparison. Validate activation, an explainable choice
and later reuse before planning managed acquisition or durable chat. The first
promise is narrow and observable: **compare two supported configurations on this
Mac, understand the evidence, and use the chosen profile with truthful status.**

## Milestone A — result and B readiness (2026-09-08)

Milestone A is implemented per `docs/plans/2026-09-07-part3-milestone-a-trust-the-facts.md`.
Evidence: [compatibility/milestone-a.md](compatibility/milestone-a.md) on
`local-m4-16gib` (MLX-LM `74e7cf9`, MLX 0.32.2, Qwen3-1.7B-4bit
`3b1b1768`, coding-check-v1). Historical research dates above are retained;
this section records what live requests proved.

Evidence-based corrections: `/health` and `/v1/models` are independent;
catalogue entries are availability only and never imply selection or
residency. Selection is explicit; response `model` echoes the request and
`default_model` on missing-`model` requests is not attributable, so
unselected requests stay refused. Completion requires `[DONE]` plus finish
`stop` plus non-empty answer; reasoning is preserved separately, tools stay
unexecuted, and length-capped/reasoning-only output is incomplete. Metrics
are client-observed first-output/answer/total plus client request tok/s;
no prefill/decode speed, no cold claims, cached tokens mean server reuse.
Memory is GiB process RSS plus avail/total; context numbers are character
estimates with excluded-turn counts. Cancellation is client-side only.

B readiness: runtime `74e7cf9`, model revision `3b1b1768`, and
coding-check-v1 are pinned identities for the comparison input. Two larger
cached revisions (`Qwen3.5-4B-MLX-4bit@32f3e8e`, `Ornith-1.5-9B-MLX-4bit@a48173b`)
are unqualified resources, not recommendations. Only `local-m4-16gib` has
real evidence; a second named, physically available memory tier is still
missing and remains an explicit B-entry blocker. Structured tool output for
this model/settings, KV quantization, allocator peaks, prefill timing,
residency, and engine cancellation remain unknown.

## Milestone B — implementation and validation status (2026-09-09)

The attach-mode two-profile comparison, durable results, explicit Keep/Retain/
Reject decision, saved-profile reuse, and opt-in real-runtime contract are
implemented. Automated implementation evidence is tracked in
[`plans/2026-09-09-part3-milestone-b-prove-the-choice.md`](plans/2026-09-09-part3-milestone-b-prove-the-choice.md).

Qualification and product validation are not complete. No live B run has been
performed, the second physical tier is still unknown, the proposed owner is not
confirmed for publication, and the five-operator/14-day observation gate has
not started. The exact qualification inputs, retained-evidence table,
consent-based observation sheet, and focused next question are in
[`compatibility/milestone-b.md`](compatibility/milestone-b.md). Milestone B is
therefore not achieved, and Milestones C/D remain gated.

## Milestone C — implementation versus validation status (2026-09-12)

The managed implementation is present behind explicit `--managed`/setup
selection. It includes an app-owned pinned runtime, retained child identity,
verified offline snapshot loading, setup-to-Compare navigation, saved-choice
activation recovery, and opt-in live contract tests. Attach mode remains the
default for existing configurations and never adopts or stops discovered
processes. Automated implementation evidence is tracked in
[`plans/2026-09-12-part3-milestone-c-own-activation.md`](plans/2026-09-12-part3-milestone-c-own-activation.md).

Validation is not complete: Milestone B still has no qualified second physical
tier or product observations, and no fresh-install users have been recruited.
The opt-in C contract requires exact runtime/model inputs and a retained output
record before it can run. C is not achieved until at least four of five
consenting fresh-install users reach the trustworthy comparison without manual
server setup or developer intervention, and ownership/shutdown checks pass.
No recommendation, tier claim, or user result is inferred from this
implementation checkpoint.

## Milestone E — qualification only; shared serving blocked (2026-09-13)

Phase 3 implements the opt-in HTTP plus OpenCode 1.18.28 qualification
suite (`tests/runtime/test_milestone_e.py`) and the client recipes in the
[client guide](clients.md). No live runs are retained yet, so the
[Milestone E evidence record](compatibility/milestone-e.md) stays
qualification-only: supported shared serving stays blocked because stock
upstream `74e7cf9` cannot reject wrong-target requests before loading or
coordinate lifecycle changes across external clients. Pi receives no
compatibility claim from OpenCode results.

The full E gate is enforceable wrong-target rejection plus lifecycle
coordination, repeated TUI-plus-client workloads, and consenting
target-user separate-session reuse. A completed implementation plan
substitutes for none of these. Milestones B, C, and D remain unvalidated
per their sections above.

## Milestone F — implementation versus release qualification (2026-09-15)

The 0.3.0 candidate has retained wheel/sdist bytes, private diagnostics,
isolated predecessor upgrade/rollback evidence, and the opt-in installed
qualification contract in `tests/runtime/test_milestone_f.py`. The contract is
fail-closed and records package/runtime/profile identity, managed chat and
recovery, session attachment preservation, and owned cleanup outcomes without
collecting prompts or other conversation content.

F is not achieved. No live contract, clean-account install, genuinely offline
restart, real-MLX shutdown run, seven-day personal observation, or fourteen-day
two-user observation is retained. The current host is macOS 27.0 while the
managed matrix is pinned to macOS 26.6.2; broader hardware remains unsupported.
The release decision stays **blocked**, and no publishing, tagging, uploading,
or participant messaging is authorized.
