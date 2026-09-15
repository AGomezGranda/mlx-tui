"""Opt-in final installed-artifact and managed-runtime qualification."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import socket
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

import psutil
import pytest

from mlx_tui.attachments import read_attachment
from mlx_tui.chat import stream_turn
from mlx_tui.comparison import (
    CODING_CHECK_EXPECTED,
    CODING_CHECK_PROMPT,
    assert_coding_check_v1,
    coding_payload,
    verify_profile_snapshot,
)
from mlx_tui.config import AppConfig
from mlx_tui.managed import ManagedRuntime, inspect_runtime
from mlx_tui.models import model_identity_matches, verify_cached_assets
from mlx_tui.profiles import ProfileEntry, load_coding_profiles
from mlx_tui.sessions import (
    RequestSettings,
    SessionTurn,
    load_session,
    new_session,
    save_session,
)

_REQUIRED = (
    "MLX_TUI_F_ARTIFACT",
    "MLX_TUI_F_ARTIFACT_SHA256",
    "MLX_TUI_F_RUNTIME_ROOT",
    "MLX_TUI_F_MODEL_PATH",
    "MLX_TUI_F_MACHINE_TIER",
    "MLX_TUI_F_OUTPUT",
)
_TIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_TIMEOUT_S = 180.0
_CANCEL_WINDOW_S = 0.25
_CANCEL_TIMEOUT_S = 30.0
_MATRIX = {
    "local-m4-16gib": {
        "system": "Darwin",
        "machine": "arm64",
        "macos": "26.6.2",
        "hardware_model": "Mac16,10",
    }
}


@dataclass(frozen=True)
class FInputs:
    artifact: Path
    artifact_sha256: str
    runtime_root: Path
    model_path: Path
    machine_tier: str
    output: Path


@dataclass
class EvidenceRecorder:
    output: Path
    records: list[dict[str, Any]]
    metadata: dict[str, Any]

    def record(
        self,
        scenario: str,
        status: str,
        *,
        started_at: str | None = None,
        **details: Any,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.records.append(
            {
                "scenario": scenario,
                "status": status,
                "started_at": started_at or now,
                "ended_at": now,
                **details,
            }
        )

    def write(self) -> None:
        (self.output / "metadata.json").write_text(
            json.dumps(self.metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (self.output / "results.json").write_text(
            json.dumps(
                {"schema_version": 1, "contracts": self.records},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def _absolute_path(raw: str, name: str, *, directory: bool) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        pytest.fail(f"{name} must be an absolute path")
    if path.is_symlink():
        pytest.fail(f"{name} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        pytest.fail(f"{name} does not exist: {exc}")
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


def _artifact_version(path: Path) -> str:
    if path.suffix != ".whl":
        pytest.fail("MLX_TUI_F_ARTIFACT must be a wheel")
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                pytest.fail("candidate wheel must contain one METADATA file")
            for line in archive.read(metadata_names[0]).decode().splitlines():
                if line.startswith("Version:"):
                    return line.partition(":")[2].strip()
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as exc:
        pytest.fail(f"candidate wheel metadata is unreadable: {exc}")
    pytest.fail("candidate wheel metadata has no Version")
    raise AssertionError("pytest.fail must raise")


def _output_dir(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        pytest.fail("MLX_TUI_F_OUTPUT must be an absolute fresh directory")
    if path.exists() or path.is_symlink():
        pytest.fail("MLX_TUI_F_OUTPUT must not already exist")
    try:
        path.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        pytest.fail(f"could not create MLX_TUI_F_OUTPUT: {exc}")
    return path.resolve()


def _installed_artifact(artifact: Path, expected_sha256: str) -> dict[str, Any]:
    if os.environ.get("PYTHONPATH"):
        pytest.fail("live qualification must run with PYTHONPATH unset")
    package = importlib.util.find_spec("mlx_tui")
    if package is None or package.origin is None:
        pytest.fail("installed mlx_tui package is unavailable")
    package_path = Path(package.origin).resolve()
    if not any(
        part in {"site-packages", "dist-packages"} for part in package_path.parts
    ):
        pytest.fail("mlx_tui was not imported from an installed package directory")
    distribution = importlib.metadata.distribution("mlx-tui")
    direct_url_text = distribution.read_text("direct_url.json")
    if not direct_url_text:
        pytest.fail("installed candidate has no direct artifact identity")
    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError as exc:
        pytest.fail(f"installed candidate direct artifact identity is invalid: {exc}")
    archive_info = direct_url.get("archive_info")
    hashes = archive_info.get("hashes") if isinstance(archive_info, dict) else None
    recorded_hash = hashes.get("sha256") if isinstance(hashes, dict) else None
    if isinstance(archive_info, dict) and recorded_hash is None:
        raw_hash = archive_info.get("hash")
        if isinstance(raw_hash, str) and raw_hash == f"sha256={expected_sha256}":
            recorded_hash = expected_sha256
    if recorded_hash != expected_sha256:
        source_url = direct_url.get("url")
        parsed = urlparse(source_url) if isinstance(source_url, str) else None
        if parsed is not None and parsed.scheme == "file" and parsed.path:
            source = Path(unquote(parsed.path))
            if source.is_file() and _sha256(source) == expected_sha256:
                recorded_hash = expected_sha256
    if recorded_hash != expected_sha256:
        pytest.fail("installed candidate hash does not match MLX_TUI_F_ARTIFACT")
    actual_version = distribution.version
    artifact_version = _artifact_version(artifact)
    if actual_version != artifact_version:
        pytest.fail("installed candidate version does not match artifact metadata")
    dependencies = {
        name: importlib.metadata.version(name)
        for name in ("httpx", "huggingface-hub", "psutil", "textual", "rich")
    }
    return {
        "version": actual_version,
        "sha256": expected_sha256,
        "package_from_site_packages": True,
        "dependencies": dependencies,
    }


def _hardware_model() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.model"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _host_facts() -> dict[str, str]:
    return {
        "system": platform.system(),
        "machine": platform.machine().lower(),
        "macos": platform.mac_ver()[0] or "unknown",
        "hardware_model": _hardware_model(),
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _identity_alive(identity: Any) -> bool:
    if identity is None:
        return False
    try:
        process = psutil.Process(identity.pid)
        return abs(process.create_time() - identity.create_time) <= 2
    except (OSError, psutil.Error):
        return False


def _safe_runtime_evidence(value: dict[str, Any]) -> dict[str, Any]:
    packages = value.get("packages")
    package_versions: dict[str, object] = {}
    if isinstance(packages, dict):
        package_versions = {
            name: packages.get(name) for name in ("mlx", "mlx-metal", "mlx-lm")
        }
    return {
        "python_version": value.get("python_version"),
        "uv_version": value.get("uv_version"),
        "runtime_commit": value.get("runtime_commit"),
        "packages": package_versions,
        "freeze_sha256": value.get("freeze_sha256"),
        "resource_hashes": value.get("resource_hashes"),
    }


def _find_profile(model: Path) -> ProfileEntry:
    matches = [
        entry
        for entry in load_coding_profiles()
        if entry.profile.revision == model.name
        and model_identity_matches(entry.profile.repo_id, model)
    ]
    if len(matches) != 1:
        pytest.fail("model path does not match exactly one packaged profile")
    return matches[0]


def _settings(profile: ProfileEntry, model: Path) -> RequestSettings:
    coding = profile.profile
    return RequestSettings(
        model=str(model),
        repo_id=coding.repo_id,
        revision=coding.revision,
        system=coding.system,
        temperature=coding.temperature,
        top_p=coding.top_p,
        max_tokens=coding.max_tokens,
        max_ctx=coding.max_ctx,
        seed=coding.seed,
        enable_thinking=coding.enable_thinking,
        profile_id=coding.id,
        profile_fingerprint=coding.fingerprint,
    )


async def _tui_turn(app: Any, pilot: Any, text: str) -> dict[str, Any]:
    from mlx_tui.chat_pane import ChatInput, ChatPane  # noqa: PLC0415

    pane = app.query_one("#chat-pane", ChatPane)
    before = len(pane.messages)
    composer = app.query_one("#chat-input", ChatInput)
    composer.text = text
    composer.focus()
    started = time.monotonic()
    await pilot.press("ctrl+enter")
    for _ in range(900):
        if len(pane.messages) >= before + 2:
            break
        await pilot.pause()
        await asyncio.sleep(0.2)
    else:
        raise TimeoutError("TUI turn did not complete")
    return {
        "elapsed_s": time.monotonic() - started,
        "generation_state": app.server_identity.generation_state,
        "response_model": app.server_identity.last_response_model,
    }


def _close_and_record(
    manager: ManagedRuntime, recorder: EvidenceRecorder, scenario: str, port: int
) -> bool:
    started_at = datetime.now(UTC).isoformat()
    identity = manager.identity
    manager.close()
    process_exited = not _identity_alive(identity)
    port_released = not _port_open(port)
    clean = process_exited and port_released
    recorder.record(
        scenario,
        "supported" if clean else "failed",
        started_at=started_at,
        process_exited=process_exited,
        port_released=port_released,
        pid=identity.pid if identity is not None else None,
        create_time=identity.create_time if identity is not None else None,
    )
    return clean


@pytest.fixture(scope="session")
def f_inputs() -> FInputs:
    if os.environ.get("MLX_TUI_F_QUALIFY") != "1":
        pytest.skip("Milestone F qualification requires MLX_TUI_F_QUALIFY=1")
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.fail(f"missing Milestone F environment: {', '.join(missing)}")
    artifact = _absolute_path(
        os.environ["MLX_TUI_F_ARTIFACT"], "MLX_TUI_F_ARTIFACT", directory=False
    )
    expected_sha256 = os.environ["MLX_TUI_F_ARTIFACT_SHA256"]
    if _SHA256_RE.fullmatch(expected_sha256) is None:
        pytest.fail("MLX_TUI_F_ARTIFACT_SHA256 must be lowercase SHA-256")
    if _sha256(artifact) != expected_sha256:
        pytest.fail("MLX_TUI_F_ARTIFACT_SHA256 does not match artifact bytes")
    runtime_root = _absolute_path(
        os.environ["MLX_TUI_F_RUNTIME_ROOT"],
        "MLX_TUI_F_RUNTIME_ROOT",
        directory=True,
    )
    model_path = _absolute_path(
        os.environ["MLX_TUI_F_MODEL_PATH"], "MLX_TUI_F_MODEL_PATH", directory=True
    )
    tier = os.environ["MLX_TUI_F_MACHINE_TIER"]
    if _TIER_RE.fullmatch(tier) is None:
        pytest.fail("MLX_TUI_F_MACHINE_TIER must be lowercase slug text")
    return FInputs(
        artifact=artifact,
        artifact_sha256=expected_sha256,
        runtime_root=runtime_root,
        model_path=model_path,
        machine_tier=tier,
        output=_output_dir(os.environ["MLX_TUI_F_OUTPUT"]),
    )


@pytest.mark.asyncio
async def test_milestone_f_managed_release_contract(  # noqa: PLR0912, PLR0915
    f_inputs: FInputs, tmp_path: Path
) -> None:  # noqa: PLR0912, PLR0915
    recorder = EvidenceRecorder(
        f_inputs.output,
        [],
        {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "artifact_sha256": f_inputs.artifact_sha256,
            "machine_tier": f_inputs.machine_tier,
        },
    )
    manager: ManagedRuntime | None = None
    generation_manager: ManagedRuntime | None = None
    completed = False
    cleanup_failed = False
    try:
        artifact = _installed_artifact(f_inputs.artifact, f_inputs.artifact_sha256)
        host = _host_facts()
        expected_host = _MATRIX.get(f_inputs.machine_tier)
        if expected_host != host:
            pytest.fail("host facts do not match the declared support matrix row")
        profile = _find_profile(f_inputs.model_path)
        asset_hashes = verify_profile_snapshot(profile, f_inputs.model_path)
        verify_cached_assets(f_inputs.model_path)
        runtime = inspect_runtime(f_inputs.runtime_root)
        recorder.metadata.update(
            {
                "artifact_version": artifact["version"],
                "artifact_dependencies": artifact["dependencies"],
                "runtime": _safe_runtime_evidence(runtime),
                "profile_id": profile.profile.id,
                "profile_revision": profile.profile.revision,
            }
        )
        recorder.record(
            "preflight",
            "supported",
            artifact=artifact,
            host=host,
            machine_tier=f_inputs.machine_tier,
            profile={
                "id": profile.profile.id,
                "revision": profile.profile.revision,
                "fingerprint": profile.profile.fingerprint,
                "template_sha256": profile.profile.template_sha256,
                "asset_names": sorted(asset_hashes),
            },
            runtime=_safe_runtime_evidence(runtime),
        )

        before_start_port = _free_port()
        before_start = ManagedRuntime(
            f_inputs.runtime_root, host="127.0.0.1", port=before_start_port
        )
        if not _close_and_record(
            before_start, recorder, "shutdown_before_start", before_start_port
        ):
            pytest.fail("before-start managed cleanup did not release its port")

        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
            monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
            monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
            monkeypatch.setenv("HOME", str(tmp_path / "home"))

            port = _free_port()
            manager = ManagedRuntime(f_inputs.runtime_root, host="127.0.0.1", port=port)
            await asyncio.to_thread(
                manager.start, f_inputs.model_path, on_line=lambda _line: None
            )
            identity = manager.identity
            if identity is None:
                pytest.fail("managed runtime did not retain child identity")
            recorder.record(
                "managed_start_idle",
                "supported",
                pid=identity.pid,
                create_time=identity.create_time,
                port=port,
                listener_owned=manager.listener_matches_child(),
            )

            endpoint = f"http://127.0.0.1:{port}/v1/chat/completions"
            fixed = await asyncio.wait_for(
                stream_turn(
                    endpoint,
                    cast(
                        dict[str, object], coding_payload(profile, f_inputs.model_path)
                    ),
                    prompt_estimate=1,
                    on_flush=lambda _text: None,
                ),
                timeout=_REQUEST_TIMEOUT_S,
            )
            assert_coding_check_v1(fixed, str(f_inputs.model_path.resolve()))
            recorder.record(
                "chat_fixed_coding_check",
                "supported",
                response_model_matches=(
                    fixed.response_model == str(f_inputs.model_path.resolve())
                ),
                stream_complete=fixed.stream_complete,
            )

            from mlx_tui.app import MlxTuiApp  # noqa: PLC0415

            coding = profile.profile
            app_config = AppConfig(
                model=str(f_inputs.model_path),
                host="127.0.0.1",
                port=port,
                runtime_mode="attach",
                temperature=coding.temperature,
                top_p=coding.top_p,
                max_tokens=coding.max_tokens,
                max_ctx=coding.max_ctx,
                seed=coding.seed,
                enable_thinking=coding.enable_thinking,
                system=coding.system,
            )
            app = MlxTuiApp(host="127.0.0.1", port=port, config=app_config)
            async with app.run_test() as pilot:
                await pilot.pause()
                from mlx_tui.chat_pane import ChatInput, ChatPane  # noqa: PLC0415

                pane = app.query_one("#chat-pane", ChatPane)
                composer = app.query_one("#chat-input", ChatInput)
                composer.text = "F cancellation: produce a long answer"
                composer.focus()
                await pilot.press("ctrl+enter")
                await asyncio.sleep(_CANCEL_WINDOW_S)
                await pilot.press("escape")
                for _ in range(150):
                    if app.server_identity.generation_state in {
                        "client_cancelled",
                        "failed",
                        "succeeded",
                    }:
                        break
                    await pilot.pause()
                    await asyncio.sleep(0.2)
                else:
                    pytest.fail("TUI cancellation did not reach a terminal state")
                recorder.record(
                    "chat_client_cancellation",
                    "supported",
                    generation_state=app.server_identity.generation_state,
                    engine_cancellation="unknown",
                )
                recovery = await _tui_turn(app, pilot, "F recovery: reply with OK")
                assert recovery["generation_state"] == "succeeded"
                assert recovery["response_model"] == str(f_inputs.model_path.resolve())
                assert len(pane.messages) >= 2
                recorder.record(
                    "chat_recovery",
                    "supported",
                    elapsed_s=recovery["elapsed_s"],
                    generation_state=recovery["generation_state"],
                    response_model_matches=True,
                )

            if not _close_and_record(manager, recorder, "shutdown_idle", port):
                pytest.fail("idle managed cleanup did not release its child and port")
            manager = None

            generation_port = _free_port()
            generation_manager = ManagedRuntime(
                f_inputs.runtime_root, host="127.0.0.1", port=generation_port
            )
            await asyncio.to_thread(
                generation_manager.start,
                f_inputs.model_path,
                on_line=lambda _line: None,
            )
            long_payload = cast(
                dict[str, object],
                {
                    **coding_payload(profile, f_inputs.model_path),
                    "messages": [
                        {
                            "role": "user",
                            "content": "Write a detailed explanation of every Python feature you know.",
                        }
                    ],
                    "max_tokens": 1024,
                },
            )
            generation_task = asyncio.create_task(
                stream_turn(
                    f"http://127.0.0.1:{generation_port}/v1/chat/completions",
                    long_payload,
                    prompt_estimate=1,
                    on_flush=lambda _text: None,
                )
            )
            await asyncio.sleep(_CANCEL_WINDOW_S)
            active = not generation_task.done()
            if not _close_and_record(
                generation_manager,
                recorder,
                "shutdown_during_generation",
                generation_port,
            ):
                pytest.fail("generation-time managed cleanup did not release ownership")
            if active:
                try:
                    await asyncio.wait_for(generation_task, timeout=_CANCEL_TIMEOUT_S)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                recorder.record(
                    "generation_request_after_shutdown",
                    "unknown",
                    engine_cancellation="unknown",
                )
            else:
                recorder.record(
                    "generation_request_after_shutdown",
                    "unknown",
                    engine_cancellation="unknown",
                    note="request completed before shutdown observation",
                )
            generation_manager = None

            attachment_file = tmp_path / "milestone-f-attachment.txt"
            original_attachment = "milestone F immutable attachment\n"
            attachment_file.write_text(original_attachment, encoding="utf-8")
            attachment = read_attachment(attachment_file)
            settings = _settings(profile, f_inputs.model_path)
            session = new_session(settings, draft="saved draft")
            turn = SessionTurn(
                turn_id=str(uuid.uuid4()),
                created_at=datetime.now(UTC).isoformat(),
                original_draft="saved draft",
                sent_content=CODING_CHECK_PROMPT,
                settings=settings,
                attachments=(attachment,),
                answer=CODING_CHECK_EXPECTED,
                finish_reason="stop",
                stream_complete=True,
                outcome="success",
            )
            session = replace(
                session,
                attachments=(attachment,),
                attempts=(turn,),
            )
            session_path = save_session(session)
            attachment_file.write_text("changed after save\n", encoding="utf-8")
            reopened = load_session(session_path)
            assert reopened == session
            assert reopened.attachments[0].content == original_attachment
            assert reopened.attempts[0].attachments[0].content == original_attachment
            recorder.record(
                "session_reopen",
                "supported",
                acknowledged_attempts=1,
                attachment_snapshot_preserved=True,
            )
        finally:
            monkeypatch.undo()
        completed = True
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                cleanup_failed = True
        if generation_manager is not None:
            try:
                generation_manager.close()
            except Exception:
                cleanup_failed = True
        if not completed:
            recorder.record("qualification", "failed", reason="contract failed")
        if cleanup_failed:
            recorder.record("final_cleanup", "failed", reason="close raised")
        recorder.write()
    if cleanup_failed:
        pytest.fail("managed cleanup raised during final qualification cleanup")
