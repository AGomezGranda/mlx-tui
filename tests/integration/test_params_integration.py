"""Mounted tests for the ChatPane configuration bridge."""

from __future__ import annotations

from typing import override

from textual.app import App, ComposeResult
from textual.widgets import Input, TabbedContent

from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.params import ParamsPane
from tests.conftest import AppHarness


class ParamsTestApp(App[None]):
    @override
    def compose(self) -> ComposeResult:
        yield ParamsPane()


async def test_apply_config_params_writes_full_config(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    pane = harness.chat_pane()
    pane.apply_config_params(
        AppConfig(temperature=0.2, top_p=0.6, max_tokens=512, system="Be concise."),
    )
    await harness.pilot.pause()

    assert harness.app.query_one("#param-temp", Input).value == "0.2"
    assert harness.app.query_one("#param-top-p", Input).value == "0.6"
    assert harness.app.query_one("#param-max-tokens", Input).value == "512"


async def test_apply_config_params_clears_optional_values(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    pane = harness.chat_pane()
    pane.apply_config_params(AppConfig())

    await harness.pilot.pause()
    assert harness.app.query_one("#param-temp", Input).value == "0.7"
    assert harness.app.query_one("#param-top-p", Input).value == "1.0"
    assert harness.app.query_one("#param-max-tokens", Input).value == "1024"


async def test_apply_config_params_uses_parser_defaults(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    pane = harness.chat_pane()
    pane.apply_config_params(
        AppConfig(temperature=None, top_p=None, max_tokens=None),
    )
    await harness.pilot.pause()

    assert harness.app.query_one(ParamsPane).read_values() == (0.7, 1.0, 1024)


async def test_params_pane_applies_partial_values() -> None:
    app = ParamsTestApp()
    async with app.run_test() as pilot:
        params = app.query_one(ParamsPane)
        params.apply_values(0.2, None, 512)
        await pilot.pause()

        assert params.read_values() == (0.2, 1.0, 512)
        assert app.query_one("#param-top-p", Input).value == "1.0"


async def test_params_pane_normalizes_nonfinite_and_out_of_range_values() -> None:
    app = ParamsTestApp()
    async with app.run_test() as pilot:
        params = app.query_one(ParamsPane)
        app.query_one("#param-temp", Input).value = "nan"
        app.query_one("#param-top-p", Input).value = "-inf"
        app.query_one("#param-max-tokens", Input).value = "0"
        await pilot.pause()

        assert params.read_values() == (0.7, 1.0, 1)


async def test_params_submission_stays_with_params_pane(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    params = harness.app.query_one(ParamsPane)
    harness.app.query_one(TabbedContent).active = "chat"
    params.collapsed = False
    await harness.pilot.pause()
    temp = harness.app.query_one("#param-temp", Input)
    temp.value = "nan"
    temp.focus()
    await harness.pilot.press("enter")

    assert params.read_values() == (0.7, 1.0, 1024)
    assert not harness.app.query_one("#chat-input", ChatInput).disabled
    assert not harness.server.requests


async def test_native_params_collapse_retains_values(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    harness.app.query_one(TabbedContent).active = "chat"
    params = harness.app.query_one(ParamsPane)
    temp = params.query_one("#param-temp", Input)
    await harness.pilot.pause()
    assert params.size.height == 1
    title = params.query_one("CollapsibleTitle")
    title.focus()
    await harness.pilot.press("tab")
    assert harness.app.focused is not temp
    assert temp not in harness.app.screen.focus_chain
    title.focus()
    await harness.pilot.press("enter")
    temp.value = "0.25"
    await harness.pilot.pause()
    assert "temp 0.25" in params.title
    params.collapsed = True
    await harness.pilot.pause()
    params.collapsed = False
    await harness.pilot.pause()
    assert temp.value == "0.25"
    title.focus()
    for field in ("#param-temp", "#param-top-p", "#param-max-tokens"):
        await harness.pilot.press("tab")
        assert harness.app.focused is params.query_one(field)
        assert harness.app.screen.can_view_entire(params.query_one(field))
    assert not harness.server.requests
