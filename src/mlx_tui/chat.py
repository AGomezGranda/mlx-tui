"""One SSE turn, no UI."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from mlx_tui.sse import (
    delta_content_from_chunk,
    finish_reason_from_chunk,
    iter_sse_data,
    token_accounting,
    usage_from_chunk,
)

_FLUSH_INTERVAL_S = 0.1


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
class TurnResult:
    """Values of one completed turn, ready for UI stamp formatting."""

    full_text: str
    ttft: float
    tok_in_str: str
    tok_out_str: str
    tok_s: float
    finish_reason: str | None = None
    skipped_frames: int = 0


def stream_turn(  # noqa: PLR0913
    url: str,
    payload: dict[str, object],
    *,
    user_chars: int,
    on_flush: Callable[[str], None],
    flush_interval: float = _FLUSH_INTERVAL_S,
    on_active: Callable[[httpx.Response | None], None] | None = None,
) -> TurnResult:
    t_send = time.perf_counter()
    t_first_text: float | None = None
    parts: list[str] = []
    last_flush = t_send
    counted_deltas = 0
    skipped_frames = 0
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    with httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=300.0, write=5.0, pool=5.0)
    ) as client:
        with client.stream("POST", url, json=payload) as response:
            if on_active is not None:
                on_active(response)
            if response.is_error:
                response.read()
                response.raise_for_status()
            for data in iter_sse_data(response.iter_lines()):
                try:
                    chunk = json.loads(data)
                except ValueError:
                    skipped_frames += 1
                    continue
                new_pt, new_ct = usage_from_chunk(chunk)
                if new_pt is not None:
                    prompt_tokens = new_pt
                if new_ct is not None:
                    completion_tokens = new_ct
                reason = finish_reason_from_chunk(chunk)
                if reason is not None:
                    finish_reason = reason
                content = delta_content_from_chunk(chunk)
                if content is not None:
                    if t_first_text is None:
                        t_first_text = time.perf_counter()
                    parts.append(content)
                    counted_deltas += 1
                    now = time.perf_counter()
                    if now - last_flush >= flush_interval:
                        last_flush = now
                        on_flush("".join(parts))
            if on_active is not None:
                on_active(None)
    now = time.perf_counter()
    ttft = (t_first_text - t_send) if t_first_text is not None else now - t_send
    elapsed = (now - t_first_text) if t_first_text is not None else 0.0
    tok_in_str, tok_out_str, tok_s = token_accounting(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        counted_deltas=counted_deltas,
        user_chars=user_chars,
        elapsed=elapsed,
    )
    return TurnResult(
        full_text="".join(parts),
        ttft=ttft,
        tok_in_str=tok_in_str,
        tok_out_str=tok_out_str,
        tok_s=tok_s,
        finish_reason=finish_reason,
        skipped_frames=skipped_frames,
    )
