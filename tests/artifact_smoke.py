"""Build and install smoke checks for the wheel and source distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
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
PYTHON_VERSION = "3.13.1"
SMOKE_PACKAGES = (
    "mlx-tui",
    "httpx",
    "huggingface-hub",
    "psutil",
    "textual",
    "rich",
)


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


def _run_status(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        check=False,
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
) -> dict[str, str]:
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
    result = _run(
        str(python),
        "-c",
        "import importlib.metadata as m, json; "
        f"print(json.dumps({{name: m.version(name) for name in {SMOKE_PACKAGES!r}}}))",
        cwd=workdir,
        env=env,
    )
    return json.loads(result.stdout)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_provenance() -> dict[str, object]:
    revision = _run("git", "rev-parse", "HEAD", cwd=ROOT).stdout.strip()
    dirty = _run("git", "status", "--porcelain", cwd=ROOT).stdout.strip()
    uv_version = _run("uv", "--version", cwd=ROOT).stdout.strip()
    return {
        "source_revision": revision,
        "dirty_tree": bool(dirty),
        "python_version": platform.python_version(),
        "uv_version": uv_version,
    }


def _retain_artifacts(  # noqa: PLR0913, PLR0917
    retain_dir: Path,
    expected_version: str,
    wheel: Path,
    sdist: Path,
    smoke_versions: dict[str, dict[str, str]],
    upgrade: dict[str, object] | None = None,
) -> None:
    retain_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, dict[str, object]] = {}
    for kind, source in (("wheel", wheel), ("sdist", sdist)):
        target = retain_dir / source.name
        with target.open("xb") as file:
            file.write(source.read_bytes())
        artifacts[kind] = {
            "filename": target.name,
            "sha256": _sha256(target),
            "size": target.stat().st_size,
        }
    manifest = {
        "schema_version": 1,
        "release_role": (
            "pre-F development checkpoint"
            if expected_version == "0.2.0"
            else "F candidate"
        ),
        "project": "mlx-tui",
        "version": expected_version,
        **_source_provenance(),
        "artifacts": artifacts,
        "smoke_environments": smoke_versions,
    }
    if upgrade is not None:
        manifest["upgrade"] = upgrade
    with (retain_dir / "manifest.json").open("x") as file:
        json.dump(manifest, file, indent=2, sort_keys=True)
        file.write("\n")


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(_sha256(path).encode())
    return digest.hexdigest()


def _upgrade_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    roots = {
        "XDG_CONFIG_HOME": root / "config",
        "XDG_STATE_HOME": root / "state",
        "XDG_DATA_HOME": root / "data",
        "XDG_CACHE_HOME": root / "cache",
        "UV_CACHE_DIR": root / "uv-cache",
        "UV_TOOL_DIR": root / "uv-tools",
        "UV_TOOL_BIN_DIR": root / "uv-bin",
    }
    env.update({key: str(value) for key, value in roots.items()})
    return env


def _tool_python(tool_dir: Path) -> Path:
    candidates = sorted(tool_dir.rglob("bin/python"))
    assert candidates, f"uv tool environment not found under {tool_dir}"
    return candidates[0]


def _install_tool(
    artifact: Path, *, env: dict[str, str], workdir: Path
) -> tuple[subprocess.CompletedProcess[str], Path, str]:
    result = _run_status(
        "uv",
        "tool",
        "install",
        "--force",
        "--python",
        PYTHON_VERSION,
        str(artifact),
        cwd=workdir,
        env=env,
    )
    python = _tool_python(Path(env["UV_TOOL_DIR"]))
    freeze = _run(
        "uv",
        "pip",
        "freeze",
        "--python",
        str(python),
        cwd=workdir,
        env=env,
    ).stdout
    return result, python, freeze


def _run_tool_python(
    python: Path,
    code: str,
    *args: str,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, object]:
    result = _run(str(python), "-c", code, *args, cwd=cwd, env=env)
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _run_upgrade(  # noqa: PLR0915
    candidate: Path, predecessor: Path, expected_sha256: str, root: Path
) -> dict[str, object]:
    if not predecessor.is_file() or predecessor.suffix != ".whl":
        raise AssertionError("--upgrade-from must name a wheel file")
    try:
        int(expected_sha256, 16)
    except ValueError as exc:
        raise AssertionError("--upgrade-from-sha256 must be hexadecimal") from exc
    assert len(expected_sha256) == hashlib.sha256().digest_size * 2
    actual_sha256 = _sha256(predecessor)
    assert actual_sha256 == expected_sha256.lower(), (
        f"predecessor hash mismatch: expected {expected_sha256}, found {actual_sha256}"
    )
    name, predecessor_version = _wheel_metadata(predecessor)
    assert name == "mlx-tui"
    candidate_name, candidate_version = _wheel_metadata(candidate)
    assert candidate_name == "mlx-tui"
    assert predecessor_version != candidate_version

    env = _upgrade_env(root)
    workdir = root / "outside-checkout"
    workdir.mkdir(parents=True)
    config_dir = Path(env["XDG_CONFIG_HOME"]) / "mlx-tui"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        'runtime_mode = "attach"\nhost = "127.0.0.1"\nport = 8080\n'
        'model = "legacy/model"\n',
        encoding="utf-8",
    )
    (config_dir / "presets.toml").write_text(
        '[[preset]]\nname = "preserved"\nsystem = "keep this"\nmax_tokens = 42\n',
        encoding="utf-8",
    )
    data_roots = {
        key: Path(env[key])
        for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")
    }
    stages: list[dict[str, object]] = []

    def install_stage(label: str, artifact: Path) -> tuple[Path, str]:
        before = {key: _tree_digest(path) for key, path in data_roots.items()}
        result, python, freeze = _install_tool(artifact, env=env, workdir=workdir)
        assert result.returncode == 0, result.stderr or result.stdout
        after = {key: _tree_digest(path) for key, path in data_roots.items()}
        assert after == before, f"tool install changed app data during {label}"
        stages.append(
            {
                "stage": label,
                "artifact": artifact.name,
                "sha256": _sha256(artifact),
                "exit_status": result.returncode,
                "python": str(python),
                "freeze": freeze.splitlines(),
                "data_before": before,
                "data_after": after,
            }
        )
        return python, freeze

    predecessor_python, _ = install_stage("predecessor", predecessor)
    config_path = config_dir / "config.toml"
    presets_path = config_dir / "presets.toml"
    config_bytes = config_path.read_bytes()
    presets_bytes = presets_path.read_bytes()
    seeded: dict[str, object] = {}
    if predecessor_version == "0.2.0":
        attachment = workdir / "attachment.txt"
        attachment.write_text("preserved attachment\n", encoding="utf-8")
        seeded = _run_tool_python(
            predecessor_python,
            """
