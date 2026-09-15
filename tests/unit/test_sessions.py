"""Focused storage checks for the session contract (no UI, no network)."""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import uuid
from pathlib import Path

import pytest

from mlx_tui import sessions as S
from mlx_tui.attachments import MAX_ATTACHMENT_BYTES, read_attachment

T0 = "2030-01-01T00:00:00Z"
T1 = "2030-01-01T00:00:01Z"
T2 = "2030-01-01T00:00:02Z"
T3 = "2030-01-01T00:00:03Z"

needs_lock = pytest.mark.skipif(S.fcntl is None, reason="session locking is Unix-only")
needs_fork = pytest.mark.skipif(
    S.fcntl is None or not hasattr(os, "fork"),
    reason="second-process lock tests need fork and fcntl",
)


def _settings(**overrides: object) -> S.RequestSettings:
    base: dict[str, object] = {
        "model": "mlx-community/stub-test",
        "repo_id": "mlx-community/stub-test",
        "revision": "abc123",
        "system": "You are helpful.",
        "temperature": 0.5,
        "top_p": 0.9,
        "max_tokens": 512,
        "max_ctx": 8192,
    }
    base.update(overrides)
    return S.RequestSettings(**base)  # type: ignore[arg-type]


def _turn(**overrides: object) -> S.SessionTurn:
    base: dict[str, object] = {
        "turn_id": str(uuid.uuid4()),
        "created_at": T0,
        "original_draft": "draft",
        "sent_content": "question?",
        "settings": _settings(),
        "answer": "answer.",
        "outcome": "success",
    }
    base.update(overrides)
    return S.SessionTurn(**base)  # type: ignore[arg-type]


def _session(**overrides: object) -> S.ChatSession:
    base: dict[str, object] = {
        "session_id": str(uuid.uuid4()),
        "created_at": T0,
        "updated_at": T1,
        "settings": _settings(),
        "draft": "",
        "attempts": (),
    }
    base.update(overrides)
    return S.ChatSession(**base)  # type: ignore[arg-type]


def _raw_session_doc(session_id: str) -> dict[str, object]:
    return json.loads(S.encode_session(_session(session_id=session_id)))


def test_unicode_settings_tool_data_round_trip() -> None:
    settings = _settings(
        model=None,
        system="Sÿstem héllo 🐍",
        seed=7,
        enable_thinking=True,
        profile_id="qwen3-1.7b-baseline",
        profile_fingerprint="fp-" + "0" * 8,
        profile_modified=True,
        profile_runtime_commit="74e7cf9",
        profile_runtime_provenance="verified",
        profile_template_sha256="tmpl",
        profile_template_provenance="requested",
        profile_launch_settings={"server": "mlx_lm.server", "port": 18080},
        profile_launch_provenance="requested",
    )
    turn = _turn(
        original_draft="  indented\n\ncode 🐍  ",
        sent_content="  indented\n\ncode 🐍  ",
        settings=settings,
        answer="réponse ✓\n```py\nx = 1\n```",
        reasoning="rêasoning…",
        tool_calls=({"name": "lire", "arguments": '{"f": "héllo"}'},),
        response_model="resp-модель",
        finish_reason="stop",
        skipped_frames=2,
        stream_complete=True,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cached_prompt_tokens=3,
        first_output_s=0.1,
        answer_started_s=0.2,
        total_s=1.5,
    )
    session = _session(settings=settings, draft="bröuillon ✎", attempts=(turn,))
    path = S.save_session(session)
    assert path.name == f"{session.session_id}.json"
    loaded = S.load_session(path)
    assert loaded == session
    assert loaded.attempts[0].tool_calls == turn.tool_calls


def test_success_only_derived_history() -> None:
    good = _turn(sent_content="q1", answer="a1", outcome="success")
    failed = _turn(sent_content="q2", answer="partial", outcome="failed")
    running = _turn(sent_content="q3", answer="", outcome="running")
    session = _session(attempts=(good, failed, running))
    path = S.save_session(session)
    with S.lock_session(session.session_id):
        recovered = S.load_session(path)
    assert S.request_messages(recovered) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    assert [turn.outcome for turn in S.successful_turns(recovered)] == ["success"]


