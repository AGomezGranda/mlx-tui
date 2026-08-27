"""OS introspection helpers for locating the mlx server process."""

from __future__ import annotations

from pathlib import Path

import psutil

from mlx_tui.status import MemorySnapshot

_SERVER_TOKEN_SUFFIXES = ("mlx_lm.server", "mlx_vlm.server")

_pid_cache: int | None = None


def _pid_from_file(pidfile: str) -> int | None:
    try:
        return int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        return None


def _matches_server_tokens(cmdline: list[str]) -> bool:
    return any(token.endswith(_SERVER_TOKEN_SUFFIXES) for token in cmdline)


def _cmdline_matches(pid: int) -> bool:
    try:
        cmdline: list[str] = psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return _matches_server_tokens(cmdline)


def find_server_pid(pidfile: str | None = None) -> int | None:  # noqa: PLW0603
    """Find mlx server pid; pidfile fastpath, then cache, then scan."""
    global _pid_cache  # noqa: PLW0603
    if pidfile is not None:
        pid = _pid_from_file(pidfile)
        if pid is not None and _cmdline_matches(pid):
            _pid_cache = pid
            return pid
    if _pid_cache is not None and _cmdline_matches(_pid_cache):
        return _pid_cache
    _pid_cache = None
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmdline = proc.info.get("cmdline") or []
        if _matches_server_tokens(cmdline):
            _pid_cache = proc.pid
            return _pid_cache
    return None


def model_from_cmdline(proc: psutil.Process) -> str | None:
    """Extract the ``--model`` argv value; NoSuchProcess/AccessDenied degrade to None."""
    try:
        cmdline = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    for i, token in enumerate(cmdline):
        if token == "--model" and i + 1 < len(cmdline):
            return cmdline[i + 1]
    return None


def memory_snapshot() -> MemorySnapshot:
    """Current system memory in GiB, ready for the status formatter."""
    vm = psutil.virtual_memory()
    return MemorySnapshot(vm.available / 2**30, vm.total / 2**30)
