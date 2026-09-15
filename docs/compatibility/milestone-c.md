# Milestone C implementation record

**Status:** Implementation in progress; live validation blocked by Milestone B
**Updated:** 2026-09-12

## Entry decision

The Phase 1–2 implementation was authorized while preserving the product
gate. [`milestone-b.md`](milestone-b.md) still records no live B qualification,
no observed second physical tier, and no five-operator/14-day evidence. No
profile recommendation or C validation claim is made here.

## Retained implementation inputs

| Input | Recorded value | Evidence |
|---|---|---|
| Observed physical tier | `local-m4-16gib`: Mac mini Mac16,10, Apple M4, 16 GiB unified memory | [Milestone A](milestone-a.md) |
| Observed OS | macOS 26.6.2 (Darwin 25.6.0), arm64 | [Milestone A](milestone-a.md) |
| Second physical tier | Not observed | B blocker |
| TUI artifact | `mlx-tui` 0.2.0 wheel/sdist built from this tree | Wheel SHA-256: `6035a385f316363c8aaf6e9ee8fc028aae51b1d1f2ba337fbb5f217f16ba6be1` |
| Resolver/interpreter | uv 0.12.7 / CPython 3.13.1 | A and Phase 1 probe |
| MLX-LM source | `74e7cf931e84ef7c2f63e875adf414e20decc1c5` | Packaged runtime freeze |
| MLX / MLX-Metal | 0.32.2 / 0.32.2 | Packaged runtime freeze |
| MLX-LM | 0.32.0 | Packaged runtime freeze |
| Build backend pin | `setuptools==84.0.0` | `managed-build-constraints.txt` |
| Qualified OS matrix | Darwin arm64, macOS 26.6.2 only | A observation; not a broad Mac claim |
| Recommendation owner | Alvaro Gomez proposed; confirmation pending | B report |

## Phase 1 build evidence

The pinned source uses legacy `setup.py` packaging. An isolated uv 0.12.7
probe resolved `setuptools==84.0.0`; a constrained reconstruction installed
MLX-LM 0.32.0 from the same commit. The retained resource hashes are:

- `managed-runtime.txt`: `d856e423900bb148aa1d8e4a2acf2df14a8d8ae4ad56b86b0f11e525b01397d3`
- `managed-build-constraints.txt`: `9ddc934e727bb4da3a6c7cb677cf939c0d291af6cce63153e436888f3c379fa9`
- wheel: `6035a385f316363c8aaf6e9ee8fc028aae51b1d1f2ba337fbb5f217f16ba6be1`

No model download or inference was performed for this implementation
checkpoint.

## Validation still required

- B qualification must identify the second tier and retain the two accepted
  comparison orders before C recommendations can be published.
- A final wheel hash and exact installed dependency record must be retained
  for testers.
- Automated phases 3–4 and the opt-in contract are implemented; only the
  qualified live runtime inputs and user-observation gate remain unverified.
- The opt-in command is
  `MLX_TUI_C_QUALIFY=1 MLX_TUI_C_OUTPUT=/absolute/output MLX_TUI_C_MACHINE_TIER=...`
  plus the test-owned runtime and two pinned snapshot paths. It must record
  both comparison orders, cleanup, and zero skipped tests for each qualified
  tier; it is not run without those inputs.
- Fresh-install recruitment remains out of scope until the B gate and runtime
  inputs are qualified. No C recommendation is published from this record.

## Implementation gate records

The following checks passed on 2026-09-12 from the working tree; they use
stubs/offline fixtures and do not qualify MLX inference:

- `rtk proxy uv run pytest -q` — 570 passed, 9 opt-in live-contract skips.
- `rtk proxy uv run python tests/artifact_smoke.py` — passed.
- `rtk proxy uv run ruff check .` — passed.
- `rtk proxy uv run ruff format --check src tests` — passed.
- `rtk proxy uv run pyrefly check --min-severity warn` — 0 diagnostics.

No live C command, runtime hash, or second-tier result is retained yet. The
live record must be produced separately for each qualified tier and include
the artifact SHA-256, runtime/build manifests, profile/snapshot hashes, exact
launch argv and PID/create-time, comparison result hashes, durations, and
cleanup outcome. Diagnostics must contain no access tokens.

## Fresh-install observations

No users have been recruited. The blank table is an explicit missing-evidence
record, not a passing result.

| Anonymous ID | Consent | Starting tooling/cache | Artifact | Hardware/OS/tier | Download time | Other setup | Comparison | Assistance | Failure/recovery | Ownership/shutdown | Dropout/alternative |
|---|---|---|---|---|---:|---:|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — | — | — |
