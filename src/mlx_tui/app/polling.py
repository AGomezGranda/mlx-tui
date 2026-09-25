"""App endpoint polling/identity helpers (app-parameterized)."""

from __future__ import annotations

import json
import shlex
import threading
import time
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import psutil
from textual.css.query import NoMatches

from mlx_tui import process
from mlx_tui.comparison.contracts import ComparisonValidationError, parse_loopback_url
from mlx_tui.config import AppConfig
from mlx_tui.history.store import ResourceSample
from mlx_tui.metrics_pane import MetricsPane
from mlx_tui.models_pane import ModelsPane
from mlx_tui.operations import OperationKind
from mlx_tui.process import LOOPBACK_HOSTS, ProcessIdentity, memory_snapshot
from mlx_tui.status import (
    MemorySnapshot,
    ServerProbe,
    health_state_from_response,
    probe_from_response,
)
from mlx_tui.status_bar import StatusBar
from mlx_tui.text_screen import TextPreviewScreen


async def _fetch_probe(app: Any) -> ServerProbe:
    health_state = "red"
    try:
        health = await app._http.get("/health")
    except httpx.HTTPError as exc:
        if app.operations.current is not OperationKind.RESTARTING:
            app.log_error_once("poll-health", exc)
    else:
        try:
            health_body: object = health.json()
        except ValueError as exc:
            app.log_error_once("poll-health", exc)
            health_state = "amber"
        else:
            health_state = health_state_from_response(health.status_code, health_body)
            app.clear_error("poll-health")

    catalogue = ServerProbe(state="red", model_id=None, catalogue_state="red")
    try:
        response = await app._http.get("/v1/models")
    except httpx.HTTPError as exc:
        if app.operations.current is not OperationKind.RESTARTING:
            app.log_error_once("poll-catalogue", exc)
    else:
        try:
            catalogue_body: object = response.json()
        except ValueError as exc:
            app.log_error_once("poll-catalogue", exc)
            catalogue = ServerProbe(
                state="amber", model_id=None, catalogue_state="amber"
            )
        else:
            catalogue = probe_from_response(response.status_code, catalogue_body)
            app.clear_error("poll-catalogue")
    return ServerProbe(
        state=health_state,
        model_id=None,
        available_models=catalogue.available_models,
        catalogue_state=catalogue.catalogue_state,
    )


def _advance_observation(app: Any) -> int:
    app._observation_seq += 1
    return app._observation_seq


async def _poll(app: Any) -> None:  # noqa: PLR0912
    if (
        app._closing
        or app._poll_in_flight
        or app.operations.current in (OperationKind.COMPARING, OperationKind.RESTARTING)
    ):
        return
    app._poll_in_flight = True
    poll_sequence = app._advance_observation()
    prev_state = app.status_state
    prev_identity = app.server_identity
    try:
        probe = await app._fetch_probe()
        if (
            poll_sequence != app._observation_seq
            or app.operations.current is OperationKind.RESTARTING
        ):
            return
        app.status_state = probe.state
        snapshot = memory_snapshot()
        app.latest_avail_gib = snapshot.avail_gib
        ownership_state: str | None = None
        if app.managed_runtime is not None:
            if not app.managed_runtime.child_is_running():
                if app.managed_runtime.identity is not None:
                    ownership_state = "owned child exited"
                    app.status_state = "red"
                    probe = replace(probe, state="red")
                proc_ident = None
            elif app.managed_runtime.listener_matches_child():
                proc_ident = app.managed_runtime.identity
            else:
                ownership_state = "ownership mismatch"
                app.status_state = "amber"
                probe = replace(probe, state="amber")
                proc_ident = None
        elif app.host in LOOPBACK_HOSTS:
            proc_ident = process.find_server_process(app.host, app.port)
        else:
            proc_ident = None
        app.update_server_identity(
            probe,
            proc_ident,
            poll_sequence=poll_sequence,
            endpoint_changed=probe.state != prev_state,
        )
        app._last_ownership_state = ownership_state
        rss_gib: float | None = None
        if proc_ident is not None:
            try:
                rss_gib = psutil.Process(proc_ident.pid).memory_info().rss / 2**30
            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess,
            ):
                pass
        app._render_status(
            rss_gib=rss_gib,
            snapshot=snapshot,
            ownership_state=ownership_state,
        )
        app.refresh_models()
        app._refresh_metrics()
    except Exception as exc:
        # Unexpected worker fault: keep the last known state instead of
        # fabricating ordinary server downtime, and surface one bounded
        # diagnostic (repeats stay silent until recovery).
        app.status_state = prev_state
        app.server_identity = prev_identity
        app.log_error_once("poll", exc)
    finally:
        app._poll_in_flight = False


