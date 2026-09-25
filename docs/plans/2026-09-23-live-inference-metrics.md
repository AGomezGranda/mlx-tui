# Live Inference Metrics Dashboard

**Date:** 2026-09-23
**Work Item:** n/a
**Status:** In Progress

## Overview
Replace the two small sparklines with a readable live dashboard answering “what happens to this Mac while I use MLX?” Correlate CPU and memory graphs with this app's requests and operations, retaining recent request timings as supporting evidence.

## Current State
- `src/mlx_tui/metrics_pane.py:31` allocates three terminal rows to each sparkline and sixteen to the request table. `refresh_metrics()` rebuilds the table on every refresh.
- `src/mlx_tui/history/sparkline.py:31` rescales each visible series to its observed minimum/maximum. The throughput series is indexed by successful completed turns, not elapsed time.
- `src/mlx_tui/app/app.py:132` owns a 256-entry `memory_store`; `on_mount()` at line 207 schedules `_poll()` every two seconds.
- `src/mlx_tui/app/polling.py:77` skips polling during comparisons/restarts. Memory sampling occurs after HTTP probes and is appended at line 150; live telemetry currently depends on that polling path.
- `src/mlx_tui/process.py` provides `ProcessIdentity`, listener-verified `find_server_process()`, and `memory_snapshot()`. Preserve its ownership and attribution rules.
- `src/mlx_tui/operations.py:11` already defines idle, chatting, comparing, loading, restarting, installing, downloading, and deleting operations. These are app operations, not proof of server-wide idleness.
- `src/mlx_tui/chat_ui/turns.py:687` receives structured answer/reasoning progress. No reliable engine prefill/decode telemetry is exposed to Metrics.
- `src/mlx_tui/comparison/runner.py:144` separately samples process RSS every 50 ms as comparison evidence. The live dashboard must not replace or modify that evidence pipeline.
- `tests/integration/test_metrics_integration.py` covers the old chart placeholders and request accounting. `tests/unit/test_sparkline.py` covers the old renderers; `tests/unit/test_process.py` covers listener identity and memory observations.
- Existing dependencies include psutil, Rich, and Textual; no plotting dependency is needed for the proposed terminal graphs.
- Local GPU feasibility check: `/usr/bin/powermetrics --samplers gpu_power -n 1 -i 100` exits with “powermetrics must be invoked as the superuser.” No elevated collection was attempted. This rules out that collector as a transparent default, not every possible GPU source.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Inference context first | Generic system monitor; MLX activity dashboard | Keep the existing tabs/status bar. Metrics connects local resource use with Chat, Models, and Compare instead of duplicating a process explorer. |
| Two-minute rolling timeline at one-second cadence | Per-turn points; live samples | A shared elapsed-time axis exposes baseline, workload onset, and recovery. Collection continues on other tabs. |
| CPU and memory are the initial measured signals | Synthetic stress score; CPU/RAM; privileged GPU collector | Reuse installed psutil. Do not present CPU as total inference utilization or RSS as GPU/model allocation. GPU collection remains a documented follow-up decision. |
| Stable scales and labelled values | Autoscaled sparklines; capacity-scaled charts | CPU uses 0–100% of the whole Mac. Memory uses 0–physical capacity GiB. Tiny changes cannot appear as full-scale spikes. |
| App operation ribbon | Inferred thinking/prefill/decode phases; existing operation state | Label active requests “Generating,” not “Thinking,” unless future work records explicit reasoning phases. “Idle” is explained as “No request from this app.” |
| Keep request history below graphs | Remove history; history-first layout | First-output latency, answer latency, total duration, and client request tok/s are relevant outcomes of the resource activity. Preserve estimates and unsuccessful outcomes. |
| Stack full-width charts | Side-by-side tiles; stacked charts | Shared horizontal alignment makes correlation clear and remains useful at normal terminal widths. Compact heights and scrolling support short terminals. |
| One lightweight sampler and small renderer | New monitoring service/framework; app-owned sampling | Follow the existing app ownership and Rich/Textual rendering patterns. No persistence, plugin architecture, or new dependencies. |

### Intended layout and behavior

