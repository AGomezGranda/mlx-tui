# Development

From a checkout:

```sh
uv sync --locked --dev
uv run mlx-tui --help
uv run ruff check .
uv run ruff format --check src tests
uv run pyrefly check --min-severity warn
uv run pytest -q
uv run python tests/artifact_smoke.py
```

For faster feedback, run `uv run pytest tests/unit`. It skips mounted UI
components but still covers local filesystem and process boundaries. Run
`uv run pytest tests/integration` for mounted UI coverage; small widget tests
belong there too. For full-suite timings and a JUnit report, run
`uv run pytest --durations=20 --junitxml=/tmp/mlx-tui-testing.xml`. Neither
suite verifies compatibility with an actual MLX provider.

For a real-server run, start `mlx_lm.server` in another terminal and use
`uv run mlx-tui --attach --port 8080`. Automated behavior coverage uses a
local HTTP stub, supplemented by installed-package smoke checks; the stub
does not establish behavior on a real server. Implementation layout, state
ownership, polling, and storage contracts are in [Architecture](../ARCHITECTURE.md).

## Scope

The app targets local MLX with one cache layout and one memory model. It does
not provide a quantization runner, multiple backends, or server-log tailing.
Chat sessions are local single-file snapshots without sync, search, or
branching. The endpoint uses plain HTTP without TLS, API keys, or reverse-proxy
base paths; remote secure serving is outside the app's scope.
