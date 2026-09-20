"""Models DataTable: rendering API plus load/delete key bindings."""

from __future__ import annotations

from typing import override

from textual.coordinate import Coordinate
from textual.widgets import DataTable

from mlx_tui.catalog.assessment import ModelAssessment
from mlx_tui.models import ModelRow, model_identity_matches


def selected_cell(repo_id: str, selected_model: str | None) -> str:
    """The ● marker when repo_id is the explicit request target."""
    return "●" if model_identity_matches(repo_id, selected_model) else ""


def quant_cell(quant: str) -> str:
    """Name-derived quantization is a hint, not runtime evidence."""
    return f"{quant} hint" if quant != "—" else "unknown hint"


def runtime_fit_cell(assessment: ModelAssessment | None = None) -> str:
    """Only a complete local estimate can claim comfortable fit."""
    return assessment.fit.value if assessment is not None else "Unknown"


class ModelsTable(DataTable[str]):
    BINDINGS = [
        ("enter", "load_swap", "Load"),
        ("d", "delete_model", "Delete"),
        ("slash", "search_hf", "Search"),
    ]

    @override
    def on_mount(self) -> None:
        for column_key in (
            "model",
            "compatibility",
            "runtime fit",
            "size on disk",
            "quant hint",
            "selected",
        ):
            label = "Memory fit" if column_key == "runtime fit" else column_key
            self.add_column(label, key=column_key)

    def set_rows(
        self,
        rows: list[ModelRow],
        *,
        selected_model: str | None,
        assessments: dict[str, ModelAssessment] | None = None,
    ) -> None:
        """Replace every row; render disk facts and explicit selection."""
        selected_id = None
        if 0 <= self.cursor_row < self.row_count:
            selected_id = self.get_row_at(self.cursor_row)[0]
        self.clear()
        for row in rows:
            self.add_row(
                row.repo_id,
                (
                    assessments[row.repo_id].compatibility.value
                    if assessments and row.repo_id in assessments
                    else "Unknown"
                ),
                runtime_fit_cell((assessments or {}).get(row.repo_id)),
                f"{row.size_on_disk / 2**30:.1f} GiB",
                quant_cell(row.quant),
                selected_cell(row.repo_id, selected_model),
                key=row.repo_id,
            )
        if selected_id is not None:
            selected_index = next(
                (index for index, row in enumerate(rows) if row.repo_id == selected_id),
                None,
            )
            if selected_index is not None:
                self.move_cursor(row=selected_index)

    def refresh_markers(
        self,
        rows: list[ModelRow],
        *,
        selected_model: str | None,
    ) -> None:
        """Update selection cells whose rendered value changed."""
        for index, row in enumerate(rows):
            new_selected = selected_cell(row.repo_id, selected_model)
            if self.get_cell_at(Coordinate(index, 5)) != new_selected:
                self.update_cell(row.repo_id, "selected", new_selected)

    def action_load_swap(self) -> None:
        # Local import: models_pane imports table for ModelsTable, so a
        # module-top import would cycle; query_one needs the class object.
        from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

        self.app.query_one(ModelsPane).request_load_swap()

    def action_delete_model(self) -> None:
        from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

        self.app.query_one(ModelsPane).request_delete_model()

    def action_search_hf(self) -> None:
        # Local import: models_pane imports ModelsTable, so keep the owner
        # lookup lazy and let it mount discovery inline.
        from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

        self.app.query_one(ModelsPane).open_discover()
