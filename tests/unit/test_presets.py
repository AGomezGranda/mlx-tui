"""Unit tests for mlx_tui.presets."""

from __future__ import annotations

from pathlib import Path

import pytest

from mlx_tui.presets import (
    Preset,
    PresetParseError,
    load_presets,
    parse_presets,
    presets_path,
)


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    assert load_presets(missing) == []
    # parse should raise
    with pytest.raises(PresetParseError):
        parse_presets(missing)


def test_preset_name_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "presets.toml"
    p.write_text('[[preset]]\nname = "a"\n')
    presets = parse_presets(p)
    assert len(presets) == 1
    assert presets[0].name == "a"
    assert presets[0].system == ""
    assert load_presets(p) == presets


def test_full_preset_with_all_fields(tmp_path: Path) -> None:
    p = tmp_path / "presets.toml"
    p.write_text(
        '[[preset]]\nname = "full"\nsystem = "You are helpful."\ntemperature = 0.7\ntop_p = 1.0\nmax_tokens = 1024\n'
    )
    presets = parse_presets(p)
    assert len(presets) == 1
    assert presets[0] == Preset(
        name="full",
        system="You are helpful.",
        temperature=0.7,
        top_p=1.0,
        max_tokens=1024,
    )


def test_temperature_int_coerced_to_float(tmp_path: Path) -> None:
    p = tmp_path / "presets.toml"
    p.write_text('[[preset]]\nname = "inttemp"\ntemperature = 1\ntop_p = 0\n')
    presets = parse_presets(p)
    assert presets[0].temperature == 1.0
    assert isinstance(presets[0].temperature, float)
    assert presets[0].top_p == 0.0


def test_invalid_bool_for_max_tokens_filtered(tmp_path: Path) -> None:
    p = tmp_path / "presets.toml"
    p.write_text('[[preset]]\nname = "bad"\nmax_tokens = true\n')
    presets = parse_presets(p)
    # invalid bool should cause preset to be filtered (None)
    assert presets == []
    # load still returns []
    assert load_presets(p) == []


def test_malformed_toml_raises_and_load_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "presets.toml"
    p.write_text('[[preset]\nname = "oops"\n')
    with pytest.raises(PresetParseError):
        parse_presets(p)
    assert load_presets(p) == []


def test_presets_path_honours_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    expected = tmp_path / "mlx-tui" / "presets.toml"
    assert presets_path() == expected
    # also ensure load on missing returns []
    assert load_presets() == []


def test_extra_unknown_keys_ignored(tmp_path: Path) -> None:
    p = tmp_path / "presets.toml"
    p.write_text('[[preset]]\nname = "x"\nunknown = "ignored"\ntemperature = 0.5\n')
    presets = parse_presets(p)
    assert len(presets) == 1
    assert presets[0].name == "x"
    assert presets[0].temperature == 0.5
