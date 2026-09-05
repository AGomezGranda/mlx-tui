"""Streaming turn worker + abort + UI writers — plain functions, no @work."""

from __future__ import annotations

import socket
import time
from dataclasses import replace
from typing import TYPE_CHECKING

import httpx
from rich.markdown import Markdown
from rich.text import Text
from textual.widgets import Input, RichLog, Static

from mlx_tui.app.operations import OperationKind
from mlx_tui.chat import error_detail, stream_turn
from mlx_tui.history.store import TurnRecord
from mlx_tui.history.tokens import ContextLimitError, prepare_context

if TYPE_CHECKING:
    from mlx_tui.chat_pane import ChatPane

_MAX_CONTEXT_TOKENS_EST = 8_192

_PREFIX = "▎ "
_SEPARATOR = "─" * 40


def _get_max_ctx(pane: ChatPane) -> int:
    try:
        v = getattr(pane.tui.config, "max_ctx", _MAX_CONTEXT_TOKENS_EST)
        return int(v) if isinstance(v, int) and v > 0 else _MAX_CONTEXT_TOKENS_EST
    except Exception:
        return _MAX_CONTEXT_TOKENS_EST


def run_turn_impl(  # noqa: PLR0912, PLR0915
    pane: ChatPane,
    messages: list[dict[str, str]],
    cold: bool,
    submitted_message: dict[str, str] | None = None,
) -> None:
    temp, top_p, max_tok = pane._parse_params()
    # persist to AppConfig snapshot (no file write)
    system_prompt: str | None = None
    try:
        cfg = pane.tui.config
        system_prompt = cfg.system
        if cfg.temperature != temp or cfg.top_p != top_p or cfg.max_tokens != max_tok:
            pane.tui.call_from_thread(
                setattr,
                pane.tui,
                "config",
                replace(
                    cfg,
                    temperature=temp,
                    top_p=top_p,
                    max_tokens=max_tok,
                    system=system_prompt,
                ),
            )
    except (AttributeError, Exception):
        pass
    model_at_send = pane.tui.effective_model() or "—"
    max_ctx = _get_max_ctx(pane)
    url = f"http://{pane.tui.host}:{pane.tui.port}/v1/chat/completions"
    ctx_len_estimate = 0
    reserved_ctx_len = 0
    try:
        window = prepare_context(messages, system_prompt, max_ctx, max_tok)
        ctx_len_estimate = window.input_tokens
        reserved_ctx_len = window.reserved_tokens
        pane.tui.call_from_thread(pane.update_ctx_bar, window.reserved_tokens)
        payload: dict[str, object] = {
            "messages": list(window.messages),
            "stream": True,
            "max_tokens": max_tok,
            "stream_options": {"include_usage": True},
            "temperature": temp,
            "top_p": top_p,
        }
        if model_at_send != "—":
            payload["model"] = model_at_send
        result = stream_turn(
            url,
            payload,
            prompt_estimate=window.input_tokens,
            on_flush=lambda text: pane.tui.call_from_thread(pane._update_stream, text),
            on_active=lambda r: setattr(pane, "_active_response", r),
        )
        if pane._cancel_requested:
            record_cancelled(
                pane,
                model_at_send,
                cold,
                ctx_len_estimate,
                reserved_ctx_len,
            )
            return
        acct = result.accounting
        prompt_tok = acct.prompt_tokens
        out_tok = acct.completion_tokens
        prefill_tok_s: float | None = None
        if not acct.prompt_estimated and prompt_tok > 0 and result.ttft > 0:
            prefill_tok_s = prompt_tok / result.ttft
        ctx_len = prompt_tok if not acct.prompt_estimated else ctx_len_estimate
        record = TurnRecord(
            ts=time.time(),
            model=model_at_send,
            prompt_tok=prompt_tok,
            out_tok=out_tok,
            ttft_s=result.ttft,
            tok_s=acct.tok_s,
            ctx_len=ctx_len,
            cold=cold,
            cancelled=False,
            prefill_tok_s=prefill_tok_s,
            prompt_estimated=acct.prompt_estimated,
            out_estimated=acct.completion_estimated,
        )
        pane.tui.call_from_thread(pane.tui.history.add, record)
        pane.tui.call_from_thread(pane.tui._refresh_metrics)
        # Keep the bar on the request reservation; history ctx_len stays prompt depth.
        try:
            pane.tui.call_from_thread(pane.update_ctx_bar, reserved_ctx_len)
        except Exception:
            pass
        in_label = f"{prompt_tok} (est)" if acct.prompt_estimated else str(prompt_tok)
        out_label = f"{out_tok} (est)" if acct.completion_estimated else str(out_tok)
        if prefill_tok_s is not None:
            stamp = (
                f"{in_label} in · {out_label} out · "
                f"{prefill_tok_s:.0f} prefill tok/s · {acct.tok_s:.1f} decode tok/s · TTFT {result.ttft:.2f}s"
            )
        else:
            stamp = (
                f"{in_label} in · {out_label} out · "
                f"{acct.tok_s:.1f} tok/s · TTFT {result.ttft:.2f}s"
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
    except ContextLimitError as exc:
        if submitted_message is not None:
            pane.tui.call_from_thread(pane._rollback_message, submitted_message)
        pane.tui.call_from_thread(pane._write_system_line, exc.reason, "yellow")
    except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
        if pane._cancel_requested:
            record_cancelled(
                pane,
                model_at_send,
                cold,
                ctx_len_estimate,
                reserved_ctx_len,
            )
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
        # Release only after the stream worker has stopped. The UI callback
        # then clears the response and restores controls in one place.
        pane.tui.call_from_thread(pane.tui.operations.release, OperationKind.CHATTING)
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
    log.write(Text(f"{_PREFIX}{stamp_text}", style="dim"))
    if full_text:
        pane.messages.append({"role": "assistant", "content": full_text})
        log.write(Markdown(full_text))
    for notice in notices:
        log.write(Text(f"{_PREFIX}{notice}", style="yellow"))
    log.write(Text(_SEPARATOR, style="dim"))
    log.write(Text(""))


def record_cancelled(
    pane: ChatPane,
    model_at_send: str,
    cold: bool,
    ctx_len_estimate: int,
    reserved_ctx_len: int,
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
    try:
        pane.tui.call_from_thread(pane.update_ctx_bar, reserved_ctx_len)
    except Exception:
        pass
    pane.tui.call_from_thread(
        pane._write_system_line, "cancelled — request aborted", "dim"
    )


def write_system_line(pane: ChatPane, message: str, style: str) -> None:
    text = f"{_PREFIX}{message}" if style == "dim" else message
    pane.query_one("#chat-log", RichLog).write(Text(text, style=style))


def end_turn(pane: ChatPane) -> None:
    update_stream(pane, "")
    pane._active_response = None
    pane._turn_active = False
    inp = pane.query_one("#chat-input", Input)
    if pane.tui.operations.current in (OperationKind.IDLE, OperationKind.CHATTING):
        inp.disabled = False
        inp.focus()
