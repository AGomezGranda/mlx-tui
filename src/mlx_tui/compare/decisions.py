"""Compare decision-transaction helpers (pane-parameterized)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.css.query import NoMatches
from textual.widgets import (
    Input,
    TabbedContent,
)

from mlx_tui.comparison.contracts import DecisionKind
from mlx_tui.comparison.store import commit_choice
from mlx_tui.operations import OperationKind


def _decision_reason(pane: Any) -> str:
    try:
        return pane.query_one("#comparison-reason", Input).value
    except NoMatches:
        return ""


def _commit_decision(
    pane: Any, decision: DecisionKind, slot: int | None = None
) -> None:
    if pane._is_busy():
        pane._set_text(
            "#comparison-saved",
            "decision unavailable while another operation runs",
            "yellow",
        )
        return
    result = pane.tui.last_comparison
    if result is None or result.status != "completed":
        pane._set_text(
            "#comparison-saved", "no completed comparison to decide", "yellow"
        )
        return
    profile_id = (
        result.comparison.profiles[slot].profile.id if slot is not None else None
    )
    try:
        finalized, choice = commit_choice(
            result,
            decision,
            profile_id=profile_id,
            reason=pane._decision_reason(),
        )
    except (OSError, ValueError) as exc:
        pane._set_text("#comparison-saved", f"choice not saved: {exc}", "red")
        return
    pane.tui.last_comparison = finalized
    pane.tui.saved_choice = choice
    pane.tui.saved_choice_error = None
    managed_activation = False
    if decision == "keep" and profile_id is not None:
        try:
            pane.tui.apply_coding_profile(
                profile_id, expected_fingerprint=choice.profile_fingerprint
            )
        except (OSError, ValueError) as exc:
            pane._render_result(finalized)
            pane._set_text(
                "#comparison-saved",
                f"Choice saved; profile not applied: {exc}",
                "red",
            )
            return
        if pane.tui.config.runtime_mode == "managed":
            managed_activation = True
            pane._activate_managed_profile(profile_id)
    pane._render_result(finalized)
    if managed_activation:
        pane._set_text("#comparison-saved", "choice saved; activating managed profile")


def _keep_a(pane: Any) -> None:
    pane._commit_decision("keep", 0)


def _keep_b(pane: Any) -> None:
    pane._commit_decision("keep", 1)


def _retain(pane: Any) -> None:
    pane._commit_decision("retain")


def _reject(pane: Any) -> None:
    pane._commit_decision("reject")


def _use_saved(pane: Any) -> None:
    if pane._is_busy():
        pane._set_text(
            "#comparison-saved",
            "saved profile unavailable while another operation runs",
            "yellow",
        )
        return
    choice = pane.tui.saved_choice
    if choice is None or choice.decision != "keep" or choice.profile_id is None:
        pane._set_text(
            "#comparison-saved", "saved choice has no profile to apply", "yellow"
        )
        return
    try:
        pane.tui.apply_coding_profile(
            choice.profile_id, expected_fingerprint=choice.profile_fingerprint
        )
    except (OSError, ValueError) as exc:
        pane._set_text("#comparison-saved", f"saved profile not applied: {exc}", "red")
        return
    if pane.tui.config.runtime_mode == "managed":
        pane._activate_managed_profile(choice.profile_id)
    pane.refresh_profile_state()


def _activate_managed_profile(pane: Any, profile_id: str) -> None:
    if not pane.tui.operations.try_acquire(OperationKind.RESTARTING):
        pane.app.call_from_thread(
            pane._set_text,
            "#comparison-saved",
            "Choice saved; activation blocked by another operation",
            "yellow",
        )
        return
    try:
        model = pane.tui.config.model
        if model is None:
            raise ValueError(f"managed profile {profile_id} has no snapshot")

        def _report(line: str) -> None:
            pane.app.call_from_thread(pane.tui.log_app, f"[managed] {line}")

        pane.tui.start_managed(
            Path(model),
            on_line=_report,
        )
    except Exception as exc:
        pane.app.call_from_thread(
            pane._set_text,
            "#comparison-saved",
            f"Choice saved; activation failed: {exc}",
            "red",
        )
    else:
        pane.app.call_from_thread(
            pane._set_text,
            "#comparison-saved",
            "choice saved; managed profile ready",
        )
    finally:
        pane.app.call_from_thread(pane.tui.operations.release, OperationKind.RESTARTING)
        pane.app.call_from_thread(pane.tui.set_operation_ui, False)


def _go_chat(pane: Any) -> None:
    from mlx_tui.chat_ui.widgets import ChatInput  # noqa: PLC0415

    try:
        tabs = pane.tui.query_one(TabbedContent)
        tabs.active = "chat"
    except NoMatches:
        return
    try:
        composer = pane.tui.query_one("#chat-input", ChatInput)
        composer.focus()
    except NoMatches:
        pass
