"""Unit tests for the swap state machine and health-timeout math."""

from __future__ import annotations

import pytest

from mlx_tui.swap import InvalidTransition, SwapMachine, SwapState, health_timeout


def _reach(machine: SwapMachine, state: SwapState) -> None:
    """Legally drive a fresh machine into ``state``."""
    paths: dict[SwapState, tuple[SwapState, ...]] = {
        SwapState.IDLE: (),
        SwapState.STOPPING: (SwapState.STOPPING,),
        SwapState.STARTING: (SwapState.STARTING,),
        SwapState.WAITING_HEALTH: (SwapState.WAITING_HEALTH,),
        SwapState.FAILED: (SwapState.STOPPING, SwapState.FAILED),
    }
    for step in paths[state]:
        machine.transition(step)


def test_legal_warm_path() -> None:
    machine = SwapMachine()
    machine.transition(SwapState.WAITING_HEALTH)
    assert machine.busy
    machine.transition(SwapState.IDLE)
    assert not machine.busy


def test_legal_restart_path() -> None:
    machine = SwapMachine()
    machine.transition(SwapState.STOPPING)
    machine.transition(SwapState.STARTING)
    machine.transition(SwapState.WAITING_HEALTH)
    machine.transition(SwapState.IDLE)
    assert not machine.busy


def test_legal_cold_start_path() -> None:
    machine = SwapMachine()
    # IDLE→STARTING is legal: cold start never stops a server first.
    machine.transition(SwapState.STARTING)
    machine.transition(SwapState.WAITING_HEALTH)
    machine.transition(SwapState.IDLE)


def test_legal_failure_acknowledged_reset() -> None:
    machine = SwapMachine()
    machine.transition(SwapState.STOPPING)
    machine.transition(SwapState.FAILED)
    machine.transition(SwapState.IDLE)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (SwapState.IDLE, SwapState.FAILED),
        (SwapState.FAILED, SwapState.FAILED),
        (SwapState.STOPPING, SwapState.IDLE),
        (SwapState.WAITING_HEALTH, SwapState.STARTING),
    ],
)
def test_illegal_transitions_raise(source: SwapState, target: SwapState) -> None:
    machine = SwapMachine()
    _reach(machine, source)
    with pytest.raises(InvalidTransition):
        machine.transition(target)


@pytest.mark.parametrize(
    "state",
    [
        SwapState.STOPPING,
        SwapState.STARTING,
        SwapState.WAITING_HEALTH,
        SwapState.FAILED,
    ],
)
def test_busy_in_every_non_idle_state(state: SwapState) -> None:
    machine = SwapMachine()
    _reach(machine, state)
    assert machine.busy


def test_health_timeout_defaults() -> None:
    assert health_timeout(0) == 60.0
    assert health_timeout(5 * 2**30) == 110.0


def test_health_timeout_custom_rates() -> None:
    assert health_timeout(2**30, base_s=10.0, per_gib_s=1.0) == 11.0
