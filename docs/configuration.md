# Configuration

Configuration is optional. The file is
`$XDG_CONFIG_HOME/mlx-tui/config.toml`, or
`~/.config/mlx-tui/config.toml` when `XDG_CONFIG_HOME` is unset. Explicit
`--host` and `--port` CLI flags take precedence. Press `Ctrl+G` in the TUI to
create a commented template if needed and open the file in `$EDITOR`; it
reloads on save. Restart the TUI after changing host or port.

## Example

```toml
runtime_mode = "attach"  # "attach" (default) or "managed"
model = "mlx-community/Qwen3-1.7B-4bit"  # cold-start target
host = "127.0.0.1"
port = 8080
start_cmd = "mlx_lm.server --port 8080"
stop_cmd = "pkill -f mlx_lm.server"
command_shell = false
temperature = 0.7
top_p = 1.0
max_tokens = 1024
seed = 7
enable_thinking = false
system = "You are a helpful assistant."
max_ctx = 8192
swap_policy = "auto"  # "auto", "warm", or "restart"
```

`max_ctx` is the context budget. `max_context` is a compatibility alias; if
both are present, `max_context` wins. Invalid or unknown `runtime_mode` values
fall back to attach mode. CLI `--managed` and `--attach` select a mode for that
run and are mutually exclusive.

## Server ownership and model switching

**Attach** uses an operator-managed server. The TUI does not stop that server
when it quits. If the endpoint is down and `start_cmd` is configured, the
status bar offers `Ctrl+S` to start it. **Managed** uses the app-owned pinned
runtime at `127.0.0.1:18080`; it owns only the child it started and stops
that child on normal quit. It never adopts a discovered listener. See
[Managed activation](activation.md) for the exact platform requirements,
installation, and recovery behavior.

Commands run as argument vectors by default. Shell metacharacters such as
`;` and `$()` are not executed; quotes only group arguments while parsing.
For `start_cmd`, use
`{model}` or an exact `--model` argument; otherwise the TUI appends `--model`
with the chosen target. Set `command_shell = true` only when you intend shell
execution. In that mode, reference `"$MLX_TUI_MODEL"` instead of `{model}`:

```toml
command_shell = true
start_cmd = "mlx_lm.server --model \"$MLX_TUI_MODEL\" --port 8080"
```

`swap_policy = "auto"` restarts the server and streams its output if both
`start_cmd` and `stop_cmd` are configured, even when the endpoint is healthy.
Otherwise it attempts a warm load against a healthy endpoint. `"warm"`
always requires a healthy endpoint. `"restart"` always requires both
commands. A warm load first records the explicit selection; request-scoped
success requires matching response identity or a follow-up `/v1/models`
probe. Neither the cache catalogue nor a healthy endpoint proves residency.
Server discovery does not give the TUI ownership of an attached process.

The TUI uses plain HTTP to `host:port`, without TLS, an API key, or a
reverse-proxy base path. A non-loopback host is unauthenticated HTTP, not a
secure remote-server setup. See [Local clients](clients.md) before connecting
another client to the same server.

## Presets

Presets live in a sibling file,
`$XDG_CONFIG_HOME/mlx-tui/presets.toml` (or
`~/.config/mlx-tui/presets.toml`):

```toml
[[preset]]
name = "default"
system = "You are helpful."
temperature = 0.7
```

Press `Ctrl+N` / `Ctrl+O` to cycle presets forward / back. `Ctrl+G` reloads
them after editing. Presets are managed through the file and editor; there is
no preset-management screen.