def test_v1_sessions_upgrade_to_v2_with_empty_attachments() -> None:
    session_id = str(uuid.uuid4())
    document = _raw_session_doc(session_id)
    document["schema_version"] = 1
    document.pop("attachments", None)

    upgraded = S.decode_session(json.dumps(document), expected_id=session_id)

    assert upgraded.schema_version == 2
    assert upgraded.attachments == ()
    encoded = json.loads(S.encode_session(upgraded))
    assert encoded["schema_version"] == 2
    assert encoded["attachments"] == []


def test_v1_file_is_not_rewritten_until_explicit_save() -> None:
    session_id = str(uuid.uuid4())
    document = _raw_session_doc(session_id)
    document["schema_version"] = 1
    document.pop("attachments", None)
    path = S.session_dir() / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()

    loaded = S.load_session(path)

    assert loaded.schema_version == 2
    assert path.read_bytes() == before
    S.save_session(loaded)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_attachment_snapshots_round_trip_on_draft_and_attempt(tmp_path: Path) -> None:
    source = tmp_path / "snippet.py"
    source.write_text("print('snapshot')\n", encoding="utf-8")
    snapshot = read_attachment(source)
    turn = _turn(attachments=(snapshot,))
    session = _session(draft="inspect", attachments=(snapshot,), attempts=(turn,))

    loaded = S.load_session(S.save_session(session))

    assert loaded.attachments == (snapshot,)
    assert loaded.attempts[0].attachments == (snapshot,)


def test_attachment_aggregate_bound_counts_all_draft_files(tmp_path: Path) -> None:
    paths = [tmp_path / f"part-{index}.txt" for index in range(5)]
    for path in paths[:4]:
        path.write_bytes(b"a" * MAX_ATTACHMENT_BYTES)
    paths[4].write_bytes(b"b")
    snapshots = tuple(read_attachment(path) for path in paths)

    with pytest.raises(S.SessionValidationError, match="1 MiB"):
        S.encode_session(_session(attachments=snapshots))


def test_strict_schema_rejection() -> None:
    session_id = str(uuid.uuid4())

    def _bad(mutate: object) -> None:
        doc = _raw_session_doc(session_id)
        assert isinstance(mutate, dict)
        doc.update(mutate)
        with pytest.raises(S.SessionValidationError):
            S.decode_session(json.dumps(doc))

    _bad({"unexpected": 1})
    _bad({"schema_version": "1"})
    _bad({"session_id": "not-a-uuid"})
    _bad({"session_id": str(uuid.uuid4()).upper()})
    _bad({"created_at": "yesterday"})
    _bad({"attempts": {}})
    _bad({"draft": None})


def test_strict_turn_rejection() -> None:
    session_id = str(uuid.uuid4())

    def _bad_turn(mutate: dict[str, object]) -> None:
        doc = _raw_session_doc(session_id)
        doc["attempts"] = [_turn_from_doc()]
        attempts = doc["attempts"]
        assert isinstance(attempts, list)
        turn = attempts[0] = dict(_turn_from_doc())
        turn.update(mutate)
        with pytest.raises(S.SessionValidationError):
            S.decode_session(json.dumps(doc))

    def _turn_from_doc() -> dict[str, object]:
        return json.loads(S.encode_session(_session(attempts=(_turn(),))))["attempts"][
            0
        ]

    _bad_turn({"turn_id": "xyz"})
    _bad_turn({"outcome": "maybe"})
    _bad_turn({"outcome": "success", "answer": "", "tool_calls": []})
    _bad_turn({"sent_content": ""})
    _bad_turn({"included_turn_ids": "nope"})
    _bad_turn({"tool_calls": ["nope"]})
    _bad_turn({"skipped_frames": -1})
    _bad_turn({"skipped_frames": True})
    _bad_turn({"stream_complete": "yes"})
    _bad_turn({"estimated_input": 1.5})
    _bad_turn({"error_category": "mystery"})
    _bad_turn({"error_detail": 42})
    _bad_turn({"total_s": float("nan")})
    _bad_turn({"settings": "nope"})


