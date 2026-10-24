"""Validated config documents and safe TOML patching."""

from __future__ import annotations

import fcntl
import os
import tomllib
from pathlib import Path

import pytest

from mlx_tui.config import AppConfig, ConfigParseError
from mlx_tui.config_io import ConfigWriteError, read_config, save_config


def test_read_config_is_read_only_and_reports_fields(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    assert read_config(path).config == AppConfig()
    assert not path.parent.exists()
    path.parent.mkdir()
    path.write_text('temperature = 1\nmax_context = 16384\ncustom = "keep"\n')
    doc = read_config(path)
    assert doc.config.temperature == 1.0
    assert doc.config.max_ctx == 16384
    assert {(i.key, i.severity) for i in doc.issues} == {
        ("custom", "warning"),
        ("max_context", "warning"),
    }
    assert doc.source == path.read_bytes()
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("source", [b"<<<", b'host = "\xff"'])
def test_read_config_rejects_unparseable_source(tmp_path: Path, source: bytes) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(source)
    with pytest.raises(ConfigParseError):
        read_config(path)
    assert path.read_bytes() == source


@pytest.mark.parametrize(
    "line,key",
    [
        ('host = " "', "host"),
        ("host = 1", "host"),
        ("port = true", "port"),
        ("port = 0", "port"),
        ("port = 65536", "port"),
        ("temperature = true", "temperature"),
        ("temperature = nan", "temperature"),
        ("temperature = inf", "temperature"),
        ("temperature = 2.1", "temperature"),
        ("top_p = -0.1", "top_p"),
        ("max_tokens = 0", "max_tokens"),
        ("max_tokens = 16385", "max_tokens"),
        ("max_ctx = 1023", "max_ctx"),
        ("max_context = 131073", "max_context"),
        ("seed = false", "seed"),
        ("enable_thinking = 1", "enable_thinking"),
        ('command_shell = "true"', "command_shell"),
        ('runtime_mode = "other"', "runtime_mode"),
        ("swap_policy = true", "swap_policy"),
        ("model = 1", "model"),
        ("system = false", "system"),
        ("start_cmd = 1", "start_cmd"),
        ("stop_cmd = []", "stop_cmd"),
        ('preset = "bad"', "preset"),
        ('[[preset]]\nname = "bad"\nmax_tokens = true', "preset[0]"),
        ('[[preset]]\nname = "bad"\ntemperature = 3', "preset[0]"),
        ('[[preset]]\nsystem = "missing name"', "preset[0]"),
    ],
)
def test_save_rejects_invalid_known_fields(tmp_path: Path, line: str, key: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(line + "\n")
    doc = read_config(path)
    assert any(i.key.startswith(key) and i.severity == "error" for i in doc.issues)
    with pytest.raises(ConfigWriteError):
        save_config(doc, {"unrelated": "changed"})
    assert path.read_bytes() == doc.source


def test_preset_validation_names_entries_and_warns_on_unknown_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[[preset]]\nname = "Coding"\ntemprature = 1\ntop_p = false\n')
    issues = read_config(path).issues
    assert any(i.key == "preset[0].top_p" and "Coding" in i.message for i in issues)
    assert any(
        i.key == "preset[0].temprature" and i.severity == "warning" for i in issues
    )


def test_config_patch_preserves_unedited_content(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('''# personal settings
temperature = 0.7 # sampling
max_context = 16384
model = "old"
start_cmd = 'custom --model "$MLX_TUI_MODEL"'
system = """Hola 世界
second line"""
[custom]
colors = ["red", "blue"] # keep me
''')
    doc = save_config(
        read_config(path), {"temperature": 0, "model": None, "max_ctx": 32768}
    )
    data = tomllib.loads(path.read_text())
    assert data["temperature"] == 0
    assert data["max_ctx"] == 32768
    assert "model" not in data and "max_context" not in data
    assert data["start_cmd"] == 'custom --model "$MLX_TUI_MODEL"'
    assert data["system"] == "Hola 世界\nsecond line"
    assert data["custom"] == {"colors": ["red", "blue"]}
    for comment in ("# personal settings", "# sampling", "# keep me"):
        assert comment in path.read_text()
    assert any(i.key == "custom" and i.severity == "warning" for i in doc.issues)


@pytest.mark.parametrize(
    "mutation", ["edit", "delete", "create", "legacy", "legacy_create", "retarget"]
)
def test_save_conflict_preserves_original(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "config.toml"
    target = tmp_path / "target.toml"
    legacy = tmp_path / "presets.toml"
    if mutation != "create":
        path.write_text("temperature = 0.7\n")
    if mutation == "legacy":
        legacy.write_text('[[preset]]\nname = "before"\n')
    if mutation == "retarget":
        path.rename(target)
        path.symlink_to(target)
    doc = read_config(path)
    if mutation in {"edit", "create"}:
        path.write_text("temperature = 1\n")
    elif mutation == "delete":
        path.unlink()
    elif mutation in {"legacy", "legacy_create"}:
        legacy.write_text('[[preset]]\nname = "after"\n')
    else:
        other = tmp_path / "other.toml"
        other.write_bytes(target.read_bytes())
        path.unlink()
        path.symlink_to(other)
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ConfigWriteError, match="Reload"):
        save_config(doc, {"temperature": 0.2})
    assert (path.read_bytes() if path.exists() else None) == before
    assert not list(tmp_path.glob("*.bak"))


def test_save_lock_contention_preserves_original(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("temperature = 0.7\n")
    doc = read_config(path)
    with path.with_name(path.name + ".lock").open("wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ConfigWriteError, match="lock"):
            save_config(doc, {"temperature": 0.2})
    assert path.read_bytes() == doc.source


def test_save_rechecks_snapshot_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("temperature = 0.7\n")
    doc = read_config(path)
    original_flock = fcntl.flock

    def concurrent_edit(fd: int, operation: int) -> None:
        path.write_text("temperature = 1\n")
        original_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", concurrent_edit)
    with pytest.raises(ConfigWriteError, match="Reload"):
        save_config(doc, {"temperature": 0.2})
    assert path.read_text() == "temperature = 1\n"


def test_save_replace_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("temperature = 0.7\n")
    doc = read_config(path)

    def fail_replace(_src: str, _dst: Path) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(ConfigWriteError):
        save_config(doc, {"temperature": 0.2})
    assert path.read_bytes() == doc.source
    assert not list(tmp_path.glob(".*.tmp"))


def test_preset_import_is_explicit_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("# original\ntemperature = 0.7\n")
    legacy = tmp_path / "presets.toml"
    legacy.write_text('# legacy\n[[preset]]\nname = "B"\n[[preset]]\nname = "A"\n')
    doc = read_config(path)
    assert [p.name for p in doc.presets] == ["B", "A"]
    assert path.read_bytes() == doc.source
    assert not list(tmp_path.glob("*.bak"))
    imported = save_config(doc, {"temperature": 0.2})
    backup = path.with_name(path.name + ".pre-unification.bak")
    assert backup.read_bytes() == doc.source
    assert legacy.read_bytes() == doc.legacy_source
    assert [p.name for p in imported.presets] == ["B", "A"]
    assert imported.legacy_source is None
    assert "# legacy" in path.read_text()
    legacy.write_text("<<<")
    saved = save_config(imported, {"temperature": 0.3})
    assert [p.name for p in saved.presets] == ["B", "A"]
    assert backup.read_bytes() == doc.source


@pytest.mark.parametrize(
    "canonical", ["preset = []\n", '[[preset]]\nname = "canonical"\n']
)
def test_canonical_presets_take_precedence(tmp_path: Path, canonical: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(canonical)
    (tmp_path / "presets.toml").write_text("<<<")
    doc = save_config(read_config(path), {})
    assert [p.name for p in doc.presets] == ([] if "[]" in canonical else ["canonical"])
    assert doc.legacy_source is None
    assert not list(tmp_path.glob("*.bak"))


@pytest.mark.parametrize(
    "source", ['[[preset]]\nname = "bad"\ntop_p = false\n', "<<<", "preset = 1"]
)
def test_invalid_legacy_import_cannot_erase_entries(
    tmp_path: Path, source: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("# keep\n")
    legacy = tmp_path / "presets.toml"
    legacy.write_text(source)
    doc = read_config(path)
    assert any(i.severity == "error" for i in doc.issues)
    with pytest.raises(ConfigWriteError):
        save_config(doc, {"temperature": 0.2})
    assert path.read_bytes() == doc.source
    assert legacy.read_text() == source


def test_import_preserves_existing_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("# config\n")
    backup = path.with_name(path.name + ".pre-unification.bak")
    backup.write_bytes(b"original backup")
    (tmp_path / "presets.toml").write_text('[[preset]]\nname = "legacy"\n')
    assert len(save_config(read_config(path), {}).presets) == 1
    assert backup.read_bytes() == b"original backup"


def test_config_save_updates_symlink_target(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    target = tmp_path / "elsewhere" / "target.toml"
    target.parent.mkdir()
    target.write_text("# linked\ntemperature = 0.7\n")
    path.symlink_to(target)
    saved = save_config(read_config(path), {"temperature": 0.2})
    assert path.is_symlink()
    assert target.read_bytes() == saved.source
    assert read_config(path) == saved
    assert read_config(target).config == saved.config


def test_new_config_is_private(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    doc = save_config(read_config(path), {})
    assert doc.config == AppConfig()
    assert path.stat().st_mode & 0o777 == 0o600
