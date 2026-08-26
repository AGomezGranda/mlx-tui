"""Unit tests for mlx_tui.process using faked psutil seams."""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import cast

import psutil
import pytest

from mlx_tui.process import ServerProcessFinder, memory_snapshot, model_from_cmdline

MATCHING_CMDLINE = ["python", "-m", "mlx_lm.server", "--model", "m"]
_PID_CACHED = 100
_PID_RESCAN = 200
_PID_LATE = 300
_AVAIL_GIB = 8.0
_TOTAL_GIB = 32.0


class FakeProcess:
    def __init__(
        self,
        pid: int,
        cmdline: list[str] | None = None,
        running: bool = True,
        cmdline_error: bool = False,
    ) -> None:
        self.pid = pid
        self._cmdline = cmdline or []
        self._running = running
        self._cmdline_error = cmdline_error
        self.info: dict[str, object] = {"pid": pid, "cmdline": self._cmdline}

    def is_running(self) -> bool:
        return self._running

    def cmdline(self) -> list[str]:
        if self._cmdline_error or not self._running:
            raise psutil.NoSuchProcess(self.pid)
        return self._cmdline

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=0)


def fake_process_iter(
    procs: list[FakeProcess],
) -> Callable[[list[str]], Iterator[FakeProcess]]:
    def _iter(attrs: list[str]) -> Iterator[FakeProcess]:
        return iter(procs)

    return _iter


def fake_process_ctor(
    running: bool, cmdline: list[str] | None = None
) -> Callable[[int], FakeProcess]:
    def _ctor(pid: int) -> FakeProcess:
        return FakeProcess(pid, cmdline=cmdline, running=running)

    return _ctor


@pytest.fixture(name="finder")
def _finder() -> ServerProcessFinder:
    return ServerProcessFinder()


def test_find_scans_then_caches(
    finder: ServerProcessFinder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_CACHED, MATCHING_CMDLINE)]),
    )
    assert finder.find() == _PID_CACHED

    scans = 0

    def counting_iter(attrs: list[str]) -> Iterator[FakeProcess]:
        nonlocal scans
        scans += 1
        return iter([])

    monkeypatch.setattr(psutil, "process_iter", counting_iter)
    monkeypatch.setattr(
        psutil, "Process", fake_process_ctor(running=True, cmdline=MATCHING_CMDLINE)
    )
    assert finder.find() == _PID_CACHED
    assert scans == 0


@pytest.mark.parametrize(
    ("token", "expected_pid"),
    [
        ("/opt/bin/mlx_vlm.server", 1),
        ("mlx_lm.server", 2),
        ("mlx_lm.serverx", None),
    ],
)
def test_find_suffix_matching(
    finder: ServerProcessFinder,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    expected_pid: int | None,
) -> None:
    procs: list[FakeProcess] = (
        [FakeProcess(expected_pid, ["python", token])] if expected_pid else []
    )
    monkeypatch.setattr(psutil, "process_iter", fake_process_iter(procs))
    assert finder.find() == expected_pid


def test_cache_dead_rescans(
    finder: ServerProcessFinder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_CACHED, MATCHING_CMDLINE)]),
    )
    assert finder.find() == _PID_CACHED
    monkeypatch.setattr(psutil, "Process", fake_process_ctor(running=False))
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_RESCAN, MATCHING_CMDLINE)]),
    )
    assert finder.find() == _PID_RESCAN


def test_cache_recycled_pid_rescans(
    finder: ServerProcessFinder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_CACHED, MATCHING_CMDLINE)]),
    )
    assert finder.find() == _PID_CACHED

    # The OS recycles the pid onto an unrelated process: alive but wrong cmdline.
    monkeypatch.setattr(
        psutil,
        "Process",
        fake_process_ctor(running=True, cmdline=["python", "-m", "unrelated.svc"]),
    )
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_RESCAN, MATCHING_CMDLINE)]),
    )
    assert finder.find() == _PID_RESCAN


