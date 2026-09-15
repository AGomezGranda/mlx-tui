from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.widgets import Button, TabbedContent

from mlx_tui.search_screen import SearchScreen
from mlx_tui.setup_screen import SetupScreen
from tests.conftest import AppHarness


async def test_keyboard_setup_selects_managed_downloads_and_reaches_compare(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("mlx_tui.setup_screen.config_path", lambda: config_file)
    install_root = tmp_path / "runtime"

    def fake_install(
        *, on_line: Callable[[str], None], cancel_event: threading.Event
    ) -> Path:
        del cancel_event
        on_line("runtime installed")
        return install_root

    monkeypatch.setattr("mlx_tui.setup_screen.install_runtime", fake_install)
    screen = SetupScreen()
    stub_harness.app.push_screen(screen)
    await stub_harness.pilot.pause()

    # The managed choice is initially focused, so Enter is the whole mode step.
    await stub_harness.pilot.press("enter")
    assert stub_harness.app.config.runtime_mode == "managed"
    assert config_file.read_text().startswith('runtime_mode = "managed"')

    context = screen.query_one("#setup-context", Button)
    context.focus()
    await stub_harness.pilot.press("enter")
    assert stub_harness.app.setup_preferences["context_budget"] == "meets intended work"

    install = screen.query_one("#setup-install", Button)
    install.focus()
    await stub_harness.pilot.press("enter")
    assert await stub_harness.wait_for(lambda _: not screen._installing)
    assert "runtime installation verified" in str(
        screen.query_one("#setup-status").render()
    )

    download = screen.query_one("#setup-download", Button)
    download.focus()
    await stub_harness.pilot.press("enter")
    await stub_harness.pilot.pause()
    assert isinstance(stub_harness.app.screen, SearchScreen)
    await stub_harness.pilot.press("escape")
    await stub_harness.pilot.pause()

    compare = screen.query_one("#setup-compare", Button)
    compare.focus()
    await stub_harness.pilot.press("enter")
    await stub_harness.pilot.pause()
    assert stub_harness.app.query_one(TabbedContent).active == "compare"


async def test_install_cancel_waits_for_worker_cleanup(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "mlx_tui.setup_screen.config_path", lambda: tmp_path / "config.toml"
    )
    completed = threading.Event()

    def fake_install(
        *, on_line: Callable[[str], None], cancel_event: threading.Event
    ) -> Path:
        on_line("installing")
        assert cancel_event.wait(5)
        completed.set()
        return tmp_path / "runtime"

    monkeypatch.setattr("mlx_tui.setup_screen.install_runtime", fake_install)
    screen = SetupScreen()
    stub_harness.app.push_screen(screen)
    await stub_harness.pilot.pause()
    screen._select_mode("managed", "managed")
    screen._install()
    assert await stub_harness.wait_for(lambda _: screen._installing)

    await screen.cancel_install_and_wait()
    await stub_harness.pilot.pause()
    assert completed.is_set()
    assert not screen._installing
    assert not stub_harness.app.operations.is_busy
