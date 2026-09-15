from __future__ import annotations

import json
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mlx_tui import app as app_module
from mlx_tui import diagnostics


def _config_path(root: Path) -> Path:
    return root / "mlx-tui" / "config.toml"


def _marker_path(root: Path) -> Path:
    return root / "mlx-tui" / "runtimes" / "74e7cf9-py3131" / ".mlx-tui-runtime.json"


def test_missing_config_and_marker_have_fixed_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    report = diagnostics.collect_diagnostics()

    assert report["schema_version"] == 1
    assert report["config"] == {
        "present": False,
        "status": "missing",
        "runtime_mode": None,
    }
    assert report["recorded_installation"]["status"] == "missing"  # type: ignore[index]


def test_malformed_config_and_secret_canary_are_not_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_root = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    secret = "DIAGNOSTICS-SECRET-CANARY"
    path = _config_path(config_root)
    path.parent.mkdir(parents=True)
    path.write_text(f'runtime_mode = "managed"\nsystem = "{secret}\n')

    report = diagnostics.collect_diagnostics()

    assert report["config"] == {
        "present": True,
        "status": "malformed",
        "runtime_mode": None,
    }
    assert secret not in json.dumps(report)


def test_marker_values_are_allowlisted_and_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_root))
    marker = _marker_path(data_root)
    marker.parent.mkdir(parents=True)
    secret = "MARKER-SECRET-CANARY"
    marker.write_text(
        json.dumps(
            {
                "runtime_root": f"/private/{secret}",
                "runtime_commit": "not-a-commit",
                "python_version": "3.13.1",
                "inspection": {
                    "packages": {"mlx": secret, "mlx-metal": "0.32.2"},
                    "resource_hashes": {
                        "managed-runtime.txt": "invalid",
                    },
                    "freeze": secret,
                },
            }
        )
    )

    report = diagnostics.collect_diagnostics()
    recorded = report["recorded_installation"]

    assert recorded["status"] == "invalid_values"  # type: ignore[index]
    assert recorded["runtime_commit"] is None  # type: ignore[index]
    assert recorded["packages"]["mlx"] is None  # type: ignore[index]
    assert secret not in json.dumps(report)


def test_missing_package_metadata_is_reported_without_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> str:
        raise diagnostics.PackageNotFoundError("missing")

    monkeypatch.setattr(diagnostics, "package_version", missing)

    report = diagnostics.collect_diagnostics()

    assert report["tui_version"] is None
    assert report["tui_version_status"] == "unavailable"
    assert all(
        value["status"] == "unavailable"
        for value in report["dependencies"].values()  # type: ignore[union-attr]
    )


def test_collection_does_not_run_subprocess_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("diagnostics must not execute external work")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)

    diagnostics.collect_diagnostics()


def test_write_is_private_exclusive_and_refuses_symlink(
    tmp_path: Path,
) -> None:
    path = tmp_path / "diagnostics.json"
    diagnostics.write_diagnostics(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(diagnostics.DiagnosticsError, match="output_exists"):
        diagnostics.write_diagnostics(path)
    symlink = tmp_path / "diagnostics-link.json"
    symlink.symlink_to(path)
    with pytest.raises(diagnostics.DiagnosticsError, match="symlink_target"):
        diagnostics.write_diagnostics(symlink)


def test_failed_write_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "diagnostics.json"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("secret detail must not escape")

    monkeypatch.setattr(diagnostics.json, "dump", fail)
    with pytest.raises(diagnostics.DiagnosticsError, match="write_failed"):
        diagnostics.write_diagnostics(path)
    assert not path.exists()


def test_cli_diagnostics_runs_before_config_or_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []
    monkeypatch.setattr(sys, "argv", ["mlx-tui", "--diagnostics", str(tmp_path / "x")])
    monkeypatch.setattr(app_module, "write_diagnostics", seen.append)
    monkeypatch.setattr(
        app_module,
        "load_config",
        lambda: pytest.fail("diagnostics should not load ordinary config"),
    )

    app_module.main()

    assert seen == [tmp_path / "x"]
