# Testing strategy and runtime audit — 23 September 2026

## Service shape

`mlx-tui` is a local Textual application for an operator, not a microservice. Its UI coordinates model selection, chat, downloads, process ownership and local session/comparison persistence; UI-free modules handle parsing, policy, accounting and storage. Boundaries are MLX HTTP/SSE, Hugging Face metadata/downloads/cache, the filesystem, subprocesses and psutil; there is no message broker, outbox or team-owned public API.

The costly mistakes here are lost conversations, incorrect process termination and deleting the wrong cached model. Those deserve real boundary tests; model inference and a five-level microservice pyramid on every commit do not. Ownership and data-loss guards are documented in [ARCHITECTURE.md](/Users/alvarogomez/code/python/mlx-tui/ARCHITECTURE.md).

## Current distribution

Measured the current working tree, including pre-existing uncommitted metrics changes, on macOS 27 / arm64, Python 3.13.1. Baseline: **728 passed, 2 warnings, 263.96 seconds (4m 24s)**. Command: `uv run pytest --durations=40 --junitxml=/tmp/mlx-tui-audit-baseline.xml`. No coverage instrumentation, parallel workers or real model inference.

Counts below are collected cases, including parameter expansion. Runtime is summed JUnit setup + call + teardown, rounded; the remaining 0.32 seconds is runner overhead. Classification follows exercised behavior, not directory names. Temporary files used only to construct input do not change a unit test into an integration test.

| Level | Cases | Runtime | What it actually exercises / substitutes |
|---|---:|---:|---|
| Unit | 367 | 5.45 s | Pure rules, codecs, formatting and coordination; MockTransport/fake Hub/process collaborators where appropriate |
| Integration | 144 | 2.30 s | Real local file round-trips, permissions/locks, package resources, subprocess and listener boundaries; remote systems mostly faked |
| Component | 217 | 255.89 s | Mounted Textual application, local HTTP stub, real UI messages and often persistence; Hub/inference/process behavior variously replaced |
| Provider-verified contract | 0 | — | Synthetic HTTP/SSE expectations exist, but do not verify the real provider |
| Full inference E2E | 0 | — | No real model startup/generation journey in pytest |

The count distribution is reasonable; the **cost distribution is the problem: component tests consume 97.1%** of measured case time. Seven component cases are inside `tests/unit/test_params.py`; one unmounted coordination test lives inside `tests/integration/test_app_integration.py`. Real filesystem/process integration is cheap here.

Largest suites, including setup/teardown: discovery **42.25 s**, app **37.32 s**, chat **31.68 s**, cancellation **24.78 s**, boot **23.27 s**. The two compare keyboard journeys cost **10.48 s** together, but exercise useful focus/layout behavior and should not simply be deleted.

All pytest cases run serially in both Ubuntu and macOS CI. [CI](/Users/alvarogomez/code/python/mlx-tui/.github/workflows/ci.yml:44) also runs [artifact_smoke.py](/Users/alvarogomez/code/python/mlx-tui/tests/artifact_smoke.py:227), which builds the wheel/sdist and installs both into fresh environments; that packaging check is outside these timings and was inspected, not executed. Historical CI runtime/failure rates are unknown. Both baseline warnings concern `fork()` in a multithreaded process during session-lock tests; these are not test failures.

## Findings

All findings below are **P2, high confidence**: material runtime/reliability improvements, not evidence of a production-critical failure. Verification tier is stated per finding.

### TS-01 — Every HTTP fixture pays avoidable shutdown polling

**Evidence:** [tests/conftest.py:237](/Users/alvarogomez/code/python/mlx-tui/tests/conftest.py:237) starts `threading.Thread(target=server.serve_forever, daemon=True)`; line 244 calls `server.shutdown()`. The installed Python implementation defaults to `serve_forever(poll_interval=0.5)` and blocks shutdown until that polling loop exits.

