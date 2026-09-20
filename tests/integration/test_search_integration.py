"""Integration tests for embedded and modal HF discovery (Phase 3)."""

from __future__ import annotations

import threading
import time

import pytest
from textual.coordinate import Coordinate
from textual.widgets import Input, Select, Static, TabbedContent

from mlx_tui.discover_pane import DiscoverPane
from mlx_tui.models_pane import ModelsPane
from mlx_tui.operations import OperationKind
from mlx_tui.search import (
    ALLOW_PATTERNS,
    CancelledDownload,
    RepoSnapshot,
    filtered_download_size,
)
from mlx_tui.search_screen import ResultsTable, SearchScreen
from mlx_tui.table import ModelsTable
from tests.conftest import AppHarness

ROW = "mlx-community/stub-test-4bit"
SECOND_ROW = "other-publisher/second-model"
SIZE_PAIRS = [("model.safetensors", 2_000_000_000), ("README.md", 10)]


def _no_schedule_metadata(_self: DiscoverPane, _repo_id: str) -> None:
    return


def _downloading(screen: DiscoverPane) -> str | None:
    """Fresh-read helper: the checker must not narrow this across mutations."""
    return screen._downloading


async def open_search(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> DiscoverPane:
    def _stub_list(api: object, q: str) -> list[str]:
        return [ROW]

    def _stub_snapshot(api: object, rid: str) -> RepoSnapshot:
        return RepoSnapshot(revision="rev-a", files=tuple(SIZE_PAIRS))

    def _stub_free() -> int:
        return 10 * 2**30

    monkeypatch.setattr("mlx_tui.discover_pane.list_results", _stub_list)
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", _stub_snapshot)
    monkeypatch.setattr("mlx_tui.discover_pane.free_disk_bytes", _stub_free)
    # Focus the models table so slash binding fires deterministically.
    try:
        harness.app.query_one("#models-table", ModelsTable).focus()
    except Exception:
        pass
    await harness.pilot.press("/")
    # Pilot may need pause for screen push
    await harness.pilot.pause()
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is not None
    ), f"discover pane never opened; screen={harness.app.screen!r}"
    screen = harness.app.query_one(ModelsPane)._discover_pane
    assert screen is not None
    return screen


def _static_plain(widget: Static) -> str:
    content = widget.content
    # Text, str, or Content
    try:
        # rich.text.Text has .plain
        plain = getattr(content, "plain", None)
        if isinstance(plain, str):
            return plain
        # Content may have .plain or str
        if plain is not None:
            return str(plain)
    except Exception:
        pass
    try:
        return str(content)
    except Exception:
        return ""


async def test_open_query_and_browse(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)

    # type query and submit
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1), (
        f"_repo_ids never filled; got {screen._repo_ids!r}"
    )
    table = screen.query_one("#search-results", ResultsTable)
    # Primary columns are model, compatibility, memory fit, download, quant.
    assert table.get_cell_at(Coordinate(0, 0)) == ROW
    assert table.get_cell_at(Coordinate(0, 4)) == "4bit"

    # lazy size fetch should populate
    assert await harness.wait_for(lambda app: ROW in screen._sizes), (
        f"size not fetched; _sizes={screen._sizes!r}"
    )
    # allow fill to propagate to table
    assert await harness.wait_for(
        lambda app: table.get_cell_at(Coordinate(0, 3)) == "1.9 GiB ✓"
    ), f"download cell not updated; got {table.get_cell_at(Coordinate(0, 3))!r}"

    await harness.pilot.press("escape")
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is None
    ), "search screen did not close on escape"


async def test_discover_controls_fit_narrow_terminal(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)
    await harness.pilot.resize_terminal(80, 24)
    await harness.pilot.pause()
    for selector in (
        "#search-input",
        "#discover-task",
        "#search-results",
    ):
        widget = screen.query_one(selector)
        assert widget.region.x >= 0
        assert widget.region.right <= 80
        assert widget.region.y >= 0
        assert widget.region.bottom <= 24


async def test_discover_unknown_filter_keeps_unassessed_models(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)
    search_input = screen.query_one("#search-input", Input)
    search_input.focus()
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: screen._repo_ids == [ROW])
    scope = screen.query_one("#discover-scope", Select)
    scope.value = "recommended"
    assert await harness.wait_for(lambda app: screen._repo_ids == [])
    scope.value = "unknown"
    assert await harness.wait_for(lambda app: screen._repo_ids == [ROW])


async def test_empty_query_keeps_table_empty(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)

    await harness.pilot.press("enter")

    def status_has_hint(app) -> bool:  # type: ignore[no-untyped-def]
        try:
            static = screen.query_one("#search-status", Static)
        except Exception:
            return False
        plain = _static_plain(static)
        return "type a search" in plain

    assert await harness.wait_for(status_has_hint), (
        f"status hint never appeared; plain={_static_plain(screen.query_one('#search-status', Static))!r}"
    )
    assert screen._repo_ids == []
    table = screen.query_one("#search-results", ResultsTable)
    assert table.row_count == 0


async def test_inspected_details_follow_the_highlighted_row(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr(DiscoverPane, "_schedule_metadata", _no_schedule_metadata)
    screen._populate(screen._search_generation, [ROW, SECOND_ROW])
    screen._fill_size_cell(screen._search_generation, ROW, 10, "—", "rev-a")
    table = screen.query_one("#search-results", ResultsTable)

    table.move_cursor(row=1)
    await harness.pilot.pause()
    screen._fill_size_cell(screen._search_generation, SECOND_ROW, 20, "—", "rev-b")
    assert SECOND_ROW in _static_plain(screen.query_one("#discover-details", Static))

    table.move_cursor(row=0)
    await harness.pilot.pause()
    details = _static_plain(screen.query_one("#discover-details", Static))
    assert f"{ROW}@rev-a" in details
    assert SECOND_ROW not in details


async def test_late_metadata_for_another_row_does_not_replace_selected_details(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr(DiscoverPane, "_schedule_metadata", _no_schedule_metadata)
    screen._populate(screen._search_generation, [ROW, SECOND_ROW])
    screen._fill_size_cell(screen._search_generation, ROW, 10, "—", "rev-a")
    screen._fill_size_cell(screen._search_generation, SECOND_ROW, 20, "—", "rev-b")
    details = _static_plain(screen.query_one("#discover-details", Static))
    assert f"{ROW}@rev-a" in details
    assert SECOND_ROW not in details


async def test_filtering_to_no_rows_clears_discover_details(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr(DiscoverPane, "_schedule_metadata", _no_schedule_metadata)
    screen._populate(screen._search_generation, [ROW])
    screen._fill_size_cell(screen._search_generation, ROW, 10, "—", "rev-a")
    assert ROW in _static_plain(screen.query_one("#discover-details", Static))

    screen.query_one("#discover-scope", Select).value = "recommended"
    await harness.pilot.pause()
    assert "Select a model" in _static_plain(
        screen.query_one("#discover-details", Static)
    )


# ---------------------------------------------------------------------------
# Phase 3 — download flow
# ---------------------------------------------------------------------------


async def test_enter_downloads_hands_off_and_dismisses(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def _recording_download(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        captured["repo_id"] = repo_id
        captured["on_progress"] = on_progress
        captured["cancel_event"] = cancel_event
        captured["cache_dir"] = cache_dir
        captured["revision"] = revision
        # also record allow_patterns if somehow passed (defensive)
        if "allow_patterns" in _kw:
            captured["allow_patterns"] = _kw["allow_patterns"]
        # simulate successful download

    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _recording_download)

    calls: list[int] = []

    def _spy_rescan(self: ModelsPane) -> None:
        calls.append(1)

    monkeypatch.setattr(ModelsPane, "rescan", _spy_rescan)

    screen = await open_search(harness, monkeypatch)

    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1), (
        f"_repo_ids never filled; got {screen._repo_ids!r}"
    )
    # ensure table is focused and size fetched (not strictly required)
    assert await harness.wait_for(lambda app: ROW in screen._sizes), "size not fetched"
    # trigger download via enter on the results table
    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda app: (
            "✓ downloaded mlx-community/stub-test-4bit" in harness.app_log_lines()
        )
    ), f"log lines: {harness.app_log_lines()!r}"

    assert captured.get("repo_id") == ROW
    assert captured.get("revision") == "rev-a"
    assert screen._revisions.get(ROW) == "rev-a"
    # The wrapper pins ALLOW_PATTERNS internally; if the fake received it, check identity.
    # Otherwise verify the constant itself includes the MLX-LM loader set.
    if "allow_patterns" in captured:
        assert captured["allow_patterns"] is ALLOW_PATTERNS
    else:
        assert "*.jinja" in ALLOW_PATTERNS and "*.py" in ALLOW_PATTERNS
    assert calls == [1]
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is None
    ), "search screen did not dismiss after success"


async def test_escape_mid_download_cancels(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _blocking_download(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        # Signal that we are in the blocking section; loop until cancelled.
        assert cancel_event is not None
        while not cancel_event.is_set():
            time.sleep(0.01)
        raise CancelledDownload("cancelled by user")  # noqa: PLC0415  # type: ignore[no-untyped-call]

    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _blocking_download)

    screen = await open_search(harness, monkeypatch)
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1), (
        f"_repo_ids never filled; got {screen._repo_ids!r}"
    )
    assert await harness.wait_for(lambda app: ROW in screen._sizes), "size not fetched"

    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda app: screen._downloading == ROW), (
        f"downloading not set; got {screen._downloading!r}"
    )
    ev = screen._cancel_event
    assert ev is not None

    await harness.pilot.press("escape")
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is None
    ), "search screen did not close on escape"
    # event should be set by action_close_screen
    assert await harness.wait_for(lambda app: ev.is_set()), "cancel event not set"
    assert await harness.wait_for(
        lambda app: any(
            "download cancelled" in line for line in harness.app_log_lines()
        )
    ), f"log lines: {harness.app_log_lines()!r}"