def test_strict_settings_rejection() -> None:
    session_id = str(uuid.uuid4())

    def _bad_settings(mutate: dict[str, object]) -> None:
        doc = _raw_session_doc(session_id)
        settings = doc["settings"]
        assert isinstance(settings, dict)
        settings.update(mutate)
        with pytest.raises(S.SessionValidationError):
            S.decode_session(json.dumps(doc))

    _bad_settings({"temperature": float("nan")})
    _bad_settings({"temperature": -0.5})
    _bad_settings({"temperature": True})
    _bad_settings({"top_p": 0})
    _bad_settings({"top_p": 1.5})
    _bad_settings({"max_tokens": 0})
    _bad_settings({"max_tokens": True})
    _bad_settings({"max_ctx": "big"})
    _bad_settings({"seed": 1.5})
    _bad_settings({"enable_thinking": "yes"})
    _bad_settings({"profile_modified": 1})
    _bad_settings({"profile_runtime_provenance": "guessed"})
    _bad_settings({"profile_runtime_commit": "abc", "profile_runtime_provenance": None})
    _bad_settings(
        {"profile_runtime_commit": None, "profile_runtime_provenance": "verified"}
    )
    _bad_settings({"profile_launch_settings": {"ok": float("inf")}})


def test_corrupt_and_newer_files_stay_intact() -> None:
    good = _session(draft="keep me")
    good_path = S.save_session(good)
    bad_id = str(uuid.uuid4())
    bad_path = S.session_dir() / f"{bad_id}.json"
    bad_path.write_bytes(b"{not json")
    newer_doc = _raw_session_doc(str(uuid.uuid4()))
    newer_doc["schema_version"] = 3
    newer_id = str(uuid.uuid4())
    newer_doc["session_id"] = newer_id
    newer_path = S.session_dir() / f"{newer_id}.json"
    newer_path.write_text(json.dumps(newer_doc), encoding="utf-8")

    with pytest.raises(S.SessionPersistenceError):
        S.load_session(bad_path)
    with pytest.raises(S.SessionPersistenceError, match="unsupported"):
        S.load_session(newer_path)
    assert bad_path.read_bytes() == b"{not json"
    assert json.loads(newer_path.read_text(encoding="utf-8"))["schema_version"] == 3
    assert S.load_session(good_path) == good
    names = {path.name for path in S.list_sessions()}
    assert {good_path.name, bad_path.name, newer_path.name} <= names


def test_filename_uuid_mismatch_rejected() -> None:
    session = _session()
    S.save_session(session)
    other = S.session_dir() / f"{uuid.uuid4()}.json"
    other.write_text(S.encode_session(session), encoding="utf-8")
    with pytest.raises(S.SessionValidationError, match="does not match"):
        S.load_session(other)


def test_duplicate_attempt_ids_and_bad_references_rejected() -> None:
    first = _turn(outcome="success", answer="a1", sent_content="q1")
    dupe = dataclasses.replace(first, created_at=T1)
    with pytest.raises(S.SessionValidationError, match="unique"):
        S.encode_session(_session(attempts=(first, dupe)))

    failed = _turn(outcome="failed", answer="oops", sent_content="q2")
    later_ref = _turn(
        outcome="failed",
        answer="x",
        sent_content="q3",
        included_turn_ids=(failed.turn_id,),
    )
    with pytest.raises(S.SessionValidationError, match="successful"):
        S.encode_session(_session(attempts=(failed, later_ref)))

    second = _turn(outcome="success", answer="a2", sent_content="q2")
    forward = dataclasses.replace(
        first, included_turn_ids=(second.turn_id,), created_at=T0
    )
    with pytest.raises(S.SessionValidationError, match="prior"):
        S.encode_session(_session(attempts=(forward, second)))

    missing = dataclasses.replace(
        first, included_turn_ids=(str(uuid.uuid4()),), created_at=T0
    )
    with pytest.raises(S.SessionValidationError, match="prior"):
        S.encode_session(_session(attempts=(missing,)))

    repeated = dataclasses.replace(
        second, included_turn_ids=(first.turn_id, first.turn_id)
    )
    with pytest.raises(S.SessionValidationError, match="unique"):
        S.encode_session(_session(attempts=(first, repeated)))


