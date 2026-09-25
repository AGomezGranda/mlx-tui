"""Unit tests for two-minute resource charts and activity ribbon."""

from __future__ import annotations

from mlx_tui.history.charts import (
    WINDOW_S,
    bin_samples,
    cpu_peak,
    is_stale,
    latest_in_window,
    render_activity_ribbon,
    render_resource_chart,
    time_axis_labels,
)
from mlx_tui.history.store import ResourceSample
from mlx_tui.operations import OperationKind
from mlx_tui.process import ProcessIdentity

NOW = 1000.0


def _sample(  # noqa: PLR0913
    ts: float,
    *,
    cpu: float | None = 10.0,
    rss: float | None = 1.5,
    avail: float | None = 8.0,
    total: float | None = 16.0,
    swap: float | None = 0.5,
    op: OperationKind = OperationKind.IDLE,
    ident: ProcessIdentity | None = None,
) -> ResourceSample:
    return ResourceSample(
        ts=ts,
        operation=op,
        process_identity=ident,
        cpu_percent=cpu,
        rss_gib=rss,
        avail_gib=avail,
        total_gib=total,
        swap_gib=swap,
    )


def test_empty_returns_placeholders() -> None:
    assert render_resource_chart(
        [], metric="cpu", now=NOW, width=20, height_rows=6
    ).plain == ("no samples yet")
    assert (
        render_resource_chart(
            [], metric="memory", now=NOW, width=20, height_rows=6
        ).plain
        == "no samples yet"
    )
    assert render_activity_ribbon([], now=NOW, width=20).plain == "no activity yet"


def test_all_zero_renders_blank_chart_not_placeholder() -> None:
    samples = [
        _sample(NOW - i, cpu=0.0, rss=0.0, avail=16.0, total=16.0, swap=0.0)
        for i in range(5)
    ]
    cpu = render_resource_chart(samples, metric="cpu", now=NOW, width=10, height_rows=3)
    assert cpu.plain != "no samples yet"
    assert cpu.plain.replace(" ", "").replace("\n", "") == ""
    mem = render_resource_chart(
        samples, metric="memory", now=NOW, width=10, height_rows=3
    )
    # RSS 0 is a known reading so it marks the bottom row; unknowns stay blank.
    assert "●" in mem.plain
    assert "█" not in mem.plain


def test_missing_stays_blank_never_zero() -> None:
    samples = [
        _sample(NOW - 119, cpu=50.0, rss=None),
        _sample(NOW - 1, cpu=50.0, rss=2.0),
    ]
    chart = render_resource_chart(
        samples, metric="cpu", now=NOW, width=10, height_rows=4
    )
    rows = chart.plain.split("\n")
    assert len(rows) == 4
    # Bottom rows have fill at both ends; middle bins are gaps (blank).
    assert rows[-1][0] == "█" and rows[-1][-1] == "█"
    assert rows[-1][5] == " "
    latest = latest_in_window(samples, NOW)
    assert latest is not None and latest.cpu_percent == 50.0
    assert not is_stale(samples, NOW)
    assert is_stale([_sample(NOW - 10)], NOW)


def test_fixed_cpu_scale_small_changes_do_not_expand() -> None:
    low = [_sample(NOW - i, cpu=10.0) for i in range(3)]
    high = [_sample(NOW - i, cpu=11.0) for i in range(3)]
    low_chart = render_resource_chart(
        low, metric="cpu", now=NOW, width=6, height_rows=6
    )
    high_chart = render_resource_chart(
        high, metric="cpu", now=NOW, width=6, height_rows=6
    )
    assert low_chart.plain == high_chart.plain
    full = render_resource_chart(
        [_sample(NOW, cpu=100.0)], metric="cpu", now=NOW, width=6, height_rows=6
    )
    # Full load fills every row; small load fills only the bottom row.
    assert full.plain.count("█") > low_chart.plain.count("█")
    assert low_chart.plain.count("█") > 0


def test_capacity_scale_constant_memory_is_stable() -> None:
    a = [_sample(NOW - i, rss=1.5, avail=8.0, total=16.0) for i in range(4)]
    b = [_sample(NOW - i, rss=1.52, avail=8.02, total=16.0) for i in range(4)]
    ca = render_resource_chart(a, metric="memory", now=NOW, width=8, height_rows=6)
    cb = render_resource_chart(b, metric="memory", now=NOW, width=8, height_rows=6)
    assert ca.plain == cb.plain