import json, sys
from pathlib import Path
from mlx_tui import comparison
from mlx_tui.attachments import read_attachment
from mlx_tui.profiles import load_coding_profiles
from mlx_tui.sessions import ChatSession, RequestSettings, SessionTurn, save_session

attachment = read_attachment(Path(sys.argv[1]))
settings = RequestSettings("legacy/model", None, None, "system", 0.5, 1.0, 42, 8192)
session = ChatSession(
    session_id="00000000-0000-4000-8000-000000000001",
    created_at="2030-01-01T00:00:00Z",
    updated_at="2030-01-01T00:00:01Z",
    settings=settings,
    draft="preserved draft",
    attachments=(attachment,),
    attempts=(SessionTurn(
        turn_id="00000000-0000-4000-8000-000000000002",
        created_at="2030-01-01T00:00:00Z",
        original_draft="draft",
        sent_content="question",
        settings=settings,
        attachments=(attachment,),
        answer="acknowledged answer",
        outcome="success",
    ),),
)
session_path = save_session(session)
entries = tuple(load_coding_profiles()[:2])
comparison_input = comparison.ComparisonInput(
    endpoint="http://127.0.0.1:18080/v1/chat/completions",
    profiles=entries,
    snapshot_paths=(Path(sys.argv[1]), Path(sys.argv[1])),
    result_path=comparison.comparison_dir() / "upgrade-comparison.json",
)
result = comparison.ComparisonResult(
    run_id="upgrade-comparison",
    status="completed",
    comparison=comparison_input,
    trials=tuple(
        comparison.TrialResult(entry.profile.id, index)
        for entry in entries
        for index in range(6)
    ),
)
comparison.save_comparison(result)
comparison.commit_choice(
    result, "keep", profile_id=entries[0].profile.id, reason="preserved"
)
print(json.dumps({
    "session": str(session_path),
    "comparison": str(comparison_input.result_path),
    "attachment_sha256": attachment.sha256,
}))
""",
            str(attachment),
            cwd=workdir,
            env=env,
        )

    def verify(python: Path, label: str) -> dict[str, object]:
        values = _run_tool_python(
            python,
            """
