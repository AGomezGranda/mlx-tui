"""Unit tests for the swap state machine and health-timeout math."""

from __future__ import annotations

from mlx_tui.swap import health_timeout


def test_health_timeout_defaults() -> None:
    assert health_timeout(0) == 60.0
    assert health_timeout(5 * 2**30) == 110.0


def test_health_timeout_custom_rates() -> None:
    assert health_timeout(2**30, base_s=10.0, per_gib_s=1.0) == 11.0
