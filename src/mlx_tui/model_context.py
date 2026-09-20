"""Validation and conversion rules for the Installed estimate context."""

from __future__ import annotations

from dataclasses import dataclass

CONTEXT_PRESETS: tuple[tuple[str, int], ...] = (
    ("2K", 2_048),
    ("4K", 4_096),
    ("8K", 8_192),
    ("16K", 16_384),
    ("32K", 32_768),
)
CUSTOM_CONTEXT_LABEL = "Custom"


def context_tokens_from_preset(label: str) -> int | None:
    """Return the token count for a preset label, or ``None`` for Custom."""
    return dict(CONTEXT_PRESETS).get(label)


def context_preset_for_tokens(tokens: int) -> str:
    """Return the matching preset label, using Custom for other values."""
    for label, preset_tokens in CONTEXT_PRESETS:
        if tokens == preset_tokens:
            return label
    return CUSTOM_CONTEXT_LABEL


@dataclass(frozen=True)
class ContextValidation:
    """Result of validating text entered for a custom context."""

    tokens: int | None
    error: str | None


def validate_custom_context(raw: str) -> ContextValidation:
    """Validate whitespace-tolerant ASCII positive-integer token input."""
    value = raw.strip()
    if not value:
        return ContextValidation(None, "Enter a context length in tokens")
    if not value.isascii() or not value.isdigit():
        return ContextValidation(None, "Context must be a positive integer")
    tokens = int(value)
    if tokens <= 0:
        return ContextValidation(None, "Context must be a positive integer")
    return ContextValidation(tokens, None)
