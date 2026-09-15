"""Shared fixtures for mlx-tui tests: a stub mlx-lm HTTP server and an app harness."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast, override

import pytest
from rich.console import RenderableType
from rich.segment import Segment
from textual.pilot import Pilot
from textual.widgets import RichLog, Static

from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_pane import ChatPane
from mlx_tui.config import AppConfig
from mlx_tui.models_pane import ModelsPane
from tests.builders import sse_frames


@pytest.fixture(autouse=True)
def _isolated_state_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point XDG state at a per-test directory before any app construction.

    Tests that need persistence across two app lifetimes within one test may
    override this root with their own ``monkeypatch.setenv`` call.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    yield


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

    def _record_post(self) -> dict[str, object]:
        assert isinstance(self.server, StubServer)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload: dict[str, object] = json.loads(raw)
        except ValueError:
            payload = {}
        self.server.requests.append(payload)
        return payload

    def do_GET(self) -> None:
        if self._mode() == "html" or (
            self._mode() == "catalog_html" and self.path != "/health"
        ):
            self.send_response(502)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>proxy</html>")
            return
        assert isinstance(self.server, StubServer)
        if self.path == "/health":
            healthy = self._mode() != "health_down"
            body = b'{"status": "ok"}' if healthy else b'{"status": "down"}'
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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

    def do_POST(self) -> None:  # noqa: PLR0915
        payload = self._record_post()
        requested_model = payload.get("model")
        model = requested_model if isinstance(requested_model, str) else "default_model"
        if self._mode() == "probe" or (
            self._mode() == "ok"
            and payload.get("max_tokens") == 1
            and payload.get("stream") is not True
        ):
            # The warm-swap probe: a plain OpenAI-shaped completion reply.
            body = (
                b'{"model": '
                + json.dumps(model).encode()
                + b', "choices": [{"message": {"role": "assistant", "content": "hi"}}],'
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
            frame = sse_frames(deltas=["Hel"], finish=None, done=False, model=model)
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
            body = sse_frames(
                deltas=["Partial ans"], finish="length", usage=(9, 3), model=model
            )
        elif self._mode() == "empty":
            # 200 OK but zero content deltas — the silent-no-answer shape.
            body = sse_frames(finish=None, usage=(12, 0), model=model)
        elif self._mode() == "no_usage":
            body = sse_frames(deltas=["Hi"], usage=None, model=model)
        elif self._mode() == "reasoning":
            body = sse_frames(
                deltas=["blue"],
                reasoning=["think step"],
                usage=(12, 6),
                model=model,
            )
        elif self._mode() == "tools":
            body = sse_frames(
                deltas=[],
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Madrid"}',
                        },
                    }
                ],
                usage=(12, 3),
                model=model,
            )
        elif self._mode() == "multiline":
            from tests.builders import sse_multiline_event  # noqa: PLC0415

            body = (
                sse_frames(deltas=["Hello"], finish=None, done=False, model=model)
                + sse_multiline_event(
                    {
                        "choices": [
                            {"delta": {"content": " world"}, "finish_reason": None}
                        ]
                    }
                )
                + sse_frames(
                    deltas=[" this", " is", " MLX."], usage=(12, 6), model=model
                )
            )
        elif self._mode() == "nospace":
            body = sse_frames(
                deltas=["Hello", " world", " this", " is", " MLX."],
                usage=(12, 6),
                no_space=True,
                model=model,
            )
        else:
            body = sse_frames(
                deltas=["Hello", " world", " this", " is", " MLX."],
                usage=(12, 6),
                model=model,
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
        lines: list[str] = []
        for widget in self.app.query(".chat-text").results(Static):
            if not widget.display:
                continue
            width = widget.content_size.width or self.app.size.width
            options = self.app.console.options.update_width(width)
            segments = self.app.console.render(
                cast(RenderableType, widget.content), options
            )
            lines.extend(
                "".join(segment.text for segment in line).rstrip()
                for line in Segment.split_lines(segments)
            )
        return lines

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
    app = MlxTuiApp(
        host="127.0.0.1",
        port=server.server_address[1],
        config=AppConfig(model=server.model_id),
    )
    async with app.run_test() as pilot:
        yield AppHarness(app=app, pilot=pilot, server=server)
