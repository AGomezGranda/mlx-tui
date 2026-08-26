"""Swap state machine and health-wait timeout math (idea doc §v1)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class BootPlan:
    """Everything one boot/restart worker run needs; config pre-checked by caller."""

    model_id: str | None
    size_on_disk: int
    stop_first: bool
    success_line: str


class SwapState(Enum):
    IDLE = "idle"
    STOPPING = "stopping"
    STARTING = "starting"
    WAITING_HEALTH = "waiting-health"
    FAILED = "failed"


_ALLOWED: dict[SwapState, frozenset[SwapState]] = {
    SwapState.IDLE: frozenset(
        {SwapState.STOPPING, SwapState.STARTING, SwapState.WAITING_HEALTH}
    ),
    # STOPPING/STARTING = restart path; STARTING alone also serves cold-start;
    # WAITING_HEALTH from IDLE = warm probe path.
    SwapState.STOPPING: frozenset({SwapState.STARTING, SwapState.FAILED}),
    SwapState.STARTING: frozenset({SwapState.WAITING_HEALTH, SwapState.FAILED}),
    SwapState.WAITING_HEALTH: frozenset({SwapState.IDLE, SwapState.FAILED}),
    SwapState.FAILED: frozenset({SwapState.IDLE}),  # acknowledged reset
}


class InvalidTransition(Exception):
    """Raised when a swap-state change is not in the allowed graph."""


class SwapMachine:
    """Tiny explicit state machine; only the swap workers touch it."""

    def __init__(self) -> None:
        self.state: SwapState = SwapState.IDLE

    def transition(self, new: SwapState) -> None:
        if new not in _ALLOWED[self.state]:
            raise InvalidTransition(
                f"illegal swap transition {self.state.value} -> {new.value}"
            )
        self.state = new

    @property
    def busy(self) -> bool:
        return self.state is not SwapState.IDLE


def health_timeout(
    size_on_disk: int, base_s: float = 60.0, per_gib_s: float = 10.0
) -> float:
    """Health-wait deadline: ~60s base plus margin per GiB of weights."""
    return base_s + per_gib_s * (size_on_disk / 2**30)
