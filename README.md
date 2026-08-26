# mlx tui

A one-screen terminal client for a local MLX OpenAI-compatible server
(`mlx_lm.server` / `mlx_vlm.server`): an always-visible status bar (liveness,
loaded model, RSS, available memory), a Models tab over your HF cache, a
streaming chat pane with per-turn TTFT / tok-s instrumentation, and a shared
log pane — you never have to leave the terminal.

## Run

```
uv sync
uv run mlx-tui --port 8080
```

Start a server first:

```
uv tool install mlx-lm
mlx_lm.server --model ornith-ai/Ornith-1.5-9B-MLX-4bit --port 8080
```

## Keys

| Key | Action |
|-----|--------|
| `enter` (Models tab) | Load / swap to the selected model |
| `d` (Models tab) | Delete the selected repo from the HF cache (confirm modal) |
| `/` (Models tab) | Search `mlx-community` (limit 50) — see Search & download |
| `ctrl+s` | Cold start the server from the red dot (`start_cmd` required) |
| `ctrl+g` | Edit `~/.config/mlx-tui/config.toml` in `$EDITOR`, reload on save |
| `esc` | Cancel an in-flight chat turn; also closes the search modal / cancels a download |
| `ctrl+q` | Quit |

Tab switching between Models and Chat works by click or arrow keys.

## Search & download

`/` on the Models tab opens a search modal over `mlx-community` (limit 50).
Type a query and press `enter` to search; arrows navigate results, `enter` on a
result row downloads the selected repo. The exact download size vs free disk
appears as you browse (`1.9 GB ✓` / `⚠` / `—` while loading); a short-disk
warning (`warning: needs … GB, only … GB free — downloading anyway`) never
blocks — an actual download failure is the ground truth. `esc` closes the
modal and cancels an in-flight download (partials resume on the next attempt).
Downloads pull only `*.safetensors`, `*.json`, `tokenizer*`; on completion the
Models table rescans automatically, so the new model is one `enter` away from
loading.

## Config file

`~/.config/mlx-tui/config.toml` (honours `$XDG_CONFIG_HOME`). All keys are
optional; CLI `--host/--port` flags win only when explicitly passed.

```toml
model = "ornith-ai/Ornith-1.5-9B-MLX-4bit"   # seeds cold start
host = "127.0.0.1"
port = 8080
start_cmd = "mlx_lm.server --port 8080"   # {model} placeholder or --model appended
stop_cmd = "pkill -f mlx_lm.server"
pidfile = "/tmp/mlx-server.pid"
```

With `start_cmd` **and** `stop_cmd` configured, swapping restarts the server
with streamed output; otherwise a green server is warm-swapped in-place via a
synchronous probe-load. `start_cmd` may be a long-running foreground server —
its output streams into the log pane while the TUI stays interactive, and the
health poll decides when it is up. Pressing `ctrl+g` creates the file from a
template if it is missing. Note that an in-server load keeps both models
resident briefly —
memory pressure is exactly what the ⚠ fits column warns about.

`ctrl+q` quits.

