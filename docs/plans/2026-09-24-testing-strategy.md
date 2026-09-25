---
date: 2026-09-24
title: Faster and more deterministic test feedback
status: complete
---

# Faster and more deterministic test feedback

Implement the four agreed phases from the [testing strategy review](../reviews/2026-09-23-testing-strategy.md). Reduce repeated application setup and artificial waits while preserving persistence, process ownership, cancellation, keyboard, layout and real HTTP coverage. This is a test and CI change; no production behavior or dependencies need to change.

## Current State

- The review measured 728 cases in 263.96 seconds, with mounted component cases accounting for 97.1% of case time. Its temporary HTTP polling experiment saved 60.82 seconds. Those are historical local measurements, not acceptance thresholds or promised CI gains.
- `tests/conftest.py:237` starts every function-scoped HTTP server with the default `serve_forever` polling interval. The fixture already isolates requests, modes and model identity correctly.
- `tests/unit/test_params.py:13` begins seven full-app tests. `src/mlx_tui/app/app.py:215` starts polling, sampling and cache discovery on mount, even for widget-local value checks. ParamsPane itself owns normalization and submission handling in `src/mlx_tui/params.py`.
- `tests/unit/test_chat.py:237` and `:247` already exercise no-space and multiline SSE through `stream_turn`, asserting `skipped_frames == 0`. The matching component cases at `tests/integration/test_chat_integration.py:30` and `:59` duplicate those framing obligations.
- `tests/integration/test_setup_integration.py:122` preserves a full pinned revision but only replaces snapshot/download collaborators. `src/mlx_tui/search_screen.py:110` forwards metadata downloads to the Hub; `src/mlx_tui/discover_pane.py:695` takes that path for a full revision.
- Five health-policy cases starting at `tests/unit/test_serverctl.py:597` use real retry sleeps. `src/mlx_tui/serverctl.py:425` computes the deadline and `:490` sleeps between attempts.
- `tests/integration/test_search_integration.py:455` holds a warning visible with a 350 ms sleep. Success journeys at `:230` and `:504` repeat search/download setup.
- `tests/integration/test_boot_integration.py:288` waits three seconds for a child to die. `tests/unit/test_boot.py:16` already provides `FakeProcess`; `src/mlx_tui/boot.py:128` exposes `spawn_with_grace` as the relevant seam.
- `.github/workflows/ci.yml:44` runs the serial suite on Ubuntu and macOS without retained pytest timings. Packaging smoke checks already exist and remain separate.
- The working tree contains unrelated live-metrics edits. The implementation must preserve them, record its actual baseline tree, and avoid attributing their changes to this plan.

## Design Decisions

- Keep pytest, pytest-asyncio, existing helpers and per-test app/server isolation. Use local fakes and bounded events, not new fixture frameworks or production injection interfaces.
- Move all mounted parameter tests into `tests/integration/test_params_integration.py`, including the small widget-only app. This makes `uv run pytest tests/unit` exclude mounted components without introducing markers or renaming the entire test tree. Document that this fast selection still includes valuable local filesystem/process integration tests.
- Preserve assertions before deleting duplicate journeys. Do not infer coverage from test counts alone.
- Use `stub_harness` for the relocated full-app parameter checks, whose assertions do not concern cache/process discovery. Keep existing polling/telemetry fixtures for tests asserting those boundaries; no global suppression.
- Record a fresh serial baseline before phase 1, and a comparable final run after phase 4. Run focused checks between phases. Do not turn noisy wall-clock comparisons into hard pass/fail thresholds.

## [x] Phase 1 — Remove avoidable waits and network access

### Overview

Apply TS-01, TS-03 and TS-04, plus the low-disk synchronization fix. Keep the same test subjects and existing behavioral assertions.

### Tests first

- **Component:** extend `test_modal_discover_preserves_pinned_revision_through_download` to record and assert the metadata request `(repo_id, "config.json", revision)`, in addition to the existing snapshot/download revision and dismissal assertions. A local config fixture must be used before running this test; do not deliberately contact the Hub to obtain a red test.
- **Unit:** retain and strengthen the five existing `wait_healthy` cases listed below. Assert virtual elapsed time, retry counts and returned probe/None; use these assertions to verify the fake-clock conversion itself.
- **Component:** retain `test_low_disk_warns_then_proceeds` and require the warning while its fake download is held open, followed by successful completion after release.
- TS-01 is fixture configuration only: existing real-socket journeys and suite teardown are its runnable checks. Do not add a test that merely asserts the polling constant.

