"""Reusable yes/no modal screen."""

from __future__ import annotations

from typing import override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Asks a yes/no question; ``dismiss(True)`` confirms, ``False`` keeps."""

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
        #confirm-box {
            width: auto;
            height: auto;
            padding: 1 2;
            border: solid $primary;
            background: $surface;
            Horizontal { height: auto; align-horizontal: center; }
            Button { margin: 0 1; }
        }
    }
    """

    BINDINGS = [
        ("y", "confirm", "yes"),
        ("n", "dismiss_no", "no"),
        ("escape", "dismiss_no", "keep"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.prompt)
            with Horizontal():
                yield Button("delete", id="btn-yes")
                yield Button("keep", id="btn-no")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss_no(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn-yes")
    def _confirm_pressed(self) -> None:
        self.action_confirm()

    @on(Button.Pressed, "#btn-no")
    def _keep_pressed(self) -> None:
        self.action_dismiss_no()
