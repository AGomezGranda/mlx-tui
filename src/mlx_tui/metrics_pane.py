"""Metrics tab pane: sparkline + DataTable for chat + memory history."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast, override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
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

_MAX_RECENT = 64


def _per_col_styles(styles: list[str]) -> list[str]:
    """Collapse per-turn dim/\"\"/bold into per-braille-col styles (bold wins)."""
    out: list[str] = []
    for k in range((len(styles) + 1) // 2):
        pair = styles[2 * k : 2 * k + 2]
        if "bold" in pair:
            out.append("bold")
        elif "" in pair:
            out.append("")
        else:
            out.append("dim")
    return out


class MetricsPane(Vertical):
    """Owns two sparklines and the metrics DataTable."""

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
        table.add_column("tok/s", key="toks")
        table.add_column("TTFT", key="ttft")
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
        recent = (
            all_records[-_MAX_RECENT:]
            if len(all_records) > _MAX_RECENT
            else all_records
        )
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
        col_styles = _per_col_styles(styles)
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
        mem_records = self.tui.memory_store.series()
        mem_braille, mem_legend = render_memory_sparkline(mem_records)
        if not mem_braille:
            spark_mem.update(Text(mem_legend, style="dim"))
        else:
            spark_mem.update(Text(f"{mem_braille}\n{mem_legend}"))

    def _populate_metrics_table(
        self, table: DataTable[Any], recent: list[TurnRecord]
    ) -> None:
        table.clear()
        for r in recent:
            t_str = time.strftime("%H:%M:%S", time.localtime(r.ts))
            model_str = r.model or "—"
            toks = f"{r.tok_s:.1f}"
            ttft = f"{r.ttft_s:.2f}"
            ctx = str(r.ctx_len)
            prompt = str(r.prompt_tok)
            out = str(r.out_tok)
            suffix = ""
            style: str | None = None
            if r.cancelled:
                suffix = " · cancelled"
                style = "dim"
            elif r.cold:
                suffix = " · cold"
                style = "dim"
            out_cell = (
                Text(f"{out}{suffix}", style=style) if style else f"{out}{suffix}"
            )
            table.add_row(t_str, model_str, toks, ttft, ctx, prompt, out_cell)
