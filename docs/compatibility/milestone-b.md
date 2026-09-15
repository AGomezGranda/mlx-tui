# Milestone B qualification and product gate

**Status:** Feature implemented; qualification and product gate blocked  
**Updated:** 2026-09-09  
**Recommendation owner:** Alvaro Gomez (proposed; confirmation pending)

The attach-mode comparison is implemented and stub-tested. No live Milestone B
qualification has been run, no second physical tier has been identified, and no
profile has earned a Milestone B recommendation. Milestone B is not achieved.

## Qualification contract

`tests/runtime/test_milestone_b.py` uses the production comparison runner for
both `a-then-b` and `b-then-a`. It sends 12 sequential requests per order: the
first request plus five repeats for each pinned profile. It does not start,
stop, download, or reconfigure the operator-owned endpoint.

Without `MLX_TUI_B_QUALIFY=1`, both cases skip. With the flag enabled, every
input below is required and invalid evidence fails before inference:

- `MLX_TUI_B_URL`: full `http://<loopback-ip>:<port>/v1/chat/completions` URL.
- `MLX_TUI_B_PROFILE_A_PATH`: absolute cached snapshot path for
  `qwen3-1.7b-baseline`.
- `MLX_TUI_B_PROFILE_B_PATH`: absolute cached snapshot path for
  `qwen3.5-4b-baseline`.
- `MLX_TUI_B_RUNTIME_ENV`: absolute MLX-LM virtual-environment directory.
- `MLX_TUI_B_LAUNCH_EVIDENCE`: absolute path to the JSON record below.
- `MLX_TUI_B_MACHINE_TIER`: stable lowercase tier slug.
- `MLX_TUI_B_OUTPUT`: absolute directory for retained comparison JSON files.

The launch-evidence file is deliberately small and operator-authored. `argv`,
`pid`, and `create_time` must match the live listening process; the runtime
environment, install freeze, and launch settings are verified before requests.

```json
{
  "schema_version": 1,
  "pid": 12345,
  "create_time": 1788950000.0,
  "argv": ["/absolute/runtime/bin/python", "/absolute/runtime/bin/mlx_lm.server", "--model", "/absolute/snapshot", "--host", "127.0.0.1", "--port", "18080", "--log-level", "INFO"],
  "runtime_env": "/absolute/runtime",
  "freeze_path": "/absolute/milestone-a-runtime.txt",
  "freeze_sha256": "64-lowercase-hex-characters",
  "launch_settings": {
    "server": "mlx_lm.server",
    "host": "127.0.0.1",
    "port": 18080,
    "log_level": "INFO",
    "model_argument": "pinned_snapshot_path"
  },
  "isolation": {
    "restart_or_eviction": "operator-observed method or unknown"
  },
  "operator_conditions": {
    "power": "operator-observed condition",
    "concurrent_load": "operator-observed condition",
    "suspect": false
  }
}
```

Run once on each named physical tier:

```sh
rtk proxy env \
  MLX_TUI_B_QUALIFY=1 \
  MLX_TUI_B_URL="$MLX_TUI_B_URL" \
  MLX_TUI_B_PROFILE_A_PATH="$MLX_TUI_B_PROFILE_A_PATH" \
  MLX_TUI_B_PROFILE_B_PATH="$MLX_TUI_B_PROFILE_B_PATH" \
  MLX_TUI_B_RUNTIME_ENV="$MLX_TUI_B_RUNTIME_ENV" \
  MLX_TUI_B_LAUNCH_EVIDENCE="$MLX_TUI_B_LAUNCH_EVIDENCE" \
  MLX_TUI_B_MACHINE_TIER="$MLX_TUI_B_MACHINE_TIER" \
  MLX_TUI_B_OUTPUT="$MLX_TUI_B_OUTPUT" \
  uv run pytest -q tests/runtime/test_milestone_b.py
```

An accepted tier run reports exactly `2 passed`, zero skipped, and produces one
retained result for each order. After each accepted run, copy the result into
`docs/compatibility/evidence/milestone-b/` and record the exact command, run
IDs, profile/template/runtime identities, result SHA-256 hashes, failures,
cache reuse, process-RSS scope, and changed conditions here. Do not commit
secrets or substitute current repository heads for pinned revisions.

## Qualification observations

No live observations yet.

| Tier | Hardware/OS owner and access | Orders | Result IDs/hashes | Review |
|---|---|---|---|---|
| `local-m4-16gib` | See [Milestone A](milestone-a.md) | Not run | None | Pending |
| Second tier | Not yet observed | Not run | None | Blocking |

Latency with materially different cached-token reuse is inconclusive. Memory is
also inconclusive without controlled restart/eviction evidence or the same
non-overlapping result in both orders. Process RSS is not per-profile residency.
Failures and unknowns remain part of the retained evidence.

## Consent-based operator observation sheet

Before recording a session, obtain the participant's consent to retain the
fields below. Use an anonymous participant ID and do not record prompts or
ordinary chat content.

| Participant | Consent | Tier | Completed unassisted? | Assistance | Choice and reason | Evidence explanation | Separate-session return within 14 days and action | Dropout or alternative reason |
|---|---|---|---|---|---|---|---|---|
| P01 | Pending | — | — | — | — | — | — | — |
| P02 | Pending | — | — | — | — | — | — | — |
| P03 | Pending | — | — | — | — | — | — | — |
| P04 | Pending | — | — | — | — | — | — | — |
| P05 | Pending | — | — | — | — | — | — | — |

The product gate requires at least four unassisted completions, three
evidence-backed explanations, and three separate-session returns within 14
days. Every failure and dropout still counts and keeps its reason even if the
numeric thresholds pass.

## Focused next validation question

Can operators on `local-m4-16gib` and one observed second memory tier complete
both profile orders with verified identities, then explain and reuse a choice?
Until that is answered, do not publish a recommendation or advance to
Milestone C/D.
