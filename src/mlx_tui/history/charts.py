"""Pure two-minute resource charts and activity ribbon."""

from __future__ import annotations

import math
from typing import Literal

from rich.text import Text

from mlx_tui.history.store import ResourceSample
from mlx_tui.operations import OperationKind

WINDOW_S = 120.0
STALE_AFTER_S = 3.0

_RIBBON_GLYPHS: dict[OperationKind, tuple[str, str]] = {
    OperationKind.IDLE: ("·", "dim"),
    OperationKind.CHATTING: ("G", "bold cyan"),
    OperationKind.COMPARING: ("C", "bold yellow"),
    OperationKind.LOADING: ("L", "bold green"),
    OperationKind.RESTARTING: ("R", "bold red"),
    OperationKind.INSTALLING: ("I", "bold blue"),
    OperationKind.DOWNLOADING: ("D", "bold magenta"),
    OperationKind.DELETING: ("X", "bold red"),
}


def operation_label(kind: OperationKind) -> str:
    if kind is OperationKind.CHATTING:
        return "Generating"
    if kind is OperationKind.IDLE:
        return "Idle"
    return kind.value


def bin_samples(
    samples: list[ResourceSample], *, now: float, width: int
) -> list[list[ResourceSample]]:
    bins: list[list[ResourceSample]] = [[] for _ in range(max(0, width))]
    if width <= 0:
        return bins
    start = now - WINDOW_S
    for s in samples:
        if s.ts > now or s.ts < start:
            continue
        idx = int((s.ts - start) / WINDOW_S * width)
        bins[min(width - 1, max(0, idx))].append(s)
    return bins


def latest_in_window(
    samples: list[ResourceSample], now: float
) -> ResourceSample | None:
    best: ResourceSample | None = None
    for s in samples:
        if s.ts > now or s.ts < now - WINDOW_S:
            continue
        if best is None or s.ts >= best.ts:
            best = s
    return best


def is_stale(samples: list[ResourceSample], now: float) -> bool:
    latest = latest_in_window(samples, now)
    if latest is None:
        return True
    return (now - latest.ts) > STALE_AFTER_S


def _clamp_cpu(value: float) -> float:
    return min(100.0, max(0.0, value))


def _clamp_mem(value: float, capacity: float) -> float:
    return min(capacity, max(0.0, value))


def cpu_peak(samples: list[ResourceSample], now: float) -> float | None:
    peak: float | None = None
    start = now - WINDOW_S
    for s in samples:
        if s.ts > now or s.ts < start or s.cpu_percent is None:
            continue
        v = _clamp_cpu(s.cpu_percent)
        peak = v if peak is None else max(peak, v)
    return peak


def _capacity_gib(samples: list[ResourceSample], now: float) -> float | None:
    best: ResourceSample | None = None
    start = now - WINDOW_S
    for s in samples:
        if s.ts > now or s.ts < start or s.total_gib is None:
            continue
        if best is None or s.ts >= best.ts:
            best = s
    if best is not None and best.total_gib and best.total_gib > 0:
        return best.total_gib
    return None


def _filled_rows(value: float, scale: float, height_rows: int) -> int:
    if value <= 0 or scale <= 0:
        return 0
    return min(height_rows, max(1, math.ceil(value / scale * height_rows)))


def _cpu_bin_values(bins: list[list[ResourceSample]]) -> list[float | None]:
    vals: list[float | None] = []
    for b in bins:
        known = [s.cpu_percent for s in b if s.cpu_percent is not None]
        if not known:
            vals.append(None)
            continue
        vals.append(_clamp_cpu(max(known)))
    return vals


def _render_cpu_plot(vals: list[float | None], height_rows: int) -> Text:
    text = Text()
    for r in range(height_rows):
        threshold = height_rows - r
        for v in vals:
            if v is None:
                text.append(" ")
            elif _filled_rows(v, 100.0, height_rows) >= threshold:
                text.append("█", style="cyan")
            else:
                text.append(" ")
        if r < height_rows - 1:
            text.append("\n")
    return text


