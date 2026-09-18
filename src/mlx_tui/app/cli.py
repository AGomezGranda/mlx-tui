"""App CLI entry point (`mlx-tui = "mlx_tui.app:main"`)."""

from __future__ import annotations

import argparse
import signal
from dataclasses import replace
from importlib.metadata import version as package_version
from pathlib import Path

from mlx_tui.app.app import MlxTuiApp
from mlx_tui.config import ConfigParseError, config_path, load_config, parse_config
from mlx_tui.diagnostics import DiagnosticsError, write_diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(prog="mlx-tui")
    parser.add_argument(
        "--version", action="version", version=package_version("mlx-tui")
    )
    parser.add_argument("--diagnostics", metavar="PATH")
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
    cfg = load_config()
    if path.exists():
        try:
            parse_config(path)
        except ConfigParseError as exc:
            config_error = str(exc)
    if args.managed:
        cfg = replace(cfg, runtime_mode="managed")
    elif args.attach:
        cfg = replace(cfg, runtime_mode="attach")
    # CLI flags win only when explicitly passed; otherwise the config file's
    # values apply (which already carry the hardcoded defaults).
    host = args.host if args.host is not None else cfg.host
    port = args.port if args.port is not None else cfg.port
    if cfg.runtime_mode == "managed" and args.host is None and args.port is None:
        host, port = "127.0.0.1", 18080
    app = MlxTuiApp(
        host=host,
        port=port,
        config=cfg,
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