async def test_download_failure_keeps_modal_usable(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing_download(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _failing_download)

    screen = await open_search(harness, monkeypatch)
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1), (
        f"_repo_ids never filled; got {screen._repo_ids!r}"
    )
    assert await harness.wait_for(lambda app: ROW in screen._sizes), "size not fetched"

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda app: any(
            "download failed: RuntimeError: disk full" in line
            for line in harness.app_log_lines()
        )
    ), f"log lines: {harness.app_log_lines()!r}"

    # modal stays open
    assert harness.app.query_one(ModelsPane)._discover_pane is screen
    # widgets re-enabled
    assert screen.query_one("#search-input", Input).disabled is False
    assert screen.query_one("#search-results", ResultsTable).disabled is False

    # red-styled failure line in #dl-progress — fall back to plain text check
    dl_plain = _static_plain(screen.query_one("#dl-progress", Static))
    assert "download failed" in dl_plain, f"dl-progress plain={dl_plain!r}"


async def test_tab_switch_keeps_inline_download_owned_until_completion(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()

    def _blocking_download(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        del repo_id, on_progress, cache_dir, revision
        started.set()
        assert cancel_event is not None
        while not cancel_event.is_set():
            time.sleep(0.01)
        raise CancelledDownload("cancelled by user")

    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _blocking_download)
    screen = await open_search(harness, monkeypatch)
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda app: started.is_set())
    assert harness.app.operations.current is OperationKind.DOWNLOADING

    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    assert harness.app.query_one(ModelsPane)._discover_pane is screen
    assert harness.app.operations.current is OperationKind.DOWNLOADING

    await harness.pilot.press("escape")
    assert await harness.wait_for(
        lambda app: harness.app.query_one(ModelsPane)._discover_pane is None
    )
    assert harness.app.operations.current == OperationKind.IDLE


