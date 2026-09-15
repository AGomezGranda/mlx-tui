"""Opt-in real-server qualification for the Milestone B profile pair.

The operator owns the endpoint. These tests only inspect supplied evidence and
run the existing comparison runner; they never start, stop, download, or
reconfigure the server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psutil
import pytest

from mlx_tui import process
from mlx_tui.comparison import (
    ComparisonInput,
    JSONValue,
    load_comparison,
    parse_loopback_url,
    run_comparison,
    verify_profile_snapshot,
)
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import ProfileEntry, load_coding_profiles

_PROFILE_IDS = ("qwen3-1.7b-baseline", "qwen3.5-4b-baseline")
_ORDERS = (("a-then-b", (0, 1)), ("b-then-a", (1, 0)))
_TIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ENV = (
    "MLX_TUI_B_URL",
    "MLX_TUI_B_PROFILE_A_PATH",
    "MLX_TUI_B_PROFILE_B_PATH",
    "MLX_TUI_B_RUNTIME_ENV",
    "MLX_TUI_B_LAUNCH_EVIDENCE",
    "MLX_TUI_B_MACHINE_TIER",
    "MLX_TUI_B_OUTPUT",
)


@dataclass(frozen=True)
class Qualification:
    endpoint: str
    profiles: tuple[ProfileEntry, ProfileEntry]
    snapshots: tuple[Path, Path]
    asset_hashes: tuple[dict[str, str], dict[str, str]]
    runtime_evidence: dict[str, JSONValue]
    install_evidence: dict[str, JSONValue]
    launch_evidence: dict[str, JSONValue]
    provenance: dict[str, JSONValue]
    process_identity: ProcessIdentity
    isolation_evidence: dict[str, JSONValue]
    operator_conditions: dict[str, JSONValue]
    machine_tier: str
    output: Path


def _absolute_path(raw: str, label: str, *, directory: bool = False) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        pytest.fail(f"{label} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        pytest.fail(f"{label} does not exist: {exc}")
    if directory and not resolved.is_dir():
        pytest.fail(f"{label} must be a directory")
    if not directory and not resolved.is_file():
        pytest.fail(f"{label} must be a file")
    return resolved


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        pytest.fail(f"{label} must be valid JSON: {exc}")
    if not isinstance(value, dict):
        pytest.fail(f"{label} must contain a JSON object")
    return value


def _object(value: object, label: str) -> dict[str, JSONValue]:
    if not isinstance(value, dict) or not value:
        pytest.fail(f"{label} must be a non-empty JSON object")
    return cast(dict[str, JSONValue], value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _installed_runtime(
    runtime_env: Path, profiles: tuple[ProfileEntry, ProfileEntry]
) -> dict[str, JSONValue]:
    pins = {
        (entry.profile.runtime_commit, entry.profile.mlx_version) for entry in profiles
    }
    if len(pins) != 1:
        pytest.fail("profile pair does not share one pinned runtime")
    runtime_commit, mlx_version = pins.pop()
    if (
        not (runtime_env / "pyvenv.cfg").is_file()
        or not (runtime_env / "bin" / "mlx_lm.server").is_file()
    ):
        pytest.fail("MLX_TUI_B_RUNTIME_ENV is not an MLX-LM virtual environment")
    site_packages = list(runtime_env.glob("lib/python*/site-packages"))
    direct_urls = [
        path
        for site in site_packages
        for path in site.glob("mlx_lm-*.dist-info/direct_url.json")
    ]
    mlx_metadata = [
        path
        for site in site_packages
        for path in site.glob(f"mlx-{mlx_version}.dist-info/METADATA")
    ]
    if len(direct_urls) != 1 or len(mlx_metadata) != 1:
        pytest.fail(
            "runtime environment does not contain the pinned MLX-LM/MLX install"
        )
    direct_url = _json_object(direct_urls[0], "MLX-LM direct_url.json")
    vcs = direct_url.get("vcs_info")
    if not isinstance(vcs, dict) or vcs.get("commit_id") != runtime_commit:
        pytest.fail("installed MLX-LM commit does not match the profiles")
    return {
        "environment": str(runtime_env),
        "runtime_commit": runtime_commit,
        "mlx_version": mlx_version,
        "direct_url_sha256": _sha256(direct_urls[0]),
    }


def _launch_record(
    path: Path,
    runtime_env: Path,
    endpoint_host: str,
    endpoint_port: int,
    profiles: tuple[ProfileEntry, ProfileEntry],
) -> tuple[
    ProcessIdentity,
    dict[str, JSONValue],
    dict[str, JSONValue],
    dict[str, JSONValue],
    dict[str, JSONValue],
]:
    record = _json_object(path, "MLX_TUI_B_LAUNCH_EVIDENCE")
    if record.get("schema_version") != 1:
        pytest.fail("launch evidence schema_version must be 1")
    identity = process.find_server_process(endpoint_host, endpoint_port)
    if identity is None:
        pytest.fail("the endpoint's MLX server process identity is unavailable")
    if record.get("pid") != identity.pid or record.get("create_time") != pytest.approx(
        identity.create_time
    ):
        pytest.fail("launch evidence does not identify the live endpoint process")
    documented_argv = record.get("argv")
    try:
        live_argv = psutil.Process(identity.pid).cmdline()
    except (psutil.Error, OSError) as exc:
        pytest.fail(f"could not inspect the live endpoint process: {exc}")
    if documented_argv != live_argv:
        pytest.fail("launch evidence argv does not match the live endpoint process")
    runtime_prefix = f"{runtime_env}{os.sep}"
    if not any(argument.startswith(runtime_prefix) for argument in live_argv):
        pytest.fail("live endpoint process does not use MLX_TUI_B_RUNTIME_ENV")
    if record.get("runtime_env") != str(runtime_env):
        pytest.fail("launch evidence runtime_env does not match MLX_TUI_B_RUNTIME_ENV")
    launch_settings = _object(record.get("launch_settings"), "launch_settings")
    expected_settings = profiles[0].profile.launch_settings
    if any(entry.profile.launch_settings != expected_settings for entry in profiles):
        pytest.fail("profile pair does not share one launch-settings contract")
    if launch_settings != expected_settings:
        pytest.fail("launch evidence does not match the pinned launch settings")
    freeze_path = _absolute_path(
        str(record.get("freeze_path", "")), "launch evidence freeze_path"
    )
    freeze_hash = record.get("freeze_sha256")
    if not isinstance(freeze_hash, str) or _HASH_RE.fullmatch(freeze_hash) is None:
        pytest.fail("launch evidence freeze_sha256 must be a SHA-256 hash")
    if _sha256(freeze_path) != freeze_hash:
        pytest.fail("launch evidence freeze hash does not match its file")
    freeze = freeze_path.read_text(encoding="utf-8")
    profile = profiles[0].profile
    if (
        f"mlx=={profile.mlx_version}" not in freeze
        or f"mlx-lm.git@{profile.runtime_commit}" not in freeze
    ):
        pytest.fail("install freeze does not contain the pinned MLX/MLX-LM identities")
    install: dict[str, JSONValue] = {
        "freeze_path": str(freeze_path),
        "freeze_sha256": freeze_hash,
    }
    launch: dict[str, JSONValue] = {
        "argv": cast(list[JSONValue], documented_argv),
        "settings": launch_settings,
    }
    return (
        identity,
        install,
        launch,
        _object(record.get("isolation"), "isolation"),
        _object(record.get("operator_conditions"), "operator_conditions"),
    )


@pytest.fixture(scope="session")
def milestone_b_qualification() -> Qualification:
    if os.environ.get("MLX_TUI_B_QUALIFY") != "1":
        pytest.skip("Milestone B qualification requires MLX_TUI_B_QUALIFY=1")
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        pytest.fail(f"missing Milestone B environment: {', '.join(missing)}")
    values = {name: os.environ[name] for name in _REQUIRED_ENV}
    endpoint = parse_loopback_url(values["MLX_TUI_B_URL"])
    catalogue = {entry.profile.id: entry for entry in load_coding_profiles()}
    try:
        profiles = cast(
            tuple[ProfileEntry, ProfileEntry],
            tuple(catalogue[profile_id] for profile_id in _PROFILE_IDS),
        )
    except KeyError as exc:
        pytest.fail(f"missing packaged Milestone B profile: {exc.args[0]}")
    snapshots = cast(
        tuple[Path, Path],
        tuple(
            _absolute_path(values[name], name, directory=True)
            for name in ("MLX_TUI_B_PROFILE_A_PATH", "MLX_TUI_B_PROFILE_B_PATH")
        ),
    )
    if any(
        path.name != entry.profile.revision
        for entry, path in zip(profiles, snapshots, strict=True)
    ):
        pytest.fail("profile paths must name their pinned snapshot revisions")
    asset_hashes = cast(
        tuple[dict[str, str], dict[str, str]],
        tuple(
            verify_profile_snapshot(entry, path)
            for entry, path in zip(profiles, snapshots, strict=True)
        ),
    )
    runtime_env = _absolute_path(
        values["MLX_TUI_B_RUNTIME_ENV"], "MLX_TUI_B_RUNTIME_ENV", directory=True
    )
    runtime_evidence = _installed_runtime(runtime_env, profiles)
    launch_path = _absolute_path(
        values["MLX_TUI_B_LAUNCH_EVIDENCE"], "MLX_TUI_B_LAUNCH_EVIDENCE"
    )
    identity, install, launch, isolation, conditions = _launch_record(
        launch_path, runtime_env, endpoint.host, endpoint.port, profiles
    )
    machine_tier = values["MLX_TUI_B_MACHINE_TIER"]
    if _TIER_RE.fullmatch(machine_tier) is None:
        pytest.fail("MLX_TUI_B_MACHINE_TIER must be a stable lowercase slug")
    output = Path(values["MLX_TUI_B_OUTPUT"]).expanduser()
    if not output.is_absolute():
        pytest.fail("MLX_TUI_B_OUTPUT must be an absolute path")
    output.mkdir(parents=True, exist_ok=True)
    return Qualification(
        endpoint=endpoint.url,
        profiles=profiles,
        snapshots=snapshots,
        asset_hashes=asset_hashes,
        runtime_evidence=runtime_evidence,
        install_evidence=install,
        launch_evidence=launch,
        provenance={
            "source": "operator-supplied Milestone B qualification fixture",
            "launch_evidence_path": str(launch_path),
            "launch_evidence_sha256": _sha256(launch_path),
        },
        process_identity=identity,
        isolation_evidence=isolation,
        operator_conditions=conditions,
        machine_tier=machine_tier,
        output=output.resolve(),
    )


@pytest.mark.parametrize(
    ("order_name", "indices"), _ORDERS, ids=[name for name, _indices in _ORDERS]
)
async def test_profile_pair_in_both_orders(
    milestone_b_qualification: Qualification,
    order_name: str,
    indices: tuple[int, int],
) -> None:
    qualification = milestone_b_qualification
    profiles = cast(
        tuple[ProfileEntry, ProfileEntry],
        tuple(qualification.profiles[index] for index in indices),
    )
    snapshots = cast(
        tuple[Path, Path], tuple(qualification.snapshots[index] for index in indices)
    )
    hashes = cast(
        tuple[dict[str, str], dict[str, str]],
        tuple(qualification.asset_hashes[index] for index in indices),
    )
    result_path = qualification.output / (
        f"{qualification.machine_tier}-{order_name}-{uuid.uuid4()}.json"
    )
    comparison = ComparisonInput(
        endpoint=qualification.endpoint,
        profiles=profiles,
        snapshot_paths=snapshots,
        verified_asset_hashes=hashes,
        runtime_evidence=qualification.runtime_evidence,
        install_evidence=qualification.install_evidence,
        launch_evidence=qualification.launch_evidence,
        provenance=qualification.provenance,
        process_identity=qualification.process_identity,
        isolation_evidence=qualification.isolation_evidence,
        machine_tier=qualification.machine_tier,
        operator_conditions=qualification.operator_conditions,
        profile_order=(profiles[0].profile.id, profiles[1].profile.id),
        result_path=result_path,
    )
    result = await run_comparison(comparison)
    assert result.status == "completed", result.error
    assert all(trial.state == "completed" for trial in result.trials)
    assert all(trial.quality_pass is True for trial in result.trials)
    assert load_comparison(result_path).run_id == result.run_id
