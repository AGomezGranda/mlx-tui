"""History ring buffers — HistoryStore + MemoryStore."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


class _Ring[T]:
    """Single deque(maxlen) wrapper — HistoryStore + MemoryStore both delegate here."""

    def __init__(self, maxlen: int) -> None:
        self._dq: deque[T] = deque(maxlen=maxlen)

    def add(self, item: T) -> None:
        self._dq.append(item)

    def series(self) -> list[T]:
        return list(self._dq)

    def clear(self) -> None:
        self._dq.clear()


_MAX_TURNS = 64


@dataclass(frozen=True)
class MemoryRecord:
    """One memory poll sample, frozen so the future JSONL path is mechanical."""

    ts: float  # time.time()
    model: str | None  # effective_model() at poll time, None→"—"
    rss_gib: float | None  # None when no pid
    avail_gib: float  # always from memory_snapshot()
    total_gib: float


class MemoryStore(_Ring[MemoryRecord]):
    """Ring buffer for memory samples; deque(maxlen=256).

    Not thread-safe — all mutations from the poll loop are on the event
    loop thread; chat workers must hop via App.call_from_thread if they
    ever add memory. No internal lock; single owner keeps stdlib-only.
    """

    def __init__(self, maxlen: int = 256) -> None:
        super().__init__(maxlen=maxlen)


@dataclass(frozen=True)
class TurnRecord:
    """One chat turn, frozen so the future JSONL path is mechanical."""

    ts: float  # time.time(), wall clock
    model: str  # effective_model() at record time, or "—" when unknown
    prompt_tok: int
    out_tok: int
    ttft_s: float
    tok_s: float
    ctx_len: int  # prompt-side context length (usage or estimate of trimmed payload)
    cold: bool
    cancelled: bool = False


class HistoryStore:
    """Per-model ring buffers; each model gets deque(maxlen=_MAX_TURNS).

    Not thread-safe — all mutations from ChatPane's thread=True worker
    must be hopped via App.call_from_thread (see Phase 3b). No internal lock;
    single UI-thread owner keeps the implementation stdlib-only and trivial.
    If a second writer ever appears, add a threading.Lock around
    add/series/models/all_records/clear.
    """

    def __init__(self, max_turns: int = _MAX_TURNS) -> None:
        self._max: int = max_turns
        self._by_model: dict[str, _Ring[TurnRecord]] = {}

    def add(self, record: TurnRecord) -> None:
        self._by_model.setdefault(record.model, _Ring(self._max)).add(record)

    def series(self, model: str) -> list[TurnRecord]:
        ring = self._by_model.get(model)
        return ring.series() if ring is not None else []

    def models(self) -> list[str]:
        return sorted(self._by_model)

    def all_records(self) -> list[TurnRecord]:
        flat: list[TurnRecord] = []
        for ring in self._by_model.values():
            flat.extend(ring.series())

        def _ts(r: TurnRecord) -> float:
            return r.ts

        flat.sort(key=_ts)
        return flat

    def clear(self, model: str | None = None) -> None:
        if model is None:
            self._by_model.clear()
        else:
            self._by_model.pop(model, None)
