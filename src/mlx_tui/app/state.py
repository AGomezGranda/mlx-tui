"""App profile/comparison-selection state helpers (app-parameterized)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from textual.css.query import NoMatches

from mlx_tui.comparison import (
    choice_is_committed,
    choice_path,
    load_choice,
    load_comparison,
    verify_profile_snapshot,
)
from mlx_tui.models import resolve_cached_snapshot
from mlx_tui.profiles import ProfileEntry

_PROFILE_COUNT = 2


def swap_busy(app: Any) -> bool:
    return app.operations.is_swap_busy


def _load_saved_choice(app: Any) -> None:
    path = choice_path()
    if not path.exists():
        return
    try:
        choice = load_choice(path)
        if not choice_is_committed(choice):
            raise ValueError("saved choice is pending or its run does not match")
        app.last_comparison = load_comparison(choice.result_path)
    except (OSError, ValueError) as exc:
        app.saved_choice_error = str(exc)
        return
    app.saved_choice = choice


def profile_entry(app: Any, profile_id: str) -> ProfileEntry | None:
    return next(
        (entry for entry in app.profile_entries if entry.profile.id == profile_id),
        None,
    )


def selected_comparison_profiles(app: Any) -> tuple[ProfileEntry, ...]:
    entries = tuple(
        entry
        for profile_id in app.comparison_profile_ids
        if (entry := app.profile_entry(profile_id)) is not None
    )
    return entries


def update_comparison_candidate(app: Any, slot: int, profile_id: str) -> None:
    if app.profile_entry(profile_id) is None or slot not in {0, 1}:
        return
    ids = list(app.comparison_profile_ids)
    if len(ids) != _PROFILE_COUNT:
        return
    other = 1 - slot
    if ids[other] == profile_id:
        ids[other] = ids[slot]
    ids[slot] = profile_id
    app.comparison_profile_ids = tuple(ids)
    app.refresh_profile_views()


def refresh_profile_views(app: Any) -> None:
    from mlx_tui.compare.pane import ComparePane  # noqa: PLC0415

    for pane_type in (ComparePane,):
        try:
            pane = app.query_one(pane_type)
            pane.refresh_profile_state()
        except NoMatches:
            pass


def mark_active_profile_modified(app: Any) -> None:
    if app.active_profile_id is None or app._applying_profile:
        return
    app.active_profile_modified = True
    app.refresh_profile_views()


def apply_coding_profile(
    app: Any, profile_id: str, *, expected_fingerprint: str | None = None
) -> None:
    entry = app.profile_entry(profile_id)
    if entry is None:
        raise ValueError(f"unknown coding profile: {profile_id}")
    profile = entry.profile
    if expected_fingerprint is not None and profile.fingerprint != expected_fingerprint:
        raise ValueError("saved profile no longer matches the packaged snapshot")
    snapshot = resolve_cached_snapshot(profile.repo_id, profile.revision)
    verify_profile_snapshot(entry, snapshot)
    app._applying_profile = True
    try:
        app.config = replace(
            app.config,
            model=str(snapshot),
            temperature=profile.temperature,
            top_p=profile.top_p,
            max_tokens=profile.max_tokens,
            seed=profile.seed,
            enable_thinking=profile.enable_thinking,
            system=profile.system or None,
            max_ctx=profile.max_ctx,
        )
        app.select_model(str(snapshot))
        pane = app._chat_pane_or_none()
        if pane is not None:
            pane.apply_config_params(app.config)
            pane.refresh_context_bar()
        app.active_profile_id = profile.id
        app.active_profile_modified = False
    finally:
        app._applying_profile = False
    app.refresh_models()
    app.refresh_profile_views()
    pane = app._chat_pane_or_none()
    if pane is not None:
        pane.mark_next_request_dirty()
    app.log_app(
        (
            f"applied {profile.id}; managed activation is ready"
            if app.config.runtime_mode == "managed"
            else f"applied {profile.id}; launch/restart remains operator-managed"
        ),
        "yellow",
    )
