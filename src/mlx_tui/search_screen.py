"""Modal host for the reusable Hugging Face discovery pane."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override

from textual.app import ComposeResult
from textual.screen import ModalScreen

from mlx_tui.discover_pane import DiscoverPane

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class SearchScreen(ModalScreen[None]):
    """Modal host for setup and comparison model downloads."""

    BINDINGS = [("escape", "close_screen", "Close")]

    DEFAULT_CSS = """
    SearchScreen { align: center middle; }
    SearchScreen > DiscoverPane {
        width: 100%;
        height: 100%;
        border: solid $primary;
    }
    """

    def __init__(self, pinned_candidates: tuple[tuple[str, str], ...] = ()) -> None:
        super().__init__()
        self.discover = DiscoverPane(
            pinned_candidates,
            host=self,
            show_chrome=True,
        )

    @property
    def tui(self) -> MlxTuiApp:  # type: ignore[name-defined]
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        yield self.discover

    def on_mount(self) -> None:
        self.discover.focus_search()

    def on_unmount(self) -> None:
        self.discover.teardown()

    def action_close_screen(self) -> None:
        self.discover.request_close()

    def discover_return(self, pane: DiscoverPane) -> None:
        if pane is self.discover:
            self.dismiss(None)

    def discover_complete(
        self, pane: DiscoverPane, outcome: str, repo_id: str | None
    ) -> None:
        if pane is not self.discover:
            return
        if outcome in {"success", "cancelled"}:
            self._rescan_models(repo_id)
            self.dismiss(None)

    def _rescan_models(self, repo_id: str | None = None) -> None:
        from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

        try:
            models = self.tui.query_one(ModelsPane)
            if repo_id is not None:
                models.invalidate_facts(repo_id)
            models.rescan()
        except Exception:
            return