def test_cache_access_denied_rescans(
    finder: ServerProcessFinder, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DeniedProcess:
        def cmdline(self) -> list[str]:
            raise psutil.AccessDenied(pid=_PID_CACHED)

    def denied_ctor(pid: int) -> DeniedProcess:
        return DeniedProcess()

    finder.pid_cache = _PID_CACHED
    monkeypatch.setattr(psutil, "Process", denied_ctor)
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_LATE, MATCHING_CMDLINE)]),
    )
    assert finder.find() == _PID_LATE


def test_no_match_returns_none_and_keeps_scanning(
    finder: ServerProcessFinder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(psutil, "process_iter", fake_process_iter([]))
    assert finder.find() is None
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_LATE, MATCHING_CMDLINE)]),
    )
    assert finder.find() == _PID_LATE


def test_pidfile_valid_and_cached(
    finder: ServerProcessFinder,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pidfile = tmp_path / "server.pid"
    pidfile.write_text(f"{_PID_CACHED}\n")
    monkeypatch.setattr(
        psutil, "Process", fake_process_ctor(running=True, cmdline=MATCHING_CMDLINE)
    )
    scans = 0

    def counting_iter(attrs: list[str]) -> Iterator[FakeProcess]:
        nonlocal scans
        scans += 1
        return iter([])

    monkeypatch.setattr(psutil, "process_iter", counting_iter)

    assert finder.find(pidfile=str(pidfile)) == _PID_CACHED
    assert finder.pid_cache == _PID_CACHED

    # A later poll with no pidfile rides the cache the pidfile seeded.
    monkeypatch.setattr(psutil, "process_iter", counting_iter)
    assert finder.find() == _PID_CACHED
    assert scans == 0


def test_pidfile_garbage_falls_through_to_scan(
    finder: ServerProcessFinder,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pidfile = tmp_path / "server.pid"
    pidfile.write_text("not-a-pid")
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_RESCAN, MATCHING_CMDLINE)]),
    )
    assert finder.find(pidfile=str(pidfile)) == _PID_RESCAN


def test_pidfile_unrelated_cmdline_falls_through_to_scan(
    finder: ServerProcessFinder,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pidfile = tmp_path / "server.pid"
    pidfile.write_text(str(_PID_LATE))
    monkeypatch.setattr(
        psutil,
        "Process",
        fake_process_ctor(running=True, cmdline=["python", "-m", "unrelated.svc"]),
    )
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_RESCAN, MATCHING_CMDLINE)]),
    )
    assert finder.find(pidfile=str(pidfile)) == _PID_RESCAN


def test_pidfile_missing_file_falls_through_to_scan(
    finder: ServerProcessFinder,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        psutil,
        "process_iter",
        fake_process_iter([FakeProcess(_PID_RESCAN, MATCHING_CMDLINE)]),
    )
    assert finder.find(pidfile=str(tmp_path / "absent.pid")) == _PID_RESCAN


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["python", "--model", "foo/bar"], "foo/bar"),
        (["python", "--model"], None),
        (["python", "-m", "mlx_lm.server"], None),
    ],
)
def test_model_from_cmdline(argv: list[str], expected: str | None) -> None:
    proc = cast(psutil.Process, FakeProcess(1, argv))
    assert model_from_cmdline(proc) == expected


def test_model_from_cmdline_degrades_on_error() -> None:
    proc = cast(psutil.Process, FakeProcess(1, ["--model", "x"], cmdline_error=True))
    assert model_from_cmdline(proc) is None


def test_memory_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_vm = SimpleNamespace(total=_TOTAL_GIB * 2**30, available=_AVAIL_GIB * 2**30)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: fake_vm)
    snap = memory_snapshot()
    assert snap.avail_gib == _AVAIL_GIB
    assert snap.total_gib == _TOTAL_GIB
