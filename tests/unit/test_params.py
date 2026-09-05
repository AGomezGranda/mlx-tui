"""Headless tests for the ChatPane configuration bridge."""

from __future__ import annotations

from dataclasses import dataclass

from mlx_tui.chat_pane import params
from mlx_tui.config import AppConfig


@dataclass
class _Input:
    value: str = ""


class _Pane:
    def __init__(self) -> None:
        self.inputs = {
            "#param-temp": _Input("old temp"),
            "#param-top-p": _Input("old top-p"),
            "#param-max-tokens": _Input("old max"),
        }
        self._system_prompt = "old system"

    def query_one(self, selector: str, _widget_type: object) -> _Input:
        return self.inputs[selector]


def test_apply_config_params_writes_full_config() -> None:
    pane = _Pane()

    params.apply_config_params(
        pane,  # type: ignore[arg-type]
        AppConfig(temperature=0.2, top_p=0.6, max_tokens=512, system="Be concise."),
    )

    assert pane.inputs["#param-temp"].value == "0.2"
    assert pane.inputs["#param-top-p"].value == "0.6"
    assert pane.inputs["#param-max-tokens"].value == "512"
    assert pane._system_prompt == "Be concise."


def test_apply_config_params_clears_optional_values() -> None:
    pane = _Pane()

    params.apply_config_params(pane, AppConfig())  # type: ignore[arg-type]

    assert pane.inputs["#param-temp"].value == "0.7"
    assert pane.inputs["#param-top-p"].value == "1.0"
    assert pane.inputs["#param-max-tokens"].value == "1024"
    assert pane._system_prompt == ""


def test_apply_config_params_uses_parser_defaults() -> None:
    pane = _Pane()

    params.apply_config_params(
        pane,  # type: ignore[arg-type]
        AppConfig(temperature=None, top_p=None, max_tokens=None),
    )

    assert params.parse_params(pane) == (0.7, 1.0, 1024)  # type: ignore[arg-type]
