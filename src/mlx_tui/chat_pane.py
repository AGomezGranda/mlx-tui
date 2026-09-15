"""Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

from __future__ import annotations

import asyncio
import ipaddress
import subprocess
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, override

import httpx
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Input, ProgressBar, Static, TabbedContent, TextArea
from textual.worker import Worker

from mlx_tui import sessions as _sessions
from mlx_tui.attachments import (
    AttachmentError,
    AttachmentSnapshot,
    read_attachment,
    render_user_content,
)
from mlx_tui.chat import TurnProgress, TurnResult, error_detail, stream_turn
from mlx_tui.chat_turn import ChatTurn, render_session_turn  # noqa: F401
from mlx_tui.config import AppConfig
from mlx_tui.history.store import TurnRecord
from mlx_tui.history.tokens import (
    ContextLimitError,
    ContextWindow,
    ctx_bar_style,
    ctx_bar_text,
    estimate_tokens,
    prepare_context,
)
from mlx_tui.operations import OperationKind
from mlx_tui.params import ParamsPane
from mlx_tui.sessions import (
    ChatSession,
    RequestSettings,
    SessionLock,
    SessionTurn,
)

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

_MAX_CONTEXT_TOKENS_EST = 8_192
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
            # Compat aliases for Input.Submitted call sites.
            self.value = text
            self.input = chat_input

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

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new_value: str) -> None:
        self.text = new_value

    def action_submit(self) -> None:
        self.post_message(ChatInput.Submitted(self, self.text))


class ChatPane(Vertical):
    """Owns the transcript viewport, composer and turn worker."""

    DEFAULT_CSS = """
    ChatPane { height: 1fr; overflow: hidden; padding: 0 1; }
    #chat-input { height: 5; min-height: 3; max-height: 8; width: 1fr; }
    #chat-composer-row { height: auto; margin: 0 1; }
    #chat-composer-row Button { width: auto; height: 3; min-height: 3; border: none; padding: 0 1; margin-left: 1; }
    #chat-attachment-row { height: 1; margin: 0 1; }
    #chat-attachment-index { width: 4; height: 1; border: none; padding: 0 1; }
    #chat-attachment-row Button { width: auto; height: 1; min-height: 1; border: none; padding: 0 1; margin-left: 1; }
    #chat-attachments {
        width: 1fr;
        min-width: 1;
        height: 1;
        margin: 0 1;
        color: $text-muted;
        overflow: hidden;
    }
    #chat-transcript {
        height: 1fr;
    }
    #chat-context {
        height: 1;
        margin: 1 1 0 1;
    }
    #chat-context Button { width: auto; height: 1; min-height: 1; border: none; padding: 0 1; margin-left: 1; }
    #chat-session-row {
        height: 1;
        margin: 1 1 0 1;
    }
    #chat-session-row Button { width: auto; margin-right: 1; height: 1; min-height: 1; border: none; padding: 0 1; }
    #chat-save-status { width: auto; color: $text-muted; height: 1; padding: 0 1; }
    #chat-reconcile-row { height: 1; margin: 0 1; display: none; }
    #chat-reconcile-row Button { width: auto; margin-right: 1; height: 1; min-height: 1; border: none; padding: 0 1; }
    #chat-save-row { height: 1; margin: 0 1; display: none; }
    #chat-save-row Button { width: auto; margin-right: 1; height: 1; min-height: 1; border: none; padding: 0 1; }
    #ctx-progress {
        height: 1;
        width: 1fr;
    }
    #ctx-progress Bar { width: 1fr; }
    #ctx-bar {
        width: auto;
        color: $text-muted;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    #ctx-progress.ctx-bar-amber Bar > .bar--bar {
        color: $warning;
    }
    #ctx-progress.ctx-bar-red Bar > .bar--bar {
        color: $error;
    }
    .ctx-bar-amber {
        color: $warning;
    }
    .ctx-bar-red {
        color: $error;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.messages: list[dict[str, str]] = []
        self._cancel_requested: bool = False
        self._turn_active: bool = False
        self._turn_worker: Worker[None] | None = None
        self._turn_started: bool = False
        self._active_turn: ChatTurn | None = None
        self._pending_draft: str | None = None
        self._restore_composer_focus = False
        self._follow_from: float | None = None
        self._follow_scheduled = False
        # Durable session records (Phase 2): attempts are authoritative for
        # restoring presentation; messages stays the successful-pair projection.
        self._session: ChatSession | None = None
        self._session_lock: SessionLock | None = None
        self._session_lock_cm: Any | None = None
        self._is_temporary = False
        self._read_only = False
        self._save_revision = 0
        self._saved_revision = 0
        self._save_failed = False
        self._save_error: str | None = None
        self._write_lock = asyncio.Lock()
        self._draft_timer: Any | None = None
        self._restoring = False
        self._pending_reconcile: RequestSettings | None = None
        self._active_session_turn_id: str | None = None
        self._progress_answer = ""
        self._progress_reasoning = ""
        self._progress_tools: dict[int, dict[str, object]] = {}
        self._progress_response_model: str | None = None
        self._last_checkpoint = 0.0
        self._shutdown_flushed = False
        self._draft_attachments: tuple[AttachmentSnapshot, ...] = ()
        self._attachment_index = 0
        self._attachment_picker_active = False
        self._context_error: str | None = None

    @property
    def tui(self) -> MlxTuiApp:  # type: ignore[name-defined]
        return cast("MlxTuiApp", self.app)

    @property
    def has_live_turn(self) -> bool:
        return self._turn_active

    @override
    def compose(self) -> ComposeResult:
        yield ParamsPane()
        yield VerticalScroll(id="chat-transcript")
        with Horizontal(id="chat-context"):
            yield ProgressBar(
                total=8192, show_percentage=False, show_eta=False, id="ctx-progress"
            )
            yield Static("ctx 0/8k", id="ctx-bar")
            yield Button("Preview", id="btn-context-preview")
        with Horizontal(id="chat-session-row"):
            yield Button("Sessions", id="btn-sessions")
            yield Button("New", id="btn-new-session")
            yield Button("New temporary", id="btn-new-temp")
            yield Button("Clear", id="btn-clear-chat")
            yield Button("Delete", id="btn-delete-session")
            yield Button("Retry", id="btn-retry-request")
            yield Static("Saved locally", id="chat-save-status")
        with Horizontal(id="chat-reconcile-row"):
            yield Button("Use saved request settings", id="btn-use-saved")
            yield Button("Continue with current settings", id="btn-keep-current")
        with Horizontal(id="chat-save-row"):
            yield Button("Retry save", id="btn-retry-save")
            yield Button("Discard unsaved work", id="btn-discard-unsaved")
        with Horizontal(id="chat-attachment-row"):
            yield Button("Add file…", id="btn-add-attachment")
            yield Input(value="1", placeholder="#", id="chat-attachment-index")
            yield Button("Inspect", id="btn-inspect-attachment")
            yield Button("Remove", id="btn-remove-attachment")
            yield Static("files: none", id="chat-attachments")
        with Horizontal(id="chat-composer-row"):
            yield ChatInput(placeholder="message…", id="chat-input")
            yield Button("Send", id="btn-send", variant="primary")

    def on_mount(self) -> None:
        self.update_ctx_bar(0)
        viewport = self.query_one("#chat-transcript", VerticalScroll)
        self.watch(self.screen, "focused", self._focus_changed, init=False)
        self.watch(viewport, "scroll_y", self._scroll_changed, init=False)
        self.watch(viewport, "scroll_target_y", self._scroll_changed, init=False)
        self._ensure_session()
        self._refresh_attachment_ui()
        self._update_save_status()
        self._update_action_visibility()

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _ensure_session(self) -> ChatSession:
        if self._session is not None:
            return self._session
        settings = self.capture_request_settings()
        session = _sessions.new_session(settings, draft="")
        # No file until meaningful content; no startup reads.
        self._session = session
        return session

    def capture_request_settings(self) -> RequestSettings:
        """Single dirty path for every next-request mutation."""
        try:
            temp, top_p, max_tok = self.query_one(ParamsPane).read_values()
        except Exception:
            temp, top_p, max_tok = (0.7, 1.0, 1024)
        try:
            cfg = self.tui.config
        except Exception:
            return RequestSettings(
                model=None,
                repo_id=None,
                revision=None,
                system="",
                temperature=temp,
                top_p=top_p,
                max_tokens=max_tok,
                max_ctx=_MAX_CONTEXT_TOKENS_EST,
            )
        try:
            model = self.tui.effective_model()
        except Exception:
            model = cfg.model
        max_ctx = (
            cfg.max_ctx
            if isinstance(cfg.max_ctx, int) and cfg.max_ctx > 0
            else _MAX_CONTEXT_TOKENS_EST
        )
        system = cfg.system or ""
        profile_id: str | None = None
        fingerprint: str | None = None
        modified = False
        runtime_commit: str | None = None
        runtime_prov: str | None = None
        template_sha: str | None = None
        template_prov: str | None = None
        launch_settings: dict[str, Any] | None = None
        launch_prov: str | None = None
        repo_id: str | None = None
        revision: str | None = None
        try:
            active_id = self.tui.active_profile_id
            modified = self.tui.active_profile_modified
            if active_id is not None:
                entry = self.tui.profile_entry(active_id)
                if entry is not None:
                    profile_id = entry.profile.id
                    fingerprint = entry.profile.fingerprint
                    repo_id = entry.profile.repo_id
                    revision = entry.profile.revision
                    runtime_commit = entry.profile.runtime_commit
                    runtime_prov = "requested"
                    template_sha = entry.profile.template_sha256
                    template_prov = "requested"
                    launch_settings = dict(entry.profile.launch_settings)
                    launch_prov = "requested"
        except Exception:
            pass
        return RequestSettings(
            model=model,
            repo_id=repo_id,
            revision=revision,
            system=system,
            temperature=temp,
            top_p=top_p,
            max_tokens=max_tok,
            max_ctx=max_ctx,
            seed=cfg.seed,
            enable_thinking=cfg.enable_thinking,
            profile_id=profile_id,
            profile_fingerprint=fingerprint,
            profile_modified=modified,
            profile_runtime_commit=runtime_commit,
            profile_runtime_provenance=runtime_prov,  # type: ignore[arg-type]
            profile_template_sha256=template_sha,
            profile_template_provenance=template_prov,  # type: ignore[arg-type]
            profile_launch_settings=launch_settings,  # type: ignore[arg-type]
            profile_launch_provenance=launch_prov,  # type: ignore[arg-type]
        )

    def mark_next_request_dirty(self) -> None:
        """Checkpoint both reconciliation choices through one path."""
        session = self._ensure_session()
        if self._read_only:
            return
        settings = self.capture_request_settings()
        self._session = replace(session, settings=settings, updated_at=self._now_iso())
        self._save_revision += 1
        self._update_save_status()
        self._schedule_draft_save()
        self.refresh_context_bar()

    def _schedule_draft_save(self, delay: float = 0.25) -> None:
        if self._is_temporary or self._read_only:
            self._update_save_status()
            return
        try:
            if self._draft_timer is not None:
                self._draft_timer.stop()
        except Exception:
            pass
        try:
            self._draft_timer = self.set_timer(delay, self._draft_timer_fired)
        except Exception:
            pass
        self._update_save_status()

    def _draft_timer_fired(self) -> None:
        self._draft_timer = None
        self._save_session_async()

    def _save_session_async(self) -> None:
        if self._is_temporary or self._read_only:
            return
        if self._session is None:
            return
        # Coalesce draft changes at 250 ms; serialize with one async lock.
        asyncio.ensure_future(self._save_session_now())

    def _has_meaningful_content(self, session: ChatSession) -> bool:
        if session.draft.strip():
            return True
        return len(session.attempts) > 0

    def _snapshot_for_save(self) -> ChatSession | None:
        session = self._session
        if session is None:
            return None
        try:
            composer = self.query_one("#chat-input", ChatInput)
            draft = composer.text
        except NoMatches:
            draft = session.draft
        # Never let an empty composer wipe a retained draft while a save
        # failure is pending (e.g. pre-send failure before end_turn restore).
        if self._save_failed and not draft.strip() and session.draft.strip():
            draft = session.draft
        settings = self.capture_request_settings()
        return replace(
            session,
            draft=draft,
            attachments=self._draft_attachments,
            settings=settings,
            updated_at=self._now_iso(),
        )

    async def _save_session_now(self) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
        """Serialize writes; older saves never mark newer edits saved."""
        if self._is_temporary or self._read_only or self._session is None:
            return True
        async with self._write_lock:
            # Take the latest snapshot after acquiring the write lock.
            snapshot = self._snapshot_for_save()
            if snapshot is None:
                return True
            self._session = snapshot
            if not self._has_meaningful_content(snapshot):
                # A spurious empty save must never clear a pending failure.
                if not self._save_failed:
                    self._saved_revision = self._save_revision
                    self._save_error = None
                try:
                    self._update_save_status()
                except Exception:
                    pass
                return not self._save_failed
            revision = self._save_revision
            # Acquire the per-session advisory lock on first durable save.
            if self._session_lock is None:
                try:
                    cm = _sessions.lock_session(snapshot.session_id)
                    handle = cm.__enter__()  # type: ignore[attr-defined]
                    self._session_lock_cm = cm
                    self._session_lock = handle
                except _sessions.SessionLockedError as exc:
                    self._save_failed = True
                    self._save_error = str(exc)
                    self._read_only = True
                    try:
                        self._update_save_status()
                        self._update_action_visibility()
                    except Exception:
                        pass
                    return False
                except _sessions.SessionPersistenceError as exc:
                    self._save_failed = True
                    self._save_error = str(exc)
                    try:
                        self._update_save_status()
                        self._update_action_visibility()
                    except Exception:
                        pass
                    return False
            try:
                await asyncio.to_thread(_sessions.save_session, snapshot)
            except _sessions.SessionDurabilityUnconfirmed as exc:
                # New bytes remain, durability unconfirmed.
                if revision == self._save_revision:
                    self._saved_revision = revision
                self._save_failed = True
                self._save_error = str(exc)
                try:
                    self._update_save_status()
                    self._update_action_visibility()
                except Exception:
                    pass
                return False
            except (
                _sessions.SessionPersistenceError,
                _sessions.SessionValidationError,
                OSError,
            ) as exc:
                self._save_failed = True
                self._save_error = str(exc)
                try:
                    self._update_save_status()
                    self._update_action_visibility()
                except Exception:
                    pass
                return False
            # Deterministic barrier: an older save finishing after a newer
            # revision must not mark newer edits saved.
            if revision == self._save_revision:
                self._saved_revision = revision
                self._save_failed = False
                self._save_error = None
            else:
                # Newer edits arrived during save; schedule another pass.
                try:
                    self._schedule_draft_save(delay=0.05)
                except Exception:
                    pass
            try:
                self._update_save_status()
                self._update_action_visibility()
            except Exception:
                pass
            return not self._save_failed

    def _update_save_status(self) -> None:
        try:
            label = self.query_one("#chat-save-status", Static)
        except NoMatches:
            return
        if self._is_temporary:
            label.update("Temporary")
        elif self._save_failed:
            label.update("Save failed — retry save")
        elif self._save_revision != self._saved_revision:
            label.update("Saving…")
        else:
            label.update("Saved locally")

    def _update_action_visibility(self) -> None:
        try:
            reconcile = self.query_one("#chat-reconcile-row", Horizontal)
            reconcile.display = self._pending_reconcile is not None
        except NoMatches:
            pass
        try:
            save_row = self.query_one("#chat-save-row", Horizontal)
            save_row.display = self._save_failed
        except NoMatches:
            pass
        try:
            composer = self.query_one("#chat-input", ChatInput)
            blocked = self._save_failed or self._pending_reconcile is not None
            # Do not steal disabled state owned by the operation lease; only
            # reflect the session block when idle.
            if not self._turn_active and not self.tui.operations.is_busy:
                composer.disabled = blocked or self._read_only
        except (NoMatches, Exception):
            pass
        try:
            attachment_busy = (
                self._turn_active
                or self.tui.operations.is_busy
                or self._read_only
                or self._save_failed
                or self._attachment_picker_active
            )
            self.query_one("#btn-add-attachment", Button).disabled = attachment_busy
            self.query_one(
                "#btn-inspect-attachment", Button
            ).disabled = not self._draft_attachments
            self.query_one("#btn-remove-attachment", Button).disabled = (
                not self._draft_attachments or attachment_busy
            )
        except (NoMatches, Exception):
            pass
        try:
            send_btn = self.query_one("#btn-send", Button)
            if not self._turn_active and not self.tui.operations.is_busy:
                send_btn.disabled = (
                    self._save_failed
                    or self._pending_reconcile is not None
                    or self._read_only
                )
        except (NoMatches, Exception):
            pass

    @property
    def save_failed(self) -> bool:
        return self._save_failed

    @property
    def is_temporary(self) -> bool:
        return self._is_temporary

    def _get_max_ctx(self) -> int:
        v = self.tui.config.max_ctx
        if isinstance(v, int) and v > 0:
            return v
        return _MAX_CONTEXT_TOKENS_EST

    def _request_destination(self) -> str:
        rendered = f"[{self.tui.host}]" if ":" in self.tui.host else self.tui.host
        return f"http://{rendered}:{self.tui.port}"

    @staticmethod
    def _is_loopback_destination(host: str) -> bool:
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host.strip("[]")).is_loopback
        except ValueError:
            return False

    def _current_draft(self) -> str:
        try:
            return self.query_one("#chat-input", ChatInput).text
        except NoMatches:
            return self._session.draft if self._session is not None else ""

    def _prepare_window(
        self,
        draft: str,
        attachments: tuple[AttachmentSnapshot, ...],
    ) -> tuple[str, ContextWindow]:
        _, _, max_tokens = self.query_one(ParamsPane).read_values()
        rendered = render_user_content(draft, attachments)
        messages = list(self.messages)
        if draft.strip() or attachments:
            messages.append({"role": "user", "content": rendered})
        window = prepare_context(
            messages,
            self.tui.config.system,
            self._get_max_ctx(),
            max_tokens,
        )
        return rendered, window

    def _refresh_attachment_ui(self) -> None:
        try:
            from rich.text import Text  # noqa: PLC0415

            if not self._draft_attachments:
                summary = f"files: none · destination {self._request_destination()}"
            else:
                files = " · ".join(
                    f"{index}. {snapshot.selected_path} ({snapshot.byte_length} B, "
                    f"~{estimate_tokens(snapshot.content)} est tokens)"
                    for index, snapshot in enumerate(self._draft_attachments, 1)
                )
                summary = f"files: {files} · destination {self._request_destination()}"
            if self._context_error:
                summary += f" · {self._context_error}"
            self.query_one("#chat-attachments", Static).update(Text(summary))
        except NoMatches:
            pass

    def _current_window(self) -> tuple[str, ContextWindow] | None:
        draft = self._current_draft()
        if not self.messages and not draft.strip() and not self._draft_attachments:
            return None
        return self._prepare_window(draft, self._draft_attachments)

    def _included_turn_ids(self, window: ContextWindow) -> tuple[str, ...]:
        successful_ids = tuple(
            turn.turn_id
            for turn in (self._session.attempts if self._session else ())
            if turn.outcome == "success"
        )
        return successful_ids[window.excluded_turns // 2 :]

    @on(Button.Pressed, "#btn-context-preview")
    def _context_preview_pressed(self) -> None:
        try:
            prepared = self._current_window()
        except ContextLimitError as exc:
            self._context_error = exc.reason
            self._refresh_attachment_ui()
            self.tui.log_app(exc.reason, "yellow")
            return
        if prepared is None:
            self.tui.log_app("no request context to preview", "dim")
            return
        _, window = prepared
        lines = [
            f"Destination: {self._request_destination()}",
            f"Estimated input: {window.input_tokens} tokens",
            f"Reserved output: {window.reserved_tokens - window.input_tokens} tokens",
            f"Configured limit: {self._get_max_ctx()} tokens",
            f"Excluded messages: {window.excluded_turns}",
        ]
        for message in window.messages:
            lines.extend(("", f"[{message['role']}]", message["content"]))
        from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

        self.tui.push_screen(
            TextPreviewScreen(
                "\n".join(lines),
                title="Request context — exact retained messages",
            )
        )

    def _attachment_failed(self, message: str) -> None:
        self.tui.log_app(f"attachment rejected: {message}", "yellow")

    def _attachment_added(self, snapshot: AttachmentSnapshot) -> None:
        self._draft_attachments = (*self._draft_attachments, snapshot)
        self._attachment_index = len(self._draft_attachments) - 1
        try:
            self.query_one("#chat-attachment-index", Input).value = str(
                self._attachment_index + 1
            )
        except NoMatches:
            pass
        if self._session is not None:
            self._session = replace(
                self._session,
                attachments=self._draft_attachments,
                updated_at=self._now_iso(),
            )
        self._context_error = None
        if not self._is_temporary and not self._read_only:
            self._save_revision += 1
            self._schedule_draft_save()
        self._refresh_attachment_ui()
        self.refresh_context_bar()

    def _attachment_picker_finished(
        self, snapshot: AttachmentSnapshot | None, error: str | None
    ) -> None:
        self._attachment_picker_active = False
        if error is not None:
            self._attachment_failed(error)
        elif snapshot is not None:
            self._attachment_added(snapshot)
        self._update_action_visibility()

    @work(exclusive=True, group="attachment-picker", thread=True)
    def _choose_attachment_worker(self) -> None:
        try:
            selected = _choose_macos_file()
            snapshot = read_attachment(Path(selected)) if selected is not None else None
            error = None
        except AttachmentError as exc:
            snapshot = None
            error = str(exc)
        self.app.call_from_thread(self._attachment_picker_finished, snapshot, error)

    @on(Button.Pressed, "#btn-add-attachment")
    def _add_attachment_pressed(self) -> None:
        if self._turn_active or self.tui.operations.is_busy:
            self.tui.log_app(
                "finish the current turn before attaching a file", "yellow"
            )
            return
        if self._read_only or self._save_failed:
            self.tui.log_app(
                "session is not editable — retry save or open an editable session",
                "yellow",
            )
            return
        self._attachment_picker_active = True
        self._update_action_visibility()
        self._choose_attachment_worker()

    def _selected_attachment(self) -> tuple[int, AttachmentSnapshot] | None:
        if not self._draft_attachments:
            self.tui.log_app("no attached file", "dim")
            return None
        try:
            raw_index = self.query_one("#chat-attachment-index", Input).value.strip()
            index = int(raw_index) - 1
        except (NoMatches, ValueError):
            index = self._attachment_index
        if not 0 <= index < len(self._draft_attachments):
            self.tui.log_app("attachment number is out of range", "yellow")
            return None
        self._attachment_index = index
        return index, self._draft_attachments[index]

    @on(Button.Pressed, "#btn-inspect-attachment")
    def _inspect_attachment_pressed(self) -> None:
        selected = self._selected_attachment()
        if selected is None:
            return
        _, snapshot = selected
        from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

        self.tui.push_screen(
            TextPreviewScreen(
                snapshot.content,
                title=f"Attached file — {snapshot.selected_path}",
            )
        )

    @on(Button.Pressed, "#btn-remove-attachment")
    def _remove_attachment_pressed(self) -> None:
        selected = self._selected_attachment()
        if selected is None:
            return
        index, _ = selected
        self._draft_attachments = tuple(
            snapshot
            for position, snapshot in enumerate(self._draft_attachments)
            if position != index
        )
        self._attachment_index = min(index, max(0, len(self._draft_attachments) - 1))
        try:
            self.query_one("#chat-attachment-index", Input).value = str(
                self._attachment_index + 1
            )
        except NoMatches:
            pass
        if self._session is not None:
            self._session = replace(
                self._session,
                attachments=self._draft_attachments,
                updated_at=self._now_iso(),
            )
        if not self._is_temporary and not self._read_only:
            self._save_revision += 1
            self._schedule_draft_save()
        self._context_error = None
        self._refresh_attachment_ui()
        self.refresh_context_bar()

    def _send_blocked_reason(self) -> str | None:
        if self._read_only:
            return "session is locked read-only — open an editable copy"
        if self._save_failed:
            return "save failed — retry save or discard unsaved work"
        if self._pending_reconcile is not None:
            return "settings differ — use saved or keep current settings"
        return None

    @on(TextArea.Changed, "#chat-input")
    def _on_draft_changed(self, event: TextArea.Changed) -> None:
        if self._restoring or self._session is None:
            return
        try:
            draft_value = event.text_area.text
        except Exception:
            try:
                draft_value = event.control.text  # type: ignore[attr-defined]
            except Exception:
                return
        if self._read_only or self._is_temporary:
            # Temporary sessions never write; still update in-memory draft.
            if self._is_temporary and self._session is not None:
                self._session = replace(
                    self._session,
                    draft=draft_value,
                    attachments=self._draft_attachments,
                )
            self._update_save_status()
            self.refresh_context_bar()
            return
        self._ensure_session()
        assert self._session is not None
        self._session = replace(
            self._session,
            draft=draft_value,
            attachments=self._draft_attachments,
            updated_at=self._now_iso(),
        )
        self._save_revision += 1
        self._update_save_status()
        self._schedule_draft_save()
        self.refresh_context_bar()

    @on(Input.Changed, "#param-temp, #param-top-p, #param-max-tokens")
    def _on_param_changed(self, _event: Input.Changed) -> None:
        if self._restoring:
            return
        self.mark_next_request_dirty()

    @on(ChatInput.Submitted, "#chat-input")
    def _on_input_submitted(self, event: Any) -> None:
        raw = getattr(event, "text", None)
        if raw is None:
            raw = getattr(event, "value", "")
        if not isinstance(raw, str):
            raw = str(raw)
        editor = (
            getattr(event, "chat_input", None)
            or getattr(event, "input", None)
            or getattr(event, "control", None)
        )
        self._do_submit(raw, editor)

    @on(Button.Pressed, "#btn-send")
    def _send_pressed(self) -> None:
        try:
            composer = self.query_one("#chat-input", ChatInput)
        except NoMatches:
            return
        self._do_submit(composer.text, composer)

    def _do_submit(self, raw: str, editor: Any) -> None:  # noqa: PLR0911, PLR0912, PLR0915
        # Test emptiness with strip() but send the original text so code
        # indentation is preserved.
        if not raw.strip():
            return
        blocked = self._send_blocked_reason()
        if blocked is not None:
            self.tui.log_app(blocked, "yellow")
            return
        selected_model = self.tui.effective_model()
        if selected_model is None:
            self.tui.log_app(
                "select a model in Models (or set model in config) before chatting",
                "yellow",
            )
            return
        draft_attachments = tuple(self._draft_attachments)
        if draft_attachments and not self._is_loopback_destination(self.tui.host):
            self.tui.log_app(
                "file attachments are disabled for non-loopback destinations",
                "yellow",
            )
            return
        try:
            rendered, window = self._prepare_window(raw, draft_attachments)
        except ContextLimitError as exc:
            self._context_error = exc.reason
            self._refresh_attachment_ui()
            try:
                rejected = ChatTurn(raw)
                self.query_one("#chat-transcript", VerticalScroll).mount(rejected)
                rejected.add_notice(exc.reason, "yellow")
                rejected.end_attempt()
            except NoMatches:
                pass
            self.tui.log_app(exc.reason, "yellow")
            return
        except Exception as exc:
            try:
                editor.load_text(raw)
            except Exception:
                pass
            self.tui.log_app(f"chat setup failed: {exc.__class__.__name__}", "red")
            return
        self._context_error = None
        if not self.tui.operations.try_acquire(OperationKind.CHATTING):
            if self.tui.swap_busy:
                self.tui.log_app("model operation in progress — chat paused", "yellow")
            else:
                self.tui.log_app("operation already in progress", "yellow")
            return
        self._pending_draft = raw
        self._cancel_requested = False
        self._turn_active = True
        self._turn_started = False
        # Pre-send checkpoint state: capture normalized controls and the exact
        # model through the single dirty path; the worker persists the running
        # attempt before any HTTP.
        session_settings = self.capture_request_settings()
        running_turn = SessionTurn(
            turn_id=str(uuid.uuid4()),
            created_at=self._now_iso(),
            original_draft=raw,
            sent_content=rendered,
            settings=session_settings,
            attachments=draft_attachments,
            included_turn_ids=self._included_turn_ids(window),
        )
        self._active_session_turn_id = running_turn.turn_id
        self._progress_answer = ""
        self._progress_reasoning = ""
        self._progress_tools = {}
        self._progress_response_model = None
        self._last_checkpoint = time.monotonic()
        # Cancel any pending draft debounce; the pre-send checkpoint covers it.
        try:
            if self._draft_timer is not None:
                self._draft_timer.stop()
                self._draft_timer = None
        except Exception:
            pass
        if self._session is not None and not self._is_temporary and not self._read_only:
            self._session = replace(
                self._session,
                settings=session_settings,
                draft="",
                attachments=(),
                attempts=(*self._session.attempts, running_turn),
                updated_at=self._now_iso(),
            )
            self._save_revision += 1
            self._update_save_status()
        self._draft_attachments = ()
        self._attachment_index = 0
        self._refresh_attachment_ui()
        try:
            try:
                composer_widget = self.query_one("#chat-input", ChatInput)
            except NoMatches:
                composer_widget = None
            # Prefer the explicit editor when it is still mounted.
            target = editor if editor is not None else composer_widget
            if target is None:
                target = composer_widget
            had_focus = False
            try:
                had_focus = self.app.focused is target
            except Exception:
                had_focus = False
            if target is not None:
                try:
                    target.clear()
                except Exception:
                    try:
                        target.load_text("")
                    except Exception:
                        pass
                try:
                    target.disabled = True
                except Exception:
                    pass
            else:
                had_focus = False
            # Keep Send disabled while the lease is held.
            try:
                self.query_one("#btn-send", Button).disabled = True
            except (NoMatches, Exception):
                pass
            self._restore_composer_focus = had_focus
            self._active_turn = ChatTurn(rendered)
            self.query_one("#chat-transcript", VerticalScroll).mount(self._active_turn)
            self._queue_follow(force=True)
            self.tui.refresh_activity()
            temp, top_p, max_tok = self.query_one(ParamsPane).read_values()
            seed = self.tui.config.seed
            enable_thinking = self.tui.config.enable_thinking
            host = self.tui.host
            port = self.tui.port
            model_at_send = selected_model
            pending_user = {"role": "user", "content": rendered}
            rendered_host = f"[{host}]" if ":" in host else host
            url = f"http://{rendered_host}:{port}/v1/chat/completions"
            worker = self._run_turn(
                window,
                pending_user,
                temp,
                top_p,
                max_tok,
                model_at_send,
                seed,
                enable_thinking,
                url,
                port,
            )
        except Exception as exc:
            try:
                self._write_system_line(
                    f"chat failed to start: {exc.__class__.__name__}", "red"
                )
                self.tui.log_app(
                    f"chat failed to start: {exc.__class__.__name__}", "red"
                )
            except NoMatches:
                pass
            finally:
                # Startup failure never sends; drop the optimistic attempt but
                # keep the exact draft via _pending_draft for end_turn restore.
                self._drop_running_attempt()
                self.end_turn()
            return
        self._turn_worker = worker
        if self._cancel_requested and self._turn_started:
            worker.cancel()

    def apply_config_params(self, cfg: AppConfig) -> None:
        self._restoring = True
        try:
            self.query_one(ParamsPane).apply_config(cfg)
        finally:
            self._restoring = False
        # Every next-request mutation checkpoints through the single dirty path.
        if self._session is not None and not self._restoring:
            self.mark_next_request_dirty()

    def refresh_context_bar(self) -> None:
        try:
            prepared = self._current_window()
        except ContextLimitError as exc:
            self._context_error = exc.reason
            self.update_ctx_bar(self._get_max_ctx())
        except Exception:
            self._context_error = "context preview unavailable"
            self.update_ctx_bar(self._get_max_ctx())
        else:
            self._context_error = None
            if prepared is None:
                self.update_ctx_bar(0)
            else:
                _, window = prepared
                self.update_ctx_bar(window.reserved_tokens, window.excluded_turns)
        self._refresh_attachment_ui()

    def update_ctx_bar(self, ctx_len: int, excluded: int = 0) -> None:
        max_ctx = self._get_max_ctx()
        style = ctx_bar_style(ctx_len, max_ctx)
        try:
            bar = self.query_one("#ctx-progress", ProgressBar)
        except NoMatches:
            bar = None
        if bar is not None:
            bar.update(
                total=max_ctx if max_ctx > 0 else 8192,
                progress=max(0, min(ctx_len, max_ctx)),
            )
            bar.remove_class("ctx-bar-amber")
            bar.remove_class("ctx-bar-red")
            if style == "yellow":
                bar.add_class("ctx-bar-amber")
            elif style == "red":
                bar.add_class("ctx-bar-red")
        try:
            label = self.query_one("#ctx-bar", Static)
        except NoMatches:
            return
        label.update(ctx_bar_text(ctx_len, max_ctx, excluded=excluded))
        label.remove_class("ctx-bar-amber")
        label.remove_class("ctx-bar-red")
        if style == "yellow":
            label.add_class("ctx-bar-amber")
        elif style == "red":
            label.add_class("ctx-bar-red")

    @work(exclusive=True, group="chat")
    async def _run_turn(  # noqa: PLR0913, PLR0915, PLR0912, PLR0917
        self,
        window: ContextWindow,
        pending_user: dict[str, str],
        temp: float,
        top_p: float,
        max_tok: int,
        model_at_send: str,
        seed: int | None,
        enable_thinking: bool | None,
        url: str,
        port: int,
    ) -> None:
        ctx_len_estimate = 0
        reserved_ctx_len = 0
        excluded_turns = 0
        self._turn_started = True
        # Pre-send checkpoint: persist the draft and running attempt before HTTP.
        # If saving fails, retain the composer and send no request.
        if (
            self._session is not None
            and not self._is_temporary
            and not self._read_only
            and self._active_session_turn_id is not None
        ):
            saved = await self._save_session_now()
            if not saved:
                self._drop_running_attempt()
                self._write_system_line(
                    "save failed — retry save or discard unsaved work", "red"
                )
                # The early return bypasses the try/finally below, so release
                # the lease and restore the composer here.
                self.end_turn()
                return
        try:
            if self._cancel_requested or (
                self._turn_worker is not None
                and self._turn_worker.cancelled_event.is_set()
            ):
                self._record_cancelled(model_at_send, 0, 0)
                self._finalize_session_attempt(
                    outcome="cancelled",
                    error_category="cancelled",
                    error_detail="client request cancelled, engine state unknown",
                )
                await self._checkpoint_terminal()
                return
            cfg = self.tui.config
            if (
                cfg.temperature != temp
                or cfg.top_p != top_p
                or cfg.max_tokens != max_tok
            ):
                self.tui.config = replace(
                    cfg,
                    temperature=temp,
                    top_p=top_p,
                    max_tokens=max_tok,
                )
            ctx_len_estimate = window.input_tokens
            reserved_ctx_len = window.reserved_tokens
            excluded_turns = window.excluded_turns
            self.update_ctx_bar(window.reserved_tokens, window.excluded_turns)
            self._update_running_estimates(
                ctx_len_estimate,
                reserved_ctx_len,
                excluded_turns,
                self._included_turn_ids(window),
            )
            payload: dict[str, object] = {
                "messages": list(window.messages),
                "stream": True,
                "max_tokens": max_tok,
                "stream_options": {"include_usage": True},
                "temperature": temp,
                "top_p": top_p,
            }
            if seed is not None:
                payload["seed"] = seed
            if enable_thinking is not None:
                payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
            payload["model"] = model_at_send
            try:
                result = await stream_turn(
                    url,
                    payload,
                    prompt_estimate=window.input_tokens,
                    on_flush=self._update_stream,
                    on_activity=self._update_activity,
                    on_progress=self._on_turn_progress,
                )
            except asyncio.CancelledError:
                self._record_cancelled(
                    model_at_send, ctx_len_estimate, reserved_ctx_len
                )
                self._finalize_session_attempt(
                    outcome="cancelled",
                    error_category="cancelled",
                    error_detail="client request cancelled, engine state unknown",
                )
                await self._checkpoint_terminal()
                raise
            if self._cancel_requested:
                self._record_cancelled(
                    model_at_send, ctx_len_estimate, reserved_ctx_len
                )
                self._finalize_session_attempt(
                    outcome="cancelled",
                    error_category="cancelled",
                    error_detail="client request cancelled, engine state unknown",
                )
                await self._checkpoint_terminal()
                return
            if result.response_model != model_at_send:
                self.tui.record_generation_success(model_at_send, result.response_model)
                self._write_system_line(
                    "response identity missing or mismatched — request not verified",
                    "red",
                )
                self._finalize_session_attempt(
                    outcome="failed",
                    response_model=result.response_model,
                    finish_reason=result.finish_reason,
                    skipped_frames=result.skipped_frames,
                    stream_complete=result.stream_complete,
                    error_category="identity",
                    error_detail="response identity missing or mismatched",
                )
                await self._checkpoint_terminal()
                return
            self.tui.record_generation_success(model_at_send, result.response_model)
            outcome = self._classify_outcome(result)
            stamp = self._format_stamp(result, max_tok)
            notices = self._outcome_notices(result, max_tok, excluded_turns)
            # Final success still comes only from TurnResult and the pane's
            # identity check, never from a progress notification.
            self._finalize_session_attempt(
                outcome=outcome,
                answer=result.full_text,
                reasoning=result.reasoning_text,
                tool_calls=result.tool_calls,
                response_model=result.response_model,
                finish_reason=result.finish_reason,
                skipped_frames=result.skipped_frames,
                stream_complete=result.stream_complete,
                prompt_tokens=result.accounting.prompt_tokens,
                completion_tokens=result.accounting.completion_tokens,
                cached_prompt_tokens=result.cached_prompt_tokens,
                first_output_s=result.first_output_s,
                answer_started_s=result.answer_started_s,
                total_s=result.total_s,
            )
            if outcome == "success":
                self._record_success(
                    model_at_send,
                    result,
                    ctx_len_estimate,
                    reserved_ctx_len,
                    excluded_turns,
                )
                self._commit_success(
                    pending_user,
                    result.full_text,
                    stamp,
                    notices,
                    result.reasoning_text,
                    result.tool_calls,
                )
            else:
                self._record_outcome(
                    model_at_send,
                    result,
                    outcome,
                    ctx_len_estimate,
                    reserved_ctx_len,
                    excluded_turns,
                )
                # Length-capped, damaged, tool-only and empty outcomes stay visible
                # but neither side enters future history, so a retry resends
                # the same context. The pending draft is intentionally left set so
                # end_turn() restores it to the composer.
                self._complete_turn_ui(
                    result.full_text,
                    stamp,
                    notices,
                    result.reasoning_text,
                    result.tool_calls,
                )
            # A completed generation whose save fails remains visible in memory
            # and retains its outcome; further send/clear/switch stays blocked.
            await self._checkpoint_terminal()
        except ContextLimitError as exc:
            self._write_system_line(exc.reason, "yellow")
            self._finalize_session_attempt(
                outcome="failed",
                error_category="context",
                error_detail=exc.reason[:500],
            )
            await self._checkpoint_terminal()
        except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
            self.tui.record_generation_failure(cancelled=self._cancel_requested)
            if self._cancel_requested:
                self._record_cancelled(
                    model_at_send, ctx_len_estimate, reserved_ctx_len
                )
                self._finalize_session_attempt(
                    outcome="cancelled",
                    error_category="cancelled",
                    error_detail="client request cancelled, engine state unknown",
                )
            else:
                self._write_system_line(f"server unreachable :{port}", "red")
                self._finalize_session_attempt(
                    outcome="failed",
                    error_category="stream",
                    error_detail=f"server unreachable :{port}"[:500],
                )
            await self._checkpoint_terminal()
        except (httpx.ConnectError, httpx.TimeoutException):
            self.tui.record_generation_failure()
            self._write_system_line(f"server unreachable :{port}", "red")
            self._finalize_session_attempt(
                outcome="failed",
                error_category="transport",
                error_detail=f"server unreachable :{port}"[:500],
            )
            await self._checkpoint_terminal()
        except httpx.HTTPStatusError as exc:
            self.tui.record_generation_failure()
            detail = error_detail(exc.response)
            is_context_error = exc.response.status_code in {400, 413}
            category = "context" if is_context_error else "server"
            message = (
                "context rejected by server — reduce context or output allowance"
                if is_context_error
                else f"server error :{port} (HTTP {exc.response.status_code}){detail}"
            )
            self._write_system_line(
                message,
                "red",
            )
            self._finalize_session_attempt(
                outcome="failed",
                error_category=category,
                error_detail=(f"HTTP {exc.response.status_code}{detail}")[:500],
            )
            await self._checkpoint_terminal()
        except Exception as exc:
            self.tui.record_generation_failure()
            try:
                self.tui.log_app(
                    f"chat failed: {exc.__class__.__name__}: {exc}"[:300], "red"
                )
            except NoMatches:
                pass
            self._write_system_line(f"chat failed: {exc.__class__.__name__}", "red")
            self._finalize_session_attempt(
                outcome="failed",
                error_category="transport",
                error_detail=f"{exc.__class__.__name__}: {exc}"[:500],
            )
            await self._checkpoint_terminal()
        finally:
            self.end_turn()

    @staticmethod
    def _classify_outcome(result: TurnResult) -> str:
        if not result.stream_complete:
            if result.finish_reason == "length":
                return "length_capped"
            if result.tool_calls and not result.full_text:
                return "tool_only"
            if not result.full_text and not result.tool_calls:
                return "empty"
            return "incomplete"
        return "success"

    @staticmethod
    def _format_stamp(result: TurnResult, max_tok: int) -> str:
        _ = max_tok
        acct = result.accounting
        if result.stream_complete:
            in_label = (
                f"{acct.prompt_tokens}~"
                if acct.prompt_estimated
                else str(acct.prompt_tokens)
            )
            out_label = (
                f"{acct.completion_tokens}~"
                if acct.completion_estimated
                else str(acct.completion_tokens)
            )
        else:
            in_label = "—"
            out_label = "—"
        first = (
            f"{result.first_output_s:.2f}s"
            if result.first_output_s is not None
            else "—"
        )
        answer = (
            f"{result.answer_started_s:.2f}s"
            if result.answer_started_s is not None
            else "—"
        )
        total = f"{result.total_s:.2f}s"
        rate = (
            f"{acct.tok_s:.1f} client request tok/s"
            if result.stream_complete
            else "— client request tok/s"
        )
        stamp = (
            f"{in_label} in · {out_label} out · "
            f"first {first} · answer {answer} · total {total} · {rate}"
        )
        if result.cached_prompt_tokens is not None and result.stream_complete:
            stamp += f" · cached {result.cached_prompt_tokens} reuse"
        return stamp

    @staticmethod
    def _outcome_notices(
        result: TurnResult, max_tok: int, excluded_turns: int
    ) -> list[str]:
        notices: list[str] = []
        if result.skipped_frames:
            notices.append(f"{result.skipped_frames} malformed stream frame(s) skipped")
        if result.finish_reason == "length":
            notices.append(f"reply hit the {max_tok}-token cap — ask it to continue")
        if result.tool_calls and not result.full_text:
            notices.append("tool output only — not executed, not in next request")
        elif not result.full_text:
            notices.append("model returned no text")
        if not result.stream_complete and result.finish_reason != "length":
            notices.append("incomplete stream — not in next request")
        if excluded_turns > 0:
            notices.append(f"{excluded_turns} earlier message(s) excluded from request")
        return notices

    def _record_success(
        self,
        model_at_send: str,
        result: TurnResult,
        ctx_len_estimate: int,
        reserved_ctx_len: int,
        excluded_turns: int,
    ) -> None:
        acct = result.accounting
        ctx_len = acct.prompt_tokens if not acct.prompt_estimated else ctx_len_estimate
        record = TurnRecord(
            ts=time.time(),
            model=model_at_send,
            prompt_tok=acct.prompt_tokens,
            out_tok=acct.completion_tokens,
            first_output_s=result.first_output_s,
            answer_started_s=result.answer_started_s,
            total_s=result.total_s,
            req_tok_s=acct.tok_s,
            ctx_len=ctx_len,
            outcome="success",
            cancelled=False,
            cached_prompt_tokens=result.cached_prompt_tokens,
            prompt_estimated=acct.prompt_estimated,
            out_estimated=acct.completion_estimated,
            excluded_turns=excluded_turns,
        )
        self.tui.history.add(record)
        self.tui._refresh_metrics()
        self.update_ctx_bar(reserved_ctx_len, excluded_turns)

    def _record_outcome(  # noqa: PLR0913, PLR0917
        self,
        model_at_send: str,
        result: TurnResult,
        outcome: str,
        ctx_len_estimate: int,
        reserved_ctx_len: int,
        excluded_turns: int,
    ) -> None:
        record = TurnRecord(
            ts=time.time(),
            model=model_at_send,
            prompt_tok=None,
            out_tok=None,
            first_output_s=result.first_output_s,
            answer_started_s=result.answer_started_s,
            total_s=result.total_s,
            req_tok_s=None,
            ctx_len=ctx_len_estimate,
            outcome=outcome,
            cancelled=False,
            cached_prompt_tokens=result.cached_prompt_tokens,
            prompt_estimated=False,
            out_estimated=False,
            excluded_turns=excluded_turns,
        )
        self.tui.history.add(record)
        self.tui._refresh_metrics()
        self.update_ctx_bar(reserved_ctx_len, excluded_turns)

    def _drop_running_attempt(self) -> None:
        if self._session is None or self._active_session_turn_id is None:
            return
        turn_id = self._active_session_turn_id
        original: str | None = None
        for turn in self._session.attempts:
            if turn.turn_id == turn_id:
                original = turn.original_draft
                break
        remaining = tuple(t for t in self._session.attempts if t.turn_id != turn_id)
        draft = original if original is not None else self._session.draft
        self._session = replace(
            self._session,
            draft=draft,
            attempts=remaining,
            updated_at=self._now_iso(),
        )
        self._active_session_turn_id = None
        self._save_revision += 1

    def _update_running_estimates(
        self,
        estimated_input: int,
        reserved_output: int,
        excluded: int,
        included_turn_ids: tuple[str, ...],
    ) -> None:
        if self._session is None or self._active_session_turn_id is None:
            return
        attempts: list[SessionTurn] = []
        for turn in self._session.attempts:
            if turn.turn_id == self._active_session_turn_id:
                attempts.append(
                    replace(
                        turn,
                        included_turn_ids=included_turn_ids,
                        estimated_input=estimated_input,
                        reserved_output=reserved_output,
                        excluded_messages=excluded,
                    )
                )
            else:
                attempts.append(turn)
        self._session = replace(
            self._session, attempts=tuple(attempts), updated_at=self._now_iso()
        )

    def _on_turn_progress(self, progress: TurnProgress) -> None:
        # Capture progress directly from TurnProgress deltas; never infer
        # reasoning from the tool-count activity string.
        if progress.answer_delta:
            self._progress_answer += progress.answer_delta
        if progress.reasoning_delta:
            self._progress_reasoning += progress.reasoning_delta
        if progress.tool_fragments:
            for frag in progress.tool_fragments:
                try:
                    index = frag.get("index")
                    if not isinstance(index, int):
                        continue
                    slot = self._progress_tools.setdefault(
                        index, {"index": index, "arguments": ""}
                    )
                    for key in ("id", "type", "name"):
                        if key in frag and key not in slot:
                            slot[key] = frag[key]
                    args = frag.get("arguments")
                    if isinstance(args, str):
                        current = slot.get("arguments")
                        slot["arguments"] = (
                            current if isinstance(current, str) else ""
                        ) + args
                except Exception:
                    continue
        if progress.response_model:
            self._progress_response_model = progress.response_model
        # Stream at most once per second; terminal/switch/clear/shutdown flush.
        now = time.monotonic()
        if now - self._last_checkpoint >= 1.0:
            self._last_checkpoint = now
            self._save_session_async()

    def _merged_progress_tools(self) -> tuple[dict[str, Any], ...]:
        merged = tuple({**slot} for _, slot in sorted(self._progress_tools.items()))
        return tuple(
            tool
            for tool in merged
            if len(tool) > 1
            and not (set(tool) == {"index", "arguments"} and not tool["arguments"])
        )

    def _finalize_session_attempt(  # noqa: PLR0913, PLR0917
        self,
        outcome: str,
        answer: str | None = None,
        reasoning: str | None = None,
        tool_calls: tuple[dict[str, object], ...] | None = None,
        response_model: str | None = None,
        finish_reason: str | None = None,
        skipped_frames: int | None = None,
        stream_complete: bool | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cached_prompt_tokens: int | None = None,
        first_output_s: float | None = None,
        answer_started_s: float | None = None,
        total_s: float | None = None,
        error_category: str | None = None,
        error_detail: str | None = None,  # noqa: A002
    ) -> None:
        if self._session is None or self._active_session_turn_id is None:
            return
        if self._is_temporary:
            return
        final_answer = answer if answer is not None else self._progress_answer
        final_reasoning = (
            reasoning if reasoning is not None else self._progress_reasoning
        )
        final_tools = (
            tuple(dict(call) for call in tool_calls)
            if tool_calls is not None
            else tuple(dict(call) for call in self._merged_progress_tools())
        )
        final_response = (
            response_model
            if response_model is not None
            else self._progress_response_model
        )
        attempts: list[SessionTurn] = []
        for turn in self._session.attempts:
            if turn.turn_id != self._active_session_turn_id:
                attempts.append(turn)
                continue
            attempts.append(
                replace(
                    turn,
                    answer=final_answer,
                    reasoning=final_reasoning,
                    tool_calls=final_tools,  # type: ignore[arg-type]
                    response_model=final_response,
                    finish_reason=finish_reason
                    if finish_reason is not None
                    else turn.finish_reason,
                    skipped_frames=skipped_frames
                    if skipped_frames is not None
                    else turn.skipped_frames,
                    stream_complete=stream_complete
                    if stream_complete is not None
                    else turn.stream_complete,
                    outcome=outcome,
                    prompt_tokens=prompt_tokens
                    if prompt_tokens is not None
                    else turn.prompt_tokens,
                    completion_tokens=completion_tokens
                    if completion_tokens is not None
                    else turn.completion_tokens,
                    cached_prompt_tokens=cached_prompt_tokens
                    if cached_prompt_tokens is not None
                    else turn.cached_prompt_tokens,
                    first_output_s=first_output_s
                    if first_output_s is not None
                    else turn.first_output_s,
                    answer_started_s=answer_started_s
                    if answer_started_s is not None
                    else turn.answer_started_s,
                    total_s=total_s if total_s is not None else turn.total_s,
                    error_category=error_category,
                    error_detail=error_detail,
                )
            )
        total_tokens: int | None = None
        if prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
        if total_tokens is not None:
            attempts = [
                replace(turn, total_tokens=total_tokens)
                if turn.turn_id == self._active_session_turn_id
                and turn.outcome == outcome
                else turn
                for turn in attempts
            ]
        self._session = replace(
            self._session, attempts=tuple(attempts), updated_at=self._now_iso()
        )
        self._save_revision += 1

    async def _checkpoint_terminal(self) -> None:
        if self._session is None or self._is_temporary or self._read_only:
            self._active_session_turn_id = None
            return
        # Immediate checkpoint at terminal outcome.
        ok = await self._save_session_now()
        self._active_session_turn_id = None
        if not ok:
            try:
                self.tui.log_app(
                    "save failed — retry save or discard unsaved work", "red"
                )
            except Exception:
                pass

    def abort(self) -> None:
        if not self._turn_active:
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.tui.refresh_activity()
        self._write_system_line("cancellation requested", "dim")
        # Pre-start: let the coroutine's idempotent check settle once without
        # cancelling its task (cancelling before first run would skip cleanup).
        if not self._turn_started:
            return
        worker = self._turn_worker
        if worker is not None:
            worker.cancel()

    async def wait_for_cleanup(self) -> None:
        worker = self._turn_worker
        if worker is None:
            return
        try:
            await worker.wait()
        except Exception:
            pass

    def _focus_changed(self) -> None:
        self._restore_composer_focus = False

    def _scroll_changed(self, old: float, new: float) -> None:
        if new < old:
            self._follow_from = None

    def on_resize(self, event: events.Resize) -> None:
        self._follow_from = None

    def _queue_follow(self, *, force: bool = False) -> None:
        viewport = self.query_one("#chat-transcript", VerticalScroll)
        if not self.is_on_screen or self.tui.query_one(TabbedContent).active != "chat":
            return
        if force or viewport.is_vertical_scroll_end:
            self._follow_from = viewport.scroll_y
            if not self._follow_scheduled:
                self._follow_scheduled = True
                self.call_after_refresh(self._follow_after_layout)

    def _follow_after_layout(self) -> None:
        self._follow_scheduled = False
        before, self._follow_from = self._follow_from, None
        if before is None or not self.is_on_screen:
            return
        viewport = self.query_one("#chat-transcript", VerticalScroll)
        if (
            self.tui.query_one(TabbedContent).active == "chat"
            and viewport.scroll_y >= before
            and viewport.scroll_target_y >= before
        ):
            viewport.scroll_end(animate=False, immediate=True, x_axis=False)

    def _update_stream(self, text: str) -> None:
        if self._active_turn is not None:
            self._queue_follow()
            self._active_turn.update_response(text)

    def _update_activity(self, text: str) -> None:
        if self._active_turn is not None:
            self._queue_follow()
            self._active_turn.update_activity(text)

    def _commit_success(  # noqa: PLR0913, PLR0917
        self,
        pending_user: dict[str, str],
        full_text: str,
        stamp: str,
        notices: list[str],
        reasoning: str = "",
        tool_calls: tuple[dict[str, object], ...] = (),
    ) -> None:
        # One UI callback with no await between writes: commit pair together.
        self.messages.append(pending_user)
        if full_text:
            self.messages.append({"role": "assistant", "content": full_text})
        self._pending_draft = None
        self._complete_turn_ui(full_text, stamp, notices, reasoning, tool_calls)

    def _complete_turn_ui(
        self,
        full_text: str,
        stamp: str,
        notices: list[str],
        reasoning: str = "",
        tool_calls: tuple[dict[str, object], ...] = (),
    ) -> None:
        if self._active_turn is not None:
            self._queue_follow()
            self._active_turn.finish_response(
                full_text, stamp, notices, reasoning, tool_calls
            )

    def _record_cancelled(
        self,
        model_at_send: str,
        ctx_len_estimate: int,
        reserved_ctx_len: int,
    ) -> None:
        self.tui.record_generation_failure(cancelled=True)
        record = TurnRecord(
            ts=time.time(),
            model=model_at_send,
            prompt_tok=None,
            out_tok=None,
            first_output_s=None,
            answer_started_s=None,
            total_s=None,
            req_tok_s=None,
            ctx_len=ctx_len_estimate,
            outcome="cancelled",
            cancelled=True,
        )
        self.tui.history.add(record)
        self.tui._refresh_metrics()
        self.update_ctx_bar(reserved_ctx_len)
        self._write_system_line(
            "cancelled — client request cancelled, engine state unknown", "dim"
        )

    def _write_system_line(self, message: str, style: str) -> None:
        if self._active_turn is not None:
            self._active_turn.add_notice(message, style)

    def end_turn(self) -> None:
        self.tui.operations.release(OperationKind.CHATTING)
        turn, self._active_turn = self._active_turn, None
        self._turn_worker = None
        self._turn_active = False
        self._turn_started = False
        restore_focus, self._restore_composer_focus = (
            self._restore_composer_focus,
            False,
        )
        try:
            if turn is not None:
                turn.end_attempt()
        finally:
            try:
                inp = self.query_one("#chat-input", ChatInput)
            except NoMatches:
                self._pending_draft = None
            else:
                if self._pending_draft is not None and not inp.text:
                    self._restoring = True
                    try:
                        inp.load_text(self._pending_draft)
                    finally:
                        self._restoring = False
                self._pending_draft = None
                try:
                    self._update_action_visibility()
                    # Preserve the session block without stealing lease state.
                    if self._turn_active or self.tui.operations.is_busy:
                        inp.disabled = True
                    else:
                        blocked = (
                            self._save_failed
                            or self._pending_reconcile is not None
                            or self._read_only
                        )
                        inp.disabled = blocked
                except Exception:
                    try:
                        inp.disabled = self.tui.operations.is_busy
                    except Exception:
                        pass
                if (
                    restore_focus
                    and not inp.disabled
                    and self.is_mounted
                    and self.app.screen is self.screen
                    and self.tui.query_one(TabbedContent).active == "chat"
                ):
                    inp.focus()
            self.tui.refresh_activity()

    # -- Session controls (2g), reopen (2i), shutdown (2j) --

    def _release_session_lock(self) -> None:
        cm, self._session_lock_cm = self._session_lock_cm, None
        self._session_lock = None
        if cm is not None:
            try:
                cm.__exit__(None, None, None)  # type: ignore[attr-defined]
            except Exception:
                pass

    def _clear_transcript_widgets(self) -> None:
        try:
            viewport = self.query_one("#chat-transcript", VerticalScroll)
        except NoMatches:
            return
        for child in list(viewport.children):
            if isinstance(child, ChatTurn):
                child.remove()

    def _render_session(self, session: ChatSession) -> None:
        """Display the transcript with current runtime state unchanged."""
        self._restoring = True
        try:
            self._draft_attachments = session.attachments
            self._attachment_index = max(0, len(self._draft_attachments) - 1)
            self._clear_transcript_widgets()
            # Session attempts are authoritative; messages stays the projection.
            self.messages = _sessions.request_messages(session)
            try:
                viewport = self.query_one("#chat-transcript", VerticalScroll)
            except NoMatches:
                viewport = None
            for turn in session.attempts:
                widget = render_session_turn(turn)
                if viewport is not None:
                    viewport.mount(widget)
            try:
                composer = self.query_one("#chat-input", ChatInput)
                composer.load_text(session.draft)
                self.query_one("#chat-attachment-index", Input).value = str(
                    self._attachment_index + 1
                )
            except NoMatches:
                pass
            self.refresh_context_bar()
        finally:
            self._restoring = False

    def _settings_differ(
        self, saved: RequestSettings, current: RequestSettings
    ) -> bool:
        return (
            saved.model != current.model
            or saved.temperature != current.temperature
            or saved.top_p != current.top_p
            or saved.max_tokens != current.max_tokens
            or saved.max_ctx != current.max_ctx
            or saved.system != current.system
            or saved.seed != current.seed
            or saved.enable_thinking != current.enable_thinking
            or saved.profile_id != current.profile_id
            or saved.profile_fingerprint != current.profile_fingerprint
        )

    async def open_session(self, session_id: str) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
        """Acquire the lock before replacing the active view."""
        if self._turn_active or self.tui.operations.is_busy:
            self.tui.log_app("finish the current turn before switching", "yellow")
            return False
        if self._save_failed:
            self.tui.log_app(
                "save failed — retry save or discard unsaved work", "yellow"
            )
            return False
        # Immediate checkpoint at session switch.
        if (
            self._session is not None
            and not self._is_temporary
            and not self._read_only
            and self._has_meaningful_content(self._session)
            and self._save_revision != self._saved_revision
        ):
            await self._save_session_now()
            if self._save_failed:
                return False
        try:
            directory = _sessions.session_dir()
            target = directory / f"{session_id}.json"
        except _sessions.SessionValidationError as exc:
            self.tui.log_app(f"invalid session id: {exc}", "red")
            return False
        # Try to acquire the advisory lock first.
        lock_cm: Any | None = None
        lock_handle: SessionLock | None = None
        read_only = False
        try:
            lock_cm = _sessions.lock_session(session_id)
            lock_handle = lock_cm.__enter__()  # type: ignore[attr-defined]
        except _sessions.SessionLockedError:
            read_only = True
        except _sessions.SessionPersistenceError as exc:
            self.tui.log_app(f"cannot open session: {exc}", "red")
            return False
        try:
            try:
                if read_only:
                    loaded = _sessions.load_session(target)
                else:
                    assert lock_handle is not None
                    loaded = _sessions.load_session(target, lock_handle=lock_handle)
            except (
                _sessions.SessionPersistenceError,
                _sessions.SessionValidationError,
            ) as exc:
                self.tui.log_app(f"cannot open session: {exc}", "red")
                if lock_cm is not None:
                    try:
                        lock_cm.__exit__(None, None, None)  # type: ignore[attr-defined]
                    except Exception:
                        pass
                return False
            self._release_session_lock()
            self._session = loaded
            self._session_lock_cm = lock_cm
            self._session_lock = lock_handle
            self._is_temporary = False
            self._read_only = read_only
            self._save_revision = 0
            self._saved_revision = 0
            self._save_failed = False
            self._save_error = None
            self._pending_reconcile = None
            self._active_session_turn_id = None
            self._render_session(loaded)
            # Reopening adds no metrics and no network request.
            current = self.capture_request_settings()
            if self._settings_differ(loaded.settings, current):
                self._pending_reconcile = loaded.settings
                self.tui.log_app(
                    "saved request settings differ — choose which to use", "yellow"
                )
            # A recovered running attempt restores its original draft only if
            # there is no newer saved composer draft.
            for turn in reversed(loaded.attempts):
                if turn.outcome == "interrupted" and not loaded.draft.strip():
                    try:
                        composer = self.query_one("#chat-input", ChatInput)
                        if not composer.text.strip():
                            self._restoring = True
                            try:
                                composer.load_text(turn.original_draft)
                            finally:
                                self._restoring = False
                    except NoMatches:
                        pass
                    break
            self._update_save_status()
            self._update_action_visibility()
            if read_only:
                self.tui.log_app("session is locked elsewhere — read-only", "yellow")
            return True
        except Exception as exc:
            if lock_cm is not None and not read_only:
                try:
                    lock_cm.__exit__(None, None, None)  # type: ignore[attr-defined]
                except Exception:
                    pass
            self.tui.log_app(f"cannot open session: {exc.__class__.__name__}", "red")
            return False

    def _start_empty_session(self, *, temporary: bool) -> None:
        if self._turn_active or self.tui.operations.is_busy:
            self.tui.log_app("finish the current turn before switching", "yellow")
            return
        if self._save_failed:
            self.tui.log_app(
                "save failed — retry save or discard unsaved work", "yellow"
            )
            return
        if self._is_temporary and not temporary and self._session is not None:
            has_work = bool(
                self._session.draft.strip() or len(self._session.attempts) > 0
            )
            if has_work:
                self._confirm_discard(
                    lambda: self._start_empty_session(temporary=temporary)
                )
                return
        self._release_session_lock()
        settings = self.capture_request_settings()
        self._session = _sessions.new_session(settings, draft="")
        self._is_temporary = temporary
        self._read_only = False
        self._save_revision = 0
        self._saved_revision = 0
        self._save_failed = False
        self._save_error = None
        self._pending_reconcile = None
        self._active_session_turn_id = None
        self._render_session(self._session)
        self._update_save_status()
        self._update_action_visibility()

    def _confirm_discard(self, proceed: Any) -> None:
        from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

        def _done(result: bool | None) -> None:
            if result is True:
                self._save_failed = False
                self._save_error = None
                proceed()

        try:
            self.tui.push_screen(ConfirmScreen("Discard unsaved work?"), _done)
        except Exception:
            pass

    @on(Button.Pressed, "#btn-sessions")
    def _open_picker(self) -> None:
        if self._turn_active:
            self.tui.log_app("finish the current turn before switching", "yellow")
            return
        if self._save_failed:
            self.tui.log_app(
                "save failed — retry save or discard unsaved work", "yellow"
            )
            return
        from mlx_tui.session_screen import SessionScreen  # noqa: PLC0415

        def _done(session_id: str | None) -> None:
            if session_id:
                asyncio.ensure_future(self.open_session(session_id))

        try:
            self.tui.push_screen(SessionScreen(), _done)
        except Exception as exc:
            self.tui.log_app(f"cannot open sessions: {exc.__class__.__name__}", "red")

    @on(Button.Pressed, "#btn-new-session")
    def _new_session_pressed(self) -> None:
        self._start_empty_session(temporary=False)

    @on(Button.Pressed, "#btn-new-temp")
    def _new_temp_pressed(self) -> None:
        # Creating a temporary session never writes its draft or output.
        self._start_empty_session(temporary=True)

    @on(Button.Pressed, "#btn-clear-chat")
    def _clear_pressed(self) -> None:
        if self._turn_active or self.tui.operations.is_busy:
            self.tui.log_app("finish the current turn before clearing", "yellow")
            return
        if self._save_failed:
            self.tui.log_app(
                "save failed — retry save or discard unsaved work", "yellow"
            )
            return
        if self._read_only:
            self.tui.log_app("session is locked read-only", "yellow")
            return
        from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

        def _done(result: bool | None) -> None:
            if result is True:
                asyncio.ensure_future(self._clear_session())

        try:
            self.tui.push_screen(ConfirmScreen("Clear this session?"), _done)
        except Exception:
            pass

    async def _clear_session(self) -> None:
        if self._session is None:
            return
        if self._is_temporary:
            self._session = replace(
                _sessions.new_session(self.capture_request_settings(), draft=""),
                updated_at=self._now_iso(),
            )
            self._render_session(self._session)
            self._update_save_status()
            self._update_action_visibility()
            return
        cleared = replace(
            self._session,
            draft="",
            attachments=(),
            attempts=(),
            updated_at=self._now_iso(),
        )
        self._session = cleared
        self._save_revision += 1
        # Clear removes attempts/draft after successful replacement.
        ok = await self._save_session_now()
        if not ok:
            return
        self._render_session(cleared)
        self._update_save_status()
        self._update_action_visibility()

    @on(Button.Pressed, "#btn-delete-session")
    def _delete_pressed(self) -> None:
        if self._turn_active or self.tui.operations.is_busy:
            self.tui.log_app("finish the current turn before deleting", "yellow")
            return
        if self._session is None:
            return
        if self._is_temporary:
            self._start_empty_session(temporary=False)
            return
        from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

        def _done(result: bool | None) -> None:
            if result is True:
                asyncio.ensure_future(self._delete_session())

        try:
            self.tui.push_screen(ConfirmScreen("Delete this session?"), _done)
        except Exception:
            pass

    async def _delete_session(self) -> None:
        if self._session is None or self._is_temporary:
            return
        session_id = self._session.session_id
        # Delete consumes the already-held lock handle; do not release and
        # reacquire around deletion.
        try:
            if self._session_lock is not None:
                await asyncio.to_thread(
                    _sessions.delete_session, session_id, self._session_lock
                )
            else:
                await asyncio.to_thread(_sessions.delete_session, session_id)
        except (
            _sessions.SessionPersistenceError,
            _sessions.SessionValidationError,
        ) as exc:
            self.tui.log_app(f"delete failed: {exc}", "red")
            return
        finally:
            self._release_session_lock()
        # Neither clear nor delete clears comparison results or Metrics.
        settings = self.capture_request_settings()
        self._session = _sessions.new_session(settings, draft="")
        self._is_temporary = False
        self._read_only = False
        self._save_revision = 0
        self._saved_revision = 0
        self._save_failed = False
        self._pending_reconcile = None
        self._render_session(self._session)
        self._update_save_status()
        self._update_action_visibility()

    _RETRYABLE_OUTCOMES = frozenset(
        {
            "failed",
            "cancelled",
            "length_capped",
            "tool_only",
            "empty",
            "incomplete",
            "interrupted",
        }
    )

    def _last_retryable_turn(self) -> SessionTurn | None:
        if self._session is None:
            return None
        for turn in reversed(self._session.attempts):
            if turn.outcome in self._RETRYABLE_OUTCOMES:
                return turn
        return None

    @on(Button.Pressed, "#btn-retry-request")
    def _retry_request_pressed(self) -> None:
        # Retry restores the most recent unsuccessful draft/settings for review
        # without sending automatically. Previous attempts stay visible; a
        # normal Send creates exactly one new attempt via the operation lease.
        if self._turn_active or self.tui.operations.is_busy:
            self.tui.log_app("finish the current turn before retrying", "yellow")
            return
        if self._save_failed:
            self.tui.log_app(
                "save failed — retry save or discard unsaved work", "yellow"
            )
            return
        if self._read_only:
            self.tui.log_app("session is locked read-only", "yellow")
            return
        turn = self._last_retryable_turn()
        if turn is None:
            self.tui.log_app("no failed turn to retry", "dim")
            return
        try:
            composer = self.query_one("#chat-input", ChatInput)
            current = composer.text
        except NoMatches:
            current = ""
        if current and current != turn.original_draft:
            # Retain the newer draft and offer explicit replacement.
            from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

            def _done(result: bool | None, _turn: SessionTurn = turn) -> None:
                if result is True:
                    self._apply_retry_draft(_turn)

            try:
                self.tui.push_screen(
                    ConfirmScreen("Replace current draft with retry draft?"), _done
                )
            except Exception:
                pass
            return
        self._apply_retry_draft(turn)

    def _apply_retry_draft(self, turn: SessionTurn) -> None:
        try:
            composer = self.query_one("#chat-input", ChatInput)
        except NoMatches:
            return
        self._restoring = True
        try:
            composer.load_text(turn.original_draft)
        finally:
            self._restoring = False
        if self._session is not None:
            self._session = replace(
                self._session,
                draft=turn.original_draft,
                attachments=turn.attachments,
                updated_at=self._now_iso(),
            )
            self._save_revision += 1
        self._draft_attachments = turn.attachments
        self._attachment_index = max(0, len(self._draft_attachments) - 1)
        self._refresh_attachment_ui()
        self._restore_retry_settings(turn.settings)
        self._update_save_status()
        self._schedule_draft_save()
        try:
            if not composer.disabled:
                composer.focus()
        except Exception:
            pass
        self.tui.log_app("retry draft restored — review and Send", "dim")

    def _restore_retry_settings(self, saved: RequestSettings) -> None:
        try:
            self.query_one(ParamsPane).apply_values(
                saved.temperature, saved.top_p, saved.max_tokens
            )
        except Exception:
            pass
        try:
            cfg = self.tui.config
            merged = replace(
                cfg,
                temperature=saved.temperature,
                top_p=saved.top_p,
                max_tokens=saved.max_tokens,
                seed=saved.seed,
                enable_thinking=saved.enable_thinking,
                system=saved.system or None,
                max_ctx=saved.max_ctx,
            )
            # Merge request fields only; never restore credentials/commands.
            self.tui.config = merged
            if saved.model is not None and saved.model != self.tui.effective_model():
                try:
                    self.tui.select_model(saved.model)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.mark_next_request_dirty()
        except Exception:
            pass

    @on(Button.Pressed, "#btn-retry-save")
    def _retry_save_pressed(self) -> None:
        # Retry save performs no HTTP request.
        asyncio.ensure_future(self._retry_save())

    async def _retry_save(self) -> bool:
        if self._is_temporary or self._read_only:
            return True
        ok = await self._save_session_now()
        if ok:
            try:
                self.tui.log_app("saved locally", "dim")
            except Exception:
                pass
        return ok

    @on(Button.Pressed, "#btn-discard-unsaved")
    def _discard_pressed(self) -> None:
        def _proceed() -> None:
            self._save_failed = False
            self._save_error = None
            # Explicit discard unblocks without sending.
            if self._session is not None:
                self._save_revision += 1
                self._saved_revision = self._save_revision
            self._update_save_status()
            self._update_action_visibility()

        self._confirm_discard(_proceed)

    @on(Button.Pressed, "#btn-use-saved")
    def _use_saved_pressed(self) -> None:
        saved = self._pending_reconcile
        if saved is None:
            return
        asyncio.ensure_future(self._apply_saved_settings(saved))

    async def _apply_saved_settings(self, saved: RequestSettings) -> None:
        # Verify any known exact cached revision/profile fingerprint first.
        if saved.repo_id is not None and saved.revision is not None:
            try:
                from mlx_tui.models import resolve_cached_snapshot  # noqa: PLC0415

                await asyncio.to_thread(
                    resolve_cached_snapshot, saved.repo_id, saved.revision
                )
            except Exception:
                self.tui.log_app(
                    "saved model is missing or changed — see Models/Compare recovery",
                    "yellow",
                )
        if saved.profile_id is not None and saved.profile_fingerprint is not None:
            entry = self.tui.profile_entry(saved.profile_id)
            if entry is None or entry.profile.fingerprint != saved.profile_fingerprint:
                self.tui.log_app(
                    "saved profile changed — see Models/Compare recovery", "yellow"
                )
        # Merge only request fields into the current config; never restore
        # endpoint credentials, commands, process identity or runtime ownership.
        from dataclasses import replace as _replace  # noqa: PLC0415

        cfg = self.tui.config
        merged = _replace(
            cfg,
            temperature=saved.temperature,
            top_p=saved.top_p,
            max_tokens=saved.max_tokens,
            seed=saved.seed,
            enable_thinking=saved.enable_thinking,
            system=saved.system or None,
            max_ctx=saved.max_ctx,
        )
        self.tui.restore_request_state(merged, saved.model)
        try:
            self.apply_config_params(merged)
        except Exception:
            pass
        self._pending_reconcile = None
        # Checkpoint both reconciliation choices through the single dirty path.
        self.mark_next_request_dirty()
        ok = await self._save_session_now()
        if ok:
            self._update_action_visibility()

    @on(Button.Pressed, "#btn-keep-current")
    def _keep_current_pressed(self) -> None:
        if self._pending_reconcile is None:
            return
        self._pending_reconcile = None
        # Checkpoint the continue-with-current choice as well.
        self.mark_next_request_dirty()
        asyncio.ensure_future(self._save_session_now())

    async def flush_for_shutdown(self) -> bool:
        """Immediate checkpoint at orderly shutdown; idempotent."""
        if self._shutdown_flushed:
            return not self._save_failed
        self._shutdown_flushed = True
        if self._session is None or self._is_temporary or self._read_only:
            return True
        try:
            composer = self.query_one("#chat-input", ChatInput)
            draft = composer.text
        except NoMatches:
            draft = None
        if (
            draft is not None
            and self._session is not None
            and draft != self._session.draft
        ):
            self._session = replace(
                self._session, draft=draft, updated_at=self._now_iso()
            )
            self._save_revision += 1
        return await self._save_session_now()

    def on_unmount(self) -> None:
        # Best-effort synchronous release; async flush lives in app.on_unmount.
        try:
            if self._draft_timer is not None:
                self._draft_timer.stop()
        except Exception:
            pass
        self._release_session_lock()
