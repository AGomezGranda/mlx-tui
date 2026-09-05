# mlx-tui

One-screen TUI control plane for a local MLX OpenAI-compatible server (`mlx_lm.server` / `mlx_vlm.server`).

A shared log pane shows server output, download progress and notices. The TUI talks to the server over plain OpenAI-compatible HTTP.

## Features

**Status bar (always visible)** — liveness dot, loaded model, server RSS and available memory, port. When the server is down and a start command is configured it hints `ctrl+s to start`.

**Models tab** — every model in your HF cache with quant label, size on disk, fits-in-memory hint and loaded marker. Load/swap with `enter`, delete with `d`, search `mlx-community` with `/` — all without leaving the terminal.

**Chat tab** — streaming chat with live token preview. Each turn is stamped with prompt/output tokens, tok/s, TTFT and cold-vs-warm tag. Context is trimmed automatically, responses render as markdown, and temperature / top-p / max-tokens are adjustable inline. `esc` cancels an in-flight turn.

**Search & download** — modal HF search (`mlx-community`, 50 results) with quant and disk-fit indicator. Downloads stream progress and resume on retry; on success the Models table rescans automatically.

**Metrics tab** — last 64 turns across all models plus two sparklines: chat throughput (shaded by context depth) and server memory (RSS / available).

## Requirements

* Python 3.13 (`.python-version`) — `uv` manages the toolchain
* [uv](https://docs.astral.sh/uv/) ≥ 0.8
* macOS Apple Silicon for the memory story to be meaningful (unified memory); the chat layer works against any OpenAI-compatible server
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
start_cmd = "mlx_lm.server --port 8080"   # {model} placeholder or --model appended
stop_cmd = "pkill -f mlx_lm.server"
pidfile = "/tmp/mlx-server.pid"
temperature = 0.7
top_p = 1.0
max_tokens = 1024
system = "You are a helpful assistant."
swap_policy = "auto"  # "auto" | "warm" | "restart"
```

With `swap_policy = "auto"` (the default), model swaps restart the server and stream output when both `start_cmd` **and** `stop_cmd` are configured — even when the endpoint is green — otherwise `auto` uses a warm in-server load against a healthy endpoint. `swap_policy = "warm"` always uses warm load and requires a green endpoint; `swap_policy = "restart"` always restarts and requires both `start_cmd` and `stop_cmd`. A warm load is confirmed against `/v1/models` before the loaded marker changes. `ctrl+g` creates a commented template if missing and reloads on save — host/port changes need a TUI restart.

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

* MLX-only by design — one cache layout, one memory model; the chat HTTP layer is accidentally portable to any OpenAI-compatible server.
* No quantization runner, no multi-backend abstraction, no server-log tailing, no conversation persistence beyond in-memory history.
* Presets are file + `$EDITOR` only — no management UI.

## Architecture

Implementation details, polling/timeouts, swap state machine, SSE parsing, storage and project layout: see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

Same as the repo — see `pyproject.toml:2`.