1. **Context strip:** “Live · last 2 min · sampled 1s,” current app operation, and selected request target. Show the target as selected, never as proven resident. Endpoint reachability and runtime controls remain in the existing global status bar.
2. **CPU panel:** full-width filled terminal graph, six plotting rows on a normal-height terminal, fixed 0/50/100% ticks; current and observed window-peak CPU values. Label the scope “This Mac.”
3. **Memory panel:** aligned six-row graph, system usage as a filled area and server RSS as a contrasting trace; current available/total GiB, server RSS GiB, and swap-used GiB. Define system usage as total minus available. Do not add RSS to system usage or call either metric macOS memory pressure.
4. **App activity ribbon:** one cell-aligned time band directly below the charts, with textual state legend. Generating/loading/comparing remain distinguishable from idle without relying only on color. Other operations use their existing names. This is sampled activity, so sub-second operations may not appear.
5. **Recent requests:** existing last-64-turn table with clearer headers and units: Time, Model, First output s, Answer s, Total s, Request tok/s, Context, Prompt, Output. Preserve column keys/order and outcome suffixes. Keep selection and scroll position stable while telemetry refreshes.

Use theme surfaces and foreground colors, muted grid lines, cyan CPU and violet memory accents, and a separate high-contrast RSS trace. No flashing, animated interpolation, or decorative gauges. Charts shrink to three plotting rows on short terminals; at 80×24 the pane scrolls without losing labels or access to history. Verify at 120×40 and 80×24. Unknowns show `—`, not zero; stale values receive an explicit label and chart gaps.

All proposed new symbols and files below are implementation specifications, not claims that they already exist. Verify their final Textual rendering integration during implementation against the installed version.

## Implementation Phases

### Phase 1: Decouple live sampling from endpoint polling
Collect bounded, timestamped CPU/memory observations independently of network health, preserving the existing UI during this phase.

**Changes:**
- `src/mlx_tui/history/store.py` — add frozen `ResourceSample` with `ts: float` (monotonic seconds), `operation: OperationKind`, `process_identity: ProcessIdentity | None`, and nullable `cpu_percent`, `rss_gib`, `avail_gib`, `total_gib`, `swap_gib` floats. Import annotation-only types under `TYPE_CHECKING` to avoid circular runtime imports. Keep `MemoryRecord` temporarily for the old pane.
- `src/mlx_tui/process.py` — add `sample_resources(identity: ProcessIdentity | None, operation: OperationKind) -> ResourceSample`. Read nonblocking system CPU, virtual memory, and swap; independently tolerate unavailable readings. For RSS, verify PID creation time against the supplied identity before reading. Never discover a listener or inspect a remote process in this function.
- `src/mlx_tui/app/app.py` — own `resource_store: deque[ResourceSample]` bounded to 121 observations, a single sampling task, and a stop event. Start the sampler at mount; prime psutil CPU measurement and keep first CPU observation unknown. Use a single long-lived worker thread so nonblocking CPU deltas use the same thread. At unmount, signal and await the sampler before closing the app; no worker may update unmounted widgets.
- `src/mlx_tui/app/polling.py` — add the sampling loop and main-thread append helper following the module's app-parameterized convention. Capture operation and current trusted process identity on the app thread; sample cheaply in the worker; append on the app thread. Use the existing owned-child identity when appropriate and the latest listener-verified attached identity otherwise. Restarting, missing/exited child, remote endpoint, or identity mismatch means unknown RSS. Do not alter health polling or comparison evidence collection. Prevent overlapping samples; failures create unknown readings and use bounded diagnostics. During this phase leave old memory collection intact.
- `tests/unit/test_process.py` — extend existing faked psutil seams to check values/units, separate missing readings, PID reuse, denied RSS, and no process inspection when identity is absent.
- `tests/integration/test_metrics_integration.py` — check sampling continues with Metrics hidden and while operation is COMPARING/RESTARTING, does not make additional HTTP probes, and stops cleanly at teardown. Use mocked readings and a controllable tick rather than real hardware thresholds.

**Success Criteria:**

#### Automated Verification:
- [x] Resource readings and identity safeguards pass: `rtk proxy uv run pytest tests/unit/test_process.py tests/integration/test_metrics_integration.py`
- [x] Type checks pass: `rtk proxy uv run pyrefly check`
- [x] Lint passes: `rtk proxy uv run ruff check .`

#### Manual Verification:
- [ ] Collect baseline, run a chat on another tab, and confirm the buffer includes both idle and generating samples.
- [ ] Sampling remains responsive during endpoint timeouts and server restarts; missing RSS never appears as zero.
- [ ] Quit during sampling without a hanging thread or callbacks after shutdown.

### Phase 2: Replace sparklines with aligned resource charts
Make live telemetry the visual focus while retaining request history and the rest of the app's navigation.

