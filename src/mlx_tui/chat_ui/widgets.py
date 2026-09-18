"""Chat composer widgets: native file chooser and multiline ChatInput."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from mlx_tui.attachments import (
    AttachmentError,
)
from mlx_tui.chat_turn import ChatTurn, render_session_turn  # noqa: F401

if TYPE_CHECKING:
    pass


_MACOS_FILE_PICKER_SCRIPT = """try
POSIX path of (choose file with prompt "Attach a text file to mlx-tui")
on error number -128
return ""
end try"""


def _choose_macos_file() -> str | None:
    """Return a file selected with the native macOS chooser, or None on cancel."""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", _MACOS_FILE_PICKER_SCRIPT],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise AttachmentError("native macOS file chooser is unavailable") from exc
    if result.returncode != 0:
        raise AttachmentError("native macOS file chooser failed")
    return result.stdout.rstrip("\r\n") or None


class ChatInput(TextArea):
    """Multiline composer: Enter inserts newline, Tab moves focus, paste never submits."""

    BINDINGS = [
        *TextArea.BINDINGS,
        Binding("ctrl+enter", "submit", "Send", show=True),
    ]

    class Submitted(Message):
        """Carries the editor and exact text (indentation preserved)."""

        def __init__(self, chat_input: ChatInput, text: str) -> None:
            super().__init__()
            self.chat_input = chat_input
            self.text = text

        @property
        def control(self) -> ChatInput:  # type: ignore[override]
            return self.chat_input

    def __init__(
        self,
        text: str = "",
        *,
        placeholder: str = "",
        id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            text,
            placeholder=placeholder,
            id=id,
            tab_behavior="focus",
            **kwargs,
        )

    def action_submit(self) -> None:
        self.post_message(ChatInput.Submitted(self, self.text))
