"""Shared fixtures for mlx-tui tests: a stub mlx-lm HTTP server and an app harness."""

from __future__ import annotations

import asyncio
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
from tests.builders import SseStreamBuilder


class StubServer(HTTPServer):
    mode: str = "ok"


class StubHandler(BaseHTTPRequestHandler):
    @override
    def log_message(self, format: str, *args: object) -> None:
        pass

    def _mode(self) -> str:
        assert isinstance(self.server, StubServer)
        return self.server.mode

    def do_GET(self) -> None:
        if self._mode() == "html":
            self.send_response(502)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>proxy</html>")
            return
        body = b"""{
            "object": "list",
            "data": [
                {
                    "id": "mlx-community/stub-test",
                    "object": "model",
                    "created": 0
                }
            ]
        }"""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
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
            frame = SseStreamBuilder().role_frame().delta("Hel").build()
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
            body = (
                SseStreamBuilder()
                .role_frame()
                .delta("Partial ans")
                .finish_frame(reason="length")
                .usage(9, 3)
                .done()
                .build()
            )
        elif self._mode() == "empty":
            # 200 OK but zero content deltas — the silent-no-answer shape.
            body = SseStreamBuilder().role_frame().usage(12, 0).done().build()
        else:
            body = (
                SseStreamBuilder()
                .role_frame()
                .delta("Hello")
                .delta(" world")
                .delta(" this")
                .delta(" is")
                .delta(" MLX.")
                .finish_frame()
                .usage(12, 6)
                .done()
                .build()
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
        return int(self.server.server_address[1])

    def log_lines(self) -> list[str]:
        log = self.app.query_one("#chat-log", RichLog)
        return [strip.text for strip in log.lines]

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
    app = MlxTuiApp(host="127.0.0.1", port=int(server.server_address[1]))
    async with app.run_test() as pilot:
        yield AppHarness(app=app, pilot=pilot, server=server)