import json, sys
from pathlib import Path
from mlx_tui.config import parse_config
from mlx_tui.presets import parse_presets

config = parse_config(Path(sys.argv[1]))
presets = parse_presets(Path(sys.argv[2]))
value = {"host": config.host, "port": config.port, "preset": presets[0].name}
if len(sys.argv) > 4:
    from mlx_tui import comparison
    from mlx_tui.sessions import load_session
    session = load_session(Path(sys.argv[3]))
    result = comparison.load_comparison(Path(sys.argv[4]))
    choice = comparison.load_choice()
    value.update({
        "draft": session.draft,
        "answer": session.attempts[0].answer,
        "attachment_sha256": session.attachments[0].sha256,
        "session_model": session.settings.model,
        "session_max_tokens": session.settings.max_tokens,
        "comparison": result.status,
        "choice": choice.decision,
    })
print(json.dumps(value))
""",
            str(config_path),
            str(presets_path),
            *([str(seeded["session"]), str(seeded["comparison"])] if seeded else []),
            cwd=workdir,
            env=env,
        )
        assert values["host"] == "127.0.0.1"
        assert values["preset"] == "preserved"
        if seeded:
            assert values["draft"] == "preserved draft"
            assert values["answer"] == "acknowledged answer"
            assert values["attachment_sha256"] == seeded["attachment_sha256"]
            assert values["session_model"] == "legacy/model"
            assert values["session_max_tokens"] == 42
            assert values["comparison"] == "completed"
            assert values["choice"] == "keep"
        stages.append({"stage": label, "verified": values})
        return values

    candidate_python, candidate_freeze = install_stage("candidate", candidate)
    verify(candidate_python, "candidate-reopen")
    assert config_path.read_bytes() == config_bytes
    assert presets_path.read_bytes() == presets_bytes
    before_repeat = {key: _tree_digest(path) for key, path in data_roots.items()}
    repeat_python, _ = install_stage("candidate-repeat", candidate)
    verify(repeat_python, "candidate-repeat-reopen")
    assert {
        key: _tree_digest(path) for key, path in data_roots.items()
    } == before_repeat

    invalid = root / "invalid.whl"
    invalid.write_bytes(b"not a wheel")
    before_failed = {key: _tree_digest(path) for key, path in data_roots.items()}
    failed = _run_status(
        "uv",
        "tool",
        "install",
        "--force",
        "--python",
        PYTHON_VERSION,
        str(invalid),
        cwd=workdir,
        env=env,
    )
    assert failed.returncode != 0
    assert {
        key: _tree_digest(path) for key, path in data_roots.items()
    } == before_failed
    stages.append(
        {
            "stage": "invalid-replacement",
            "artifact": invalid.name,
            "exit_status": failed.returncode,
        }
    )

    restored_python, _ = install_stage("predecessor-rollback", predecessor)
    verify(restored_python, "rollback-reopen")
    assert config_path.read_bytes() == config_bytes
    assert presets_path.read_bytes() == presets_bytes
    return {
        "predecessor": {
            "filename": predecessor.name,
            "version": predecessor_version,
            "sha256": actual_sha256,
        },
        "candidate": {"filename": candidate.name, "version": candidate_version},
        "stages": stages,
        "candidate_freeze": candidate_freeze.splitlines(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retain-dir", type=Path)
    parser.add_argument("--upgrade-from", type=Path)
    parser.add_argument("--upgrade-from-sha256")
    args = parser.parse_args()
    if (args.upgrade_from is None) != (args.upgrade_from_sha256 is None):
        parser.error(
            "--upgrade-from and --upgrade-from-sha256 must be supplied together"
        )
    expected_version = _project_version()
    upgrade: dict[str, object] | None = None
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
        wheel_versions = _install_and_smoke(
            wheel, tmp / "wheel-venv", workdir, expected_version
        )
        sdist_versions = _install_and_smoke(
            rebuilt_wheel, tmp / "sdist-venv", workdir, expected_version
        )
        if args.upgrade_from is not None:
            upgrade = _run_upgrade(
                wheel,
                args.upgrade_from.resolve(),
                args.upgrade_from_sha256,
                tmp / "upgrade",
            )
        if args.retain_dir is not None:
            _retain_artifacts(
                args.retain_dir,
                expected_version,
                wheel,
                sdist,
                {"wheel": wheel_versions, "sdist": sdist_versions},
                upgrade,
            )

    print("artifact smoke passed")


if __name__ == "__main__":
    main()