def update_server_identity(
    app: Any,
    probe: ServerProbe,
    process_identity: ProcessIdentity | None,
    *,
    poll_sequence: int | None = None,
    endpoint_changed: bool = False,
) -> bool:
    """Apply endpoint/process facts without deriving selection from catalogue."""
    if poll_sequence is not None and poll_sequence != app._observation_seq:
        return False
    pid = process_identity.pid if process_identity is not None else None
    ctime = process_identity.create_time if process_identity is not None else None
    previous = app.server_identity
    endpoint_changed = endpoint_changed or (
        previous.host,
        previous.port,
        previous.pid,
        previous.pid_create_time,
    ) != (app.host, app.port, pid, ctime)
    generation_state = "unknown" if endpoint_changed else previous.generation_state
    selected_model = previous.selected_model
    last_response_model = previous.last_response_model
    last_success_at = previous.last_success_at
    if probe.model_id is not None:
        selected_model = probe.model_id
        last_response_model = probe.model_id
        last_success_at = time.time()
        generation_state = "succeeded"
        if poll_sequence is None:
            app._advance_observation()
    app.server_identity = replace(
        app.server_identity,
        host=app.host,
        port=app.port,
        selected_model=selected_model,
        last_response_model=last_response_model,
        last_success_at=last_success_at,
        generation_state=generation_state,
        available_models=probe.available_models,
        catalogue_state=probe.catalogue_state,
        pid=pid,
        pid_create_time=ctime,
    )
    return True


def select_model(app: Any, model: str) -> None:
    """Set the explicit request target and invalidate current-success claims."""
    app._advance_observation()
    app.server_identity = replace(
        app.server_identity,
        selected_model=model,
        generation_state="unknown",
    )
    app.mark_active_profile_modified()
    pane = app._chat_pane_or_none()
    if pane is not None and not app._applying_profile:
        pane.mark_next_request_dirty()


def restore_request_state(app: Any, config: AppConfig, model: str | None) -> None:
    """Restore the exact pre-comparison request controls as unverified state."""
    app.config = config
    app._advance_observation()
    app.server_identity = replace(
        app.server_identity,
        selected_model=model,
        generation_state="unknown",
    )
    pane = app._chat_pane_or_none()
    app._applying_profile = True
    try:
        if pane is not None:
            pane.apply_config_params(config)
            pane.refresh_context_bar()
    finally:
        app._applying_profile = False
    app.refresh_models()
    if pane is not None:
        pane.mark_next_request_dirty()


def record_generation_success(
    app: Any, selected_model: str, response_model: str | None
) -> None:
    """Record dated response evidence; identity must match when supplied."""
    app._advance_observation()
    state = (
        "succeeded"
        if response_model is not None and response_model == selected_model
        else "failed"
    )
    app.server_identity = replace(
        app.server_identity,
        selected_model=selected_model,
        last_response_model=(
            response_model
            if response_model is not None
            else app.server_identity.last_response_model
        ),
        last_success_at=(
            time.time() if state == "succeeded" else app.server_identity.last_success_at
        ),
        generation_state=state,
    )


def record_generation_failure(app: Any, *, cancelled: bool = False) -> None:
    app._advance_observation()
    app.server_identity = replace(
        app.server_identity,
        generation_state="client_cancelled" if cancelled else "failed",
    )


def effective_model(app: Any) -> str | None:
    """Return the explicit request target, independent of endpoint liveness."""
    return app.server_identity.selected_model


