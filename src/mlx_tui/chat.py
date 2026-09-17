"""One SSE turn, no UI."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import httpx

from mlx_tui.sse import (
    SSEDecoder,
    TokenAccounting,
    cached_prompt_from_chunk,
    delta_content_from_chunk,
    delta_reasoning_from_chunk,
    delta_tool_fragments_from_chunk,
    finish_reason_from_chunk,
    token_accounting,
    usage_from_chunk,
)


def error_detail(response: httpx.Response) -> str:
    """Extract the server's explanation from an error body, if one parses."""
    try:
        body: object = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    detail: object = body.get("detail")
    err = body.get("error")
    if detail is None:
        detail = err.get("message") if isinstance(err, dict) else err
    if not isinstance(detail, str) or not detail.strip():
        return ""
    return f": {detail.strip()}"


@dataclass(frozen=True)
class TurnProgress:
    """One incremental delta; the pane accumulates without resending history."""

    answer_delta: str = ""
    reasoning_delta: str = ""
    tool_fragments: tuple[dict[str, object], ...] = ()
    response_model: str | None = None


@dataclass(frozen=True)
class TurnResult:
    """Values of one completed turn, ready for UI stamp formatting."""

    full_text: str
    accounting: TokenAccounting
    finish_reason: str | None = None
    skipped_frames: int = 0
    response_model: str | None = None
    reasoning_text: str = ""
    tool_calls: tuple[dict[str, object], ...] = ()
    first_output_s: float | None = None
    answer_started_s: float | None = None
    total_s: float = 0.0
    cached_prompt_tokens: int | None = None
    stream_complete: bool = False


def _merge_tool_fragments(
    merged: dict[int, dict[str, object]], fragments: Iterable[dict[str, object]]
) -> None:
    for frag in fragments:
        index = frag["index"]
        assert isinstance(index, int)
        slot = merged.setdefault(index, {"index": index, "arguments": ""})
        for key in ("id", "type", "name"):
            if key in frag and key not in slot:
                slot[key] = frag[key]
        args = frag.get("arguments")
        if isinstance(args, str):
            current = slot.get("arguments")
            slot["arguments"] = (current if isinstance(current, str) else "") + args


async def stream_turn(  # noqa: PLR0913, PLR0912, PLR0915
    url: str,
    payload: dict[str, object],
    *,
    prompt_estimate: int,
    on_flush: Callable[[str], None],
    flush_interval: float = 0.1,
    on_activity: Callable[[str], None] | None = None,
    on_progress: Callable[[TurnProgress], None] | None = None,
) -> TurnResult:
    t_send = time.monotonic()
    t_first_output: float | None = None
    t_answer_start: float | None = None
    parts: list[str] = []
    reasoning_parts: list[str] = []
    merged_tools: dict[int, dict[str, object]] = {}
    last_flush = t_send
    skipped_frames = 0
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    response_model: str | None = None
    done_seen = False
    decoder = SSEDecoder()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=300.0, write=5.0, pool=5.0)
    ) as client:
        async with client.stream("POST", url, json=payload) as response:
            if response.is_error:
                await response.aread()
                response.raise_for_status()
            async for raw_line in response.aiter_lines():
                payload_str = decoder.feed(raw_line)
                if payload_str is None:
                    continue
                if payload_str == "[DONE]":
                    done_seen = True
                    break
                try:
                    chunk = json.loads(payload_str)
                except ValueError:
                    skipped_frames += 1
                    continue
                chunk_model = chunk.get("model") if isinstance(chunk, dict) else None
                if isinstance(chunk_model, str) and chunk_model:
                    response_model = chunk_model
                    if on_progress is not None:
                        on_progress(TurnProgress(response_model=chunk_model))
                new_pt, new_ct = usage_from_chunk(chunk)
                if new_pt is not None:
                    prompt_tokens = new_pt
                if new_ct is not None:
                    completion_tokens = new_ct
                cached = cached_prompt_from_chunk(chunk, prompt_tokens)
                if cached is not None:
                    cached_prompt_tokens = cached
                reason = finish_reason_from_chunk(chunk)
                if reason is not None:
                    finish_reason = reason
                reasoning = delta_reasoning_from_chunk(chunk)
                if reasoning is not None:
                    if t_first_output is None:
                        t_first_output = time.monotonic()
                    reasoning_parts.append(reasoning)
                    if on_activity is not None:
                        on_activity("".join(reasoning_parts))
                    if on_progress is not None:
                        on_progress(TurnProgress(reasoning_delta=reasoning))
                tool_frags = delta_tool_fragments_from_chunk(chunk)
                if tool_frags:
                    if t_first_output is None:
                        t_first_output = time.monotonic()
                    _merge_tool_fragments(merged_tools, tool_frags)
                    if on_progress is not None:
                        on_progress(
                            TurnProgress(
                                tool_fragments=tuple(dict(frag) for frag in tool_frags)
                            )
                        )
                content = delta_content_from_chunk(chunk)
                if content is not None:
                    if t_first_output is None:
                        t_first_output = time.monotonic()
                    if t_answer_start is None:
                        t_answer_start = time.monotonic()
                    parts.append(content)
                    if on_progress is not None:
                        on_progress(TurnProgress(answer_delta=content))
                    now = time.monotonic()
                    if now - last_flush >= flush_interval:
                        last_flush = now
                        on_flush("".join(parts))
    t_end = time.monotonic()
    total_s = t_end - t_send
    first_output_s = (t_first_output - t_send) if t_first_output is not None else None
    answer_started_s = (t_answer_start - t_send) if t_answer_start is not None else None
    full_text = "".join(parts)
    reasoning_text = "".join(reasoning_parts)
    tool_calls = tuple({**slot} for _, slot in sorted(merged_tools.items()))
    # Drop index-only slots with no identity or argument content.
    tool_calls = tuple(
        tc
        for tc in tool_calls
        if len(tc) > 1
        and not (set(tc) == {"index", "arguments"} and not tc["arguments"])
    )
    observed_text = (
        full_text
        + reasoning_text
        + "".join(str(tc.get("arguments", "")) for tc in tool_calls)
    )
    accounting = token_accounting(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_estimate=prompt_estimate,
        full_text=observed_text,
        elapsed=total_s,
        cached_prompt_tokens=cached_prompt_tokens,
    )
    stream_complete = (
        done_seen
        and skipped_frames == 0
        and finish_reason == "stop"
        and bool(full_text)
    )
    return TurnResult(
        full_text=full_text,
        accounting=accounting,
        finish_reason=finish_reason,
        skipped_frames=skipped_frames,
        response_model=response_model,
        reasoning_text=reasoning_text,
        tool_calls=tool_calls,
        first_output_s=first_output_s,
        answer_started_s=answer_started_s,
        total_s=total_s,
        cached_prompt_tokens=cached_prompt_tokens,
        stream_complete=stream_complete,
    )