async def test_low_disk_warns_then_proceeds(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    # low disk: 1 GB free, size is 2 GB → fits_disk False → warning but proceed
    def _stub_list_low(api: object, q: str) -> list[str]:
        return [ROW]

    def _stub_snapshot_low(api: object, rid: str) -> RepoSnapshot:
        return RepoSnapshot(revision="rev-a", files=tuple(SIZE_PAIRS))

    def _stub_free_low() -> int:
        return 1_000_000_000

    captured: dict[str, object] = {}

    def _recording_download_low(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        captured["repo_id"] = repo_id
        captured["revision"] = revision
        # Hold the modal open briefly so the warning line is observable
        # before the success log + dismiss.
        time.sleep(0.35)

    monkeypatch.setattr("mlx_tui.discover_pane.list_results", _stub_list_low)
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", _stub_snapshot_low)
    monkeypatch.setattr("mlx_tui.discover_pane.free_disk_bytes", _stub_free_low)
    monkeypatch.setattr(
        "mlx_tui.discover_pane.download_snapshot", _recording_download_low
    )

    # open manually (cannot use helper which patches free to 10GB)
    try:
        harness.app.query_one("#models-table", ModelsTable).focus()
    except Exception:
        pass
    await harness.pilot.press("/")
    await harness.pilot.pause()
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is not None
    )
    screen = harness.app.query_one(ModelsPane)._discover_pane
    assert screen is not None

    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1)

    # ensure size fetched (will use low free → glyph ⚠ but not asserted)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)

    await harness.pilot.press("enter")

    # warning line appears in #dl-progress
    def warning_present(app) -> bool:  # type: ignore[no-untyped-def]
        try:
            plain = _static_plain(screen.query_one("#dl-progress", Static))
        except Exception:
            return False
        return "warning: needs 1.9 GiB" in plain

    assert await harness.wait_for(warning_present), (
        f"warning not found; plain={_static_plain(screen.query_one('#dl-progress', Static))!r}"
    )
    # and eventual success log
    assert await harness.wait_for(
        lambda app: any("✓ downloaded" in line for line in harness.app_log_lines())
    ), f"log lines: {harness.app_log_lines()!r}"
    assert captured.get("repo_id") == ROW
    assert captured.get("revision") == "rev-a"


async def test_size_and_download_share_revision_and_expanded_patterns(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    expanded = [
        ("model.safetensors", 1000),
        ("config.json", 100),
        ("tokenizer.json", 200),
        ("modeling_custom.py", 300),
        ("o200k_base.tiktoken", 400),
        ("tiktoken.model", 500),
        ("vocab.txt", 600),
        ("metadata.jsonl", 700),
        ("chat_template.jinja", 800),
        ("README.md", 9999),
    ]
    expected_size = filtered_download_size(expanded)
    # sanity: expanded loader files are counted, README is not
    assert expected_size == 1000 + 100 + 200 + 300 + 400 + 500 + 600 + 700 + 800

    def _stub_list(api: object, q: str) -> list[str]:
        return [ROW]

    def _stub_snapshot(api: object, rid: str) -> RepoSnapshot:
        return RepoSnapshot(revision="rev-a", files=tuple(expanded))

    def _stub_free() -> int:
        return 10 * 2**30

    captured: dict[str, object] = {}

    def _recording_download(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        captured["repo_id"] = repo_id
        captured["revision"] = revision

    monkeypatch.setattr("mlx_tui.discover_pane.list_results", _stub_list)
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", _stub_snapshot)
    monkeypatch.setattr("mlx_tui.discover_pane.free_disk_bytes", _stub_free)
    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _recording_download)

    def _noop_rescan(self: ModelsPane) -> None:
        return None

    monkeypatch.setattr(ModelsPane, "rescan", _noop_rescan)

    try:
        harness.app.query_one("#models-table", ModelsTable).focus()
    except Exception:
        pass
    await harness.pilot.press("/")
    await harness.pilot.pause()
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is not None
    )
    screen = harness.app.query_one(ModelsPane)._discover_pane
    assert screen is not None

    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)
    assert screen._sizes[ROW] == expected_size
    assert screen._revisions.get(ROW) == "rev-a"

    await harness.pilot.press("enter")
    assert await harness.wait_for(
        lambda app: any("✓ downloaded" in line for line in harness.app_log_lines())
    )
    assert captured.get("repo_id") == ROW
    assert captured.get("revision") == "rev-a"


