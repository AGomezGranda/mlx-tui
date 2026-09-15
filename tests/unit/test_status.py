"""Unit tests for mlx_tui.status domain logic."""

from __future__ import annotations

import pytest

from mlx_tui.status import (
    health_state_from_response,
    probe_from_response,
)


@pytest.mark.parametrize(
    ("status_code", "body", "state"),
    [
        (200, {"data": [{"id": "m"}]}, "green"),
        (200, {"data": [{"id": ""}, {"id": "second"}]}, "green"),
        (200, {"data": [{"id": "first"}, {"id": "second"}]}, "green"),
        (200, {"data": [{"id": 123}, {"id": "ok"}]}, "green"),
        (200, {"data": ["x"]}, "green"),
        (200, {"data": [{"no_id": 1}]}, "green"),
        (200, {"data": []}, "amber"),
        (200, {}, "amber"),
        (200, None, "amber"),
        (200, "junk", "amber"),
        (200, {"data": "junk"}, "amber"),
        (502, "<html>proxy</html>", "amber"),
        (500, {"data": [{"id": "m"}]}, "amber"),
    ],
)
def test_probe_from_response(status_code: int, body: object, state: str) -> None:
    probe = probe_from_response(status_code, body)
    assert probe.state == state
    assert probe.model_id is None


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (200, {"status": "ok"}, "green"),
        (200, {"data": [{"id": "m"}]}, "amber"),
        (503, {"status": "ok"}, "amber"),
    ],
)
def test_health_state_is_independent(
    status_code: int, body: object, expected: str
) -> None:
    assert health_state_from_response(status_code, body) == expected
