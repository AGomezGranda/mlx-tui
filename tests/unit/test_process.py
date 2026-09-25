"""Unit tests for mlx_tui.process using faked psutil seams."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest

import mlx_tui.process as proc_mod
from mlx_tui.operations import OperationKind
from mlx_tui.process import (
    ProcessIdentity,
    find_server_process,
    memory_snapshot,
    sample_resources,
)

MATCHING_CMDLINE = ["python", "-m", "mlx_lm.server", "--model", "m"]
OTHER_CMDLINE = ["python", "-m", "unrelated.svc"]
_HOST = "127.0.0.1"
_PORT_A = 8080
_PORT_B = 9090
_AVAIL_GIB = 8.0
_TOTAL_GIB = 32.0


class FakeProcess:
    def __init__(
        self,
        pid: int,
        cmdline: list[str],
        create_time: float,
        connections: list[SimpleNamespace] | Exception | None = None,
    ) -> None:
        self.pid = pid
        self._cmdline = cmdline
        self._create_time = create_time
        self._connections = [] if connections is None else connections
        self.connection_calls = 0

    def cmdline(self) -> list[str]:
        return self._cmdline

    def create_time(self) -> float:
        return self._create_time

    def net_connections(self, kind: str = "tcp") -> list[SimpleNamespace]:
        self.connection_calls += 1
        assert kind == "tcp"
        if isinstance(self._connections, Exception):
            raise self._connections
        return self._connections

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=0)


def _conn(pid: int | None, port: int, status: str = "LISTEN") -> SimpleNamespace:
    return SimpleNamespace(pid=pid, status=status, laddr=("127.0.0.1", port))


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    proc_mod._net_denied = False  # type: ignore[attr-defined]


def _install(
    monkeypatch: pytest.MonkeyPatch,
    procs: dict[int, FakeProcess],
    conns: list[SimpleNamespace] | Exception,
) -> dict[str, int]:
    calls = {"net": 0}

    if isinstance(conns, Exception):
        for process in procs.values():
            process._connections = conns
    else:
        for process in procs.values():
            process._connections = [conn for conn in conns if conn.pid == process.pid]

    def fake_ctor(pid: int) -> FakeProcess:
        if pid not in procs:
            raise psutil.NoSuchProcess(pid)
        return procs[pid]

    def fake_conns(kind: str = "tcp") -> list[SimpleNamespace]:
        calls["net"] += 1
        if isinstance(conns, Exception):
            raise conns
        assert kind == "tcp"
        return [
            conn
            for process in procs.values()
            if not isinstance(process._connections, Exception)
            for conn in process._connections
        ]

    monkeypatch.setattr(psutil, "Process", fake_ctor)
    monkeypatch.setattr(psutil, "net_connections", fake_conns)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(()))
    return calls


def test_two_mlx_processes_on_different_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
    }
    _install(monkeypatch, procs, [_conn(100, _PORT_A), _conn(200, _PORT_B)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)
    assert find_server_process(_HOST, _PORT_B) == ProcessIdentity(200, 222.0)


def test_denied_global_scan_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
    }
    _install(monkeypatch, procs, psutil.AccessDenied())
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(procs.values()))

    assert find_server_process(_HOST, _PORT_B) is None


def test_denied_global_scan_verifies_process_listeners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
        300: FakeProcess(300, OTHER_CMDLINE, 333.0),
    }
    calls = _install(monkeypatch, procs, psutil.AccessDenied())
    procs[100]._connections = [_conn(None, _PORT_A)]
    procs[200]._connections = [_conn(None, _PORT_B)]
    procs[300]._connections = [_conn(None, _PORT_A)]
    monkeypatch.setattr(psutil, "process_iter", lambda: iter(procs.values()))

    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)
    assert find_server_process(_HOST, _PORT_B) == ProcessIdentity(200, 222.0)
    assert calls["net"] == 1
    procs[200]._connections = [_conn(None, _PORT_A)]
    assert find_server_process(_HOST, _PORT_A) is None


def test_pidfile_on_wrong_port_falls_through_to_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
    }
    _install(monkeypatch, procs, [_conn(100, _PORT_B), _conn(200, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(200, 222.0)


def test_pidfile_on_wrong_port_no_other_listener_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_B)])
    assert find_server_process(_HOST, _PORT_A) is None


def test_recycled_pid_creation_time_not_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)
    # OS recycles the pid: same pid, new start time, unrelated cmdline.
    procs[100] = FakeProcess(100, OTHER_CMDLINE, 999.0)
    assert find_server_process(_HOST, _PORT_A) is None


def test_recycled_pid_with_new_mlx_returns_new_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)
    procs[100] = FakeProcess(
        100, MATCHING_CMDLINE, 222.0, connections=[_conn(100, _PORT_A)]
    )
    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    # Simulate a fresh poll after cache invalidation: new start time wins.
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 222.0)


def test_cached_hit_checks_local_listener_without_global_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    calls = _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)
    assert calls["net"] == 1
    # Second poll checks the process locally but must not rescan globally.
    calls["net"] = 0

    def exploding_conns(kind: str = "tcp") -> list[SimpleNamespace]:
        raise AssertionError("must not scan connections on cached hit")

    monkeypatch.setattr(psutil, "net_connections", exploding_conns)
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)
    assert procs[100].connection_calls == 1


def test_cached_listener_loss_falls_through_to_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)

    procs[100]._connections = []
    assert find_server_process(_HOST, _PORT_A) is None
    assert proc_mod._cached_identity is None  # type: ignore[attr-defined]


def test_cached_listener_replacement_is_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
    }
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)

    procs[100]._connections = []
    procs[200]._connections = [_conn(200, _PORT_A)]
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(200, 222.0)


def test_cached_listener_inspection_denied_falls_through_to_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
    }
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)

    procs[100]._connections = psutil.AccessDenied(pid=100)
    procs[200]._connections = [_conn(200, _PORT_A)]
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(200, 222.0)


def test_remote_host_returns_none_without_syscall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exploding_conns(kind: str = "tcp") -> list[SimpleNamespace]:
        raise AssertionError("remote must not scan")

    def exploding_ctor(pid: int) -> FakeProcess:
        raise AssertionError("remote must not inspect processes")

    monkeypatch.setattr(psutil, "net_connections", exploding_conns)
    monkeypatch.setattr(psutil, "Process", exploding_ctor)
    assert find_server_process("192.168.1.10", _PORT_A) is None
    assert find_server_process("example.com", _PORT_A) is None


def test_net_connections_access_denied_returns_none_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    calls = _install(monkeypatch, procs, psutil.AccessDenied(pid=100))
    assert find_server_process(_HOST, _PORT_A) is None
    assert calls["net"] == 1
    # Cached denial avoids a syscall storm on every 2s poll.
    assert find_server_process(_HOST, _PORT_A) is None
    assert calls["net"] == 1
    assert proc_mod._net_denied is True  # type: ignore[attr-defined]


def test_no_listener_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, {}, [])
    assert find_server_process(_HOST, _PORT_A) is None


def test_non_mlx_listener_is_not_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, OTHER_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) is None


def test_pidfile_garbage_falls_through_to_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {200: FakeProcess(200, MATCHING_CMDLINE, 222.0)}
    _install(monkeypatch, procs, [_conn(200, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(200, 222.0)


def test_localhost_address_family_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process("localhost", _PORT_A) is None


def test_listener_address_must_match(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    wrong_address = SimpleNamespace(pid=100, status="LISTEN", laddr=("::1", _PORT_A))
    _install(monkeypatch, procs, [wrong_address])
    assert find_server_process(_HOST, _PORT_A) is None


def test_ambiguous_matching_listeners_are_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
    }
    _install(monkeypatch, procs, [_conn(100, _PORT_A), _conn(200, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) is None


def test_memory_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_vm = SimpleNamespace(total=_TOTAL_GIB * 2**30, available=_AVAIL_GIB * 2**30)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: fake_vm)
    snap = memory_snapshot()
    assert snap.avail_gib == _AVAIL_GIB
    assert snap.total_gib == _TOTAL_GIB


def _install_resource_seams(  # noqa: PLR0913
    monkeypatch: pytest.MonkeyPatch,
    *,
    cpu: float | Exception = 12.5,
    avail_gib: float | Exception = 8.0,
    total_gib: float = 16.0,
    swap_gib: float | Exception = 1.0,
    rss_gib: float | Exception | None = 2.0,
    create_time: float = 111.0,
) -> None:
    def fake_cpu(interval: object = None) -> float:
        assert interval is None
        if isinstance(cpu, Exception):
            raise cpu
        return cpu

    def fake_vm() -> SimpleNamespace:
        if isinstance(avail_gib, Exception):
            raise avail_gib
        return SimpleNamespace(available=avail_gib * 2**30, total=total_gib * 2**30)

    def fake_swap() -> SimpleNamespace:
        if isinstance(swap_gib, Exception):
            raise swap_gib
        return SimpleNamespace(used=swap_gib * 2**30)

    class FakeRssProcess:
        def create_time(self) -> float:
            return create_time

        def memory_info(self) -> SimpleNamespace:
            if isinstance(rss_gib, Exception):
                raise rss_gib
            assert rss_gib is not None
            return SimpleNamespace(rss=rss_gib * 2**30)

    def fake_ctor(pid: int) -> FakeRssProcess:
        assert pid == 100
        return FakeRssProcess()

    monkeypatch.setattr(psutil, "cpu_percent", fake_cpu)
    monkeypatch.setattr(psutil, "virtual_memory", fake_vm)
    monkeypatch.setattr(psutil, "swap_memory", fake_swap)
    monkeypatch.setattr(psutil, "Process", fake_ctor)


def test_sample_resources_values_and_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_resource_seams(monkeypatch)
    ident = ProcessIdentity(pid=100, create_time=111.0)
    sample = sample_resources(ident, OperationKind.CHATTING)
    assert sample.operation is OperationKind.CHATTING
    assert sample.process_identity == ident
    assert sample.cpu_percent == 12.5
    assert sample.avail_gib == 8.0
    assert sample.total_gib == 16.0
    assert sample.swap_gib == 1.0
    assert sample.rss_gib == 2.0
    assert isinstance(sample.ts, float)


def test_sample_resources_missing_readings_stay_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_resource_seams(monkeypatch, cpu=psutil.Error())
    ident = ProcessIdentity(pid=100, create_time=111.0)
    sample = sample_resources(ident, OperationKind.IDLE)
    assert sample.cpu_percent is None
    assert sample.avail_gib == 8.0
    assert sample.rss_gib == 2.0

    _install_resource_seams(monkeypatch, avail_gib=OSError("no vm"))
    sample2 = sample_resources(ident, OperationKind.IDLE)
    assert sample2.avail_gib is None
    assert sample2.total_gib is None
    assert sample2.cpu_percent == 12.5
    assert sample2.rss_gib == 2.0

    _install_resource_seams(monkeypatch, swap_gib=OSError("no swap"))
    sample3 = sample_resources(ident, OperationKind.IDLE)
    assert sample3.swap_gib is None
    assert sample3.cpu_percent == 12.5
    assert sample3.avail_gib == 8.0


def test_sample_resources_pid_reuse_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_resource_seams(monkeypatch, create_time=999.0)
    ident = ProcessIdentity(pid=100, create_time=111.0)
    sample = sample_resources(ident, OperationKind.IDLE)
    assert sample.rss_gib is None
    assert sample.cpu_percent == 12.5
    assert sample.avail_gib == 8.0


def test_sample_resources_denied_rss_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_resource_seams(monkeypatch, rss_gib=psutil.AccessDenied(pid=100))
    ident = ProcessIdentity(pid=100, create_time=111.0)
    sample = sample_resources(ident, OperationKind.IDLE)
    assert sample.rss_gib is None
    assert sample.cpu_percent == 12.5


def test_sample_resources_without_identity_skips_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_resource_seams(monkeypatch)

    def exploding_ctor(pid: int) -> object:
        raise AssertionError("must not inspect a process without identity")

    def exploding_conns(kind: str = "tcp") -> object:
        raise AssertionError("must not scan listeners")

    monkeypatch.setattr(psutil, "Process", exploding_ctor)
    monkeypatch.setattr(psutil, "net_connections", exploding_conns)

    def _no_discovery(host: str, port: int) -> None:
        raise AssertionError("must not discover")

    monkeypatch.setattr(proc_mod, "find_server_process", _no_discovery)
    sample = sample_resources(None, OperationKind.IDLE)
    assert sample.rss_gib is None
    assert sample.process_identity is None
    assert sample.cpu_percent == 12.5
    assert sample.avail_gib == 8.0


_LISTENER_CODE = (
    "import socket, time; "
    "s = socket.socket(); "
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
    "s.bind(('127.0.0.1', 0)); s.listen(1); "
    "print(s.getsockname()[1], flush=True); "
    "time.sleep(30)"
)


def test_real_listener_discovery_and_pidfile_never_trusted() -> None:
    """Disposable python listener with an MLX-matching argv token.

    When the platform permits listener inspection the child is discovered;
    when inspection is denied the supported unavailable result (None) is
    returned instead of an error.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", _LISTENER_CODE, "mlx_lm.server"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline()
        assert line.strip(), "listener child produced no port"
        port = int(line.strip())
        ident = find_server_process(_HOST, port)
        # Either discovered or unavailable.
        assert ident is None or ident.pid == child.pid
        if ident is not None:
            assert ident.create_time > 0
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)