def test_time_gaps_leave_blank_columns() -> None:
    samples = [
        _sample(NOW - 119, cpu=80.0),
        _sample(NOW - 1, cpu=80.0),
    ]
    chart = render_resource_chart(
        samples, metric="cpu", now=NOW, width=12, height_rows=2
    )
    rows = chart.plain.split("\n")
    assert len(rows) == 2
    # Middle bins have no samples: every row is blank there.
    for row in rows:
        assert row[5] == " " and row[6] == " "
    bins = bin_samples(samples, now=NOW, width=12)
    assert bins[0] and not bins[6] and bins[-1]


def test_narrow_widths_and_bounds() -> None:
    for width in (1, 2, 5):
        cpu = render_resource_chart(
            [_sample(NOW, cpu=50.0)], metric="cpu", now=NOW, width=width, height_rows=2
        )
        assert len(cpu.plain.split("\n")[0]) == width
        ribbon = render_activity_ribbon([_sample(NOW)], now=NOW, width=width)
        assert len(ribbon.plain) == width
        axis = time_axis_labels(width)
        assert len(axis.plain) == width
    over = render_resource_chart(
        [_sample(NOW, cpu=250.0)], metric="cpu", now=NOW, width=4, height_rows=2
    )
    # One occupied bin fills both rows; the other bins stay blank (gaps).
    assert over.plain.count("█") == 2
    assert over.plain.split("\n")[0].count("█") == 1
    neg = render_resource_chart(
        [_sample(NOW, cpu=-5.0)], metric="cpu", now=NOW, width=4, height_rows=2
    )
    assert "█" not in neg.plain
    assert (
        render_resource_chart([], metric="cpu", now=NOW, width=0, height_rows=2).plain
        == "—"
    )
    assert (
        render_resource_chart([], metric="cpu", now=NOW, width=4, height_rows=0).plain
        == "—"
    )


def test_pid_discontinuity_leaves_gaps() -> None:
    old = ProcessIdentity(pid=100, create_time=1.0)
    new = ProcessIdentity(pid=200, create_time=2.0)
    samples = [
        _sample(NOW - 100, rss=5.0, ident=old),
        _sample(NOW - 1, rss=1.0, ident=new),
    ]
    chart = render_resource_chart(
        samples, metric="memory", now=NOW, width=12, height_rows=4
    )
    rows = chart.plain.split("\n")
    # Gap bins between the two identities stay blank on every row.
    for row in rows:
        assert row[6] == " "
    bins = bin_samples(samples, now=NOW, width=12)
    assert bins[2] and not bins[6] and bins[-1]


def test_ribbon_active_over_idle_and_alignment() -> None:
    width = 12
    samples = [
        _sample(NOW - 110, op=OperationKind.IDLE),
        _sample(NOW - 109, op=OperationKind.CHATTING),
        _sample(NOW - 5, op=OperationKind.IDLE),
        _sample(NOW - 1, op=OperationKind.COMPARING),
    ]
    ribbon = render_activity_ribbon(samples, now=NOW, width=width)
    assert len(ribbon.plain) == width
    assert "G" in ribbon.plain and "C" in ribbon.plain
    cpu = render_resource_chart(
        samples, metric="cpu", now=NOW, width=width, height_rows=2
    )
    mem = render_resource_chart(
        samples, metric="memory", now=NOW, width=width, height_rows=2
    )
    assert len(cpu.plain.split("\n")[0]) == len(mem.plain.split("\n")[0]) == width
    axis = time_axis_labels(width)
    assert len(axis.plain) == width
    assert axis.plain.startswith("-120s")
    assert axis.plain.endswith("now")


def test_peaks_are_observed_samples() -> None:
    samples = [_sample(NOW - i, cpu=float(i)) for i in range(5)]
    assert cpu_peak(samples, NOW) == 4.0
    assert WINDOW_S == 120.0
    old = [_sample(NOW - 200, cpu=99.0)]
    assert cpu_peak(old, NOW) is None
    assert latest_in_window(old, NOW) is None
