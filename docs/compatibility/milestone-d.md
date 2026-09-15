# Milestone D implementation and evidence record

**Status:** Implementation complete; product acceptance deferred
**Updated:** 2026-09-13
**Plan:** [Milestone D — Earn Daily Use](../plans/2026-09-13-part3-milestone-d-earn-daily-use.md)

## Entry decision

The operator explicitly authorized implementation despite the unmet Phase 0
entry gate. That waiver permits implementation only: it is not qualification,
adoption evidence, or a Milestone D pass.

- [`milestone-b.md`](milestone-b.md) records no live B qualification, no
  observed second physical tier, no profile recommendation, and no retained
  five-operator/14-day return-use evidence. Milestone B remains unachieved.
- [`milestone-c.md`](milestone-c.md) records an implemented managed workflow
  whose live validation is still blocked by B.
- No participant has consented to a Milestone D observation, and no concrete
  restart, editing, copying, file-paste, or context-understanding obstacle has
  been retained from a participant.

## Implementation checklist

This checklist describes shipped behavior, not product acceptance.

- [x] Durable local sessions, explicit reopen/reconcile, interruption records,
  temporary sessions, and clear/delete controls.
- [x] Multiline editing, paste-safe explicit send, answer copying, and explicit
  retry without automatic resubmission.
- [x] Immutable selected UTF-8 file snapshots and exact request-context preview.
- [x] Automated regression, static, formatting, and CLI checks recorded in the
  implementation plan.
- [ ] Named-Mac real-runtime recovery exercise retained for all four scenarios.
- [ ] Consented matched baseline/follow-up observations retained and reviewed.
- [ ] Milestone D achieve/revise/defer decision revisited with user evidence.

## Feature enablement record

Phases 3 and 4 were enabled under the same implementation-only waiver. The
required participant observations were not present before enablement, so the
rows remain explicit missing-evidence records rather than inferred findings.
Ordinary prompts, answers, and file contents must never be copied here.

| Feature subset | Anonymous ID and consent | Tier/runtime | Task category | Observed obstacle | Existing workaround | Baseline dates | Separate-session uses before enablement |
|---|---|---|---|---|---|---|---:|
| Session recovery | Unobserved | — | — | Unobserved | Unobserved | Not started | — |
| Multiline/copy/retry | Unobserved | — | — | Unobserved | Unobserved | Not started | — |
| Selected file snapshots/context preview | Unobserved | — | — | Unobserved | Unobserved | Not started | — |

The developer-local test-isolation finding involving ambient
`XDG_STATE_HOME` is not an operator obstacle and does not satisfy this table.

## Proposed observation protocol

Use a matched 14-day baseline and 14-day follow-up for each consenting
participant. This is this implementation plan's proposed protocol, not a
threshold specified by Part 3. Start follow-up only after the participant's
selected feature subset is enabled. If either window is shorter or exposure
differs, record its actual dates and days and do not compare the windows as
equal exposure.

Retain raw numerators and denominators for every participant:

| Anonymous ID | Consent | Window and dates | Observed days | Task opportunities | Restarts | Resumed tasks | Completed tasks | Assisted tasks | Saved-work losses / acknowledged saves | Unsaved-work losses / opportunities | Session uses | Attributed value | Dropout / alternative-tool reason |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---:|---|---|
| — | — | Baseline: not started | 0 | — | — | — | — | — | — | — | — | — | — |
| — | — | Follow-up: not started | 0 | — | — | — | — | — | — | — | — | — | — |

Record assistance even when a task completes. Count dropouts and use of another
tool in the relevant denominator and preserve the participant's reason. Do not
turn missing observations into zeroes.

## Named-Mac real-runtime recovery exercise

Target hardware available from retained compatibility evidence:
`local-m4-16gib` (Mac mini Mac16,10, Apple M4, 16 GiB, macOS 26.6.2). The
runtime/model identity for an exercise must come from the live pinned attach or
managed setup; cached files or the Milestone A record alone do not prove that a
model was ready during the exercise.

No qualifying exercise was run on 2026-09-13. The table records the exact
procedure still required without substituting stub tests for runtime evidence.

| Scenario | Required procedure | Runtime/model identity | Result/failures | Retained evidence |
|---|---|---|---|---|
| Successful turn and draft | Complete a real coding turn; type a different draft; wait for `Saved locally`; quit and restart; explicitly reopen, reconcile settings, and continue. | Not observed | Not run | None |
| Client cancellation | Start a real turn; request client cancellation; wait for cleanup/save; restart; reopen and verify partial output is excluded before continuing explicitly. | Not observed | Not run | None |
| Endpoint failure | Preserve a draft through a real endpoint failure; restart; reopen/reconcile and continue after readiness is independently restored. | Not observed | Not run | None |
| Context rejection plus snapshot | Attach a selected text file; record its hash without content; provoke a real server context rejection; change or delete the source; restart and verify the saved snapshot before continuing. | Not observed | Not run | None |

For each run, retain the named machine, date/time, attach or managed mode,
verified runtime and model revision, exact operator procedure, observed UI
states, failures, and references to content-free evidence. Never retain the
ordinary coding prompt, answer, or snapshotted file contents in this record.

## Product acceptance decision

**Decision: defer.** Milestone D is not achieved. There are no consented
before/after observations, no returning-user task completions, no attributed
value, and no named-Mac four-scenario recovery record. Automated tests prove
implementation behavior only.

Revisit the decision only when returning users complete real tasks after a
restart without losing acknowledged saved work, repeat use improves against
their own baseline with raw exposure retained, and participants attribute
value to the workflow. Review failures, assistance, dropouts, and alternative
tools even if aggregate use improves. Until then, do not fund more optional
chat/file scope solely because the implementation works.

## Remaining limitations

- Conversation restoration does not restore a warm KV cache, establish model
  residency/readiness, restart an attached server, or prove engine cancellation.
- Only work acknowledged as `Saved locally` is claimed durable; pending text
  may be lost after SIGKILL or power loss.
- Context/token values are character estimates, and server rejection remains
  authoritative.
- Sessions are private local snapshots, not encrypted storage, cloud sync,
  search, branching, or secure erasure from backups.
