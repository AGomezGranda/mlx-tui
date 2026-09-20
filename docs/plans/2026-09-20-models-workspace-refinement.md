---
date: 2026-09-20
title: Models workspace layout and assessment refinement
status: complete
---

## Implementation checklist

- [x] Phase 1 — Correct and explain assessments
- [x] Phase 2 — Build the Installed layout and context control
- [x] Phase 3 — Embed the existing Discover flow

## Overview

Give Models a stable two-column layout: system information and controls on the left; the installed-model table and selected-model details on the right. Discover replaces the right-hand content when opened, retaining its existing search, filtering, suggestions, inspection, and download flow. Correct misleading or stale assessments alongside the layout work.

This plan follows the user's refinement of the earlier [hardware-aware discovery proposal](2026-09-19-hardware-aware-model-discovery.md). It targets the current working tree, which already contains uncommitted catalog and UI work. Preserve that work; the earlier document's statement that implementation has not started is historical.

## Design decisions

- Keep model details with the table, in a compact panel beneath it. Do not place model details in the left column or expand individual table rows.
- Left column: labelled chip and unified-memory information, available memory, assessment budget, Discover navigation, and the Installed context control. Explain the budget as an estimate; expose the Metal working-set figure in secondary hardware information.
- Right column: installed table using the available height, followed by a bounded selected-model summary. Preserve model selection, load/delete actions, disk usage, and quantization hints. Rename the displayed “runtime fit” heading to “Memory fit” for consistency with Discover.
- Use a 30-cell sidebar at widths of at least 120 cells. Below that, use a compact full-width header with collapsible hardware information and the table underneath. At 80×24, retain at least four visible table rows; secondary columns may scroll horizontally. These are draft implementation defaults, subject to visual verification.
- Label the Installed control “Estimate context”. Offer 2K, 4K, 8K, 16K, 32K, and Custom; K means 1,024 tokens. Initialize from `config.max_ctx`, selecting Custom for non-preset values. Presets apply immediately; custom positive integers apply on Enter. Empty, nonnumeric, zero, and negative input show an inline error while preserving the last valid estimate. The field changes only the estimate, not configuration or the executing server.
- Keep the chosen Installed estimate for the lifetime of the pane. During Discover, hide the Installed-only context control; preserve Discover's existing context input and independent state. This avoids two conflicting controls and leaves discovery interaction redesign for later.
- Selected-model summary: model identity; compatibility and reason; memory estimate at the applied token count; weights and cache disk usage. Use labelled rows, not a paragraph of metadata. Show current memory pressure separately from stable fit. “More details” opens a read-only view with revision, architecture, quantization, memory components, and assumptions.
- Opening Discover changes the right-hand view and focuses its search input. Returning restores the installed cursor and table focus. Preserve the current successful-download and acknowledged-cancellation return behavior. Escape during download requests cancellation and waits for acknowledgement.
- Keep setup/comparison discovery dialogs and pinned candidates working through a shared discovery widget. Only discovery launched from Models moves inline.

```text
Models
┌──────────────────────────┬──────────────────────────────────────────┐
│ YOUR MAC                 │ Installed models                         │
│ Chip                     │ Model · Compatibility · Memory fit · …  │
│ Unified memory           │ …                                        │
│ Available now            │ …                                        │
│ Assessment budget        │ …                                        │
│                          ├──────────────────────────────────────────┤
│ Discover on Hugging Face │ Selected model                           │
│                          │ Compatibility   Status and reason        │
│ Estimate context [8K ▾]  │ Memory          Estimate at 8,192 tokens │
│ Used for estimates only  │ Weights · Cache disk       More details  │
└──────────────────────────┴──────────────────────────────────────────┘
```

## Verified implementation context

- `src/mlx_tui/models_pane.py:77` composes every element vertically. `_show_selected_details()` at line 230 mixes identity, metadata, and assessment text into one `Static`; an empty selection returns without clearing the previous details.
- `src/mlx_tui/catalog/hardware.py:65` verifies only a managed runtime with installation evidence. `src/mlx_tui/managed/runtime.py:53` initializes that evidence empty; `start()` at line 164 populates it. An installed managed runtime can therefore remain unverified until a model is loaded.
- `src/mlx_tui/app/polling.py:412` refreshes markers without reassessing. A managed boot calls this path, leaving earlier assessment results stale until a rescan.
- `src/mlx_tui/catalog/facts.py:98` treats the presence of attention-layout keys as hybrid, including a null `sliding_window`. Dimensions are currently read from the top-level config. `ModelAssessment` has a compatibility reason but no dedicated memory-fit reason (`catalog/assessment.py:77`).
- `src/mlx_tui/search_screen.py:439` skips detail updates for already inspected rows; `_fill_size_cell()` at line 612 updates details for whichever metadata request completes. Both can detach details from the highlighted row.
- `ResultsTable.action_start_download()` (`search_screen.py:68`) assumes its screen is `SearchScreen`. Setup (`setup_screen.py:290`) and comparison (`compare/workflow.py:300`) also construct that screen with pinned candidates. Embedding requires separating widget ownership from modal navigation.
- Existing pytest/Textual harnesses and mocked Hub responses support deterministic tests. CI commands live in `.github/workflows/ci.yml`.

These are code-supported causes of unknown/stale output, not proof of which cause affects every locally installed model. During implementation, reproduce with available local metadata before claiming the reported issue is fully resolved; automated coverage must use frozen metadata.

## Phase 1 — Correct and explain assessments

### Overview

Make assessments reflect the available runtime/model evidence and the highlighted row. Preserve justified uncertainty and give compatibility and memory fit separate explanations.

### Changes

- In `catalog/facts.py`, distinguish explicitly disabled/null conventional-attention options from active or unfamiliar layouts. Add frozen conventional config fixtures; do not treat arbitrary falsey or malformed values as evidence of conventional attention. Keep nested/unsupported architectures unknown with a specific reason rather than deriving an incomplete multimodal estimate.
- In `catalog/assessment.py`, add a memory-fit reason and propagate missing weights, missing dimensions, unsupported attention, invalid scenario, missing hardware budget, and lower-bound-over-budget explanations. Split runtime-unverified and model-metadata-missing compatibility explanations. Retain existing thresholds and conservative compatibility states.
- In `managed/runtime.py`, expose a read-only, synchronized inspection method using the existing ownership check and `inspect_runtime()`. Run it off the UI thread when Models needs managed-runtime evidence, before loading a model. Inspection must not install, start, or download anything. Cache session evidence; clear it on failed reinspection/runtime replacement, and retain start-time verification.
- In `models_pane.py`, retain per-row assessment failure reasons instead of silently swallowing metadata failures. Recompute assessments from cached facts after context or runtime evidence changes, without rescanning files on each edit. Reject stale worker results using a generation plus scenario/runtime identity. Keep the displayed revision tied to the existing assessed snapshot; do not relabel hash ordering as “latest”.
- Update runtime-change/boot completion hooks in `app/ops.py` and `models_pane.py` to trigger reassessment. Keep routine polling and marker refresh lightweight; do not run runtime inspection on every memory sample.
- In `search_screen.py`, always render details for the highlighted identity, even when metadata is already cached. Background completions update their own row and only update details if still selected. Clear details when selection/results disappear. Show memory-fit reasons without changing discovery controls.

### Tests written before implementation

- Unit, `tests/unit/test_catalog.py`: null/disabled window metadata for a supported conventional fixture produces a full estimate; active/unknown layout remains unknown; missing dimensions/weights/budget produce distinct reasons; unverified runtime can coexist with a valid local memory estimate. Retain existing custom-loader and incomplete-shard cases.
- Unit, `tests/unit/test_managed.py`: inspecting a valid installed runtime exposes evidence before boot without starting/installing; failed inspection clears previous evidence; missing runtime remains unverified. Mock subprocess inspection.
- Integration, new `tests/integration/test_models_integration.py`: runtime evidence transition updates existing rows without tab switching; an older context assessment cannot replace the latest; empty/deleted selection clears details.
- Integration, `tests/integration/test_search_integration.py`: revisiting an inspected row shows its details; another row's late metadata cannot replace them; filtering to no rows clears the panel. Reuse existing stale-search tests rather than duplicate them.

### Success criteria

Automated:

```sh
uv run pytest tests/unit/test_catalog.py tests/unit/test_managed.py tests/unit/test_table.py tests/integration/test_models_integration.py tests/integration/test_search_integration.py
```

Manual: inspect a supported cached conventional model and one incomplete/unsupported model; verify both statuses have independent explanations. In attached mode, compatibility explicitly explains the unverified server while local memory fit remains independently useful. An installed verified managed runtime can be assessed before model boot.

### What we're NOT doing

No new attached-server capability protocol, hybrid/recurrent/multimodal estimator, new architecture allowlist, benchmark workflow, custom runtime installation, memory-policy recalibration, or revision-selection redesign.

## Phase 2 — Build the Installed layout and context control

### Overview

Introduce the two-column workspace and a readable details panel beneath the table. Give context selection an explicit label, validation, and predictable application behavior.

### Changes

- Rework `ModelsPane.compose()` and its CSS in `models_pane.py` into sidebar and main containers, with resize handling for the compact layout. Retain the existing table instance and its keyboard actions; avoid remounting it during resize.
- Add the preset/custom context control and last-valid applied value. Route changes through phase 1's reassessment path. Show the applied token count in the details so an invalid draft cannot mislabel the displayed estimate.
- Replace hardware prose with labelled values and secondary information. Render unavailable probes explicitly; keep the memory budget consistent with `catalog/assessment.py` rather than duplicating its arithmetic in the widget.
- Render selected-model details as bounded labelled rows, with compatibility and memory reasons visible. Keep model identity/facts visible even when weight size is unknown. Reuse `TextPreviewScreen` for full details, including the exact revision and existing assumptions.
- In `table.py`, change the displayed heading to Memory fit while keeping internal column identity stable where practical. Preserve selection by repository identity and existing load/delete bindings.
- Update Installed guidance in `docs/usage.md` and the corresponding README description.

### Tests written before implementation

