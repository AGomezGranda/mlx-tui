"""Compare build/preflight/run workflow helpers (pane-parameterized)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, cast

from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Select,
)

import mlx_tui.compare.render as _compare_render
from mlx_tui import process
from mlx_tui.compare.presenter import (
    trial_detail_text,
)
from mlx_tui.comparison.contracts import (
    _PROFILE_COUNT,
    ComparisonInput,
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonValidationError,
    JSONValue,
    parse_loopback_url,
)
from mlx_tui.comparison.runner import (
    run_comparison,
    verify_profile_snapshot,
)
from mlx_tui.comparison.store import comparison_dir, load_comparison
from mlx_tui.config import AppConfig
from mlx_tui.models import resolve_cached_snapshot
from mlx_tui.operations import OperationKind
from mlx_tui.params import ParamsPane
from mlx_tui.search_screen import SearchScreen


def _candidate_changed(pane: Any, event: Select.Changed) -> None:
    if not isinstance(event.value, str):
        return
    slot = 0 if event.select.id == "comparison-profile-a" else 1
    pane._preflight_input = None
    pane.tui.update_comparison_candidate(slot, event.value)
    _compare_render._update_controls(pane)


def _setup_input_changed(pane: Any) -> None:
    pane._preflight_input = None
    pane.refresh_profile_state()


def _operator_value(pane: Any, selector: str) -> dict[str, JSONValue]:
    value = pane.query_one(selector, Input).value.strip()
    return {"operator": value or "unknown"}


def _build_comparison_input(pane: Any) -> ComparisonInput:
    entries = pane.tui.selected_comparison_profiles()
    if len(entries) != _PROFILE_COUNT or entries[0].profile.id == entries[1].profile.id:
        raise ComparisonValidationError("select two distinct coding profiles")
    host = pane.tui.host
    rendered_host = f"[{host}]" if ":" in host else host
    raw_endpoint = f"http://{rendered_host}:{pane.tui.port}/v1/chat/completions"
    endpoint = parse_loopback_url(raw_endpoint).url
    snapshots = tuple(
        resolve_cached_snapshot(entry.profile.repo_id, entry.profile.revision)
        for entry in entries
    )
    hashes = tuple(
        verify_profile_snapshot(entry, snapshot)
        for entry, snapshot in zip(entries, snapshots, strict=True)
    )
    manager = pane.tui.managed_runtime
    if pane.tui.config.runtime_mode == "managed":
        if (
            manager is None
            or manager.identity is None
            or not manager.child_is_running()
            or not manager.listener_matches_child()
        ):
            raise ComparisonValidationError("managed server ownership is not verified")
        identity = manager.identity
        runtime_evidence = manager.install_evidence
        install_evidence: dict[str, JSONValue] = {
            "runtime_root": str(manager.root),
            "completion_marker": str(manager.root / ".mlx-tui-runtime.json"),
        }
        launch_evidence: dict[str, JSONValue] = {
            "owned": True,
            "argv": cast(list[JSONValue], list(manager.argv)),
            "host": manager.host,
            "port": manager.port,
        }
        provenance: dict[str, JSONValue] = {
            "source": "managed runtime inspection",
            "offline_hub": manager.environment.get("HF_HUB_OFFLINE") == "1",
            "python_no_user_site": manager.environment.get("PYTHONNOUSERSITE") == "1",
        }
        isolation_evidence: dict[str, JSONValue] = {"ownership": "TUI-retained child"}
    else:
        identity = process.find_server_process(pane.tui.host, pane.tui.port)
        if identity is None:
            raise ComparisonValidationError(
                "attached server process identity is unavailable"
            )
        runtime_evidence = _operator_value(pane, "#comparison-runtime")
        install_evidence = _operator_value(pane, "#comparison-install")
        launch_evidence = _operator_value(pane, "#comparison-launch")
        provenance = {"source": "operator preflight"}
        isolation_evidence = _operator_value(pane, "#comparison-isolation")
    try:
        tier = pane.query_one("#comparison-machine-tier", Input).value.strip()
    except NoMatches:
        tier = ""
    return ComparisonInput(
        endpoint=endpoint,
        profiles=entries,
        snapshot_paths=snapshots,
        verified_asset_hashes=hashes,
        runtime_evidence=runtime_evidence,
        install_evidence=install_evidence,
        launch_evidence=launch_evidence,
        provenance=provenance,
        process_identity=identity,
        isolation_evidence=isolation_evidence,
        machine_tier=tier or "unknown",
        operator_conditions=_operator_value(pane, "#comparison-conditions"),
        profile_order=(entries[0].profile.id, entries[1].profile.id),
        result_path=comparison_dir() / f"{uuid.uuid4()}.json",
    )


def _preflight(pane: Any) -> None:
    if _compare_render._is_busy(pane):
        _compare_render._set_text(
            pane,
            "#comparison-progress",
            "comparison or another operation is already running",
            "yellow",
        )
        return
    catalogue_error = pane.tui.profile_error
    if catalogue_error is not None:
        _compare_render._set_text(
            pane,
            "#comparison-progress",
            f"catalogue unavailable: {catalogue_error}",
            "red",
        )
        return
    try:
        pane._preflight_input = _build_comparison_input(pane)
    except (OSError, ValueError) as exc:
        pane._preflight_input = None
        _compare_render._set_text(
            pane, "#comparison-progress", f"readiness failed: {exc}", "red"
        )
        _compare_render._update_controls(pane)
        return
    _compare_render._set_text(
        pane,
        "#comparison-progress",
        (
            "ready · managed child identity retained; "
            "readiness does not prove residency"
            if pane.tui.config.runtime_mode == "managed"
            else "ready · attached server remains operator-managed; "
            "readiness is not proof the runtime applied all settings"
        ),
    )
    _compare_render._update_controls(pane)


def _start_comparison(pane: Any) -> None:
    if _compare_render._is_busy(pane):
        _compare_render._set_text(
            pane,
            "#comparison-progress",
            "comparison or another operation is already running",
            "yellow",
        )
        return
    if pane._preflight_input is None:
        _compare_render._set_text(
            pane, "#comparison-progress", "run Check readiness first", "yellow"
        )
        return
    if not pane.tui.operations.try_acquire(OperationKind.COMPARING):
        pane.tui.log_app("operation already in progress", "yellow")
        return
    try:
        comparison_input = _build_comparison_input(pane)
        params = pane.tui.query_one(ParamsPane).read_values()
        previous_config = replace(
            pane.tui.config,
            temperature=params[0],
            top_p=params[1],
            max_tokens=params[2],
        )
        previous_model = pane.tui.effective_model()
        pane._cancel_requested = False
        pane._run_started = False
        pane._comparison_active = True
        pane._selected_trial = None
        _compare_render._clear_result_display(pane)
        if pane.query_one("#comparison-run", Button).has_focus:
            pane.focus()
        pane.tui.set_operation_ui(True)
        _compare_render._update_controls(pane)
        pane._comparison_worker = pane._run_comparison(
            comparison_input, previous_config, previous_model
        )
    except Exception as exc:
        pane._comparison_active = False
        pane.tui.operations.release(OperationKind.COMPARING)
        pane.tui.set_operation_ui(False)
        _compare_render._set_text(
            pane, "#comparison-progress", f"readiness changed: {exc}", "red"
        )
        _compare_render._update_controls(pane)


def _on_progress(pane: Any, result: ComparisonResult) -> None:
    pane.tui.last_comparison = result
    _compare_render._render_result(pane, result)


async def _run_comparison(  # noqa: PLR0912, PLR0915
    pane: Any,
    comparison_input: ComparisonInput,
    previous_config: AppConfig,
    previous_model: str | None,
) -> None:
    import mlx_tui.app.polling as _app_polling  # noqa: PLC0415

    pane._run_started = True
    try:
        task = asyncio.create_task(
            run_comparison(comparison_input, on_progress=partial(_on_progress, pane))
        )
        pane._comparison_task = task
        await asyncio.sleep(0)
        if pane._cancel_requested:
            task.cancel()
        result = await task
        pane.tui.last_comparison = result
        _on_progress(pane, result)
        if result.status == "failed":
            pane.tui.log_app(f"comparison failed: {result.error}", "red")
    except asyncio.CancelledError:
        try:
            pane.tui.last_comparison = load_comparison(
                comparison_input.result_path  # type: ignore[arg-type]
            )
            if pane.tui.last_comparison is not None:
                _compare_render._render_result(pane, pane.tui.last_comparison)
        except ComparisonPersistenceError:
            pass
        _compare_render._set_text(
            pane,
            "#comparison-progress",
            "cancelled · partial checkpoint retained; engine state unknown",
            "yellow",
        )
    except Exception as exc:
        _compare_render._set_text(
            pane,
            "#comparison-progress",
            f"comparison failed: {exc.__class__.__name__}: {exc}",
            "red",
        )
        pane.tui.log_app(f"comparison failed: {exc}", "red")
    finally:
        try:
            if pane._cancel_requested and pane.tui.managed_runtime is not None:
                try:
                    await asyncio.to_thread(pane.tui.managed_runtime.stop)
                except Exception as exc:
                    pane.tui.log_app(f"managed shutdown failed: {exc}", "red")
        finally:
            pane._comparison_task = None
            pane._comparison_worker = None
            pane._run_started = False
            pane._comparison_active = False
            pane._preflight_input = None
            try:
                try:
                    pane.tui.restore_request_state(previous_config, previous_model)
                except Exception as exc:
                    pane.tui.log_app(f"request state restoration failed: {exc}", "red")
            finally:
                pane.tui.operations.release(OperationKind.COMPARING)
                pane.tui.set_operation_ui(False)
        await _app_polling._poll(pane.tui)
        pane.refresh_profile_state()
        if pane.tui.last_comparison is not None:
            _compare_render._render_result(pane, pane.tui.last_comparison)
        _compare_render._update_controls(pane)


def _download(pane: Any) -> None:
    if _compare_render._is_busy(pane):
        _compare_render._set_text(
            pane,
            "#comparison-progress",
            "download unavailable while another operation runs",
            "yellow",
        )
        return
    pane._preflight_input = None
    candidates = tuple(
        (entry.profile.repo_id, entry.profile.revision)
        for entry in pane.tui.selected_comparison_profiles()
    )
    _compare_render._update_controls(pane)
    pane.app.push_screen(SearchScreen(candidates))


async def _restart(pane: Any) -> None:
    if _compare_render._is_busy(pane):
        _compare_render._set_text(
            pane,
            "#comparison-progress",
            "server start unavailable while another operation runs",
            "yellow",
        )
        return
    pane._preflight_input = None
    _compare_render._update_controls(pane)
    await pane.tui.action_cold_start()
    _compare_render._update_controls(pane)


def _open_result(pane: Any) -> None:
    if _compare_render._is_busy(pane):
        _compare_render._set_text(
            pane,
            "#comparison-open-status",
            "result opening unavailable while another operation runs",
            "yellow",
        )
        return
    try:
        raw = pane.query_one("#comparison-open-path", Input).value.strip()
    except NoMatches:
        return
    if not raw:
        _compare_render._set_text(
            pane, "#comparison-open-status", "enter a result path to open", "yellow"
        )
        return
    opened_path = Path(raw).expanduser()
    try:
        loaded = load_comparison(opened_path)
    except (OSError, ValueError) as exc:
        _compare_render._set_text(
            pane,
            "#comparison-open-status",
            f"could not open result: {exc}",
            "red",
        )
        return
    authoritative = replace(
        loaded,
        comparison=replace(loaded.comparison, result_path=opened_path),
    )
    pane.tui.last_comparison = authoritative
    pane._selected_trial = None
    _compare_render._render_result(pane, authoritative)
    _compare_render._set_text(
        pane,
        "#comparison-open-status",
        f"opened {loaded.status} result {loaded.run_id}; "
        "saved choice, setup, and active settings unchanged",
    )


def _trial_selected(pane: Any, event: DataTable.RowSelected) -> None:
    raw = event.row_key.value
    if raw is None:
        return
    key = raw
    if ":" not in key:
        return
    profile_id, _, repeat_text = key.rpartition(":")
    try:
        repeat_index = int(repeat_text)
    except ValueError:
        return
    if not profile_id:
        return
    pane._selected_trial = (profile_id, repeat_index)
    result = pane.tui.last_comparison
    if result is None:
        return
    _compare_render._set_rich_text(
        pane,
        "#comparison-trial-detail",
        trial_detail_text(result, profile_id, repeat_index),
    )
