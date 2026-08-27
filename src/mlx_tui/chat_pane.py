"""Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

from __future__ import annotations

import socket
import time
from typing import TYPE_CHECKING, Any, cast, override

import httpx
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog, Static

from mlx_tui.chat import error_detail, stream_turn
from mlx_tui.history import TurnRecord, _tok_int, estimate_tokens, trim_for_context

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

_MAX_OUTPUT_TOKENS = 1024
_MAX_CONTEXT_TOKENS_EST = 8_000


class ChatPane(Vertical):
    """Owns #chat-stream, #chat-log and #chat-input plus the turn worker."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.messages: list[dict[str, str]] = []
        self._cancel_requested: bool = False
        self._active_response: httpx.Response | None = None

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @property
    def has_live_turn(self) -> bool:
        return self._active_response is not None

    @override
    def compose(self) -> ComposeResult:
        yield Static("", id="chat-stream")
        yield RichLog(id="chat-log", markup=False, wrap=True)
        yield Input(placeholder="message…", id="chat-input")

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self.tui.swap_machine.busy:
            self.tui.log_app("model swapping — chat paused", "yellow")
            return
        event.input.clear()
        self.messages.append({"role": "user", "content": text})
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"you › {text}"))
        cold = self.tui.cold_tracker.consume_cold()
        self._cancel_requested = False
        event.input.disabled = True
        self._run_turn(list(self.messages), cold)

    @work(exclusive=True, group="chat", thread=True)
    def _run_turn(self, messages: list[dict[str, str]], cold: bool) -> None:
        model_at_send = self.tui.effective_model() or "—"
        trimmed = trim_for_context(messages, _MAX_CONTEXT_TOKENS_EST)
        ctx_len_estimate = sum(estimate_tokens(m["content"]) for m in trimmed)
        payload: dict[str, object] = {
            "messages": trimmed,
            "stream": True,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "stream_options": {"include_usage": True},
        }
        if model_at_send != "—":
            payload["model"] = model_at_send
        url = f"http://{self.tui.host}:{self.tui.port}/v1/chat/completions"
        user_chars = len(messages[-1]["content"]) if messages else 0
        try:
            result = stream_turn(
                url,
                payload,
                user_chars=user_chars,
                on_flush=lambda text: self.tui.call_from_thread(
                    self._update_stream, text
                ),
                on_active=lambda r: setattr(self, "_active_response", r),
            )
            if self._cancel_requested:
                self._record_cancelled(model_at_send, cold, ctx_len_estimate)
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
            self.tui.call_from_thread(self.tui.history.add, record)
            self.tui.call_from_thread(self.tui.update_history_strip)
            stamp = (
                f"{result.tok_in_str} in · {result.tok_out_str} out · "
                f"{result.tok_s:.1f} tok/s · TTFT {result.ttft:.2f}s"
            )
            notices: list[str] = []
            if result.skipped_frames:
                notices.append(
                    f"{result.skipped_frames} malformed stream frame(s) skipped"
                )
            if result.finish_reason == "length":
                notices.append(
                    f"reply hit the {_MAX_OUTPUT_TOKENS}-token cap — ask it to continue"
                )
            if not result.full_text:
                notices.append("model returned no text")
            self.tui.call_from_thread(
                self._complete_turn_ui, result.full_text, stamp, cold, notices
            )
        except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
            if self._cancel_requested:
                self._record_cancelled(model_at_send, cold, ctx_len_estimate)
            else:
                self.tui.call_from_thread(
                    self._write_system_line,
                    f"server unreachable :{self.tui.port}",
                    "red",
                )
        except (httpx.ConnectError, httpx.TimeoutException):
            self.tui.call_from_thread(
                self._write_system_line,
                f"server unreachable :{self.tui.port}",
                "red",
            )
        except httpx.HTTPStatusError as exc:
            self.tui.call_from_thread(
                self._write_system_line,
                f"server error :{self.tui.port} (HTTP {exc.response.status_code})"
                f"{error_detail(exc.response)}",
                "red",
            )
        finally:
            self._active_response = None
            self.tui.call_from_thread(self.end_turn)

    def abort(self) -> None:
        """Cancel any live turn; the cancelled line is written by _run_turn."""
        self._cancel_requested = True
        self.workers.cancel_group(self, "chat")
        response = self._active_response
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

    def _update_stream(self, text: str) -> None:
        self.query_one("#chat-stream", Static).update(text)

    def _complete_turn_ui(
        self, full_text: str, stamp: str, cold: bool, notices: list[str]
    ) -> None:
        stamp_text = f"{stamp} · cold" if cold else stamp
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(stamp_text, style="dim"))
        if full_text:
            self.messages.append({"role": "assistant", "content": full_text})
            log.write(full_text)
            log.write("")
        for notice in notices:
            log.write(Text(notice, style="yellow"))

    def _record_cancelled(
        self, model_at_send: str, cold: bool, ctx_len_estimate: int
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
        self.tui.call_from_thread(self.tui.history.add, record)
        self.tui.call_from_thread(self.tui.update_history_strip)
        self.tui.call_from_thread(
            self._write_system_line, "cancelled — request aborted", "dim"
        )

    def _write_system_line(self, message: str, style: str) -> None:
        self.query_one("#chat-log", RichLog).write(Text(message, style=style))

    def end_turn(self) -> None:
        self._update_stream("")
        inp = self.query_one("#chat-input", Input)
        if not self.tui.swap_machine.busy:
            inp.disabled = False
            inp.focus()
