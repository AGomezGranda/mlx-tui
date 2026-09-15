"""Integration fixtures shared by model lifecycle tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from mlx_tui.app import MlxTuiApp
from tests.conftest import AppHarness, StubServer
from tests.integration.model_helpers import _no_process, _stub_rows


@pytest.fixture
async def stub_harness(
    stub_server_factory: Callable[[str], StubServer],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AppHarness]:
    """Harness whose cache always scans to the shared stub row."""
    monkeypatch.setattr("mlx_tui.models_pane.scan_models", _stub_rows)
    monkeypatch.setattr(
        "mlx_tui.models_pane.process.find_server_process",
        _no_process,
    )
    monkeypatch.setattr(
        "mlx_tui.app.process.find_server_process",
        _no_process,
    )
    monkeypatch.setattr(
        "mlx_tui.process.find_server_process",
        _no_process,
    )
    server = stub_server_factory("ok")
    app = MlxTuiApp(host="127.0.0.1", port=server.server_address[1])
    async with app.run_test() as pilot:
        yield AppHarness(app=app, pilot=pilot, server=server)