### Changes

1. In `tests/conftest.py::stub_server_factory.start`, pass `kwargs={"poll_interval": 0.01}` to the existing `threading.Thread`. Keep function scope, shutdown and server close unchanged.
2. In `tests/integration/test_setup_integration.py::test_modal_discover_preserves_pinned_revision_through_download`, add `tmp_path: Path`, write `{}` to `tmp_path / "config.json"`, and patch `mlx_tui.search_screen.hf_hub_download` with a recording fake returning that path as a string. Assert its arguments once inspection has completed. Retain the 40-character revision.
3. In `tests/unit/test_serverctl.py`, add a file-local fake clock with `monotonic() -> float` and `sleep(seconds: float) -> None`; sleep advances an elapsed value. An explicitly requested fixture replaces `serverctl.time` with this object only for:
   - `test_wait_healthy_wrong_model_then_right`: two generation calls, matching probe, virtual elapsed 1.0 seconds.
   - `test_wait_healthy_times_out_when_always_red`: None, virtual elapsed 1.0, ticks `[0, 1]`, two health/catalogue attempts.
   - `test_wait_healthy_wrong_endpoint_model_times_out`: None, two health/catalogue attempts, one generation attempt, virtual elapsed 1.0.
   - `test_wait_healthy_missing_response_identity_times_out`: the same deadline behavior with missing response identity.
   - `test_wait_healthy_probe_timeout_returns_none`: connection failures still return None after one virtual second and two health/catalogue attempts.
   Keep the real-clock cancellation and process tests unchanged. Never patch the shared `time.monotonic` function.
4. In `tests/integration/test_search_integration.py::test_low_disk_warns_then_proceeds`, replace `time.sleep(0.35)` with a `threading.Event` gate and a bounded `wait(timeout=5)` assertion. Wrap the download trigger and warning assertion in `try/finally`; always release the gate in `finally`, then await the existing success assertions. Remove the `time` import only if no remaining case uses it.

### Success criteria

**Automated:** before edits, record platform, working-tree status and a baseline run:

```sh
rtk git status --short
rtk proxy uv run python -c 'import platform, sys; print(platform.platform(), sys.version)'
rtk proxy uv run pytest --durations=20 --junitxml=/tmp/mlx-tui-testing-before.xml
```

After changes:

```sh
rtk proxy uv run pytest tests/unit/test_serverctl.py tests/integration/test_setup_integration.py tests/integration/test_search_integration.py --durations=20
```

All cases pass; the five fake-clock cases no longer spend seconds sleeping. Metadata arguments prove the previously missed seam is replaced. Events release even if assertions fail.

**Manual:** inspect the diff for unchanged fixture scope and process/cancellation clocks. No manual application launch is needed.

### What we're NOT doing

No shared server, shortened production deadlines, global offline mode, blanket sleep removal or new timing library.

### Phase 1 execution record

- Baseline commit: `12e597bedf13ded9022453e10df0031cc2ed5901`; platform: `macOS-27.0-arm64-arm-64bit-Mach-O`, Python 3.13.1. The baseline working tree already contained unrelated live-metrics changes in `ARCHITECTURE.md`, `README.md`, `docs/usage.md`, `src/mlx_tui/app/app.py`, `src/mlx_tui/app/polling.py`, `src/mlx_tui/history/store.py`, `src/mlx_tui/metrics_pane.py`, `src/mlx_tui/process.py`, `tests/integration/test_metrics_integration.py`, and `tests/unit/test_process.py`; it also deleted the sparkline files and added `src/mlx_tui/history/charts.py`, `tests/unit/test_charts.py`, the live-metrics plan and review files, and this plan and its review.
- Baseline: 728 passed, 2 existing fork warnings, 265.90 seconds. JUnit: `/tmp/mlx-tui-testing-before.xml`.
- Result: 90 focused tests passed in 46.18 seconds after the fixture, local metadata fake, per-test fake clock and event gate changes. Ruff check, format check and `git diff --check` passed. No production code or dependencies changed.

## [x] Phase 2 — Reduce repeated full-app testing

