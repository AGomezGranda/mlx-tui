"""OS introspection helpers for locating the mlx server process."""

from __future__ import annotations

import psutil

from mlx_tui.status import MemorySnapshot

_SERVER_TOKEN_SUFFIXES = ("mlx_lm.server", "mlx_vlm.server")


class ServerProcessFinder:
    """Finds the mlx-lm server pid, caching it between polls.

    Cache policy: a full ``psutil.process_iter`` scan happens **only** when
    the cached pid died or nothing is cached — never every tick. (Accepted
    trade-off from the v0 plan.) A cache hit is re-validated against
    ``_SERVER_TOKEN_SUFFIXES`` so a recycled pid pointing at an unrelated
    process is not trusted.
    """

    def __init__(self) -> None:
        self.pid_cache: int | None = None

    def find(self) -> int | None:
        if self.pid_cache is not None and self._cmdline_matches(self.pid_cache):
            return self.pid_cache
        self.pid_cache = None
        for proc in psutil.process_iter(["pid", "cmdline"]):
            cmdline = proc.info.get("cmdline") or []
            if _matches_server_tokens(cmdline):
                self.pid_cache = proc.pid
                return self.pid_cache
        return None

    def _cmdline_matches(self, pid: int) -> bool:
        try:
            cmdline: list[str] = psutil.Process(pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False
        return _matches_server_tokens(cmdline)


def _matches_server_tokens(cmdline: list[str]) -> bool:
    return any(token.endswith(_SERVER_TOKEN_SUFFIXES) for token in cmdline)


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
