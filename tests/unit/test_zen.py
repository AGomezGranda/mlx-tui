from __future__ import annotations

import pytest

from mlx_tui.chat_ui.pane import format_zen_stats


@pytest.mark.parametrize(
    ("completion_tokens", "total_s", "estimated", "expected"),
    [
        (248, 3.24, False, "248 out · 3.2s"),
        (248, 3.24, True, "≈248 out · 3.2s"),
        (None, None, False, ""),
        (0, 0.0, False, "0 out · 0.0s"),
        (42, None, False, "42 out"),
        (None, 3.24, False, "3.2s"),
    ],
)
def test_format_zen_stats(
    completion_tokens: int | None,
    total_s: float | None,
    estimated: bool,
    expected: str,
) -> None:
    assert format_zen_stats(completion_tokens, total_s, estimated=estimated) == expected
