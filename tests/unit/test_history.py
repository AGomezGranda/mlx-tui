"""Unit tests for history records and storage."""

from __future__ import annotations

import dataclasses

import pytest

from mlx_tui.history.store import HistoryStore, TurnRecord


def _record(**overrides: object) -> TurnRecord:
    base: dict[str, object] = {
        "ts": 1.0,
        "model": "m",
        "prompt_tok": 10,
        "out_tok": 5,
        "first_output_s": 0.1,
        "answer_started_s": 0.2,
        "total_s": 1.0,
        "req_tok_s": 12.3,
        "ctx_len": 10,
        "outcome": "success",
    }
    base.update(overrides)
    return TurnRecord(**base)  # type: ignore[arg-type]


def test_turn_record_is_frozen_and_has_expected_fields() -> None:
    r = _record()
    assert r.model == "m"
    assert len(dataclasses.fields(TurnRecord)) == 15
    assert r.outcome == "success"
    assert r.cached_prompt_tokens is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.model = "other"  # type: ignore[misc]


def test_turn_record_outcome_and_cached_defaults() -> None:
    r = _record(outcome="length_capped", cached_prompt_tokens=109)
    assert r.outcome == "length_capped"
    assert r.cached_prompt_tokens == 109
    r2 = _record()
    assert r2.outcome == "success"
    assert r2.cached_prompt_tokens is None


def test_incomplete_unknown_rate_in_all_records() -> None:
    store = HistoryStore()
    rec = _record(outcome="incomplete", prompt_tok=None, out_tok=None, req_tok_s=None)
    store.add(rec)
    got = store.all_records()[0]
    assert got.req_tok_s is None
    assert got.prompt_tok is None
    assert got.outcome == "incomplete"


def test_store_add_and_series_preserves_order() -> None:
    store = HistoryStore()
    for ts in (1, 2, 3):
        store.add(_record(ts=float(ts)))
    assert [r.ts for r in store.series("m")] == [1.0, 2.0, 3.0]


def test_store_per_model_isolation() -> None:
    store = HistoryStore()
    store.add(_record(ts=1, model="a", req_tok_s=1))
    store.add(_record(ts=2, model="a", req_tok_s=2))
    store.add(_record(ts=3, model="b", req_tok_s=3))
    assert len(store.series("a")) == 2
    assert len(store.series("b")) == 1


def test_store_ring_eviction_at_64() -> None:
    store = HistoryStore(max_turns=3)
    for ts in (1, 2, 3, 4):
        store.add(_record(ts=float(ts)))
    assert [r.ts for r in store.series("m")] == [2.0, 3.0, 4.0]


def test_store_outcome_and_cancelled_flags_survive() -> None:
    store = HistoryStore()
    store.add(_record(ts=1, outcome="length_capped"))
    store.add(_record(ts=2, outcome="cancelled", cancelled=True, req_tok_s=None))
    records = store.all_records()
    assert any(r.outcome == "length_capped" for r in records)
    assert any(r.cancelled for r in records)


def test_store_clear_one_model() -> None:
    store = HistoryStore()
    store.add(_record(ts=1, model="a"))
    store.add(_record(ts=2, model="b"))
    store.clear("a")
    assert store.series("a") == []
    assert len(store.series("b")) == 1
    store.clear()
    assert store.series("b") == []


def test_max_turns_constant_is_64() -> None:
    assert HistoryStore()._max == 64  # noqa: SLF001


def test_series_returns_copy_mutating_does_not_affect_store() -> None:
    store = HistoryStore()
    store.add(_record(ts=1))
    original = store.series("m")
    lst = store.series("m")
    lst.clear()
    assert store.series("m") == original


def test_estimated_flags_default_false_and_survive() -> None:
    store = HistoryStore()
    rec = _record(prompt_estimated=True, out_estimated=True)
    store.add(rec)
    got = store.all_records()[0]
    assert got.prompt_estimated is True
    assert got.out_estimated is True
    defaulted = _record(ts=2.0)
    assert defaulted.prompt_estimated is False
    assert defaulted.out_estimated is False


def test_failed_cancelled_counts_unknown() -> None:
    rec = _record(
        outcome="cancelled",
        cancelled=True,
        prompt_tok=None,
        out_tok=None,
        first_output_s=None,
        answer_started_s=None,
        total_s=None,
        req_tok_s=None,
    )
    assert rec.prompt_tok is None
    assert rec.req_tok_s is None