### Overview

Apply TS-02 by narrowing widget tests and merging genuinely duplicated component journeys. Keep UI routing and lifecycle assertions mounted.

### Tests first

- **Widget component:** preserve `test_params_pane_applies_partial_values` and `test_params_pane_normalizes_nonfinite_and_out_of_range_values` before changing their fixture. Their assertions pin preservation of unspecified values, finite defaults and the minimum token clamp.
- **Full-app component:** preserve all three `test_apply_config_params_*` cases, `test_params_submission_stays_with_params_pane`, and `test_native_params_collapse_retains_values`. They retain defaults/config bridging, Enter event ownership, no chat request, focus order, summary updates and value retention.
- **Unit:** run the existing no-space and multiline chat cases and verify both assert exact decoded content and zero skipped frames before removing mounted duplicates.
- **Component:** add the expanded-size assertions to the retained discovery success journey before removing its duplicate. Keep the modal pinned-revision scenario from phase 1 separately.

### Changes

1. Move `tests/unit/test_params.py` to `tests/integration/test_params_integration.py`. Add a module-local `ParamsTestApp(App[None])` whose `compose() -> ComposeResult` yields only `ParamsPane()`. Use its `run_test()` context for the two widget-local cases; query the mounted pane directly, without an HTTP fixture. The other five cases use the existing `stub_harness`, retaining their assertions.
2. Delete `test_chat_multiline_event_streams_without_malformed_notice` and `test_chat_no_space_data_stream_parses` from `tests/integration/test_chat_integration.py` after the lower-level checks pass. Retain `test_status_green_and_chat_stamp_over_stub_http` in `test_app_integration.py`, `test_truncated_stream_red_line_and_no_stale_pane`, and all keyboard, reasoning/tools, history, cancellation and scroll journeys. Remove `StubHandler`'s `multiline` and `nospace` branches only after a repository search confirms no remaining callers; keep the SSE builders used by unit tests.
3. In `tests/integration/test_search_integration.py::test_enter_downloads_hands_off_and_dismisses`, use the expanded snapshot from `test_size_and_download_share_revision_and_expanded_patterns`. Patch `repo_snapshot` after `open_search` returns and before submitting the query, so the helper's defaults do not overwrite it. Assert the expected size (4600 bytes, excluding README), resolved/download revision, repo identity, completion log, exactly one rescan and pane dismissal. Then delete the duplicate size/download journey. Do not change `open_search`'s signature.

### Success criteria

**Automated:**

```sh
rtk proxy uv run pytest tests/integration/test_params_integration.py tests/unit/test_chat.py tests/unit/test_search.py tests/integration/test_chat_integration.py tests/integration/test_app_integration.py tests/integration/test_search_integration.py --durations=20
rtk proxy rg -n 'multiline|nospace' tests
```

All retained behaviors pass. The two widget checks no longer request an app/server harness; only the three identified redundant component cases disappear. Search output is inspected before removing unused stub modes.

**Manual:** compare old/new assertions for the moved params and merged discovery tests. No manual UI check is needed because focus, submission and geometry still run through Pilot.

### Phase 2 execution record

- Result: moved all seven parameter cases to `tests/integration/test_params_integration.py`. The two value-only cases now mount `ParamsPane` in a module-local app; the other five retain their assertions under `stub_harness`, including submission ownership, focus order, summary updates and value retention.
- Removed the two mounted SSE framing journeys after confirming `tests/unit/test_chat.py` still asserts exact decoded content and `skipped_frames == 0` for no-space and multiline events. Removed the now-unused `StubHandler` modes; the unit SSE builders remain.
- Merged expanded-size coverage into `test_enter_downloads_hands_off_and_dismisses`: 4600 bytes, pinned `rev-a`, repo identity, success log, one rescan and modal dismissal. The 9999-byte README remains excluded.
- Focused baseline before edits: 84 passed in 61.08s. Phase 2 focused command: 134 passed in 85.54s. The selections differ, so these runtimes are not a before/after comparison.
- `ruff check`, `ruff format --check` on changed test files, `git diff --check`, and the final `rg -n 'multiline|nospace' tests` inspection passed. Remaining matches are the unit SSE builder and its unit test.

### What we're NOT doing

No production ParamsPane refactor, blanket conversion to mocks, removal of real HTTP error handling, or new taxonomy across every test directory.

