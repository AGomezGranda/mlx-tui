"""Params sidebar clamp + config bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Input

from mlx_tui.config import AppConfig

if TYPE_CHECKING:
    from mlx_tui.chat_pane import ChatPane

_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_TOP_P = 1.0
_DEFAULT_MAX_TOKENS = 1024
_TEMP_MIN = 0.0
_TEMP_MAX = 2.0
_TOP_P_MIN = 0.0
_TOP_P_MAX = 1.0
_MAX_TOK_MIN = 1
_MAX_TOK_MAX = 16384


def parse_params(pane: ChatPane) -> tuple[float, float, int]:
    try:
        raw_map = {
            "temp": pane.query_one("#param-temp", Input).value.strip(),
            "top_p": pane.query_one("#param-top-p", Input).value.strip(),
            "max": pane.query_one("#param-max-tokens", Input).value.strip(),
        }
    except NoMatches:
        return (_DEFAULT_TEMPERATURE, _DEFAULT_TOP_P, _DEFAULT_MAX_TOKENS)
    specs: list[tuple[str, float | int, float | int, float | int, type]] = [
        ("temp", _DEFAULT_TEMPERATURE, _TEMP_MIN, _TEMP_MAX, float),
        ("top_p", _DEFAULT_TOP_P, _TOP_P_MIN, _TOP_P_MAX, float),
        ("max", _DEFAULT_MAX_TOKENS, _MAX_TOK_MIN, _MAX_TOK_MAX, int),
    ]
    out: list[float | int] = []
    for key, default, lo, hi, parser in specs:
        raw = raw_map[key]
        try:
            v: float | int = parser(raw) if raw else default  # type: ignore[operator]
        except ValueError:
            v = default
        if v < lo:
            v = lo
        elif v > hi:
            v = hi
        out.append(v)
    return (float(out[0]), float(out[1]), int(out[2]))


def apply_params_to_inputs(
    pane: ChatPane,
    temperature: float | None,
    top_p: float | None,
    max_tokens: int | None,
) -> None:
    try:
        if temperature is not None:
            pane.query_one("#param-temp", Input).value = str(temperature)
        if top_p is not None:
            pane.query_one("#param-top-p", Input).value = str(top_p)
        if max_tokens is not None:
            pane.query_one("#param-max-tokens", Input).value = str(max_tokens)
    except NoMatches:
        pass


def apply_config_params(pane: ChatPane, cfg: AppConfig) -> None:
    apply_params_to_inputs(pane, cfg.temperature, cfg.top_p, cfg.max_tokens)
    if cfg.system is not None:
        pane._system_prompt = cfg.system


def on_param_submitted(
    pane: ChatPane, event: object
) -> None:  # event type is Input.Submitted
    temp, top_p, max_tok = parse_params(pane)
    # rewrite clamped values back to inputs
    try:
        pane.query_one("#param-temp", Input).value = str(temp)
        pane.query_one("#param-top-p", Input).value = str(top_p)
        pane.query_one("#param-max-tokens", Input).value = str(max_tok)
    except NoMatches:
        pass
