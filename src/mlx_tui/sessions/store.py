"""Session store: UI-free atomic file storage and advisory locks."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from operator import attrgetter
from pathlib import Path

from mlx_tui.json_util import atomic_write_bytes, sync_dir
from mlx_tui.sessions.codec import _session_uuid, decode_session, encode_session
from mlx_tui.sessions.errors import (
    SessionDurabilityUnconfirmed,
    SessionLockedError,
    SessionPersistenceError,
    SessionStaleWriteError,
    SessionValidationError,
)
from mlx_tui.sessions.models import ChatSession

try:
    import fcntl
except ImportError:  # pragma: no cover - session locking is Unix-only
    fcntl = None  # type: ignore[assignment]


def session_dir() -> Path:
    """Return the sessions directory without creating or reading anything."""
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "mlx-tui" / "sessions"


def _refuse_symlinked_root(directory: Path) -> None:
    data_root = directory.parents[1]
    for path in (data_root, data_root / "mlx-tui", directory):
        if path.exists() and path.is_symlink():
            raise SessionPersistenceError(f"refusing symlinked sessions path: {path}")


def _checked_dir(create: bool) -> Path:
    directory = session_dir()
    _refuse_symlinked_root(directory)
    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise SessionPersistenceError(
                f"could not create sessions directory: {directory}"
            ) from exc
        if directory.is_symlink() or not directory.is_dir():
            raise SessionPersistenceError(
                f"refusing unexpected sessions path: {directory}"
            )
    return directory


def _session_path(directory: Path, session_id: str) -> Path:
    canonical = _session_uuid(session_id, "session_id")
    return directory / f"{canonical}.json"


def _lock_path(directory: Path, session_id: str) -> Path:
    canonical = _session_uuid(session_id, "session_id")
    target = directory / f"{canonical}.lock"
    if target.exists() and target.is_symlink():
        raise SessionPersistenceError(f"refusing symlinked session lock: {target}")
    return target


def _parse_updated_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _regular_session_file(target: Path) -> Path:
    if target.is_symlink() or not target.is_file():
        raise SessionValidationError(f"refusing non-regular session file: {target}")
    return target


@dataclass
class SessionLock:
    """A held exclusive advisory lock for one session; released on close."""

    session_id: str
    path: Path
    fd: int = field(repr=False, compare=False)
    held: bool = field(default=True, compare=False)

    def release(self) -> None:
        """Release the advisory lock exactly once; closing is idempotent."""
        if not self.held:
            return
        if fcntl is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.held = False


@contextmanager
def lock_session(session_id: str) -> Generator[SessionLock]:
    """Hold a nonblocking exclusive lock on a stable content-free sibling file.

    A locked session can be viewed read-only elsewhere; only the holder may
    save or delete. Lock files are never unlinked. The same process must not
    open a second descriptor for a session it already holds (a second
    ``flock`` through a separately opened descriptor blocks even in the same
    process on macOS); pass the yielded handle to ``delete_session`` instead.
    """
    if fcntl is None:
        raise SessionPersistenceError("session locking is unsupported on this OS")
    directory = _checked_dir(create=True)
    target = _lock_path(directory, session_id)
    try:
        fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise SessionPersistenceError(f"cannot open session lock: {target}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            raise SessionLockedError(
                f"session is locked by another process: {session_id}"
            ) from exc
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise SessionPersistenceError(f"cannot lock session: {session_id}") from exc
    handle = SessionLock(
        session_id=_session_uuid(session_id, "session_id"), path=target, fd=fd
    )
    try:
        yield handle
    finally:
        handle.release()


def _locked_by_other(lock_target: Path) -> bool:
    if fcntl is None:
        raise SessionPersistenceError("session locking is unsupported on this OS")
    try:
        fd = os.open(lock_target, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SessionPersistenceError(
            f"could not probe session lock: {lock_target}"
        ) from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)


def save_session(session: ChatSession) -> Path:
    """Validate, then atomically replace the session snapshot.

    A stale in-memory revision (older ``updated_at`` than the file on disk)
    is rejected without touching the file. Post-replacement directory-sync
    failures raise ``SessionDurabilityUnconfirmed``: the new bytes remain on
    disk but durability is unconfirmed.
    """
    encoded = encode_session(session).encode("utf-8")
    directory = _checked_dir(create=True)
    target = _session_path(directory, session.session_id)
    if target.exists():
        _regular_session_file(target)
        try:
            existing = decode_session(
                target.read_text(encoding="utf-8"),
                expected_id=session.session_id,
            )
        except SessionValidationError:
            existing = None
        except (OSError, UnicodeError) as exc:
            raise SessionPersistenceError(
                f"could not read existing session {target}"
            ) from exc
        if existing is not None and _parse_updated_at(
            existing.updated_at
        ) > _parse_updated_at(session.updated_at):
            raise SessionStaleWriteError(
                "session file holds a newer revision; refusing stale save"
            )
    atomic_write_bytes(target, encoded, error=SessionPersistenceError, what="session")
    sync_dir(directory, target, error=SessionDurabilityUnconfirmed)
    return target


def load_session(path: Path, lock_handle: SessionLock | None = None) -> ChatSession:
    """Load and strictly validate one session without rewriting it.

    A ``running`` attempt is preserved while another process owns the edit
    lock; it maps to ``interrupted`` in memory only after the caller proves
    no writer remains, either by passing its held ``lock_handle`` or by a
    successful lock probe. Corrupt and newer-schema files stay intact and
    raise independently.
    """
    target = Path(path)
    if target.is_symlink():
        raise SessionValidationError(f"refusing non-regular session file: {target}")
    if not target.exists():
        raise SessionPersistenceError(f"unknown session file: {target}")
    _regular_session_file(target)
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SessionPersistenceError(f"could not read session {target}") from exc
    session = decode_session(content, expected_id=target.stem)
    if not any(turn.outcome == "running" for turn in session.attempts):
        return session
    if lock_handle is not None:
        if not lock_handle.held or lock_handle.session_id != session.session_id:
            raise SessionValidationError("session lock is not held for this session")
        locked_by_other = False
    else:
        locked_by_other = _locked_by_other(
            _lock_path(target.parent, session.session_id)
        )
    if locked_by_other:
        return session
    return ChatSession(
        session_id=session.session_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        settings=session.settings,
        draft=session.draft,
        attempts=tuple(
            replace(
                turn,
                outcome="interrupted" if turn.outcome == "running" else turn.outcome,
            )
            for turn in session.attempts
        ),
    )


def list_sessions() -> list[Path]:
    """Return candidate session files by name; contents stay unread until opened."""
    directory = session_dir()
    if not directory.exists():
        return []
    _refuse_symlinked_root(directory)
    if not directory.is_dir():
        raise SessionPersistenceError(f"refusing unexpected sessions path: {directory}")
    return sorted(directory.glob("*.json"), key=attrgetter("name"))


def delete_session(session_id: str, lock_handle: SessionLock | None = None) -> None:
    """Unlink one session file, consuming the owner's held lock when given.

    Without a handle, deletion is refused while another process holds the
    lock. Lock files are never unlinked.
    """
    directory = _checked_dir(create=False)
    target = _session_path(directory, session_id)
    if lock_handle is not None:
        if not lock_handle.held or lock_handle.session_id != target.stem:
            raise SessionValidationError("session lock is not held for this session")
    elif target.with_suffix(".lock").exists() and _locked_by_other(
        _lock_path(directory, session_id)
    ):
        raise SessionLockedError(f"session is locked by another process: {session_id}")
    if target.is_symlink():
        raise SessionValidationError(f"refusing non-regular session file: {target}")
    if not target.exists():
        raise SessionPersistenceError(f"unknown session: {session_id}")
    _regular_session_file(target)
    try:
        target.unlink()
    except FileNotFoundError as exc:
        raise SessionPersistenceError(f"unknown session: {session_id}") from exc
    except OSError as exc:
        raise SessionPersistenceError(f"could not delete session {target}") from exc
