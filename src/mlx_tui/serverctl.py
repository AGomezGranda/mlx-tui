"""Stop/start commands, warm-load probe, health waiting — no UI knowledge."""

from __future__ import annotations

import os
import shlex
import signal
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


def _argv_from(cmd: str | list[str]) -> list[str]:
    if isinstance(cmd, list):
        if not cmd:
            raise ValueError("empty command")
        return list(cmd)
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        raise ValueError(f"invalid command: {exc}") from exc
    if not argv:
        raise ValueError("empty command")
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
    except ValueError:
        return
    except Exception:
        return
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass


def _kill_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _terminate_failed_process(proc: subprocess.Popen[str] | None) -> None:  # noqa: PLR0911,PLR0912,PLR0915
    """Stop an owned failed-boot/stop-timeout process group; never a success."""
    if proc is None:
        return
    try:
        pid = proc.pid
    except Exception:
        return
    if os.name == "posix":
        try:
            pgid = os.getpgid(pid)
        except Exception:
            pgid = pid
        _kill_group(pgid, signal.SIGTERM)
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            _kill_group(pgid, signal.SIGKILL)
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5.0)
            except Exception:
                pass
        except Exception:
            pass
        # Shell-exits-first: direct child reaped but descendants survive.
        _kill_group(pgid, signal.SIGKILL)
        try:
            if proc.poll() is None:
                proc.wait(timeout=1.0)
        except Exception:
            pass
        return
    try:
        if proc.poll() is not None:
            return
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            return
        try:
            proc.wait(timeout=5.0)
        except Exception:
            return
    except Exception:
        return


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
    else:
        launch_cmd = _argv_from(cmd)
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
        _terminate_failed_process(proc)
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
                if target_model in probe.available_models:
                    # MLX-LM lists cached models. Verify serving with a completion.
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    result = warm_load(
                        url.removesuffix("/models") + "/chat/completions",
                        target_model,
                        timeout_s=remaining,
                    )
                    if result.response_model == target_model:
                        return ServerProbe(state="green", model_id=target_model)
                    return None
            if is_running is not None and not is_running():
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(1.0)
