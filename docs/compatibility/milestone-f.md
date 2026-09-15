# Milestone F release and support record

**Status:** Implementation in progress; release blocked
**Updated:** 2026-09-15
**Plan:** [Milestone F — Ship a Supported Release](../plans/2026-09-14-part3-milestone-f-ship-a-supported-release.md)

This record separates packaging evidence from real managed-runtime evidence.
Building and installing an artifact does not qualify MLX inference, shutdown,
offline startup, hardware support, or adoption.

## Artifact and dependency evidence

| Record | Version | Artifact | SHA-256 | Python / uv | Dependency resolution | Evidence |
|---|---:|---|---|---|---|---|
| Pre-F development checkpoint | 0.2.0 | `mlx_tui-0.2.0-py3-none-any.whl` | `27e39d77ce5bac6dcaf1979c52fb2f767b930b2181da00568de333d4b2d66b9c` | 3.13.1 / 0.12.7 | httpx 0.28.1; huggingface-hub 1.31.0; psutil 7.2.2; textual 8.2.8; rich 15.0.0 | [retained manifest](evidence/milestone-f/pre-f-0.2.0-development/manifest.json) |
| Pre-F development checkpoint | 0.2.0 | `mlx_tui-0.2.0.tar.gz` | `5f567b8f36db39d1ee3d6107e489abbd82ba6778e0f6fbb6395c99e5dafc037b` | 3.13.1 / 0.12.7 | Same isolated smoke environments as wheel | [retained manifest](evidence/milestone-f/pre-f-0.2.0-development/manifest.json) |
| F candidate | 0.3.0 | `mlx_tui-0.3.0-py3-none-any.whl` | `e4dd8c7c5817f3fb358dd43f6454eb2f2815d7381821120c03b2fa417f15e44a` | 3.13.1 / 0.12.7 | httpx 0.28.1; huggingface-hub 1.31.0; psutil 7.2.2; textual 8.2.8; rich 15.0.0 | [retained manifest](evidence/milestone-f/f-candidate-0.3.0-development/manifest.json) |
| F candidate | 0.3.0 | `mlx_tui-0.3.0.tar.gz` | `3e0904d1387777819b4eeeec58a94fe0284d66913f644e76594d225f7453e3d0` | 3.13.1 / 0.12.7 | Same isolated smoke environments as wheel | [retained manifest](evidence/milestone-f/f-candidate-0.3.0-development/manifest.json) |

The retained artifacts were built from revision
`5ce9cc8ab32b4ebf3f1d648cf670dca3b7b3e115` with a dirty working tree. The
dirty status is part of each manifest; these are development checkpoints, not
published release bytes. The existing 0.1.0 wheel and historical C hash remain
untouched and are not relabelled as F evidence.

## Phase 3 upgrade/rollback evidence

The optional artifact mode validates predecessor wheel metadata and SHA-256,
uses isolated `UV_TOOL_*` and XDG roots, captures `uv pip freeze` at each
stage, preserves config/presets and synthetic state, rejects an invalid
replacement, repeats the candidate install, and reopens the predecessor after
rollback. The current phase-3 candidate wheel hash is
`ea85349611f3869263e5e9d6b27642e570dd7c2acd1d948aabd48a20dbfd2679`; the
retained manifests include the full dependency freezes and stage results:

| Predecessor | SHA-256 | Result | Evidence |
|---|---|---|---|
| 0.2.0 development checkpoint | `27e39d77ce5bac6dcaf1979c52fb2f767b930b2181da00568de333d4b2d66b9c` | Passed session/draft/acknowledged-answer/attachment/comparison/choice preservation and rollback | [manifest](evidence/milestone-f/upgrade-pre-f-0.2.0-20260915/manifest.json) |
| 0.1.0 legacy wheel | `226aae53187aa6573120f8bd644a8969d16465ba2981a990c166a9d8a5b99b03` | Passed attach/config replacement and rollback; legacy wheel has no session/managed APIs | [manifest](evidence/milestone-f/upgrade-legacy-0.1.0-20260915/manifest.json) |

These are isolated artifact exercises, not live-MLX qualification and not a
claim that the candidate is ready for publication.

## Runtime inputs and support matrix

