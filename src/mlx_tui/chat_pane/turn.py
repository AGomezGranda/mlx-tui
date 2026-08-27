"""Streaming turn worker + abort + UI writers — plain functions, no @work."""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING

import httpx
from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import Input, RichLog, Static

from mlx_tui.chat import error_detail, stream_turn
from mlx_tui.config import AppConfig
from mlx_tui.history.store import TurnRecord
from mlx_tui.history.tokens import _tok_int, estimate_tokens, trim_for_context

if TYPE_CHECKING:
    from mlx_tui.chat_pane import ChatPane

_MAX_CONTEXT_TOKENS_EST = 8_000


def run_turn_impl(pane: ChatPane, messages: list[dict[str, str]], cold: bool) -> None:  # noqa: PLR0912
    temp, top_p, max_tok = pane._parse_params()
    # persist to AppConfig snapshot (no file write)
    try:
        cfg = pane.tui.config
        # only update if differs to avoid churn; always sync system
        if (
            cfg.temperature != temp
            or cfg.top_p != top_p
            or cfg.max_tokens != max_tok
            or cfg.system != (pane._system_prompt or None)
        ):
            pane.tui.call_from_thread(
                setattr,
                pane.tui,
                "config",
                AppConfig(
                    model=cfg.model,
                    host=cfg.host,
                    port=cfg.port,
                    start_cmd=cfg.start_cmd,
                    stop_cmd=cfg.stop_cmd,
                    pidfile=cfg.pidfile,
                    temperature=temp,
                    top_p=top_p,
                    max_tokens=max_tok,
                    system=pane._system_prompt or None,
                ),
            )
    except (AttributeError, Exception):
        pass
    model_at_send = pane.tui.effective_model() or "—"
    trimmed = trim_for_context(messages, _MAX_CONTEXT_TOKENS_EST)
    trimmed_with_system = (
        [{"role": "system", "content": pane._system_prompt}]
        if pane._system_prompt
        else []
    ) + trimmed
    ctx_len_estimate = sum(estimate_tokens(m["content"]) for m in trimmed)
    payload: dict[str, object] = {
        "messages": trimmed_with_system,
        "stream": True,
        "max_tokens": max_tok,
        "stream_options": {"include_usage": True},
        "temperature": temp,
        "top_p": top_p,
    }
    if model_at_send != "—":
        payload["model"] = model_at_send
    url = f"http://{pane.tui.host}:{pane.tui.port}/v1/chat/completions"
    user_chars = len(messages[-1]["content"]) if messages else 0
    try:
        result = stream_turn(
            url,
            payload,
            user_chars=user_chars,
            on_flush=lambda text: pane.tui.call_from_thread(pane._update_stream, text),
            on_active=lambda r: setattr(pane, "_active_response", r),
        )
        if pane._cancel_requested:
            record_cancelled(pane, model_at_send, cold, ctx_len_estimate)
            return
        ctx_len = (
            _tok_int(result.tok_in_str)
            if " (est)" not in result.tok_in_str
            else ctx_len_estimate
        )
        record = TurnRecord(
            ts=time.time(),
            model=model_at_send,
            prompt_tok=_tok_int(result.tok_in_str),
            out_tok=_tok_int(result.tok_out_str),
            ttft_s=result.ttft,
            tok_s=result.tok_s,
            ctx_len=ctx_len,
            cold=cold,
            cancelled=False,
        )
        pane.tui.call_from_thread(pane.tui.history.add, record)
        pane.tui.call_from_thread(pane.tui._refresh_metrics)
        stamp = (
            f"{result.tok_in_str} in · {result.tok_out_str} out · "
            f"{result.tok_s:.1f} tok/s · TTFT {result.ttft:.2f}s"
        )
        notices: list[str] = []
        if result.skipped_frames:
            notices.append(f"{result.skipped_frames} malformed stream frame(s) skipped")
        if result.finish_reason == "length":
            notices.append(f"reply hit the {max_tok}-token cap — ask it to continue")
        if not result.full_text:
            notices.append("model returned no text")
        pane.tui.call_from_thread(
            pane._complete_turn_ui, result.full_text, stamp, cold, notices
        )
    except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
        if pane._cancel_requested:
            record_cancelled(pane, model_at_send, cold, ctx_len_estimate)
        else:
            pane.tui.call_from_thread(
                pane._write_system_line,
                f"server unreachable :{pane.tui.port}",
                "red",
            )
    except (httpx.ConnectError, httpx.TimeoutException):
        pane.tui.call_from_thread(
            pane._write_system_line,
            f"server unreachable :{pane.tui.port}",
            "red",
        )
    except httpx.HTTPStatusError as exc:
        pane.tui.call_from_thread(
            pane._write_system_line,
            f"server error :{pane.tui.port} (HTTP {exc.response.status_code})"
            f"{error_detail(exc.response)}",
            "red",
        )
    finally:
        pane._active_response = None
        pane.tui.call_from_thread(pane.end_turn)


def abort(pane: ChatPane) -> None:
    """Cancel any live turn; the cancelled line is written by _run_turn."""
    pane._cancel_requested = True
    pane.workers.cancel_group(pane, "chat")
    response = pane._active_response
    if response is not None:
        # ponytail: socket shutdown wakes macOS recv, remove if httpx fixes blocking iter_lines
        try:
            network_stream = response.extensions.get("network_stream")
            sock = getattr(network_stream, "_sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        response.close()


def update_stream(pane: ChatPane, text: str) -> None:
    pane.query_one("#chat-stream", Static).update(text)


def complete_turn_ui(
    pane: ChatPane, full_text: str, stamp: str, cold: bool, notices: list[str]
) -> None:
    stamp_text = f"{stamp} · cold" if cold else stamp
    log = pane.query_one("#chat-log", RichLog)
    log.write(Text(stamp_text, style="dim"))
    if full_text:
        pane.messages.append({"role": "assistant", "content": full_text})
        log.write(Markdown(full_text))
        log.write("")
    for notice in notices:
        log.write(Text(notice, style="yellow"))


def record_cancelled(
    pane: ChatPane, model_at_send: str, cold: bool, ctx_len_estimate: int
) -> None:
    record = TurnRecord(
        ts=time.time(),
        model=model_at_send,
        prompt_tok=0,
        out_tok=0,
        ttft_s=0.0,
        tok_s=0.0,
        ctx_len=ctx_len_estimate,
        cold=cold,
        cancelled=True,
    )
    pane.tui.call_from_thread(pane.tui.history.add, record)
    pane.tui.call_from_thread(pane.tui._refresh_metrics)
    pane.tui.call_from_thread(
        pane._write_system_line, "cancelled — request aborted", "dim"
    )


def write_system_line(pane: ChatPane, message: str, style: str) -> None:
    pane.query_one("#chat-log", RichLog).write(Text(message, style=style))


def end_turn(pane: ChatPane) -> None:
    update_stream(pane, "")
    inp = pane.query_one("#chat-input", Input)
    if not pane.tui.swap_busy:
        inp.disabled = False
        inp.focus()
