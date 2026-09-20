"""Pure validation and conversion rules for the Installed estimate context."""

from __future__ import annotations

import pytest

from mlx_tui.model_context import (
    context_preset_for_tokens,
    context_tokens_from_preset,
    validate_custom_context,
)


@pytest.mark.parametrize(
    ("label", "tokens"),
    [
        ("2K", 2_048),
        ("4K", 4_096),
        ("8K", 8_192),
        ("16K", 16_384),
        ("32K", 32_768),
    ],
)
def test_preset_labels_use_1024_token_units(label: str, tokens: int) -> None:
    assert context_tokens_from_preset(label) == tokens
    assert context_preset_for_tokens(tokens) == label


def test_custom_and_non_preset_values_are_explicit() -> None:
    assert context_tokens_from_preset("Custom") is None
    assert context_preset_for_tokens(12_345) == "Custom"


@pytest.mark.parametrize("raw", ["1", " 8192 ", "32768"])
def test_custom_context_accepts_positive_integer_text(raw: str) -> None:
    result = validate_custom_context(raw)

    assert result.tokens == int(raw)
    assert result.error is None


@pytest.mark.parametrize("raw", ["", "   ", "tokens", "1.5", "0", "-1"])
def test_custom_context_rejects_blank_nonnumeric_zero_and_negative(raw: str) -> None:
    result = validate_custom_context(raw)

    assert result.tokens is None
    assert result.error
