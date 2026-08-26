"""Unit tests for the ModelsTable pure cell-rendering functions."""

from __future__ import annotations

import pytest

from mlx_tui.models import ModelRow
from mlx_tui.table import fits_cell, loaded_cell


@pytest.mark.parametrize(
    ("repo_id", "effective_model", "expected"),
    [
        ("m", "m", "●"),
        ("m", "other", ""),
        ("m", None, ""),
    ],
)
def test_loaded_cell(repo_id: str, effective_model: str | None, expected: str) -> None:
    assert loaded_cell(repo_id, effective_model) == expected


def _row(size_on_disk: int) -> ModelRow:
    return ModelRow("org/m-4bit", size_on_disk, "4bit", True, ("h",))


def test_fits_cell_checkmark_with_ample_headroom() -> None:
    assert fits_cell(_row(5_000_000_000).size_on_disk, 10.0) == "✓"


def test_fits_cell_warning_when_oversized() -> None:
    assert fits_cell(_row(9_000_000_000).size_on_disk, 10.0) == "⚠"


def test_fits_cell_dash_when_memory_unknown() -> None:
    assert fits_cell(_row(5_000_000_000).size_on_disk, None) == "—"
