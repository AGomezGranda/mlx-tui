"""History ring buffer and records."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlx_tui.operations import OperationKind
    from mlx_tui.process import ProcessIdentity

_MAX_TURNS = 64


@dataclass(frozen=True)
class ResourceSample:
    ts: float
    operation: OperationKind
    process_identity: ProcessIdentity | None
    cpu_percent: float | None
    rss_gib: float | None
    avail_gib: float | None
    total_gib: float | None
    swap_gib: float | None


@dataclass(frozen=True)
class TurnRecord:
    ts: float
    model: str
    prompt_tok: int | None
    out_tok: int | None
    first_output_s: float | None
    answer_started_s: float | None
    total_s: float | None
    req_tok_s: float | None
    ctx_len: int
    outcome: str = "success"
    cancelled: bool = False
    cached_prompt_tokens: int | None = None
    prompt_estimated: bool = False
    out_estimated: bool = False
    excluded_turns: int = 0


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

    def all_records(self) -> list[TurnRecord]:
        return sorted(
            chain.from_iterable(self._by_model.values()),
            key=lambda r: r.ts,  # type: ignore[implicit-any-lambda]
        )

    def clear(self, model: str | None = None) -> None:
        if model is None:
            self._by_model.clear()
        else:
            self._by_model.pop(model, None)
