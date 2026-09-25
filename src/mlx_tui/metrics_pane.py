"""Metrics tab pane: live resource charts plus request history."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast, override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static, TabbedContent

from mlx_tui.history import charts as charts_mod
from mlx_tui.history.charts import (
    is_stale,
    latest_in_window,
    operation_label,
    render_activity_ribbon,
    render_resource_chart,
    time_axis_labels,
)
from mlx_tui.history.store import TurnRecord
from mlx_tui.operations import OperationKind

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

_GUTTER_W = 5
_TABLE_LIMIT = 64


def _fmt_gib(v: float | None) -> str:
    return f"{v:.1f} GiB" if v is not None else "—"


def _memory_labels(
    latest: Any, stale: bool
) -> tuple[str, str, str, str, str, str, float | None, str]:
    if latest is None:
        return ("—", "—", "—", "—", "—", "—", None, "no samples yet")
    avail = latest.avail_gib
    total = latest.total_gib
    rss = latest.rss_gib
    swap = latest.swap_gib
    used = total - avail if total is not None and avail is not None else None
    capacity = total
    suffix = " (stale)" if stale else ""
    state = "stale" if stale else "live"
    avail_s = (_fmt_gib(avail) + suffix) if avail is not None or stale else "—"
    total_s = (_fmt_gib(total) + suffix) if total is not None or stale else "—"
    rss_s = _fmt_gib(rss) if rss is not None else "—"
    swap_s = _fmt_gib(swap) if swap is not None else "—"
    used_s = _fmt_gib(used) if used is not None else "—"
    if stale:
        if rss_s != "—" and not rss_s.endswith(suffix):
            rss_s += suffix
        elif rss_s == "—":
            rss_s = "— (stale)"
        if swap_s != "—" and not swap_s.endswith(suffix):
            swap_s += suffix
        elif swap_s == "—":
            swap_s = "— (stale)"
        if used_s != "—" and not used_s.endswith(suffix):
            used_s += suffix
        elif used_s == "—":
            used_s = "— (stale)"
    cap_s = _fmt_gib(capacity) if capacity is not None else "—"
    if latest.cpu_percent is None and avail is None and rss is None:
        avail_s = total_s = rss_s = swap_s = used_s = "—"
        if stale:
            avail_s = total_s = rss_s = swap_s = used_s = "— (stale)"
    return (avail_s, total_s, rss_s, swap_s, used_s, cap_s, capacity, state)


class MetricsPane(VerticalScroll):
    """Live CPU/memory charts, activity ribbon, and request history."""

    DEFAULT_CSS = """
    MetricsPane { height: 1fr; padding: 0 1; }
    #metrics-context, #metrics-cpu, #metrics-memory, #metrics-activity {
        height: auto;
        padding: 0 1;
    }
    #metrics-table {
        height: 16;
    }
    """

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        yield Static("", id="metrics-context")
        yield Static("", id="metrics-cpu")
        yield Static("", id="metrics-memory")
        yield Static("", id="metrics-activity")
        yield DataTable(id="metrics-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#metrics-table", DataTable)
        table.add_column("Time", key="time")
        table.add_column("Model", key="model")
        table.add_column("First output s", key="first")
        table.add_column("Answer s", key="answer")
        table.add_column("Total s", key="total")
        table.add_column("Request tok/s", key="req")
        table.add_column("Context", key="ctx")
        table.add_column("Prompt", key="prompt")
        table.add_column("Output", key="out")
        self._last_table_key: tuple[TurnRecord, ...] | None = None
        self._last_records: list[TurnRecord] = []
        self.refresh_metrics()

    def on_resize(self, event: object) -> None:
        self.refresh_metrics()

    def _is_metrics_visible(self) -> bool:
        try:
            return self.tui.query_one(TabbedContent).active == "metrics"
        except Exception:
            return True

    def _plot_geometry(self) -> tuple[int, int]:
        content_w = 0
        try:
            content_w = self.size.width or 0
        except Exception:
            content_w = 0
        if not content_w:
            try:
                content_w = self.tui.size.width or 0
            except Exception:
                content_w = 0
        if not content_w:
            content_w = 80
        width = max(10, min(120, content_w - 10))
        height = 6
        try:
            h = self.tui.size.height or 0
            if h and h < 32:  # noqa: PLR2004
                height = 3
        except Exception:
            pass
        try:
            if self.tui.screen.has_class("compact"):
                height = 3
        except Exception:
            pass
        return width, height

    def refresh_metrics(self) -> None:
        try:
            table = self.query_one("#metrics-table", DataTable)
            ctx = self.query_one("#metrics-context", Static)
            cpu_w = self.query_one("#metrics-cpu", Static)
            mem_w = self.query_one("#metrics-memory", Static)
            act_w = self.query_one("#metrics-activity", Static)
        except NoMatches:
            return
        all_records = self.tui.history.all_records()
        recent = all_records[-_TABLE_LIMIT:]
        self._populate_metrics_table(table, recent)
        if not self._is_metrics_visible():
            return
        width, height = self._plot_geometry()
        now = time.monotonic()
        samples = list(getattr(self.tui, "resource_store", []))
        self._update_context(ctx)
        self._update_cpu(cpu_w, samples, now, width, height)
        self._update_memory(mem_w, samples, now, width, height)
        self._update_activity(act_w, samples, now, width)

    def _update_context(self, widget: Static) -> None:
        try:
            op = self.tui.operations.current
        except Exception:
            op = OperationKind.IDLE
        if op is OperationKind.IDLE:
            op_text = "Idle — no request from this app"
        else:
            op_text = operation_label(op)
        try:
            target = self.tui.server_identity.selected_model
        except Exception:
            target = None
        text = Text()
        text.append("Live · last 2 min · sampled 1s", style="bold")
        text.append(f" · {op_text}")
        text.append(
            f" · target {target or '—'} (selected, residency unknown)", style="dim"
        )
        widget.update(text)

    def _with_gutter(self, plot: Text, top: str, mid: str, bottom: str) -> Text:
        out = Text()
        lines = plot.plain.split("\n")
        # ponytail: chart Text carries styles per cell; rebuild with gutters.
        spans = list(plot._spans)  # type: ignore[attr-defined]  # noqa: SLF001
        # Map plain offset -> style by walking spans.
        offset_styles: list[str] = [""] * len(plot.plain)
        for span in spans:
            for i in range(span.start, min(span.end, len(offset_styles))):
                offset_styles[i] = str(span.style)
        # plot offsets skip newlines; track separately.
        plain_idx = 0
        for row_idx, line in enumerate(lines):
            if row_idx == 0:
                gutter = top
            elif row_idx == len(lines) // 2 and len(lines) > 1:
                gutter = mid
            elif row_idx == len(lines) - 1:
                gutter = bottom
            else:
                gutter = "    |"
            out.append(gutter, style="dim")
            for ch in line:
                # Find style at current plain offset (skip newlines in source).
                while plain_idx < len(plot.plain) and plot.plain[plain_idx] == "\n":
                    plain_idx += 1
                style = (
                    offset_styles[plain_idx] if plain_idx < len(offset_styles) else ""
                )
                out.append(ch, style=style or "")
                plain_idx += 1
            # Consume the newline in source offsets.
            while plain_idx < len(plot.plain) and plot.plain[plain_idx] == "\n":
                plain_idx += 1
                break
            if row_idx < len(lines) - 1:
                out.append("\n")
        return out

    def _tick_line(self, width: int) -> Text:
        out = Text(" " * _GUTTER_W, style="dim")
        out.append_text(time_axis_labels(width))
        return out

    def _update_cpu(
        self, widget: Static, samples: list[Any], now: float, width: int, height: int
    ) -> None:
        chart = render_resource_chart(
            samples, metric="cpu", now=now, width=width, height_rows=height
        )
        latest = latest_in_window(samples, now)
        peak = charts_mod.cpu_peak(samples, now)
        stale = is_stale(samples, now) if samples else True
        empty = latest is None and peak is None

        def fmt_cpu(v: float | None) -> str:
            return f"{v:.1f}%" if v is not None else "—"

        if empty:
            current_s = "—"
            peak_s = "—"
            state = "no samples yet"
        elif stale and latest is not None:
            age = max(0.0, now - latest.ts)
            cur = fmt_cpu(latest.cpu_percent)
            current_s = f"{cur} (stale {age:.0f}s ago)" if cur != "—" else "— (stale)"
            peak_s = fmt_cpu(peak)
            state = "stale"
        else:
            current_s = fmt_cpu(latest.cpu_percent) if latest else "—"  # type: ignore[union-attr]
            peak_s = fmt_cpu(peak)
            state = "live"
        header = Text()
        header.append("CPU — This Mac", style="bold")
        header.append(f" · current {current_s} · peak {peak_s} · scale 0–100%")
        if state != "live":
            header.append(f" · {state}", style="yellow")
        body = Text()
        body.append_text(header)
        body.append("\n")
        if chart.plain in ("no samples yet", "—"):
            body.append(chart)
        else:
            body.append_text(self._with_gutter(chart, "100%|", " 50%|", "  0%|"))
            body.append("\n")
            body.append_text(self._tick_line(width))
        body.append("\n")
        note = Text("CPU is This Mac CPU only, excludes GPU load", style="dim")
        body.append_text(note)
        widget.update(body)

    def _update_memory(
        self, widget: Static, samples: list[Any], now: float, width: int, height: int
    ) -> None:
        chart = render_resource_chart(
            samples, metric="memory", now=now, width=width, height_rows=height
        )
        latest = latest_in_window(samples, now)
        stale = is_stale(samples, now) if samples else True
        avail_s, total_s, rss_s, swap_s, used_s, cap_s, capacity, state = (
            _memory_labels(latest, stale)
        )
        header = Text()
        header.append("Memory", style="bold")
        header.append(
            f" · used {used_s} (total−avail) · avail {avail_s}/{total_s}"
            f" · RSS {rss_s} · swap {swap_s} · scale 0–{cap_s}"
        )
        if state != "live":
            header.append(f" · {state}", style="yellow")
        body = Text()
        body.append_text(header)
        body.append("\n")
        if chart.plain in ("no samples yet", "capacity unknown —", "—"):
            body.append(chart)
        else:
            cap = capacity or 0.0
            top = f"{cap:4.1f}|" if cap else "    |"
            mid = f"{cap / 2:4.1f}|" if cap else "    |"
            body.append_text(self._with_gutter(chart, top, mid, " 0.0|"))
            body.append("\n")
            body.append_text(self._tick_line(width))
        body.append("\n")
        legend = Text()
        legend.append("█", style="magenta")
        legend.append(" system used  ", style="dim")
        legend.append("●", style="bold yellow")
        legend.append(" server RSS (process-wide, not model memory)", style="dim")
        body.append_text(legend)
        widget.update(body)

    def _update_activity(
        self, widget: Static, samples: list[Any], now: float, width: int
    ) -> None:
        ribbon = render_activity_ribbon(samples, now=now, width=width)
        body = Text()
        body.append("App activity (sampled 1s)", style="bold")
        body.append("\n")
        if ribbon.plain in ("no activity yet", "—"):
            body.append(ribbon)
        else:
            row = Text(" " * _GUTTER_W, style="dim")
            row.append_text(ribbon)
            body.append_text(row)
            body.append("\n")
            body.append_text(self._tick_line(width))
        body.append("\n")
        legend = Text(
            "G Generating · C comparing · L loading · R restarting"
            " · I installing · D downloading · X deleting"
            " · · idle (no request from this app)"
            " · sub-second ops may not appear",
            style="dim",
        )
        body.append_text(legend)
        widget.update(body)

    def _populate_metrics_table(
        self, table: DataTable[Any], recent: list[TurnRecord]
    ) -> None:
        def fmt(v: float | None, spec: str) -> str:
            return f"{v:{spec}}" if v is not None else "—"

        def fmt_tok(v: int | None, estimated: bool) -> str:
            if v is None:
                return "—"
            return f"{v}~" if estimated else str(v)

        key = tuple(recent)
        if getattr(self, "_last_table_key", None) == key:
            return
        old_records: list[TurnRecord] = getattr(self, "_last_records", [])
        try:
            cur_row: int = table.cursor_row
        except Exception:
            cur_row = 0
        selected: TurnRecord | None = None
        if old_records and 0 <= cur_row < len(old_records):
            try:
                selected = old_records[cur_row]
            except Exception:
                selected = None
        table.clear()
        for r in recent:
            t_str = time.strftime("%H:%M:%S", time.localtime(r.ts))
            model_str = r.model or "—"
            first = fmt(r.first_output_s, ".2f")
            answer = fmt(r.answer_started_s, ".2f")
            total = fmt(r.total_s, ".2f")
            req = fmt(r.req_tok_s, ".1f")
            ctx = str(r.ctx_len)
            prompt = fmt_tok(r.prompt_tok, r.prompt_estimated)
            out = fmt_tok(r.out_tok, r.out_estimated)
            suffix = ""
            style: str | None = None
            if r.cancelled or r.outcome == "cancelled":
                suffix = " · cancelled"
                style = "dim"
            elif r.outcome != "success":
                suffix = f" · {r.outcome}"
                style = "dim"
            out_cell = (
                Text(f"{out}{suffix}", style=style) if style else f"{out}{suffix}"
            )
            table.add_row(
                t_str, model_str, first, answer, total, req, ctx, prompt, out_cell
            )
        self._last_table_key = key
        self._last_records = list(recent)
        if selected is not None and recent:
            try:
                idx = recent.index(selected)
                table.move_cursor(row=idx)
            except ValueError:
                try:
                    table.move_cursor(row=min(cur_row, max(0, len(recent) - 1)))
                except Exception:
                    pass
