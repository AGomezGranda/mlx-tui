"""App CLI entry point (`mlx-tui = "mlx_tui.app:main"`)."""

from __future__ import annotations

import argparse
import signal
from importlib.metadata import version as package_version
from pathlib import Path

from mlx_tui.app.app import MlxTuiApp
from mlx_tui.config import (
    AppConfig,
    ConfigParseError,
    LaunchOverrides,
    config_path,
    resolve_launch,
)
from mlx_tui.config_io import read_config
from mlx_tui.diagnostics import DiagnosticsError, write_diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(prog="mlx-tui")
    parser.add_argument(
        "--version", action="version", version=package_version("mlx-tui")
    )
    parser.add_argument("--diagnostics", metavar="PATH")
    parser.add_argument(
        "--config-path", action="store_true", help="print the config location and exit"
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None, type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--managed", action="store_true", help="use the app-owned runtime"
    )
    mode.add_argument(
        "--attach", action="store_true", help="use an operator-managed server"
    )
    args = parser.parse_args()
    if args.config_path:
        if (
            args.diagnostics is not None
            or args.host is not None
            or args.port is not None
            or args.managed
            or args.attach
        ):
            parser.error(
                "--config-path cannot be combined with diagnostics, endpoint or mode flags"
            )
        print(config_path())
        return
    if args.diagnostics is not None:
        if (
            args.host is not None
            or args.port is not None
            or args.managed
            or args.attach
        ):
            parser.error("--diagnostics cannot be combined with endpoint or mode flags")
        try:
            write_diagnostics(Path(args.diagnostics))
        except DiagnosticsError as exc:
            parser.error(str(exc))
        return
    path = config_path()
    config_error: str | None = None
    document = None
    try:
        document = read_config(path)
    except ConfigParseError as exc:
        config_error = str(exc)
    overrides = LaunchOverrides(
        host=args.host,
        port=args.port,
        runtime_mode="managed" if args.managed else "attach" if args.attach else None,
    )
    cfg = resolve_launch(
        document.config if document is not None else AppConfig(), overrides
    )
    app = MlxTuiApp(
        host=cfg.host,
        port=cfg.port,
        config=cfg,
        config_document=document,
        launch_overrides=overrides,
        needs_setup=not path.exists() and not (args.managed or args.attach),
        config_error=config_error,
    )
    old_handlers = {
        sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
    }

    def request_shutdown(_signum: int, _frame: object) -> None:
        app.exit()

    try:
        for sig in old_handlers:
            signal.signal(sig, request_shutdown)
        app.run()
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        if app.managed_runtime is not None:
            app.managed_runtime.close()
