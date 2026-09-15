"""Stop/start commands, warm-load probe, health waiting — no UI knowledge."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress

import httpx
import psutil

from mlx_tui.process import ProcessIdentity
from mlx_tui.status import ServerProbe, health_state_from_response, probe_from_response


def build_start_command(start_cmd: str, model_id: str | None) -> list[str]:  # noqa: PLR0912
    """Parse argv and inject the target model deterministically.

    Only exact ``--model value`` and ``--model=value`` options are managed;
    ``--model-path`` and similar names are left alone. ``{model}`` inside a
    parsed argument is literal data substitution, so spaces and shell
    metacharacters stay as one argument. Duplicates collapse to one target.
    """
    try:
        argv = shlex.split(start_cmd)
    except ValueError as exc:
        raise ValueError(f"invalid start_cmd: {exc}") from exc
    if not argv:
        raise ValueError("empty start_cmd")
    placeholder = any("{model}" in arg for arg in argv)
    if placeholder:
        if model_id is None:
            raise ValueError("start_cmd contains {model} but no model target")
        argv = [arg.replace("{model}", model_id) for arg in argv]
    positions: list[tuple[int, str]] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--model":
            if i + 1 >= len(argv):
                raise ValueError("dangling --model without value")
            positions.append((i, "sep"))
            i += 2
        elif arg.startswith("--model="):
            positions.append((i, "eq"))
            i += 1
        else:
            i += 1
    if model_id is not None:
        if positions:
            first_pos, first_kind = positions[0]
            if first_kind == "sep":
                argv[first_pos + 1] = model_id
            else:
                argv[first_pos] = f"--model={model_id}"
            for pos, kind in reversed(positions[1:]):
                if kind == "sep":
                    del argv[pos : pos + 2]
                else:
                    del argv[pos]
        elif not placeholder:
            argv = [*argv, "--model", model_id]
    return argv


def _pump(proc: subprocess.Popen[str], on_line: Callable[[str], None]) -> None:
    try:
        assert proc.stdout is not None  # guaranteed by stdout=PIPE
        for line in proc.stdout:
            stripped = line.rstrip("\n")
            if stripped:
                try:
                    on_line(stripped)
                except Exception:
                    continue
    except Exception:
        return
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass


def _descendant_identities(pid: int) -> list[ProcessIdentity]:
    try:
        return [
            ProcessIdentity(child.pid, child.create_time())
            for child in psutil.Process(pid).children(recursive=True)
        ]
    except (psutil.Error, OSError):
        return []


def _live_identity(identity: ProcessIdentity) -> psutil.Process | None:
    try:
        current = psutil.Process(identity.pid)
        if current.create_time() != identity.create_time:
            return None
        if current.status() == psutil.STATUS_ZOMBIE:
            return None
    except (psutil.Error, OSError):
        return None
    return current


def _terminate_known_processes(identities: list[ProcessIdentity]) -> None:
    active = [identity for identity in identities if _live_identity(identity)]
    for identity in active:
        current = _live_identity(identity)
        if current is not None:
            with suppress(psutil.Error, OSError):
                current.terminate()
    deadline = time.monotonic() + 5.0
    while active and time.monotonic() < deadline:
        active = [identity for identity in active if _live_identity(identity)]
        if active:
            time.sleep(0.05)
    for identity in active:
        current = _live_identity(identity)
        if current is not None:
            with suppress(psutil.Error, OSError):
                current.kill()


def terminate_failed_process(proc: subprocess.Popen[str] | None) -> None:  # noqa: PLR0911,PLR0912,PLR0915
    """Stop an owned failed-boot/stop-timeout process group; never a success."""
    if proc is None:
        return

    try:
        pid = proc.pid
    except Exception:
        return
    descendants = _descendant_identities(pid)
    owned_group = False
    pgid: int | None = None
    leader: ProcessIdentity | None = None
    if os.name == "posix":
        try:
            pgid = os.getpgid(pid)
        except Exception:
            pass
        try:
            leader = ProcessIdentity(pid, psutil.Process(pid).create_time())
            owned_group = pgid == pid
        except (psutil.Error, OSError):
            owned_group = False
        if (
            owned_group
            and pgid is not None
            and leader is not None
            and _live_identity(leader) is not None
        ):
            with suppress(OSError):
                os.killpg(pgid, signal.SIGTERM)
        try:
            if proc.poll() is None:
                if not owned_group:
                    proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            if (
                owned_group
                and pgid is not None
                and leader is not None
                and _live_identity(leader) is not None
            ):
                with suppress(OSError):
                    os.killpg(pgid, signal.SIGKILL)
            with suppress(Exception):
                if proc.poll() is None:
                    proc.kill()
            with suppress(Exception):
                proc.wait(timeout=5.0)
        except Exception:
            pass
        with suppress(Exception):
            if proc.poll() is None:
                proc.wait(timeout=1.0)
        if owned_group:
            _terminate_known_processes(descendants)
        return
    try:
        if proc.poll() is not None:
            return
        proc.terminate()
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        with suppress(Exception):
            proc.kill()
            proc.wait(timeout=5.0)
    except Exception:
        return


def terminate_owned_process(  # noqa: PLR0912
    proc: subprocess.Popen[str], identity: ProcessIdentity
) -> None:
    """Terminate only a child whose PID and create time still match."""
    if proc.pid != identity.pid:
        raise RuntimeError("owned process PID changed before shutdown")
    try:
        current = psutil.Process(identity.pid)
        if current.create_time() != identity.create_time:
            raise RuntimeError("owned process identity changed before shutdown")
    except psutil.NoSuchProcess:
        proc.wait(timeout=1.0)
        return
    except (psutil.AccessDenied, psutil.ZombieProcess) as exc:
        raise RuntimeError("could not validate owned process before shutdown") from exc

    if proc.poll() is not None:
        proc.wait(timeout=1.0)
        return
    owned_group = False
    if os.name == "posix":
        with suppress(OSError):
            owned_group = os.getpgid(identity.pid) == identity.pid
    if owned_group:
        with suppress(OSError):
            os.killpg(identity.pid, signal.SIGTERM)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            current = psutil.Process(identity.pid)
            if current.create_time() != identity.create_time:
                raise RuntimeError("owned process identity changed before kill")
        except psutil.NoSuchProcess:
            proc.wait(timeout=1.0)
            return
        if owned_group:
            with suppress(OSError):
                os.killpg(identity.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait(timeout=5.0)
    if proc.poll() is None:
        raise RuntimeError("owned process did not exit")


def _popen(
    cmd: str | list[str],
    *,
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    new_session = os.name == "posix"
    if shell:
        launch_cmd: str | list[str] = shlex.join(cmd) if isinstance(cmd, list) else cmd
        if isinstance(launch_cmd, str):
            if not launch_cmd.strip():
                raise ValueError("empty command")
        elif not launch_cmd:
            raise ValueError("empty command")
    elif isinstance(cmd, list):
        if not cmd:
            raise ValueError("empty command")
        launch_cmd = list(cmd)
    else:
        try:
            launch_cmd = shlex.split(cmd)
        except ValueError as exc:
            raise ValueError(f"invalid command: {exc}") from exc
        if not launch_cmd:
            raise ValueError("empty command")
    return subprocess.Popen(
        launch_cmd,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=new_session,
    )


def run_command(
    cmd: str | list[str],
    *,
    on_line: Callable[[str], None],
    timeout_s: float = 30.0,
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> int:
    """Stream a command and raise ``TimeoutExpired`` when it exceeds its budget."""
    proc = _popen(cmd, shell=shell, env=env)
    pump = threading.Thread(
        target=_pump, args=(proc, on_line), daemon=True, name="command-output"
    )
    pump.start()
    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        terminate_failed_process(proc)
        raise
    pump.join(timeout=1.0)
    return rc


def spawn_command(
    cmd: str | list[str],
    *,
    on_line: Callable[[str], None],
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    """Start a long-lived command (a server) without waiting for its exit."""
    proc = _popen(cmd, shell=shell, env=env)
    threading.Thread(
        target=lambda: _pump(proc, on_line), daemon=True, name="spawned-cmd-output"
    ).start()
    return proc


def spawn_with_grace(  # noqa: PLR0913
    cmd: str | list[str],
    *,
    on_line: Callable[[str], None],
    grace_s: float,
    poll_s: float,
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen[str], bool]:
    """Start a long-running server command, then watch a crash-grace window."""
    proc = spawn_command(cmd, on_line=on_line, shell=shell, env=env)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(poll_s)
    return proc, proc.poll() is None


def warm_load(url: str, repo_id: str, *, timeout_s: float) -> str | None:
    """Synchronous one-token probe-load; raises on any non-2xx/odd body."""
    timeout = httpx.Timeout(
        connect=min(5.0, max(0.1, timeout_s)),
        read=min(5.0, max(0.1, timeout_s)),
        write=5.0,
        pool=5.0,
    )
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
    return response_model


def wait_healthy(  # noqa: PLR0911,PLR0912,PLR0913
    url: str,
    *,
    target_model: str | None = None,
    is_running: Callable[[], bool] | None = None,
    timeout_s: float,
    on_tick: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> ServerProbe | None:
    """Wait for health and verify an explicit target with real generation."""
    deadline = time.monotonic() + timeout_s
    health_url = url.removesuffix("/v1/models") + "/health"
    completion_url = url.removesuffix("/models") + "/chat/completions"
    with httpx.Client(timeout=httpx.Timeout(0.5)) as client:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return None
            elapsed = int(timeout_s - (deadline - time.monotonic()))
            if on_tick is not None:
                on_tick(elapsed)
            probe = ServerProbe(state="red", model_id=None, catalogue_state="red")
            health_state = "red"
            try:
                health = client.get(health_url)
                if cancel_event is not None and cancel_event.is_set():
                    return None
                health_body: object = health.json()
                health_state = health_state_from_response(
                    health.status_code, health_body
                )
            except (httpx.HTTPError, ValueError):
                pass
            try:
                resp = client.get(url)
                if cancel_event is not None and cancel_event.is_set():
                    return None
                body: object = resp.json()
                probe = probe_from_response(resp.status_code, body)
            except (httpx.HTTPError, ValueError):
                pass
            if health_state == "green":
                if target_model is None:
                    return ServerProbe(
                        state="green",
                        model_id=None,
                        available_models=probe.available_models,
                        catalogue_state=probe.catalogue_state,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if cancel_event is not None and cancel_event.is_set():
                    return None
                try:
                    response_model = warm_load(
                        completion_url,
                        target_model,
                        timeout_s=remaining,
                    )
                except (httpx.HTTPError, RuntimeError):
                    response_model = None
                if response_model == target_model:
                    return ServerProbe(
                        state="green",
                        model_id=target_model,
                        available_models=probe.available_models,
                        catalogue_state=probe.catalogue_state,
                    )
            if is_running is not None and not is_running():
                return None
            if time.monotonic() >= deadline:
                return None
            if cancel_event is not None:
                cancel_event.wait(1.0)
            else:
                time.sleep(1.0)
