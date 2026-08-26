"""Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Any, cast, override

import httpx
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog, Static

from mlx_tui.chat import ChatClient, error_detail
from mlx_tui.history import trim_for_context

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

_MAX_OUTPUT_TOKENS = 1024
# Ceiling, in estimated tokens, for the window sent to the server; the local
# transcript itself is kept whole.
_MAX_CONTEXT_TOKENS_EST = 8_000


class ChatPane(Vertical):
    """Owns #chat-stream, #chat-log and #chat-input plus the turn worker.

    Pane↔App contract: the pane renders and handles its own widgets while
    shared state and cross-pane orchestration live on ``MlxTuiApp``, accessed
    via the typed :attr:`tui` property.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.messages: list[dict[str, str]] = []
        self._cancel_requested: bool = False
        self._chat = ChatClient()

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @property
    def has_live_turn(self) -> bool:
        return self._chat.active_response is not None

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
        # Clear immediately so a second Enter can't re-send the same message;
        # Textual's action_submit does not clear the widget itself.
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
        payload: dict[str, object] = {
            "messages": trim_for_context(messages, _MAX_CONTEXT_TOKENS_EST),
            "stream": True,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "stream_options": {"include_usage": True},
        }
        # mlx-lm resolves a missing model field through Path(None) when no
        # default is loaded; pinning it makes turns deterministic across
        # server restarts and warm loads.
        model = self.tui.current_model_supplier()
        if model is not None:
            payload["model"] = model
        url = f"http://{self.tui.host}:{self.tui.port}/v1/chat/completions"
        user_chars = len(messages[-1]["content"]) if messages else 0
        try:
            result = self._chat.stream_turn(
                url,
                payload,
                user_chars=user_chars,
                on_flush=lambda text: self.tui.call_from_thread(
                    self._update_stream, text
                ),
            )
            if self._cancel_requested:
                self.tui.call_from_thread(
                    self._write_system_line, "cancelled — request aborted", "dim"
                )
                return
            stamp = (
                f"{result.tok_in_str} in · {result.tok_out_str} out · "
                f"{result.tok_s:.1f} tok/s · TTFT {result.ttft:.2f}s"
            )
            # An empty or truncated reply must never look like a normal turn:
            # without these notices the only trace is a stamp and silence.
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
            # RemoteProtocolError is how a server killed mid-stream surfaces
            # (it sits under ProtocolError, not ReadError); without it the
            # worker dies and takes the whole TUI to the crash screen.
            if self._cancel_requested:
                self.tui.call_from_thread(
                    self._write_system_line, "cancelled — request aborted", "dim"
                )
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
            self._chat.active_response = None
            self.tui.call_from_thread(self.end_turn)

    def abort(self) -> None:
        """Cancel any live turn; the cancelled line is written by _run_turn."""
        self._cancel_requested = True
        self.workers.cancel_group(self, "chat")
        response = self._chat.active_response
        if response is not None:
            # Closing the response alone does NOT wake a blocked iter_lines()
            # recv on macOS (verified empirically in v0), so shut the socket
            # down to unblock the read in ~1 ms.
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
            # Runs on the UI thread and the input is disabled for the whole
            # turn, so nothing else can be appending to self.messages now.
            self.messages.append({"role": "assistant", "content": full_text})
            log.write(full_text)
            log.write("")
        for notice in notices:
            log.write(Text(notice, style="yellow"))

    def _write_system_line(self, message: str, style: str) -> None:
        self.query_one("#chat-log", RichLog).write(Text(message, style=style))

    def end_turn(self) -> None:
        # Runs in every exit path (success, error, cancel): a half-flushed
        # reply must never linger above the scrollback.
        self._update_stream("")
        inp = self.query_one("#chat-input", Input)
        # On the restart path the aborted turn's teardown can land after the
        # swap took the UI busy; the swap owns the input until it finishes.
        if not self.tui.swap_machine.busy:
            inp.disabled = False
            inp.focus()
