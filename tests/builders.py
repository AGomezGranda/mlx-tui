"""Builders composing canned protocol payloads for tests."""

from __future__ import annotations

import json
from typing import Self


class SseStreamBuilder:
    """Fluent builder emitting OpenAI-style SSE chat-completion wire bytes.

    Wire format (matches mlx-lm): each frame is ``data: {json}\\n\\n``;
    the stream ends with ``data: [DONE]\\n\\n``; keepalives are SSE comment
    frames ``": keepalive {processed}/{total}\\n\\n"``.
    """

    def __init__(self) -> None:
        self._frames: list[bytes] = []

    def _add_json(self, chunk: dict[str, object]) -> Self:
        self._frames.append(f"data: {json.dumps(chunk)}\n\n".encode())
        return self

    def raw_data(self, body: str) -> Self:
        """Escape hatch appending a raw ``data: {body}`` frame."""
        self._frames.append(f"data: {body}\n\n".encode())
        return self

    def role_frame(self) -> Self:
        return self._add_json(
            {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}
        )

    def delta(self, text: str) -> Self:
        return self._add_json(
            {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
        )

    def finish_frame(self, reason: str = "stop") -> Self:
        return self._add_json({"choices": [{"delta": {}, "finish_reason": reason}]})

    def usage(self, prompt_tokens: int, completion_tokens: int) -> Self:
        return self._add_json(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )

    def malformed(self) -> Self:
        return self.raw_data("{not json")

    def keepalive(self, processed: int, total: int) -> Self:
        """SSE comment frame (no ``data:`` prefix), emitted by mlx-lm during long prompts."""
        self._frames.append(f": keepalive {processed}/{total}\n\n".encode())
        return self

    def done(self) -> Self:
        return self.raw_data("[DONE]")

    def build(self) -> bytes:
        return b"".join(self._frames)


def sse_frames(
    deltas: list[str] | None = None,
    *,
    finish: str | None = "stop",
    malformed: bool = False,
    keepalive: tuple[int, int] | None = None,
    usage: tuple[int, int] | None = None,
) -> bytes:
    """One-liner for 80% of builder uses; builder remains for odd shapes."""
    b = SseStreamBuilder()
    if malformed:
        b.malformed()
    if keepalive is not None:
        b.keepalive(*keepalive)
    b.role_frame()
    for d in deltas or []:
        b.delta(d)
    if finish is not None:
        b.finish_frame(finish)
    if usage is not None:
        b.usage(*usage)
    b.done()
    return b.build()
