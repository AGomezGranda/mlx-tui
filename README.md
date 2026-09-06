# mlx-tui

One-screen TUI control plane for a local MLX server (`mlx_lm.server` / `mlx_vlm.server`).

A shared log pane shows server output, download progress and notices. The TUI talks to the server over plain local HTTP (`http://host:port` with no TLS, API key, or reverse-proxy base-path configuration).

## Features

**Status bar (always visible)** — liveness dot, loaded model, server RSS and available memory, port. When the server is down and a start command is configured it hints `ctrl+s to start`.

**Models tab** — every model in your HF cache with quant label, size on disk, fits-in-memory hint and loaded marker. Load/swap with `enter`, delete with `d`, search `mlx-community` with `/` — all without leaving the terminal.

**Chat tab** — streaming chat with live token preview. Each turn is stamped with prompt/output tokens, tok/s, TTFT and cold-vs-warm tag. Context is trimmed automatically, responses render as markdown, and temperature / top-p / max-tokens are adjustable inline. Prompt or completion counts estimated by the client are marked `(est)`; when prompt usage is returned, the stamp also shows client-observed prefill and decode rates. `esc` requests client-side cancellation of an in-flight turn.

**Search & download** — modal HF search (`mlx-community`, 50 results) with quant and disk-fit indicator. Downloads stream progress and resume on retry; on success the Models table rescans automatically.

**Metrics tab** — last 64 turns across all models plus two sparklines: chat throughput (shaded by context depth) and server memory (RSS / available).

## Requirements

* Python 3.13 (`.python-version`) — `uv` manages the toolchain
* [uv](https://docs.astral.sh/uv/) ≥ 0.8
* macOS Apple Silicon for the memory story to be meaningful (unified memory); verified against a local MLX server over plain HTTP
* An MLX server for real use (optional for dev — tests use an ephemeral stub)

```
uv tool install mlx-lm
```

## Quick start

Start a server first:

```
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
```

Run the TUI against it:

```
uv sync
uv run mlx-tui --port 8080
# host defaults to 127.0.0.1; explicit CLI flags win over the config file
uv run mlx-tui --host 127.0.0.1 --port 8080
uv run mlx-tui --help
```

No server yet and `start_cmd` configured? A red dot shows `ctrl+s to start` — see [Configuration](#configuration).

## Key bindings

| Key | Context | Action |
|-----|---------|--------|
| `enter` | Models — row selected | Load / swap to the selected model |
| `d` | Models | Delete the selected repo from the HF cache (with confirm) |
| `/` | Models | Search `mlx-community` |
| `ctrl+s` | Any (red dot + `start_cmd`) | Cold start the server; output streams into the log pane |
| `ctrl+g` | Any | Open `~/.config/mlx-tui/config.toml` in `$EDITOR`, reload on save |
| `ctrl+n` | Any | Cycle presets forward |
| `ctrl+o` | Any | Cycle presets back |
| `esc` | Any | Cancel in-flight chat / close search / cancel download |
| `ctrl+q` | Any | Quit |

Tab switching between Models, Chat and Metrics works by click or arrow keys.

## Configuration

All config is optional; CLI `--host`/`--port` win when explicitly passed. Config lives at `~/.config/mlx-tui/config.toml` (honours `$XDG_CONFIG_HOME`).

```toml
model = "mlx-community/Qwen3-1.7B-4bit"   # seeds cold start target
host = "127.0.0.1"
port = 8080
start_cmd = "mlx_lm.server --port 8080"   # argv mode: {model} or exact --model managed, else --model appended
stop_cmd = "pkill -f mlx_lm.server"
command_shell = false  # true: run start_cmd/stop_cmd via shell with $MLX_TUI_MODEL
# shell example: command_shell = true
# start_cmd = "mlx_lm.server --model \"$MLX_TUI_MODEL\" --port 8080"
pidfile = "/tmp/mlx-server.pid"
temperature = 0.7
top_p = 1.0
max_tokens = 1024
system = "You are a helpful assistant."
swap_policy = "auto"  # "auto" | "warm" | "restart"
```

Commands run as argv by default: no shell expansion, `;`, `$()`, and quotes stay literal argument data. Set `command_shell = true` to opt into shell execution; shell commands must reference `"$MLX_TUI_MODEL"` instead of `{model}` and receive the exact target in the environment.

`max_ctx` is the context budget and `max_context` is its compatibility alias. If both are present, `max_context` wins. The request reserves the configured `max_tokens` allowance plus protocol/system overhead before trimming complete user-bound turns; an over-sized request is rejected with a visible notice instead of being sent.

With `swap_policy = "auto"` (the default), model swaps restart the server and stream output when both `start_cmd` **and** `stop_cmd` are configured — even when the endpoint is green — otherwise `auto` uses a warm in-server load against a healthy endpoint. `swap_policy = "warm"` always uses warm load and requires a green endpoint; `swap_policy = "restart"` always restarts and requires both `start_cmd` and `stop_cmd`. A warm load is accepted from the response model when provided, or from a follow-up `/v1/models` probe that reports the target, before the loaded marker changes. `ctrl+g` creates a commented template if missing and reloads on save — host/port changes need a TUI restart.

Chat history is transactional: the attempted user message remains visible in the transcript, but a user/assistant pair enters the next request's context only after a successful response. Failed, truncated, and cancelled requests are excluded from future context; an empty successful reply retains the user message and shows a notice.

Escape during chat shows “cancellation requested” until the HTTPX request has cleaned up. This is a client-side cancellation and does not claim that the server stopped generating. Download Escape follows the same honest model: the modal remains open until the Hub worker acknowledges cancellation, then it rescans completed cache content and closes.

**Params** — Chat tab has a collapsed `Params` panel with three inputs (Temperature 0–2, Top-p 0–1, Max tokens 1–16384). Values apply to the next turn.

**Presets** — flat sibling file `~/.config/mlx-tui/presets.toml`:

```toml
[[preset]]
name = "default"
system = "You are helpful."
temperature = 0.7
```

`ctrl+n` / `ctrl+o` cycles presets; `ctrl+g` reloads them.

## Develop locally

```bash
git clone <repo>
cd mlx-tui
uv sync --dev          # creates .venv, installs deps
uv run mlx-tui --help
uv run mlx-tui --port 8080

# real server in another terminal
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
```

Tests / lint / typecheck — same gates as CI:

```bash
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -q
```

## Notes & scope

* Local MLX only by design — one cache layout, one memory model, plain HTTP to `host:port` with no TLS, API key, or base-path support; remote/secure servers are not supported. A non-loopback host is best-effort unauthenticated HTTP, not a secure remote-server feature.
* No quantization runner, no multi-backend abstraction, no server-log tailing, no conversation persistence beyond in-memory history.
* Presets are file + `$EDITOR` only — no management UI.

## Architecture

Implementation details, polling/timeouts, swap state machine, SSE parsing, storage and project layout: see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

See the [MIT license](LICENSE).