**Changes:**
- `src/mlx_tui/history/sparkline.py` — replace the unused-after-migration per-turn renderers with a small pure time-chart renderer, then rename the module to `src/mlx_tui/history/charts.py`. Proposed signature: `render_resource_chart(samples: list[ResourceSample], *, metric: Literal["cpu", "memory"], now: float, width: int, height_rows: int) -> Text`. Reuse the Braille packing idea where useful, but draw filled CPU/system-memory areas with explicit stable scales and a contrasting RSS trace. Do not retain the old min/max normalization or context shading.
- `src/mlx_tui/history/charts.py` — use one shared two-minute binning helper for both plots and `render_activity_ribbon(samples: list[ResourceSample], *, now: float, width: int) -> Text`. Position samples by elapsed time, not array index; empty bins remain blank. Use a common timestamp/plot width for all three renderables, with ticks at −120s, −60s, and now. For several samples in a display bin retain the maximum metric value; the ribbon marks active operation over idle and latest non-idle when several occur. Label chart peaks as observed samples. Never interpolate across unavailable readings or server identity changes.
- `src/mlx_tui/metrics_pane.py` — replace both old Static widgets with context, CPU, memory, activity, and request-history sections following the layout above. Supply chart width from the available content area after label gutters. Re-render on resize and new samples; use existing compact-height convention. Show stale after three seconds without a fresh reading; show unknown when the latest reading is missing rather than displaying an older reading as current.
- `src/mlx_tui/metrics_pane.py` — keep `#metrics-table` and its column keys/order. Cache the last displayed tuple of immutable `TurnRecord` values; only rebuild when records change. Preserve cursor/scroll when rebuilding where the selected record remains present. Refresh graphs only when Metrics is visible, and render accumulated history immediately on activation.
- `src/mlx_tui/app/polling.py`, `src/mlx_tui/app/app.py`, `src/mlx_tui/history/store.py` — remove the superseded memory-only append/store/record after migrating all callers. Keep status-bar memory reading and comparison sampling intact. Re-run reference search before removal.
- `tests/unit/test_sparkline.py` — replace obsolete sparkline tests and rename to `tests/unit/test_charts.py`. Cover empty, all-zero, missing and stale samples, fixed CPU/capacity scale, time gaps, narrow widths, bounds, PID discontinuity, and consistent ribbon alignment using deterministic samples.
- `tests/integration/test_metrics_integration.py` — update chart selectors/assertions; retain token-accounting coverage and reword the misleading unsuccessful-outcome test name. Assert sampling-only updates do not reset table selection or scroll.

**Success Criteria:**

#### Automated Verification:
- [x] Rendering and metrics integration tests pass (new test filename created in this phase): `rtk proxy uv run pytest tests/unit/test_charts.py tests/integration/test_metrics_integration.py`
- [x] Formatting passes: `rtk proxy uv run ruff format --check src tests`
- [x] Lint and type checks pass: `rtk proxy uv run ruff check .` and `rtk proxy uv run pyrefly check`

Deviation note: charts use block fill (█) with a ● RSS overlay instead of Braille packing; stable 0–100% CPU and 0–capacity GiB scales, time-binned gaps, and shared width/ticks are retained. `MemoryRecord`/`memory_store`/`sparkline.py` removed; status-bar `memory_snapshot` and comparison evidence untouched.

#### Manual Verification:
- [ ] At 120×40 and 80×24, charts, scales, legends, context, and history remain readable and keyboard-accessible without overlapping the status bar or Activity drawer.
- [ ] Idle → chat → idle produces aligned resource and app-activity history. A low CPU reading is never presented as proof of low GPU load.
- [ ] Constant memory is visually stable; small changes do not expand to full-scale spikes. RSS and system usage are distinguishable and not stacked additively.
- [ ] Resize, switch tabs, and inspect older table rows while sampling continues without focus theft or selection jumps.
- [ ] Cancelled/failed requests retain their table outcomes; cancellation does not claim the server has stopped.

### Phase 3: Verify app semantics and document the dashboard
Complete real-use checks and make resource scope and GPU limitations explicit.

