"""Unit tests for chat and memory sparkline rendering."""

from __future__ import annotations

import dataclasses

import pytest

from mlx_tui.history.sparkline import render_sparkline
from mlx_tui.history.store import TurnRecord


def _record(**overrides: object) -> TurnRecord:
    base: dict[str, object] = {
        "ts": 1,
        "model": "m",
        "prompt_tok": 10,
        "out_tok": 5,
        "first_output_s": 0.1,
        "answer_started_s": 0.2,
        "total_s": 1.0,
        "req_tok_s": 10.0,
        "ctx_len": 100,
        "outcome": "success",
    }
    base.update(overrides)
    return TurnRecord(**base)  # type: ignore[arg-type]


def test_render_empty_and_unsuccessful_filtered_return_placeholder() -> None:
    assert render_sparkline([]) == ("", "no history yet — chat to build it")
    unsuccessful = [_record(outcome="length_capped", req_tok_s=None)]
    assert render_sparkline(unsuccessful) == ("", "no history yet — chat to build it")
    cancelled = [_record(outcome="cancelled", cancelled=True, req_tok_s=None)]
    assert render_sparkline(cancelled) == ("", "no history yet — chat to build it")


def test_render_single_turn_produces_one_braille_char_plus_legend() -> None:
    rec = _record(req_tok_s=10.0, ctx_len=100)
    braille, legend = render_sparkline([rec], width=32, height_rows=2)
    stripped = braille.replace("\n", "").strip()
    assert len(stripped) == 1
    assert 0x2800 <= ord(stripped) <= 0x28FF
    assert "10.0 client request tok/s" in legend


def test_render_flat_req_does_not_div0() -> None:
    recs = [_record(ts=float(i), req_tok_s=5.0, ctx_len=10) for i in range(3)]
    braille, legend = render_sparkline(recs)
    assert braille  # non-empty
    assert "5.0 client request tok/s" in legend
    lines = braille.split("\n")
    assert any(line.strip() for line in lines)


def test_render_unsuccessful_excluded() -> None:
    recs = [
        _record(ts=1, req_tok_s=10.0, ctx_len=10),
        _record(ts=2, outcome="incomplete", req_tok_s=None, ctx_len=10),
    ]
    braille, legend = render_sparkline(recs)
    assert "1 turns" in legend
    assert braille.replace("\n", "").strip()


def test_render_width_clipping() -> None:
    recs = [_record(ts=float(i), req_tok_s=float(i), ctx_len=10 + i) for i in range(70)]
    braille, _legend = render_sparkline(recs, width=32, height_rows=2)
    lines = braille.split("\n")
    assert len(lines) == 2
    for line in lines:
        assert len(line) == 32


def test_render_height_rows_two_produces_two_lines() -> None:
    recs = [_record(ts=float(i), req_tok_s=float(i), ctx_len=10) for i in range(4)]
    braille2, _ = render_sparkline(recs, height_rows=2)
    assert braille2.count("\n") == 1
    braille1, _ = render_sparkline(recs, height_rows=1)
    assert "\n" not in braille1


def test_shade_for_ctx_quartiles() -> None:
    from mlx_tui.history.sparkline import _shade_for_ctx  # noqa: PLC0415

    styles = _shade_for_ctx([100, 200, 300, 400])
    assert styles[0] == "dim"
    assert styles[-1] == "bold"
    assert styles[2] == ""
    assert _shade_for_ctx([]) == []


def test_memory_record_is_frozen() -> None:
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    r = MemoryRecord(ts=1.0, model="m", rss_gib=1.2, avail_gib=8.0, total_gib=16.0)
    assert r.model == "m"
    assert len(dataclasses.fields(MemoryRecord)) == 5
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.model = "other"  # type: ignore[misc]


def test_render_memory_empty_returns_placeholder() -> None:
    from mlx_tui.history.sparkline import render_memory_sparkline  # noqa: PLC0415

    assert render_memory_sparkline([]) == ("", "no memory samples yet")


def test_render_memory_all_none_returns_placeholder() -> None:
    from mlx_tui.history.sparkline import render_memory_sparkline  # noqa: PLC0415
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    recs = [MemoryRecord(ts=1, model="m", rss_gib=None, avail_gib=8.0, total_gib=16.0)]
    assert render_memory_sparkline(recs) == ("", "no memory samples yet")


def test_render_memory_single_produces_braille_and_legend() -> None:
    from mlx_tui.history.sparkline import render_memory_sparkline  # noqa: PLC0415
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    rec = MemoryRecord(ts=1, model="m", rss_gib=2.5, avail_gib=8.0, total_gib=16.0)
    braille, legend = render_memory_sparkline([rec], width=32, height_rows=2)
    stripped = braille.replace("\n", "").strip()
    assert len(stripped) == 1
    assert 0x2800 <= ord(stripped) <= 0x28FF
    assert "2.5 GiB RSS" in legend
    assert "avail 8.0/16.0" in legend
    assert "1 samples" in legend


def test_render_memory_flat_does_not_div0() -> None:
    from mlx_tui.history.sparkline import render_memory_sparkline  # noqa: PLC0415
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    recs = [
        MemoryRecord(ts=float(i), model="m", rss_gib=1.0, avail_gib=8.0, total_gib=16.0)
        for i in range(3)
    ]
    braille, legend = render_memory_sparkline(recs)
    assert braille
    assert "1.0 GiB RSS" in legend


def test_render_memory_width_clipping() -> None:
    from mlx_tui.history.sparkline import render_memory_sparkline  # noqa: PLC0415
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    recs = [
        MemoryRecord(
            ts=float(i), model="m", rss_gib=float(i), avail_gib=8.0, total_gib=16.0
        )
        for i in range(70)
    ]
    braille, _ = render_memory_sparkline(recs, width=32, height_rows=2)
    lines = braille.split("\n")
    assert len(lines) == 2
    for line in lines:
        assert len(line) == 32


def test_render_memory_with_none_blank_column() -> None:
    from mlx_tui.history.sparkline import render_memory_sparkline  # noqa: PLC0415
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    recs = [
        MemoryRecord(ts=1, model="m", rss_gib=None, avail_gib=8.0, total_gib=16.0),
        MemoryRecord(ts=2, model="m", rss_gib=2.0, avail_gib=7.5, total_gib=16.0),
    ]
    braille, legend = render_memory_sparkline(recs, width=32, height_rows=1)
    assert braille
    assert "2 samples" in legend


def test_render_memory_latest_unknown_not_silent() -> None:
    from mlx_tui.history.sparkline import render_memory_sparkline  # noqa: PLC0415
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    recs = [
        MemoryRecord(ts=1, model="m", rss_gib=2.0, avail_gib=8.0, total_gib=16.0),
        MemoryRecord(ts=2, model="m", rss_gib=None, avail_gib=7.5, total_gib=16.0),
    ]
    _braille, legend = render_memory_sparkline(recs, width=32, height_rows=1)
    assert "RSS unknown" in legend
