"""Build and install smoke checks for the wheel and source distribution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE_ENTRY_POINT = 'mlx-tui = "mlx_tui.app:main"'
EXPECTED_WHEEL_ENTRY_POINT = "mlx-tui = mlx_tui.app:main"
RUNTIME_RESOURCES = ("managed-runtime.txt", "managed-build-constraints.txt")


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["project"]["version"])


def _source_resource(name: str) -> bytes:
    return (ROOT / "src" / "mlx_tui" / name).read_bytes()


def _run(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def _license_text() -> str:
    return (ROOT / "LICENSE").read_text()


def _assert_license(text: str) -> None:
    license_text = _license_text()
    assert "MIT License" in text
    assert "Copyright (c) 2026 Alvaro Gomez" in text
    assert "Permission is hereby granted" in text
    assert "Permission is hereby granted" in license_text


def _wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(archive.read(metadata_name).decode())
    name = metadata["Name"]
    version = metadata["Version"]
    assert name is not None and version is not None
    return name, version


def _inspect_wheel(path: Path, expected_version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        license_names = [name for name in names if name.endswith("/LICENSE")]
        assert license_names, f"wheel has no packaged license: {path}"
        _assert_license(archive.read(license_names[0]).decode())
        assert "mlx_tui/__init__.py" in names
        assert "mlx_tui/coding_profiles.toml" in names
        for resource in RUNTIME_RESOURCES:
            packaged = f"mlx_tui/{resource}"
            assert packaged in names
            assert archive.read(packaged) == _source_resource(resource)
        name, version = _wheel_metadata(path)
        assert name == "mlx-tui"
        assert version == expected_version
        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode()
        assert EXPECTED_WHEEL_ENTRY_POINT in entry_points


def _inspect_sdist(path: Path, extract_to: Path, expected_version: str) -> Path:
    extract_to.mkdir()
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name.removeprefix("./") for member in members}
        license_names = [name for name in names if name.endswith("/LICENSE")]
        assert license_names, f"sdist has no license: {path}"
        license_member = archive.getmember(license_names[0])
        license_text = archive.extractfile(license_member)
        assert license_text is not None
        _assert_license(license_text.read().decode())
        pyproject_name = next(
            name for name in names if name.endswith("/pyproject.toml")
        )
        pyproject_member = archive.getmember(pyproject_name)
        pyproject_file = archive.extractfile(pyproject_member)
        assert pyproject_file is not None
        pyproject_text = pyproject_file.read().decode()
        assert EXPECTED_SOURCE_ENTRY_POINT in pyproject_text
        assert f'version = "{expected_version}"' in pyproject_text
        assert any(name.endswith("/src/mlx_tui/__init__.py") for name in names)
        archive.extractall(extract_to, filter="data")

    roots = [path for path in extract_to.iterdir() if path.is_dir()]
    assert len(roots) == 1, roots
    source_root = roots[0]
    for resource in RUNTIME_RESOURCES:
        assert (
            source_root / "src" / "mlx_tui" / resource
        ).read_bytes() == _source_resource(resource)
    return source_root


def _venv_python(venv: Path) -> Path:
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    return bindir / "python.exe" if os.name == "nt" else bindir / "python"


def _install_and_smoke(
    wheel: Path, venv: Path, workdir: Path, expected_version: str
) -> None:
    _run("uv", "venv", str(venv), "--python", sys.executable, cwd=ROOT)
    python = _venv_python(venv)
    _run("uv", "pip", "install", "--python", str(python), str(wheel), cwd=ROOT)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    xdg_roots = {
        key: workdir / f"{venv.name}-{suffix}"
        for key, suffix in (
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_STATE_HOME", "state"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_CACHE_HOME", "cache"),
        )
    }
    env.update({key: str(root) for key, root in xdg_roots.items()})
    cli = python.parent / "mlx-tui"
    if os.name == "nt":
        cli = cli.with_suffix(".exe")
    result = _run(str(cli), "--help", cwd=workdir, env=env)
    assert "usage: mlx-tui" in result.stdout
    assert "--managed" in result.stdout
    assert "--attach" in result.stdout
    result = _run(str(cli), "--version", cwd=workdir, env=env)
    assert result.stdout.strip() == expected_version
    diagnostics_path = workdir / f"{venv.name}-diagnostics.json"
    result = _run(
        str(cli), "--diagnostics", str(diagnostics_path), cwd=workdir, env=env
    )
    diagnostics = json.loads(result.stdout or diagnostics_path.read_text())
    assert diagnostics["schema_version"] == 1
    assert diagnostics["tui_version"] == expected_version
    assert all(not root.exists() for root in xdg_roots.values())
    result = _run(
        str(python),
        "-c",
        "import mlx_tui, mlx_tui.app; from mlx_tui.setup_screen import SetupScreen; "
        "print(mlx_tui.__file__)",
        cwd=workdir,
        env=env,
    )
    imported = Path(result.stdout.strip()).resolve()
    assert imported.is_relative_to(venv.resolve()), imported
    assert not imported.is_relative_to(ROOT.resolve()), imported
    result = _run(
        str(python),
        "-c",
        "from mlx_tui.profiles import load_coding_profiles; "
        "print(','.join(p.profile.id for p in load_coding_profiles()))",
        cwd=workdir,
        env=env,
    )
    assert result.stdout.strip().split(",") == [
        "qwen3-1.7b-baseline",
        "qwen3.5-4b-baseline",
    ]
    result = _run(
        str(python),
        "-c",
        "from importlib.resources import files; "
        "from mlx_tui.profiles import load_coding_profiles; "
        "freeze=files('mlx_tui').joinpath('managed-runtime.txt').read_text(); "
        "assert 'mlx==0.32.2' in freeze; "
        "assert 'mlx-metal==0.32.2' in freeze; "
        "assert '74e7cf931e84ef7c2f63e875adf414e20decc1c5' in freeze; "
        "assert all(p.profile.runtime_commit == '74e7cf931e84ef7c2f63e875adf414e20decc1c5' "
        "for p in load_coding_profiles()); "
        "assert 'setuptools==84.0.0' in files('mlx_tui').joinpath('managed-build-constraints.txt').read_text(); "
        "print('runtime resources ok')",
        cwd=workdir,
        env=env,
    )
    assert result.stdout.strip() == "runtime resources ok"


def main() -> None:
    expected_version = _project_version()
    with tempfile.TemporaryDirectory(prefix="mlx-tui-artifacts-") as raw_tmp:
        tmp = Path(raw_tmp)
        built = tmp / "built"
        built.mkdir()
        _run("uv", "build", "--out-dir", str(built), cwd=ROOT)
        wheel = next(built.glob("*.whl"))
        sdist = next(built.glob("*.tar.gz"))
        _inspect_wheel(wheel, expected_version)
        source_root = _inspect_sdist(sdist, tmp / "sdist", expected_version)

        rebuilt = tmp / "rebuilt"
        rebuilt.mkdir()
        _run("uv", "build", "--wheel", "--out-dir", str(rebuilt), cwd=source_root)
        rebuilt_wheel = next(rebuilt.glob("*.whl"))
        _inspect_wheel(rebuilt_wheel, expected_version)

        workdir = tmp / "outside-checkout"
        workdir.mkdir()
        _install_and_smoke(wheel, tmp / "wheel-venv", workdir, expected_version)
        _install_and_smoke(rebuilt_wheel, tmp / "sdist-venv", workdir, expected_version)

    print("artifact smoke passed")


if __name__ == "__main__":
    main()
