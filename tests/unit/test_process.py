"""Unit tests for mlx_tui.process using faked psutil seams."""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import psutil
import pytest

import mlx_tui.process as proc_mod
from mlx_tui.process import ProcessIdentity, find_server_process, memory_snapshot

MATCHING_CMDLINE = ["python", "-m", "mlx_lm.server", "--model", "m"]
OTHER_CMDLINE = ["python", "-m", "unrelated.svc"]
_HOST = "127.0.0.1"
_PORT_A = 8080
_PORT_B = 9090
_AVAIL_GIB = 8.0
_TOTAL_GIB = 32.0


class FakeProcess:
    def __init__(self, pid: int, cmdline: list[str], create_time: float) -> None:
        self.pid = pid
        self._cmdline = cmdline
        self._create_time = create_time

    def cmdline(self) -> list[str]:
        return self._cmdline

    def create_time(self) -> float:
        return self._create_time

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

    def fake_ctor(pid: int) -> FakeProcess:
        if pid not in procs:
            raise psutil.NoSuchProcess(pid)
        return procs[pid]

    def fake_conns(kind: str = "tcp") -> list[SimpleNamespace]:
        calls["net"] += 1
        if isinstance(conns, Exception):
            raise conns
        assert kind == "tcp"
        return conns

    monkeypatch.setattr(psutil, "Process", fake_ctor)
    monkeypatch.setattr(psutil, "net_connections", fake_conns)
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
    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    assert find_server_process(_HOST, _PORT_B) == ProcessIdentity(200, 222.0)


def test_pidfile_on_wrong_port_falls_through_to_scan(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "server.pid"
    pidfile.write_text("100\n")
    procs = {
        100: FakeProcess(100, MATCHING_CMDLINE, 111.0),
        200: FakeProcess(200, MATCHING_CMDLINE, 222.0),
    }
    _install(monkeypatch, procs, [_conn(100, _PORT_B), _conn(200, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A, pidfile=str(pidfile)) == ProcessIdentity(
        200, 222.0
    )


def test_pidfile_on_wrong_port_no_other_listener_returns_none(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "server.pid"
    pidfile.write_text("100\n")
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_B)])
    assert find_server_process(_HOST, _PORT_A, pidfile=str(pidfile)) is None


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
    procs[100] = FakeProcess(100, MATCHING_CMDLINE, 222.0)
    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    # Simulate a fresh poll after cache invalidation: new start time wins.
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 222.0)


def test_cached_hit_short_circuits_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    calls = _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)
    assert calls["net"] == 1
    # Second poll with same pid/start time must not rescan connections.
    calls["net"] = 0

    def exploding_conns(kind: str = "tcp") -> list[SimpleNamespace]:
        raise AssertionError("must not scan connections on cached hit")

    monkeypatch.setattr(psutil, "net_connections", exploding_conns)
    assert find_server_process(_HOST, _PORT_A) == ProcessIdentity(100, 111.0)


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
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "server.pid"
    pidfile.write_text("not-a-pid")
    procs = {200: FakeProcess(200, MATCHING_CMDLINE, 222.0)}
    _install(monkeypatch, procs, [_conn(200, _PORT_A)])
    assert find_server_process(_HOST, _PORT_A, pidfile=str(pidfile)) == ProcessIdentity(
        200, 222.0
    )


def test_loopback_variants_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    procs = {100: FakeProcess(100, MATCHING_CMDLINE, 111.0)}
    _install(monkeypatch, procs, [_conn(100, _PORT_A)])
    assert find_server_process("localhost", _PORT_A) is not None
    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    assert find_server_process("::1", _PORT_A) is not None


def test_memory_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_vm = SimpleNamespace(total=_TOTAL_GIB * 2**30, available=_AVAIL_GIB * 2**30)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: fake_vm)
    snap = memory_snapshot()
    assert snap.avail_gib == _AVAIL_GIB
    assert snap.total_gib == _TOTAL_GIB
