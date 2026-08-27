"""Unit tests for mlx_tui.history token estimation and window trimming."""

from __future__ import annotations

import dataclasses

import pytest

from mlx_tui.history.sparkline import render_sparkline
from mlx_tui.history.store import HistoryStore, TurnRecord
from mlx_tui.history.tokens import (
    CHARS_PER_TOKEN_EST,
    _tok_int,
    estimate_tokens,
    trim_for_context,
)


def u(text: str) -> dict[str, str]:
    return {"role": "user", "content": text}


def a(text: str) -> dict[str, str]:
    return {"role": "assistant", "content": text}


def test_chars_per_token_constant_is_pinned() -> None:
    # Both history.py and sse.py depend on this value's stability.
    assert CHARS_PER_TOKEN_EST == 3.5


def test_estimate_tokens_matches_chars_over_three_point_five() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("ab") == 1
    assert estimate_tokens("abcd") == 2  # ceil(4/3.5)


def test_trim_returns_empty_for_empty_history() -> None:
    assert trim_for_context([], 100) == []


def test_trim_keeps_everything_when_budget_allows() -> None:
    messages = [u("hi"), a("hello"), u("more")]
    assert trim_for_context(messages, 10_000) == messages


def test_trim_drops_oldest_turns_first() -> None:
    messages = [u("one"), a("reply-one"), u("two"), a("reply-two"), u("three")]
    # Budget fits "two"/"reply-two"/"three" but not turn one.
    budget = (
        estimate_tokens("two") + estimate_tokens("reply-two") + estimate_tokens("three")
    )
    trimmed = trim_for_context(messages, budget)
    assert trimmed == [u("two"), a("reply-two"), u("three")]


def test_trim_boundary_lands_on_user_message() -> None:
    messages = [u("old prompt"), a("old reply"), u("new")]
    # Budget fits only from the assistant reply onward — an illegal boundary —
    # so the window must fall forward to the next user message.
    budget = estimate_tokens("old reply") + estimate_tokens("new")
    trimmed = trim_for_context(messages, budget)
    assert trimmed[0]["role"] == "user"
    assert trimmed == [u("new")]


def test_trim_never_drops_the_newest_message() -> None:
    huge = "x" * 100_000
    messages = [u(huge), u("latest")]
    trimmed = trim_for_context(messages, 1)
    assert trimmed == [u("latest")]


def test_trim_single_huge_message_still_sent() -> None:
    huge = "y" * 100_000
    trimmed = trim_for_context([u(huge)], 1)
    assert trimmed == [u(huge)]


def test_turn_record_is_frozen_and_has_nine_fields() -> None:
    r = TurnRecord(
        ts=1.0,
        model="m",
        prompt_tok=10,
        out_tok=5,
        ttft_s=0.1,
        tok_s=12.3,
        ctx_len=10,
        cold=False,
    )
    assert r.model == "m"
    assert len(dataclasses.fields(TurnRecord)) == 9
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.model = "other"  # type: ignore[misc]


def test_store_add_and_series_preserves_order() -> None:
    store = HistoryStore()
    for ts in (1, 2, 3):
        store.add(
            TurnRecord(
                ts=float(ts),
                model="m",
                prompt_tok=10,
                out_tok=5,
                ttft_s=0.1,
                tok_s=12.3,
                ctx_len=10,
                cold=False,
            )
        )
    assert [r.ts for r in store.series("m")] == [1.0, 2.0, 3.0]


def test_store_per_model_isolation() -> None:
    store = HistoryStore()
    store.add(
        TurnRecord(
            ts=1,
            model="a",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=1,
            ctx_len=1,
            cold=False,
        )
    )
    store.add(
        TurnRecord(
            ts=2,
            model="a",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=2,
            ctx_len=1,
            cold=False,
        )
    )
    store.add(
        TurnRecord(
            ts=3,
            model="b",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=3,
            ctx_len=1,
            cold=False,
        )
    )
    assert len(store.series("a")) == 2
    assert len(store.series("b")) == 1
    assert store.models() == ["a", "b"]


