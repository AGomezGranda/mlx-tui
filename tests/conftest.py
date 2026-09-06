"""Shared fixtures for mlx-tui tests: a stub mlx-lm HTTP server and an app harness."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import override

import pytest
from textual.pilot import Pilot
from textual.widgets import RichLog

from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_pane import ChatPane
from mlx_tui.models_pane import ModelsPane
from tests.builders import sse_frames


class StubServer(HTTPServer):
    mode: str = "ok"
    model_id: str = "mlx-community/stub-test"

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[StubHandler],
    ) -> None:
        super().__init__(server_address, handler)
        self.requests: list[dict[str, object]] = []


class StubHandler(BaseHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: object) -> None:
        pass

    def _mode(self) -> str:
        assert isinstance(self.server, StubServer)
        return self.server.mode

    def _record_post(self) -> None:
        assert isinstance(self.server, StubServer)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload: dict[str, object] = json.loads(raw)
        except ValueError:
            payload = {}
        self.server.requests.append(payload)

    def do_GET(self) -> None:
        if self._mode() == "html":
            self.send_response(502)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>proxy</html>")
            return
        assert isinstance(self.server, StubServer)
        model_id = self.server.model_id
        body = (
            b'{"object": "list", "data": [{"id": "'
            + model_id.encode()
            + b'", "object": "model", "created": 0}]}'
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self._record_post()
        if self._mode() == "probe":
            # The warm-swap probe: a plain OpenAI-shaped completion reply.
            body = (
                b'{"choices": [{"message": {"role": "assistant", "content": "hi"}}],'
                b' "usage": {"prompt_tokens": 1, "completion_tokens": 1,'
                b' "total_tokens": 2}}'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self._mode() == "error500":
            body = b'{"detail": "model load failed"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self._mode() == "truncated":
            # Chunked framing cut off after one frame, after a delay long
            # enough for the client's throttled flush to paint the partial
            # reply: reproduces httpx.RemoteProtocolError mid-stream.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            frame = sse_frames(deltas=["Hel"], finish=None, done=False)
            self.wfile.write(f"{len(frame):X}\r\n".encode() + frame)
            self.wfile.flush()
            time.sleep(0.15)
            self.wfile.write(b"\x00broken-footer")
            self.wfile.flush()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if self._mode() == "length_cap":
            # finish_reason "length" with no [DONE]-adjacent stop frame: what
            # mlx-lm emits when the reply hits the max-tokens cap.
            body = sse_frames(deltas=["Partial ans"], finish="length", usage=(9, 3))
        elif self._mode() == "empty":
            # 200 OK but zero content deltas — the silent-no-answer shape.
            body = sse_frames(finish=None, usage=(12, 0))
        elif self._mode() == "no_usage":
            body = sse_frames(deltas=["Hi"], usage=None)
        elif self._mode() == "multiline":
            from tests.builders import sse_multiline_event  # noqa: PLC0415

            body = (
                sse_frames(deltas=["Hello"], finish=None, done=False)
                + sse_multiline_event(
                    {
                        "choices": [
                            {"delta": {"content": " world"}, "finish_reason": None}
                        ]
                    }
                )
                + sse_frames(deltas=[" this", " is", " MLX."], usage=(12, 6))
            )
        elif self._mode() == "nospace":
            body = sse_frames(
                deltas=["Hello", " world", " this", " is", " MLX."],
                usage=(12, 6),
                no_space=True,
            )
        else:
            body = sse_frames(
                deltas=["Hello", " world", " this", " is", " MLX."],
                usage=(12, 6),
            )
        if self._mode() == "slow":
            # Hold back everything after the role frame so the client has an
            # open-but-idle stream to cancel mid-flight.
            head_end = body.index(b"\n\n") + 2
            self.wfile.write(body[:head_end])
            self.wfile.flush()
            time.sleep(0.5)
            body = body[head_end:]
        self.wfile.write(body)
        self.wfile.flush()


@pytest.fixture
def stub_server_factory() -> Iterator[Callable[[str], StubServer]]:
    servers: list[StubServer] = []

    def start(mode: str) -> StubServer:
        server = StubServer(("127.0.0.1", 0), StubHandler)
        server.mode = mode
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@dataclass
class AppHarness:
    """Bundles the app under test with its pilot and the stub server backing it."""

    app: MlxTuiApp
    pilot: Pilot[None]
    server: StubServer

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def models_pane(self) -> ModelsPane:
        return self.app.query_one(ModelsPane)

    def chat_pane(self) -> ChatPane:
        return self.app.query_one("#chat-pane", ChatPane)

    def log_lines(self) -> list[str]:
        log = self.app.query_one("#chat-log", RichLog)
        return [strip.text.rstrip() for strip in log.lines]

    def app_log_lines(self) -> list[str]:
        log = self.app.query_one("#app-log", RichLog)
        return [strip.text.rstrip() for strip in log.lines]

    async def wait_for(
        self, predicate: Callable[[MlxTuiApp], bool], attempts: int = 200
    ) -> bool:
        for _ in range(attempts):
            await self.pilot.pause()
            if predicate(self.app):
                return True
            await asyncio.sleep(0.02)
        return False


@pytest.fixture
async def harness(
    stub_server_factory: Callable[[str], StubServer],
) -> AsyncIterator[AppHarness]:
    server = stub_server_factory("ok")
    app = MlxTuiApp(host="127.0.0.1", port=server.server_address[1])
    async with app.run_test() as pilot:
        yield AppHarness(app=app, pilot=pilot, server=server)
