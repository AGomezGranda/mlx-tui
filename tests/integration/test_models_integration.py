"""Assessment refresh and selected-model details integration coverage."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import Static, TabbedContent

from mlx_tui.catalog.assessment import (
    AssessmentScenario,
    Compatibility,
    HardwareProfile,
    RuntimeCapabilities,
    assess,
)
from mlx_tui.models import ModelRow
from mlx_tui.models_pane import ModelsPane
from mlx_tui.search_screen import SearchScreen
from mlx_tui.table import ModelsTable
from tests.conftest import AppHarness

REVISION = "a" * 40
REPO_ID = "mlx-community/stub-test"


def _plain(widget: Static) -> str:
    content = widget.content
    plain = getattr(content, "plain", None)
    return plain if isinstance(plain, str) else str(content)


def _snapshot(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen2",
                "num_hidden_layers": 32,
                "num_attention_heads": 32,
                "num_key_value_heads": 8,
                "hidden_size": 4096,
            }
        )
    )
    (snapshot / "tokenizer_config.json").write_text("{}")
    (snapshot / "model.safetensors").write_bytes(b"weights")
    return snapshot


async def _prepare_model(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[ModelsPane, ModelRow]:
    snapshot = _snapshot(tmp_path)
    row = ModelRow(REPO_ID, 100, "4bit", (REVISION,))
    monkeypatch.setattr("mlx_tui.models_pane.scan_models", lambda: [row])

    def resolve_snapshot(repo_id: str, revision: str) -> Path:
        del repo_id, revision
        return snapshot

    monkeypatch.setattr("mlx_tui.models_pane.resolve_cached_snapshot", resolve_snapshot)
    monkeypatch.setattr(
        "mlx_tui.models_pane.local_hardware",
        lambda: HardwareProfile("Test Mac", 32 * 2**30, 20 * 2**30, 26 * 2**30),
    )
    pane = harness.models_pane()
    harness.app.query_one(TabbedContent).active = "models"
    pane.rescan()
    assert await harness.wait_for(lambda app: pane.rows == [row])
    assert await harness.wait_for(lambda app: REPO_ID in pane.assessments)
    return pane, row


async def test_runtime_evidence_transition_reassesses_existing_rows(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pane, _row = await _prepare_model(harness, monkeypatch, tmp_path)
    assert pane.assessments[REPO_ID].compatibility is Compatibility.UNKNOWN

    class Runtime:
        install_evidence = {"verified": True}

    harness.app.config = replace(harness.app.config, runtime_mode="managed")
    harness.app.managed_runtime = Runtime()  # type: ignore[assignment]
    pane.reassess()
    assert await harness.wait_for(
        lambda app: pane.assessments[REPO_ID].compatibility == Compatibility.SUPPORTED
    )


async def test_older_context_assessment_cannot_replace_latest(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pane, _row = await _prepare_model(harness, monkeypatch, tmp_path)
    real_assess = assess
    old_started = threading.Event()
    release_old = threading.Event()

    def delayed_assess(
        facts: object,
        hardware: HardwareProfile,
        runtime: RuntimeCapabilities,
        scenario: AssessmentScenario,
    ):
        if scenario.context_tokens == 4096:
            old_started.set()
            assert release_old.wait(timeout=5)
        return real_assess(facts, hardware, runtime, scenario)  # type: ignore[arg-type]

    monkeypatch.setattr("mlx_tui.models_pane.assess", delayed_assess)
    pane.reassess(context=4096)
    assert await harness.wait_for(lambda app: old_started.is_set())

    pane.reassess(context=8192)
    assert await harness.wait_for(
        lambda app: (
            REPO_ID in pane.assessments
            and pane.assessments[REPO_ID].kv_bytes
            == real_assess(
                next(iter(pane._facts_cache.values())),
                HardwareProfile("Test Mac", 32 * 2**30, 20 * 2**30, 26 * 2**30),
                RuntimeCapabilities(None, frozenset(), False),
                AssessmentScenario(8192),
            ).kv_bytes
        )
    )
    release_old.set()
    assert await harness.wait_for(
        lambda app: (
            pane.assessments[REPO_ID].kv_bytes
            == real_assess(
                next(iter(pane._facts_cache.values())),
                HardwareProfile("Test Mac", 32 * 2**30, 20 * 2**30, 26 * 2**30),
                RuntimeCapabilities(None, frozenset(), False),
                AssessmentScenario(8192),
            ).kv_bytes
        )
    )


async def test_empty_selection_clears_model_details(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pane, _row = await _prepare_model(harness, monkeypatch, tmp_path)
    table = pane.query_one("#models-table", ModelsTable)
    table.focus()
    assert REPO_ID in _plain(pane.query_one("#models-details", Static))

    pane._populate([])
    details = _plain(pane.query_one("#models-details", Static))
    assert "Select a model" in details
    assert REPO_ID not in details


async def test_models_workspace_layout_and_resize_preserve_table(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pane, _row = await _prepare_model(harness, monkeypatch, tmp_path)
    table = pane.query_one("#models-table", ModelsTable)
    table.focus()
    selected_row = table.cursor_row

    await harness.pilot.resize_terminal(140, 40)
    await harness.pilot.pause()
    sidebar = pane.query_one("#models-sidebar")
    main = pane.query_one("#models-main")
    details = pane.query_one("#models-details-panel")
    assert not pane.has_class("compact-layout")
    assert sidebar.region.width >= 30
    assert sidebar.region.right <= main.region.x
    assert table.region.bottom <= details.region.y

    await harness.pilot.resize_terminal(80, 24)
    await harness.pilot.pause()
    assert pane.has_class("compact-layout")
    assert pane.query_one("#models-table", ModelsTable) is table
    assert table.cursor_row == selected_row
    assert table.region.height >= 4
    assert pane.query_one("#models-sidebar").region.bottom <= table.region.y
    assert table.region.bottom <= pane.query_one("#models-details-panel").region.y


async def test_models_discover_replaces_main_view_without_modal(
    harness: AppHarness,
) -> None:
    pane = harness.models_pane()
    button = pane.query_one("#models-discover")
    button.focus()

    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda app: pane._discover_pane is not None)
    assert not isinstance(harness.app.screen, SearchScreen)
    assert pane.query_one("#search-input")


async def test_inline_discover_return_restores_focus_and_rejects_late_metadata(
    harness: AppHarness,
) -> None:
    pane = harness.models_pane()
    table = pane.query_one("#models-table", ModelsTable)
    table.focus()
    selected_row = table.cursor_row

    pane.open_discover()
    assert await harness.wait_for(lambda app: pane._discover_pane is not None)
    discover = pane._discover_pane
    assert discover is not None
    assert await harness.wait_for(
        lambda app: discover.query_one("#search-results") is not None
    )

    old_generation = discover._search_generation
    discover._all_repo_ids = ["late/repo"]
    discover.request_close()
    await harness.pilot.pause()
    discover._fill_size_cell(
        old_generation,
        "late/repo",
        123,
        "✓",
        "rev-late",
    )

    assert pane._discover_pane is None
    assert table.has_focus
    assert table.cursor_row == selected_row
    assert discover._sizes == {}


async def test_more_details_opens_revision_and_assumptions_preview(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from textual.widgets import TextArea  # noqa: PLC0415

    from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

    pane, _row = await _prepare_model(harness, monkeypatch, tmp_path)
    await harness.pilot.click("#models-details-more")
    await harness.pilot.pause()

    preview = harness.app.screen
    assert isinstance(preview, TextPreviewScreen)
    preview_text = preview.query_one("#text-preview-area", TextArea).text
    assert f"Revision: {REVISION}" in preview_text
    assert "Architecture: qwen2" in preview_text
    assert "Assumptions:" in preview_text
    await harness.pilot.press("escape")