def test_valid_prior_success_reference_accepted() -> None:
    first = _turn(outcome="success", answer="a1", sent_content="q1")
    second = _turn(
        outcome="success",
        answer="a2",
        sent_content="q2",
        included_turn_ids=(first.turn_id,),
        estimated_input=10,
        reserved_output=5,
        excluded_messages=0,
    )
    session = _session(attempts=(first, second))
    loaded = S.load_session(S.save_session(session))
    assert loaded.attempts[1].included_turn_ids == (first.turn_id,)


def test_per_draft_and_per_attempt_aggregate_bounds() -> None:
    big = "x" * (S.MAX_AGGREGATE_BYTES + 1)
    with pytest.raises(S.SessionValidationError, match="1 MiB"):
        S.encode_session(_session(draft=big))
    turn = _turn(answer=big)
    with pytest.raises(S.SessionValidationError, match="1 MiB"):
        S.encode_session(_session(attempts=(turn,)))
    tools = {"blob": "y" * (S.MAX_AGGREGATE_BYTES + 1)}
    sneaky = _turn(answer="", tool_calls=(tools,), outcome="tool_only")
    with pytest.raises(S.SessionValidationError, match="1 MiB"):
        S.encode_session(_session(attempts=(sneaky,)))

    exact = "z" * S.MAX_AGGREGATE_BYTES
    kept = S.load_session(S.save_session(_session(draft=exact)))
    assert kept.draft == exact


def test_two_revision_barrier_keeps_newer_bytes() -> None:
    session_id = str(uuid.uuid4())
    older = _session(session_id=session_id, draft="older", updated_at=T1)
    newer = _session(session_id=session_id, draft="newer", updated_at=T2)
    S.save_session(older)
    S.save_session(newer)
    with pytest.raises(S.SessionStaleWriteError):
        S.save_session(older)
    target = S.session_dir() / f"{session_id}.json"
    assert target.read_text(encoding="utf-8") == S.encode_session(newer)
    assert S.load_session(target).updated_at == T2


def test_write_failure_preserves_prior_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(draft="durable")
    target = S.save_session(session)
    before = target.read_bytes()

    def _boom(*args: object, **kwargs: object) -> object:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(S.SessionPersistenceError):
        S.save_session(dataclasses.replace(session, draft="lost", updated_at=T2))
    assert target.read_bytes() == before
    leftovers = list(S.session_dir().glob(".*.tmp"))
    assert leftovers == []


def test_post_replace_sync_failure_leaves_bytes_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(draft="first")
    target = S.save_session(session)
    real_fsync = os.fsync

    def _fail_on_dir(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory sync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _fail_on_dir)
    pending = dataclasses.replace(session, draft="second", updated_at=T2)
    with pytest.raises(S.SessionDurabilityUnconfirmed) as excinfo:
        S.save_session(pending)
    assert excinfo.value.path == target
    assert json.loads(target.read_text(encoding="utf-8"))["draft"] == "second"


