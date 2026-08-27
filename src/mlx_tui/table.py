"""Models DataTable: rendering API plus load/delete key bindings."""

from __future__ import annotations

from typing import override

from textual.coordinate import Coordinate
from textual.widgets import DataTable

from mlx_tui.models import ModelRow, fits_headroom

_FITS_GLYPHS: dict[bool | None, str] = {True: "✓", False: "⚠", None: "—"}


def loaded_cell(repo_id: str, effective_model: str | None) -> str:
    """The ● marker when repo_id is the model currently serving."""
    return "●" if effective_model == repo_id else ""


def fits_cell(size_on_disk: int, avail_gib: float | None) -> str:
    """✓/⚠ headroom hint; em-dash when system memory is unknown."""
    return _FITS_GLYPHS.get(fits_headroom(size_on_disk, avail_gib), "—")


class ModelsTable(DataTable[str]):
    BINDINGS = [
        ("enter", "load_swap", "Load/swap"),
        ("d", "delete_model", "Delete"),
        ("slash", "search_hf", "Search HF"),
    ]

    @override
    def on_mount(self) -> None:
        for column_key in ("model", "quant", "size", "fits", "loaded"):
            self.add_column(column_key, key=column_key)

    def set_rows(
        self,
        rows: list[ModelRow],
        *,
        effective_model: str | None,
        avail_gib: float | None,
    ) -> None:
        """Replace every row; renders quant/size/fits/loaded cells."""
        self.clear()
        for row in rows:
            self.add_row(
                row.repo_id,
                row.quant,
                f"{row.size_on_disk / 2**30:.1f} GB",
                _FITS_GLYPHS.get(fits_headroom(row.size_on_disk, avail_gib), "—"),
                "●" if effective_model == row.repo_id else "",
                key=row.repo_id,
            )

    def refresh_markers(
        self,
        rows: list[ModelRow],
        *,
        effective_model: str | None,
        avail_gib: float | None,
    ) -> None:
        """Update fits/loaded cells whose rendered value changed."""
        for index, row in enumerate(rows):
            new_loaded = "●" if effective_model == row.repo_id else ""
            new_fits = _FITS_GLYPHS.get(fits_headroom(row.size_on_disk, avail_gib), "—")
            if self.get_cell_at(Coordinate(index, 4)) != new_loaded:
                self.update_cell(row.repo_id, "loaded", new_loaded)
            if self.get_cell_at(Coordinate(index, 3)) != new_fits:
                self.update_cell(row.repo_id, "fits", new_fits)

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