**Changes:**
- `tests/integration/test_metrics_integration.py` — add focused regressions for initial empty history with live telemetry, known CPU with unknown server RSS, identity loss/replacement, and sampled operation transitions. Confirm selecting a different model does not relabel earlier process samples or imply residency.
- `README.md` — update the Metrics bullet to describe live local resource/activity graphs and recent request results.
- `docs/usage.md` — replace the sparkline description with the two-minute window, local scope, operation legend, units, sampling limits, and missing/stale behavior. Explain that Request tok/s remains a full-request average, CPU excludes GPU utilization, and server RSS is process-wide rather than model memory.
- `ARCHITECTURE.md` — update collection ownership, independent one-second sampling, bounded resource buffer, pure chart rendering, and module map. Preserve the distinct comparison evidence contract and the polling suppression rules.
- This plan — record GPU feasibility as a resolved default limitation: the tested native `powermetrics` route needs elevated privilege; no collector ships in these phases. Before adding GPU later, verify an actual supported source, units, machine scope, permissions, overhead, and lifecycle, then specify that collector in a separate plan. Do not add a fake GPU gauge or speculative provider interface.

**Success Criteria:**

#### Automated Verification:
- [x] All tests pass: `rtk proxy uv run pytest`
- [x] Lint passes: `rtk proxy uv run ruff check .`
- [x] Formatting passes: `rtk proxy uv run ruff format --check src tests`
- [x] Types pass: `rtk proxy uv run pyrefly check`

Resolution note: GPU feasibility recorded as a resolved default limitation —
the tested native `powermetrics --samplers gpu_power` route exits unless run
as superuser, so no GPU collector ships in these phases; CPU is labelled
This-Mac-only and docs state it excludes GPU load. A future collector needs
its own plan specifying source, units, scope, permissions, overhead, and
lifecycle. No fake gauge or speculative provider interface was added.

#### Manual Verification:
- [ ] With a real local MLX server, observe baseline, long generation, cancellation, and recovery. Capture both terminal sizes for visual review; stub tests alone cannot establish visual quality or real workload behavior.
- [ ] Run Compare and confirm live local resources continue while comparison evidence/results remain unchanged.
- [ ] Restart or stop the server and confirm process attribution clears and recovers correctly; the dashboard never attributes the TUI's own RSS to MLX.
- [ ] Existing light/dark themes retain readable labels and traces; state remains understandable without color.
- [ ] Observe idle overhead and interaction responsiveness; avoid connection scans in the one-second sampler and repainting hidden charts.

## Out of Scope
- GPU/power/temperature collection requiring privileged tooling, private APIs, or a new dependency in this iteration; no claim that CPU+RAM fully measures inference stress.
- Engine prefill/decode rates, live token/s estimates, exact reasoning-phase instrumentation, or a composite stress score.
- Per-core CPU panels, process lists, network/disk dashboards, alerts, persistent metrics, export, configurable time ranges, and dashboard customization.
- Replacing Compare's measured evidence or attributing machine activity/RSS exclusively to one model.
- Changes to inference requests, runtime ownership, status-bar controls, chat persistence, or model-selection semantics.

## Risks & Mitigations
- MLX may be GPU-heavy while CPU stays low → label CPU accurately and document unavailable GPU coverage; keep the GPU collector decision explicit.
- Background apps and other server clients affect metrics → label machine-wide versus server-process scope and describe idle as this app's activity only.
- Health probes pause during comparisons and restarts → independent sampler uses known identity with per-sample creation-time validation; unknown process readings remain gaps.
- Nonblocking psutil CPU percentages have a baseline and thread-local state → prime once and sample from one long-lived worker thread; never publish the priming zero as evidence.
- Wall-clock jumps, delayed ticks, and app suspension distort index-based charts → timestamp with a monotonic clock, bin by elapsed time, leave gaps, and show staleness.
- RSS can be small relative to total capacity → retain precise current values alongside the honest capacity scale rather than amplifying the trace.
- More UI updates can disturb interaction → bounded buffer, one-second cadence, visible-only chart paints, and history updates only when records change.
- Sampled activity misses short requests → describe the ribbon as sampled; the exact completed request still appears in the history table.

### Research validation

- Existing targeted baseline: 39 tests passed across sparkline, process, and Metrics integration tests.
- Repository lint, format (120 files), and type checks passed during planning.
- The new `test_charts.py` command replaces a verified existing test target; run it after the specified rename.
- The exact Phase 1 test command also passed: 24 tests in 3.48 seconds.
- Full-suite command was verified to collect and run, but was interrupted after more than three minutes to bound planning validation; no complete full-suite result is claimed. Interruption also produced pytest teardown diagnostics. Run the full suite to completion during implementation.