## [x] Phase 3 — Make boot UI tests deterministic

### Overview

Replace artificial child lifetimes in UI-state scenarios with controlled outcomes. Keep real subprocess boundary tests and a mounted real-child cleanup journey.

### Tests first

- **Component:** strengthen `test_failed_boot_terminates_proc_and_restores_controls` to record the spawned child and assert it has exited after failure, before adding fallback cleanup in `finally`. This closes a gap in the retained real boundary check.
- Preserve the existing mid-boot death, instant-crash, duplicate-start and model-size timeout assertions while replacing their drivers. The mid-boot case must still execute real `execute_boot` error mapping.
- **Unit/OS integration:** retain `test_early_crash_cleans_owned_process`, `test_health_failure_cleans_owned_process`, `test_verified_health_does_not_clean_up`, and all existing real `serverctl` command, grace, process-group and identity tests. Do not duplicate their implementation coverage in new UI cases.

### Changes

Modify only `tests/integration/test_boot_integration.py`, reusing `FakeProcess` from `tests/unit/test_boot.py`:

1. `test_cold_start_reports_mid_boot_death`: fake `serverctl.spawn_with_grace` returns `(FakeProcess(None), True)`. Fake `wait_healthy` checks `is_running()` is initially true, sets `poll_result = 1`, checks it is false, and returns None. Replace `terminate_failed_process` with a recorder and assert cleanup receives that fake. Keep the real boot coordinator, error log and restored controls; remove the three-second child and its cleanup loop.
2. `test_cold_start_instant_crash_fails_fast`: patch `mlx_tui.models_pane.execute_boot` to raise `RuntimeError("[swap] start_cmd exited 3")`. Keep the log, IDLE and enabled-input assertions. Real instant-crash detection remains in serverctl and boot tests.
3. `test_cold_start_timeout_scales_with_model_size`: replace the real process created by `fake_spawn` with fake `spawn_with_grace` returning `(FakeProcess(0), False)`. Retain the captured health timeout and success assertion. Stub process cleanup whenever a fake process can reach that path.
4. `test_double_cold_start_is_guarded`: use a blocking fake `models_pane.execute_boot` with started/release events. Await started before the second key press, retain the busy/RESTARTING assertions, release in `finally`, and await IDLE. Bound the worker wait to five seconds; return a green `ServerProbe` after release.
5. `test_failed_boot_terminates_proc_and_restores_controls`: use the file's existing `recording_spawn` pattern, await operation completion and restored controls, then assert exactly one recorded child and `proc.poll() is not None`. In `finally`, terminate/wait only if it is still alive. Keep real production cleanup and the fake unhealthy result.
6. Retain real `test_cold_start_long_running_boot_keeps_ui_live`, restart/output and shell-mode journeys, and `test_verified_boot_ui_failure_does_not_terminate_server`.

### Success criteria

**Automated:**

```sh
rtk proxy uv run pytest tests/unit/test_boot.py tests/unit/test_serverctl.py tests/integration/test_boot_integration.py --durations=20
```

All cases pass. UI-state cases do not launch the replaced children or wait through real grace periods. Real cleanup is asserted before fallback cleanup; fake PIDs never reach OS termination.

**Manual:** inspect retained subprocess tests and event `finally` blocks. No model download or inference run is required.

### What we're NOT doing

No production boot/health changes, generic process simulator, shorter OS timeouts or removal of ownership/escalation tests.

### Phase 3 execution record

- Replaced the mid-boot, instant-crash, duplicate-start and model-size UI drivers with the planned fake outcomes and bounded event gate. The mid-boot test still runs real `execute_boot` error mapping; fake process cleanup is recorded rather than sent to the OS.
- Strengthened the retained real-child failure journey to assert the owned child exited before fallback cleanup.
- Focused command: 86 passed in 15.08s. Ruff check, format check and `git diff --check` passed. The implementation changed only `tests/integration/test_boot_integration.py`; this plan records the completed phase. No production behavior changed.

## [x] Phase 4 — Make improvements measurable

### Overview

Retain per-platform pytest timings in CI and document fast local feedback. Compare the final serial run with the phase-1 baseline, accounting for moved/deleted cases.

### Tests first