def test_store_ring_eviction_at_64() -> None:
    store = HistoryStore(max_turns=3)
    for ts in (1, 2, 3, 4):
        store.add(
            TurnRecord(
                ts=float(ts),
                model="m",
                prompt_tok=10,
                out_tok=5,
                ttft_s=0.1,
                tok_s=12.3,
                ctx_len=10,
                cold=False,
            )
        )
    assert [r.ts for r in store.series("m")] == [2.0, 3.0, 4.0]


def test_store_cold_and_cancelled_flags_survive() -> None:
    store = HistoryStore()
    store.add(
        TurnRecord(
            ts=1,
            model="m",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=1,
            ctx_len=1,
            cold=True,
        )
    )
    store.add(
        TurnRecord(
            ts=2,
            model="m",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=1,
            ctx_len=1,
            cold=False,
            cancelled=True,
        )
    )
    records = store.all_records()
    assert any(r.cold for r in records)
    assert any(r.cancelled for r in records)


def test_store_clear_one_model() -> None:
    store = HistoryStore()
    store.add(
        TurnRecord(
            ts=1,
            model="a",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=1,
            ctx_len=1,
            cold=False,
        )
    )
    store.add(
        TurnRecord(
            ts=2,
            model="b",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=1,
            ctx_len=1,
            cold=False,
        )
    )
    store.clear("a")
    assert store.series("a") == []
    assert len(store.series("b")) == 1
    store.clear()
    assert store.series("b") == []
    assert store.models() == []


def test_max_turns_constant_is_64() -> None:
    assert HistoryStore()._max == 64  # noqa: SLF001


def test_series_returns_copy_mutating_does_not_affect_store() -> None:
    store = HistoryStore()
    store.add(
        TurnRecord(
            ts=1,
            model="m",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=1,
            ctx_len=1,
            cold=False,
        )
    )
    original = store.series("m")
    lst = store.series("m")
    lst.clear()
    assert store.series("m") == original


def test_render_empty_and_cold_filtered_return_placeholder() -> None:
    assert render_sparkline([]) == ("", "no history yet — chat to build it")
    cold_only = [
        TurnRecord(
            ts=1,
            model="m",
            prompt_tok=1,
            out_tok=1,
            ttft_s=0.1,
            tok_s=5,
            ctx_len=10,
            cold=True,
        )
    ]
    assert render_sparkline(cold_only) == ("", "no history yet — chat to build it")


def test_render_single_turn_produces_one_braille_char_plus_legend() -> None:
    rec = TurnRecord(
        ts=1,
        model="m",
        prompt_tok=10,
        out_tok=5,
        ttft_s=0.1,
        tok_s=10,
        ctx_len=100,
        cold=False,
    )
    braille, legend = render_sparkline([rec], width=32, height_rows=2)
    stripped = braille.replace("\n", "").strip()
    assert len(stripped) == 1
    assert 0x2800 <= ord(stripped) <= 0x28FF
    assert "10.0 tok/s" in legend


def test_render_flat_tok_s_does_not_div0() -> None:
    recs = [
        TurnRecord(
            ts=float(i),
            model="m",
            prompt_tok=10,
            out_tok=5,
            ttft_s=0.1,
            tok_s=5.0,
            ctx_len=10,
            cold=False,
        )
        for i in range(3)
    ]
    braille, legend = render_sparkline(recs)
    assert braille  # non-empty
    assert "5.0 tok/s" in legend
    # flat series should render as bottom line — at least bottom row non-blank
    lines = braille.split("\n")
    assert any(line.strip() for line in lines)


def test_render_cancelled_excluded() -> None:
    recs = [
        TurnRecord(
            ts=1,
            model="m",
            prompt_tok=10,
            out_tok=5,
            ttft_s=0.1,
            tok_s=10,
            ctx_len=10,
            cold=False,
        ),
        TurnRecord(
            ts=2,
            model="m",
            prompt_tok=10,
            out_tok=5,
            ttft_s=0.1,
            tok_s=20,
            ctx_len=10,
            cold=False,
            cancelled=True,
        ),
    ]
    braille, legend = render_sparkline(recs)
    assert "1 turns" in legend
    assert braille.replace("\n", "").strip()  # one visible => one char


