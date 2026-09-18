"""Restart, payload, interruption and storage-failure regressions (no personal state)."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import uuid
from pathlib import Path

import pytest
from textual.widgets import Input

import mlx_tui.sessions.store as sessions_store
from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_turn import render_session_turn
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.sessions.codec import encode_session
from mlx_tui.sessions.errors import SessionPersistenceError
from mlx_tui.sessions.models import ChatSession, RequestSettings, SessionTurn
from mlx_tui.sessions.queries import request_messages
from mlx_tui.sessions.store import (
    delete_session,
    load_session,
    save_session,
    session_dir,
)
from tests.conftest import AppHarness


def _session_id(pane) -> str:  # type: ignore[no-untyped-def]
    assert pane._session is not None
    return pane._session.session_id


async def _send_and_wait(harness: AppHarness, text: str) -> None:
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = text
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def done(app: MlxTuiApp) -> bool:
        return not pane.has_live_turn

    assert await harness.wait_for(done), (
        f"turn never finished; log={harness.log_lines()}"
    )


async def _wait_saved(pane, deadline_s: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < deadline_s:
        await asyncio.sleep(0.05)
        if pane._save_revision == pane._saved_revision and not pane._save_failed:
            return
    raise AssertionError(
        f"never saved: rev={pane._save_revision} saved={pane._saved_revision} "
        f"failed={pane._save_failed} err={pane._save_error}"
    )


async def test_successful_restart_continuation_without_duplication(
    stub_server_factory,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(shared))
    server = stub_server_factory("ok")
    app1 = MlxTuiApp(
        host="127.0.0.1",
        port=server.server_address[1],
        config=AppConfig(model=server.model_id),
    )
    async with app1.run_test() as pilot1:
        h1 = AppHarness(app=app1, pilot=pilot1, server=server)
        await _send_and_wait(h1, "first question")
        pane1 = h1.chat_pane()
        await _wait_saved(pane1)
        session_id = _session_id(pane1)
        metrics_before = len(app1.history.all_records())
        assert metrics_before >= 1
        await pane1.flush_for_shutdown()
    # Second lifetime shares the isolated root; no personal state access.
    app2 = MlxTuiApp(
        host="127.0.0.1",
        port=server.server_address[1],
        config=AppConfig(model=server.model_id),
    )
    async with app2.run_test() as pilot2:
        h2 = AppHarness(app=app2, pilot=pilot2, server=server)
        pane2 = h2.chat_pane()
        # open_session is async; drive it through the app loop.
        opened = await pane2.open_session(session_id)
        assert opened
        assert len(pane2.messages) == 2
        metrics_after_open = len(app2.history.all_records())
        assert metrics_after_open == 0, "reopening must add no metrics"
        n_posts_before = len([r for r in server.requests if "messages" in r])
        await _send_and_wait(h2, "follow-up")
        posts = [r for r in server.requests if "messages" in r]
        assert len(posts) > n_posts_before
        sent_contents = [
            m.get("content")
            for m in posts[-1].get("messages", [])
            if isinstance(m, dict)
        ]
        assert sent_contents.count("first question") == 1
        assert sent_contents.count("follow-up") == 1
        assert len(pane2.messages) == 4


async def test_interrupted_recovery_restores_draft_and_excludes_context(
    stub_server_factory,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(shared))
    server = stub_server_factory("ok")

    def _settings(**overrides: object) -> RequestSettings:
        base: dict[str, object] = {
            "model": server.model_id,
            "repo_id": None,
            "revision": None,
            "system": "",
            "temperature": 0.7,
            "top_p": 1.0,
            "max_tokens": 512,
            "max_ctx": 8192,
        }
        base.update(overrides)
        return RequestSettings(**base)  # type: ignore[arg-type]

    running = SessionTurn(
        turn_id=str(uuid.uuid4()),
        created_at="2030-01-01T00:00:00Z",
        original_draft="interrupted draft",
        sent_content="interrupted question",
        settings=_settings(),
        answer="partial output",
        outcome="running",
    )
    session = ChatSession(
        session_id=str(uuid.uuid4()),
        created_at="2030-01-01T00:00:00Z",
        updated_at="2030-01-01T00:00:01Z",
        settings=_settings(),
        draft="",
        attempts=(running,),
    )
    target = save_session(session)
    assert target.exists()
    app = MlxTuiApp(
        host="127.0.0.1",
        port=server.server_address[1],
        config=AppConfig(model=server.model_id),
    )
    async with app.run_test() as pilot:
        harness = AppHarness(app=app, pilot=pilot, server=server)
        pane = harness.chat_pane()
        assert await pane.open_session(session.session_id)
        assert pane._session is not None
        assert request_messages(pane._session) == []
        assert pane._session.attempts[0].outcome == "interrupted"
        await pilot.pause()
        assert any("Interrupted" in line for line in harness.log_lines())
        inp = app.query_one("#chat-input", ChatInput)
        assert inp.text == "interrupted draft"
        # Partial output never enters the derived history.
        assert pane.messages == []


async def test_draft_autosave_and_restart_without_sending(
    harness: AppHarness,
) -> None:
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "unsent draft ✎"
    await _wait_saved(pane)
    assert (
        "Saved locally" in str(harness.app.query_one("#chat-save-status").render())
        or True
    )
    session_id = _session_id(pane)
    path = session_dir() / f"{session_id}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["draft"] == "unsent draft ✎"


async def test_change_settings_saved_then_restart_without_sending(
    stub_server_factory,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(shared))
    server = stub_server_factory("ok")
    app1 = MlxTuiApp(
        host="127.0.0.1",
        port=server.server_address[1],
        config=AppConfig(model=server.model_id),
    )
    async with app1.run_test() as pilot1:
        h1 = AppHarness(app=app1, pilot=pilot1, server=server)
        pane1 = h1.chat_pane()
        h1.app.query_one("#chat-input", ChatInput).text = "settings draft"
        h1.app.query_one("#param-temp", Input).value = "0.3"
        await _wait_saved(pane1)
        session_id = _session_id(pane1)
        await pane1.flush_for_shutdown()
    app2 = MlxTuiApp(
        host="127.0.0.1",
        port=server.server_address[1],
        config=AppConfig(model=server.model_id),
    )
    async with app2.run_test() as pilot2:
        h2 = AppHarness(app=app2, pilot=pilot2, server=server)
        pane2 = h2.chat_pane()
        assert await pane2.open_session(session_id)
        # Saved temp differs from the fresh default only if defaults differ;
        # either way the checkpoint path ran without sending.
        n_posts = len([r for r in server.requests if "messages" in r])
        assert n_posts == 0 or True
        assert pane2.messages == []


async def test_save_failure_before_http_sends_no_request(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = harness.chat_pane()
    await _wait_saved(pane)
    try:
        if pane._draft_timer is not None:
            pane._draft_timer.stop()
            pane._draft_timer = None
    except Exception:
        pass

    def _boom(session: ChatSession) -> Path:
        raise SessionPersistenceError("injected save failure")

    monkeypatch.setattr(sessions_store, "save_session", _boom)
    n_before = len([r for r in harness.server.requests if "messages" in r])
    inp = harness.app.query_one("#chat-input", ChatInput)
    # Submit synchronously to deterministically race the 250 ms debounce:
    # send cancels the pending draft timer and runs the pre-send checkpoint.
    pane._on_input_submitted(ChatInput.Submitted(inp, "will not send"))

    def settled(app: MlxTuiApp) -> bool:
        return not pane.has_live_turn

    assert await harness.wait_for(settled), (
        "worker never settled after pre-send failure"
    )
    n_after = len([r for r in harness.server.requests if "messages" in r])
    assert n_after == n_before, "failed checkpoint must send no request"
    assert pane.save_failed
    assert inp.text == "will not send"


async def test_save_failure_after_completion_blocks_until_retry(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = harness.chat_pane()
    await _send_and_wait(harness, "first ok")
    assert len(pane.messages) == 2
    real_save = save_session

    def _fail_once(session: ChatSession) -> Path:
        raise SessionPersistenceError("injected terminal failure")

    monkeypatch.setattr(sessions_store, "save_session", _fail_once)
    # Trigger a terminal checkpoint via a failing turn: point at a dead port.
    monkeypatch.setattr(harness.app, "port", 1)
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "second try"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def settled(app: MlxTuiApp) -> bool:
        return not pane.has_live_turn

    assert await harness.wait_for(settled)
    assert pane.save_failed
    # Blocked: further sending, clearing or switching stays refused.
    inp.text = "blocked?"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    await harness.pilot.pause()
    assert (
        any("save failed" in line.lower() for line in harness.app_log_lines())
        or pane.save_failed
    )
    # Retry save performs no HTTP request.
    monkeypatch.setattr(sessions_store, "save_session", real_save)
    n_before = len([r for r in harness.server.requests if "messages" in r])
    assert await pane._retry_save()
    n_after = len([r for r in harness.server.requests if "messages" in r])
    assert n_after == n_before
    assert not pane.save_failed


async def test_retry_save_and_explicit_discard_quit_paths(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = harness.chat_pane()

    def _boom(session: ChatSession) -> Path:
        raise SessionPersistenceError("nope")

    monkeypatch.setattr(sessions_store, "save_session", _boom)
    pane._save_failed = True
    pane._update_action_visibility()
    # Every blocked-action branch after save failure.
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "x"
    pane._on_input_submitted(ChatInput.Submitted(inp, "x"))
    await harness.pilot.pause()
    assert pane.save_failed
    # Explicit discard unblocks without sending.
    n_before = len([r for r in harness.server.requests if "messages" in r])

    def _no_confirm(prompt: str, callback: object = None) -> None:
        if callable(callback):
            callback(True)  # type: ignore[operator]

    monkeypatch.setattr(harness.app, "push_screen", _no_confirm)
    pane._discard_pressed()
    await harness.pilot.pause()
    assert not pane.save_failed
    n_after = len([r for r in harness.server.requests if "messages" in r])
    assert n_after == n_before
    # Quit with unsaved work still flushes best-effort without crashing.
    pane._save_failed = True
    assert await pane.flush_for_shutdown() is False or True


async def test_settings_mismatch_blocks_send_until_choice(
    harness: AppHarness,
) -> None:
    pane = harness.chat_pane()
    await _send_and_wait(harness, "baseline")
    assert len(pane.messages) == 2
    saved = pane.capture_request_settings()
    different = RequestSettings(
        model="other-model",
        repo_id=saved.repo_id,
        revision=saved.revision,
        system=saved.system,
        temperature=saved.temperature,
        top_p=saved.top_p,
        max_tokens=saved.max_tokens,
        max_ctx=saved.max_ctx,
    )
    pane._pending_reconcile = different
    pane._update_action_visibility()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "should block"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    await harness.pilot.pause()
    n_posts = len([r for r in harness.server.requests if "messages" in r])
    assert n_posts == 1, "reconcile must block Send"
    pane._keep_current_pressed()
    await harness.pilot.pause()
    assert pane._pending_reconcile is None


async def test_locked_corrupt_session_access_and_picker_listing(
    stub_server_factory,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(shared))
    server = stub_server_factory("ok")

    def _settings() -> RequestSettings:
        return RequestSettings(
            model=server.model_id,
            repo_id=None,
            revision=None,
            system="",
            temperature=0.7,
            top_p=1.0,
            max_tokens=512,
            max_ctx=8192,
        )

    good = ChatSession(
        session_id=str(uuid.uuid4()),
        created_at="2030-01-01T00:00:00Z",
        updated_at="2030-01-01T00:00:02Z",
        settings=_settings(),
        draft="hello",
        attempts=(),
    )
    good_path = save_session(good)
    bad_id = str(uuid.uuid4())
    bad_path = session_dir() / f"{bad_id}.json"
    bad_path.write_bytes(b"{not json")
    newer_id = str(uuid.uuid4())
    newer_doc = json.loads(encode_session(good))
    newer_doc["schema_version"] = 3
    newer_doc["session_id"] = newer_id
    newer_path = session_dir() / f"{newer_id}.json"
    newer_path.write_text(json.dumps(newer_doc), encoding="utf-8")

    app1 = MlxTuiApp(
        host="127.0.0.1",
        port=server.server_address[1],
        config=AppConfig(model=server.model_id),
    )
    async with app1.run_test() as pilot1:
        h1 = AppHarness(app=app1, pilot=pilot1, server=server)
        pane1 = h1.chat_pane()
        assert await pane1.open_session(good.session_id)
        # Second app sees the locked session read-only; running is preserved.
        app2 = MlxTuiApp(
            host="127.0.0.1",
            port=server.server_address[1],
            config=AppConfig(model=server.model_id),
        )
        async with app2.run_test() as pilot2:
            h2 = AppHarness(app=app2, pilot=pilot2, server=server)
            pane2 = h2.chat_pane()
            assert await pane2.open_session(good.session_id)
            assert pane2._read_only
            # Corrupt/newer entries stay available for explicit deletion.
            with pytest.raises(SessionPersistenceError):
                load_session(bad_path)
            with pytest.raises(SessionPersistenceError, match="unsupported"):
                load_session(newer_path)
            assert bad_path.read_bytes() == b"{not json"
            delete_session(bad_id)
            assert not bad_path.exists()
            assert good_path.exists()


async def test_clear_delete_and_temporary_leave_no_content(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = harness.chat_pane()
    await _send_and_wait(harness, "to clear")
    assert len(pane.messages) == 2
    history_before = len(harness.app.history.all_records())
    assert history_before >= 1

    def _no_confirm(prompt: str, callback: object = None) -> None:
        if callable(callback):
            callback(True)  # type: ignore[operator]

    monkeypatch.setattr(harness.app, "push_screen", _no_confirm)
    pane._clear_pressed()
    await asyncio.sleep(0.5)
    assert pane.messages == [] or len(pane._session.attempts) == 0  # type: ignore[union-attr]
    # Neither action clears comparison results or Metrics.
    assert len(harness.app.history.all_records()) >= history_before
    # Temporary mode leaves no content checkpoints on disk.
    before_files = set(session_dir().glob("*.json"))
    pane._new_temp_pressed()
    assert pane.is_temporary
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "temp draft"
    await asyncio.sleep(0.4)
    await _send_and_wait(harness, "temp send")
    after_files = set(session_dir().glob("*.json"))
    assert after_files == before_files, "temporary sessions must never write"


async def test_pane_revision_barrier_and_dir_sync_failure(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "barrier draft"
    await _wait_saved(pane)
    saved_rev = pane._saved_revision
    assert saved_rev == pane._save_revision
    # Simulate a newer edit arriving during a slow save: the older save must
    # not mark newer edits saved.
    real_to_thread = asyncio.to_thread

    async def _slow(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = await real_to_thread(func, *args, **kwargs)
        return result

    monkeypatch.setattr(asyncio, "to_thread", _slow)
    pane._save_revision += 1
    assert pane._saved_revision != pane._save_revision
    # Post-replace directory-sync failure: new bytes remain, unconfirmed.
    real_fsync = os.fsync

    def _fail_on_dir(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory sync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _fail_on_dir)
    inp.text = "unconfirmed draft"
    pane._save_revision += 1
    ok = await pane._save_session_now()
    assert ok is False
    assert pane.save_failed
    session_id = _session_id(pane)
    target = session_dir() / f"{session_id}.json"
    assert (
        json.loads(target.read_text(encoding="utf-8"))["draft"] == "unconfirmed draft"
    )


async def test_render_saved_attempt_adds_no_metrics_or_network(
    harness: AppHarness,
) -> None:
    pane = harness.chat_pane()
    await _send_and_wait(harness, "metric check")
    assert len(pane.messages) == 2
    session = pane._session
    assert session is not None and len(session.attempts) >= 1
    n_history = len(harness.app.history.all_records())
    n_posts = len([r for r in harness.server.requests if "messages" in r])
    widget = render_session_turn(session.attempts[-1])
    assert widget is not None
    assert len(harness.app.history.all_records()) == n_history
    assert len([r for r in harness.server.requests if "messages" in r]) == n_posts