- Use pytest collection to verify relocated cases still exist exactly once and the fast directory contains no mounted component tests. These are collection checks, not a new test framework or meta-test suite.
- Preserve the unmounted shutdown error test unchanged while relocating it, so the fast command includes this coordination behavior.
- Existing full CI and packaging jobs remain the acceptance checks; no test of YAML literals is needed.

### Changes

1. Move `test_shutdown_continues_after_chat_cleanup_errors` from `tests/integration/test_app_integration.py` into new `tests/unit/test_app.py`, bringing only its required imports. Remove old imports only when unused elsewhere.
2. In `.github/workflows/ci.yml`, change the checks job's Test command to `uv run pytest -q --durations=20 --junitxml=test-results/pytest.xml`. Immediately follow it with `actions/upload-artifact@v4`, `if: always()`, artifact name `pytest-${{ matrix.os }}`, path `test-results/pytest.xml`, `retention-days: 14`, and `if-no-files-found: warn`. Keep both OSes, serial execution and packaging steps.
3. In `docs/development.md`, document `uv run pytest tests/unit` as fast UI-free feedback (including local filesystem/process boundaries), `uv run pytest tests/integration` as mounted UI coverage, and the full timing/JUnit command. Explain that small mounted widget tests belong with components and that neither command verifies actual MLX provider compatibility.
4. Append an implementation-results section to this plan when implementation is complete: actual tree/commit and platform, before/after totals and runtimes, moved cases, the three deleted duplicates, preserved coverage, warnings, and Ubuntu/macOS artifact results when available. Mark unavailable CI observations explicitly; never substitute local results for them.

### Success criteria

**Automated:**

```sh
rtk proxy uv run pytest tests/unit --collect-only -q
rtk proxy uv run pytest tests/integration/test_params_integration.py --collect-only -q
rtk proxy rg -n 'run_test|AppHarness|stub_harness' tests/unit
rtk proxy uv run pytest tests/unit --durations=20
rtk proxy uv run ruff check .
rtk proxy uv run ruff format --check src tests
rtk proxy uv run pyrefly check --min-severity warn
rtk proxy uv run pytest --durations=20 --junitxml=/tmp/mlx-tui-testing-after.xml
```

The `rg` check is expected to find no matches; inspect any match before claiming the selection excludes mounted components. All changed tests and quality checks pass. If unrelated pre-existing edits cause failures, record exact failures and reproduce against the baseline rather than expanding this task silently. A final case-count reduction of three is expected if no unrelated tests change; investigate other differences.

**Manual:** compare JUnit before/after totals and slow cases on the same platform. In CI, check that both matrix jobs show duration output and provide readable JUnit artifacts, including after a pytest failure. Packaging smoke jobs must still run as configured. No absolute runtime target is imposed; investigate any regression before calling the optimization successful.

### Phase 4 execution record

- Moved the shutdown cleanup test unchanged into `tests/unit/test_app.py`; collection finds it once, and `tests/unit` has no mounted-app harness references. All seven parameter cases remain in integration.
- Added `test-results/` creation before pytest so the JUnit path exists on fresh CI runners. Updated development docs with the fast/UI/full-suite commands and their coverage limits.
- Collection checks, focused app tests (28 passed), all 511 unit tests, Ruff check, Ruff format check, Pyrefly and `git diff --check` passed. Pyrefly exposed a missing `@override` on the earlier `ParamsTestApp.compose`; added it, then Pyrefly reported 0 diagnostics.
- Final serial suite: 725 passed, 2 existing fork warnings, 196.90 seconds. JUnit report: `/tmp/mlx-tui-testing-after.xml`.
- No GitHub Actions run was available to inspect; Ubuntu/macOS artifact and packaging results remain unavailable.

### What we're NOT doing

No custom benchmark dashboard, automatic performance thresholds, xdist dependency, marker system, fork-warning suppression or packaging rewrite.

## PR Strategy

Use four small PRs in phase order: (1) fixture/time/network reliability, (2) reduced component duplication, (3) deterministic boot scenarios, (4) CI timings and local feedback documentation. Each carries its listed focused checks; the first establishes the baseline and the last runs the final full comparison. Phase 2 depends on phase 1's discovery synchronization; phase 4 depends on phase 2's test relocation. Phase 3 is logically independent but stays in sequence for review and timing attribution.

