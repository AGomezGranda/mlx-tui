"""Unit tests for the ModelsTable pure cell-rendering functions."""

from __future__ import annotations

import pytest

from mlx_tui.models import ModelRow
from mlx_tui.table import quant_cell, runtime_fit_cell, selected_cell


@pytest.mark.parametrize(
    ("repo_id", "selected_model", "expected"),
    [
        ("m", "m", "●"),
        ("m", "other", ""),
        ("m", None, ""),
    ],
)
def test_selected_cell(repo_id: str, selected_model: str | None, expected: str) -> None:
    assert selected_cell(repo_id, selected_model) == expected


def _row(size_on_disk: int) -> ModelRow:
    return ModelRow("org/m-4bit", size_on_disk, "4bit", ("h",))


def test_quant_cell_is_explicitly_a_name_hint() -> None:
    assert quant_cell(_row(5_000_000_000).quant) == "4bit hint"


def test_runtime_fit_is_unknown() -> None:
    assert runtime_fit_cell() == "unknown"


def test_unknown_quant_hint_is_explicit() -> None:
    assert quant_cell("—") == "unknown hint"
