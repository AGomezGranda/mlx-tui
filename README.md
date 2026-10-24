# mlx-tui

A terminal interface for running and using a local MLX server (`mlx_lm.server`
or `mlx_vlm.server`). Browse cached models, select a request target, chat with
streaming responses, and compare two pinned coding profiles from one screen.

## What you can do

- **Models:** inspect cached revisions with local memory estimates, discover
  MLX candidates across Hugging Face, select a model, or delete a cached
  repository.
- **Chat:** stream responses, adjust request parameters, attach text files,
  return to locally saved sessions, and switch to a focused Zen view with Ctrl+Z.
- **Compare:** run the fixed `coding-check-v1` task against two pinned profiles,
  inspect the recorded evidence, and explicitly save a choice.
- **Metrics and Activity:** watch live local CPU/memory graphs with sampled app
  activity over the last two minutes, plus recent request latencies and client
  request tok/s. Activity shows server output, downloads, and notices.

The TUI connects to a local MLX server over plain HTTP. It can attach to a
server you run yourself or start an app-owned runtime through the guided setup.

## Quick start

You need macOS with Apple silicon, Python 3.13.1, and
[uv](https://docs.astral.sh/uv/). Install a versioned wheel:

```sh
uv tool install --python 3.13.1 /absolute/path/to/mlx_tui-<version>-py3-none-any.whl
mlx-tui
```

Or run from this checkout:

```sh
uv sync
uv run mlx-tui
```

On first run, Settings opens before connecting. Choose **Attach**, keep
**Enable local server start** selected to use the bundled MLX-LM server, and
**Save**. Download or select a model, then press `Ctrl+S` to start it. Reopen
Settings with `Ctrl+G`; **Runtime setup** provides installation, downloads,
and recovery. You can also attach to a server you already operate.
**Managed** is a separate pinned path
with exact version requirements; see the [activation guide](docs/activation.md).

For a server you run yourself, start it in another terminal, then point the
TUI at the same endpoint:

```sh
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
```

```sh
uv run mlx-tui --attach --host 127.0.0.1 --port 8080
```

In the TUI, select a cached model in **Models** and press `Enter` to use it as
the request target. Open **Chat**, type a prompt, and press `Ctrl+Enter` to send
it (`Enter` inserts a newline). Press `F2` to inspect Activity, `F3` to see
endpoint details, `Ctrl+Z` to enter or leave Zen, and `Ctrl+Q` to quit. The [Zen
mode guide](docs/usage.md#zen-mode) explains its return behavior and compact
context estimate; the [usage guide](docs/usage.md) covers other controls.

Use `uv run mlx-tui --help` for CLI options. Explicit `--host` and `--port`
flags override the config file for this run. `mlx-tui --config-path` prints
the config location without creating it. Settings **Save** changes defaults;
**Apply chat defaults** changes subsequent requests in the current chat.
Runtime edits show **Restart required** until the next launch. See the
[configuration guide](docs/configuration.md) for precedence and migration,
and the [activation guide](docs/activation.md) for runtime installation.

## Documentation

| Guide | Contents |
| --- | --- |
| [Using the TUI](docs/usage.md) | Tabs, keyboard controls, chat, sessions, and attachments |
| [Configuration](docs/configuration.md) | Config file, server commands, model switching, and presets |
| [Installation and managed activation](docs/activation.md) | Runtime requirements, ownership, diagnostics, and upgrades |
| [Comparing profiles](docs/comparison.md) | Setup, runs, evidence, decisions, and saved results |
| [Local clients](docs/clients.md) | Endpoint preview and connecting another HTTP client |
| [Development](docs/development.md) | Local checks and project scope |
| [Architecture](ARCHITECTURE.md) | Implementation and storage contracts |

See the [MIT license](LICENSE).