async def test_cancel_pending_until_acknowledged(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    ack = threading.Event()
    started = threading.Event()

    def _ack_download(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        assert cancel_event is not None
        started.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        assert ack.wait(timeout=5)
        raise CancelledDownload("cancelled by user")  # noqa: PLC0415  # type: ignore[no-untyped-call]

    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _ack_download)
    rescans: list[int] = []

    def _spy_rescan(self: ModelsPane) -> None:
        rescans.append(1)

    monkeypatch.setattr(ModelsPane, "rescan", _spy_rescan)

    screen = await open_search(harness, monkeypatch)
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda app: screen._downloading == ROW)
    assert await harness.wait_for(lambda app: started.is_set())
    ev = screen._cancel_event
    assert ev is not None

    await harness.pilot.press("escape")
    assert await harness.wait_for(lambda app: ev.is_set())
    await harness.pilot.pause()

    def pending_line() -> str:
        try:
            return _static_plain(screen.query_one("#dl-progress", Static))
        except Exception:
            return ""

    assert await harness.wait_for(
        lambda app: "cancellation requested" in pending_line()
    )
    assert harness.app.query_one(ModelsPane)._discover_pane is screen
    assert screen._downloading == ROW
    assert screen.query_one("#search-input", Input).disabled is True
    assert not any("download cancelled" in line for line in harness.app_log_lines())
    assert not any("✓ downloaded" in line for line in harness.app_log_lines())

    await harness.pilot.press("enter")
    await harness.pilot.pause()
    assert screen._downloading == ROW
    assert not any("download cancelled" in line for line in harness.app_log_lines()), (
        "second download must not start while pending"
    )

    ack.set()
    assert await harness.wait_for(
        lambda app: any(
            "download cancelled" in line for line in harness.app_log_lines()
        )
    )
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is None
    )
    assert _downloading(screen) is None
    assert screen._cancel_event is None
    assert rescans == [1]
    assert (
        len([line for line in harness.app_log_lines() if "download cancelled" in line])
        == 1
    )
    assert (
        len(
            [
                line
                for line in harness.app_log_lines()
                if "cancellation requested" in line
            ]
        )
        == 1
    )


async def test_repeated_escape_single_request(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    ack = threading.Event()
    started = threading.Event()

    def _ack_download(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        assert cancel_event is not None
        started.set()
        while not cancel_event.is_set():
            time.sleep(0.01)
        assert ack.wait(timeout=5)
        raise CancelledDownload("cancelled by user")  # noqa: PLC0415  # type: ignore[no-untyped-call]

    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _ack_download)

    screen = await open_search(harness, monkeypatch)
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda app: screen._downloading == ROW)
    assert await harness.wait_for(lambda app: started.is_set())

    await harness.pilot.press("escape")
    await harness.pilot.press("escape")
    await harness.pilot.press("escape")
    await harness.pilot.pause()
    assert harness.app.query_one(ModelsPane)._discover_pane is screen
    assert screen._downloading == ROW

    ack.set()
    assert await harness.wait_for(
        lambda app: any(
            "download cancelled" in line for line in harness.app_log_lines()
        )
    )
    assert await harness.wait_for(
        lambda app: app.query_one(ModelsPane)._discover_pane is None
    )
    await harness.pilot.pause()
    assert (
        len(
            [
                line
                for line in harness.app_log_lines()
                if "cancellation requested" in line
            ]
        )
        == 1
    )
    assert (
        len([line for line in harness.app_log_lines() if "download cancelled" in line])
        == 1
    )


async def test_error_before_ack_reports_failure_not_cancel(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_after_cancel(
        repo_id: str,
        *,
        on_progress=None,  # type: ignore[no-untyped-def]
        cancel_event=None,  # type: ignore[no-untyped-def]
        cache_dir=None,  # type: ignore[no-untyped-def]
        revision=None,  # type: ignore[no-untyped-def]
        **_kw: object,
    ) -> None:
        assert cancel_event is not None
        while not cancel_event.is_set():
            time.sleep(0.01)
        raise RuntimeError("disk full")

    monkeypatch.setattr("mlx_tui.discover_pane.download_snapshot", _fail_after_cancel)

    screen = await open_search(harness, monkeypatch)
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda app: screen._downloading == ROW)

    await harness.pilot.press("escape")
    assert await harness.wait_for(
        lambda app: any(
            "download failed: RuntimeError: disk full" in line
            for line in harness.app_log_lines()
        )
    )
    assert harness.app.query_one(ModelsPane)._discover_pane is screen
    assert screen.query_one("#search-input", Input).disabled is False
    assert not any("download cancelled" in line for line in harness.app_log_lines())
    dl_plain = _static_plain(screen.query_one("#dl-progress", Static))
    assert "download failed" in dl_plain


