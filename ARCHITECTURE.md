# Architecture

`mlx-tui` is a Textual control plane for a local MLX OpenAI-compatible server
(`mlx_lm.server` or `mlx_vlm.server`). The supported endpoint is plain HTTP at
`host:port`; there is no TLS, API-key, or reverse-proxy base-path configuration.
The entry point is `mlx_tui.app:main`.

## Application ownership

`MlxTuiApp` in `src/mlx_tui/app/__init__.py` is the coordinator. It owns the
polling loop, endpoint/process identity, status widgets, config editing,
preset cycling, cross-pane operation UI, and the shared log. The only UI-free
lifecycle primitive is `app/operations.py`, whose `OperationCoordinator` holds
one lease for a complete chat, load, restart, or delete worker lifetime.

The app polls `GET /v1/models` every two seconds with a short async HTTPX
timeout. A successful response is green only when it is a 200 response with a
non-empty model list. A completed but unexpected response is amber; transport
failure is red. Unexpected polling faults keep the last known identity and are
reported with bounded, per-source diagnostics rather than being presented as
ordinary downtime.

The current endpoint catalog is the source of model identity. For loopback
servers, `process.py` can add a verified `ProcessIdentity` by matching a
listener on the configured port and the `mlx_lm.server`/`mlx_vlm.server`
token. A pidfile only changes candidate order after listener and process
validation; it is never trusted by itself. Model identity is not inferred from
an arbitrary command-line `--model` value. `MemoryStore` records RSS and
available-memory samples for the Metrics tab.

## Models and server lifecycle

`ModelsPane` owns cache-table population, loaded/fits markers, model selection,
delete confirmation, warm loading, cold starts, and restart workers. The
domain adapter in `models.py` uses public `huggingface_hub` cache types to
scan MLX-compatible repositories and to delete exact revision hashes. Deleting
the active model is refused both before confirmation and again immediately
before cache mutation, covering a selection/identity race.

`swap.py` contains pure swap policy and boot-plan decisions. `serverctl.py`
contains command parsing, streaming subprocess helpers, health checks, and
process cleanup. Commands use argv mode by default (`shlex.split`, no shell
expansion). Exact `--model value` and `--model=value` options are replaced or
deduplicated for a selected target; `{model}` is replaced only after parsing.
`command_shell = true` is an explicit opt-in: the target is passed as
`MLX_TUI_MODEL`, and shell templates must reference `"$MLX_TUI_MODEL"`.

Owned start commands use a new POSIX session. A failed start or stop timeout
terminates only that owned process group, escalating from TERM to KILL within
bounded waits and reaping the direct child. A successfully health-verified
server is left running. UI callbacks and cleanup are guarded so a logging or
teardown error cannot strand the operation lease or leave controls disabled.
Warm loading is accepted only when the response model matches the target, or
when a follow-up `/v1/models` probe verifies it.

## Chat and context

`ChatPane` owns its params inputs, transcript, context bar, async Textual
worker, and cancellation lifecycle. `chat.stream_turn` uses a per-turn
`httpx.AsyncClient` and `async with client.stream`; it closes both client and
response on success, errors, and cancellation. It returns typed token
accounting, TTFT, finish reason, and malformed-frame counts while keeping UI
callbacks synchronous.

`sse.py` implements incremental server-sent-event framing. `SSEDecoder` joins
consecutive `data` fields, preserves payload whitespace, ignores comments and
unknown fields, dispatches only at blank lines, and stops the synchronous and
async wrappers at `[DONE]`. An unfinished event at EOF is discarded.

Before a request, `history/tokens.py` trims complete user-bound turns and
reserves the configured `max_tokens` allowance plus system/protocol overhead.
`max_context` is an alias for `max_ctx` and wins if both are present. The
pending user message is visible in the transcript but is not committed to
future request context until transport succeeds. Successful turns commit the
user and non-empty assistant pair together; failed, truncated, and cancelled
attempts remain visible as notices but do not enter later context. An empty
successful reply retains the user message and reports that the model returned
no text.

Escape is client-side cancellation. The pane records one cancellation intent,
cancels the retained worker after it starts, and keeps the chat lease until
the async request cleanup finishes. It reports “cancellation requested” and
does not claim that the remote generation stopped. Cleanup is idempotent,
including a worker cancelled before its coroutine starts.

Each completed or cancelled turn enters the per-model `HistoryStore` ring of
64 records. Known server usage is displayed directly; missing prompt or
completion counts are estimated and marked `(est)`. When prompt usage is
known, prefill is reported as prompt tokens divided by client-observed TTFT;
decode throughput uses completion tokens over the post-first-text interval.

## Search and downloads

`SearchScreen` owns the search modal, result table, size lookup, revision
selection, download worker, progress, and cancellation acknowledgement.
`search.py` remains UI-free: it lists `mlx-community`, filters the supported
MLX file set, computes disk-fit hints, and wraps `snapshot_download` with a
throttled progress class.

A highlighted result records the Hub revision hash and downloads with that
revision and the same allow patterns used for size estimation. Escape sets a
cooperative event and leaves the modal mounted with “cancellation requested”
until the Hub worker returns. Success, acknowledged cancellation, and errors
share one terminal UI path; cancellation rescans the cache without deleting
partial or completed files. A blocked Hub operation is not promised to stop
immediately.

## Configuration and presets

`config.py` parses the optional TOML file at `$XDG_CONFIG_HOME/mlx-tui/config.toml`
or `~/.config/mlx-tui/config.toml`. Known values require their declared TOML
types, numeric controls are clamped, and invalid files degrade to defaults at
startup. The editor action creates a commented template when needed, runs a
shell-split `$EDITOR` while Textual is suspended, and reloads only after a
successful zero exit and valid TOML parse. Editor launch failures and nonzero
exits are reported without claiming a reload. Host/port changes require a TUI
restart.

`presets.py` reads the sibling `presets.toml`; applying a preset updates the
chat inputs and current config snapshot. Presets are intentionally a flat file
and `$EDITOR` workflow, not a second management subsystem.

## Project layout

```text
src/mlx_tui/
  app/__init__.py          # coordinator, polling, status, config edit, presets
  app/operations.py        # UI-free operation lease
  chat.py                  # async HTTPX stream and typed result
  chat_pane/__init__.py    # chat UI, params, context, worker lifecycle
  confirm.py               # delete confirmation modal
  history/                 # token bounds, turn/memory rings, sparklines
  metrics_pane.py          # metrics table and sparklines
  models.py                # public HF cache adapter and ModelRow
  models_pane/__init__.py  # model UI and lifecycle workers
  process.py               # listener-based process identity and memory
  search.py                # UI-free Hub/search/download adapters
  search_screen/__init__.py # search UI and acknowledged download lifecycle
  serverctl.py             # argv/shell commands, health, process groups
  sse.py                   # incremental SSE decoder and token helpers
  status.py                # endpoint probes and status formatting
  swap.py                  # pure swap policy and boot-plan rules
  table.py                 # model table rendering and key bindings
```

## Verification

The locked project gates are:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check src tests
uv run pyrefly check --min-severity warn
```

CI runs those gates on Ubuntu and macOS 14, reports the macOS architecture,
and runs isolated highest-resolution and declared-direct-floor suites. The
packaging job builds a fresh wheel and sdist, checks license and entry-point
metadata, rebuilds a wheel from the extracted sdist, and installs both wheels
outside the checkout before running `mlx-tui --help` and an import-path check.
