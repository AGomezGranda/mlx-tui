"""Session queries: UI-free projections over saved sessions."""

from __future__ import annotations

from mlx_tui.sessions.models import ChatSession, SessionTurn


def successful_turns(session: ChatSession) -> tuple[SessionTurn, ...]:
    """Return successful attempts in order; partial/error attempts never qualify."""
    return tuple(turn for turn in session.attempts if turn.outcome == "success")


def request_messages(session: ChatSession) -> list[dict[str, str]]:
    """Derive request-window messages only from successful user/assistant pairs."""
    messages: list[dict[str, str]] = []
    for turn in successful_turns(session):
        messages.append({"role": "user", "content": turn.sent_content})
        messages.append({"role": "assistant", "content": turn.answer})
    return messages


def session_label(session: ChatSession, excerpt_chars: int = 40) -> str:
    """Derive a picker label from the first prompt excerpt and the update date."""
    excerpt = ""
    for turn in session.attempts:
        if turn.sent_content:
            excerpt = turn.sent_content
            break
    if not excerpt:
        excerpt = session.draft
    first_line = excerpt.strip().splitlines()[0] if excerpt.strip() else "Empty session"
    if len(first_line) > excerpt_chars:
        first_line = first_line[:excerpt_chars] + "…"
    return f"{first_line} · {session.updated_at[:10]}"
