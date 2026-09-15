"""Models DataTable: rendering API plus load/delete key bindings."""

from __future__ import annotations

from typing import override

from textual.coordinate import Coordinate
from textual.widgets import DataTable

from mlx_tui.models import ModelRow, model_identity_matches


def selected_cell(repo_id: str, selected_model: str | None) -> str:
    """The ● marker when repo_id is the explicit request target."""
    return "●" if model_identity_matches(repo_id, selected_model) else ""


def quant_cell(quant: str) -> str:
    """Name-derived quantization is a hint, not runtime evidence."""
    return f"{quant} hint" if quant != "—" else "unknown hint"


def runtime_fit_cell() -> str:
    """Disk size never proves runtime memory fit."""
    return "unknown"


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
            "quant hint",
            "size on disk",
            "runtime fit",
            "selected",
        ):
            self.add_column(column_key, key=column_key)

    def set_rows(
        self,
        rows: list[ModelRow],
        *,
        selected_model: str | None,
    ) -> None:
        """Replace every row; render disk facts and explicit selection."""
        self.clear()
        for row in rows:
            self.add_row(
                row.repo_id,
                quant_cell(row.quant),
                f"{row.size_on_disk / 2**30:.1f} GiB",
                runtime_fit_cell(),
                selected_cell(row.repo_id, selected_model),
                key=row.repo_id,
            )

    def refresh_markers(
        self,
        rows: list[ModelRow],
        *,
        selected_model: str | None,
    ) -> None:
        """Update selection cells whose rendered value changed."""
        # runtime fit is always "unknown" — no per-cell check needed.
        for index, row in enumerate(rows):
            new_selected = selected_cell(row.repo_id, selected_model)
            if self.get_cell_at(Coordinate(index, 4)) != new_selected:
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
        # Local import: search_screen hands off to models_pane lazily, so a
        # module-top import would cycle (same reason as the ModelsPane imports).
        from mlx_tui.search_screen import SearchScreen  # noqa: PLC0415

        self.app.push_screen(SearchScreen())
