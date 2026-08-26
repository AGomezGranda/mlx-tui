"""UI-free streaming chat client consuming one SSE turn."""

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


@dataclass(frozen=True)
class TurnResult:
    """Values of one completed turn, ready for UI stamp formatting."""

    full_text: str
    ttft: float  # seconds to first text-bearing delta (never first byte/role frame)
    tok_in_str: str
    tok_out_str: str
    tok_s: float  # denominator is last-chunk time minus first-text time, never t_send
    finish_reason: str | None = None  # "stop", "length", … None if server never said
    skipped_frames: int = 0  # malformed JSON frames dropped mid-stream


class ChatClient:
    """Streams one chat-completion turn over SSE without knowing about the UI.

    ``active_response`` is read by the App's cancel action from the UI thread
    while the worker thread writes it — same cross-thread pattern as the old
    ``_active_stream``; the **App** clears it in its worker ``finally``, not
    the client.

    httpx exceptions propagate deliberately — mapping them to user-facing
    messages is the UI layer's job.
    """

    def __init__(self) -> None:
        self.active_response: httpx.Response | None = None

    def stream_turn(
        self,
        url: str,
        payload: dict[str, object],
        *,
        user_chars: int,
        on_flush: Callable[[str], None],
        flush_interval: float = _FLUSH_INTERVAL_S,
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
                self.active_response = response
                # A 4xx/5xx JSON error body parses as zero SSE frames, which
                # would otherwise masquerade as a successful empty turn with
                # fabricated stamp numbers. Surface it instead — and read the
                # small body here, inside the stream context, so its detail
                # stays accessible to the UI after the response closes.
                if response.is_error:
                    response.read()
                    response.raise_for_status()
                for data in iter_sse_data(response.iter_lines()):
                    # One malformed frame degrades to a lost token, never a
                    # crashed app — but the loss is counted so the UI can say
                    # why a reply came back thin or empty.
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
                        # Throttled to ~10 Hz: consecutive partial RichLog.write()s
                        # render as separate lines on textual 8.2.8, so streaming
                        # repaints one accumulated Static block instead.
                        if now - last_flush >= flush_interval:
                            last_flush = now
                            on_flush("".join(parts))
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