| Row | Label | OS / architecture | Machine tier | Runtime identity | Result | Evidence |
|---|---|---|---|---|---|---|
| Milestone A | Historical | macOS 26.6.2 / arm64 | `local-m4-16gib` — Mac16,10, Apple M4, 16 GiB | MLX-LM `74e7cf9`; MLX/MLX-Metal 0.32.2; freeze `d856e423900bb148aa1d8e4a2acf2df14a8d8ae4ad56b86b0f11e525b01397d3`; build constraints `9ddc934e727bb4da3a6c7cb677cf939c0d291af6cce63153e436888f3c379fa9` | Historical request/stream evidence only | [Milestone A](milestone-a.md) |
| Milestone C | Historical implementation | macOS 26.6.2 / arm64 | `local-m4-16gib` | Same pinned runtime and resource hashes | No live F qualification | [Milestone C](milestone-c.md) |
| F live candidate | Not run | Current host: macOS 27.0 / Darwin 27.0.0 / arm64 | `local-m4-16gib` — Mac16,10, 16 GiB | Candidate 0.3.0; same pinned runtime resources | Not run; current OS is outside the pinned 26.6.2 managed matrix | No F live evidence |

The packaged resource hashes are exact inputs, not proof that a runtime is
installed or healthy. “Packaging-tested”, “historical”, “runtime-qualified”,
“untested”, and “blocked” are intentionally separate labels. No wider physical
tier is claimed.

## F qualification rows

All live F rows remain **not run** until a test-owned installation, runtime,
model snapshot, output directory, and validated matrix identity are supplied.

| Check | Status | Evidence path |
|---|---|---|
| Clean-account candidate install | Not run | — |
| 0.1.0 attach/config replacement | Not run | — |
| Pre-F 0.2.0 session/managed preservation | Not run | — |
| Failed replacement and rollback | Not run | — |
| Managed install/start/chat/compare shutdown | Not run | — |
| Offline restart with complete assets and networking disabled | Not run | — |
| Real-MLX workload and recovery | Not run | — |
| Seven days personal use | Not run | — |
| Fourteen days, at least two external target users | Not run | — |

No live model was downloaded or started for the packaging checkpoints. No
participant has been invented and no adoption observation is counted as a
success.

Phase-4 dummy-process lifecycle checks cover installer cancellation,
identity-checked process-group cleanup, a TERM-exiting parent with a TERM-
ignoring descendant, repeated managed close, and survival of an unrelated
process. Real-MLX shutdown rows remain **not run**.

## Opt-in F contract

The final installed-artifact contract is `tests/runtime/test_milestone_f.py`.
Run a copied version outside the checkout from the candidate environment with
`PYTHONPATH` unset and a fresh output directory:

```sh
MLX_TUI_F_QUALIFY=1 \
MLX_TUI_F_ARTIFACT=/absolute/candidate/mlx_tui-0.3.0-py3-none-any.whl \
MLX_TUI_F_ARTIFACT_SHA256=<lowercase-sha256> \
MLX_TUI_F_RUNTIME_ROOT=/absolute/test-owned/runtime \
MLX_TUI_F_MODEL_PATH=/absolute/pinned/hf/snapshot \
MLX_TUI_F_MACHINE_TIER=local-m4-16gib \
MLX_TUI_F_OUTPUT=/absolute/fresh/evidence \
  /absolute/candidate-python -m pytest -q /absolute/copied/test_milestone_f.py
```

The contract derives the installed version and direct-artifact hash, validates
the declared Darwin arm64/macOS 26.6.2 `local-m4-16gib` row, inspects the
existing runtime without installing, verifies the packaged model profile,
exercises a fixed request plus client cancellation/recovery and saved
attachment reopening, and records owned process/port cleanup before start,
while idle, and during generation. `metadata.json` and `results.json` retain
allowlisted identities, package versions, timings, PIDs/create-times and
cleanup outcomes only. No live F run is retained yet; the current host is
macOS 27.0 and therefore outside the pinned managed matrix.

## Release decision and preserved blockers

The release decision is **blocked**. Packaging and static implementation work
does not satisfy the real-runtime, clean-install, rollback, offline, shutdown,
performance, or consented-use gates. Milestones B, C, D, and E retain their
existing qualification blockers; broader hardware support remains unsupported
with only one physically available Mac. No publishing, tagging, uploading, or
participant messaging is authorized by this plan.

The proposed 5% upstream-latency claim is unverified and is not inferred from
client timings. Engine cancellation, SIGKILL/power-loss cleanup, warm KV
restoration, and general coding ability remain unknown unless separately
observed and retained.