**Obligation / consequence:** shared test infrastructure should not impose production-scale waits unrelated to assertions. Repeating this across mounted cases adds shutdown latency even to simple widget checks. **Counterevidence:** function-scoped servers correctly isolate mutable mode, model identity and captured requests; retain that isolation. Long-running response handlers can also delay shutdown, so 500 ms is a polling bound, not a fixed charge for every test.

**Smallest correction:** supply `kwargs={"poll_interval": 0.01}` to the existing thread. Keep the real socket tests and per-test server. **Effort:** minutes. **Verification tier:** top-line; full fixture reviewed, stdlib implementation inspected, full-suite temporary override measured below.

**Measured experiment:** temporarily overriding only `StubServer.serve_forever` to use 10 ms polling reduced the full run from **263.96 s to 203.14 s: 60.82 s saved, 23.0% faster**. All **728 identical cases passed**, with the same two warnings; no assertions, fixtures or production files were edited. The 217 recorded shutdown calls totaled 1.35 s in the modified run. This is one sequential local run per configuration, not a repeated benchmark or a guaranteed CI percentage.

The concrete proposed fixture change is:

```python
thread = threading.Thread(
    target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
)
```

Reproduction of the audit override: `PYTHONPATH=/tmp:. AUDIT_FAST_HTTP=1 AUDIT_OUTPUT=/tmp/mlx-tui-audit-fast.jsonl uv run pytest -p mlx_tui_audit_probe --durations=20 --junitxml=/tmp/mlx-tui-audit-fast.xml`. [Modified-run log](/tmp/mlx-tui-audit-fast.log), [JUnit](/tmp/mlx-tui-audit-fast.xml), [phase/shutdown timings](/tmp/mlx-tui-audit-fast.jsonl).

### TS-02 — Use smaller test subjects and consolidate repeated component paths

**Evidence:** all seven [parameter tests](/Users/alvarogomez/code/python/mlx-tui/tests/unit/test_params.py:13) take `harness: AppHarness`, including direct `params.read_values()` checks. The [harness](/Users/alvarogomez/code/python/mlx-tui/tests/conftest.py:297) mounts all panes; [on_mount](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/app/app.py:224) starts polling, telemetry and cache rescanning. The parameter suite costs **5.55 s**. Two [chat framing cases](/Users/alvarogomez/code/python/mlx-tui/tests/integration/test_chat_integration.py:30) cost **1.86 s**, although [MockTransport tests](/Users/alvarogomez/code/python/mlx-tui/tests/unit/test_chat.py:237) already exercise both formats through `stream_turn` and the real decoder; all 24 chat unit cases cost **0.024 s**.

**Obligation / consequence:** use the smallest scope that proves a behavior. Pure framing variations and widget-local normalization repeatedly pay for unrelated app/server lifecycles. **Counterevidence:** configuration bridging, event routing, focus, scrolling and cancellation ownership really do need mounted coverage; mocking those would remove the behavior being tested.

**Smallest correction:** move direct ParamsPane value checks into a tiny Textual test app, keeping full-app bridge/submission checks. Keep one chat-over-real-HTTP journey plus protocol-truncation coverage; remove the two redundant formatting journeys after preserving their no-malformed-message assertions in the lower-level transport tests. Combine [discovery download](/Users/alvarogomez/code/python/mlx-tui/tests/integration/test_search_integration.py:230) with the expanded-size/revision assertions from [the second download journey](/Users/alvarogomez/code/python/mlx-tui/tests/integration/test_search_integration.py:504), removing one **1.52 s** lifecycle. Scope reductions save only part of current case time; these figures are not additive forecasts after TS-01.

**Effort:** half a day. **Verification tier:** top-line for params/chat (full relevant suites/fixture and source flow); corroborating for discovery (both complete scenarios and helper checked against delegated whole-suite review).

### TS-03 — A mocked setup scenario still downloads Hub metadata

**Evidence:** [test_setup_integration.py:127](/Users/alvarogomez/code/python/mlx-tui/tests/integration/test_setup_integration.py:127) uses `revision = "b" * 40` and mocks `repo_snapshot` and `download_snapshot`, but not `hf_hub_download`. [DiscoverPane](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/discover_pane.py:695) fetches `config.json` for a full revision; [SearchScreen](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/search_screen.py:110) forwards to the real Hub function.

