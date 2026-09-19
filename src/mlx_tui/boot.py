"""Server boot/restart execution without UI dependencies."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from importlib.util import find_spec

from mlx_tui import serverctl
from mlx_tui.config import AppConfig
from mlx_tui.serverctl import build_start_command
from mlx_tui.status import ServerProbe
from mlx_tui.swap import BootPlan, health_timeout


def prepare_commands(  # noqa: PLR0912
    config: AppConfig, plan: BootPlan
) -> tuple[str | list[str], str | list[str] | None, bool, dict[str, str] | None, str]:
    """Validate and prepare the configured commands before stopping anything."""
    start_raw = config.start_cmd
    stop_raw = config.stop_cmd
    if start_raw is None:
        raise RuntimeError("[swap] start_cmd vanished from config")
    if plan.stop_first and stop_raw is None:
        raise RuntimeError("[swap] stop_cmd vanished from config")

    env: dict[str, str] | None = None
    if config.command_shell:
        if "{model}" in start_raw or (
            plan.stop_first and stop_raw is not None and "{model}" in stop_raw
        ):
            raise RuntimeError(
                "[swap] start/stop_cmd uses {model}; shell mode requires "
                '"$MLX_TUI_MODEL" (e.g., --model "$MLX_TUI_MODEL")'
            )
        if not start_raw.strip():
            raise RuntimeError("[swap] invalid start_cmd: empty command")
        if plan.stop_first and stop_raw is not None and not stop_raw.strip():
            raise RuntimeError("[swap] invalid stop_cmd: empty command")
        if plan.model_id is not None:
            # Shell contract: --model "$MLX_TUI_MODEL". The reference is
            # required but never treated as proof; endpoint verification
            # remains the final check. Do not rewrite shell syntax.
            if "MLX_TUI_MODEL" not in start_raw:
                raise RuntimeError(
                    '[swap] shell start_cmd must reference "$MLX_TUI_MODEL" '
                    '(e.g., --model "$MLX_TUI_MODEL")'
                )
            env = {**os.environ, "MLX_TUI_MODEL": plan.model_id}
        return (start_raw, stop_raw if plan.stop_first else None, True, env, start_raw)

    try:
        start_argv = build_start_command(start_raw, plan.model_id)
    except ValueError as exc:
        raise RuntimeError(f"[swap] invalid start_cmd: {exc}") from exc
    if start_argv[0] == "mlx_lm.server" and find_spec("mlx_lm") is not None:
        start_argv = [sys.executable, "-m", "mlx_lm.server", *start_argv[1:]]

    stop_argv: list[str] | None = None
    if plan.stop_first:
        assert stop_raw is not None
        try:
            parsed_stop = shlex.split(stop_raw)
        except ValueError as exc:
            raise RuntimeError(f"[swap] invalid stop_cmd: {exc}") from exc
        if not parsed_stop:
            raise RuntimeError("[swap] invalid stop_cmd: empty command")
        if any("{model}" in arg for arg in parsed_stop):
            if plan.model_id is None:
                raise RuntimeError(
                    "[swap] stop_cmd contains {model} but no model target"
                )
            parsed_stop = [arg.replace("{model}", plan.model_id) for arg in parsed_stop]
        stop_argv = parsed_stop
    return (start_argv, stop_argv, False, None, shlex.join(start_argv))


def execute_boot(  # noqa: PLR0913
    plan: BootPlan,
    config: AppConfig,
    *,
    host: str,
    port: int,
    on_line: Callable[[str], None],
    on_tick: Callable[[int], None],
) -> ServerProbe:
    """Run one stop/spawn/health sequence and return only after verification.

    The spawned process is owned locally until health verification succeeds.
    Any failure before that boundary terminates the owned process group; a
    successful return transfers all further responsibility to the caller.
    """
    start, stop, shell, env, display = prepare_commands(config, plan)
    proc: subprocess.Popen[str] | None = None
    monitor = False

    def stream(value: str) -> None:
        with suppress(Exception):
            on_line(value)

    def tick(value: int) -> None:
        with suppress(Exception):
            on_tick(value)

    try:
        if plan.stop_first:
            assert stop is not None
            try:
                stop_rc = serverctl.run_command(
                    stop,
                    on_line=stream,
                    shell=shell,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("[swap] stop_cmd timed out") from exc
            except ValueError as exc:
                raise RuntimeError(f"[swap] invalid stop_cmd: {exc}") from exc
            if stop_rc != 0:
                raise RuntimeError(f"[swap] stop_cmd exited {stop_rc}")

        stream(f"starting: {display}")
        try:
            proc, monitor = serverctl.spawn_with_grace(
                start,
                on_line=stream,
                grace_s=2.0,
                poll_s=0.05,
                shell=shell,
                env=env,
            )
        except ValueError as exc:
            raise RuntimeError(f"[swap] invalid start_cmd: {exc}") from exc
        except FileNotFoundError as exc:
            command = start[0] if isinstance(start, list) else start
            raise RuntimeError(
                f"[swap] server command not found: {command}; "
                "install the server or update start_cmd"
            ) from exc
        start_rc = proc.poll()
        if start_rc is not None and start_rc != 0:
            raise RuntimeError(f"[swap] start_cmd exited {start_rc}")

        deadline = health_timeout(plan.size_on_disk)
        rendered = f"[{host}]" if ":" in host else host
        probe = serverctl.wait_healthy(
            f"http://{rendered}:{port}/v1/models",
            target_model=plan.model_id,
            is_running=(lambda: proc is not None and proc.poll() is None)
            if monitor
            else None,
            timeout_s=deadline,
            on_tick=tick,
        )
        if probe is None:
            if monitor and proc is not None and proc.poll() is not None:
                raise RuntimeError(f"[swap] start_cmd exited {proc.returncode}")
            message = (
                f"swap timed out after {int(deadline)}s — check the log pane"
                if plan.stop_first
                else "server did not come up in time — check the log pane"
            )
            raise RuntimeError(message)
        return probe
    except Exception:
        try:
            serverctl.terminate_failed_process(proc)
        except Exception:
            pass
        raise