def test_private_permissions() -> None:
    target = S.save_session(_session(draft="secret"))
    assert stat.S_IMODE(S.session_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_interrupted_recovery_without_rewrite() -> None:
    running = _turn(answer="partial", outcome="running", sent_content="q?")
    session = _session(attempts=(running,), updated_at=T2)
    target = S.save_session(session)
    before = target.read_bytes()
    loaded = S.load_session(target)
    assert loaded.attempts[0].outcome == "interrupted"
    assert loaded.attempts[0].answer == "partial"
    assert target.read_bytes() == before


@needs_lock
def test_two_independent_lock_holders_denied() -> None:
    session = _session()
    S.save_session(session)
    with S.lock_session(session.session_id):
        with pytest.raises(S.SessionLockedError):
            with S.lock_session(session.session_id):
                pass  # pragma: no cover - second descriptor must block


@needs_fork
def test_second_process_lock_denied() -> None:
    session = _session()
    S.save_session(session)
    with S.lock_session(session.session_id):
        pid = os.fork()
        if pid == 0:
            try:
                with S.lock_session(session.session_id):
                    os._exit(10)
            except S.SessionLockedError:
                os._exit(0)
            except Exception:  # pragma: no cover - unexpected child failure
                os._exit(99)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0


@needs_lock
def test_owner_delete_under_held_lock() -> None:
    session = _session(draft="bye")
    target = S.save_session(session)
    with S.lock_session(session.session_id) as handle:
        assert handle.held
        S.delete_session(session.session_id, lock_handle=handle)
        assert not target.exists()
        lock_file = target.with_suffix(".lock")
        assert lock_file.exists()
    with pytest.raises(S.SessionPersistenceError, match="unknown session"):
        S.delete_session(session.session_id)


@needs_fork
def test_second_process_delete_denied() -> None:
    session = _session()
    S.save_session(session)
    with S.lock_session(session.session_id):
        pid = os.fork()
        if pid == 0:
            try:
                S.delete_session(session.session_id)
                os._exit(10)
            except S.SessionLockedError:
                os._exit(0)
            except Exception:  # pragma: no cover - unexpected child failure
                os._exit(99)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0


@needs_lock
def test_two_app_running_read_only_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(shared))
    running = _turn(answer="partial…", outcome="running", sent_content="q?")
    session = _session(draft="unsent ✎", attempts=(running,), updated_at=T2)
    target = S.save_session(session)

    with S.lock_session(session.session_id) as owner:
        viewer = S.load_session(target)
        assert viewer.attempts[0].outcome == "running"
        assert viewer.draft == "unsent ✎"
        owner_view = S.load_session(target, lock_handle=owner)
        assert owner_view.attempts[0].outcome == "interrupted"
        assert S.request_messages(viewer) == []
    assert S.load_session(target).attempts[0].outcome == "interrupted"


def test_symlinked_and_non_regular_files_rejected(tmp_path: Path) -> None:
    session = _session()
    target = S.save_session(session)
    outside = tmp_path / "outside.json"
    outside.write_text(S.encode_session(_session()), encoding="utf-8")
    link_id = str(uuid.uuid4())
    link = S.session_dir() / f"{link_id}.json"
    link.symlink_to(outside)
    with pytest.raises(S.SessionValidationError, match="non-regular"):
        S.load_session(link)
    directory = S.session_dir() / f"{uuid.uuid4()}.json"
    directory.mkdir()
    with pytest.raises(S.SessionValidationError, match="non-regular"):
        S.load_session(directory)
    with pytest.raises(S.SessionValidationError, match="non-regular"):
        S.save_session(dataclasses.replace(session, session_id=link_id))
    assert target.exists()


def test_symlinked_sessions_root_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link_root = tmp_path / "linked"
    link_root.symlink_to(real)
    monkeypatch.setenv("XDG_STATE_HOME", str(link_root))
    with pytest.raises(S.SessionPersistenceError, match="symlinked"):
        S.save_session(_session())


def test_invalid_ids_rejected() -> None:
    with pytest.raises(S.SessionValidationError):
        S.save_session(_session(session_id="nope"))
    with pytest.raises(S.SessionValidationError):
        S.delete_session("../escape")
    with pytest.raises(S.SessionValidationError):
        S.load_session(S.session_dir() / "ghost.json")
