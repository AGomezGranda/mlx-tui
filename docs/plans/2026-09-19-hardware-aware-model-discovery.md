# Hardware-aware model discovery and recommendations

**Date:** 2026-09-19  
**Status:** Proposed; product direction agreed, implementation not started  
**Deliverable:** Research and design proposal only

## 1. Purpose and agreed direction

Help users answer three questions before downloading or selecting a model:

1. Can this application's runtime use this exact model variant?
2. Is it likely to run comfortably on my Mac at the context length I need?
3. Why would I choose it for chat, coding, or reasoning?

Replace the disconnected installed-model table and Hugging Face search modal with a unified **Models** workspace containing **Installed** and **Discover** views. Both views use the same model facts, compatibility assessment, and memory estimator.

The following decisions were agreed during planning:

| Decision | Agreed direction |
|---|---|
| Recommendation objective | Task-aware balance for chat, coding, and reasoning |
| Publisher coverage | Discover MLX models across Hugging Face, including direct repository links |
| Unsupported models | Keep visible, explain requirements, exclude from runnable recommendations |
| Interface | Unified Installed/Discover workspace |
| External recommendation engine | Native Python implementation; external projects are references |
| Initial evidence | Contextual estimates with explicit assumptions; no new benchmarking workflow |
| Runtime scope | Current MLX-LM execution paths; no installation of model-specific runtimes in v1 |

Success means a user can understand suitability and its uncertainty without confusing disk size, runtime support, and memory capacity. A green disk check must never imply that a model can run.

## 2. Current implementation and diagnosis

These findings come from the repository inspected on 2026-09-19.

| Area | Current behavior | Consequence |
|---|---|---|
| `src/mlx_tui/table.py` | `runtime_fit_cell()` always returns `unknown`; marker refresh deliberately skips fit | The fit feature is a placeholder, rather than a working estimator with an isolated arithmetic bug |
| `src/mlx_tui/search.py` | `list_results()` fixes `author="mlx-community"` and returns up to 50 IDs | Valid models from other publishers are excluded from ordinary search |
| `src/mlx_tui/search_screen.py` | Search fetches download size and renders a disk-space glyph | No runtime-memory assessment exists in discovery |
| `src/mlx_tui/models.py` | Cache detection checks for configuration, tokenizer configuration, and safetensors files | Generic or unsupported safetensors repositories can look like MLX candidates |
| `ModelRow.size_on_disk` | Repository-level cache usage includes revisions | It cannot stand in for the resident weights of one selected revision |
| `repo_snapshot()` | Missing file sizes become zero | An incomplete sum can be displayed as if it were an exact download size |
| Quantization display | Extracted from a repository-name suffix | Useful search hint, but not authoritative model metadata |
| Hardware observations | `process.py` samples system memory; diagnostics records total RAM | No shared hardware suitability profile is available to the model views |
| Existing comparisons | Memory summary is explicitly inconclusive because RSS is process-wide | Existing samples cannot be relabeled as verified per-model memory requirements |

The focused baseline passed: **53 tests** across `test_models.py`, `test_search.py`, and `test_table.py`. One test explicitly requires runtime fit to remain unknown. Implementation must replace that expectation with meaningful assessment tests.

The inspected development environment reports MLX-LM 0.31.3. Its MLX device probe reported an Apple M1 Pro, 16 GiB unified memory, and approximately 11.84 GiB recommended working set. This is an example showing why total RAM alone is insufficient, not a profile to hard-code for other users.

`AppConfig.max_ctx` already defaults to 8,192. The application also supports attached and managed runtimes. Runtime compatibility must concern the runtime that will execute a request, which may differ from the Python environment hosting the TUI.

## 3. Relevant projects and reuse decision

Sources below were inspected on 2026-09-19. Their implementations and catalogs can change; links to moving branches are research references, not reproducible dependencies.

