"""Build and install smoke checks for the wheel and source distribution."""

from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE_ENTRY_POINT = 'mlx-tui = "mlx_tui.app:main"'
EXPECTED_WHEEL_ENTRY_POINT = "mlx-tui = mlx_tui.app:main"


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


def _inspect_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        license_names = [name for name in names if name.endswith("/LICENSE")]
        assert license_names, f"wheel has no packaged license: {path}"
        _assert_license(archive.read(license_names[0]).decode())
        assert "mlx_tui/__init__.py" in names
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(archive.read(metadata_name).decode())
        assert metadata["Name"] == "mlx-tui"
        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode()
        assert EXPECTED_WHEEL_ENTRY_POINT in entry_points


def _inspect_sdist(path: Path, extract_to: Path) -> Path:
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
        assert EXPECTED_SOURCE_ENTRY_POINT in pyproject_file.read().decode()
        assert any(name.endswith("/src/mlx_tui/__init__.py") for name in names)
        archive.extractall(extract_to, filter="data")

    roots = [path for path in extract_to.iterdir() if path.is_dir()]
    assert len(roots) == 1, roots
    return roots[0]


def _venv_python(venv: Path) -> Path:
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    return bindir / "python.exe" if os.name == "nt" else bindir / "python"


def _install_and_smoke(wheel: Path, venv: Path, workdir: Path) -> None:
    _run("uv", "venv", str(venv), "--python", sys.executable, cwd=ROOT)
    python = _venv_python(venv)
    _run("uv", "pip", "install", "--python", str(python), str(wheel), cwd=ROOT)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    cli = python.parent / "mlx-tui"
    if os.name == "nt":
        cli = cli.with_suffix(".exe")
    result = _run(str(cli), "--help", cwd=workdir, env=env)
    assert "usage: mlx-tui" in result.stdout
    result = _run(
        str(python),
        "-c",
        "import mlx_tui, mlx_tui.app; print(mlx_tui.__file__)",
        cwd=workdir,
        env=env,
    )
    imported = Path(result.stdout.strip()).resolve()
    assert imported.is_relative_to(venv.resolve()), imported
    assert not imported.is_relative_to(ROOT.resolve()), imported


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mlx-tui-artifacts-") as raw_tmp:
        tmp = Path(raw_tmp)
        built = tmp / "built"
        built.mkdir()
        _run("uv", "build", "--out-dir", str(built), cwd=ROOT)
        wheel = next(built.glob("*.whl"))
        sdist = next(built.glob("*.tar.gz"))
        _inspect_wheel(wheel)
        source_root = _inspect_sdist(sdist, tmp / "sdist")

        rebuilt = tmp / "rebuilt"
        rebuilt.mkdir()
        _run("uv", "build", "--wheel", "--out-dir", str(rebuilt), cwd=source_root)
        rebuilt_wheel = next(rebuilt.glob("*.whl"))
        _inspect_wheel(rebuilt_wheel)

        workdir = tmp / "outside-checkout"
        workdir.mkdir()
        _install_and_smoke(wheel, tmp / "wheel-venv", workdir)
        _install_and_smoke(rebuilt_wheel, tmp / "sdist-venv", workdir)

    print("artifact smoke passed")


if __name__ == "__main__":
    main()