async def test_teardown_sets_event_without_fabricated_outcome(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    screen = await open_search(harness, monkeypatch)
    ev = threading.Event()
    screen._cancel_event = ev
    screen._downloading = ROW
    screen.on_unmount()
    assert ev.is_set()


async def test_size_lookup_failure_logs_once_with_repo_id(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    screen = await open_search(harness, monkeypatch)

    def failing_snapshot(api: object, rid: str) -> RepoSnapshot:
        raise OSError("disk gone")

    def working_snapshot(api: object, rid: str) -> RepoSnapshot:
        return RepoSnapshot(revision="rev-a", files=tuple(SIZE_PAIRS))

    def size_failed(app: object) -> bool:
        return harness.app._last_errors.get(f"size {ROW}") == "OSError: disk gone"

    def size_failed_lines() -> list[str]:
        return [
            t
            for t in harness.app_log_lines()
            if f"size {ROW} failed" in t and "OSError" in t
        ]

    # Submit first so the row exists; the automatic fetch succeeds.
    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: len(screen._repo_ids) == 1)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)

    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", failing_snapshot)
    del screen._sizes[ROW]
    screen._fetch_size(ROW, screen._search_generation)
    assert await harness.wait_for(size_failed), harness.app_log_lines()
    assert ROW not in screen._sizes
    # A repeated identical failure stays silent.
    screen._fetch_size(ROW, screen._search_generation)
    await harness.pilot.pause()
    await harness.pilot.pause()
    assert len(size_failed_lines()) == 1

    # A successful fetch clears the source; recurrence becomes visible again.
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", working_snapshot)
    screen._fetch_size(ROW, screen._search_generation)
    assert await harness.wait_for(lambda app: ROW in screen._sizes)
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", failing_snapshot)
    del screen._sizes[ROW]
    screen._fetch_size(ROW, screen._search_generation)
    assert await harness.wait_for(lambda app: len(size_failed_lines()) == 2), (
        harness.app_log_lines()
    )


