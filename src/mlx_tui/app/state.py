"""Endpoint-derived server identity + effective model."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from mlx_tui.process import ProcessIdentity
from mlx_tui.status import ServerProbe

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


def update_server_identity(
    app: MlxTuiApp,
    probe: ServerProbe,
    process_identity: ProcessIdentity | None,
) -> None:
    """Replace the endpoint snapshot; stale models never survive a new probe."""
    model = probe.model_id if probe.state == "green" else None
    pid = process_identity.pid if process_identity is not None else None
    ctime = process_identity.create_time if process_identity is not None else None
    app.server_identity = dataclasses.replace(
        app.server_identity,
        host=app.host,
        port=app.port,
        model_id=model,
        pid=pid,
        pid_create_time=ctime,
    )


def effective_model(app: MlxTuiApp) -> str | None:
    """Only the model in the latest green endpoint probe."""
    if app.status_state != "green":
        return None
    return app.server_identity.model_id
