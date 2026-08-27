"""Swap state machine and health-wait timeout math (idea doc §v1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SwapState(Enum):  # kept for test compat, graph removed
    IDLE = "idle"
    STOPPING = "stopping"
    STARTING = "starting"
    WAITING_HEALTH = "waiting-health"
    FAILED = "failed"


@dataclass(frozen=True)
class BootPlan:
    """Everything one boot/restart worker run needs; config pre-checked by caller."""

    model_id: str | None
    size_on_disk: int
    stop_first: bool
    success_line: str


def health_timeout(
    size_on_disk: int, base_s: float = 60.0, per_gib_s: float = 10.0
) -> float:
    """Health-wait deadline: ~60s base plus margin per GiB of weights."""
    return base_s + per_gib_s * (size_on_disk / 2**30)