def _mem_bin_values(
    bins: list[list[ResourceSample]], capacity: float
) -> tuple[list[float | None], list[float | None]]:
    sys_vals: list[float | None] = []
    rss_vals: list[float | None] = []
    for b in bins:
        sys_known = [
            s.total_gib - s.avail_gib
            for s in b
            if s.total_gib is not None and s.avail_gib is not None
        ]
        rss_known = [s.rss_gib for s in b if s.rss_gib is not None]
        if sys_known:
            sys_vals.append(_clamp_mem(max(sys_known), capacity))
        else:
            sys_vals.append(None)
        if rss_known:
            rss_vals.append(_clamp_mem(max(rss_known), capacity))
        else:
            rss_vals.append(None)
    return sys_vals, rss_vals


def _render_mem_plot(
    sys_vals: list[float | None],
    rss_vals: list[float | None],
    capacity: float,
    height_rows: int,
) -> Text:
    text = Text()
    width = len(sys_vals)
    for r in range(height_rows):
        threshold = height_rows - r
        for c in range(width):
            rss = rss_vals[c]
            rss_row = _rss_row(rss, capacity, height_rows)
            if rss_row is not None and r == rss_row:
                # ponytail: RSS marker overlays fill; separate trace stays readable.
                text.append("●", style="bold yellow")
            elif _is_sys_filled(sys_vals[c], capacity, height_rows, threshold):
                text.append("█", style="magenta")
            else:
                text.append(" ")
        if r < height_rows - 1:
            text.append("\n")
    return text


def _rss_row(rss: float | None, capacity: float, height_rows: int) -> int | None:
    if rss is None:
        return None
    if rss <= 0:
        return height_rows - 1
    return height_rows - _filled_rows(rss, capacity, height_rows)


def _is_sys_filled(
    value: float | None, capacity: float, height_rows: int, threshold: int
) -> bool:
    if value is None:
        return False
    return _filled_rows(value, capacity, height_rows) >= threshold


def render_resource_chart(
    samples: list[ResourceSample],
    *,
    metric: Literal["cpu", "memory"],
    now: float,
    width: int,
    height_rows: int,
) -> Text:
    if width <= 0 or height_rows <= 0:
        return Text("—", style="dim")
    bins = bin_samples(samples, now=now, width=width)
    if all(not b for b in bins):
        return Text("no samples yet", style="dim")
    if metric == "cpu":
        return _render_cpu_plot(_cpu_bin_values(bins), height_rows)
    capacity = _capacity_gib(samples, now)
    if capacity is None:
        return Text("capacity unknown —", style="dim")
    sys_vals, rss_vals = _mem_bin_values(bins, capacity)
    if all(v is None for v in sys_vals) and all(v is None for v in rss_vals):
        return Text("no samples yet", style="dim")
    return _render_mem_plot(sys_vals, rss_vals, capacity, height_rows)


def _latest_by_ts(samples: list[ResourceSample]) -> ResourceSample:
    best = samples[0]
    for s in samples[1:]:
        if s.ts >= best.ts:
            best = s
    return best


def render_activity_ribbon(
    samples: list[ResourceSample], *, now: float, width: int
) -> Text:
    if width <= 0:
        return Text("—", style="dim")
    bins = bin_samples(samples, now=now, width=width)
    if all(not b for b in bins):
        return Text("no activity yet", style="dim")
    text = Text()
    for b in bins:
        if not b:
            text.append(" ")
            continue
        active = [s for s in b if s.operation is not OperationKind.IDLE]
        pick = _latest_by_ts(active) if active else _latest_by_ts(b)
        glyph, style = _RIBBON_GLYPHS.get(pick.operation, ("?", ""))
        text.append(glyph, style=style)
    return text


def time_axis_labels(width: int) -> Text:
    if width <= 0:
        return Text("", style="dim")
    cells = [" "] * width
    text = Text()
    spans: list[tuple[int, str]] = [(0, "-120s"), (max(0, width // 2 - 2), "-60s")]
    spans.append((max(0, width - 3), "now"))
    placed: list[tuple[int, int]] = []
    for start, label in spans:
        end = start + len(label)
        if end > width:
            continue
        if any(not (end <= s or start >= e) for s, e in placed):
            continue
        placed.append((start, end))
        for i, ch in enumerate(label):
            cells[start + i] = ch
    for ch in cells:
        text.append(ch, style="dim")
    return text
