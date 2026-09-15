"""Opt-in Milestone E qualification: TUI plus real external clients.

The operator owns the endpoint. These tests never start, stop, download,
or reconfigure the server and never install an environment. Without
``MLX_TUI_E_QUALIFY=1`` every test skips; with opt-in, missing or invalid
inputs fail. OpenCode coverage needs the extra ``MLX_TUI_E_OPENCODE=1``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
from tests.runtime.conftest import EvidenceRecorder

from mlx_tui import process
from mlx_tui.comparison import parse_loopback_url, verify_profile_snapshot
from mlx_tui.managed import inspect_runtime
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import ProfileEntry, load_coding_profiles

_PROFILE_ID = "qwen3-1.7b-baseline"
_OPENCODE_VERSION = "1.18.28"
_TIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REQUIRED = (
    "MLX_TUI_E_URL",
    "MLX_TUI_E_MODEL",
    "MLX_TUI_E_RUNTIME_ROOT",
    "MLX_TUI_E_MACHINE_TIER",
    "MLX_TUI_E_OUTPUT",
)
_CYCLES = 5
_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=5.0)
_TUI_DEADLINE_S = 180.0
_EXT_DEADLINE_S = 120.0
_OPENCODE_DEADLINE_S = 180.0
_OPENCODE_OUTPUT_LIMIT = 256_000
_FIXTURE_NAME = "opencode-fixture.txt"
_FIXTURE_CONTENT = "mlx-tui milestone E harmless fixture: orchard sparrow 42\n"
_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def _absolute_dir(raw: str, name: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        pytest.fail(f"{name} must be an absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        pytest.fail(f"{name} does not exist: {exc}")
    if not resolved.is_dir():
        pytest.fail(f"{name} must be a directory")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload(model: str, prompt: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 32,
        "temperature": 0,
        "seed": 7,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    payload.update(extra)
    return payload


@dataclass(frozen=True)
class EInputs:
    endpoint_url: str
    host: str
    port: int
    base_v1: str
    snapshot: Path
    profile: ProfileEntry
    asset_hashes: dict[str, str]
    runtime_evidence: dict[str, Any]
    runtime_root: Path
    tier: str
    output: Path


@pytest.fixture(scope="session")
def e_inputs() -> EInputs:
    if os.environ.get("MLX_TUI_E_QUALIFY") != "1":
        pytest.skip("Milestone E qualification requires MLX_TUI_E_QUALIFY=1")
    missing = [name for name in _REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.fail(f"missing Milestone E environment: {', '.join(missing)}")
    env = {name: os.environ[name] for name in _REQUIRED}
    try:
        endpoint = parse_loopback_url(env["MLX_TUI_E_URL"])
    except ValueError as exc:
        pytest.fail(f"MLX_TUI_E_URL is not a loopback chat URL: {exc}")
    rendered = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
    base_v1 = f"http://{rendered}:{endpoint.port}/v1"
    snapshot = _absolute_dir(env["MLX_TUI_E_MODEL"], "MLX_TUI_E_MODEL")
    catalogue = {entry.profile.id: entry for entry in load_coding_profiles()}
    if _PROFILE_ID not in catalogue:
        pytest.fail(f"missing packaged profile: {_PROFILE_ID}")
    entry = catalogue[_PROFILE_ID]
    if snapshot.name != entry.profile.revision:
        pytest.fail("MLX_TUI_E_MODEL must name its pinned revision")
    try:
        hashes = verify_profile_snapshot(entry, snapshot)
    except ValueError as exc:
        pytest.fail(f"pinned snapshot verification failed: {exc}")
    runtime_root = _absolute_dir(
        env["MLX_TUI_E_RUNTIME_ROOT"], "MLX_TUI_E_RUNTIME_ROOT"
    )
    try:
        runtime_evidence = inspect_runtime(runtime_root)
    except ValueError as exc:
        pytest.fail(f"runtime inspection failed: {exc}")
    tier = env["MLX_TUI_E_MACHINE_TIER"]
    if _TIER_RE.fullmatch(tier) is None:
        pytest.fail("MLX_TUI_E_MACHINE_TIER must be a lowercase slug")
    output = Path(env["MLX_TUI_E_OUTPUT"]).expanduser()
    if not output.is_absolute():
        pytest.fail("MLX_TUI_E_OUTPUT must be an absolute path")
    output.mkdir(parents=True, exist_ok=True)
    return EInputs(
        endpoint_url=endpoint.url,
        host=endpoint.host,
        port=endpoint.port,
        base_v1=base_v1,
        snapshot=snapshot,
        profile=entry,
        asset_hashes=hashes,
        runtime_evidence=runtime_evidence,
        runtime_root=runtime_root,
        tier=tier,
        output=output.resolve(),
    )


def _new_recorder(e_inputs: EInputs, name: str) -> EvidenceRecorder:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = e_inputs.output / f"milestone-e-{name}-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "endpoint": e_inputs.endpoint_url,
        "model": str(e_inputs.snapshot),
        "profile": _PROFILE_ID,
        "profile_revision": e_inputs.profile.profile.revision,
        "machine_tier": e_inputs.tier,
        "runtime_root": str(e_inputs.runtime_root),
        "note": "request-echoed model is not independent residency proof",
    }
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return EvidenceRecorder(run_dir)


def _binding_error(  # noqa: PLR0911
    identity: ProcessIdentity | None,
    live_argv: list[str],
    live_create_time: float,
    runtime_root: Path,
    snapshot: Path,
) -> str | None:
    """Return why a listener cannot qualify, or None when it qualifies."""
    if identity is None:
        return "no verified mlx server listener on the endpoint"
    if abs(live_create_time - identity.create_time) > 2:
        return "live PID create time does not match the listener identity"
    try:
        interpreter = Path(live_argv[0]).resolve()
    except (IndexError, OSError):
        return "live argv has no resolvable interpreter"
    try:
        interpreter.relative_to(runtime_root.resolve())
    except ValueError:
        return "live interpreter is not under MLX_TUI_E_RUNTIME_ROOT"
    if not any(token.endswith("mlx_lm.server") for token in live_argv):
        return "live process argv is not mlx_lm.server"
    if "--model" in live_argv:
        model_arg = live_argv[live_argv.index("--model") + 1]
    else:
        model_arg = next(
            (
                token.split("=", 1)[1]
                for token in live_argv
                if token.startswith("--model=")
            ),
            "",
        )
    if model_arg != str(snapshot):
        return "live --model argument does not equal the verified snapshot"
    return None


def _check_listener(e_inputs: EInputs) -> tuple[ProcessIdentity, list[str]]:
    identity = process.find_server_process(e_inputs.host, e_inputs.port)
    if identity is None:
        pytest.fail("the endpoint's MLX server process identity is unavailable")
    assert identity is not None
    try:
        live = psutil.Process(identity.pid)
        live_argv, live_create = live.cmdline(), live.create_time()
    except (psutil.Error, OSError) as exc:
        pytest.fail(f"could not inspect the live endpoint process: {exc}")
        raise AssertionError("pytest.fail must raise")
    error = _binding_error(
        identity, live_argv, live_create, e_inputs.runtime_root, e_inputs.snapshot
    )
    if error is not None:
        pytest.fail(f"live listener cannot qualify: {error}")
    return identity, live_argv


def test_preflight_binds_listener(e_inputs: EInputs) -> None:
    recorder = _new_recorder(e_inputs, "preflight")
    try:
        identity, live_argv = _check_listener(e_inputs)
        recorder.record(
            "listener_binding",
            status="supported",
            pid=identity.pid,
            argv=live_argv,
            model=str(e_inputs.snapshot),
            runtime_root=str(e_inputs.runtime_root),
        )
    finally:
        recorder.write()


def test_preflight_rejects_unrelated_root_or_model(e_inputs: EInputs) -> None:
    identity, live_argv = _check_listener(e_inputs)
    try:
        live_create = psutil.Process(identity.pid).create_time()
    except (psutil.Error, OSError) as exc:
        pytest.fail(f"could not inspect the live endpoint process: {exc}")
        raise AssertionError("pytest.fail must raise")
    bogus_root = Path("/definitely/not-an-mlx-tui-runtime-root")
    bogus_model = Path("/definitely/not-an-mlx-tui-model")
    assert (
        _binding_error(identity, live_argv, live_create, bogus_root, e_inputs.snapshot)
        is not None
    ), "unrelated runtime root must not qualify the listener"
    assert (
        _binding_error(
            identity, live_argv, live_create, e_inputs.runtime_root, bogus_model
        )
        is not None
    ), "unrelated model must not qualify the listener"
    assert (
        _binding_error(
            None, live_argv, live_create, e_inputs.runtime_root, e_inputs.snapshot
        )
        is not None
    ), "a missing listener identity must not qualify"


async def _external_post(
    url: str, payload: dict[str, Any], *, timeout_s: float = 60.0
) -> dict[str, Any]:
    started = time.monotonic()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=5.0)
    ) as client:
        response = await client.post(url, json=payload)
    try:
        echoed = response.json().get("model") if response.status_code == 200 else None
    except ValueError:
        echoed = None
    return {
        "status_code": response.status_code,
        "body": response.text[:4000],
        "model": echoed,
        "elapsed_s": time.monotonic() - started,
    }


async def _external_stream(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    first_s: float | None = None
    chunks = 0
    done = False
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        async with client.stream(
            "POST", url, json={**payload, "stream": True}
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line.removeprefix("data:").strip()
                if raw == "[DONE]":
                    done = True
                    break
                chunks += 1
                if first_s is None:
                    first_s = time.monotonic() - started
    return {
        "chunks": chunks,
        "done": done,
        "first_output_s": first_s,
        "total_s": time.monotonic() - started,
    }


async def _tui_turn(
    app: Any,
    pilot: Any,
    text: str,
    *,
    deadline_s: float = _TUI_DEADLINE_S,
    on_submit: Any = None,
) -> dict[str, Any]:
    from mlx_tui.chat_pane import ChatInput, ChatPane  # noqa: PLC0415

    pane = app.query_one("#chat-pane", ChatPane)
    before = len(pane.messages)
    selected_before = app.effective_model()
    composer = app.query_one("#chat-input", ChatInput)
    composer.text = text
    composer.focus()
    started = time.monotonic()
    await pilot.press("ctrl+enter")
    if on_submit is not None:
        on_submit()
    for _ in range(int(deadline_s * 5) + 1):
        if len(pane.messages) >= before + 2:
            break
        await asyncio.sleep(0.2)
    else:
        raise TimeoutError(f"TUI turn did not complete in {deadline_s}s")
    total_s = time.monotonic() - started
    return {
        "total_s": total_s,
        "messages": len(pane.messages),
        "selected_before": selected_before,
        "selected_after": app.effective_model(),
        "generation_state": app.server_identity.generation_state,
        "last_response_model": app.server_identity.last_response_model,
    }


def _intervals_overlap(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


async def test_mixed_tui_and_external_cycles(
    e_inputs: EInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlx_tui.app import MlxTuiApp  # noqa: PLC0415
    from mlx_tui.config import AppConfig  # noqa: PLC0415

    recorder = _new_recorder(e_inputs, "mixed")
    try:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        model = str(e_inputs.snapshot)
        _check_listener(e_inputs)
        app = MlxTuiApp(
            host=e_inputs.host,
            port=e_inputs.port,
            config=AppConfig(model=model, runtime_mode="attach"),
        )
        prompts: list[tuple[str, str, dict[str, Any]]] = [
            ("non-stream", "Reply with exactly: pinecone", {"stream": False}),
            (
                "sse",
                "Count to three, one per line",
                {"stream": True, "stream_options": {"include_usage": True}},
            ),
            ("longer", "List five short fruit names", {"max_tokens": 128}),
            ("non-stream-2", "Reply with exactly: harbor", {}),
            ("sse-2", "Say hello in one short sentence", {}),
        ]
        async with app.run_test() as pilot:
            await pilot.pause()
            for cycle, (kind, text, extra) in enumerate(prompts):
                tui_event = asyncio.Event()
                ext_event = asyncio.Event()

                async def _tui_cycle() -> dict[str, Any]:
                    return await _tui_turn(
                        app,
                        pilot,
                        f"E cycle {cycle} ({kind}): {text}",
                        on_submit=tui_event.set,
                    )

                async def _ext_cycle() -> dict[str, Any]:
                    started = time.monotonic()
                    if kind.startswith("sse"):
                        result = await _external_stream(
                            e_inputs.endpoint_url,
                            _payload(model, f"E external {cycle}: {text}", **extra),
                        )
                        result["elapsed_s"] = result["total_s"]
                        if result["first_output_s"] is not None:
                            ext_event.set()
                    else:
                        result = await _external_post(
                            e_inputs.endpoint_url,
                            _payload(model, f"E external {cycle}: {text}", **extra),
                        )
                        if result["status_code"] == 200:
                            ext_event.set()
                    result["started_s"] = started
                    result["ended_s"] = time.monotonic()
                    return result

                tui_started = time.monotonic()
                async with asyncio.timeout(_TUI_DEADLINE_S + _EXT_DEADLINE_S):
                    tui_outcome, ext_outcome = await asyncio.gather(
                        asyncio.create_task(_tui_cycle()),
                        asyncio.create_task(_ext_cycle()),
                    )
                tui_ended = time.monotonic()
                assert tui_event.is_set() and ext_event.is_set()
                assert _intervals_overlap(
                    (tui_started, tui_ended),
                    (
                        float(ext_outcome["started_s"]),
                        float(ext_outcome["ended_s"]),
                    ),
                ), f"cycle {cycle}: no observed request overlap"
                assert tui_outcome["selected_after"] == model
                assert tui_outcome["generation_state"] == "succeeded"
                recorder.record(
                    "mixed_cycle",
                    status="supported",
                    cycle=cycle,
                    kind=kind,
                    check_id=f"mixed-{kind}-v1",
                    tui=tui_outcome,
                    external=ext_outcome,
                    overlap=True,
                )
        recorder.record("mixed_complete", status="supported", cycles=_CYCLES)
    finally:
        recorder.write()


async def test_external_cancel_before_and_after_first_output(
    e_inputs: EInputs,
) -> None:
    recorder = _new_recorder(e_inputs, "cancel-external")
    try:
        model = str(e_inputs.snapshot)
        url = e_inputs.endpoint_url
        for attempt, consume_output in (("before-first", False), ("after-first", True)):
            started = time.monotonic()
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    url,
                    json={
                        **_payload(model, "Count slowly from one to fifty"),
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    first_line: str | None = None
                    if consume_output:
                        async for line in response.aiter_lines():
                            first_line = line
                            break
            close_s = time.monotonic() - started
            recovery = await _external_post(url, _payload(model, "Reply OK"))
            assert recovery["status_code"] == 200
            recorder.record(
                "external_cancel",
                status="supported",
                attempt=attempt,
                first_line=first_line,
                client_close_s=close_s,
                recovery=recovery,
                cancellation="unknown; client close and recovery "
                "do not prove engine cancellation",
            )
    finally:
        recorder.write()


async def test_tui_cancel_and_recovery(
    e_inputs: EInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlx_tui.app import MlxTuiApp  # noqa: PLC0415
    from mlx_tui.chat_pane import ChatInput, ChatPane  # noqa: PLC0415
    from mlx_tui.config import AppConfig  # noqa: PLC0415

    recorder = _new_recorder(e_inputs, "cancel-tui")
    try:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        model = str(e_inputs.snapshot)
        app = MlxTuiApp(
            host=e_inputs.host,
            port=e_inputs.port,
            config=AppConfig(model=model, runtime_mode="attach"),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            pane = app.query_one("#chat-pane", ChatPane)
            composer = app.query_one("#chat-input", ChatInput)
            draft = "E cancellation draft: slow count to fifty please"
            composer.text = draft
            composer.focus()
            await pilot.press("ctrl+enter")
            await asyncio.sleep(1.0)
            await pilot.press("escape")
            for _ in range(200):
                await pilot.pause()
                state = app.server_identity.generation_state
                if state in {"client_cancelled", "failed", "succeeded"}:
                    break
                await asyncio.sleep(0.05)
            async with asyncio.timeout(_TUI_DEADLINE_S):
                outcome = await _tui_turn(app, pilot, "E recovery: reply OK")
            assert outcome["generation_state"] == "succeeded"
            assert outcome["selected_after"] == model
            assert len(pane.messages) >= 2
            recorder.record(
                "tui_cancel_recovery",
                status="supported",
                recovery=outcome,
                cancellation="unknown; draft restore and recovery "
                "do not prove engine cancellation",
            )
    finally:
        recorder.write()


async def test_error_and_wrong_target_then_recovery(e_inputs: EInputs) -> None:
    recorder = _new_recorder(e_inputs, "errors")
    try:
        model = str(e_inputs.snapshot)
        url = e_inputs.endpoint_url
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            malformed = await client.post(
                url,
                content=b"{not-json",
                headers={"Content-Type": "application/json"},
            )
            recorder.record(
                "malformed_json",
                status="supported" if malformed.status_code >= 400 else "unavailable",
                http_status=malformed.status_code,
                body=malformed.text[:2000],
            )
            invalid = _payload(model, "x", max_tokens=1, top_p=2.0)
            try:
                invalid_response = await client.post(url, json=invalid)
                invalid_outcome: dict[str, Any] = {
                    "http_status": invalid_response.status_code,
                    "body": invalid_response.text[:2000],
                }
            except httpx.RemoteProtocolError as exc:
                invalid_outcome = {"protocol_disconnect": str(exc)}
            recorder.record("invalid_sampler", status="supported", **invalid_outcome)
            missing = dict(_payload(model, "Reply OK", max_tokens=8))
            missing.pop("model")
            missing_response = await client.post(url, json=missing)
            recorder.record(
                "missing_model",
                status="supported",
                http_status=missing_response.status_code,
                body=missing_response.text[:2000],
            )
            bogus = "/definitely/missing-mlx-tui-e-model"
            wrong_target_statuses: list[dict[str, Any]] = []
            for extra in (
                {"model": bogus},
                {"model": model, "adapters": [bogus]},
                {"model": model, "draft_model": bogus},
            ):
                probe_payload = _payload(model, "x", max_tokens=1)
                probe_payload.update(extra)
                response = await client.post(url, json=probe_payload)
                wrong_target_statuses.append(
                    {"request": extra, "http_status": response.status_code}
                )
            enforcement = (
                "failed/unavailable: upstream accepts per-request "
                "model/draft_model/adapters, so a 404 for a nonexistent "
                "target does not prove isolation"
            )
            recorder.record(
                "wrong_target_enforcement",
                status="failed/unavailable",
                attempts=wrong_target_statuses,
                note=enforcement,
            )
        recovery = await _external_post(url, _payload(model, "Reply OK"))
        assert recovery["status_code"] == 200
        recorder.record("error_recovery", status="supported", recovery=recovery)
    finally:
        recorder.write()


async def test_structured_tool_round_trip(e_inputs: EInputs) -> None:
    recorder = _new_recorder(e_inputs, "tools")
    try:
        model = str(e_inputs.snapshot)
        url = e_inputs.endpoint_url
        tool_prompt = "What is the weather in Madrid? Use the get_weather tool."
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            plain = await client.post(
                url,
                json=_payload(
                    model,
                    tool_prompt,
                    max_tokens=96,
                    tools=[_TOOL_SCHEMA],
                    tool_choice="required",
                ),
            )
            if plain.status_code != 200:
                recorder.record(
                    "structured_tool",
                    status="failed",
                    http_status=plain.status_code,
                    body=plain.text[:2000],
                )
                plain.raise_for_status()
            message = plain.json()["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if not calls:
                recorder.record(
                    "structured_tool",
                    status="unavailable",
                    note="model returned text instead of tool_calls; "
                    "tool-dependent coding-app qualification stays blocked",
                    response=plain.json(),
                )
                return
            call = calls[0]
            function = call.get("function", {})
            assert function.get("name") == "get_weather"
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except ValueError:
                recorder.record(
                    "structured_tool",
                    status="failed",
                    note="tool arguments were not JSON",
                    call=call,
                )
                pytest.fail("tool arguments were not JSON")
            assert arguments == {"city": "Madrid"}
            call_id = call.get("id")
            assert isinstance(call_id, str) and call_id
            follow_up = await client.post(
                url,
                json={
                    **_payload(model, tool_prompt, max_tokens=96),
                    "messages": [
                        {"role": "user", "content": tool_prompt},
                        {"role": "assistant", "tool_calls": calls},
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": '{"city": "Madrid", "temp_c": 21}',
                        },
                    ],
                },
            )
            follow_up.raise_for_status()
            final_text = follow_up.json()["choices"][0]["message"]["content"]
            assert isinstance(final_text, str) and final_text.strip()
            streamed = await _external_stream(
                url,
                _payload(
                    model,
                    tool_prompt,
                    max_tokens=96,
                    tools=[_TOOL_SCHEMA],
                    tool_choice="required",
                ),
            )
            recorder.record(
                "structured_tool",
                status="supported",
                call_id=call_id,
                arguments=arguments,
                final_answer_chars=len(final_text),
                streamed_fragments=streamed,
                note="model-written code or shell was never executed",
            )
    finally:
        recorder.write()


_INJECTION_ENV = (
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    "OPENCODE_CONFIG_CONTENT",
    "OPENCODE_TUI_CONFIG",
    "OPENCODE_AUTO_SHARE",
)
_CREDENTIAL_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_",
    "GEMINI_",
    "GOOGLE_",
    "AZURE_",
    "AWS_",
    "VERTEX_",
)


def _opencode_bin() -> str:
    override = os.environ.get("MLX_TUI_E_OPENCODE_BIN", "").strip()
    candidates: list[str] = [override] if override else []
    candidates.extend(["opencode"])
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    pytest.fail("opencode executable not found for Milestone E qualification")
    raise AssertionError("pytest.fail must raise")


def _check_opencode_version(exe: str) -> str:
    try:
        completed = subprocess.run(
            [exe, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f"could not run opencode --version: {exc}")
        raise AssertionError("pytest.fail must raise")
    version = (completed.stdout or completed.stderr or "").strip()
    if _OPENCODE_VERSION not in version:
        pytest.fail(f"opencode {_OPENCODE_VERSION} required; found: {version!r}")
    return version


def _scrubbed_env(isolation: Path, config_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    removed: list[str] = []
    for name in _INJECTION_ENV:
        if env.pop(name, None) is not None:
            removed.append(name)
    for name in list(env):
        if name.startswith("OPENCODE_") or name.startswith(_CREDENTIAL_PREFIXES):
            removed.append(name)
            env.pop(name)
    env["XDG_CONFIG_HOME"] = str(isolation / "config")
    env["XDG_DATA_HOME"] = str(isolation / "data")
    env["XDG_STATE_HOME"] = str(isolation / "state")
    env["XDG_CACHE_HOME"] = str(isolation / "cache")
    env["HOME"] = str(isolation / "home")
    env["OPENCODE_CONFIG"] = str(config_path)
    env["NO_COLOR"] = "1"
    for key in (
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "HOME",
    ):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    env["_MLX_TUI_E_SCRUBBED"] = ",".join(sorted(set(removed)))
    return env


def _write_opencode_isolation(
    e_inputs: EInputs, isolation: Path
) -> tuple[Path, Path, dict[str, Any]]:
    workdir = isolation / "coding-dir"
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / _FIXTURE_NAME
    fixture.write_text(_FIXTURE_CONTENT, encoding="utf-8")
    model_id = "mlx-tui-local/local"
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "mlx-tui-local": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "mlx-tui-local",
                "options": {"baseURL": e_inputs.base_v1},
                "models": {"local": {"name": str(e_inputs.snapshot)}},
            }
        },
        "model": model_id,
        "small_model": model_id,
        "enabled_providers": ["mlx-tui-local"],
        "share": "disabled",
        "permission": {
            "*": "deny",
            "read": {"*": "deny", str(fixture): "allow"},
            "edit": "deny",
            "bash": "deny",
            "glob": "deny",
            "grep": "deny",
            "task": "deny",
            "skill": "deny",
            "question": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "external_directory": {"*": "deny"},
            "doom_loop": "deny",
        },
        "mcp": {},
    }
    config_path = isolation / "opencode.json"
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return workdir, fixture, config


def _walk_json(value: Any) -> Any:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


_KNOWN_TOOLS = {
    "read",
    "edit",
    "write",
    "patch",
    "bash",
    "shell",
    "exec",
    "task",
    "subagent",
    "skill",
    "question",
    "webfetch",
    "websearch",
    "glob",
    "grep",
    "lsp",
    "doom_loop",
}


def _observed_tool_uses(events: list[Any]) -> list[dict[str, str]]:
    uses: list[dict[str, str]] = []
    for event in events:
        for node in _walk_json(event):
            if not isinstance(node, dict):
                continue
            name = node.get("tool") or node.get("toolName") or node.get("tool_name")
            if not isinstance(name, str) or name.lower() not in _KNOWN_TOOLS:
                continue
            raw_args = node.get("input") or node.get("arguments") or node.get("args")
            if isinstance(raw_args, dict):
                args_repr = json.dumps(raw_args, sort_keys=True)
            else:
                args_repr = str(raw_args or node.get("path") or node.get("file") or "")
            uses.append({"tool": name.lower(), "args": args_repr})
    return uses


def _run_opencode(
    exe: str,
    env: dict[str, str],
    workdir: Path,
    message: str,
    *,
    timeout_s: float = _OPENCODE_DEADLINE_S,
) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.Popen(
        [
            exe,
            "run",
            "--pure",
            "--format",
            "json",
            "--dir",
            str(workdir),
            "--model",
            "mlx-tui-local/local",
            message,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            exit_code: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=30)
            exit_code = proc.returncode
            return {
                "exit_code": exit_code,
                "timeout": True,
                "stdout": (stdout or "")[-_OPENCODE_OUTPUT_LIMIT:],
                "stderr": (stderr or "")[-_OPENCODE_OUTPUT_LIMIT:],
                "elapsed_s": time.monotonic() - started,
            }
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    return {
        "exit_code": exit_code,
        "timeout": False,
        "stdout": (stdout or "")[-_OPENCODE_OUTPUT_LIMIT:],
        "stderr": (stderr or "")[-_OPENCODE_OUTPUT_LIMIT:],
        "elapsed_s": time.monotonic() - started,
    }


def _parse_opencode_events(stdout: str) -> list[Any]:
    events: list[Any] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _all_text(events: list[Any]) -> str:
    parts: list[str] = []

    def _collect(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                _collect(item)
        elif isinstance(value, list):
            for item in value:
                _collect(item)

    _collect(events)
    return "\n".join(parts)


@pytest.fixture(scope="session")
def opencode_inputs(
    e_inputs: EInputs, tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Any]:
    if os.environ.get("MLX_TUI_E_OPENCODE") != "1":
        pytest.skip("OpenCode qualification requires MLX_TUI_E_OPENCODE=1")
    exe = _opencode_bin()
    version = _check_opencode_version(exe)
    isolation = tmp_path_factory.mktemp("opencode-e") / "iso"
    isolation.mkdir(parents=True, exist_ok=True)
    workdir, fixture, config = _write_opencode_isolation(e_inputs, isolation)
    env = _scrubbed_env(isolation, isolation / "opencode.json")
    if "--auto" in (os.environ.get("MLX_TUI_E_OPENCODE_ARGS", "")):
        pytest.fail("must not run OpenCode qualification with --auto")
    return {
        "exe": exe,
        "version": version,
        "isolation": isolation,
        "workdir": workdir,
        "fixture": fixture,
        "config": config,
        "config_sha256": _sha256_file(isolation / "opencode.json"),
        "env": env,
    }


def test_opencode_text_and_read_cycles(
    e_inputs: EInputs, opencode_inputs: dict[str, Any]
) -> None:
    recorder = _new_recorder(e_inputs, "opencode")
    try:
        exe = str(opencode_inputs["exe"])
        env = dict(opencode_inputs["env"])
        workdir = Path(str(opencode_inputs["workdir"]))
        fixture = Path(str(opencode_inputs["fixture"]))
        expected = fixture.read_text(encoding="utf-8")
        recorder.record(
            "opencode_config",
            status="supported",
            version=opencode_inputs["version"],
            config_sha256=opencode_inputs["config_sha256"],
            routing="mlx-tui-local/local for main and auxiliary model",
            sharing="disabled",
            pure=True,
            auto_flag=False,
        )
        for cycle in range(_CYCLES):
            probe = _run_opencode(
                exe, env, workdir, f"E text probe {cycle}: reply exactly: juniper"
            )
            probe_events = _parse_opencode_events(str(probe["stdout"]))
            assert probe["timeout"] is False, f"cycle {cycle}: opencode timed out"
            assert probe["exit_code"] == 0, (
                f"cycle {cycle}: exit {probe['exit_code']} "
                f"stderr={str(probe['stderr'])[:1000]}"
            )
            probe_text = _all_text(probe_events)
            assert probe_text.strip(), f"cycle {cycle}: empty text probe"
            read_message = (
                f"E read cycle {cycle}: read the file {_FIXTURE_NAME} in this "
                "directory with the read tool, then reply with its exact content "
                "and nothing else"
            )
            result = _run_opencode(exe, env, workdir, read_message)
            events = _parse_opencode_events(str(result["stdout"]))
            assert result["timeout"] is False, f"cycle {cycle}: read timed out"
            assert result["exit_code"] == 0, (
                f"cycle {cycle}: exit {result['exit_code']} "
                f"stderr={str(result['stderr'])[:1000]}"
            )
            uses = _observed_tool_uses(events)
            unexpected = [use for use in uses if use["tool"] != "read"]
            assert not unexpected, f"cycle {cycle}: unexpected tools {unexpected}"
            reads = [use for use in uses if use["tool"] == "read"]
            assert reads, f"cycle {cycle}: text-only response, no read tool observed"
            assert any(str(fixture) in use["args"] for use in reads), (
                f"cycle {cycle}: read did not target the exact fixture: {reads}"
            )
            final_text = _all_text(events)
            assert expected.strip() in final_text, (
                f"cycle {cycle}: final answer lacks the exact fixture content"
            )
            recorder.record(
                "opencode_cycle",
                status="supported",
                cycle=cycle,
                probe_chars=len(probe_text),
                tool_uses=uses,
                elapsed_s=float(result["elapsed_s"]),
            )
    finally:
        recorder.write()


def test_opencode_cancel_before_and_after_first_event(
    e_inputs: EInputs, opencode_inputs: dict[str, Any]
) -> None:
    recorder = _new_recorder(e_inputs, "opencode-cancel")
    try:
        exe = str(opencode_inputs["exe"])
        env = dict(opencode_inputs["env"])
        workdir = Path(str(opencode_inputs["workdir"]))
        for attempt in ("before-first", "after-first"):
            started = time.monotonic()
            proc = subprocess.Popen(
                [
                    exe,
                    "run",
                    "--pure",
                    "--format",
                    "json",
                    "--dir",
                    str(workdir),
                    "--model",
                    "mlx-tui-local/local",
                    "E cancel probe: describe this directory briefly",
                ],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert proc.stdout is not None
            seen_events = 0
            first_event_s: float | None = None
            try:
                if attempt == "after-first":
                    deadline = started + 60.0
                    remainder = ""
                    while time.monotonic() < deadline:
                        chunk = proc.stdout.read(1)
                        if not chunk:
                            break
                        remainder += chunk
                        if remainder.strip().startswith("{"):
                            try:
                                json.loads(remainder)
                            except ValueError:
                                continue
                            seen_events = 1
                            first_event_s = time.monotonic() - started
                            break
                proc.terminate()
                try:
                    stdout, stderr = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate(timeout=30)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=30)
            close_s = time.monotonic() - started
            recovery = _run_opencode(
                exe, env, workdir, "E cancel recovery: reply exactly: recovered"
            )
            recovery_events = _parse_opencode_events(str(recovery["stdout"]))
            assert recovery["exit_code"] == 0
            assert _all_text(recovery_events).strip()
            recorder.record(
                "opencode_cancel",
                status="supported",
                attempt=attempt,
                seen_events=seen_events,
                first_event_s=first_event_s,
                client_close_s=close_s,
                exit_code=proc.returncode,
                recovery_chars=len(_all_text(recovery_events)),
            )
    finally:
        recorder.write()
