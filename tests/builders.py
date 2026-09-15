"""Builders composing canned protocol payloads for tests."""

from __future__ import annotations

import json


def _frame(chunk: dict[str, object], *, no_space: bool = False) -> bytes:
    prefix = "data:" if no_space else "data: "
    return f"{prefix}{json.dumps(chunk)}\n\n".encode()


def sse_frames(  # noqa: PLR0913, PLR0912
    deltas: list[str] | None = None,
    *,
    finish: str | None = "stop",
    malformed: bool = False,
    keepalive: tuple[int, int] | None = None,
    usage: tuple[int, int] | None = None,
    done: bool = True,
    no_space: bool = False,
    model: str = "test-model",
    reasoning: list[str] | None = None,
    tool_calls: list[dict[str, object]] | None = None,
    cached: int | None = None,
    raw_usage: dict[str, object] | None = None,
) -> bytes:
    """One-liner for builder uses; covers malformed/keepalive/usage/done."""
    frames: list[bytes] = []
    if malformed:
        frames.append(b"data:{not json\n\n" if no_space else b"data: {not json\n\n")
    if keepalive is not None:
        frames.append(f": keepalive {keepalive[0]}/{keepalive[1]}\n\n".encode())
    # role frame
    frames.append(
        _frame(
            {
                "model": model,
                "choices": [{"delta": {"role": "assistant"}, "finish_reason": None}],
            },
            no_space=no_space,
        )
    )
    for d in deltas or []:
        frames.append(
            _frame(
                {
                    "model": model,
                    "choices": [{"delta": {"content": d}, "finish_reason": None}],
                },
                no_space=no_space,
            )
        )
    for r in reasoning or []:
        frames.append(
            _frame(
                {
                    "model": model,
                    "choices": [{"delta": {"reasoning": r}, "finish_reason": None}],
                },
                no_space=no_space,
            )
        )
    if tool_calls is not None:
        frames.append(
            _frame(
                {
                    "model": model,
                    "choices": [
                        {"delta": {"tool_calls": tool_calls}, "finish_reason": None}
                    ],
                },
                no_space=no_space,
            )
        )
    if finish is not None:
        frames.append(
            _frame(
                {
                    "model": model,
                    "choices": [{"delta": {}, "finish_reason": finish}],
                },
                no_space=no_space,
            )
        )
    if raw_usage is not None:
        frames.append(
            _frame(
                {"model": model, "choices": [], "usage": raw_usage},
                no_space=no_space,
            )
        )
    elif usage is not None:
        pt, ct = usage
        usage_obj: dict[str, object] = {
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
        }
        if cached is not None:
            usage_obj["prompt_tokens_details"] = {"cached_tokens": cached}
        frames.append(
            _frame(
                {"model": model, "choices": [], "usage": usage_obj},
                no_space=no_space,
            )
        )
    if done:
        frames.append(b"data:[DONE]\n\n" if no_space else b"data: [DONE]\n\n")
    return b"".join(frames)


def sse_multiline_event(chunk: dict[str, object]) -> bytes:
    """One JSON event split across two data lines joined with newline."""
    raw = json.dumps(chunk)
    mid = len(raw) // 2
    return f"data: {raw[:mid]}\ndata: {raw[mid:]}\n\n".encode()
