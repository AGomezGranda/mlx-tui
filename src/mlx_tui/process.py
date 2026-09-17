"""OS introspection helpers for locating the mlx server process."""

from __future__ import annotations

from dataclasses import dataclass

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


def _matches_server_tokens(cmdline: list[str]) -> bool:
    return any(token.endswith(_SERVER_TOKEN_SUFFIXES) for token in cmdline)


def _conn_addr(conn: object) -> tuple[str | None, int | None]:
    laddr = getattr(conn, "laddr", None)
    if isinstance(laddr, (tuple, list)):
        host = laddr[0] if laddr else None
        port = laddr[1] if len(laddr) >= 2 else None  # noqa: PLR2004
        return (
            host if isinstance(host, str) else None,
            port if isinstance(port, int) else None,
        )
    ip = getattr(laddr, "ip", None)
    port = getattr(laddr, "port", None)
    return (
        ip if isinstance(ip, str) else None,
        port if isinstance(port, int) else None,
    )


def _address_matches(requested: str, actual: str | None) -> bool:
    if requested == "127.0.0.1":
        return actual in {"127.0.0.1", "0.0.0.0"}
    if requested == "::1":
        return actual in {"::1", "::"}
    return False


def _conn_status(conn: object) -> str | None:
    status = getattr(conn, "status", None)
    return status if isinstance(status, str) else None


def _listening_pids(host: str, port: int, conns: object) -> set[int]:
    pids: set[int] = set()
    assert isinstance(conns, list)
    for conn in conns:
        pid = getattr(conn, "pid", None)
        if not isinstance(pid, int):
            continue
        if _conn_status(conn) != "LISTEN":
            continue
        host_addr, port_addr = _conn_addr(conn)
        if port_addr != port:
            continue
        if not _address_matches(host, host_addr):
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


def _cached_identity_is_verified(
    identity: ProcessIdentity, host: str, port: int
) -> bool:
    try:
        proc = psutil.Process(identity.pid)
        cmdline: list[str] = proc.cmdline()
        ctime = proc.create_time()
        conns = proc.net_connections(kind="tcp")
    except (
        psutil.NoSuchProcess,
        psutil.AccessDenied,
        psutil.ZombieProcess,
        PermissionError,
        RuntimeError,
    ):
        return False
    return (
        ctime == identity.create_time
        and _matches_server_tokens(cmdline)
        and any(
            _conn_status(conn) == "LISTEN"
            and (addr := _conn_addr(conn))[1] == port
            and _address_matches(host, addr[0])
            for conn in conns
        )
    )


def _fetch_listening_pids(host: str, port: int) -> set[int] | None:
    global _net_denied  # noqa: PLW0603
    if _net_denied:
        return None
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, PermissionError, RuntimeError):
        _net_denied = True
        return None
    return _listening_pids(host, port, conns)


def find_server_process(host: str, port: int) -> ProcessIdentity | None:
    """Find the mlx server process serving the configured loopback endpoint.

    Returns None for non-loopback hosts, ambiguous ``localhost`` address
    families, missing process permissions, ambiguous matches, stale/recycled PIDs,
    and unmatched listeners.
    """
    global _cached_identity, _cached_endpoint  # noqa: PLW0603
    if host not in LOOPBACK_HOSTS or host == "localhost":
        return None
    if _cached_endpoint != (host, port):
        _cached_identity = None
    _cached_endpoint = (host, port)
    cached_identity = _cached_identity
    if cached_identity is not None:
        if _cached_identity_is_verified(cached_identity, host, port):
            return cached_identity
        _cached_identity = None
    listening = _fetch_listening_pids(host, port)
    identities: list[ProcessIdentity] = []
    # macOS may deny the global scan while allowing our server's sockets.
    candidates = (
        sorted(listening)
        if listening is not None
        else (proc.pid for proc in psutil.process_iter())
    )
    for pid in candidates:
        ident = _validated_identity(pid)
        if ident is not None and (
            listening is not None or _cached_identity_is_verified(ident, host, port)
        ):
            identities.append(ident)
    if len(identities) != 1:
        return None
    _cached_identity = identities[0]
    return identities[0]


def memory_snapshot() -> MemorySnapshot:
    """Current system memory in GiB, ready for the status formatter."""
    vm = psutil.virtual_memory()
    return MemorySnapshot(vm.available / 2**30, vm.total / 2**30)
