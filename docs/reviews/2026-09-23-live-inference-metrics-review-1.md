# Plan Review: Live Inference Metrics Dashboard

**Date:** 2026-09-23
**Target:** docs/plans/2026-09-23-live-inference-metrics.md
**Review:** 1
**Verdict:** COMMENT

## Assessment

The plan is grounded in the current code and keeps collection, rendering, request history, and comparison evidence appropriately separate. No critical or major design problems were found; two small additions would make the freshness and CPU-sampling contracts easier to implement and verify.

## Review Scope and Verification

Applied Correctness, Architecture & Patterns, Test Coverage, Plan Mechanics, and Performance, with an independent review of lifecycle and process attribution. Performance is relevant to the continuous worker and UI refresh path. A separate Security lens is unnecessary because no new external input or authority is introduced; process attribution was checked under Correctness. Data/Migrations and API/Compatibility were skipped because there is no persisted schema, public API, or dependency change.

No previous review of this plan exists. Referenced existing paths and symbols were checked, including the current polling suppression, memory buffer, operation states, turn progress, comparison sampler, and chart/table callers. The proposed chart module and its test are explicitly new/renamed files.

- Targeted baseline: **39 passed in 3.35s** across process, Metrics integration, and sparkline tests.
- Exact Phase 1 command: **24 passed in 3.28s**.
- `rtk proxy uv run ruff check .`: passed.
- `rtk proxy uv run ruff format --check src tests`: passed; 120 files formatted.
- `rtk proxy uv run pyrefly check`: passed; 0 errors, 311 suppressed.
- `rtk proxy uv run pytest`: **718 passed, 2 warnings in 255.89s**. The warnings are existing `os.fork()` deprecation warnings in the session-lock tests when running in a multithreaded process.
- Repeated the non-elevated `powermetrics` command: exit 1 with the documented superuser requirement. No elevated collection attempted.
- Phase 2's `test_charts.py` command cannot run before the specified rename; its existing predecessor passes. Real MLX workloads and terminal screenshots remain implementation acceptance checks, not claims established by this review.

## Findings

### Critical

None.

### Major

None.

### Minor

- **F1 — [Correctness / Test Coverage] Phase 2: give stale-state rendering a clock-driven trigger.** Plan lines 78–79 require staleness after three seconds but list new samples, resize, and activation as refresh triggers. If sampling stalls while Metrics stays visible, none of those events necessarily fires, so the displayed value can keep looking current. The current pane has no independent freshness timer (`src/mlx_tui/metrics_pane.py:51–75`), and health polling cannot guarantee it during comparison/restart (`src/mlx_tui/app/polling.py:77–84`). Specify a visible-only clock tick, or equivalent independent refresh, and a deterministic integration test that advances time without adding a sample or changing tabs. Keep the existing history-cache rule so this tick does not rebuild the table.

- **F2 — [Test Coverage] Phase 1: explicitly test CPU priming and thread affinity.** The design correctly requires one long-lived thread and an unknown initial CPU reading (plan lines 55 and 134), but the enumerated process and integration cases at lines 57–58 do not directly verify either. Mocked complete readings could let a regression publish the priming zero or move measurements between threads while all listed cases pass. Add one focused sampler test that records the thread identity at the mocked CPU boundary across priming and subsequent ticks, checks that it is the same worker thread, and checks that the initial published CPU value is unknown before later values become available. This belongs at the loop boundary, not solely in the stateless process-helper tests.

### Suggestions

None.

## Strengths

- Reuses existing psutil, Rich, Textual, operation state, and app ownership without adding a monitoring framework or dependency.
- Explicitly separates machine-wide CPU/memory, process RSS, selected request target, and model residency; GPU limitations are accurately scoped.
- Keeps unknown readings distinct from zero, uses monotonic timestamps and fixed scales, and preserves PID-generation boundaries.
- Protects comparison evidence and request accounting while addressing table-selection disruption.
- Phases preserve a working old pane until migration, specify automated and manual gates separately, and require real workload and terminal-size validation.

## Recommended Changes

1. **F1:** Add the independent visible-only freshness trigger and its no-new-sample integration check to Phase 2.
2. **F2:** Add the focused CPU priming/thread-affinity regression to Phase 1.

The plan and its Draft status are unchanged. These are non-blocking clarifications; the user can choose which to incorporate before implementation.
