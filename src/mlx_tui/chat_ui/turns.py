"""Chat turn execution helpers (pane-parameterized)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import replace
from typing import Any

import httpx
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Button, Input, TabbedContent, TextArea

from mlx_tui.chat import (
    TurnProgress,
    TurnResult,
    _merge_tool_fragments,
    error_detail,
    stream_turn,
)
from mlx_tui.chat_turn import ChatTurn, render_session_turn  # noqa: F401
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.history.store import TurnRecord
from mlx_tui.history.tokens import (
    ContextLimitError,
    ContextWindow,
)
from mlx_tui.operations import OperationKind
from mlx_tui.params import ParamsPane
from mlx_tui.sessions.models import SessionTurn


def _send_blocked_reason(pane: Any) -> str | None:
    if pane._read_only:
        return "session is locked read-only — open an editable copy"
    if pane._save_failed:
        return "save failed — retry save or discard unsaved work"
    if pane._pending_reconcile is not None:
        return "settings differ — use saved or keep current settings"
    return None


def _on_draft_changed(pane: Any, event: TextArea.Changed) -> None:
    if pane._restoring or pane._session is None:
        return
    try:
        draft_value = event.text_area.text
    except Exception:
        try:
            draft_value = event.control.text  # type: ignore[attr-defined]
        except Exception:
            return
    if pane._read_only or pane._is_temporary:
        # Temporary sessions never write; still update in-memory draft.
        if pane._is_temporary and pane._session is not None:
            pane._session = replace(
                pane._session,
                draft=draft_value,
                attachments=pane._draft_attachments,
            )
        pane._update_save_status()
        pane.refresh_context_bar()
        return
    pane._ensure_session()
    assert pane._session is not None
    pane._session = replace(
        pane._session,
        draft=draft_value,
        attachments=pane._draft_attachments,
        updated_at=pane._now_iso(),
    )
    pane._save_revision += 1
    pane._update_save_status()
    pane._schedule_draft_save()
    pane.refresh_context_bar()


def _on_param_changed(pane: Any, _event: Input.Changed) -> None:
    if pane._restoring:
        return
    pane.mark_next_request_dirty()


def _on_input_submitted(pane: Any, event: ChatInput.Submitted) -> None:
    pane._do_submit(event.text, event.chat_input)


def _send_pressed(pane: Any) -> None:
    try:
        composer = pane.query_one("#chat-input", ChatInput)
    except NoMatches:
        return
    pane._do_submit(composer.text, composer)


def _do_submit(pane: Any, raw: str, editor: ChatInput) -> None:  # noqa: PLR0911, PLR0912, PLR0915
    # Test emptiness with strip() but send the original text so code
    # indentation is preserved.
    if not raw.strip():
        return
    blocked = pane._send_blocked_reason()
    if blocked is not None:
        pane.tui.log_app(blocked, "yellow")
        return
    selected_model = pane.tui.effective_model()
    if selected_model is None:
        pane.tui.log_app(
            "select a model in Models (or set model in config) before chatting",
            "yellow",
        )
        return
    draft_attachments = tuple(pane._draft_attachments)
    if draft_attachments and not pane._is_loopback_destination(pane.tui.host):
        pane.tui.log_app(
            "file attachments are disabled for non-loopback destinations",
            "yellow",
        )
        return
    try:
        rendered, window = pane._prepare_window(raw, draft_attachments)
    except ContextLimitError as exc:
        pane._context_error = exc.reason
        pane._refresh_attachment_ui()
        try:
            rejected = ChatTurn(raw)
            pane.query_one("#chat-transcript", VerticalScroll).mount(rejected)
            rejected.add_notice(exc.reason, "yellow")
            rejected.end_attempt()
        except NoMatches:
            pass
        pane.tui.log_app(exc.reason, "yellow")
        return
    except Exception as exc:
        try:
            editor.load_text(raw)
        except Exception:
            pass
        pane.tui.log_app(f"chat setup failed: {exc.__class__.__name__}", "red")
        return
    pane._context_error = None
    if not pane.tui.operations.try_acquire(OperationKind.CHATTING):
        if pane.tui.swap_busy:
            pane.tui.log_app("model operation in progress — chat paused", "yellow")
        else:
            pane.tui.log_app("operation already in progress", "yellow")
        return
    pane._pending_draft = raw
    pane._cancel_requested = False
    pane._turn_active = True
    pane._turn_started = False
    # Pre-send checkpoint state: capture normalized controls and the exact
    # model through the single dirty path; the worker persists the running
    # attempt before any HTTP.
    session_settings = pane.capture_request_settings()
    running_turn = SessionTurn(
        turn_id=str(uuid.uuid4()),
        created_at=pane._now_iso(),
        original_draft=raw,
        sent_content=rendered,
        settings=session_settings,
        attachments=draft_attachments,
        included_turn_ids=pane._included_turn_ids(window),
    )
    pane._active_session_turn_id = running_turn.turn_id
    pane._progress_answer = ""
    pane._progress_reasoning = ""
    pane._progress_tools = dict[int, dict[str, object]]()
    pane._progress_response_model = None
    pane._last_checkpoint = time.monotonic()
    # Cancel any pending draft debounce; the pre-send checkpoint covers it.
    try:
        if pane._draft_timer is not None:
            pane._draft_timer.stop()
            pane._draft_timer = None
    except Exception:
        pass
    if pane._session is not None and not pane._is_temporary and not pane._read_only:
        pane._session = replace(
            pane._session,
            settings=session_settings,
            draft="",
            attachments=(),
            attempts=(*pane._session.attempts, running_turn),
            updated_at=pane._now_iso(),
        )
        pane._save_revision += 1
        pane._update_save_status()
    pane._draft_attachments = ()
    pane._attachment_index = 0
    pane._refresh_attachment_ui()
    try:
        try:
            composer_widget = pane.query_one("#chat-input", ChatInput)
        except NoMatches:
            composer_widget = None
        # Prefer the explicit editor when it is still mounted.
        target = editor if editor is not None else composer_widget
        if target is None:
            target = composer_widget
        had_focus = False
        try:
            had_focus = pane.app.focused is target
        except Exception:
            had_focus = False
        if target is not None:
            try:
                target.clear()
            except Exception:
                try:
                    target.load_text("")
                except Exception:
                    pass
            try:
                target.disabled = True
            except Exception:
                pass
        else:
            had_focus = False
        # Keep Send disabled while the lease is held.
        try:
            pane.query_one("#btn-send", Button).disabled = True
        except (NoMatches, Exception):
            pass
        pane._restore_composer_focus = had_focus
        pane._active_turn = ChatTurn(rendered)
        pane.query_one("#chat-transcript", VerticalScroll).mount(pane._active_turn)
        pane._queue_follow(force=True)
        pane.tui.refresh_activity()
        temp, top_p, max_tok = pane.query_one(ParamsPane).read_values()
        seed = pane.tui.config.seed
        enable_thinking = pane.tui.config.enable_thinking
        host = pane.tui.host
        port = pane.tui.port
        model_at_send = selected_model
        pending_user = {"role": "user", "content": rendered}
        rendered_host = f"[{host}]" if ":" in host else host
        url = f"http://{rendered_host}:{port}/v1/chat/completions"
        worker = pane._run_turn(
            window,
            pending_user,
            temp,
            top_p,
            max_tok,
            model_at_send,
            seed,
            enable_thinking,
            url,
            port,
        )
    except Exception as exc:
        try:
            pane._write_system_line(
                f"chat failed to start: {exc.__class__.__name__}", "red"
            )
            pane.tui.log_app(f"chat failed to start: {exc.__class__.__name__}", "red")
        except NoMatches:
            pass
        finally:
            # Startup failure never sends; drop the optimistic attempt but
            # keep the exact draft via _pending_draft for end_turn restore.
            pane._drop_running_attempt()
            pane.end_turn()
        return
    pane._turn_worker = worker
    if pane._cancel_requested and pane._turn_started:
        worker.cancel()


def apply_config_params(pane: Any, cfg: AppConfig) -> None:
    pane._restoring = True
    try:
        pane.query_one(ParamsPane).apply_config(cfg)
    finally:
        pane._restoring = False
    # Every next-request mutation checkpoints through the single dirty path.
    if pane._session is not None and not pane._restoring:
        pane.mark_next_request_dirty()


async def _run_turn(  # noqa: PLR0913, PLR0915, PLR0912, PLR0917
    pane: Any,
    window: ContextWindow,
    pending_user: dict[str, str],
    temp: float,
    top_p: float,
    max_tok: int,
    model_at_send: str,
    seed: int | None,
    enable_thinking: bool | None,
    url: str,
    port: int,
) -> None:
    ctx_len_estimate = 0
    reserved_ctx_len = 0
    excluded_turns = 0
    pane._turn_started = True
    # Pre-send checkpoint: persist the draft and running attempt before HTTP.
    # If saving fails, retain the composer and send no request.
    if (
        pane._session is not None
        and not pane._is_temporary
        and not pane._read_only
        and pane._active_session_turn_id is not None
    ):
        saved = await pane._save_session_now()
        if not saved:
            pane._drop_running_attempt()
            pane._write_system_line(
                "save failed — retry save or discard unsaved work", "red"
            )
            # The early return bypasses the try/finally below, so release
            # the lease and restore the composer here.
            pane.end_turn()
            return
    try:
        if pane._cancel_requested or (
            pane._turn_worker is not None and pane._turn_worker.cancelled_event.is_set()
        ):
            pane._record_cancelled(model_at_send, 0, 0)
            pane._finalize_session_attempt(
                outcome="cancelled",
                error_category="cancelled",
                error_detail="client request cancelled, engine state unknown",
            )
            await pane._checkpoint_terminal()
            return
        cfg = pane.tui.config
        if cfg.temperature != temp or cfg.top_p != top_p or cfg.max_tokens != max_tok:
            pane.tui.config = replace(
                cfg,
                temperature=temp,
                top_p=top_p,
                max_tokens=max_tok,
            )
        ctx_len_estimate = window.input_tokens
        reserved_ctx_len = window.reserved_tokens
        excluded_turns = window.excluded_turns
        pane.update_ctx_bar(window.reserved_tokens, window.excluded_turns)
        pane._update_running_estimates(
            ctx_len_estimate,
            reserved_ctx_len,
            excluded_turns,
            pane._included_turn_ids(window),
        )
        payload: dict[str, object] = {
            "messages": list(window.messages),
            "stream": True,
            "max_tokens": max_tok,
            "stream_options": {"include_usage": True},
            "temperature": temp,
            "top_p": top_p,
        }
        if seed is not None:
            payload["seed"] = seed
        if enable_thinking is not None:
            payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
        payload["model"] = model_at_send
        try:
            result = await stream_turn(
                url,
                payload,
                prompt_estimate=window.input_tokens,
                on_flush=pane._update_stream,
                on_activity=pane._update_activity,
                on_progress=pane._on_turn_progress,
            )
        except asyncio.CancelledError:
            pane._record_cancelled(model_at_send, ctx_len_estimate, reserved_ctx_len)
            pane._finalize_session_attempt(
                outcome="cancelled",
                error_category="cancelled",
                error_detail="client request cancelled, engine state unknown",
            )
            await pane._checkpoint_terminal()
            raise
        if pane._cancel_requested:
            pane._record_cancelled(model_at_send, ctx_len_estimate, reserved_ctx_len)
            pane._finalize_session_attempt(
                outcome="cancelled",
                error_category="cancelled",
                error_detail="client request cancelled, engine state unknown",
            )
            await pane._checkpoint_terminal()
            return
        if result.response_model != model_at_send:
            pane.tui.record_generation_success(model_at_send, result.response_model)
            pane._write_system_line(
                "response identity missing or mismatched — request not verified",
                "red",
            )
            pane._finalize_session_attempt(
                outcome="failed",
                response_model=result.response_model,
                finish_reason=result.finish_reason,
                skipped_frames=result.skipped_frames,
                stream_complete=result.stream_complete,
                error_category="identity",
                error_detail="response identity missing or mismatched",
            )
            await pane._checkpoint_terminal()
            return
        pane.tui.record_generation_success(model_at_send, result.response_model)
        outcome = pane._classify_outcome(result)
        stamp = pane._format_stamp(result, max_tok)
        notices = pane._outcome_notices(result, max_tok, excluded_turns)
        # Final success still comes only from TurnResult and the pane's
        # identity check, never from a progress notification.
        pane._finalize_session_attempt(
            outcome=outcome,
            answer=result.full_text,
            reasoning=result.reasoning_text,
            tool_calls=result.tool_calls,
            response_model=result.response_model,
            finish_reason=result.finish_reason,
            skipped_frames=result.skipped_frames,
            stream_complete=result.stream_complete,
            prompt_tokens=result.accounting.prompt_tokens,
            completion_tokens=result.accounting.completion_tokens,
            cached_prompt_tokens=result.cached_prompt_tokens,
            first_output_s=result.first_output_s,
            answer_started_s=result.answer_started_s,
            total_s=result.total_s,
        )
        pane._record_outcome(
            model_at_send,
            result,
            outcome,
            ctx_len_estimate,
            reserved_ctx_len,
            excluded_turns,
        )
        if outcome == "success":
            pane._commit_success(
                pending_user,
                result.full_text,
                stamp,
                notices,
                result.reasoning_text,
                result.tool_calls,
            )
        else:
            # Length-capped, damaged, tool-only and empty outcomes stay visible
            # but neither side enters future history, so a retry resends
            # the same context. The pending draft is intentionally left set so
            # end_turn() restores it to the composer.
            pane._complete_turn_ui(
                result.full_text,
                stamp,
                notices,
                result.reasoning_text,
                result.tool_calls,
            )
        # A completed generation whose save fails remains visible in memory
        # and retains its outcome; further send/clear/switch stays blocked.
        await pane._checkpoint_terminal()
    except ContextLimitError as exc:
        pane._write_system_line(exc.reason, "yellow")
        pane._finalize_session_attempt(
            outcome="failed",
            error_category="context",
            error_detail=exc.reason[:500],
        )
        await pane._checkpoint_terminal()
    except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
        pane.tui.record_generation_failure(cancelled=pane._cancel_requested)
        if pane._cancel_requested:
            pane._record_cancelled(model_at_send, ctx_len_estimate, reserved_ctx_len)
            pane._finalize_session_attempt(
                outcome="cancelled",
                error_category="cancelled",
                error_detail="client request cancelled, engine state unknown",
            )
        else:
            pane._write_system_line(f"server unreachable :{port}", "red")
            pane._finalize_session_attempt(
                outcome="failed",
                error_category="stream",
                error_detail=f"server unreachable :{port}"[:500],
            )
        await pane._checkpoint_terminal()
    except (httpx.ConnectError, httpx.TimeoutException):
        pane.tui.record_generation_failure()
        pane._write_system_line(f"server unreachable :{port}", "red")
        pane._finalize_session_attempt(
            outcome="failed",
            error_category="transport",
            error_detail=f"server unreachable :{port}"[:500],
        )
        await pane._checkpoint_terminal()
    except httpx.HTTPStatusError as exc:
        pane.tui.record_generation_failure()
        detail = error_detail(exc.response)
        is_context_error = exc.response.status_code in {400, 413}
        category = "context" if is_context_error else "server"
        message = (
            "context rejected by server — reduce context or output allowance"
            if is_context_error
            else f"server error :{port} (HTTP {exc.response.status_code}){detail}"
        )
        pane._write_system_line(
            message,
            "red",
        )
        pane._finalize_session_attempt(
            outcome="failed",
            error_category=category,
            error_detail=(f"HTTP {exc.response.status_code}{detail}")[:500],
        )
        await pane._checkpoint_terminal()
    except Exception as exc:
        pane.tui.record_generation_failure()
        try:
            pane.tui.log_app(
                f"chat failed: {exc.__class__.__name__}: {exc}"[:300], "red"
            )
        except NoMatches:
            pass
        pane._write_system_line(f"chat failed: {exc.__class__.__name__}", "red")
        pane._finalize_session_attempt(
            outcome="failed",
            error_category="transport",
            error_detail=f"{exc.__class__.__name__}: {exc}"[:500],
        )
        await pane._checkpoint_terminal()
    finally:
        pane.end_turn()


def _classify_outcome(result: TurnResult) -> str:
    if not result.stream_complete:
        if result.finish_reason == "length":
            return "length_capped"
        if result.tool_calls and not result.full_text:
            return "tool_only"
        if not result.full_text and not result.tool_calls:
            return "empty"
        return "incomplete"
    return "success"


def _format_stamp(result: TurnResult, max_tok: int) -> str:
    _ = max_tok
    acct = result.accounting
    if result.stream_complete:
        in_label = (
            f"{acct.prompt_tokens}~"
            if acct.prompt_estimated
            else str(acct.prompt_tokens)
        )
        out_label = (
            f"{acct.completion_tokens}~"
            if acct.completion_estimated
            else str(acct.completion_tokens)
        )
    else:
        in_label = "—"
        out_label = "—"
    first = (
        f"{result.first_output_s:.2f}s" if result.first_output_s is not None else "—"
    )
    answer = (
        f"{result.answer_started_s:.2f}s"
        if result.answer_started_s is not None
        else "—"
    )
    total = f"{result.total_s:.2f}s"
    rate = (
        f"{acct.tok_s:.1f} client request tok/s"
        if result.stream_complete
        else "— client request tok/s"
    )
    stamp = (
        f"{in_label} in · {out_label} out · "
        f"first {first} · answer {answer} · total {total} · {rate}"
    )
    if result.cached_prompt_tokens is not None and result.stream_complete:
        stamp += f" · cached {result.cached_prompt_tokens} reuse"
    return stamp


def _outcome_notices(
    result: TurnResult, max_tok: int, excluded_turns: int
) -> list[str]:
    notices: list[str] = []
    if result.skipped_frames:
        notices.append(f"{result.skipped_frames} malformed stream frame(s) skipped")
    if result.finish_reason == "length":
        notices.append(f"reply hit the {max_tok}-token cap — ask it to continue")
    if result.tool_calls and not result.full_text:
        notices.append("tool output only — not executed, not in next request")
    elif not result.full_text:
        notices.append("model returned no text")
    if not result.stream_complete and result.finish_reason != "length":
        notices.append("incomplete stream — not in next request")
    if excluded_turns > 0:
        notices.append(f"{excluded_turns} earlier message(s) excluded from request")
    return notices


def _record_outcome(  # noqa: PLR0913, PLR0917
    pane: Any,
    model_at_send: str,
    result: TurnResult,
    outcome: str,
    ctx_len_estimate: int,
    reserved_ctx_len: int,
    excluded_turns: int,
) -> None:
    acct = result.accounting
    success = outcome == "success"
    record = TurnRecord(
        ts=time.time(),
        model=model_at_send,
        prompt_tok=acct.prompt_tokens if success else None,
        out_tok=acct.completion_tokens if success else None,
        first_output_s=result.first_output_s,
        answer_started_s=result.answer_started_s,
        total_s=result.total_s,
        req_tok_s=acct.tok_s if success else None,
        ctx_len=acct.prompt_tokens
        if success and not acct.prompt_estimated
        else ctx_len_estimate,
        outcome=outcome,
        cancelled=False,
        cached_prompt_tokens=result.cached_prompt_tokens,
        prompt_estimated=success and acct.prompt_estimated,
        out_estimated=success and acct.completion_estimated,
        excluded_turns=excluded_turns,
    )
    pane.tui.history.add(record)
    pane.tui._refresh_metrics()
    pane.update_ctx_bar(reserved_ctx_len, excluded_turns)


def _drop_running_attempt(pane: Any) -> None:
    if pane._session is None or pane._active_session_turn_id is None:
        return
    turn_id = pane._active_session_turn_id
    original: str | None = None
    for turn in pane._session.attempts:
        if turn.turn_id == turn_id:
            original = turn.original_draft
            break
    remaining = tuple(t for t in pane._session.attempts if t.turn_id != turn_id)
    draft = original if original is not None else pane._session.draft
    pane._session = replace(
        pane._session,
        draft=draft,
        attempts=remaining,
        updated_at=pane._now_iso(),
    )
    pane._active_session_turn_id = None
    pane._save_revision += 1


def _update_running_estimates(
    pane: Any,
    estimated_input: int,
    reserved_output: int,
    excluded: int,
    included_turn_ids: tuple[str, ...],
) -> None:
    if pane._session is None or pane._active_session_turn_id is None:
        return
    attempts: list[SessionTurn] = []
    for turn in pane._session.attempts:
        if turn.turn_id == pane._active_session_turn_id:
            attempts.append(
                replace(
                    turn,
                    included_turn_ids=included_turn_ids,
                    estimated_input=estimated_input,
                    reserved_output=reserved_output,
                    excluded_messages=excluded,
                )
            )
        else:
            attempts.append(turn)
    pane._session = replace(
        pane._session, attempts=tuple(attempts), updated_at=pane._now_iso()
    )


def _on_turn_progress(pane: Any, progress: TurnProgress) -> None:
    # Capture progress directly from TurnProgress deltas; never infer
    # reasoning from the tool-count activity string.
    if progress.answer_delta:
        pane._progress_answer += progress.answer_delta
    if progress.reasoning_delta:
        pane._progress_reasoning += progress.reasoning_delta
    _merge_tool_fragments(pane._progress_tools, progress.tool_fragments)
    if progress.response_model:
        pane._progress_response_model = progress.response_model
    # Stream at most once per second; terminal/switch/clear/shutdown flush.
    now = time.monotonic()
    if now - pane._last_checkpoint >= 1.0:
        pane._last_checkpoint = now
        pane._save_session_async()


def _merged_progress_tools(pane: Any) -> tuple[dict[str, Any], ...]:
    merged = tuple({**slot} for _, slot in sorted(pane._progress_tools.items()))
    return tuple(
        tool
        for tool in merged
        if len(tool) > 1
        and not (set(tool) == {"index", "arguments"} and not tool["arguments"])
    )


def _finalize_session_attempt(  # noqa: PLR0913, PLR0917
    pane: Any,
    outcome: str,
    answer: str | None = None,
    reasoning: str | None = None,
    tool_calls: tuple[dict[str, object], ...] | None = None,
    response_model: str | None = None,
    finish_reason: str | None = None,
    skipped_frames: int | None = None,
    stream_complete: bool | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cached_prompt_tokens: int | None = None,
    first_output_s: float | None = None,
    answer_started_s: float | None = None,
    total_s: float | None = None,
    error_category: str | None = None,
    error_detail: str | None = None,  # noqa: A002
) -> None:
    if pane._session is None or pane._active_session_turn_id is None:
        return
    if pane._is_temporary:
        return
    final_answer = answer if answer is not None else pane._progress_answer
    final_reasoning = reasoning if reasoning is not None else pane._progress_reasoning
    final_tools = (
        tuple(dict(call) for call in tool_calls)
        if tool_calls is not None
        else tuple(dict(call) for call in pane._merged_progress_tools())
    )
    final_response = (
        response_model if response_model is not None else pane._progress_response_model
    )
    attempts: list[SessionTurn] = []
    for turn in pane._session.attempts:
        if turn.turn_id != pane._active_session_turn_id:
            attempts.append(turn)
            continue
        attempts.append(
            replace(
                turn,
                answer=final_answer,
                reasoning=final_reasoning,
                tool_calls=final_tools,  # type: ignore[arg-type]
                response_model=final_response,
                finish_reason=finish_reason
                if finish_reason is not None
                else turn.finish_reason,
                skipped_frames=skipped_frames
                if skipped_frames is not None
                else turn.skipped_frames,
                stream_complete=stream_complete
                if stream_complete is not None
                else turn.stream_complete,
                outcome=outcome,
                prompt_tokens=prompt_tokens
                if prompt_tokens is not None
                else turn.prompt_tokens,
                completion_tokens=completion_tokens
                if completion_tokens is not None
                else turn.completion_tokens,
                cached_prompt_tokens=cached_prompt_tokens
                if cached_prompt_tokens is not None
                else turn.cached_prompt_tokens,
                first_output_s=first_output_s
                if first_output_s is not None
                else turn.first_output_s,
                answer_started_s=answer_started_s
                if answer_started_s is not None
                else turn.answer_started_s,
                total_s=total_s if total_s is not None else turn.total_s,
                error_category=error_category,
                error_detail=error_detail,
            )
        )
    total_tokens: int | None = None
    if prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens is not None:
        attempts = [
            replace(turn, total_tokens=total_tokens)
            if turn.turn_id == pane._active_session_turn_id and turn.outcome == outcome
            else turn
            for turn in attempts
        ]
    pane._session = replace(
        pane._session, attempts=tuple(attempts), updated_at=pane._now_iso()
    )
    pane._save_revision += 1


async def _checkpoint_terminal(pane: Any) -> None:
    if pane._session is None or pane._is_temporary or pane._read_only:
        pane._active_session_turn_id = None
        return
    # Immediate checkpoint at terminal outcome.
    ok = await pane._save_session_now()
    pane._active_session_turn_id = None
    if not ok:
        try:
            pane.tui.log_app("save failed — retry save or discard unsaved work", "red")
        except Exception:
            pass


def abort(pane: Any) -> None:
    if not pane._turn_active:
        return
    if pane._cancel_requested:
        return
    pane._cancel_requested = True
    pane.tui.refresh_activity()
    pane._write_system_line("cancellation requested", "dim")
    # Pre-start: let the coroutine's idempotent check settle once without
    # cancelling its task (cancelling before first run would skip cleanup).
    if not pane._turn_started:
        return
    worker = pane._turn_worker
    if worker is not None:
        worker.cancel()


async def wait_for_cleanup(pane: Any) -> None:
    worker = pane._turn_worker
    if worker is None:
        return
    try:
        await worker.wait()
    except Exception:
        pass


def _commit_success(  # noqa: PLR0913, PLR0917
    pane: Any,
    pending_user: dict[str, str],
    full_text: str,
    stamp: str,
    notices: list[str],
    reasoning: str = "",
    tool_calls: tuple[dict[str, object], ...] = (),
) -> None:
    # One UI callback with no await between writes: commit pair together.
    pane.messages.append(pending_user)
    if full_text:
        pane.messages.append({"role": "assistant", "content": full_text})
    pane._pending_draft = None
    pane._complete_turn_ui(full_text, stamp, notices, reasoning, tool_calls)


def _complete_turn_ui(  # noqa: PLR0913, PLR0917
    pane: Any,
    full_text: str,
    stamp: str,
    notices: list[str],
    reasoning: str = "",
    tool_calls: tuple[dict[str, object], ...] = (),
) -> None:
    if pane._active_turn is not None:
        pane._queue_follow()
        pane._active_turn.finish_response(
            full_text, stamp, notices, reasoning, tool_calls
        )


def _record_cancelled(
    pane: Any,
    model_at_send: str,
    ctx_len_estimate: int,
    reserved_ctx_len: int,
) -> None:
    pane.tui.record_generation_failure(cancelled=True)
    record = TurnRecord(
        ts=time.time(),
        model=model_at_send,
        prompt_tok=None,
        out_tok=None,
        first_output_s=None,
        answer_started_s=None,
        total_s=None,
        req_tok_s=None,
        ctx_len=ctx_len_estimate,
        outcome="cancelled",
        cancelled=True,
    )
    pane.tui.history.add(record)
    pane.tui._refresh_metrics()
    pane.update_ctx_bar(reserved_ctx_len)
    pane._write_system_line(
        "cancelled — client request cancelled, engine state unknown", "dim"
    )


def end_turn(pane: Any) -> None:
    pane.tui.operations.release(OperationKind.CHATTING)
    turn, pane._active_turn = pane._active_turn, None
    pane._turn_worker = None
    pane._turn_active = False
    pane._turn_started = False
    restore_focus, pane._restore_composer_focus = (
        pane._restore_composer_focus,
        False,
    )
    try:
        if turn is not None:
            turn.end_attempt()
    finally:
        try:
            inp = pane.query_one("#chat-input", ChatInput)
        except NoMatches:
            pane._pending_draft = None
        else:
            if pane._pending_draft is not None and not inp.text:
                pane._restoring = True
                try:
                    inp.load_text(pane._pending_draft)
                finally:
                    pane._restoring = False
            pane._pending_draft = None
            try:
                pane._update_action_visibility()
                # Preserve the session block without stealing lease state.
                if pane._turn_active or pane.tui.operations.is_busy:
                    inp.disabled = True
                else:
                    blocked = (
                        pane._save_failed
                        or pane._pending_reconcile is not None
                        or pane._read_only
                    )
                    inp.disabled = blocked
            except Exception:
                try:
                    inp.disabled = pane.tui.operations.is_busy
                except Exception:
                    pass
            if (
                restore_focus
                and not inp.disabled
                and pane.is_mounted
                and pane.app.screen is pane.screen
                and pane.tui.query_one(TabbedContent).active == "chat"
            ):
                inp.focus()
        pane.tui.refresh_activity()


# -- Session controls (2g), reopen (2i), shutdown (2j) --