async def test_old_search_completion_cannot_replace_new_results(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_started = threading.Event()
    release_old = threading.Event()

    def delayed_list(api: object, query: str) -> list[str]:
        if query == "old":
            old_started.set()
            assert release_old.wait(timeout=5)
            return ["old/repo"]
        return ["new/repo"]

    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr("mlx_tui.discover_pane.list_results", delayed_list)
    search_input = screen.query_one("#search-input", Input)

    search_input.focus()
    await harness.pilot.press(*"old", "enter")
    assert await harness.wait_for(lambda app: old_started.is_set())

    search_input.value = ""
    search_input.focus()
    await harness.pilot.press(*"new", "enter")
    assert await harness.wait_for(lambda app: screen._repo_ids == ["new/repo"])

    release_old.set()
    assert await harness.wait_for(lambda app: screen._repo_ids == ["new/repo"])
    table = screen.query_one("#search-results", ResultsTable)
    assert table.row_count == 1
    assert table.get_cell_at(Coordinate(0, 0)) == "new/repo"


async def test_empty_submit_invalidates_pending_search(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_started = threading.Event()
    release_old = threading.Event()

    def delayed_list(api: object, query: str) -> list[str]:
        old_started.set()
        assert release_old.wait(timeout=5)
        return ["old/repo"]

    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr("mlx_tui.discover_pane.list_results", delayed_list)
    search_input = screen.query_one("#search-input", Input)

    search_input.focus()
    await harness.pilot.press(*"old", "enter")
    assert await harness.wait_for(lambda app: old_started.is_set())

    search_input.value = ""
    search_input.focus()
    await harness.pilot.press("enter")
    release_old.set()
    assert await harness.wait_for(lambda app: screen._repo_ids == [])
    table = screen.query_one("#search-results", ResultsTable)
    assert table.row_count == 0
    assert "type a search" in _static_plain(screen.query_one("#search-status", Static))


async def test_removed_row_ignores_pending_size_completion(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    size_started = threading.Event()
    release_size = threading.Event()

    def delayed_snapshot(api: object, rid: str) -> RepoSnapshot:
        size_started.set()
        assert release_size.wait(timeout=5)
        return RepoSnapshot(revision="rev-old", files=tuple(SIZE_PAIRS))

    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", delayed_snapshot)

    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: size_started.is_set())
    search_input = screen.query_one("#search-input", Input)
    search_input.value = ""
    search_input.focus()
    await harness.pilot.press("enter")
    release_size.set()
    assert await harness.wait_for(lambda app: ROW not in screen._sizes)
    assert screen._revisions == {}
    assert screen.query_one("#search-results", ResultsTable).row_count == 0


async def test_same_repo_new_generation_keeps_new_metadata(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_started = threading.Event()
    release_old = threading.Event()
    calls = 0
    old_pairs = (("old.safetensors", 2_000_000_000),)
    new_pairs = (("new.safetensors", 3_000_000_000),)

    def snapshots(api: object, rid: str) -> RepoSnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            old_started.set()
            assert release_old.wait(timeout=5)
            return RepoSnapshot(revision="rev-old", files=old_pairs)
        return RepoSnapshot(revision="rev-new", files=new_pairs)

    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", snapshots)
    search_input = screen.query_one("#search-input", Input)

    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: old_started.is_set())
    search_input.value = ""
    search_input.focus()
    await harness.pilot.press(*"qwen again", "enter")
    assert await harness.wait_for(lambda app: screen._revisions.get(ROW) == "rev-new")

    release_old.set()
    assert await harness.wait_for(lambda app: screen._revisions.get(ROW) == "rev-new")
    assert screen._sizes[ROW] == 3_000_000_000


async def test_stale_search_error_does_not_replace_current_status(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_started = threading.Event()
    release_old = threading.Event()

    def delayed_list(api: object, query: str) -> list[str]:
        if query == "old":
            old_started.set()
            assert release_old.wait(timeout=5)
            raise RuntimeError("old search")
        return ["new/repo"]

    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr("mlx_tui.discover_pane.list_results", delayed_list)
    search_input = screen.query_one("#search-input", Input)

    search_input.focus()
    await harness.pilot.press(*"old", "enter")
    assert await harness.wait_for(lambda app: old_started.is_set())
    search_input.value = ""
    search_input.focus()
    await harness.pilot.press(*"new", "enter")
    assert await harness.wait_for(lambda app: screen._repo_ids == ["new/repo"])

    release_old.set()
    await harness.pilot.pause()
    status = _static_plain(screen.query_one("#search-status", Static))
    assert status.startswith("1 results")
    assert not any("old search" in line for line in harness.app_log_lines())


async def test_stale_size_error_does_not_clear_new_metadata(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_started = threading.Event()
    release_old = threading.Event()
    calls = 0

    def snapshots(api: object, rid: str) -> RepoSnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            old_started.set()
            assert release_old.wait(timeout=5)
            raise OSError("old metadata")
        return RepoSnapshot(revision="rev-new", files=tuple(SIZE_PAIRS))

    screen = await open_search(harness, monkeypatch)
    monkeypatch.setattr("mlx_tui.discover_pane.repo_snapshot", snapshots)
    search_input = screen.query_one("#search-input", Input)

    await harness.pilot.press(*"qwen", "enter")
    assert await harness.wait_for(lambda app: old_started.is_set())
    search_input.value = ""
    search_input.focus()
    await harness.pilot.press(*"qwen again", "enter")
    assert await harness.wait_for(lambda app: screen._revisions.get(ROW) == "rev-new")

    release_old.set()
    await harness.pilot.pause()
    assert screen._revisions.get(ROW) == "rev-new"
    assert f"size {ROW} failed" not in "\n".join(harness.app_log_lines())


async def test_modal_escape_owns_key_even_with_chat_active(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = harness.chat_pane()
    pane._turn_active = True
    try:
        screen = SearchScreen()
        harness.app.push_screen(screen)
        await harness.pilot.pause()
        assert harness.app.screen is screen
        await harness.pilot.press(*"d/query")
        assert screen.query_one("#search-input", Input).value == "d/query"
        await harness.pilot.press("escape")
        assert await harness.wait_for(
            lambda app: not isinstance(app.screen, SearchScreen)
        )
        assert not pane._cancel_requested
    finally:
        pane._turn_active = False
