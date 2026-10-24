"""Parameter controls for the chat pane."""

from __future__ import annotations

import math
from typing import Any

from textual import on
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Collapsible, Input, Label

from mlx_tui.config import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    MAX_TOKENS_RANGE,
    TEMPERATURE_RANGE,
    TOP_P_RANGE,
    AppConfig,
)


def _parse_clamped(
    raw: str, default: float, lo: float, hi: float, ctor: Any = float
) -> Any:
    if not raw:
        return default
    try:
        value = ctor(raw)
    except ValueError:
        return default
    if ctor is float and not math.isfinite(value):
        return default
    return max(lo, min(hi, value))


class ParamsPane(Collapsible):
    """Owns chat parameter inputs and their normalization."""

    DEFAULT_CSS = """
    ParamsPane {
        height: auto;
        padding: 0;
        border: none;
    }
    ParamsPane > Contents {
        layout: horizontal;
        height: auto;
        padding: 1 0 0 0;
    }
    .param-field {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }
    .param-field Label {
        width: 100%;
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    .param-field Input {
        width: 100%;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("title", "Params")
        kwargs.setdefault("collapsed", True)
        kwargs.setdefault("id", "params-collapsible")
        super().__init__(
            Vertical(
                Label("Temperature · 0–2", markup=False),
                Input(
                    value=str(DEFAULT_TEMPERATURE),
                    id="param-temp",
                    tooltip="Creativity: 0 deterministic → 2 very random",
                ),
                classes="param-field",
            ),
            Vertical(
                Label("Top-p · 0–1", markup=False),
                Input(
                    value=str(DEFAULT_TOP_P),
                    id="param-top-p",
                    tooltip="Nucleus sampling: lower = more focused",
                ),
                classes="param-field",
            ),
            Vertical(
                Label("Max tokens · 1–16384", markup=False),
                Input(
                    value=str(DEFAULT_MAX_TOKENS),
                    id="param-max-tokens",
                    tooltip="Maximum reply length",
                ),
                classes="param-field",
            ),
            **kwargs,
        )

    def on_mount(self) -> None:
        self.refresh_summary()

    @on(Input.Changed)
    def refresh_summary(self) -> None:
        temp, top_p, max_tok = self.read_values()
        self.title = f"Params · temp {temp:g} · top-p {top_p:g} · max {max_tok}"
        marker = getattr(self.app, "mark_active_profile_modified", None)
        if marker is not None:
            marker()

    def read_values(self) -> tuple[float, float, int]:
        try:
            raw_temp = self.query_one("#param-temp", Input).value.strip()
            raw_top_p = self.query_one("#param-top-p", Input).value.strip()
            raw_max = self.query_one("#param-max-tokens", Input).value.strip()
        except NoMatches:
            return (DEFAULT_TEMPERATURE, DEFAULT_TOP_P, DEFAULT_MAX_TOKENS)
        return (
            _parse_clamped(raw_temp, DEFAULT_TEMPERATURE, *TEMPERATURE_RANGE),
            _parse_clamped(raw_top_p, DEFAULT_TOP_P, *TOP_P_RANGE),
            _parse_clamped(raw_max, DEFAULT_MAX_TOKENS, *MAX_TOKENS_RANGE, ctor=int),
        )

    def apply_values(
        self,
        temperature: float | None,
        top_p: float | None,
        max_tokens: int | None,
    ) -> None:
        try:
            if temperature is not None:
                self.query_one("#param-temp", Input).value = str(temperature)
            if top_p is not None:
                self.query_one("#param-top-p", Input).value = str(top_p)
            if max_tokens is not None:
                self.query_one("#param-max-tokens", Input).value = str(max_tokens)
        except NoMatches:
            pass
        self.refresh_summary()

    def apply_config(self, config: AppConfig) -> None:
        self.apply_values(
            config.temperature
            if config.temperature is not None
            else DEFAULT_TEMPERATURE,
            config.top_p if config.top_p is not None else DEFAULT_TOP_P,
            config.max_tokens if config.max_tokens is not None else DEFAULT_MAX_TOKENS,
        )

    @on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")
    def _on_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.apply_values(*self.read_values())
