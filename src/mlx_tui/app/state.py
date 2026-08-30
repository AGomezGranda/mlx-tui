"""Tracked-model state + effective model union."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psutil

from mlx_tui import (
    process,  # module import so monkeypatch of process.find_server_pid propagates
)
from mlx_tui.process import model_from_cmdline
from mlx_tui.swap import SwapState

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class _SwapShim:
    """Legacy shim: keeps SwapState graph API but no validation, proxies to busy."""

    def __init__(self) -> None:
        self.state: SwapState = SwapState.IDLE

    @property
    def busy(self) -> bool:
        return self.state is not SwapState.IDLE

    def transition(self, new: SwapState) -> None:
        self.state = new

    def reset(self) -> None:
        self.state = SwapState.IDLE


def effective_model(app: MlxTuiApp) -> str | None:
    """Tracked warm-swaps win over --model cmdline; gated on live pid."""
    if app._tracked_model is not None:
        return app._tracked_model
    pid = process.find_server_pid(app.config.pidfile)
    if pid is None:
        return None
    try:
        return model_from_cmdline(psutil.Process(pid))
    except psutil.NoSuchProcess:
        return None


def set_tracked_model(app: MlxTuiApp, model: str | None) -> None:
    """UI-thread setter for warm-swap tracking (hop via call_from_thread)."""
    app._tracked_model = model
    app._refresh_metrics()
