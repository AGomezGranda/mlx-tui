"""Read-only text preview with exact copy actions."""

from __future__ import annotations

import subprocess
import sys
from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

_COPIED_OSC52_NOTE = (
    "Copied (terminal clipboard support varies — macOS Terminal may not support OSC 52)"
)


def copy_text_to_system(text: str) -> tuple[bool, str]:
    """Copy text without temp files; macOS uses argv-only pbcopy.

    Returns (ok, message). Never claims success after a failed command.
    """
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["/usr/bin/pbcopy"],
                input=text.encode("utf-8"),
                timeout=5,
                check=False,
            )
        except FileNotFoundError:
            return False, "copy failed: /usr/bin/pbcopy not found"
        except subprocess.TimeoutExpired:
            return False, "copy failed: pbcopy timed out"
        except OSError as exc:
            return False, f"copy failed: {exc.__class__.__name__}"
        if proc.returncode != 0:
            return False, f"copy failed: pbcopy exited {proc.returncode}"
        return True, "Copied to clipboard"
    # Elsewhere rely on the installed Textual clipboard (OSC 52) and label limits.
    return True, _COPIED_OSC52_NOTE


class TextPreviewScreen(ModalScreen[None]):
    """Show exact answer text in a read-only editor for selection/copy."""

    BINDINGS = [("escape", "close_screen", "Close")]

    DEFAULT_CSS = """
    TextPreviewScreen {
        align: center middle;
        #text-preview-box {
            width: 90%;
            max-height: 80%;
            padding: 1 2;
            border: solid $primary;
            background: $surface;
            #text-preview-area { height: 12; }
            Static { height: auto; }
        }
    }
    """

    def __init__(
        self, text: str, title: str = "Answer — select and copy exactly"
    ) -> None:
        super().__init__()
        self._text = text
        self._title = title

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="text-preview-box"):
            yield Static(self._title, id="text-preview-title")
            yield TextArea(
                self._text,
                read_only=True,
                id="text-preview-area",
            )
            yield Static("", id="text-preview-status")
            with Horizontal():
                yield Button("Copy all", id="btn-copy-all")
                yield Button("Copy selection", id="btn-copy-selection")
                yield Button("Close", id="btn-preview-close")

    def _set_status(self, message: str, style: str | None = None) -> None:
        from rich.text import Text  # noqa: PLC0415

        try:
            widget = self.query_one("#text-preview-status", Static)
        except NoMatches:
            return
        widget.update(Text(message) if style is None else Text(message, style=style))

    def _copy_text(self, text: str) -> None:
        if sys.platform == "darwin":
            ok, msg = copy_text_to_system(text)
            self._set_status(msg, None if ok else "red")
            return
        # Non-macOS: use Textual clipboard and label the limitation.
        try:
            self.app.copy_to_clipboard(text)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"copy failed: {exc.__class__.__name__}", "red")
            return
        self._set_status(_COPIED_OSC52_NOTE)

    @on(Button.Pressed, "#btn-copy-all")
    def _copy_all(self) -> None:
        self._copy_text(self._text)

    @on(Button.Pressed, "#btn-copy-selection")
    def _copy_selection(self) -> None:
        try:
            area = self.query_one("#text-preview-area", TextArea)
        except NoMatches:
            self._set_status("copy failed: preview unavailable", "red")
            return
        selected = ""
        try:
            selected = area.selected_text
        except Exception:
            selected = ""
        if not selected:
            self._set_status("no selection — select text or use Copy all", "yellow")
            return
        self._copy_text(selected)

    @on(Button.Pressed, "#btn-preview-close")
    def _close(self) -> None:
        self.action_close_screen()

    def action_close_screen(self) -> None:
        try:
            self.dismiss(None)
        except NoMatches:
            pass
