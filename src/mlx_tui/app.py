from __future__ import annotations

import argparse
import socket
from typing import override

import httpx
import psutil
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog, Static

from mlx_tui.chat import ChatClient
from mlx_tui.history import trim_for_context
from mlx_tui.process import ServerProcessFinder, memory_snapshot, model_from_cmdline
from mlx_tui.status import ColdTracker, classify_liveness, format_status_line

_MAX_OUTPUT_TOKENS = 1024
# Ceiling, in estimated tokens, for the window sent to the server; the local
# transcript itself is kept whole. The idea doc's future ctx indicator and
# params sidebar replace this constant, not this mechanism.
_MAX_CONTEXT_TOKENS_EST = 8_000


def _error_detail(response: httpx.Response) -> str:
    """Extract the server's explanation from an error body, if one parses.

    mlx-lm/FastAPI errors carry ``{"detail": ...}``; OpenAI-style servers use
    ``{"error": {"message": ...}}``. A bare status code alone turns
    "prompt too long" and "model failed to load" into the same red line.
    """
    try:
        body: object = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    detail: object = body.get("detail")
    error = body.get("error")
    if detail is None and isinstance(error, dict):
        detail = error.get("message")
    if detail is None:
        detail = error
    if not isinstance(detail, str) or not detail.strip():
        return ""
    return f": {detail.strip()}"


class MlxTuiApp(App[None]):
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "cancel_chat", "Cancel"),
    ]

    CSS_PATH = "app.tcss"

    _http: httpx.AsyncClient

    def __init__(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.messages: list[dict[str, str]] = []
        self._poll_in_flight: bool = False
        self._process_finder = ServerProcessFinder()
        self.status_state: str = "red"
        self._cold_tracker = ColdTracker()
        self._cancel_requested: bool = False
        self._chat = ChatClient()

    @override
    def compose(self) -> ComposeResult:
        yield Static(f"● :{self.port}", id="status-bar")
        with Vertical():
            yield Static("", id="chat-stream")
            yield RichLog(id="chat-log", markup=False, wrap=True)
            yield Input(placeholder="message…", id="chat-input")

    def on_mount(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"http://{self.host}:{self.port}",
            timeout=httpx.Timeout(0.5),
        )
        self.set_interval(2.0, self._poll)

    async def on_unmount(self) -> None:
        await self._http.aclose()

    async def _poll(self) -> None:
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        try:
            state = await self._classify_liveness()
            self.status_state = state
            self._cold_tracker.observe(state)
            pid = self._process_finder.find()
            model: str | None = None
            rss_gib: float | None = None
            if pid is not None:
                try:
                    proc = psutil.Process(pid)
                    model = model_from_cmdline(proc)
                    rss_gib = proc.memory_info().rss / 2**30
                except psutil.NoSuchProcess:
                    pass
            self._render_status(model=model, rss_gib=rss_gib)
        finally:
            self._poll_in_flight = False

    async def _classify_liveness(self) -> str:
        try:
            resp = await self._http.get("/v1/models")
        except Exception:
            return "red"
        body: object = None
        try:
            body = resp.json()
        except ValueError:
            pass
        return classify_liveness(resp.status_code, body)

    def _render_status(self, *, model: str | None, rss_gib: float | None) -> None:
        snapshot = memory_snapshot()
        self.query_one("#status-bar", Static).update(
            format_status_line(
                state=self.status_state,
                model=model,
                rss_gib=rss_gib,
                memory=snapshot,
                port=self.port,
            )
        )

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        # Clear immediately so a second Enter can't re-send the same message;
        # Textual's action_submit does not clear the widget itself.
        event.input.clear()
        if not text:
            return
        self.messages.append({"role": "user", "content": text})
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"you › {text}"))
        cold = self._cold_tracker.consume_cold()
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
        url = f"http://{self.host}:{self.port}/v1/chat/completions"
        user_chars = len(messages[-1]["content"]) if messages else 0
        try:
            result = self._chat.stream_turn(
                url,
                payload,
                user_chars=user_chars,
                on_flush=lambda text: self.call_from_thread(self._update_stream, text),
            )
            if self._cancel_requested:
                self.call_from_thread(
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
            self.call_from_thread(
                self._complete_turn_ui, result.full_text, stamp, cold, notices
            )
        except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
            # RemoteProtocolError is how a server killed mid-stream surfaces
            # (it sits under ProtocolError, not ReadError); without it the
            # worker dies and takes the whole TUI to the crash screen.
            if self._cancel_requested:
                self.call_from_thread(
                    self._write_system_line, "cancelled — request aborted", "dim"
                )
            else:
                self.call_from_thread(
                    self._write_system_line,
                    f"server unreachable :{self.port}",
                    "red",
                )
        except (httpx.ConnectError, httpx.TimeoutException):
            self.call_from_thread(
                self._write_system_line, f"server unreachable :{self.port}", "red"
            )
        except httpx.HTTPStatusError as exc:
            self.call_from_thread(
                self._write_system_line,
                f"server error :{self.port} (HTTP {exc.response.status_code})"
                f"{_error_detail(exc.response)}",
                "red",
            )
        finally:
            self._chat.active_response = None
            self.call_from_thread(self._end_turn_ui)

    def action_cancel_chat(self) -> None:
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

    def _end_turn_ui(self) -> None:
        # Runs in every exit path (success, error, cancel): a half-flushed
        # reply must never linger above the scrollback.
        self._update_stream("")
        inp = self.query_one("#chat-input", Input)
        inp.disabled = False
        inp.focus()


def main() -> None:
    parser = argparse.ArgumentParser(prog="mlx-tui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    MlxTuiApp(host=args.host, port=args.port).run()