**Obligation / consequence:** an isolated component test should not depend on the public Hub or a developer's cache. This introduces network/retry variability into ordinary pytest. **Counterevidence:** both conftests were checked; neither stubs this call. Download errors are swallowed, which can hide the dependency while still paying its delay. The baseline case took only **1.01 s**, so this is a reliability finding, not the explanation for most of this run's four minutes.

**Smallest correction:** stub `mlx_tui.search_screen.hf_hub_download` to return a temporary config file; retain the 40-character revision and revision-forwarding assertions. **Effort:** minutes. **Verification tier:** top-line; full test and fixtures, metadata and host forwarding flow inspected.

**Probe confirmation:** running this one unchanged test with a temporary recording replacement for the Hub function captured exactly one call for `mlx-community/pinned-model`, `config.json`, revision `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`. Returning a local `{}` config preserved the passing test. This establishes the missing seam, not a measured speedup: the separate run took 1.15 s. [Probe evidence](/tmp/mlx-tui-audit-hub.jsonl).

### TS-04 — Five isolated health-policy tests wait on the real clock

**Evidence:** [test_serverctl.py:597](/Users/alvarogomez/code/python/mlx-tui/tests/unit/test_serverctl.py:597) retries a wrong model; four following timeout scenarios use `timeout_s=1`, with two asserting `time.monotonic() - start >= 0.9`. [wait_healthy](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/serverctl.py:415) sleeps one second between attempts. These five cases total **5.05 s** despite using MockTransport.

**Obligation / consequence:** test retry/deadline decisions with controlled time; repeated real seconds add no protocol coverage. **Counterevidence:** actual OS process termination and cancellation deadlines are different obligations and should keep real boundary checks.

**Smallest correction:** replace the module-local `serverctl.time` reference with a fake clock whose sleep advances virtual time; assert retries, elapsed virtual time and results. Do not patch the shared global `time.monotonic` while asyncio is running. **Effort:** under an hour. **Verification tier:** corroborating; all five scenarios, transport helper and complete polling implementation inspected against delegated suite review.

## Recommendations

