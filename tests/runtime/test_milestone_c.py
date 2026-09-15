"""Opt-in live contract for the app-owned Milestone C runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import time
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from mlx_tui.comparison import (
    ComparisonInput,
    load_comparison,
    parse_loopback_url,
    run_comparison,
    verify_profile_snapshot,
)
from mlx_tui.comparison_contracts import JSONValue
from mlx_tui.managed import ManagedRuntime, inspect_runtime
from mlx_tui.profiles import ProfileEntry, load_coding_profiles

_PROFILE_IDS = ("qwen3-1.7b-baseline", "qwen3.5-4b-baseline")
_TIER_RE = r"^[a-z0-9][a-z0-9-]*$"
_REQUIRED = (
    "MLX_TUI_C_OUTPUT",
    "MLX_TUI_C_MACHINE_TIER",
    "MLX_TUI_C_RUNTIME_ROOT",
    "MLX_TUI_C_PROFILE_A_PATH",
    "MLX_TUI_C_PROFILE_B_PATH",
)


def _absolute(raw: str, name: str, *, directory: bool = False) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        pytest.fail(f"{name} must be an absolute path")
    resolved = path.resolve(strict=True)
    if directory and not resolved.is_dir():
        pytest.fail(f"{name} must be a directory")
    if not directory and not resolved.is_file():
        pytest.fail(f"{name} must be a file")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="session")
def c_inputs() -> dict[str, Any]:
    if os.environ.get("MLX_TUI_C_QUALIFY") != "1":
        pytest.skip("Milestone C qualification requires MLX_TUI_C_QUALIFY=1")
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.fail(f"missing Milestone C environment: {', '.join(missing)}")
    tier = os.environ["MLX_TUI_C_MACHINE_TIER"]
    if re.fullmatch(_TIER_RE, tier) is None:
        pytest.fail("MLX_TUI_C_MACHINE_TIER must be lowercase slug text")
    output = Path(os.environ["MLX_TUI_C_OUTPUT"]).expanduser()
    if not output.is_absolute():
        pytest.fail("MLX_TUI_C_OUTPUT must be an absolute path")
    output.mkdir(parents=True, exist_ok=True)
    entries = {entry.profile.id: entry for entry in load_coding_profiles()}
    try:
        profiles = tuple(entries[profile_id] for profile_id in _PROFILE_IDS)
    except KeyError as exc:
        pytest.fail(f"missing packaged profile: {exc.args[0]}")
    snapshots = (
        _absolute(
            os.environ["MLX_TUI_C_PROFILE_A_PATH"],
            "MLX_TUI_C_PROFILE_A_PATH",
            directory=True,
        ),
        _absolute(
            os.environ["MLX_TUI_C_PROFILE_B_PATH"],
            "MLX_TUI_C_PROFILE_B_PATH",
            directory=True,
        ),
    )
    for entry, snapshot in zip(profiles, snapshots, strict=True):
        if snapshot.name != entry.profile.revision:
            pytest.fail(f"{entry.profile.id} snapshot must name its pinned revision")
        verify_profile_snapshot(entry, snapshot)
    runtime = _absolute(
        os.environ["MLX_TUI_C_RUNTIME_ROOT"], "MLX_TUI_C_RUNTIME_ROOT", directory=True
    )
    return {
        "output": output.resolve(),
        "tier": tier,
        "profiles": cast(tuple[ProfileEntry, ProfileEntry], profiles),
        "snapshots": snapshots,
        "runtime": runtime,
    }


@pytest.mark.asyncio
async def test_managed_runtime_comparison_contract(c_inputs: dict[str, Any]) -> None:
    """Run both retained orders and prove ordinary cleanup of the owned child."""
    profiles = c_inputs["profiles"]
    snapshots = c_inputs["snapshots"]
    output: Path = c_inputs["output"]
    manager = ManagedRuntime(c_inputs["runtime"], host="127.0.0.1", port=18080)
    records: dict[str, Any] = {
        "schema_version": 1,
        "artifact": {
            "version": "0.2.0",
            "sha256": os.environ.get("MLX_TUI_C_ARTIFACT_SHA256", "unknown"),
        },
        "hardware": {"machine": platform.machine(), "system": platform.platform()},
        "machine_tier": c_inputs["tier"],
        "runtime": inspect_runtime(c_inputs["runtime"]),
        "snapshots": [str(path) for path in snapshots],
        "orders": {},
    }
    try:
        for name, indices in (("a-then-b", (0, 1)), ("b-then-a", (1, 0))):
            started = time.monotonic()
            await asyncio.to_thread(
                manager.start, snapshots[indices[0]], on_line=lambda _line: None
            )
            if manager.identity is None:
                pytest.fail("managed start did not retain a process identity")
            ordered_profiles = tuple(profiles[index] for index in indices)
            ordered_snapshots = tuple(snapshots[index] for index in indices)
            hashes = tuple(
                verify_profile_snapshot(entry, snapshot)
                for entry, snapshot in zip(
                    ordered_profiles, ordered_snapshots, strict=True
                )
            )
            result_path = output / f"{c_inputs['tier']}-{name}-{uuid.uuid4()}.json"
            comparison = ComparisonInput(
                endpoint=parse_loopback_url(
                    "http://127.0.0.1:18080/v1/chat/completions"
                ).url,
                profiles=ordered_profiles,
                snapshot_paths=ordered_snapshots,
                verified_asset_hashes=hashes,
                runtime_evidence=manager.install_evidence,
                install_evidence={"runtime_root": str(manager.root)},
                launch_evidence={
                    "owned": True,
                    "argv": cast(list[JSONValue], list(manager.argv)),
                },
                provenance={"source": "Milestone C managed runtime"},
                process_identity=manager.identity,
                isolation_evidence={"ownership": "TUI-retained child"},
                machine_tier=c_inputs["tier"],
                profile_order=tuple(entry.profile.id for entry in ordered_profiles),
                result_path=result_path,
            )
            result = await run_comparison(comparison)
            assert result.status == "completed", result.error
            assert len(result.trials) == 12
            assert all(trial.quality_pass is True for trial in result.trials)
            assert load_comparison(result_path).run_id == result.run_id
            records["orders"][name] = {
                "result": str(result_path),
                "result_sha256": _sha256(result_path),
                "duration_s": time.monotonic() - started,
                "process_identity": {
                    "pid": manager.identity.pid,
                    "create_time": manager.identity.create_time,
                },
            }
    finally:
        await asyncio.to_thread(manager.close)
    if manager.process is not None or manager.identity is not None:
        pytest.fail("managed child ownership survived close")
    records["cleanup"] = {"owned_process_live": False}
    (output / "milestone-c-record.json").write_text(
        json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
