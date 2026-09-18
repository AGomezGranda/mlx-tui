"""Session picker: explicit reopening without startup reads or auto-apply."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static


@dataclass(frozen=True)
class SessionRow:
    path: Path
    session_id: str
    label: str
    updated_at: str
    state: str


class SessionScreen(ModalScreen[str | None]):
    """List saved sessions by updated time; transcripts render only on open."""

    BINDINGS = [("escape", "close_screen", "Close")]

    DEFAULT_CSS = """
    SessionScreen {
        align: center middle;
        #session-box {
            width: 90%;
            max-height: 80%;
            padding: 1 2;
            border: solid $primary;
            background: $surface;
            #session-table { height: 12; }
            Static { height: auto; }
        }
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[SessionRow] = []
        self._generation = 0

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="session-box"):
            yield Static("Sessions — explicit reopen, no auto-load", id="session-title")
            yield Static("loading…", id="session-status")
            yield DataTable(id="session-table", cursor_type="row")
            with Horizontal():
                yield Button("Open", id="btn-session-open")
                yield Button("Delete", id="btn-session-delete")
                yield Button("Close", id="btn-session-close")

    def on_mount(self) -> None:
        table = self.query_one("#session-table", DataTable)
        for column_key in ("session", "updated", "state"):
            table.add_column(column_key, key=column_key)
        self._refresh()

    def _refresh(self) -> None:
        self._generation += 1
        self._load_rows(self._generation)

    def _set_status(self, message: str, style: str | None = None) -> None:
        from rich.text import Text  # noqa: PLC0415

        try:
            widget = self.query_one("#session-status", Static)
        except NoMatches:
            return
        widget.update(Text(message) if style is None else Text(message, style=style))

    @work(exclusive=True, group="sessions-list", thread=True)
    def _load_rows(self, generation: int) -> None:
        from mlx_tui.sessions.errors import (  # noqa: PLC0415
            SessionLockedError,
            SessionPersistenceError,
            SessionValidationError,
        )
        from mlx_tui.sessions.queries import session_label  # noqa: PLC0415
        from mlx_tui.sessions.store import (  # noqa: PLC0415
            list_sessions,
            load_session,
            lock_session,
        )

        entries: list[SessionRow] = []
        try:
            candidates = list_sessions()
        except SessionPersistenceError as exc:
            self.app.call_from_thread(self._failed, generation, exc)
            return
        # Newest first by filename fallback; updated time after load.
        for path in candidates:
            session_id = path.stem
            try:
                loaded = load_session(path)
            except SessionPersistenceError as exc:
                entries.append(
                    SessionRow(
                        path=path,
                        session_id=session_id,
                        label=session_id[:8],
                        updated_at="",
                        state=f"unreadable: {exc.__class__.__name__}",
                    )
                )
                continue
            except SessionValidationError as exc:
                entries.append(
                    SessionRow(
                        path=path,
                        session_id=session_id,
                        label=session_id[:8],
                        updated_at="",
                        state=f"invalid: {exc}",
                    )
                )
                continue
            locked = any(turn.outcome == "running" for turn in loaded.attempts)
            try:
                with lock_session(loaded.session_id):
                    pass
                probe_locked = False
            except SessionLockedError:
                probe_locked = True
            except SessionPersistenceError:
                probe_locked = locked
            state = "locked read-only" if (locked or probe_locked) else "saved"
            if any(turn.outcome == "interrupted" for turn in loaded.attempts):
                state = "interrupted" if state == "saved" else state
            entries.append(
                SessionRow(
                    path=path,
                    session_id=loaded.session_id,
                    label=session_label(loaded),
                    updated_at=loaded.updated_at,
                    state=state,
                )
            )
        from operator import attrgetter  # noqa: PLC0415

        entries.sort(key=attrgetter("updated_at"), reverse=True)
        self.app.call_from_thread(self._populate, generation, entries)

    def _failed(self, generation: int, exc: Exception) -> None:
        if generation != self._generation or not self.is_mounted:
            return
        self._set_status(f"sessions unavailable: {exc.__class__.__name__}", "red")

    def _populate(self, generation: int, entries: list[SessionRow]) -> None:
        if generation != self._generation or not self.is_mounted:
            return
        try:
            table = self.query_one("#session-table", DataTable)
        except NoMatches:
            return
        self._rows = entries
        table.clear()
        for row in entries:
            table.add_row(row.label, row.updated_at[:16], row.state, key=row.session_id)
        if not entries:
            self._set_status("no saved sessions", "dim")
        else:
            self._set_status(f"{len(entries)} session(s)", "dim")
            table.focus()

    def _selected_id(self) -> str | None:
        try:
            table = self.query_one("#session-table", DataTable)
        except NoMatches:
            return None
        if not self._rows or not 0 <= table.cursor_row < len(self._rows):
            # Fall back to cursor coordinate key when rows exist.
            try:
                key = table.coordinate_to_cell_key(table.cursor_coordinate)
                value = key.row_key.value
                return value if value else None
            except Exception:
                return None
        return self._rows[table.cursor_row].session_id

    @on(Button.Pressed, "#btn-session-open")
    def _open_pressed(self) -> None:
        session_id = self._selected_id()
        if session_id is None:
            self._set_status("no session selected", "yellow")
            return
        row = next((r for r in self._rows if r.session_id == session_id), None)
        if row is not None and row.state.startswith(("unreadable", "invalid")):
            self._set_status("session is corrupt — delete it explicitly", "red")
            return
        try:
            self.dismiss(session_id)
        except NoMatches:
            pass

    @on(Button.Pressed, "#btn-session-delete")
    def _delete_pressed(self) -> None:
        session_id = self._selected_id()
        if session_id is None:
            self._set_status("no session selected", "yellow")
            return
        self._delete_row(session_id, self._generation)

    @work(exclusive=True, group="sessions-delete", thread=True)
    def _delete_row(self, session_id: str, generation: int) -> None:
        from mlx_tui.sessions.errors import (  # noqa: PLC0415
            SessionLockedError,
            SessionPersistenceError,
            SessionValidationError,
        )
        from mlx_tui.sessions.store import delete_session  # noqa: PLC0415

        try:
            delete_session(session_id)
        except SessionLockedError:
            self.app.call_from_thread(
                self._set_status, "session is locked — cannot delete", "yellow"
            )
            return
        except (SessionPersistenceError, SessionValidationError) as exc:
            self.app.call_from_thread(self._set_status, f"delete failed: {exc}", "red")
            return
        self.app.call_from_thread(self._refresh_after_delete, generation)

    def _refresh_after_delete(self, generation: int) -> None:
        if generation != self._generation or not self.is_mounted:
            return
        self._set_status("deleted", "dim")
        self._refresh()

    @on(Button.Pressed, "#btn-session-close")
    def _close_pressed(self) -> None:
        self.action_close_screen()

    def action_close_screen(self) -> None:
        try:
            self.dismiss(None)
        except NoMatches:
            pass
