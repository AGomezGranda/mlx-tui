"""Unit tests for mlx_tui.status domain logic."""

from __future__ import annotations

import pytest

from mlx_tui.status import (
    ColdTracker,
    MemorySnapshot,
    classify_liveness,
    format_status_line,
    probe_from_response,
)


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (200, {"data": [{"id": "m"}]}, "green"),
        (200, {"data": ["x"]}, "green"),
        (200, {"data": []}, "amber"),
        (200, {}, "amber"),
        (200, None, "amber"),
        (200, "junk", "amber"),
        (502, "<html>proxy</html>", "amber"),
        (500, {"data": [{"id": "m"}]}, "amber"),
    ],
)
def test_classify_liveness(status_code: int, body: object, expected: str) -> None:
    assert classify_liveness(status_code, body) == expected


@pytest.mark.parametrize(
    ("status_code", "body", "state", "model_id"),
    [
        (200, {"data": [{"id": "m"}]}, "green", "m"),
        (200, {"data": [{"id": ""}, {"id": "second"}]}, "green", "second"),
        (200, {"data": [{"id": "first"}, {"id": "second"}]}, "green", None),
        (200, {"data": [{"id": 123}, {"id": "ok"}]}, "green", "ok"),
        (200, {"data": ["x"]}, "green", None),
        (200, {"data": [{"no_id": 1}]}, "green", None),
        (200, {"data": []}, "amber", None),
        (200, {}, "amber", None),
        (200, None, "amber", None),
        (200, "junk", "amber", None),
        (200, {"data": "junk"}, "amber", None),
        (502, "<html>proxy</html>", "amber", None),
        (500, {"data": [{"id": "m"}]}, "amber", None),
    ],
)
def test_probe_from_response(
    status_code: int, body: object, state: str, model_id: str | None
) -> None:
    probe = probe_from_response(status_code, body)
    assert probe.state == state
    assert probe.model_id == model_id
    assert classify_liveness(status_code, body) == state


def test_cold_tracker_fresh_consume_is_false() -> None:
    tracker = ColdTracker()
    assert tracker.consume_cold() is False


def test_cold_tracker_red_before_any_green_arms_nothing() -> None:
    tracker = ColdTracker()
    tracker.observe("red")
    assert tracker.ever_green is False
    tracker.observe("green")
    assert tracker.ever_green
    assert tracker.consume_cold() is False


def test_cold_tracker_full_cycle_arms_once() -> None:
    tracker = ColdTracker()
    tracker.observe("green")
    tracker.observe("red")
    tracker.observe("green")
    assert tracker.cold_pending is True
    assert tracker.consume_cold() is True
    assert tracker.consume_cold() is False
    assert tracker.red_since_green is False


def test_cold_tracker_double_red_between_greens_arms_once() -> None:
    tracker = ColdTracker()
    tracker.observe("green")
    tracker.observe("red")
    tracker.observe("red")
    tracker.observe("green")
    assert tracker.consume_cold() is True
    assert tracker.consume_cold() is False


def test_cold_tracker_amber_is_inert() -> None:
    tracker = ColdTracker()
    tracker.observe("green")
    tracker.observe("amber")
    tracker.observe("red")
    tracker.observe("amber")
    tracker.observe("green")
    assert tracker.consume_cold() is True


def test_format_status_line_all_present_green() -> None:
    line = format_status_line(
        state="green",
        model="m",
        rss_gib=1.9,
        memory=MemorySnapshot(avail_gib=8.0, total_gib=16.0),
        port=8080,
    )
    assert line == "[green]●[/] m · RSS 1.9 GB · avail 8.0/16.0 GB · :8080"


def test_format_status_line_missing_pieces_use_em_dash() -> None:
    line = format_status_line(
        state="red",
        model=None,
        rss_gib=None,
        memory=MemorySnapshot(avail_gib=8.0, total_gib=16.0),
        port=8080,
    )
    assert line == "[red]●[/] — · RSS — GB · avail 8.0/16.0 GB · :8080"


def test_format_status_line_amber_maps_to_yellow_dot() -> None:
    line = format_status_line(
        state="amber",
        model="m",
        rss_gib=1.9,
        memory=MemorySnapshot(avail_gib=8.0, total_gib=16.0),
        port=8080,
    )
    assert line == "[yellow]●[/] m · RSS 1.9 GB · avail 8.0/16.0 GB · :8080"
