"""Immutable, user-selected UTF-8 file snapshots for chat requests."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

MAX_ATTACHMENT_BYTES = 256 * 1024


class AttachmentError(ValueError):
    """The selected path cannot become a safe text snapshot."""


@dataclass(frozen=True, slots=True)
class AttachmentSnapshot:
    """The exact bytes read at attachment time and their file identity."""

    selected_path: Path
    resolved_path: Path
    byte_length: int
    sha256: str
    content: str


def read_attachment(path: Path) -> AttachmentSnapshot:
    """Read one explicitly selected regular UTF-8 file, bounded and exactly."""
    selected = Path(path)
    opened = selected.expanduser()
    fd: int | None = None
    try:
        fd = os.open(opened, os.O_RDONLY | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise AttachmentError("selected path is not a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            raw = handle.read(MAX_ATTACHMENT_BYTES + 1)
    except AttachmentError:
        raise
    except FileNotFoundError as exc:
        raise AttachmentError("selected file does not exist") from exc
    except (IsADirectoryError, PermissionError) as exc:
        raise AttachmentError("selected path is not readable") from exc
    except OSError as exc:
        raise AttachmentError("selected file could not be read") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(
            f"selected file exceeds {MAX_ATTACHMENT_BYTES // 1024} KiB"
        )
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AttachmentError("selected file is not valid UTF-8 text") from exc
    if "\x00" in content:
        raise AttachmentError("selected file contains binary NUL bytes")
    try:
        resolved = opened.resolve(strict=True)
    except OSError as exc:
        raise AttachmentError("selected file could not be resolved") from exc
    return AttachmentSnapshot(
        selected_path=selected,
        resolved_path=resolved,
        byte_length=len(raw),
        sha256=sha256(raw).hexdigest(),
        content=content,
    )


def render_user_content(
    prompt: str, attachments: tuple[AttachmentSnapshot, ...]
) -> str:
    """Render the exact prompt plus deterministic labelled file blocks."""
    if not attachments:
        return prompt
    blocks = [prompt]
    for index, snapshot in enumerate(attachments, start=1):
        blocks.append(
            f"\n\n--- Attached file {index}: {snapshot.selected_path} ---\n"
            f"{snapshot.content}"
        )
    return "".join(blocks)
