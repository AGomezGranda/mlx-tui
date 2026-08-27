"""Preset apply/cycle — ChatPane param bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.css.query import NoMatches

from mlx_tui.chat_pane import ChatPane
from mlx_tui.config import AppConfig
from mlx_tui.presets import Preset

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


def apply_preset(app: MlxTuiApp, preset: Preset) -> None:
    try:
        pane = app.query_one(ChatPane)
    except NoMatches:
        return
    pane.set_system_prompt(preset.system)
    pane._apply_params_to_inputs(preset.temperature, preset.top_p, preset.max_tokens)
    # sync config snapshot
    temp, top_p, max_tok = pane._parse_params()
    app.config = AppConfig(
        model=app.config.model,
        host=app.config.host,
        port=app.config.port,
        start_cmd=app.config.start_cmd,
        stop_cmd=app.config.stop_cmd,
        pidfile=app.config.pidfile,
        temperature=temp,
        top_p=top_p,
        max_tokens=max_tok,
        system=preset.system or None,
    )
    app.log_app(f"preset: {preset.name}", "dim")


def cycle_preset(app: MlxTuiApp, step: int) -> None:
    if not app.presets:
        app.log_app("no presets — create ~/.config/mlx-tui/presets.toml", "yellow")
        return
    app.preset_idx = (app.preset_idx + step) % len(app.presets)
    apply_preset(app, app.presets[app.preset_idx])
