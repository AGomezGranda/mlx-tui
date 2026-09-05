"""Unit tests for the shared operation coordinator."""

from __future__ import annotations

import threading

from mlx_tui.app.operations import OperationCoordinator, OperationKind


def test_acquisition_and_conflicting_requests() -> None:
    coordinator = OperationCoordinator()

    assert coordinator.try_acquire(OperationKind.CHATTING)
    assert coordinator.current is OperationKind.CHATTING
    assert coordinator.is_busy
    assert not coordinator.is_swap_busy
    assert not coordinator.try_acquire(OperationKind.LOADING)
    assert coordinator.current is OperationKind.CHATTING


def test_release_is_owner_only_and_idle_recovers() -> None:
    coordinator = OperationCoordinator()

    assert coordinator.try_acquire(OperationKind.LOADING)
    coordinator.release(OperationKind.CHATTING)
    assert coordinator.current is OperationKind.LOADING
    coordinator.release(OperationKind.LOADING)
    coordinator.release(OperationKind.LOADING)
    assert coordinator.current is OperationKind.IDLE
    assert not coordinator.is_busy
    assert not coordinator.is_swap_busy


def test_each_non_idle_kind_is_distinct() -> None:
    coordinator = OperationCoordinator()

    for kind in (
        OperationKind.CHATTING,
        OperationKind.LOADING,
        OperationKind.RESTARTING,
        OperationKind.DELETING,
    ):
        assert coordinator.try_acquire(kind)
        assert coordinator.current is kind
        assert coordinator.is_busy
        assert coordinator.is_swap_busy is (kind is not OperationKind.CHATTING)
        coordinator.release(kind)
        assert coordinator.current is OperationKind.IDLE


def test_idle_cannot_be_acquired_as_an_operation() -> None:
    assert not OperationCoordinator().try_acquire(OperationKind.IDLE)


def test_two_threads_racing_acquire_and_release() -> None:
    coordinator = OperationCoordinator()
    ready = threading.Barrier(2)
    acquired = threading.Barrier(2)
    results: list[bool] = []

    def race(kind: OperationKind) -> None:
        ready.wait()
        won = coordinator.try_acquire(kind)
        results.append(won)
        acquired.wait()
        if won:
            coordinator.release(kind)

    threads = [
        threading.Thread(target=race, args=(OperationKind.LOADING,)),
        threading.Thread(target=race, args=(OperationKind.RESTARTING,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert coordinator.current is OperationKind.IDLE
