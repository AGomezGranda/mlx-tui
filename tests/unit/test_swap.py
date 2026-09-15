"""Unit tests for the swap state machine and health-timeout math."""

from __future__ import annotations

import pytest

from mlx_tui.swap import health_timeout, resolve_swap_action


def test_health_timeout_defaults() -> None:
    assert health_timeout(0) == 60.0
    assert health_timeout(5 * 2**30) == 110.0


@pytest.mark.parametrize(
    ("status", "policy", "has_start", "has_stop", "expected"),
    [
        ("amber", "auto", True, True, "refuse"),
        ("amber", "warm", False, False, "refuse"),
        ("amber", "restart", True, True, "refuse"),
        ("green", "auto", True, True, "restart"),
        ("green", "auto", False, False, "warm"),
        ("green", "auto", True, False, "warm"),
        ("green", "warm", False, False, "warm"),
        ("green", "warm", True, True, "warm"),
        ("green", "restart", True, True, "restart"),
        ("green", "restart", False, False, "refuse"),
        ("green", "restart", True, False, "refuse"),
        ("red", "auto", True, True, "restart"),
        ("red", "auto", True, False, "cold"),
        ("red", "auto", False, False, "refuse"),
        ("red", "warm", False, False, "refuse"),
        ("red", "restart", True, True, "restart"),
        ("red", "restart", True, False, "refuse"),
    ],
)
def test_resolve_swap_action_branches(
    status: str, policy: str, has_start: bool, has_stop: bool, expected: str
) -> None:
    assert resolve_swap_action(status, policy, has_start, has_stop) == expected
