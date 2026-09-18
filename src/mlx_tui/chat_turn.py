"""Presentation of one attempted turn, independent of request history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.containers import VerticalGroup
from textual.widgets import Button, Static

if TYPE_CHECKING:
    from mlx_tui.sessions.models import SessionTurn


class ChatTurn(VerticalGroup):
    DEFAULT_CSS = """
    ChatTurn { height: auto; margin-bottom: 1; }
    ChatTurn .chat-text { height: auto; }
    ChatTurn .chat-stamp { color: $text-muted; }
    ChatTurn Button { width: auto; height: 1; min-height: 1; border: none; padding: 0 1; }
    """

    def __init__(self, prompt: str) -> None:
        self.user = Static(Text(f"▎ you › {prompt}", style="bold"), classes="chat-text")
        self.assistant = Static(Text("▎ assistant", style="bold"), classes="chat-text")
        self.reasoning = Static(Text(), classes="chat-text chat-reasoning")
        self.tools = Static(Text(), classes="chat-text chat-tools")
        self.body = Static(
            "Waiting for output…", classes="chat-text chat-response", markup=False
        )
        self.stamp = Static("", classes="chat-text chat-stamp", markup=False)
        self.notices = Static(Text(), classes="chat-text chat-notices")
        self.answer_text = ""
        self.view_button = Button(
            "View/copy answer", id="btn-view-answer", classes="view-answer"
        )
        self.reasoning.display = False
        self.tools.display = False
        self.stamp.display = False
        self.notices.display = False
        self.view_button.display = False
        self.waiting = True
        self.finished = False
        super().__init__(
            self.user,
            self.assistant,
            self.reasoning,
            self.tools,
            self.body,
            self.stamp,
            self.notices,
            self.view_button,
            classes="chat-turn",
        )

    @on(Button.Pressed, "#btn-view-answer")
    def _view_pressed(self) -> None:
        # Exact answer only; reasoning/tool data stays separate, no parsing.
        from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

        try:
            self.app.push_screen(TextPreviewScreen(self.answer_text))
        except Exception:
            pass

    def update_response(self, text: str) -> None:
        if self.finished or not text:
            return
        self.waiting = False
        self.answer_text = text
        self.body.update(Markdown(text))
        self.view_button.display = True

    def update_activity(self, text: str) -> None:
        """Show reasoning/tool progress before answer text appears."""
        if self.finished or not text:
            return
        self.reasoning.update(Text(f"▎ thinking › {text}", style="dim"))
        self.reasoning.display = True

    def finish_response(
        self,
        text: str,
        stamp: str,
        notices: list[str],
        reasoning: str = "",
        tool_calls: tuple[dict[str, object], ...] = (),
    ) -> None:
        self.answer_text = text
        self.body.update(Markdown(text) if text else "")
        self.body.display = bool(text)
        self.view_button.display = bool(text)
        self.waiting = False
        self.finished = True
        if reasoning:
            self.reasoning.update(Text(f"▎ thinking › {reasoning}", style="dim"))
            self.reasoning.display = True
        if tool_calls:
            lines: list[str] = []
            for call in tool_calls:
                name = call.get("name", "?")
                args = call.get("arguments", "")
                lines.append(f"▎ tool › {name} {args}".rstrip())
            self.tools.update(Text("\n".join(lines), style="yellow"))
            self.tools.display = True
        self.stamp.update(Text(f"▎ {stamp}", style="dim"))
        self.stamp.display = bool(stamp)
        for notice in notices:
            self.add_notice(notice, "yellow")

    def add_notice(self, message: str, style: str) -> None:
        content = self.notices.content
        assert isinstance(content, Text)
        text = content.copy()
        if text:
            text.append("\n")
        text.append(message, style=style)
        self.notices.update(text)
        self.notices.display = True

    def end_attempt(self) -> None:
        if self.finished:
            return
        if self.waiting:
            self.body.update("")
            self.body.display = False
        # Partial answers stay viewable/copyable even without finish_response.
        if not self.answer_text:
            self.view_button.display = False
        else:
            self.view_button.display = True
        self.waiting = False
        self.finished = True
        self.add_notice("Not included in next request", "yellow")


def _session_stamp(turn: SessionTurn) -> str:
    """Derive a measurement stamp from raw persisted facts only."""
    if turn.outcome != "success":
        return ""
    prompt_known = turn.prompt_tokens is not None
    completion_known = turn.completion_tokens is not None
    in_label = str(turn.prompt_tokens) if prompt_known else "—"
    out_label = str(turn.completion_tokens) if completion_known else "—"
    first = f"{turn.first_output_s:.2f}s" if turn.first_output_s is not None else "—"
    answer = (
        f"{turn.answer_started_s:.2f}s" if turn.answer_started_s is not None else "—"
    )
    total = f"{turn.total_s:.2f}s" if turn.total_s is not None else "—"
    if (
        turn.prompt_tokens is not None
        and turn.completion_tokens is not None
        and turn.total_s
    ):
        rate = f"{turn.completion_tokens / turn.total_s:.1f} client request tok/s"
    else:
        rate = "— client request tok/s"
    stamp = (
        f"{in_label} in · {out_label} out · "
        f"first {first} · answer {answer} · total {total} · {rate}"
    )
    if turn.cached_prompt_tokens is not None:
        stamp += f" · cached {turn.cached_prompt_tokens} reuse"
    return stamp


def _session_notices(turn: SessionTurn) -> list[str]:
    """Derive display notices from raw outcome/accounting facts."""
    notices: list[str] = []
    if turn.skipped_frames:
        notices.append(f"{turn.skipped_frames} malformed stream frame(s) skipped")
    max_tok = turn.settings.max_tokens
    if turn.finish_reason == "length":
        notices.append(f"reply hit the {max_tok}-token cap — ask it to continue")
    if turn.outcome == "interrupted":
        notices.append("Interrupted — not included in next request")
    elif turn.outcome == "cancelled":
        notices.append("cancelled — client request cancelled, engine state unknown")
    elif turn.outcome == "failed":
        detail = f": {turn.error_detail}" if turn.error_detail else ""
        category = turn.error_category or "failed"
        notices.append(f"{category}{detail}".rstrip())
    elif turn.outcome in {"length_capped", "tool_only", "empty", "incomplete"}:
        if turn.tool_calls and not turn.answer:
            notices.append("tool output only — not executed, not in next request")
        elif not turn.answer:
            notices.append("model returned no text")
        if not turn.stream_complete and turn.finish_reason != "length":
            notices.append("incomplete stream — not in next request")
    if turn.excluded_messages:
        notices.append(
            f"{turn.excluded_messages} earlier message(s) excluded from request"
        )
    return notices


def render_session_turn(turn: SessionTurn) -> ChatTurn:
    """Re-render a saved attempt without metrics or network requests."""
    widget = ChatTurn(turn.sent_content)
    tool_calls = tuple(dict(call) for call in turn.tool_calls)
    # mypy: ChatTurn.finish_response expects tuple[dict[str, object], ...]
    widget.finish_response(
        turn.answer,
        _session_stamp(turn),
        _session_notices(turn),
        turn.reasoning,
        tool_calls,  # type: ignore[arg-type]
    )
    return widget
