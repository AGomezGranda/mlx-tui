"""Swap state machine and health-wait timeout math (idea doc §v1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mlx_tui.models import ModelRow


@dataclass(frozen=True)
class BootPlan:
    """Everything one boot/restart worker run needs; config pre-checked by caller."""

    model_id: str | None
    size_on_disk: int
    stop_first: bool
    success_line: str


def health_timeout(size_on_disk: int) -> float:
    """Health-wait deadline: ~60s base plus margin per GiB of weights."""
    return 60.0 + 10.0 * (size_on_disk / 2**30)


SwapAction = Literal["warm", "restart", "cold", "refuse"]


def resolve_swap_action(
    status_state: str,
    swap_policy: str,
    has_start: bool,
    has_stop: bool,
) -> SwapAction:
    """Deterministic swap policy.

    Priority: amber refuses (proxy squatting); explicit "warm" requires green;
    explicit "restart" requires both commands; "auto" restarts when both
    commands exist, otherwise warms when green and cold-starts when red with
    a start_cmd.
    """
    if swap_policy not in ("auto", "warm", "restart"):
        swap_policy = "auto"
    if status_state == "amber":
        return "refuse"
    if swap_policy == "warm":
        return "warm" if status_state == "green" else "refuse"
    if swap_policy == "restart":
        return "restart" if (has_start and has_stop) else "refuse"
    # swap_policy == "auto"
    if has_start and has_stop:
        return "restart"
    if status_state == "green":
        return "warm"
    return "cold" if (status_state == "red" and has_start) else "refuse"


def boot_plan_for(row: ModelRow, *, stop_first: bool = True) -> BootPlan:
    return BootPlan(
        model_id=row.repo_id,
        size_on_disk=row.size_on_disk,
        stop_first=stop_first,
        success_line=f"✓ request succeeded for {row.repo_id}; residency unknown",
    )
