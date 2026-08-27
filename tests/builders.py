"""Builders composing canned protocol payloads for tests."""

from __future__ import annotations

import json


def _frame(chunk: dict[str, object]) -> bytes:
    return f"data: {json.dumps(chunk)}\n\n".encode()


def sse_frames(  # noqa: PLR0913
    deltas: list[str] | None = None,
    *,
    finish: str | None = "stop",
    malformed: bool = False,
    keepalive: tuple[int, int] | None = None,
    usage: tuple[int, int] | None = None,
    done: bool = True,
) -> bytes:
    """One-liner for builder uses; covers malformed/keepalive/usage/done."""
    frames: list[bytes] = []
    if malformed:
        frames.append(b"data: {not json\n\n")
    if keepalive is not None:
        frames.append(f": keepalive {keepalive[0]}/{keepalive[1]}\n\n".encode())
    # role frame
    frames.append(
        _frame({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]})
    )
    for d in deltas or []:
        frames.append(
            _frame({"choices": [{"delta": {"content": d}, "finish_reason": None}]})
        )
    if finish is not None:
        frames.append(_frame({"choices": [{"delta": {}, "finish_reason": finish}]}))
    if usage is not None:
        pt, ct = usage
        frames.append(
            _frame(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": pt,
                        "completion_tokens": ct,
                        "total_tokens": pt + ct,
                    },
                }
            )
        )
    if done:
        frames.append(b"data: [DONE]\n\n")
    return b"".join(frames)
