"""History ring buffers — HistoryStore + MemoryStore."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

_MAX_TURNS = 64


@dataclass(frozen=True)
class MemoryRecord:
    ts: float
    model: str | None
    rss_gib: float | None
    avail_gib: float
    total_gib: float


class MemoryStore:
    """Ring buffer for memory samples; deque(maxlen=256)."""

    def __init__(self, maxlen: int = 256) -> None:
        self._dq: deque[MemoryRecord] = deque(maxlen=maxlen)

    def add(self, item: MemoryRecord) -> None:
        self._dq.append(item)

    def series(self) -> list[MemoryRecord]:
        return list(self._dq)

    def clear(self) -> None:
        self._dq.clear()


@dataclass(frozen=True)
class TurnRecord:
    ts: float
    model: str
    prompt_tok: int
    out_tok: int
    ttft_s: float
    tok_s: float
    ctx_len: int
    cold: bool
    cancelled: bool = False
    prefill_tok_s: float | None = None
    prompt_estimated: bool = False
    out_estimated: bool = False


class HistoryStore:
    """Per-model ring buffers; each model gets deque(maxlen=_MAX_TURNS)."""

    def __init__(self, max_turns: int = _MAX_TURNS) -> None:
        self._max: int = max_turns
        self._by_model: dict[str, deque[TurnRecord]] = {}

    def add(self, record: TurnRecord) -> None:
        self._by_model.setdefault(record.model, deque(maxlen=self._max)).append(record)

    def series(self, model: str) -> list[TurnRecord]:
        ring = self._by_model.get(model)
        return list(ring) if ring is not None else []

    def models(self) -> list[str]:
        return sorted(self._by_model)

    def all_records(self) -> list[TurnRecord]:
        flat: list[TurnRecord] = []
        for ring in self._by_model.values():
            flat.extend(ring)

        def _ts(r: TurnRecord) -> float:
            return r.ts

        flat.sort(key=_ts)
        return flat

    def clear(self, model: str | None = None) -> None:
        if model is None:
            self._by_model.clear()
        else:
            self._by_model.pop(model, None)
