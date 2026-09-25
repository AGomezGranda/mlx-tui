"""Chat context and attachment helpers (pane-parameterized)."""

from __future__ import annotations

import ipaddress
from dataclasses import replace
from pathlib import Path
from typing import Any

from textual.css.query import NoMatches
from textual.widgets import Input, ProgressBar, Static

from mlx_tui.attachments import (
    AttachmentError,
    AttachmentSnapshot,
    read_attachment,
    render_user_content,
)
from mlx_tui.chat_ui.widgets import ChatInput, _choose_macos_file
from mlx_tui.history.tokens import (
    ContextLimitError,
    ContextWindow,
    ctx_bar_style,
    ctx_bar_text,
    estimate_tokens,
    prepare_context,
)
from mlx_tui.params import ParamsPane

_MAX_CONTEXT_TOKENS_EST = 8_192


def _get_max_ctx(pane: Any) -> int:
    v = pane.tui.config.max_ctx
    if isinstance(v, int) and v > 0:
        return v
    return _MAX_CONTEXT_TOKENS_EST


def _request_destination(pane: Any) -> str:
    rendered = f"[{pane.tui.host}]" if ":" in pane.tui.host else pane.tui.host
    return f"http://{rendered}:{pane.tui.port}"


def _is_loopback_destination(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _current_draft(pane: Any) -> str:
    try:
        return pane.query_one("#chat-input", ChatInput).text
    except NoMatches:
        return pane._session.draft if pane._session is not None else ""


def _prepare_window(
    pane: Any,
    draft: str,
    attachments: tuple[AttachmentSnapshot, ...],
) -> tuple[str, ContextWindow]:
    _, _, max_tokens = pane.query_one(ParamsPane).read_values()
    rendered = render_user_content(draft, attachments)
    messages = list(pane.messages)
    if draft.strip() or attachments:
        messages.append({"role": "user", "content": rendered})
    window = prepare_context(
        messages,
        pane.tui.config.system,
        pane._get_max_ctx(),
        max_tokens,
    )
    return rendered, window


def _refresh_attachment_ui(pane: Any) -> None:
    try:
        from rich.text import Text  # noqa: PLC0415

        if not pane._draft_attachments:
            summary = f"files: none · destination {pane._request_destination()}"
        else:
            files = " · ".join(
                f"{index}. {snapshot.selected_path} ({snapshot.byte_length} B, "
                f"~{estimate_tokens(snapshot.content)} est tokens)"
                for index, snapshot in enumerate(pane._draft_attachments, 1)
            )
            summary = f"files: {files} · destination {pane._request_destination()}"
        if pane._context_error:
            summary += f" · {pane._context_error}"
        pane.query_one("#chat-attachments", Static).update(Text(summary))
    except NoMatches:
        pass
    pane.refresh_zen_info()


def _current_window(pane: Any) -> tuple[str, ContextWindow] | None:
    draft = pane._current_draft()
    if not pane.messages and not draft.strip() and not pane._draft_attachments:
        return None
    return pane._prepare_window(draft, pane._draft_attachments)


def _included_turn_ids(pane: Any, window: ContextWindow) -> tuple[str, ...]:
    successful_ids = tuple(
        turn.turn_id
        for turn in (pane._session.attempts if pane._session else ())
        if turn.outcome == "success"
    )
    return successful_ids[window.excluded_turns // 2 :]


def _context_preview_pressed(pane: Any) -> None:
    try:
        prepared = pane._current_window()
    except ContextLimitError as exc:
        pane._context_error = exc.reason
        pane._refresh_attachment_ui()
        pane.tui.log_app(exc.reason, "yellow")
        return
    if prepared is None:
        pane.tui.log_app("no request context to preview", "dim")
        return
    _, window = prepared
    lines = [
        f"Destination: {pane._request_destination()}",
        f"Estimated input: {window.input_tokens} tokens",
        f"Reserved output: {window.reserved_tokens - window.input_tokens} tokens",
        f"Configured limit: {pane._get_max_ctx()} tokens",
        f"Excluded messages: {window.excluded_turns}",
    ]
    for message in window.messages:
        lines.extend(("", f"[{message['role']}]", message["content"]))
    from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

    pane.tui.push_screen(
        TextPreviewScreen(
            "\n".join(lines),
            title="Request context — exact retained messages",
        )
    )


def _attachment_failed(pane: Any, message: str) -> None:
    pane.tui.log_app(f"attachment rejected: {message}", "yellow")


def _attachment_added(pane: Any, snapshot: AttachmentSnapshot) -> None:
    pane._draft_attachments = (*pane._draft_attachments, snapshot)
    pane._attachment_index = len(pane._draft_attachments) - 1
    try:
        pane.query_one("#chat-attachment-index", Input).value = str(
            pane._attachment_index + 1
        )
    except NoMatches:
        pass
    if pane._session is not None:
        pane._session = replace(
            pane._session,
            attachments=pane._draft_attachments,
            updated_at=pane._now_iso(),
        )
    pane._context_error = None
    if not pane._is_temporary and not pane._read_only:
        pane._save_revision += 1
        pane._schedule_draft_save()
    pane._refresh_attachment_ui()
    pane.refresh_context_bar()


def _attachment_picker_finished(
    pane: Any, snapshot: AttachmentSnapshot | None, error: str | None
) -> None:
    pane._attachment_picker_active = False
    if error is not None:
        pane._attachment_failed(error)
    elif snapshot is not None:
        pane._attachment_added(snapshot)
    pane._update_action_visibility()


def _choose_attachment_worker(pane: Any) -> None:
    try:
        selected = _choose_macos_file()
        snapshot = read_attachment(Path(selected)) if selected is not None else None
        error = None
    except AttachmentError as exc:
        snapshot = None
        error = str(exc)
    pane.app.call_from_thread(pane._attachment_picker_finished, snapshot, error)


def _add_attachment_pressed(pane: Any) -> None:
    if pane._turn_active or pane.tui.operations.is_busy:
        pane.tui.log_app("finish the current turn before attaching a file", "yellow")
        return
    if pane._read_only or pane._save_failed:
        pane.tui.log_app(
            "session is not editable — retry save or open an editable session",
            "yellow",
        )
        return
    pane._attachment_picker_active = True
    pane._update_action_visibility()
    pane._choose_attachment_worker()


def _selected_attachment(pane: Any) -> tuple[int, AttachmentSnapshot] | None:
    if not pane._draft_attachments:
        pane.tui.log_app("no attached file", "dim")
        return None
    try:
        raw_index = pane.query_one("#chat-attachment-index", Input).value.strip()
        index = int(raw_index) - 1
    except (NoMatches, ValueError):
        index = pane._attachment_index
    if not 0 <= index < len(pane._draft_attachments):
        pane.tui.log_app("attachment number is out of range", "yellow")
        return None
    pane._attachment_index = index
    return index, pane._draft_attachments[index]


def _inspect_attachment_pressed(pane: Any) -> None:
    selected = pane._selected_attachment()
    if selected is None:
        return
    _, snapshot = selected
    from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

    pane.tui.push_screen(
        TextPreviewScreen(
            snapshot.content,
            title=f"Attached file — {snapshot.selected_path}",
        )
    )


def _remove_attachment_pressed(pane: Any) -> None:
    selected = pane._selected_attachment()
    if selected is None:
        return
    index, _ = selected
    pane._draft_attachments = tuple(
        snapshot
        for position, snapshot in enumerate(pane._draft_attachments)
        if position != index
    )
    pane._attachment_index = min(index, max(0, len(pane._draft_attachments) - 1))
    try:
        pane.query_one("#chat-attachment-index", Input).value = str(
            pane._attachment_index + 1
        )
    except NoMatches:
        pass
    if pane._session is not None:
        pane._session = replace(
            pane._session,
            attachments=pane._draft_attachments,
            updated_at=pane._now_iso(),
        )
    if not pane._is_temporary and not pane._read_only:
        pane._save_revision += 1
        pane._schedule_draft_save()
    pane._context_error = None
    pane._refresh_attachment_ui()
    pane.refresh_context_bar()


def refresh_context_bar(pane: Any) -> None:
    try:
        prepared = pane._current_window()
    except ContextLimitError as exc:
        pane._context_error = exc.reason
        pane.update_ctx_bar(pane._get_max_ctx())
    except Exception:
        pane._context_error = "context preview unavailable"
        pane.update_ctx_bar(pane._get_max_ctx())
    else:
        pane._context_error = None
        if prepared is None:
            pane.update_ctx_bar(0)
        else:
            _, window = prepared
            pane.update_ctx_bar(window.reserved_tokens, window.excluded_turns)
    pane._refresh_attachment_ui()


def update_ctx_bar(pane: Any, ctx_len: int, excluded: int = 0) -> None:
    max_ctx = pane._get_max_ctx()
    style = ctx_bar_style(ctx_len, max_ctx)
    try:
        bar = pane.query_one("#ctx-progress", ProgressBar)
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
        label = pane.query_one("#ctx-bar", Static)
    except NoMatches:
        return
    label.update(ctx_bar_text(ctx_len, max_ctx, excluded=excluded))
    label.remove_class("ctx-bar-amber")
    label.remove_class("ctx-bar-red")
    if style == "yellow":
        label.add_class("ctx-bar-amber")
    elif style == "red":
        label.add_class("ctx-bar-red")
    pane._zen_context_text = f"ctx ≈{round(max(0, ctx_len) / max_ctx * 100)}%"
    if excluded > 0:
        pane._zen_context_text += f" · {excluded} excluded"
    pane._zen_context_style = style
    pane.refresh_zen_info()
