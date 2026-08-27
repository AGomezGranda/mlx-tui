"""Poll tick: liveness, memory, model markers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import psutil

from mlx_tui import (
    process,  # module import so monkeypatch of process.find_server_pid propagates
)
from mlx_tui.app.state import effective_model
from mlx_tui.history.store import MemoryRecord
from mlx_tui.process import memory_snapshot
from mlx_tui.status import classify_liveness

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


async def classify_liveness_for(app: MlxTuiApp) -> str:
    try:
        resp = await app._http.get("/v1/models")
    except Exception:
        return "red"
    body: object = None
    try:
        body = resp.json()
    except ValueError:
        pass
    return classify_liveness(resp.status_code, body)


async def poll_tick(app: MlxTuiApp) -> None:
    state = await classify_liveness_for(app)
    app.status_state = state
    app.cold_tracker.observe(state)
    snapshot = memory_snapshot()
    app.latest_avail_gib = snapshot.avail_gib
    rss_gib: float | None = None
    model: str | None = None
    pid = process.find_server_pid(app.config.pidfile)
    if pid is not None:
        try:
            rss_gib = psutil.Process(pid).memory_info().rss / 2**30
        except psutil.NoSuchProcess:
            pass
        # Effective is authoritative for the marker/status text:
        # warm swaps shadow stale cmdline, while restarts keep both
        # in sync. Gate on pid so a down server (red, no pid) shows
        # "—" rather than a stale tracked value.
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
