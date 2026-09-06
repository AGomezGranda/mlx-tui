"""OS introspection helpers for locating the mlx server process."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psutil

from mlx_tui.status import MemorySnapshot

_SERVER_TOKEN_SUFFIXES = ("mlx_lm.server", "mlx_vlm.server")

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    create_time: float


_cached_identity: ProcessIdentity | None = None
_cached_endpoint: tuple[str, int] | None = None
_net_denied: bool = False


def _pid_from_file(pidfile: str) -> int | None:
    try:
        return int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        return None


def _matches_server_tokens(cmdline: list[str]) -> bool:
    return any(token.endswith(_SERVER_TOKEN_SUFFIXES) for token in cmdline)


def _conn_port(conn: object) -> int | None:
    laddr = getattr(conn, "laddr", None)
    if laddr is None:
        return None
    if isinstance(laddr, (tuple, list)) and len(laddr) >= 2:  # noqa: PLR2004
        port = laddr[1]
        return port if isinstance(port, int) else None
    port = getattr(laddr, "port", None)
    return port if isinstance(port, int) else None


def _conn_status(conn: object) -> str | None:
    status = getattr(conn, "status", None)
    return status if isinstance(status, str) else None


def _listening_pids(port: int, conns: object) -> set[int]:
    pids: set[int] = set()
    assert isinstance(conns, list)
    for conn in conns:
        pid = getattr(conn, "pid", None)
        if not isinstance(pid, int):
            continue
        if _conn_status(conn) != "LISTEN":
            continue
        if _conn_port(conn) != port:
            continue
        pids.add(pid)
    return pids


def _validated_identity(pid: int) -> ProcessIdentity | None:
    try:
        proc = psutil.Process(pid)
        cmdline: list[str] = proc.cmdline()
        ctime = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None
    if not _matches_server_tokens(cmdline):
        return None
    return ProcessIdentity(pid=pid, create_time=ctime)


def _fetch_listening_pids(port: int) -> set[int] | None:
    global _net_denied  # noqa: PLW0603
    if _net_denied:
        return None
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, PermissionError, RuntimeError):
        _net_denied = True
        return None
    return _listening_pids(port, conns)


def _process_listening_pids(port: int) -> set[int]:
    """macOS permits inspecting our servers even when a global scan is denied."""
    pids: set[int] = set()
    for proc in psutil.process_iter():
        try:
            if _matches_server_tokens(proc.cmdline()) and any(
                _conn_status(conn) == "LISTEN" and _conn_port(conn) == port
                for conn in proc.net_connections(kind="tcp")
            ):
                pids.add(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return pids


def find_server_process(
    host: str, port: int, pidfile: str | None = None
) -> ProcessIdentity | None:
    """Find the mlx server process serving the configured loopback endpoint.

    Returns None for non-loopback hosts, missing permissions, stale/recycled
    PIDs, and unmatched listeners.
    """
    global _cached_identity, _cached_endpoint  # noqa: PLW0603
    if host not in LOOPBACK_HOSTS:
        return None
    if _cached_endpoint != (host, port):
        _cached_identity = None
    _cached_endpoint = (host, port)
    if _cached_identity is not None:
        try:
            proc = psutil.Process(_cached_identity.pid)
            cmdline: list[str] = proc.cmdline()
            ctime = proc.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _cached_identity = None
        else:
            if ctime == _cached_identity.create_time and _matches_server_tokens(
                cmdline
            ):
                return _cached_identity
            _cached_identity = None
    listening = _fetch_listening_pids(port)
    if listening is None:
        listening = _process_listening_pids(port)
    preferred_pid = _pid_from_file(pidfile) if pidfile is not None else None
    candidates = sorted(listening)
    if preferred_pid is not None and preferred_pid in listening:
        candidates.remove(preferred_pid)
        candidates.insert(0, preferred_pid)
    for pid in candidates:
        ident = _validated_identity(pid)
        if ident is not None:
            _cached_identity = ident
            return ident
    return None


def memory_snapshot() -> MemorySnapshot:
    """Current system memory in GiB, ready for the status formatter."""
    vm = psutil.virtual_memory()
    return MemorySnapshot(vm.available / 2**30, vm.total / 2**30)
