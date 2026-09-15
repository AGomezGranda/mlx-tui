# Local clients — qualification only; shared serving blocked

**Status:** Qualification only. Supported shared serving is blocked.
See the [Milestone E evidence record](compatibility/milestone-e.md).
A completed implementation plan does not substitute for enforcement,
repeated workloads, or observed reuse.

This guide describes what can be investigated with an operator's existing
local endpoint. It does not enable supported sharing.

## What the four stages mean

- **Investigation:** an operator points an HTTP client or coding app at
  their own endpoint and observes behavior. No support claim follows.
- **Verified request features:** named routes and behaviors proven by
  retained opt-in contracts on a named Mac
  (`/health`, `/v1/models`, `/v1/chat/completions`; streaming,
  cancellation/recovery, errors, tool round trips). Untested routes and
  universal OpenAI compatibility remain unclaimed.
- **Enforceable shared serving:** the product gate. Not met.
- **Observed adoption:** consenting target users reuse the endpoint in
  separate sessions. Not observed.

No checkbox or passing test in this guide upgrades one stage into another.

## Required policy for supported sharing (not met)

Supported shared serving of one endpoint by the TUI plus an external
client would require, for both clients at once:

- One pinned model/adapter/draft tuple. A request for any other tuple
  must be rejected **before** loading anything.
- Lifecycle coordination: a model switch, restart, or shutdown must
  refuse or drain active work from both clients first.

Stock upstream `74e7cf9` (`mlx_lm.server`) cannot enforce either
guarantee across external clients: it accepts `model`, `draft_model`
and `adapters` per request and loads a changed tuple, clearing the old
target before the replacement is ready. None of the following fixes
this: an app-local operation lock (covers only this TUI), offline mode,
a client-side model allowlist, or a startup `--model` flag.

This block is a product support gate. It is not a network access
restriction on an operator's existing endpoint: operators remain free
to point their own tools at their own server.

## Isolated qualification only

Until the gate above is enforceable, only isolated qualification runs
are defined:

- Same explicit absolute snapshot target for the TUI and the external
  client; no concurrent Compare run, model activation/switch, or cache
  deletion during the exercise.
- No background server lifetime: the endpoint lives only for the
  qualified run. Managed shutdown stops its owned child; attach
  shutdown never stops the operator's server.
- Explicit cancellation on each client (before/after first output) plus
  a fresh follow-up recovery request.
- The operator ends external work before any intentional restart. The
  TUI cannot detect all external work and promises no drain; an
  intentional shutdown during client activity is an interruption, not a
  graceful drain.

Standalone HTTP and OpenCode recipes live below. Every value marked
`<...>` comes from the TUI's `F3` Endpoint preview at qualification time;
the request shapes are copied from the implemented preview and qualification
output, not hand-diverged payloads.

## Route scope

Tested routes, when qualified: `GET /health`, `GET /v1/models`, and
`POST /v1/chat/completions` (plain and SSE streaming). Responses,
embeddings, and universal OpenAI compatibility remain untested, and no
compatibility claim follows from passing the three routes above.

## Standalone HTTP recipe

Point any HTTP client at the loopback `/v1` base from the Endpoint preview.
The qualification suite (`tests/runtime/test_milestone_e.py`) uses this
exact payload shape with the verified absolute snapshot as `model`:

```python
import httpx

payload = {
    "model": "<absolute pinned snapshot>",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 32,
    "temperature": 0,
    "seed": 7,
    "chat_template_kwargs": {"enable_thinking": False},
}
response = httpx.post(
    "http://<host>:<port>/v1/chat/completions",
    json=payload,
    timeout=httpx.Timeout(120.0, connect=5.0),
)
```

The Endpoint preview also shows a generated curl recipe with explicit model
and a 30-second cap, built with `shlex.join` so hostile model strings stay
literal:

```sh
curl -sS --max-time 30 'http://<host>:<port>/v1/chat/completions' \
  -H 'Content-Type: application/json' \
  -d '{"model": "<absolute pinned snapshot>", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 32, "stream": false}'
```

Copying either example executes nothing by itself; both are labelled
unqualified until matching retained evidence exists in the
[Milestone E evidence record](compatibility/milestone-e.md).

## OpenCode 1.18.28 isolated setup

Qualified only as a disposable, local-only exercise. Never point OpenCode at
an endpoint you have not isolated first, and never let it touch the
operator's global configuration.

1. Create empty directories for the harness-owned coding directory and the
   four XDG locations (`XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
   `XDG_STATE_HOME`, `XDG_CACHE_HOME`). Start from an empty `HOME` as well
   so inherited global/project configuration cannot apply.
2. Remove inherited provider credentials and config-injection variables
   before setting harness-owned values: `OPENCODE_CONFIG`,
   `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_TUI_CONFIG`,
   and `OPENCODE_AUTO_SHARE`, plus any `OPENCODE_*` and provider credential
   variables. Then set `OPENCODE_CONFIG` to the generated file below.
3. Write this generated config (exact shape used by the qualification
   suite), with the loopback `/v1` base and the verified snapshot:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "mlx-tui-local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "mlx-tui-local",
      "options": {"baseURL": "http://<host>:<port>/v1"},
      "models": {"local": {"name": "<absolute pinned snapshot>"}}
    }
  },
  "model": "mlx-tui-local/local",
  "small_model": "mlx-tui-local/local",
  "enabled_providers": ["mlx-tui-local"],
  "share": "disabled",
  "permission": {
    "*": "deny",
    "read": {"*": "deny", "<absolute fixture path>": "allow"},
    "edit": "deny",
    "bash": "deny",
    "glob": "deny",
    "grep": "deny",
    "task": "deny",
    "skill": "deny",
    "question": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": {"*": "deny"},
    "doom_loop": "deny"
  },
  "mcp": {}
}
```

4. Run with explicit argv, `--pure`, and no `--auto` (deny-by-default stays
   enforced; unexpected tool calls are never approved or executed):

```sh
opencode run --pure --format json --dir <harness coding dir> \
  --model mlx-tui-local/local "<message>"
```

Main and auxiliary model selection are both `mlx-tui-local/local`; verify
effective routing from the retained config hash and the `--model` argv
before counting a run. Fetching the OpenCode client or its provider package
is a separate setup step; verify offline inference only after prerequisites
are cached. Results for other coding apps (including Pi) cannot be inferred
from OpenCode runs.
