"""Metrics tab pane: sparkline + DataTable for chat + memory history."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast, override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from mlx_tui.history.sparkline import (
    _shade_for_ctx,
    render_memory_sparkline,
    render_sparkline,
    sparkline_visible,
)
from mlx_tui.history.store import TurnRecord

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class MetricsPane(VerticalScroll):
    """Owns two sparklines and the metrics DataTable."""

    DEFAULT_CSS = """
    MetricsPane { height: 1fr; padding: 0 1; }
    #metrics-sparkline, #metrics-memory-sparkline {
        height: 3;
        border-bottom: solid $primary;
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
        yield Static("", id="metrics-sparkline")
        yield Static("", id="metrics-memory-sparkline")
        yield DataTable(id="metrics-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#metrics-table", DataTable)
        table.add_column("time", key="time")
        table.add_column("model", key="model")
        table.add_column("first", key="first")
        table.add_column("answer", key="answer")
        table.add_column("total", key="total")
        table.add_column("req", key="req")
        table.add_column("ctx", key="ctx")
        table.add_column("prompt", key="prompt")
        table.add_column("out", key="out")
        self.refresh_metrics()

    def refresh_metrics(self) -> None:
        try:
            table = self.query_one("#metrics-table", DataTable)
            spark_chat = self.query_one("#metrics-sparkline", Static)
            spark_mem = self.query_one("#metrics-memory-sparkline", Static)
        except NoMatches:
            return
        all_records = self.tui.history.all_records()
        recent = all_records[-64:]
        self._update_chat_sparkline(spark_chat, recent)
        self._update_memory_sparkline(spark_mem)
        self._populate_metrics_table(table, recent)

    def _update_chat_sparkline(
        self, spark_chat: Static, recent: list[TurnRecord]
    ) -> None:
        braille, legend = render_sparkline(recent)
        if not braille:
            spark_chat.update(Text(legend, style="dim"))
            return
        visible = sparkline_visible(recent)
        ctx_lens = [r.ctx_len for r in visible]
        styles = _shade_for_ctx(ctx_lens)
        col_styles: list[str] = []
        for k in range((len(styles) + 1) // 2):
            pair = styles[2 * k : 2 * k + 2]
            if "bold" in pair:
                col_styles.append("bold")
            elif "" in pair:
                col_styles.append("")
            else:
                col_styles.append("dim")
        lines = braille.split("\n")
        text = Text()
        for row_idx, line in enumerate(lines):
            for k, ch in enumerate(line):
                style = col_styles[k] if k < len(col_styles) else ""
                if ch == " ":
                    style = ""
                text.append(ch, style=style)
            if row_idx < len(lines) - 1:
                text.append("\n")
        text.append("\n")
        text.append(legend)
        spark_chat.update(text)

    def _update_memory_sparkline(self, spark_mem: Static) -> None:
        mem_records = list(self.tui.memory_store)
        mem_braille, mem_legend = render_memory_sparkline(mem_records)
        if not mem_braille:
            spark_mem.update(Text(mem_legend, style="dim"))
        else:
            spark_mem.update(Text(f"{mem_braille}\n{mem_legend}"))

    def _populate_metrics_table(
        self, table: DataTable[Any], recent: list[TurnRecord]
    ) -> None:
        def fmt(v: float | None, spec: str) -> str:
            return f"{v:{spec}}" if v is not None else "—"

        def fmt_tok(v: int | None, estimated: bool) -> str:
            if v is None:
                return "—"
            return f"{v}~" if estimated else str(v)

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
