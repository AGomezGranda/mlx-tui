"""Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast, override

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Input, ProgressBar, Static, TabbedContent, TextArea
from textual.worker import Worker

import mlx_tui.sessions.queries as _sessions_queries
from mlx_tui.attachments import (
    AttachmentSnapshot,
)
from mlx_tui.chat import (
    TurnProgress,
    TurnResult,
)
from mlx_tui.chat_turn import ChatTurn, render_session_turn
from mlx_tui.config import AppConfig
from mlx_tui.history.tokens import (
    ContextWindow,
)
from mlx_tui.params import ParamsPane
from mlx_tui.sessions.models import ChatSession, RequestSettings, SessionTurn
from mlx_tui.sessions.store import SessionLock

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


import mlx_tui.chat_ui.context as _chat_context
import mlx_tui.chat_ui.persistence as _chat_persistence
import mlx_tui.chat_ui.turns as _chat_turns
from mlx_tui.chat_ui.widgets import ChatInput


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

    def _write_system_line(self, message: str, style: str) -> None:
        if self._active_turn is not None:
            self._active_turn.add_notice(message, style)

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
            self.messages = _sessions_queries.request_messages(session)
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

    def on_unmount(self) -> None:
        # Best-effort synchronous release; async flush lives in app.on_unmount.
        try:
            if self._draft_timer is not None:
                self._draft_timer.stop()
        except Exception:
            pass
        self._release_session_lock()

    def _ensure_session(self) -> ChatSession:
        return _chat_persistence._ensure_session(self)

    def capture_request_settings(self) -> RequestSettings:
        return _chat_persistence.capture_request_settings(self)

    def mark_next_request_dirty(self) -> None:
        return _chat_persistence.mark_next_request_dirty(self)

    def _schedule_draft_save(self, delay: float = 0.25) -> None:
        return _chat_persistence._schedule_draft_save(self, delay)

    def _draft_timer_fired(self) -> None:
        return _chat_persistence._draft_timer_fired(self)

    def _save_session_async(self) -> None:
        return _chat_persistence._save_session_async(self)

    def _has_meaningful_content(self, session: ChatSession) -> bool:
        return _chat_persistence._has_meaningful_content(self, session)

    def _snapshot_for_save(self) -> ChatSession | None:
        return _chat_persistence._snapshot_for_save(self)

    async def _save_session_now(self) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
        return await _chat_persistence._save_session_now(self)

    def _update_save_status(self) -> None:
        return _chat_persistence._update_save_status(self)

    def _update_action_visibility(self) -> None:
        return _chat_persistence._update_action_visibility(self)

    @property
    def save_failed(self) -> bool:
        return _chat_persistence.save_failed(self)

    @property
    def is_temporary(self) -> bool:
        return _chat_persistence.is_temporary(self)

    def _get_max_ctx(self) -> int:
        return _chat_context._get_max_ctx(self)

    def _request_destination(self) -> str:
        return _chat_context._request_destination(self)

    @staticmethod
    def _is_loopback_destination(host: str) -> bool:
        return _chat_context._is_loopback_destination(host)

    def _current_draft(self) -> str:
        return _chat_context._current_draft(self)

    def _prepare_window(
        self,
        draft: str,
        attachments: tuple[AttachmentSnapshot, ...],
    ) -> tuple[str, ContextWindow]:
        return _chat_context._prepare_window(self, draft, attachments)

    def _refresh_attachment_ui(self) -> None:
        return _chat_context._refresh_attachment_ui(self)

    def _current_window(self) -> tuple[str, ContextWindow] | None:
        return _chat_context._current_window(self)

    def _included_turn_ids(self, window: ContextWindow) -> tuple[str, ...]:
        return _chat_context._included_turn_ids(self, window)

    @on(Button.Pressed, "#btn-context-preview")
    def _context_preview_pressed(self) -> None:
        return _chat_context._context_preview_pressed(self)

    def _attachment_failed(self, message: str) -> None:
        return _chat_context._attachment_failed(self, message)

    def _attachment_added(self, snapshot: AttachmentSnapshot) -> None:
        return _chat_context._attachment_added(self, snapshot)

    def _attachment_picker_finished(
        self, snapshot: AttachmentSnapshot | None, error: str | None
    ) -> None:
        return _chat_context._attachment_picker_finished(self, snapshot, error)

    @work(exclusive=True, group="attachment-picker", thread=True)
    def _choose_attachment_worker(self) -> None:
        return _chat_context._choose_attachment_worker(self)

    @on(Button.Pressed, "#btn-add-attachment")
    def _add_attachment_pressed(self) -> None:
        return _chat_context._add_attachment_pressed(self)

    def _selected_attachment(self) -> tuple[int, AttachmentSnapshot] | None:
        return _chat_context._selected_attachment(self)

    @on(Button.Pressed, "#btn-inspect-attachment")
    def _inspect_attachment_pressed(self) -> None:
        return _chat_context._inspect_attachment_pressed(self)

    @on(Button.Pressed, "#btn-remove-attachment")
    def _remove_attachment_pressed(self) -> None:
        return _chat_context._remove_attachment_pressed(self)

    def _send_blocked_reason(self) -> str | None:
        return _chat_turns._send_blocked_reason(self)

    @on(TextArea.Changed, "#chat-input")
    def _on_draft_changed(self, event: TextArea.Changed) -> None:
        return _chat_turns._on_draft_changed(self, event)

    @on(Input.Changed, "#param-temp, #param-top-p, #param-max-tokens")
    def _on_param_changed(self, _event: Input.Changed) -> None:
        return _chat_turns._on_param_changed(self, _event)

    @on(ChatInput.Submitted, "#chat-input")
    def _on_input_submitted(self, event: ChatInput.Submitted) -> None:
        return _chat_turns._on_input_submitted(self, event)

    @on(Button.Pressed, "#btn-send")
    def _send_pressed(self) -> None:
        return _chat_turns._send_pressed(self)

    def _do_submit(self, raw: str, editor: ChatInput) -> None:  # noqa: PLR0911, PLR0912, PLR0915
        return _chat_turns._do_submit(self, raw, editor)

    def apply_config_params(self, cfg: AppConfig) -> None:
        return _chat_turns.apply_config_params(self, cfg)

    def refresh_context_bar(self) -> None:
        return _chat_context.refresh_context_bar(self)

    def update_ctx_bar(self, ctx_len: int, excluded: int = 0) -> None:
        return _chat_context.update_ctx_bar(self, ctx_len, excluded)

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
        return await _chat_turns._run_turn(
            self,
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

    @staticmethod
    def _classify_outcome(result: TurnResult) -> str:
        return _chat_turns._classify_outcome(result)

    @staticmethod
    def _format_stamp(result: TurnResult, max_tok: int) -> str:
        return _chat_turns._format_stamp(result, max_tok)

    @staticmethod
    def _outcome_notices(
        result: TurnResult, max_tok: int, excluded_turns: int
    ) -> list[str]:
        return _chat_turns._outcome_notices(result, max_tok, excluded_turns)

    def _record_outcome(  # noqa: PLR0913, PLR0917
        self,
        model_at_send: str,
        result: TurnResult,
        outcome: str,
        ctx_len_estimate: int,
        reserved_ctx_len: int,
        excluded_turns: int,
    ) -> None:
        return _chat_turns._record_outcome(
            self,
            model_at_send,
            result,
            outcome,
            ctx_len_estimate,
            reserved_ctx_len,
            excluded_turns,
        )

    def _drop_running_attempt(self) -> None:
        return _chat_turns._drop_running_attempt(self)

    def _update_running_estimates(
        self,
        estimated_input: int,
        reserved_output: int,
        excluded: int,
        included_turn_ids: tuple[str, ...],
    ) -> None:
        return _chat_turns._update_running_estimates(
            self, estimated_input, reserved_output, excluded, included_turn_ids
        )

    def _on_turn_progress(self, progress: TurnProgress) -> None:
        return _chat_turns._on_turn_progress(self, progress)

    def _merged_progress_tools(self) -> tuple[dict[str, Any], ...]:
        return _chat_turns._merged_progress_tools(self)

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
        return _chat_turns._finalize_session_attempt(
            self,
            outcome,
            answer,
            reasoning,
            tool_calls,
            response_model,
            finish_reason,
            skipped_frames,
            stream_complete,
            prompt_tokens,
            completion_tokens,
            cached_prompt_tokens,
            first_output_s,
            answer_started_s,
            total_s,
            error_category,
            error_detail,
        )

    async def _checkpoint_terminal(self) -> None:
        return await _chat_turns._checkpoint_terminal(self)

    def abort(self) -> None:
        return _chat_turns.abort(self)

    async def wait_for_cleanup(self) -> None:
        return await _chat_turns.wait_for_cleanup(self)

    def _commit_success(  # noqa: PLR0913, PLR0917
        self,
        pending_user: dict[str, str],
        full_text: str,
        stamp: str,
        notices: list[str],
        reasoning: str = "",
        tool_calls: tuple[dict[str, object], ...] = (),
    ) -> None:
        return _chat_turns._commit_success(
            self, pending_user, full_text, stamp, notices, reasoning, tool_calls
        )

    def _complete_turn_ui(
        self,
        full_text: str,
        stamp: str,
        notices: list[str],
        reasoning: str = "",
        tool_calls: tuple[dict[str, object], ...] = (),
    ) -> None:
        return _chat_turns._complete_turn_ui(
            self, full_text, stamp, notices, reasoning, tool_calls
        )

    def _record_cancelled(
        self,
        model_at_send: str,
        ctx_len_estimate: int,
        reserved_ctx_len: int,
    ) -> None:
        return _chat_turns._record_cancelled(
            self, model_at_send, ctx_len_estimate, reserved_ctx_len
        )

    def end_turn(self) -> None:
        return _chat_turns.end_turn(self)

    def _release_session_lock(self) -> None:
        return _chat_persistence._release_session_lock(self)

    async def open_session(self, session_id: str) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
        return await _chat_persistence.open_session(self, session_id)

    def _start_empty_session(self, *, temporary: bool) -> None:
        return _chat_persistence._start_empty_session(self, temporary=temporary)

    def _confirm_discard(self, proceed: Any) -> None:
        return _chat_persistence._confirm_discard(self, proceed)

    @on(Button.Pressed, "#btn-sessions")
    def _open_picker(self) -> None:
        return _chat_persistence._open_picker(self)

    @on(Button.Pressed, "#btn-new-session")
    def _new_session_pressed(self) -> None:
        return _chat_persistence._new_session_pressed(self)

    @on(Button.Pressed, "#btn-new-temp")
    def _new_temp_pressed(self) -> None:
        return _chat_persistence._new_temp_pressed(self)

    @on(Button.Pressed, "#btn-clear-chat")
    def _clear_pressed(self) -> None:
        return _chat_persistence._clear_pressed(self)

    async def _clear_session(self) -> None:
        return await _chat_persistence._clear_session(self)

    @on(Button.Pressed, "#btn-delete-session")
    def _delete_pressed(self) -> None:
        return _chat_persistence._delete_pressed(self)

    async def _delete_session(self) -> None:
        return await _chat_persistence._delete_session(self)

    def _last_retryable_turn(self) -> SessionTurn | None:
        return _chat_persistence._last_retryable_turn(self)

    @on(Button.Pressed, "#btn-retry-request")
    def _retry_request_pressed(self) -> None:
        return _chat_persistence._retry_request_pressed(self)

    def _apply_retry_draft(self, turn: SessionTurn) -> None:
        return _chat_persistence._apply_retry_draft(self, turn)

    def _restore_retry_settings(self, saved: RequestSettings) -> None:
        return _chat_persistence._restore_retry_settings(self, saved)

    @on(Button.Pressed, "#btn-retry-save")
    def _retry_save_pressed(self) -> None:
        return _chat_persistence._retry_save_pressed(self)

    async def _retry_save(self) -> bool:
        return await _chat_persistence._retry_save(self)

    @on(Button.Pressed, "#btn-discard-unsaved")
    def _discard_pressed(self) -> None:
        return _chat_persistence._discard_pressed(self)

    @on(Button.Pressed, "#btn-use-saved")
    def _use_saved_pressed(self) -> None:
        return _chat_persistence._use_saved_pressed(self)

    async def _apply_saved_settings(self, saved: RequestSettings) -> None:
        return await _chat_persistence._apply_saved_settings(self, saved)

    @on(Button.Pressed, "#btn-keep-current")
    def _keep_current_pressed(self) -> None:
        return _chat_persistence._keep_current_pressed(self)

    async def flush_for_shutdown(self) -> bool:
        return await _chat_persistence.flush_for_shutdown(self)
