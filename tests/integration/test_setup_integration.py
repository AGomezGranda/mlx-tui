from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from huggingface_hub import scan_cache_dir
from textual.widgets import Button, Static, TabbedContent

from mlx_tui.models_pane import ModelsPane
from mlx_tui.search import RepoSnapshot
from mlx_tui.search_screen import ResultsTable, SearchScreen
from mlx_tui.setup_screen import SetupScreen
from tests.conftest import AppHarness


async def test_first_run_attach_has_working_start_action(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("mlx_tui.setup_screen.config_path", lambda: config_file)
    screen = SetupScreen()
    stub_harness.app.push_screen(screen)
    await stub_harness.pilot.pause()
    assert screen.query_one("#setup-attach", Button).has_focus

    await stub_harness.pilot.press("enter")
    assert stub_harness.app.config.runtime_mode == "attach"
    command = f"mlx_lm.server --port {stub_harness.app.port}"
    assert stub_harness.app.config.start_cmd == command
    assert f'start_cmd = "{command}"' in config_file.read_text()

    called: list[str] = []

    async def start() -> None:
        called.append(stub_harness.app.config.runtime_mode)

    monkeypatch.setattr(stub_harness.app, "action_cold_start", start)
    screen.query_one("#setup-start", Button).focus()
    await stub_harness.pilot.press("enter")
    assert await stub_harness.wait_for(lambda _: called == ["attach"])


async def test_fresh_install_without_hub_cache_shows_download_needed(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_cache = tmp_path / "huggingface" / "hub"

    def scan_missing() -> object:
        return scan_cache_dir(cache_dir=missing_cache)

    monkeypatch.setattr("mlx_tui.models.scan_cache_dir", scan_missing)
    screen = SetupScreen()
    stub_harness.app.push_screen(screen)
    await stub_harness.pilot.pause()

    details = str(screen.query_one("#setup-mode", Static).render())
    assert "download needed" in details
    assert not missing_cache.exists()


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

    managed = screen.query_one("#setup-managed", Button)
    managed.focus()
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


async def test_modal_discover_preserves_pinned_revision_through_download(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_id = "mlx-community/pinned-model"
    revision = "b" * 40
    observed: dict[str, object] = {}
    config_file = tmp_path / "config.json"
    config_file.write_text("{}")

    def snapshot(
        _api: object,
        _repo_id: str,
        *,
        revision: str | None = None,
    ) -> RepoSnapshot:
        observed["revision"] = revision
        return RepoSnapshot(
            revision=revision,
            files=(("model.safetensors", 2_000_000_000),),
        )

    def download(
        _repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        revision: str | None = None,
        **_kwargs: object,
    ) -> None:
        del on_progress, cancel_event
        observed["download_revision"] = revision

    def download_metadata(
        requested_repo: str, filename: str, *, revision: str | None = None
    ) -> str:
        observed["metadata_request"] = (requested_repo, filename, revision)
        return str(config_file)

    monkeypatch.setattr("mlx_tui.search_screen.repo_snapshot", snapshot)
    monkeypatch.setattr("mlx_tui.search_screen.download_snapshot", download)
    monkeypatch.setattr("mlx_tui.search_screen.hf_hub_download", download_metadata)

    def noop_rescan(_self: ModelsPane) -> None:
        return None

    monkeypatch.setattr(ModelsPane, "rescan", noop_rescan)

    screen = SearchScreen(((repo_id, revision),))
    stub_harness.app.push_screen(screen)
    await stub_harness.pilot.pause()
    assert await stub_harness.wait_for(
        lambda _app: observed.get("revision") == revision
    )
    assert await stub_harness.wait_for(
        lambda _app: repo_id in screen.discover._inspected
    )
    assert observed["metadata_request"] == (repo_id, "config.json", revision)

    table = screen.query_one("#search-results", ResultsTable)
    table.focus()
    await stub_harness.pilot.press("enter")
    assert await stub_harness.wait_for(
        lambda _app: observed.get("download_revision") == revision
    )
    assert await stub_harness.wait_for(
        lambda _app: not isinstance(stub_harness.app.screen, SearchScreen)
    )


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