| Project | Relevant approach | What to adopt conceptually | Limitations for this project |
|---|---|---|---|
| [llmfit](https://github.com/AlexsJones/llmfit) | Hardware discovery, memory classifications, task scoring, confidence labels, and runtime providers | Explainable fit levels and separation of measured versus estimated evidence | General backend assumptions and quantization catalogs need MLX-specific validation |
| [hf-agents](https://github.com/huggingface/hf-agents) | Wraps llmfit for hardware-aware selection and launches a local coding workflow | Example of integrating an external recommendation engine | Its documented serving path uses llama.cpp; adopting it would not resolve MLX compatibility |
| [Rondine](https://github.com/antonellof/rondine) | Hardware-aware planning, curated candidates, live Hub discovery, and explicit configurations | Connect each suggestion to its intended context and runtime settings | Broader runtime management is beyond this redesign; no code reuse without reviewing its license |
| [MacLLM](https://github.com/kuarezma/MacLLM) | Hardware-adaptive model groups and context defaults | Understandable recommendation groups and hardware summary | Its GGUF/llama.cpp execution path is not interchangeable with MLX-LM |

llmfit is the closest recommendation reference. Its [methodology](https://github.com/AlexsJones/llmfit/blob/main/docs/how-it-works.md) describes fit bands and task-dependent scoring. Those are useful UX concepts, but its generic quality heuristics and offloading assumptions must not become unexamined facts in mlx-tui.

**Decision:** build a small native assessment layer around exact MLX model variants. Do not require a Rust executable, separate service, or external recommendation process. This keeps runtime identity, cached snapshots, discovery, and recommendations consistent with the existing application.

Do not copy third-party catalogs, benchmark tables, or implementation code as part of this documentation task. Any later reuse must pin the source revision and preserve applicable attribution and license terms. Repository popularity is not evidence of estimator correctness.

## 4. User experience

### 4.1 Workspace structure

The Models workspace contains:

- A hardware summary: chip, total unified memory, available memory, and recommended working set when known.
- Installed and Discover view selectors.
- A task selector: Chat, Coding, or Reasoning; default Chat.
- An assessment context field initialized from `max_ctx`.
- Search and filters for publisher, compatibility, and estimated fit.
- A results table with a shared model-details area below it.

Use the existing Textual table and worker patterns. At narrow terminal widths, keep model name, compatibility, and memory fit visible; move secondary metadata into details. Preserve cursor selection by repository/revision identity during metadata refresh.

Illustrative layout; values are placeholders, not model measurements:

```text
Models   [Installed] [Discover]
Apple M1 Pro · 16 GiB unified · available now … · recommended working set …
Task: Chat     Estimate at: 8192 tokens     Search: …

Model / publisher       Compatibility       Memory estimate       Disk
publisher/model-a       Supported           Comfortable*          …
publisher/model-b       Requires loader     Unknown               …

* Estimated for one sequence at 8192 tokens.
Selected model: revision, quantization, reasons, assumptions, source links
Actions: Download / Load / Delete / Open model page, as applicable
```

### 4.2 Installed

Show cached candidates immediately without waiting for the network. Preserve selection, deletion, and load/swap operations. Keep aggregate disk usage useful for cache management, but label the revision used for compatibility and memory assessment separately.

If a repository has multiple revisions, assess the snapshot selected by the existing resolver. Show its revision in details; do not silently assess one snapshot and load another. After deletion, invalidate affected facts and refresh the installed view.

### 4.3 Discover

Opening Discover shows task-aware suggestions instead of an empty input-only modal. Provide separate Recommended and All results filters so unsupported or uncertain candidates remain inspectable.

Accept free text, `owner/repository`, and standard Hugging Face model URLs. Exact identifiers bypass broad search and inspect the requested repository directly. A direct model lookup must not fail merely because its publisher is outside `mlx-community` or its MLX tags are incomplete.

Keep `/` as the discovery shortcut. Existing setup/comparison entry points that pass pinned candidates should open Discover with that candidate set and preserve the pinned revisions. Do not alter the existing qualified coding profiles merely because the user changes the discovery task selector.

### 4.4 Details and actions

Details show the resolved revision, publisher, architecture, verified quantization metadata or name hint, context assumptions, weight/download/cache sizes, runtime compatibility, and the explanation for each assessment.

Unsupported models remain downloadable for cache management, with their requirements visible. A known unsupported model cannot be launched through the app's current runtime action. Unknown compatibility is distinct from known incompatibility: retain the existing explicit load path with a visible uncertainty message, but exclude it from automatic recommendations.

Memory estimates are advisory, not proof of successful loading. A tight or over-budget estimate remains visible when a user explicitly chooses a model; it must not silently change context, select another model, or start a download.

## 5. Discovery and model facts

### 5.1 Candidate retrieval

Use Hugging Face's MLX library/tag metadata without an author restriction. The [Hub API](https://huggingface.co/docs/huggingface_hub/guides/search) exposes metadata filtering, author filtering, text search, and sorting independently.

Treat tags as candidate-generation signals. They are not loader certification. Exact ID/URL inspection provides the escape hatch for missing tags; broad discovery need not enumerate every safetensors repository on the Hub.

Fetch results in bounded batches, initially 50, with an explicit Load more action. Start with downloads ordering for general discovery; label it as popularity, not quality. Resolve metadata for visible rows and the selected row first. Avoid fetching every model card and configuration synchronously before rendering results.

### 5.2 Metadata and identity

Resolve each candidate to a commit SHA before reading configuration or estimating its files. Carry that SHA through assessment and download. Recheck required metadata when the candidate revision changes.

Record separate quantities:

- **Weight bytes:** the relevant weight set for one revision.
- **Full download bytes:** all files selected by the supported download contract.
- **Additional download bytes:** missing assets when their cache presence can be established reliably.
- **Cache disk usage:** current local storage, potentially including multiple revisions.
- **Estimated runtime memory:** a derived quantity for a stated execution scenario.

Preserve absent sizes as unknown. A partial sum may be shown as a lower bound, but never as a complete download or weight estimate. Avoid counting alternative weight sets or duplicate shard references twice. Shard indexes describe membership; validate that referenced files exist.

Quantization precedence is configuration/tensor metadata first, name-derived hint second. Per-module mixed precision must remain representable; a `2bit` suffix is not a promise that every tensor occupies exactly two bits per parameter.

### 5.3 Compatibility

Use these user-facing states:

| State | Meaning |
|---|---|
| Supported | Inspected architecture, assets, and quantization are supported by the identified runtime |
| Requires different runtime | Known custom loader, unsupported architecture, or unsupported quantization contract |
| Unknown | Metadata or executing-runtime identity is insufficient |

Supported means supported by inspection, not a successful test run. Surface actual load failures separately and retain their reasons.

Managed mode can use its recorded/inspected runtime capabilities. Attached mode must not infer the remote server's capabilities from the TUI's installed packages. Without sufficient endpoint/runtime evidence, report unknown. Likewise, label the local Mac assessment as local; do not claim it describes a server on another machine.

Inspect data files without importing repository Python or executing model-provided code during discovery. Do not add runtime installation, custom-loader registration, or automatic trust of remote code to v1.

### 5.4 Bonsai acceptance example

The supplied [Ternary-Bonsai-2-27B-mlx-2bit repository](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-mlx-2bit#quickstart) illustrates why publisher-independent discovery and compatibility inspection must ship together. At inspection time, its documentation names a custom model type and a bundled loader. It also distinguishes packed language weights from the full artifact.

Expected behavior:

1. Pasting its URL resolves the repository regardless of publisher.
2. Details show its actual revision and configuration, not assumptions derived from `27B` or `2bit` in the name.
3. A current runtime without the required loader receives a compatibility explanation, not a runnable recommendation.
4. No custom code is executed to inspect it.
5. Do not derive an MLX throughput estimate from benchmark numbers reported for a different backend.

Use a frozen metadata fixture for regression coverage because the live repository may change.

## 6. Hardware and memory assessment

### 6.1 Hardware profile

Collect OS/architecture, chip/device name, total RAM, current available RAM, and the Metal recommended working set where available. Prefer the supported [`mlx.core.device_info`](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.device_info.html) API, with a compatibility adapter for the packaged runtime when necessary.

Do not hard-code chip names as a requirement for fit estimation. An unfamiliar chip with valid memory observations can still receive memory estimates; performance remains unknown. Failure to probe Metal must degrade to incomplete evidence rather than prevent the model list from rendering.

Cache stable device facts for the session; refresh availability through the existing memory polling path. Apple unified memory is one pool: never add a GPU memory figure to total system RAM. Recommended working set is guidance, not a guaranteed allocation limit.

### 6.2 Execution scenario

Every assessment includes:

- Exact model revision and runtime identity.
- Context occupancy in tokens, initially the configured `max_ctx`.
- One active sequence in v1.
- Supported cache dtype/quantization and architecture-specific behavior, when known.
- An estimator version and list of assumptions.

Context occupancy includes prompt and generated tokens retained in the cache. It is not synonymous with output `max_tokens`. Changing the estimate field recomputes the assessment; it does not silently reconfigure an attached server or guarantee enforcement of its memory usage.

### 6.3 Estimate components

Use the following decomposition:

```text
estimated peak = resident weights + attention cache + recurrent state
               + runtime / prefill / allocator allowance
```

For supported conventional attention architectures, an unquantized KV estimate can use:

```text
KV bytes = 2 × layers × retained tokens × KV heads × head dimension
             × bytes per cache element × active sequences
```

Use KV heads rather than query heads for grouped-query attention. Do not assume weight quantization also quantizes the cache. Quantized caches need their own scale/metadata overhead. Sliding-window, hybrid attention, recurrent models, and asymmetric key/value dimensions require architecture-specific adapters; do not apply this formula indiscriminately.

Start with conventional attention support and return unknown for architectures without a validated adapter. For MoE, all resident expert weights count toward memory even when only a subset participates in each token's computation. Do not assume offloading or SSD paging makes an oversized model comfortably runnable.

Selected weight-file sizes provide a practical starting point only when the loader preserves the inspected representation. Packed, mixed-precision, or custom representations require explicit handling. Unknown conversion or allocation behavior must lower confidence or prevent a complete estimate.

### 6.4 Proposed initial policy

The following are **provisional product defaults**, not measured guarantees. Keep them centralized and versioned so calibration does not require UI changes:

| Quantity | Initial policy |
|---|---|
| OS/application reserve | Greater of 4 GiB or 20% of total RAM |
| Stable model budget | Minimum of total RAM minus reserve and recommended working set, when the latter is available |
| Runtime allowance | Greater of 1 GiB or 10% of resident weights, until architecture-specific evidence replaces it |
| Comfortable | Complete estimate at or below 80% of stable model budget |
| Tight | Complete estimate above 80% and at or below the budget |
| Exceeds budget | Complete estimate above the budget, or a known lower bound already above it |
| Unknown | Essential metadata, memory budget, or supported estimation method is missing |

The runtime allowance is a coarse starting heuristic; it may understate prefill peaks. Mark these results as estimated, and retain this limitation in details. Never classify a model as comfortable using only a lower bound.

Report current availability separately from stable fit: for example, “Comfortable estimate for this Mac; available memory is currently below the estimate.” Avoid reordering the entire table on every memory sample.

Do not add the active server's RSS back into available memory when predicting a replacement. RSS is not proven reclaimable model residency. Warm swaps, concurrent clients, and retained prompt caches can raise peaks beyond the single-model scenario; report that limitation instead of claiming a guaranteed safe swap.

## 7. Recommendation policy

Recommendations combine eligibility and task evidence. They do not need an opaque 0–100 score in v1.

1. Require an inspected compatible runtime and a complete comfortable-fit estimate for the Recommended group.
2. Require positive task evidence for the selected task. Models without it remain discoverable and may be labeled “Fits this Mac; task suitability unverified.”
3. Prefer task evidence tied to the exact variant over family-level claims. Mark family evidence as indirect.
4. Within the same evidence tier, prefer greater estimated headroom; break ties by downloads, then repository ID for stable ordering.
5. Put tight candidates in a separate “Consider with less headroom” group. Keep incompatible and unknown candidates in All results.

Use a small versioned task-evidence catalog with source links, review dates, model/revision scope, and short explanations. Published benchmark evidence, documented model purpose, and local results are different evidence classes; do not merge them into an unsupported claim of measured quality.

Default recommendations should explain tradeoffs: “documented coding specialization; comfortable at 8K” is more useful than “score 87.” Do not treat larger models as universally better, higher download counts as quality scores, or every low-bit model as equivalent.

Existing comparison results may appear as scoped observations when identity and settings match. Their synthetic coding check is not evidence of general coding ability, and their process-wide RSS is not per-model peak memory. If no candidates satisfy the evidence requirements, show that honestly and offer the wider results list.

## 8. Implementation boundaries and data flow

Introduce a headless `catalog/` package for discovery facts and assessment. Keep UI orchestration in the Models workspace. Avoid placing network calls, hardware probes, ranking, and widget rendering into one enlarged pane class.

Suggested internal contracts:

| Contract | Responsibility |
|---|---|
| `HardwareProfile` | Stable local capabilities plus timestamped memory observations |
| `RuntimeCapabilities` | Executing runtime identity, supported architectures/formats, evidence status |
| `ModelFacts` | Repository/revision identity, files, architecture, quantization, context limits, metadata provenance |
| `AssessmentScenario` | Context occupancy, active sequences, cache settings, runtime identity |
| `ModelAssessment` | Compatibility, memory components/status, download status, assumptions and reasons |
| `Recommendation` | Task, eligibility group, evidence tier, and user-facing explanation |

Use bytes internally and GiB in the interface. Missing values remain optional, not zero. Pure assessment functions take facts and a scenario; they perform no network access and do not load weights.

```text
Hub search / cache scan
          ↓
revision-specific facts ← metadata cache
          ↓
compatibility + memory assessment ← hardware + runtime + scenario
          ↓
task-aware ranking
          ↓
Installed / Discover / shared details
```

Cache immutable facts by repository and revision. Cache search results separately with a proposed 24-hour lifetime and explicit refresh. Keep timestamp/source information visible for stale results. Recompute assessments when hardware, runtime, context, or estimator version changes; do not cache derived fit solely by repository ID.

Bound metadata requests, cancel obsolete work, and use the existing search-generation pattern to reject late responses. Treat authorization errors, missing repositories, rate limits, and network failures as distinct states. Never interpret a fetch failure as a zero-byte model or a confirmed unsupported architecture.

This redesign requires no public HTTP API or comparison/session schema change. Preserve existing model selection and managed-runtime contracts. Any persisted discovery preferences or metadata cache should use an independent versioned format.

## 9. Delivery stages

### Stage 1 — Facts and broader discovery

Remove the publisher restriction, add direct ID/URL inspection, preserve unknown sizes, and bind metadata/downloads to revisions. Add compatibility states and the Bonsai fixture before presenting broader search results as loadable.

**Exit condition:** a non-community repository can be discovered and inspected without making unsupported compatibility claims.

### Stage 2 — Hardware and memory assessment

Introduce hardware/runtime profiles and pure estimates for supported conventional attention models. Centralize provisional margins, preserve unknown cases, and distinguish stable fit from current availability.

**Exit condition:** identical facts and scenarios yield identical assessments; larger context increases the supported KV component; unknown information never produces an unjustified comfortable label.

### Stage 3 — Unified workspace

Build Installed/Discover navigation and shared details. Redirect search shortcuts and pinned-candidate flows. Preserve load, delete, selection, progress, and operation locking.

**Exit condition:** users can discover, inspect, download, and select supported models while existing setup/comparison workflows still pass.

### Stage 4 — Task recommendations

Add reviewed task evidence and the transparent ranking policy. Display recommendation reasons and explicitly separate estimates from matching local observations.

**Exit condition:** each recommended row explains its task evidence, runtime support, and context-dependent fit; unsupported or unevaluated candidates are not silently promoted.

## 10. Verification and acceptance scenarios

Use mocked Hub responses and frozen model metadata for deterministic automated tests. Live Hub smoke checks are optional and must not download large weights or become a CI dependency.

| Scenario | Required result |
|---|---|
| Compatible model outside `mlx-community` | Discoverable, inspectable, and eligible on the same terms as community models |
| Direct URL with missing MLX tags | Exact lookup succeeds without publisher/tag filtering |
| Bonsai custom-loader fixture | Visible with requirements; not recommended for the ordinary runtime |
| Generic safetensors repository | File presence alone does not establish MLX compatibility |
| Unknown file size | Unknown/partial size is preserved; no false exact disk-fit check |
| Multiple revisions / repeated shard references | Runtime weights counted for one revision and deduplicated |
| Remote metadata changes during workflow | Assessment and download remain tied to the chosen commit |
| GQA architecture | KV estimate uses KV heads, not query heads |
| Context grows from 8K to 32K | Supported non-windowed KV component grows fourfold; weight component stays fixed |
| MoE with low active parameter count | Resident memory still includes all loaded experts |
| Hybrid/recurrent architecture without adapter | Unknown estimate with reason |
| Probe failure or unidentified attached runtime | Partial hardware/compatibility evidence; no crash or invented certainty |
| Local Mac with remote endpoint | Local fit clearly scoped; remote hardware suitability remains unknown |
| Memory availability fluctuates | Live advisory updates without continual recommendation reshuffling |
| Warm swap / retained caches | No unsupported reclaimability calculation from server RSS |
| Metadata worker finishes after another query | Old result cannot overwrite current rows |
| Offline / gated / rate-limited Hub | Installed models remain usable; actionable discovery status |
| Task changes | Ranking/reasons update; pinned coding-profile qualification is unchanged |
| Narrow terminal | Essential fields and keyboard actions remain usable |
| Download cancellation, deletion, load/swap | Existing operation coordination and pinned flows remain intact |

Run focused catalog/model/search tests first, then affected Textual integration tests for search, setup, swap, deletion, and app behavior. Complete the repository's lint and type checks. Run packaging smoke coverage when new catalog resources are added.

For this documentation-only deliverable, validate Markdown structure, local references, and the diff. Application tests need not be rerun merely because this file is added.

## 11. Open questions and deferred work

These do not block the documentation or the agreed direction. Resolve the calibration and evidence questions before presenting the feature as validated on a broad range of Macs.

| Question | Proposed default / next step |
|---|---|
| Which task benchmarks should be maintained? | Begin with a small manually reviewed catalog carrying source, date, and variant scope; do not scrape an unqualified universal score |
| Are the provisional reserves and overheads adequate? | Validate representative 8/16/24/32/64+ GiB machines and varied context lengths before tuning the centralized policy |
| Which hybrid/recurrent architectures come first? | Prioritize models users actually discover; require a fixture and architecture-specific estimate before removing unknown status |
| Can attached-runtime capabilities be identified reliably? | Use existing verified runtime evidence where available; otherwise preserve unknown rather than assume local-package parity |
| How should prefill and retained caches be modeled? | Keep the current limitation explicit; expand only with scoped measurements or runtime instrumentation |
| How should task evidence expire? | Record review dates now; choose an expiry policy once catalog maintenance has an owner and cadence |
| Should fit estimates support explicit multi-client scenarios? | Defer beyond the one-sequence v1 scenario |
| Should custom runtimes such as Bonsai's be managed? | Deferred by agreement; discover and explain requirements only |
| Should users run guided benchmarks? | Deferred by agreement; reuse existing observations only within their original scope |

## 12. Source index

- [llmfit repository](https://github.com/AlexsJones/llmfit) and [methodology](https://github.com/AlexsJones/llmfit/blob/main/docs/how-it-works.md).
- [Hugging Face hf-agents](https://github.com/huggingface/hf-agents).
- [Rondine](https://github.com/antonellof/rondine).
- [MacLLM](https://github.com/kuarezma/MacLLM).
- [Hugging Face Hub search documentation](https://huggingface.co/docs/huggingface_hub/guides/search).
- [MLX device information API](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.device_info.html).
- [Bonsai model card and loader requirements](https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-mlx-2bit#quickstart).

External descriptions are research inputs. The proposed fit thresholds, ranking policy, UI, and implementation boundaries above are design choices for mlx-tui, not claims that those projects validate them.
