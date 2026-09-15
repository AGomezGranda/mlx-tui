# Plan Review: Milestone E — Serve Real Clients: Gated Qualification

**Date:** 2026-09-14
**Target:** docs/plans/2026-09-13-part3-milestone-e-serve-real-clients.md
**Review:** 1
**Verdict:** REVISE

## Assessment

The plan draws the right boundary between qualification and supported shared serving, reuses existing components, and accurately describes most of the current code. It is not ready to implement because the OpenCode exercise is not fail-closed and Phase 3 leaves several preflight, workload, and cancellation contracts inconsistent or underspecified.

Repository verification matched the recorded baseline: Ruff passed, Pyrefly reported 0 errors, the managed/status subset passed 28 tests, runtime tests skipped all 9 opt-in contracts, and the full suite passed 623 tests with 9 skips and the two recorded fork warnings. OpenCode 1.18.28 and the claimed `run` flags are present. No earlier review exists for this plan.

## Cross-Cutting Themes

- Phase 3 is careful about evidence semantics but not yet equally precise about the executable harness boundary: runtime identity, model/profile identity, tool permissions, repetition, and cancellation all need exact contracts.
- Several UI requirements exceed the facts or URL forms the current app can represent. The plan should either add the missing state/canonicalization or narrow its claims.

## Findings

### Critical

- **[Security] Phase 3 OpenCode setup and case (lines 105–106):** Isolated XDG directories, `--pure`, and a local-only provider do not sandbox OpenCode tools. The plan requires a real model-directed read-tool cycle but defines no permission policy, while also promising not to execute arbitrary model-written shell or code. OpenCode permissions can cover read, edit, bash, task, web, and external-directory access; without a fail-closed generated policy, an unexpected tool call can mutate files, execute commands, or access the network. Generate a policy that denies all tools by default, permits only reading the exact fixture path, denies external-directory/write/edit/bash/task/web/MCP-style actions and sharing, never uses `--auto`, strips inherited config-injection variables as well as credentials, and verifies the emitted tool name/arguments before counting success. See [OpenCode permissions](https://opencode.ai/docs/permissions/) and installed `opencode run --help`, where `--pure` only disables external plugins.

### Major

- **[Correctness / Plan Mechanics] Phase 3 preflight (line 99):** `MLX_TUI_E_URL` is defined as an origin, but `parse_loopback_url` accepts only an exact `/v1/chat/completions` URL (`src/mlx_tui/comparison_contracts.py:57–89`). A valid origin would fail. Define the input as the full chat URL, or append the route before validation and derive the origin from the canonical result.
- **[Correctness / API/Compatibility] Phase 3 preflight (line 99):** `verify_profile_snapshot` requires a `ProfileEntry` plus a snapshot (`src/mlx_tui/comparison_runner.py:124–143`), but the proposed inputs supply no profile identity. Pin and load `qwen3-1.7b-baseline` (or add a profile-ID input), verify its revision/repository mapping, and then bind `MLX_TUI_E_MODEL` to that entry as Milestone C does in `tests/runtime/test_milestone_c.py:74–94`.
- **[Security / Correctness] Phase 3 listener provenance (line 99):** `inspect_runtime` verifies files under a runtime root, while `find_server_process` only proves that an MLX server-like process owns the listener (`src/mlx_tui/process.py:81–90,133–168`). Neither proves that the listener uses the supplied runtime or model. Specify the existing Milestone B-style checks: live PID/create time, exact recorded argv, an interpreter/runtime-root prefix, and the expected `--model` argument, plus a negative test for an unrelated runtime (`tests/runtime/test_milestone_b.py:159–180`).
- **[API/Compatibility] Phase 2 IPv6 behavior (lines 75, 80):** The preview may format IPv6 correctly, but the app itself creates `http://{host}:{port}` without brackets (`src/mlx_tui/app/__init__.py:317–323`), so an `::1` app cannot mount an HTTP client. Canonicalize the app's actual base URL, audit the managed probe URL at `src/mlx_tui/managed.py:727–732`, and test IPv6 end to end instead of only testing preview text.
- **[Correctness] Phase 2 endpoint facts (line 75):** Existing state cannot truthfully show “last response and its recorded time.” `ServerIdentity` has only `last_success_at` (`src/mlx_tui/status.py:27–37`); after a mismatched response, `record_generation_success` updates `last_response_model` but retains the older success timestamp (`src/mlx_tui/app/__init__.py:626–650`). Add a distinct `last_response_at` updated whenever response identity is observed, or label the existing timestamp strictly as last verified success. Cover mismatch-after-success.
- **[Test Coverage / Plan Mechanics] Phase 3 OpenCode workload (lines 106–107, 121–122):** The test specifies one text probe and one read-tool round trip, but the evidence and manual criteria require five mixed cycles for each client and repeated workloads. Define five bounded OpenCode cycles with per-cycle outcomes, or narrow the evidence claim to the two exercises actually specified.
- **[Test Coverage] Phase 3 OpenCode cancellation (lines 106–107, 122):** Cancellation and follow-up are acceptance requirements, but the OpenCode case defines no reproducible trigger, process/session action, before/after-first-event distinction, deadline, recovery request, or retained outcome. Add a bounded automated case or an exact manual protocol covering those details.

### Minor

- **[Test Coverage] Phase 2 endpoint preview (lines 75, 80, 91):** Add explicit assertions for absent/present timestamp rendering and freshness after a later response; the current coverage list does not name them.
- **[Plan Mechanics] Phase 2 focused command (line 85):** `tests/unit/test_status.py` tests response classification, not `StatusBar` wording (`src/mlx_tui/status_bar.py:78–99`). Include a focused status-bar/integration selector or call this only the managed/status-domain subset; the full suite remains the actual `Ready` → `Reachable` guard.

### Suggestions

- None beyond the prioritized changes below.

## Strengths

- The enforcement gate remains independent of happy-path qualification, matching the upstream server's request-controlled model/adapter/draft loading behavior.
- Replacing same-target synthetic success with the existing one-token `wait_healthy` probe addresses the root cause without adding a new probing layer.
- The plan reuses `TextPreviewScreen`, `httpx`, pytest, and `EvidenceRecorder` rather than introducing new dependencies or speculative infrastructure.
- It distinguishes selection, catalogue availability, response identity, residency, client disconnect, and engine cancellation instead of overclaiming from weak evidence.
- Default live tests remain opt-in and model-free, disruptive cases are ordered after useful workloads, and failed qualification remains evidence rather than becoming a skip or false pass.
- Hostile shell/JSON strings, compact layout, focus restoration, raw protocol failures, and owned-process cleanup are explicitly covered.

## Recommended Changes

1. Make the generated OpenCode harness fail closed: exact read-only permission, all other tools denied, inherited config overrides removed, sharing disabled, and emitted tool arguments verified.
2. Make Phase 3 preflight implementable by defining the URL form, profile binding, and exact live-process/runtime/model provenance checks and negative tests.
3. Resolve the Phase 2 fact-model gaps: canonicalize IPv6 URLs used by the app and either add `last_response_at` or narrow the timestamp label.
4. Align OpenCode execution with the evidence contract by defining five cycles and a reproducible cancellation/recovery protocol, or narrow the claimed evidence.
5. Add the two focused UI assertions for timestamp semantics and status-bar wording.
