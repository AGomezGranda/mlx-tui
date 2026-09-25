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
[uv](https://docs.astral.sh/uv/). From this checkout:

```sh
uv sync
uv run mlx-tui
```

On first run, choose **Attach server** to use the MLX-LM server installed with
the TUI. Download or select a model, then press **Start**. You can also attach
to a server you already operate. **Managed runtime** is a separate pinned path
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
flags override the config file. You can also install a versioned wheel; see
the [activation guide](docs/activation.md).

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
