"""Stop/start commands, warm-load probe, health waiting — no UI knowledge."""

from __future__ import annotations

import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from mlx_tui.status import ServerProbe, probe_from_response


@dataclass(frozen=True)
class WarmLoadResult:
    response_model: str | None


def build_start_command(start_cmd: str, model_id: str | None) -> str:
    """Inject the target model into a configured start command."""
    if "{model}" in start_cmd:
        return start_cmd.replace("{model}", model_id or "")
    if "--model" in start_cmd:
        return start_cmd
    if model_id is None:
        return start_cmd
    return f"{start_cmd} --model {shlex.quote(model_id)}"


def _pump(proc: subprocess.Popen[str], on_line: Callable[[str], None]) -> None:
    assert proc.stdout is not None  # guaranteed by stdout=PIPE
    for line in proc.stdout:
        stripped = line.rstrip("\n")
        if stripped:
            on_line(stripped)


def _popen(cmd: str, *, on_line: Callable[[str], None]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def run_command(
    cmd: str, *, on_line: Callable[[str], None], timeout_s: float = 30.0
) -> int:
    """Stream a command and raise ``TimeoutExpired`` when it exceeds its budget."""
    proc = _popen(cmd, on_line=on_line)
    pump = threading.Thread(
        target=_pump, args=(proc, on_line), daemon=True, name="command-output"
    )
    pump.start()
    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
        raise
    pump.join(timeout=1.0)
    return rc


def spawn_command(cmd: str, *, on_line: Callable[[str], None]) -> subprocess.Popen[str]:
    """Start a long-lived command (a server) without waiting for its exit."""
    proc = _popen(cmd, on_line=on_line)
    threading.Thread(
        target=lambda: _pump(proc, on_line), daemon=True, name="spawned-cmd-output"
    ).start()
    return proc


def spawn_with_grace(
    cmd: str,
    *,
    on_line: Callable[[str], None],
    grace_s: float,
    poll_s: float,
) -> tuple[subprocess.Popen[str], bool]:
    """Start a long-running server command, then watch a crash-grace window."""
    proc = spawn_command(cmd, on_line=on_line)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(poll_s)
    return proc, proc.poll() is None


def warm_load(url: str, repo_id: str, *, timeout_s: float) -> WarmLoadResult:
    """Synchronous one-token probe-load; raises on any non-2xx/odd body."""
    timeout = httpx.Timeout(connect=5.0, read=timeout_s, write=5.0, pool=5.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            url,
            json={
                "model": repo_id,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
        )
    response.raise_for_status()
    try:
        body: object = response.json()
    except ValueError as exc:
        raise RuntimeError("unexpected probe response") from exc
    if not isinstance(body, dict) or not body.get("choices"):
        raise RuntimeError("unexpected probe response")
    raw_model = body.get("model")
    response_model = raw_model if isinstance(raw_model, str) and raw_model else None
    return WarmLoadResult(response_model=response_model)


def wait_healthy(  # noqa: PLR0913
    url: str,
    *,
    target_model: str | None = None,
    is_running: Callable[[], bool] | None = None,
    timeout_s: float,
    on_tick: Callable[[int], None] | None = None,
) -> ServerProbe | None:
    """Poll ``GET /v1/models`` until green (and optionally model-matched)."""
    deadline = time.monotonic() + timeout_s
    with httpx.Client(timeout=0.5) as client:
        while True:
            elapsed = int(timeout_s - (deadline - time.monotonic()))
            if on_tick is not None:
                on_tick(elapsed)
            probe: ServerProbe | None = None
            try:
                resp = client.get(url)
                body: object = resp.json()
                probe = probe_from_response(resp.status_code, body)
            except (httpx.HTTPError, ValueError):
                pass
            if probe is not None and probe.state == "green":
                if target_model is None or probe.model_id == target_model:
                    return probe
            if is_running is not None and not is_running():
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(1.0)
