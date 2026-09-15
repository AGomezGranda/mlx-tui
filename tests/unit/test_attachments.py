"""Bounded file snapshots and deterministic request rendering."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mlx_tui.attachments import (
    MAX_ATTACHMENT_BYTES,
    AttachmentError,
    read_attachment,
    render_user_content,
)


def test_read_attachment_keeps_exact_text_and_identity(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def answer():\n    return '✓'\n", encoding="utf-8")

    snapshot = read_attachment(source)
    source.write_text("changed", encoding="utf-8")

    assert snapshot.selected_path == source
    assert snapshot.resolved_path == source.resolve()
    assert snapshot.byte_length == len("def answer():\n    return '✓'\n".encode())
    assert snapshot.content == "def answer():\n    return '✓'\n"
    assert snapshot.text == snapshot.content


def test_render_user_content_is_exact_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    snapshots = (read_attachment(first), read_attachment(second))

    rendered = render_user_content("Fix this", snapshots)

    assert rendered == render_user_content("Fix this", snapshots)
    assert rendered.startswith("Fix this\n\n--- Attached file 1:")
    assert "one\n" in rendered and "two" in rendered
    assert "Attached file 2:" in rendered
    assert render_user_content("Fix this", ()) == "Fix this"


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("bad.bin", b"\xff", "UTF-8"),
        ("nul.txt", b"safe\x00text", "NUL"),
        ("large.txt", b"x" * (MAX_ATTACHMENT_BYTES + 1), "exceeds"),
    ],
)
def test_read_attachment_rejects_unsafe_content(
    tmp_path: Path, name: str, data: bytes, message: str
) -> None:
    source = tmp_path / name
    source.write_bytes(data)

    with pytest.raises(AttachmentError, match=message):
        read_attachment(source)


def test_read_attachment_rejects_directories_and_special_files(tmp_path: Path) -> None:
    with pytest.raises(AttachmentError):
        read_attachment(tmp_path)

    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("FIFO unavailable")
    with pytest.raises(AttachmentError):
        read_attachment(fifo)