- Unit, new `tests/unit/test_model_context.py`: preset/token conversion and custom positive-integer validation, including whitespace, blank, text, zero, negative and non-preset initial values. Keep validation outside widget orchestration.
- Integration, `test_models_integration.py`: preset changes apply immediately; invalid custom input preserves the previous estimate; Enter applies valid custom input; neither path changes `config.max_ctx` or starts a load. Check cursor retention and details clearing through existing phase 1 cases.
- Integration, same file: at 140×40 sidebar precedes the table and details are below it; at 80×24 essentials remain reachable with at least four table rows; resizing preserves focus/selection. Assert geometry and behavior, not theme colors or exact whitespace.

### Success criteria

Automated:

```sh
uv run pytest tests/unit/test_model_context.py tests/unit/test_table.py tests/integration/test_models_integration.py tests/integration/test_delete_integration.py tests/integration/test_swap_integration.py tests/integration/test_boot_integration.py
```

Manual: browse by keyboard; inspect long model names and missing facts; open/close full details; switch preset/custom context; resize between wide and narrow terminals. Verify the table dominates the right column and details remain legible without crowding it.

### What we're NOT doing

No new table sorting/filtering, inline expanded rows, changes to load/delete policy, persisted UI preferences, server context configuration, or Discover-control redesign.

## Phase 3 — Embed the existing Discover flow

### Overview

Make Models discovery replace the right-hand content. Reuse the same flow in existing modal entry points without changing search semantics or download behavior.

### Changes

- Extract discovery composition, state and workers from `search_screen.py` into `discover_pane.py`. Keep `SearchScreen` as a thin modal host. Replace `ResultsTable`'s screen cast with a widget message handled by its owning discovery pane.
- Add a small host contract for return/close and completion. Scope queries to each pane so a setup/comparison dialog and the mounted Models workspace cannot address each other's widgets. Preserve pinned revisions, generation checks, operation ownership, and cancellation acknowledgement.
- In `models_pane.py`, switch the right side between Installed and Discover, preserving the installed table/cursor. Route `table.py`'s `/` action and the sidebar button to this entry point. Keep `setup_screen.py` and `compare/workflow.py` using the modal wrapper.
- Embedded Discover keeps its search input, publisher filter, task/scope controls, context input, suggestions, refresh/load-more actions, result table, details and progress. Omit only duplicate hardware/title chrome already supplied by the host. Wrap existing control rows when space requires it; do not hide or redesign filters.
- Returning from an idle discovery view invalidates outstanding requests before removing it, so late callbacks cannot steal focus. Reopening starts a fresh discovery session, matching the current modal lifecycle. Keep the Installed context value independent.
- During downloads, Return/Escape retain the current cancellation-and-acknowledgement behavior. Switching app tabs must not unmount the discovery worker or release its lock. Success/acknowledged cancellation rescans Installed and returns there; errors stay in Discover for retry. Teardown must release only an operation owned by this discovery instance.
- Adapt search integration helpers to the pane owner. Retain the existing cancellation, error, pinned-revision, stale-result and chat-interaction assertions. Update `docs/usage.md` navigation instructions.

### Tests written before implementation

- Integration, `test_models_integration.py`: button and `/` open inline Discover without a modal; Return restores the previous installed cursor/focus; closing while metadata is pending rejects late callbacks.
- Integration, `test_search_integration.py`: migrate existing flow tests to the embedded host, preserving success, errors, cancellation acknowledgement, revision identity and stale-result coverage. Add tab-switch-during-download coverage and a narrow embedded-layout case.
- Integration, `test_setup_integration.py` and `test_compare_integration.py`: existing pinned-candidate dialogs still open, inspect/download the exact revision and return to their original caller. Keep one modal Escape/cancellation ownership case; avoid duplicating the entire search suite for both hosts.

### Success criteria

Automated:

```sh
uv run pytest tests/integration/test_models_integration.py tests/integration/test_search_integration.py tests/integration/test_setup_integration.py tests/integration/test_compare_integration.py tests/integration/test_chat_cancellation.py
uv run ruff check .
uv run ruff format --check src tests
uv run pyrefly check --min-severity warn
uv run pytest -q
```

Manual: open Discover from Models, search/filter/inspect, return, and confirm the Installed selection survives. Exercise failed download and acknowledged cancellation using controlled fixtures. Confirm setup/comparison discovery still returns to its caller. Check both hosts at 80×24 and 140×40. Automated tests must not require live Hub access or large weight downloads.

### What we're NOT doing

No discovery UX rethink, recommendation changes, automatic searches/downloads, new filters, shared/persisted discovery preferences, or replacement of setup/comparison navigation.

## Delivery and rollback

Use three reviewable commits/PRs in phase order: assessment correctness; Installed layout; discovery embedding. Each phase must pass its focused checks before the next begins. Phase 3 receives the full regression/lint/type checks above. Roll back in reverse order if needed; no configuration, session, cache-schema, or model-data migration is introduced. Do not reset or overwrite the pre-existing working-tree changes.

Phases 1, 2, and 3 are implemented in the working tree. Phase 3's focused integration, lint, formatting, type, and full regression checks pass.