Each PR changes tests/CI/docs only, is independently revertible, and requires no data migration. Revert an affected PR to restore its former test setup; never remove a production safeguard as a rollback. Preserve unrelated working-tree edits and rerun the relevant focused checks after resolving conflicts.

## Risks & Mitigations

- **Coverage accidentally lost during consolidation:** move assertions first, verify lower-level framing assertions, and review the old/new assertion mapping before deleting cases.
- **Fake-clock leakage into asyncio or process tests:** patch the module reference in an opt-in fixture used only by the five synchronous health tests.
- **Worker deadlocks after assertion failures:** bounded event waits with unconditional release; real-child fallback cleanup in `finally`.
- **Fake PID passed to real termination:** replace cleanup at the same scope as the fake process, including unexpected-failure paths.
- **Misleading benchmark conclusions:** record the actual modified tree, use identical serial commands/platforms, retain JUnit evidence, and distinguish observed savings from the review's historical experiment. Repeat only if noise or a regression makes the comparison inconclusive.
- **Fast selection drifts:** document the directory convention and verify collection after relocation. No new enforcement plugin is justified by the present inventory.

## Deferred Work and Validation Assumptions

- Trial two xdist workers only if the final serial runtime still warrants the dependency and both OSes can be benchmarked after isolation fixes.
- Qualify actual MLX responses with an optional pinned-runtime smoke check when provider versions change; model downloads/inference stay outside ordinary PR tests.
- The review's stale-callback mutation probes, historical flake-rate analysis and separate packaging timing remain follow-up investigations, not prerequisites for these measured reductions.
- Assume no production change is required: validate through the focused and full suites. If a new production defect appears, report its evidence and scope it separately instead of hiding it inside a test-speed refactor.

## Requirement Mapping

| Review item | Planned outcome |
| --- | --- |
| TS-01 | Phase 1: 10 ms HTTP fixture polling, same isolation |
| TS-03 | Phase 1: local pinned-revision metadata fake and assertions |
| TS-04 | Phase 1: five controlled-clock policy cases |
| TS-02 | Phase 2: smaller ParamsPane subject, framing deduplication, merged discovery journey |
| Low-disk observation race | Phase 1: bounded event and unconditional release |
| Boot UI process outcomes | Phase 3: controlled outcomes plus retained/asserted real cleanup |
| CI timings and fast selection | Phase 4: JUnit artifacts, duration output and UI-free local command |
| Real boundary, keyboard, resize and cancellation coverage | Preserved across all phases |
| Parallelism and provider qualification | Deferred with explicit triggers above |

## Implementation Results

- **Tree and platform:** final run used the working tree based on commit `12e597bedf13ded9022453e10df0031cc2ed5901`, on `macOS-27.0-arm64-arm-64bit-Mach-O`, Python 3.13.1. The tree also contained the unrelated live-metrics edits listed in the Phase 1 baseline record; no commit was created.
- **Comparable full-suite result:** baseline 728 passed, 0 failures, 265.729 seconds; final 725 passed, 0 failures, 196.860 seconds. The three-case reduction matches the plan. Observed local runtime was 68.869 seconds (25.9%) lower; these are measurements, not a CI target. Baseline and final JUnit reports are `/tmp/mlx-tui-testing-before.xml` and `/tmp/mlx-tui-testing-after.xml`.
- **Moved and consolidated cases:** seven parameter tests moved from unit to integration, and the unchanged shutdown cleanup case moved from integration to unit. Removed the two mounted SSE framing duplicates and the duplicate size/download journey; exact decoded SSE content and zero skipped frames remain covered at unit level, and the expanded-size discovery success journey retains the 4600-byte result, revision/repo identity, completion log, single rescan and dismissal assertions.
- **Preserved boundaries:** real HTTP/status/chat coverage, keyboard and layout journeys, process ownership and real-child cleanup, cancellation, and the separate full pinned-revision setup journey remain covered.
- **Warnings and checks:** both runs retained the same two `os.fork()` deprecation warnings. Final checks passed: 511 unit tests, focused app tests, Ruff, formatting, Pyrefly (0 diagnostics), collection checks and diff whitespace validation.
- **CI observations:** Ubuntu/macOS duration output, JUnit artifacts and packaging results are unavailable because no GitHub Actions run was available for this implementation.