def test_render_width_clipping() -> None:
    recs = [
        TurnRecord(
            ts=float(i),
            model="m",
            prompt_tok=10,
            out_tok=5,
            ttft_s=0.1,
            tok_s=float(i),
            ctx_len=10 + i,
            cold=False,
        )
        for i in range(70)
    ]
    braille, _legend = render_sparkline(recs, width=32, height_rows=2)
    lines = braille.split("\n")
    assert len(lines) == 2
    for line in lines:
        assert len(line) == 32


def test_render_height_rows_two_produces_two_lines() -> None:
    recs = [
        TurnRecord(
            ts=float(i),
            model="m",
            prompt_tok=10,
            out_tok=5,
            ttft_s=0.1,
            tok_s=float(i),
            ctx_len=10,
            cold=False,
        )
        for i in range(4)
    ]
    braille2, _ = render_sparkline(recs, height_rows=2)
    assert braille2.count("\n") == 1
    braille1, _ = render_sparkline(recs, height_rows=1)
    assert "\n" not in braille1


def test_tok_int_parses_est_and_digit_guards_sentinel() -> None:
    assert _tok_int("20 (est)") == 20
    assert _tok_int("12") == 12
    assert _tok_int("—") == 0
    assert _tok_int("") == 0


def test_shade_for_ctx_quartiles() -> None:
    from mlx_tui.history.sparkline import _shade_for_ctx  # noqa: PLC0415

    styles = _shade_for_ctx([100, 200, 300, 400])
    assert styles[0] == "dim"
    assert styles[-1] == "bold"
    assert styles[2] == ""
    # empty input returns empty
    assert _shade_for_ctx([]) == []


def test_memory_record_is_frozen() -> None:
    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    r = MemoryRecord(ts=1.0, model="m", rss_gib=1.2, avail_gib=8.0, total_gib=16.0)
    assert r.model == "m"
    assert len(dataclasses.fields(MemoryRecord)) == 5
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.model = "other"  # type: ignore[misc]


def test_memory_store_add_and_series_preserves_order() -> None:
    from mlx_tui.history.store import MemoryRecord, MemoryStore  # noqa: PLC0415

    store = MemoryStore()
    for ts in (1, 2, 3):
        store.add(
            MemoryRecord(
                ts=float(ts), model="m", rss_gib=1.0, avail_gib=8.0, total_gib=16.0
            )
        )
    assert [r.ts for r in store.series()] == [1.0, 2.0, 3.0]


def test_memory_store_clear() -> None:
    from mlx_tui.history.store import MemoryRecord, MemoryStore  # noqa: PLC0415

    store = MemoryStore()
    store.add(MemoryRecord(ts=1, model="m", rss_gib=1.0, avail_gib=8.0, total_gib=16.0))
    store.clear()
    assert store.series() == []


def test_memory_store_ring_eviction_at_256() -> None:
    from mlx_tui.history.store import MemoryRecord, MemoryStore  # noqa: PLC0415

    store = MemoryStore(maxlen=3)
    for ts in (1, 2, 3, 4):
        store.add(
            MemoryRecord(
                ts=float(ts), model="m", rss_gib=1.0, avail_gib=8.0, total_gib=16.0
            )
        )
    assert [r.ts for r in store.series()] == [2.0, 3.0, 4.0]


def test_memory_store_series_returns_copy() -> None:
    from mlx_tui.history.store import MemoryRecord, MemoryStore  # noqa: PLC0415

    store = MemoryStore()
    store.add(MemoryRecord(ts=1, model="m", rss_gib=1.0, avail_gib=8.0, total_gib=16.0))
    lst = store.series()
    lst.clear()
    assert len(store.series()) == 1


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
    assert "2.5 GB RSS" in legend
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
    assert "1.0 GB RSS" in legend


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
    # should have 1 char (2 records -> 1 braille char) and not crash
    assert braille
    assert "2 samples" in legend