| Priority | Change and level | Why here | Rough effort |
|---|---|---|---|
| Do now | TS-01: shorter HTTP fixture polling | Preserves every assertion and real HTTP boundary | Minutes |
| Do now | TS-03: stub the missed Hub config request | Removes an accidental external dependency | Minutes |
| Do now | TS-04: virtual time for five unit cases | Same policy coverage, about five seconds of deliberate waits removed | <1 hour |
| Do now | TS-02: smaller widget tests; consolidate duplicated framing/discovery paths | Avoids repeated whole-app setup while retaining UI wiring coverage | Half a day |
| Worth doing | Fake process outcomes in boot UI state tests; retain a real launch/cleanup journey | The mid-boot-death case alone deliberately waits three seconds; real grace/termination is already tested in serverctl | A few hours |
| Worth doing | Replace the [low-disk fake download's 350 ms sleep](/Users/alvarogomez/code/python/mlx-tui/tests/integration/test_search_integration.py:453) with a bounded event released after the warning assertion | Removes a transient observation window and unnecessary waiting; use finally for release | <1 hour |
| Worth doing | Print `--durations=20` and retain JUnit timing artifacts in CI; identify component cases with a marker or accurate directory | Makes future regressions visible and provides a genuinely cheap local feedback command | <1 hour |
| Only if still needed | Trial two pytest-xdist workers after isolation fixes, benchmark on both CI OSes | Parallelism may reduce elapsed time, but does not remove work; adds a dependency and contention | Half a day |
| Only when changing provider versions | Small optional pinned-runtime contract/smoke check | Verifies actual MLX response assumptions without putting model downloads/inference in every PR run | Separate scoped work |

For general UI tests, reuse the existing `stub_harness` approach to avoid unrelated cache/process discovery, with explicit opt-in for tests asserting those boundaries. Do not globally disable polling or telemetry in tests that are intended to verify them. Treat gains beyond the measured shutdown experiment as proposals until benchmarked.

## Explicitly not recommended

- **Do not delete or mock local durability/locking and process ownership tests for speed.** All 144 classified boundary cases together take only 2.30 s. [Session replace/fsync/lock tests](/Users/alvarogomez/code/python/mlx-tui/tests/unit/test_sessions.py:404) and [process cleanup tests](/Users/alvarogomez/code/python/mlx-tui/tests/unit/test_serverctl.py:242) protect important real behavior.
- **Do not replace every Pilot pause with zero sleep or blindly shorten deadlines.** [AppHarness.wait_for](/Users/alvarogomez/code/python/mlx-tui/tests/conftest.py:286) waits on UI readiness plus bounded polling. Prefer explicit completion events for worker state, retaining layout/message synchronization where assertions concern rendered UI.
- **Do not share a mutable app/server across tests.** Modes, request history, workers, tabs and sessions are mutated throughout the suites; a shorter shutdown interval avoids paying for this shortcut with order-dependent failures.
- **Do not remove keyboard, resize, cancellation or persistence journeys merely because they are slow.** They test cross-widget and lifecycle contracts that pure mocks cannot establish. Cancellation's `asyncio.sleep(30)` is intentionally interrupted; it is not a 30-second delay in passing tests.
- **Do not build broker tests, consumer-contract infrastructure or a large real-inference E2E suite.** There is no broker/team API here. Synthetic protocol coverage is appropriate for fast feedback, with provider qualification handled separately.
- **Do not optimize the many millisecond-scale pure tests first.** The whole chat unit suite is faster than one UI scheduling pause.

## Coverage and unresolved

| Area / level | Audit status |
|---|---|
| Unit logic and plumbing | Checked: inventory/classification across all unit modules; deep review of timing, params, SSE/chat and representative persistence/coordination coverage |
| Filesystem / OS integration | Checked: real round-trips, locks, subprocess/listener ownership present; classification reviewed with representative source verification; not an exhaustive assertion-by-assertion audit |
| Component UI | Checked: all suites inventoried/timed; deep runtime review of harness, app/chat/params, discovery/setup, boot and metrics; session/delete/swap edge coverage sampled |
| External contracts | Checked for presence: no provider-verified suite found; handwritten stubs/MockTransport do not prove upstream compatibility |
| Full inference E2E | Not present in pytest; real MLX launch/model generation not executed in this audit |
| Packaging | Script and CI checked; install/build runtime unverified, next probe is to time the existing artifact job separately |
| Broker async | Not applicable: Textual workers/SSE are asynchronous, but not broker delivery; idempotency/DLQ/rebalance requirements do not apply |
| Async UI races | Cancellation handshakes checked; stale-result assertions sampled. Whether every stale-callback test fails when its guard is removed remains unverified; next probe is a targeted mutation or explicit completion acknowledgement |
| Incident evidence | Recent history inspected, including `f0b93d7` (setup/restart fixes with added regression tests); no production incident reports or historical CI flakes supplied |
| Measurement portability | Local serial result only; next probe is the same commands on Ubuntu/macOS CI. No claimed failure-rate or CI speedup estimate |

The working tree already contained unrelated source/test/documentation edits. This audit adds only this report; the measured optimization is a temporary external pytest plugin, not an applied repository change. The invoked [testing-strategy skill](/Users/alvarogomez/.agents/skills/testing-strategy/SKILL.md) explicitly requires: “Audit, don't rewrite.” It says to change tests only after the user has seen the report and requests implementation.

Raw local evidence: [baseline log](/tmp/mlx-tui-audit-baseline.log), [baseline JUnit](/tmp/mlx-tui-audit-baseline.xml), [case classification/timings](/tmp/mlx-tui-audit-baseline-summary.json), [temporary probe](/tmp/mlx_tui_audit_probe.py). These `/tmp` files are temporary; the measured conclusions are retained above.
