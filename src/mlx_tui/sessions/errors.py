"""Session error types: UI-free storage contract failures."""

from __future__ import annotations

from pathlib import Path


class SessionValidationError(ValueError):
    """A session record, ID, reference, or lock use is invalid."""


class SessionPersistenceError(SessionValidationError):
    """A session checkpoint could not be safely written, read, or removed."""


class SessionLockedError(SessionPersistenceError):
    """Another process holds the session's advisory edit lock."""


class SessionStaleWriteError(SessionPersistenceError):
    """The on-disk revision is newer than the session being saved; kept intact."""


class SessionDurabilityUnconfirmed(SessionPersistenceError):
    """New bytes are on disk but the directory sync failed; durability unconfirmed."""

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(message)
        self.path = path
