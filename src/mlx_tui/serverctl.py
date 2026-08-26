"""Stop/start commands, warm-load probe, health waiting — no UI knowledge."""

from __future__ import annotations

import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from mlx_tui.status import classify_liveness


def build_start_command(start_cmd: str, model_id: str | None) -> str:
    """Inject the target model into a configured start command."""
    if "{model}" in start_cmd:
        return start_cmd.replace("{model}", model_id or "")
    if "--model" in start_cmd:
        return start_cmd
    if model_id is None:
        return start_cmd
    return f"{start_cmd} --model {shlex.quote(model_id)}"


@dataclass(frozen=True)
class HealthWatch:
    """What counts as healthy, and whether the target can still get there."""

    target_model: str | None = None
    current_model: Callable[[], str | None] = lambda: None
    is_running: Callable[[], bool] | None = None


class ServerController:
    """Runs configured commands and probes the server; every callback injected."""

    def run_command(self, cmd: str, *, on_line: Callable[[str], None]) -> int:
        """Stream a free-form shell command's combined output line by line."""
        with subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as proc:
            assert proc.stdout is not None  # guaranteed by stdout=PIPE
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                if stripped:
                    on_line(stripped)
            return proc.wait()

    def spawn_command(
        self, cmd: str, *, on_line: Callable[[str], None]
    ) -> subprocess.Popen[str]:
        """Start a long-lived command (a server) without waiting for its exit.

        ``run_command`` blocks until the process ends, which for a serving
        ``start_cmd`` is never — it would freeze the swap worker forever.
        Output streams line by line on a daemon thread instead; the caller
        polls ``proc.poll()``/``returncode`` for liveness and exit codes.
        """
        proc: subprocess.Popen[str] = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        def pump() -> None:
            assert proc.stdout is not None  # guaranteed by stdout=PIPE
            for line in proc.stdout:
                stripped = line.rstrip("\n")
                if stripped:
                    on_line(stripped)

        threading.Thread(target=pump, daemon=True, name="spawned-cmd-output").start()
        return proc

    def spawn_with_grace(
        self,
        cmd: str,
        *,
        on_line: Callable[[str], None],
        grace_s: float,
        poll_s: float,
    ) -> tuple[subprocess.Popen[str], bool]:
        """Start a long-running server command, then watch a crash-grace window.

        Returns ``(proc, monitor)``. ``monitor`` is False when the process was
        already gone within ``grace_s`` — either it crashed (rc checked by the
        caller) or it detached itself (e.g. a trailing ``&``), in which case
        health waiting must run unmonitored.
        """
        proc = self.spawn_command(cmd, on_line=on_line)
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(poll_s)
        return proc, proc.poll() is None

    def warm_load(self, url: str, repo_id: str, *, timeout_s: float) -> None:
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

    def wait_healthy(
        self,
        url: str,
        watch: HealthWatch,
        *,
        timeout_s: float,
        on_tick: Callable[[int], None] | None = None,
    ) -> bool:
        """Poll ``GET /v1/models`` until green (and optionally model-matched).

        Returns whether the server turned healthy before ``timeout_s``.
        ``watch.is_running``, when given, reports liveness of the spawned
        start command: a dead process can never turn green, so the wait ends
        immediately instead of burning the whole deadline.
        """
        deadline = time.monotonic() + timeout_s
        with httpx.Client(timeout=0.5) as client:
            while True:
                elapsed = int(timeout_s - (deadline - time.monotonic()))
                if on_tick is not None:
                    on_tick(elapsed)
                green = False
                try:
                    resp = client.get(url)
                    body: object = resp.json()
                    green = classify_liveness(resp.status_code, body) == "green"
                except (httpx.HTTPError, ValueError):
                    pass
                matched = (
                    watch.target_model is None
                    or watch.current_model() == watch.target_model
                )
                if green and matched:
                    return True
                if watch.is_running is not None and not watch.is_running():
                    return False
                if time.monotonic() >= deadline:
                    return False
                time.sleep(1.0)
