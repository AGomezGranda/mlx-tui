# Milestone E qualification record — shared serving blocked

**Status:** Qualification only; supported shared serving blocked
**Updated:** 2026-09-13
**Plan:** [Milestone E — Serve Real Clients: Gated Qualification](../plans/2026-09-13-part3-milestone-e-serve-real-clients.md)
**Guide:** [Local clients — qualification only](../clients.md)

Implementation alone does not achieve Milestone E. The full gate is
enforceable lifecycle behavior plus repeated workloads plus observed
target-user reuse. A completed implementation plan substitutes for none
of these.

## Initial qualification state

| Item | State |
|---|---|
| Implementation | Pending (Phase 1: boundary and record only) |
| HTTP qualification | Not run |
| OpenCode qualification | Not run |
| Wrong-target model enforcement | Blocked on stock upstream `74e7cf9` |
| Structured tools for E | Unverified for E |
| Engine cancellation | Unknown |
| Target-user reuse | Not observed |

## Phase 3 implementation (2026-09-14, no live run yet)

`tests/runtime/test_milestone_e.py` implements the opt-in qualification
suite; no endpoint was contacted, downloaded, started, or stopped while
writing it, so every live row below stays not run. Default discovery is
model-free: `pytest -q tests/runtime` skips all 9 E tests without opt-in.

Opt-in inputs: `MLX_TUI_E_QUALIFY=1`, `MLX_TUI_E_URL` (explicit-IP loopback
`/v1/chat/completions` URL accepted by `parse_loopback_url`),
`MLX_TUI_E_MODEL` (absolute pinned snapshot, must equal the verified
`qwen3-1.7b-baseline` entry), `MLX_TUI_E_RUNTIME_ROOT` (passes
`inspect_runtime`), `MLX_TUI_E_MACHINE_TIER`, and absolute
`MLX_TUI_E_OUTPUT`. Missing opt-in skips; opted-in missing/invalid inputs
fail. OpenCode coverage additionally requires `MLX_TUI_E_OPENCODE=1` (plus
optional `MLX_TUI_E_OPENCODE_BIN`) after the HTTP suite.

What the suite retains per attempt via `EvidenceRecorder` and `try/finally`
(schema version 1 plus a `metadata.json` per run directory): sequence, cycle
and client IDs, timestamps, prompt/check identity, limits and sampling
settings, terminal state, first-output/total timing, client-close timing,
and recovery result. Request-echoed model strings are recorded as echoed
values, never as residency proof. Only fixed synthetic prompts are stored.

Coverage, all bounded with finite deadlines:

- Listener preflight: live PID/create time, recorded argv match,
  interpreter under the runtime root, exact `--model` equals the snapshot;
  a negative test proves an unrelated root or model cannot qualify.
- Five mixed cycles of real `ChatPane` submission inside an isolated
  `MlxTuiApp.run_test()` session concurrent with an independent raw-httpx
  request (non-stream, SSE, longer bounded output). Overlap is asserted
  from observed dispatch/first-byte events plus wall-clock intervals, with
  selection stability and honest observation labels checked per cycle.
- External cancellation before/after first output, TUI cancellation, each
  with a fresh recovery request. Client close plus recovery is recorded as
  unknown engine cancellation, never as proof.
- Malformed JSON, invalid sampler, missing model, and nonexistent absolute
  model/adapter/draft targets, then pinned-target recovery. Wrong-target
  enforcement stays explicitly failed/unavailable on stock upstream.
- Fixed synthetic `get_weather` tool round trip (non-stream plus streamed
  fragments, ID/name/arguments preserved, constant tool result, required
  final answer). Text instead of `tool_calls` records unavailable and keeps
  tool-dependent coding-app qualification blocked. Model-written code or
  shell is never executed.
- Five bounded OpenCode text-plus-read cycles (exact fixture content
  required in the final answer; text-only, unexpected tool, or wrong read
  path fails), plus automated before/after-first-event cancellations with
  fresh recovery. Config hash, `1.18.28` version check, `--pure`,
  deny-by-default policy, disabled sharing, and explicit `--model` routing
  are retained per run. `--auto` is refused.

To run: supply the validated E environment, then
`rtk proxy uv run pytest -q tests/runtime/test_milestone_e.py` and retain
all outputs. `rtk proxy opencode --version` (expect `1.18.28`) and
`rtk proxy opencode run --help` must remain available before optional
execution. A structured-tool or coding-client failure leaves that
capability unqualified; it is not converted to a skip or a pass.

## Live workload observations

Not run. When the named-Mac runs complete, record here: five mixed cycles
per client, automated OpenCode before/after-first-event cancellations, TUI
cancellation, retries, configured-model inspection, and the offline
exercise, with actual request behavior and auxiliary calls. Raw HTTP success
never substitutes for a running OpenCode pass. CPU/power/concurrent-load
notes go here without per-request RSS claims or new performance targets.
Absence of evidence stays unverified.

## Pinned identities for any future qualification run

| Input | Value / source |
|---|---|
| Upstream runtime | MLX-LM `74e7cf931e84ef7c2f63e875adf414e20decc1c5`, MLX 0.32.2 — [Milestone A](milestone-a.md) |
| Model snapshot | `mlx-community/Qwen3-1.7B-4bit` revision `3b1b1768f8f8cf8351c712464f906e86c2b8269e` — [Milestone A](milestone-a.md) |
| Coding-app target | OpenCode `1.18.28` (installed; only version/help verified during planning) |
| Machine | `local-m4-16gib` (Mac mini Mac16,10, Apple M4, 16 GiB, macOS 26.6.2) — [Milestone A](milestone-a.md) |
| Launch settings | Not run — record exact argv, PID/create-time, host/port, and log level per attempt |
| Workload parameters | Not run — record prompt/check identity, limits/sampling, cycle count, and deadlines per attempt |
| Dates | Not run |
| Retained evidence paths | None yet |

Every attempt, failure, and retained output must be recorded here with
its runtime/model/client versions, source or artifact hashes, machine,
launch settings, workload parameters, dates, and evidence paths. Absence
of evidence stays unverified; raw HTTP success never substitutes for a
coding-app run.

## Current blockers (B/C/D preserved)

- [Milestone B](milestone-b.md): no live qualification, no observed
  second physical tier, no recommendation, no five-operator/14-day
  return-use evidence.
- [Milestone C](milestone-c.md): live validation blocked by B; no
  fresh-install observations.
- [Milestone D](milestone-d.md): no consented baseline/follow-up
  observations; no named-Mac four-scenario recovery exercise.
- Milestone E adds its own enforcement blocker: stock `74e7cf9` accepts
  per-request `model`/`draft_model`/`adapters` and loads a changed tuple,
  so neither wrong-target rejection nor lifecycle drain is enforceable.

## Consent-based endpoint reuse observations

Obtain consent before retaining a row. Use an anonymous participant ID;
never record ordinary prompts, answers, file contents, or credentials.
No numerical adoption threshold is set; leave the table empty until real
observations exist.

| Anonymous ID | Client/version | Task | Assistance | First use | Later separate-session use | Outcome | Failure/recovery | Reason to reuse or abandon | Consent scope |
|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — |
