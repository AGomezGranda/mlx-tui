"""Chat session/draft persistence helpers (pane-parameterized)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Button, Static

import mlx_tui.sessions.errors as _sessions_errors
import mlx_tui.sessions.models as _sessions_models
import mlx_tui.sessions.store as _sessions_store
from mlx_tui.chat_ui.context import _MAX_CONTEXT_TOKENS_EST
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.params import ParamsPane
from mlx_tui.sessions.models import ChatSession, RequestSettings, SessionTurn
from mlx_tui.sessions.store import SessionLock


def _ensure_session(pane: Any) -> ChatSession:
    if pane._session is not None:
        return pane._session
    settings = pane.capture_request_settings()
    session = _sessions_models.new_session(settings, draft="")
    # No file until meaningful content; no startup reads.
    pane._session = session
    return session


def capture_request_settings(pane: Any) -> RequestSettings:
    """Single dirty path for every next-request mutation."""
    try:
        temp, top_p, max_tok = pane.query_one(ParamsPane).read_values()
    except Exception:
        temp, top_p, max_tok = (0.7, 1.0, 1024)
    try:
        cfg = pane.tui.config
    except Exception:
        return RequestSettings(
            model=None,
            repo_id=None,
            revision=None,
            system="",
            temperature=temp,
            top_p=top_p,
            max_tokens=max_tok,
            max_ctx=_MAX_CONTEXT_TOKENS_EST,
        )
    try:
        model = pane.tui.effective_model()
    except Exception:
        model = cfg.model
    max_ctx = (
        cfg.max_ctx
        if isinstance(cfg.max_ctx, int) and cfg.max_ctx > 0
        else _MAX_CONTEXT_TOKENS_EST
    )
    system = cfg.system or ""
    profile_id: str | None = None
    fingerprint: str | None = None
    modified = False
    runtime_commit: str | None = None
    runtime_prov: str | None = None
    template_sha: str | None = None
    template_prov: str | None = None
    launch_settings: dict[str, Any] | None = None
    launch_prov: str | None = None
    repo_id: str | None = None
    revision: str | None = None
    try:
        active_id = pane.tui.active_profile_id
        modified = pane.tui.active_profile_modified
        if active_id is not None:
            entry = pane.tui.profile_entry(active_id)
            if entry is not None:
                profile_id = entry.profile.id
                fingerprint = entry.profile.fingerprint
                repo_id = entry.profile.repo_id
                revision = entry.profile.revision
                runtime_commit = entry.profile.runtime_commit
                runtime_prov = "requested"
                template_sha = entry.profile.template_sha256
                template_prov = "requested"
                launch_settings = dict(entry.profile.launch_settings)
                launch_prov = "requested"
    except Exception:
        pass
    return RequestSettings(
        model=model,
        repo_id=repo_id,
        revision=revision,
        system=system,
        temperature=temp,
        top_p=top_p,
        max_tokens=max_tok,
        max_ctx=max_ctx,
        seed=cfg.seed,
        enable_thinking=cfg.enable_thinking,
        profile_id=profile_id,
        profile_fingerprint=fingerprint,
        profile_modified=modified,
        profile_runtime_commit=runtime_commit,
        profile_runtime_provenance=runtime_prov,  # type: ignore[arg-type]
        profile_template_sha256=template_sha,
        profile_template_provenance=template_prov,  # type: ignore[arg-type]
        profile_launch_settings=launch_settings,  # type: ignore[arg-type]
        profile_launch_provenance=launch_prov,  # type: ignore[arg-type]
    )


def mark_next_request_dirty(pane: Any) -> None:
    """Checkpoint both reconciliation choices through one path."""
    session = pane._ensure_session()
    if pane._read_only:
        return
    settings = pane.capture_request_settings()
    pane._session = replace(session, settings=settings, updated_at=pane._now_iso())
    pane._save_revision += 1
    pane._update_save_status()
    pane._schedule_draft_save()
    pane.refresh_context_bar()


def _schedule_draft_save(pane: Any, delay: float = 0.25) -> None:
    if pane._is_temporary or pane._read_only:
        pane._update_save_status()
        return
    try:
        if pane._draft_timer is not None:
            pane._draft_timer.stop()
    except Exception:
        pass
    try:
        pane._draft_timer = pane.set_timer(delay, pane._draft_timer_fired)
    except Exception:
        pass
    pane._update_save_status()


def _draft_timer_fired(pane: Any) -> None:
    pane._draft_timer = None
    pane._save_session_async()


def _save_session_async(pane: Any) -> None:
    if pane._is_temporary or pane._read_only:
        return
    if pane._session is None:
        return
    # Coalesce draft changes at 250 ms; serialize with one async lock.
    asyncio.ensure_future(pane._save_session_now())


def _has_meaningful_content(pane: Any, session: ChatSession) -> bool:
    if session.draft.strip():
        return True
    return len(session.attempts) > 0


def _snapshot_for_save(pane: Any) -> ChatSession | None:
    session = pane._session
    if session is None:
        return None
    try:
        composer = pane.query_one("#chat-input", ChatInput)
        draft = composer.text
    except NoMatches:
        draft = session.draft
    # Never let an empty composer wipe a retained draft while a save
    # failure is pending (e.g. pre-send failure before end_turn restore).
    if pane._save_failed and not draft.strip() and session.draft.strip():
        draft = session.draft
    settings = pane.capture_request_settings()
    return replace(
        session,
        draft=draft,
        attachments=pane._draft_attachments,
        settings=settings,
        updated_at=pane._now_iso(),
    )


async def _save_session_now(pane: Any) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
    """Serialize writes; older saves never mark newer edits saved."""
    if pane._is_temporary or pane._read_only or pane._session is None:
        return True
    async with pane._write_lock:
        # Take the latest snapshot after acquiring the write lock.
        snapshot = pane._snapshot_for_save()
        if snapshot is None:
            return True
        pane._session = snapshot
        if not pane._has_meaningful_content(snapshot):
            # A spurious empty save must never clear a pending failure.
            if not pane._save_failed:
                pane._saved_revision = pane._save_revision
                pane._save_error = None
            try:
                pane._update_save_status()
            except Exception:
                pass
            return not pane._save_failed
        revision = pane._save_revision
        # Acquire the per-session advisory lock on first durable save.
        if pane._session_lock is None:
            try:
                cm = _sessions_store.lock_session(snapshot.session_id)
                handle = cm.__enter__()  # type: ignore[attr-defined]
                pane._session_lock_cm = cm
                pane._session_lock = handle
            except _sessions_errors.SessionLockedError as exc:
                pane._save_failed = True
                pane._save_error = str(exc)
                pane._read_only = True
                try:
                    pane._update_save_status()
                    pane._update_action_visibility()
                except Exception:
                    pass
                return False
            except _sessions_errors.SessionPersistenceError as exc:
                pane._save_failed = True
                pane._save_error = str(exc)
                try:
                    pane._update_save_status()
                    pane._update_action_visibility()
                except Exception:
                    pass
                return False
        try:
            await asyncio.to_thread(_sessions_store.save_session, snapshot)
        except _sessions_errors.SessionDurabilityUnconfirmed as exc:
            # New bytes remain, durability unconfirmed.
            if revision == pane._save_revision:
                pane._saved_revision = revision
            pane._save_failed = True
            pane._save_error = str(exc)
            try:
                pane._update_save_status()
                pane._update_action_visibility()
            except Exception:
                pass
            return False
        except (
            _sessions_errors.SessionPersistenceError,
            _sessions_errors.SessionValidationError,
            OSError,
        ) as exc:
            pane._save_failed = True
            pane._save_error = str(exc)
            try:
                pane._update_save_status()
                pane._update_action_visibility()
            except Exception:
                pass
            return False
        # Deterministic barrier: an older save finishing after a newer
        # revision must not mark newer edits saved.
        if revision == pane._save_revision:
            pane._saved_revision = revision
            pane._save_failed = False
            pane._save_error = None
        else:
            # Newer edits arrived during save; schedule another pass.
            try:
                pane._schedule_draft_save(delay=0.05)
            except Exception:
                pass
        try:
            pane._update_save_status()
            pane._update_action_visibility()
        except Exception:
            pass
        return not pane._save_failed


def _update_save_status(pane: Any) -> None:
    try:
        label = pane.query_one("#chat-save-status", Static)
    except NoMatches:
        return
    if pane._is_temporary:
        label.update("Temporary")
    elif pane._save_failed:
        label.update("Save failed — retry save")
    elif pane._save_revision != pane._saved_revision:
        label.update("Saving…")
    else:
        label.update("Saved locally")


def _update_action_visibility(pane: Any) -> None:
    try:
        reconcile = pane.query_one("#chat-reconcile-row", Horizontal)
        reconcile.display = pane._pending_reconcile is not None
    except NoMatches:
        pass
    try:
        save_row = pane.query_one("#chat-save-row", Horizontal)
        save_row.display = pane._save_failed
    except NoMatches:
        pass
    try:
        composer = pane.query_one("#chat-input", ChatInput)
        blocked = pane._save_failed or pane._pending_reconcile is not None
        # Do not steal disabled state owned by the operation lease; only
        # reflect the session block when idle.
        if not pane._turn_active and not pane.tui.operations.is_busy:
            composer.disabled = blocked or pane._read_only
    except (NoMatches, Exception):
        pass
    try:
        attachment_busy = (
            pane._turn_active
            or pane.tui.operations.is_busy
            or pane._read_only
            or pane._save_failed
            or pane._attachment_picker_active
        )
        pane.query_one("#btn-add-attachment", Button).disabled = attachment_busy
        pane.query_one(
            "#btn-inspect-attachment", Button
        ).disabled = not pane._draft_attachments
        pane.query_one("#btn-remove-attachment", Button).disabled = (
            not pane._draft_attachments or attachment_busy
        )
    except (NoMatches, Exception):
        pass
    try:
        send_btn = pane.query_one("#btn-send", Button)
        if not pane._turn_active and not pane.tui.operations.is_busy:
            send_btn.disabled = (
                pane._save_failed
                or pane._pending_reconcile is not None
                or pane._read_only
            )
    except (NoMatches, Exception):
        pass
    pane.refresh_zen_info()


def save_failed(pane: Any) -> bool:
    return pane._save_failed


def is_temporary(pane: Any) -> bool:
    return pane._is_temporary


def _release_session_lock(pane: Any) -> None:
    cm, pane._session_lock_cm = pane._session_lock_cm, None
    pane._session_lock = None
    if cm is not None:
        try:
            cm.__exit__(None, None, None)  # type: ignore[attr-defined]
        except Exception:
            pass


async def open_session(pane: Any, session_id: str) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
    """Acquire the lock before replacing the active view."""
    if pane._turn_active or pane.tui.operations.is_busy:
        pane.tui.log_app("finish the current turn before switching", "yellow")
        return False
    if pane._save_failed:
        pane.tui.log_app("save failed — retry save or discard unsaved work", "yellow")
        return False
    # Immediate checkpoint at session switch.
    if (
        pane._session is not None
        and not pane._is_temporary
        and not pane._read_only
        and pane._has_meaningful_content(pane._session)
        and pane._save_revision != pane._saved_revision
    ):
        await pane._save_session_now()
        if pane._save_failed:
            return False
    try:
        directory = _sessions_store.session_dir()
        target = directory / f"{session_id}.json"
    except _sessions_errors.SessionValidationError as exc:
        pane.tui.log_app(f"invalid session id: {exc}", "red")
        return False
    # Try to acquire the advisory lock first.
    lock_cm: Any | None = None
    lock_handle: SessionLock | None = None
    read_only = False
    try:
        lock_cm = _sessions_store.lock_session(session_id)
        lock_handle = lock_cm.__enter__()  # type: ignore[attr-defined]
    except _sessions_errors.SessionLockedError:
        read_only = True
    except _sessions_errors.SessionPersistenceError as exc:
        pane.tui.log_app(f"cannot open session: {exc}", "red")
        return False
    try:
        try:
            if read_only:
                loaded = _sessions_store.load_session(target)
            else:
                assert lock_handle is not None
                loaded = _sessions_store.load_session(target, lock_handle=lock_handle)
        except (
            _sessions_errors.SessionPersistenceError,
            _sessions_errors.SessionValidationError,
        ) as exc:
            pane.tui.log_app(f"cannot open session: {exc}", "red")
            if lock_cm is not None:
                try:
                    lock_cm.__exit__(None, None, None)  # type: ignore[attr-defined]
                except Exception:
                    pass
            return False
        pane._release_session_lock()
        pane._session = loaded
        pane._session_lock_cm = lock_cm
        pane._session_lock = lock_handle
        pane._is_temporary = False
        pane._read_only = read_only
        pane._save_revision = 0
        pane._saved_revision = 0
        pane._save_failed = False
        pane._save_error = None
        pane._pending_reconcile = None
        pane._active_session_turn_id = None
        pane._render_session(loaded)
        # Reopening adds no metrics and no network request.
        current = pane.capture_request_settings()
        if pane._settings_differ(loaded.settings, current):
            pane._pending_reconcile = loaded.settings
            pane.tui.log_app(
                "saved request settings differ — choose which to use", "yellow"
            )
        # A recovered running attempt restores its original draft only if
        # there is no newer saved composer draft.
        for turn in reversed(loaded.attempts):
            if turn.outcome == "interrupted" and not loaded.draft.strip():
                try:
                    composer = pane.query_one("#chat-input", ChatInput)
                    if not composer.text.strip():
                        pane._restoring = True
                        try:
                            composer.load_text(turn.original_draft)
                        finally:
                            pane._restoring = False
                except NoMatches:
                    pass
                break
        pane._update_save_status()
        pane._update_action_visibility()
        if read_only:
            pane.tui.log_app("session is locked elsewhere — read-only", "yellow")
        return True
    except Exception as exc:
        if lock_cm is not None and not read_only:
            try:
                lock_cm.__exit__(None, None, None)  # type: ignore[attr-defined]
            except Exception:
                pass
        pane.tui.log_app(f"cannot open session: {exc.__class__.__name__}", "red")
        return False


def _start_empty_session(pane: Any, *, temporary: bool) -> None:
    if pane._turn_active or pane.tui.operations.is_busy:
        pane.tui.log_app("finish the current turn before switching", "yellow")
        return
    if pane._save_failed:
        pane.tui.log_app("save failed — retry save or discard unsaved work", "yellow")
        return
    if pane._is_temporary and not temporary and pane._session is not None:
        has_work = bool(pane._session.draft.strip() or len(pane._session.attempts) > 0)
        if has_work:
            pane._confirm_discard(
                lambda: pane._start_empty_session(temporary=temporary)
            )
            return
    pane._release_session_lock()
    settings = pane.capture_request_settings()
    pane._session = _sessions_models.new_session(settings, draft="")
    pane._is_temporary = temporary
    pane._read_only = False
    pane._save_revision = 0
    pane._saved_revision = 0
    pane._save_failed = False
    pane._save_error = None
    pane._pending_reconcile = None
    pane._active_session_turn_id = None
    pane._render_session(pane._session)
    pane._update_save_status()
    pane._update_action_visibility()


def _confirm_discard(pane: Any, proceed: Any) -> None:
    from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

    def _done(result: bool | None) -> None:
        if result is True:
            pane._save_failed = False
            pane._save_error = None
            proceed()

    try:
        pane.tui.push_screen(ConfirmScreen("Discard unsaved work?"), _done)
    except Exception:
        pass


def _open_picker(pane: Any) -> None:
    if pane._turn_active:
        pane.tui.log_app("finish the current turn before switching", "yellow")
        return
    if pane._save_failed:
        pane.tui.log_app("save failed — retry save or discard unsaved work", "yellow")
        return
    from mlx_tui.session_screen import SessionScreen  # noqa: PLC0415

    def _done(session_id: str | None) -> None:
        if session_id:
            asyncio.ensure_future(pane.open_session(session_id))

    try:
        pane.tui.push_screen(SessionScreen(), _done)
    except Exception as exc:
        pane.tui.log_app(f"cannot open sessions: {exc.__class__.__name__}", "red")


def _new_session_pressed(pane: Any) -> None:
    pane._start_empty_session(temporary=False)


def _new_temp_pressed(pane: Any) -> None:
    # Creating a temporary session never writes its draft or output.
    pane._start_empty_session(temporary=True)


def _clear_pressed(pane: Any) -> None:
    if pane._turn_active or pane.tui.operations.is_busy:
        pane.tui.log_app("finish the current turn before clearing", "yellow")
        return
    if pane._save_failed:
        pane.tui.log_app("save failed — retry save or discard unsaved work", "yellow")
        return
    if pane._read_only:
        pane.tui.log_app("session is locked read-only", "yellow")
        return
    from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

    def _done(result: bool | None) -> None:
        if result is True:
            asyncio.ensure_future(pane._clear_session())

    try:
        pane.tui.push_screen(ConfirmScreen("Clear this session?"), _done)
    except Exception:
        pass


async def _clear_session(pane: Any) -> None:
    if pane._session is None:
        return
    if pane._is_temporary:
        pane._session = replace(
            _sessions_models.new_session(pane.capture_request_settings(), draft=""),
            updated_at=pane._now_iso(),
        )
        pane._render_session(pane._session)
        pane._update_save_status()
        pane._update_action_visibility()
        return
    cleared = replace(
        pane._session,
        draft="",
        attachments=(),
        attempts=(),
        updated_at=pane._now_iso(),
    )
    pane._session = cleared
    pane._save_revision += 1
    # Clear removes attempts/draft after successful replacement.
    ok = await pane._save_session_now()
    if not ok:
        return
    pane._render_session(cleared)
    pane._update_save_status()
    pane._update_action_visibility()


def _delete_pressed(pane: Any) -> None:
    if pane._turn_active or pane.tui.operations.is_busy:
        pane.tui.log_app("finish the current turn before deleting", "yellow")
        return
    if pane._session is None:
        return
    if pane._is_temporary:
        pane._start_empty_session(temporary=False)
        return
    from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

    def _done(result: bool | None) -> None:
        if result is True:
            asyncio.ensure_future(pane._delete_session())

    try:
        pane.tui.push_screen(ConfirmScreen("Delete this session?"), _done)
    except Exception:
        pass


async def _delete_session(pane: Any) -> None:
    if pane._session is None or pane._is_temporary:
        return
    session_id = pane._session.session_id
    # Delete consumes the already-held lock handle; do not release and
    # reacquire around deletion.
    try:
        if pane._session_lock is not None:
            await asyncio.to_thread(
                _sessions_store.delete_session, session_id, pane._session_lock
            )
        else:
            await asyncio.to_thread(_sessions_store.delete_session, session_id)
    except (
        _sessions_errors.SessionPersistenceError,
        _sessions_errors.SessionValidationError,
    ) as exc:
        pane.tui.log_app(f"delete failed: {exc}", "red")
        return
    finally:
        pane._release_session_lock()
    # Neither clear nor delete clears comparison results or Metrics.
    settings = pane.capture_request_settings()
    pane._session = _sessions_models.new_session(settings, draft="")
    pane._is_temporary = False
    pane._read_only = False
    pane._save_revision = 0
    pane._saved_revision = 0
    pane._save_failed = False
    pane._pending_reconcile = None
    pane._render_session(pane._session)
    pane._update_save_status()
    pane._update_action_visibility()


def _last_retryable_turn(pane: Any) -> SessionTurn | None:
    if pane._session is None:
        return None
    for turn in reversed(pane._session.attempts):
        if turn.outcome in pane._RETRYABLE_OUTCOMES:
            return turn
    return None


def _retry_request_pressed(pane: Any) -> None:
    # Retry restores the most recent unsuccessful draft/settings for review
    # without sending automatically. Previous attempts stay visible; a
    # normal Send creates exactly one new attempt via the operation lease.
    if pane._turn_active or pane.tui.operations.is_busy:
        pane.tui.log_app("finish the current turn before retrying", "yellow")
        return
    if pane._save_failed:
        pane.tui.log_app("save failed — retry save or discard unsaved work", "yellow")
        return
    if pane._read_only:
        pane.tui.log_app("session is locked read-only", "yellow")
        return
    turn = pane._last_retryable_turn()
    if turn is None:
        pane.tui.log_app("no failed turn to retry", "dim")
        return
    try:
        composer = pane.query_one("#chat-input", ChatInput)
        current = composer.text
    except NoMatches:
        current = ""
    if current and current != turn.original_draft:
        # Retain the newer draft and offer explicit replacement.
        from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

        def _done(result: bool | None, _turn: SessionTurn = turn) -> None:
            if result is True:
                pane._apply_retry_draft(_turn)

        try:
            pane.tui.push_screen(
                ConfirmScreen("Replace current draft with retry draft?"), _done
            )
        except Exception:
            pass
        return
    pane._apply_retry_draft(turn)


def _apply_retry_draft(pane: Any, turn: SessionTurn) -> None:
    try:
        composer = pane.query_one("#chat-input", ChatInput)
    except NoMatches:
        return
    pane._restoring = True
    try:
        composer.load_text(turn.original_draft)
    finally:
        pane._restoring = False
    if pane._session is not None:
        pane._session = replace(
            pane._session,
            draft=turn.original_draft,
            attachments=turn.attachments,
            updated_at=pane._now_iso(),
        )
        pane._save_revision += 1
    pane._draft_attachments = turn.attachments
    pane._attachment_index = max(0, len(pane._draft_attachments) - 1)
    pane._refresh_attachment_ui()
    pane._restore_retry_settings(turn.settings)
    pane._update_save_status()
    pane._schedule_draft_save()
    try:
        if not composer.disabled:
            composer.focus()
    except Exception:
        pass
    pane.tui.log_app("retry draft restored — review and Send", "dim")


def _restore_retry_settings(pane: Any, saved: RequestSettings) -> None:
    try:
        pane.query_one(ParamsPane).apply_values(
            saved.temperature, saved.top_p, saved.max_tokens
        )
    except Exception:
        pass
    try:
        cfg = pane.tui.config
        merged = replace(
            cfg,
            temperature=saved.temperature,
            top_p=saved.top_p,
            max_tokens=saved.max_tokens,
            seed=saved.seed,
            enable_thinking=saved.enable_thinking,
            system=saved.system or None,
            max_ctx=saved.max_ctx,
        )
        # Merge request fields only; never restore credentials/commands.
        pane.tui.config = merged
        if saved.model is not None and saved.model != pane.tui.effective_model():
            try:
                pane.tui.select_model(saved.model)
            except Exception:
                pass
    except Exception:
        pass
    try:
        pane.mark_next_request_dirty()
    except Exception:
        pass


def _retry_save_pressed(pane: Any) -> None:
    # Retry save performs no HTTP request.
    asyncio.ensure_future(pane._retry_save())


async def _retry_save(pane: Any) -> bool:
    if pane._is_temporary or pane._read_only:
        return True
    ok = await pane._save_session_now()
    if ok:
        try:
            pane.tui.log_app("saved locally", "dim")
        except Exception:
            pass
    return ok


def _discard_pressed(pane: Any) -> None:
    def _proceed() -> None:
        pane._save_failed = False
        pane._save_error = None
        # Explicit discard unblocks without sending.
        if pane._session is not None:
            pane._save_revision += 1
            pane._saved_revision = pane._save_revision
        pane._update_save_status()
        pane._update_action_visibility()

    pane._confirm_discard(_proceed)


def _use_saved_pressed(pane: Any) -> None:
    saved = pane._pending_reconcile
    if saved is None:
        return
    asyncio.ensure_future(pane._apply_saved_settings(saved))


async def _apply_saved_settings(pane: Any, saved: RequestSettings) -> None:
    # Verify any known exact cached revision/profile fingerprint first.
    if saved.repo_id is not None and saved.revision is not None:
        try:
            from mlx_tui.models import resolve_cached_snapshot  # noqa: PLC0415

            await asyncio.to_thread(
                resolve_cached_snapshot, saved.repo_id, saved.revision
            )
        except Exception:
            pane.tui.log_app(
                "saved model is missing or changed — see Models/Compare recovery",
                "yellow",
            )
    if saved.profile_id is not None and saved.profile_fingerprint is not None:
        entry = pane.tui.profile_entry(saved.profile_id)
        if entry is None or entry.profile.fingerprint != saved.profile_fingerprint:
            pane.tui.log_app(
                "saved profile changed — see Models/Compare recovery", "yellow"
            )
    # Merge only request fields into the current config; never restore
    # endpoint credentials, commands, process identity or runtime ownership.
    from dataclasses import replace as _replace  # noqa: PLC0415

    cfg = pane.tui.config
    merged = _replace(
        cfg,
        temperature=saved.temperature,
        top_p=saved.top_p,
        max_tokens=saved.max_tokens,
        seed=saved.seed,
        enable_thinking=saved.enable_thinking,
        system=saved.system or None,
        max_ctx=saved.max_ctx,
    )
    pane.tui.restore_request_state(merged, saved.model)
    try:
        pane.apply_config_params(merged)
    except Exception:
        pass
    pane._pending_reconcile = None
    # Checkpoint both reconciliation choices through the single dirty path.
    pane.mark_next_request_dirty()
    ok = await pane._save_session_now()
    if ok:
        pane._update_action_visibility()


def _keep_current_pressed(pane: Any) -> None:
    if pane._pending_reconcile is None:
        return
    pane._pending_reconcile = None
    # Checkpoint the continue-with-current choice as well.
    pane.mark_next_request_dirty()
    asyncio.ensure_future(pane._save_session_now())


async def flush_for_shutdown(pane: Any) -> bool:
    """Immediate checkpoint at orderly shutdown; idempotent."""
    if pane._shutdown_flushed:
        return not pane._save_failed
    pane._shutdown_flushed = True
    if pane._session is None or pane._is_temporary or pane._read_only:
        return True
    try:
        composer = pane.query_one("#chat-input", ChatInput)
        draft = composer.text
    except NoMatches:
        draft = None
    if draft is not None and pane._session is not None and draft != pane._session.draft:
        pane._session = replace(pane._session, draft=draft, updated_at=pane._now_iso())
        pane._save_revision += 1
    return await pane._save_session_now()
