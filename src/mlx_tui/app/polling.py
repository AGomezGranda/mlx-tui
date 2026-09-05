"""Poll tick: liveness, memory, model markers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import psutil

from mlx_tui import (
    process,  # module import so monkeypatch of process.find_server_process propagates
)
from mlx_tui.app.state import effective_model, update_server_identity
from mlx_tui.history.store import MemoryRecord
from mlx_tui.process import LOOPBACK_HOSTS, memory_snapshot
from mlx_tui.status import ServerProbe, probe_from_response

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


async def fetch_server_probe(app: MlxTuiApp) -> ServerProbe:
    try:
        resp = await app._http.get("/v1/models")
    except Exception:
        return ServerProbe(state="red", model_id=None)
    body: object = None
    try:
        body = resp.json()
    except ValueError:
        pass
    return probe_from_response(resp.status_code, body)


async def classify_liveness_for(app: MlxTuiApp) -> str:
    return (await fetch_server_probe(app)).state


async def poll_tick(app: MlxTuiApp) -> None:
    probe = await fetch_server_probe(app)
    app.status_state = probe.state
    app.cold_tracker.observe(probe.state)
    snapshot = memory_snapshot()
    app.latest_avail_gib = snapshot.avail_gib
    if app.host in LOOPBACK_HOSTS:
        proc_ident = process.find_server_process(app.host, app.port, app.config.pidfile)
    else:
        proc_ident = None
    update_server_identity(app, probe, proc_ident)
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
    model = effective_model(app)
    # render_status is delegated via app._render_status to avoid circular
    app._render_status(model=model, rss_gib=rss_gib, snapshot=snapshot)
    app.refresh_models()
    rec = MemoryRecord(
        ts=time.time(),
        model=model,
        rss_gib=rss_gib,
        avail_gib=snapshot.avail_gib,
        total_gib=snapshot.total_gib,
    )
    app.memory_store.add(rec)
    app._refresh_metrics()