def endpoint_preview_text(app: Any) -> str:
    """Build a fresh endpoint snapshot; never cached across changes."""
    rendered = f"[{app.host}]" if ":" in app.host else app.host
    base_v1 = f"http://{rendered}:{app.port}/v1"
    chat_raw = f"http://{rendered}:{app.port}/v1/chat/completions"
    try:
        chat_url = parse_loopback_url(chat_raw).url
    except ComparisonValidationError:
        chat_url = None
    selected = app.effective_model()
    identity = app.server_identity
    last_response = identity.last_response_model
    last_success_at = identity.last_success_at
    if last_success_at is not None:
        success_label = (
            datetime.fromtimestamp(last_success_at, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
    else:
        success_label = "— (never)"
    response_label = last_response if last_response is not None else "—"
    reach_word = {
        "green": "Reachable",
        "amber": "Unexpected",
        "red": "Offline",
        "starting": "Starting",
        "stopping": "Stopping",
        "failed": "Failed",
    }.get(app.status_state, "Unknown")
    lines = [
        "Endpoint — shared serving unqualified",
        f"base URL: {base_v1}",
        f"chat URL: {chat_raw}",
        (
            f"selected request model: {selected}"
            if selected is not None
            else "selected request model: — (select a model first)"
        ),
        f"last response model: {response_label}",
        f"last verified success: {success_label}",
        f"generation state: {identity.generation_state}",
        (
            f"reachability: {reach_word} "
            f"(health {app.status_state}; health only, not generation readiness)"
        ),
        "residency: unknown (health/catalogue never prove the loaded model)",
        (
            "catalogue: "
            f"{identity.catalogue_state} "
            f"{list(identity.available_models)} "
            "(catalogue never implies residency)"
        ),
        (
            "shared serving: blocked — stock mlx_lm.server 74e7cf9 accepts "
            "per-request model/draft_model/adapters and loads a changed tuple; "
            "wrong-target rejection and lifecycle drain are not enforceable. "
            "Qualification only."
        ),
        "shutdown (managed): stops its owned child",
        "shutdown (attach): never stops the operator server",
        f"current runtime mode: {app.config.runtime_mode}",
    ]
    if (
        selected is not None
        and last_response is not None
        and selected != last_response
        and last_success_at is not None
    ):
        lines.append(
            "note: mismatched response is not the verified success; "
            "the timestamp above is an older success, not this response time."
        )
    if selected is None:
        lines.append("select a model first — runnable examples omitted.")
    elif chat_url is None:
        lines.append(
            "unsupported scope: non-loopback endpoint — "
            "local-client recipes omitted (loopback only)."
        )
    else:
        payload = {
            "model": selected,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 32,
            "stream": False,
        }
        curl_argv = [
            "curl",
            "-sS",
            "--max-time",
            "30",
            chat_url,
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
        ]
        opencode_config = {
            "provider": {
                "mlx-tui-local": {
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": base_v1},
                    "models": {"local": {"name": selected}},
                }
            }
        }
        lines.extend(
            [
                "unqualified examples — no retained evidence; "
                "copying does not execute requests or write settings.",
                f"curl: {shlex.join(curl_argv)}",
                f"opencode config: {json.dumps(opencode_config, indent=2)}",
            ]
        )
    return "\n".join(lines)


def action_endpoint_info(app: Any) -> None:
    """Show a fresh endpoint snapshot for inspection and exact copy."""
    app.push_screen(
        TextPreviewScreen(
            app.endpoint_preview_text(),
            title="Endpoint — shared serving unqualified",
        )
    )


def refresh_models(app: Any) -> None:
    """Update runtime/loaded markers via the pane; a missing pane is fine."""
    try:
        app.query_one(ModelsPane).refresh_markers()
    except NoMatches:
        return


def _refresh_metrics(app: Any) -> None:
    try:
        app.query_one(MetricsPane).refresh_metrics()
    except NoMatches:
        pass


async def _classify_liveness(app: Any) -> str:
    return (await app._fetch_probe()).state


def _render_status(
    app: Any,
    *,
    rss_gib: float | None,
    snapshot: MemorySnapshot | None = None,
    ownership_state: str | None = None,
) -> None:
    if snapshot is None:
        snapshot = memory_snapshot()
    display_state = app.status_state
    if app.config.runtime_mode == "managed":
        if app.managed_runtime is not None and app.managed_runtime.shutting_down:
            display_state = "stopping"
        elif app.operations.current is OperationKind.RESTARTING:
            display_state = "starting"
        elif ownership_state == "owned child exited":
            display_state = "failed"
    try:
        app.query_one(StatusBar).show_status(
            state=display_state,
            selected_model=app.server_identity.selected_model,
            last_response_model=app.server_identity.last_response_model,
            generation_state=app.server_identity.generation_state,
            catalogue_disagrees=(
                app.server_identity.catalogue_state == "green"
                and app.server_identity.selected_model is not None
                and app.server_identity.selected_model
                not in app.server_identity.available_models
            ),
            port=app.port,
            can_start=bool(app.config.start_cmd),
            rss_gib=rss_gib,
            snapshot=snapshot,
            runtime_mode=app.config.runtime_mode,
            ownership_state=ownership_state,
        )
    except NoMatches:
        pass
    pane = app._chat_pane_or_none()
    if pane is not None:
        pane.refresh_zen_info()


RESOURCE_INTERVAL_S = 1.0
RESOURCE_BUFFER_LEN = 121


def _managed_sampler_identity(app: Any, managed: Any) -> ProcessIdentity | None:
    try:
        running = managed.child_is_running()
    except Exception:
        return None
    if not running:
        return None
    ident = managed.identity
    if ident is None:
        return None
    if getattr(app, "_last_ownership_state", None) == "ownership mismatch":
        return None
    return ident


def _attached_sampler_identity(app: Any) -> ProcessIdentity | None:
    if getattr(app, "host", None) not in LOOPBACK_HOSTS:
        return None
    sid = getattr(app, "server_identity", None)
    if sid is None:
        return None
    pid = getattr(sid, "pid", None)
    ctime = getattr(sid, "pid_create_time", None)
    if not isinstance(pid, int) or not isinstance(ctime, (int, float)):
        return None
    return ProcessIdentity(pid=pid, create_time=float(ctime))


def _trusted_sampler_identity(app: Any) -> ProcessIdentity | None:
    """Latest trusted identity without scans; unknown RSS stays None."""
    if app.operations.current is OperationKind.RESTARTING:
        return None
    managed = getattr(app, "managed_runtime", None)
    if managed is not None:
        return _managed_sampler_identity(app, managed)
    return _attached_sampler_identity(app)


def _capture_sampler_context(app: Any) -> tuple[OperationKind, ProcessIdentity | None]:
    """Snapshot operation plus trusted identity; call on the app thread."""
    return (app.operations.current, _trusted_sampler_identity(app))


def append_resource_sample(app: Any, sample: ResourceSample) -> None:
    """Append one sample on the app thread; never touches widgets."""
    if getattr(app, "_closing", False):
        return
    store = getattr(app, "resource_store", None)
    if store is None:
        return
    store.append(sample)


def _confirm_resource_sample(app: Any, sample: ResourceSample) -> None:
    append_resource_sample(app, sample)
    try:
        app.clear_error("resource-sample")
    except Exception:
        pass


def _record_resource_failure(app: Any, sample: ResourceSample, exc: Exception) -> None:
    append_resource_sample(app, sample)
    try:
        app.log_error_once("resource-sample", exc)
    except Exception:
        pass


def sample_and_store(app: Any) -> ResourceSample:
    """Synchronous capture/sample/append for tests and manual ticks."""
    op, ident = _capture_sampler_context(app)
    try:
        sample = process.sample_resources(ident, op)
    except Exception as exc:
        sample = ResourceSample(
            ts=time.monotonic(),
            operation=op,
            process_identity=ident,
            cpu_percent=None,
            rss_gib=None,
            avail_gib=None,
            total_gib=None,
            swap_gib=None,
        )
        _record_resource_failure(app, sample, exc)
        return sample
    if getattr(app, "_resource_first", False):
        sample = replace(sample, cpu_percent=None)
        app._resource_first = False
    _confirm_resource_sample(app, sample)
    return sample


def _run_resource_loop(app: Any, stop: threading.Event, interval: float) -> None:
    """Single long-lived worker; sequential ticks cannot overlap."""
    try:
        psutil.cpu_percent(interval=None)
    except Exception:
        pass
    while not stop.is_set():
        if getattr(app, "_closing", False):
            return
        try:
            op, ident = _capture_sampler_context(app)
        except Exception as exc:
            try:
                app.call_from_thread(
                    _record_resource_failure,
                    app,
                    ResourceSample(
                        ts=time.monotonic(),
                        operation=OperationKind.IDLE,
                        process_identity=None,
                        cpu_percent=None,
                        rss_gib=None,
                        avail_gib=None,
                        total_gib=None,
                        swap_gib=None,
                    ),
                    exc,
                )
            except Exception:
                return
            stop.wait(interval)
            continue
        try:
            sample = process.sample_resources(ident, op)
        except Exception as exc:
            unknown = ResourceSample(
                ts=time.monotonic(),
                operation=op,
                process_identity=ident,
                cpu_percent=None,
                rss_gib=None,
                avail_gib=None,
                total_gib=None,
                swap_gib=None,
            )
            try:
                app.call_from_thread(_record_resource_failure, app, unknown, exc)
            except Exception:
                return
            stop.wait(interval)
            continue
        if getattr(app, "_resource_first", False):
            sample = replace(sample, cpu_percent=None)
            app._resource_first = False
        try:
            app.call_from_thread(_confirm_resource_sample, app, sample)
        except Exception:
            return
        stop.wait(interval)


def start_resource_sampler(app: Any, interval: float = RESOURCE_INTERVAL_S) -> None:
    """Start the 1s sampler; idempotent and independent of health polling."""
    existing = getattr(app, "_resource_thread", None)
    if existing is not None and existing.is_alive():
        return
    if getattr(app, "resource_store", None) is None:
        app.resource_store = deque(maxlen=RESOURCE_BUFFER_LEN)
    stop = threading.Event()
    app._resource_stop = stop
    app._resource_first = True
    thread = threading.Thread(
        target=_run_resource_loop,
        args=(app, stop, interval),
        name="resource-sampler",
        daemon=True,
    )
    app._resource_thread = thread
    thread.start()


def stop_resource_sampler(app: Any, timeout: float = 2.0) -> None:
    """Signal the sampler and join; never updates widgets."""
    stop = getattr(app, "_resource_stop", None)
    thread = getattr(app, "_resource_thread", None)
    if stop is not None:
        stop.set()
    if thread is not None:
        try:
            thread.join(timeout=timeout)
        except Exception:
            pass
    app._resource_thread = None
    app._resource_stop = None
