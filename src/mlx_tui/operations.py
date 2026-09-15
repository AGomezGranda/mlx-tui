"""Shared lifecycle lease for chat, model operations, and deletion."""

from __future__ import annotations

import threading
from enum import Enum


class OperationKind(Enum):
    IDLE = "idle"
    CHATTING = "chatting"
    COMPARING = "comparing"
    LOADING = "loading"
    RESTARTING = "restarting"
    INSTALLING = "installing"
    DOWNLOADING = "downloading"
    DELETING = "deleting"


class OperationCoordinator:
    """Own exactly one operation for the complete worker lifetime."""

    _SWAP_KINDS = frozenset(
        {
            OperationKind.COMPARING,
            OperationKind.LOADING,
            OperationKind.RESTARTING,
            OperationKind.INSTALLING,
            OperationKind.DOWNLOADING,
            OperationKind.DELETING,
        }
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current = OperationKind.IDLE

    @property
    def current(self) -> OperationKind:
        with self._lock:
            return self._current

    def try_acquire(self, kind: OperationKind) -> bool:
        if kind is OperationKind.IDLE:
            return False
        with self._lock:
            if self._current is not OperationKind.IDLE:
                return False
            self._current = kind
            return True

    def release(self, kind: OperationKind) -> None:
        """Release only the lease owned by ``kind``; late releases are safe."""
        with self._lock:
            if self._current is kind:
                self._current = OperationKind.IDLE

    @property
    def is_busy(self) -> bool:
        return self.current is not OperationKind.IDLE

    @property
    def is_swap_busy(self) -> bool:
        return self.current in self._SWAP_KINDS
