"""Mounted tests for the ChatPane configuration bridge."""

from __future__ import annotations

from textual.widgets import Input

from mlx_tui.config import AppConfig
from tests.conftest import AppHarness


async def test_apply_config_params_writes_full_config(harness: AppHarness) -> None:
    pane = harness.chat_pane()
    pane.apply_config_params(
        AppConfig(temperature=0.2, top_p=0.6, max_tokens=512, system="Be concise."),
    )
    await harness.pilot.pause()

    assert harness.app.query_one("#param-temp", Input).value == "0.2"
    assert harness.app.query_one("#param-top-p", Input).value == "0.6"
    assert harness.app.query_one("#param-max-tokens", Input).value == "512"


async def test_apply_config_params_clears_optional_values(harness: AppHarness) -> None:
    pane = harness.chat_pane()
    pane.apply_config_params(AppConfig())

    await harness.pilot.pause()
    assert harness.app.query_one("#param-temp", Input).value == "0.7"
    assert harness.app.query_one("#param-top-p", Input).value == "1.0"
    assert harness.app.query_one("#param-max-tokens", Input).value == "1024"


async def test_apply_config_params_uses_parser_defaults(harness: AppHarness) -> None:
    pane = harness.chat_pane()
    pane.apply_config_params(
        AppConfig(temperature=None, top_p=None, max_tokens=None),
    )
    await harness.pilot.pause()

    assert pane._parse_params() == (0.7, 1.0, 1024)
