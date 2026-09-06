"""Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast, override

import httpx
from rich.markdown import Markdown
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Collapsible, Input, Label, ProgressBar, RichLog, Static
from textual.worker import Worker

from mlx_tui.app.operations import OperationKind
from mlx_tui.chat import error_detail, stream_turn
from mlx_tui.config import AppConfig
from mlx_tui.history.store import TurnRecord
from mlx_tui.history.tokens import ContextLimitError, prepare_context

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_TOP_P = 1.0
_DEFAULT_MAX_TOKENS = 1024
_TEMP_MIN = 0.0
_TEMP_MAX = 2.0
_TOP_P_MIN = 0.0
_TOP_P_MAX = 1.0
_MAX_TOK_MIN = 1
_MAX_TOK_MAX = 16384
_MAX_CONTEXT_TOKENS_EST = 8_192

_PREFIX = "▎ "
_SEPARATOR = "─" * 40


def _parse_clamped_float(raw: str, default: float, lo: float, hi: float) -> float:
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _parse_clamped_int(raw: str, default: int, lo: int, hi: int) -> int:
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


class ChatPane(Vertical):
    """Owns #chat-stream, #chat-log and #chat-input plus the turn worker."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.messages: list[dict[str, str]] = []
        self._cancel_requested: bool = False
        self._turn_active: bool = False
        self._turn_worker: Worker[None] | None = None
        self._turn_started: bool = False

    @property
    def tui(self) -> MlxTuiApp:  # type: ignore[name-defined]
        return cast("MlxTuiApp", self.app)

    @property
    def has_live_turn(self) -> bool:
        return self._turn_active

    @override
    def compose(self) -> ComposeResult:
        with Collapsible(title="Params", collapsed=True, id="params-collapsible"):
            with Horizontal(classes="param-row"):
                yield Label("Temperature (0–2) — randomness", markup=False)
                yield Input(
                    placeholder="0.7",
                    id="param-temp",
                    value="0.7",
                    tooltip="Creativity: 0 deterministic → 2 very random",
                )
            with Horizontal(classes="param-row"):
                yield Label("Top-p (0–1) — diversity", markup=False)
                yield Input(
                    placeholder="1.0",
                    id="param-top-p",
                    value="1.0",
                    tooltip="Nucleus sampling: lower = more focused",
                )
            with Horizontal(classes="param-row"):
                yield Label("Max tokens (1–16384) — length", markup=False)
                yield Input(
                    placeholder="1024",
                    id="param-max-tokens",
                    value="1024",
                    tooltip="Maximum reply length",
                )
        yield Static("", id="chat-stream")
        yield RichLog(id="chat-log", markup=False, wrap=True)
        yield Input(placeholder="message…", id="chat-input")
        yield ProgressBar(
            total=8192, show_percentage=False, show_eta=False, id="ctx-progress"
        )
        yield Static("ctx 0/8k", id="ctx-bar")

    def on_mount(self) -> None:
        self.update_ctx_bar(0)

    def _get_max_ctx(self) -> int:
        v = self.tui.config.max_ctx
        if isinstance(v, int) and v > 0:
            return v
        return _MAX_CONTEXT_TOKENS_EST

    @on(Input.Submitted, "#chat-input")
    def _on_input_submitted(self, event: Input.Submitted) -> None:  # noqa: PLR0915
        text = event.value.strip()
        if not text:
            return
        if not self.tui.operations.try_acquire(OperationKind.CHATTING):
            if self.tui.swap_busy:
                self.tui.log_app("model operation in progress — chat paused", "yellow")
            else:
                self.tui.log_app("operation already in progress", "yellow")
            return
        temp, top_p, max_tok = self._parse_params()
        cfg = self.tui.config
        system_prompt = cfg.system
        raw_max = cfg.max_ctx
        max_ctx = (
            raw_max
            if isinstance(raw_max, int) and raw_max > 0
            else _MAX_CONTEXT_TOKENS_EST
        )
        host = self.tui.host
        port = self.tui.port
        model_at_send = self.tui.effective_model() or "—"
        cold = self.tui.cold_tracker.consume_cold()
        pending_user = {"role": "user", "content": text}
        candidate = list(self.messages) + [pending_user]
        url = f"http://{host}:{port}/v1/chat/completions"
        self._cancel_requested = False
        self._turn_active = True
        self._turn_started = False
        try:
            event.input.clear()
            event.input.disabled = True
        except NoMatches:
            pass
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(Text(f"▎ you › {text}", style="bold"))
        except NoMatches:
            pass
        try:
            worker = self._run_turn(
                candidate,
                pending_user,
                cold,
                temp,
                top_p,
                max_tok,
                model_at_send,
                system_prompt,
                max_ctx,
                url,
                port,
            )
        except Exception as exc:
            self._turn_active = False
            self._turn_worker = None
            self._turn_started = False
            try:
                event.input.disabled = False
            except NoMatches:
                pass
            self.tui.operations.release(OperationKind.CHATTING)
            try:
                self.tui.log_app(
                    f"chat failed to start: {exc.__class__.__name__}", "red"
                )
            except NoMatches:
                pass
            return
        self._turn_worker = worker
        if self._cancel_requested and self._turn_started:
            worker.cancel()

    def _parse_params(self) -> tuple[float, float, int]:
        try:
            raw_temp = self.query_one("#param-temp", Input).value.strip()
            raw_top_p = self.query_one("#param-top-p", Input).value.strip()
            raw_max = self.query_one("#param-max-tokens", Input).value.strip()
        except NoMatches:
            return (_DEFAULT_TEMPERATURE, _DEFAULT_TOP_P, _DEFAULT_MAX_TOKENS)
        temp = _parse_clamped_float(
            raw_temp, _DEFAULT_TEMPERATURE, _TEMP_MIN, _TEMP_MAX
        )
        top_p = _parse_clamped_float(raw_top_p, _DEFAULT_TOP_P, _TOP_P_MIN, _TOP_P_MAX)
        max_tok = _parse_clamped_int(
            raw_max, _DEFAULT_MAX_TOKENS, _MAX_TOK_MIN, _MAX_TOK_MAX
        )
        return (temp, top_p, max_tok)

    def _apply_params_to_inputs(
        self,
        temperature: float | None,
        top_p: float | None,
        max_tokens: int | None,
    ) -> None:
        try:
            if temperature is not None:
                self.query_one("#param-temp", Input).value = str(temperature)
            if top_p is not None:
                self.query_one("#param-top-p", Input).value = str(top_p)
            if max_tokens is not None:
                self.query_one("#param-max-tokens", Input).value = str(max_tokens)
        except NoMatches:
            pass

    def apply_config_params(self, cfg: AppConfig) -> None:
        self._apply_params_to_inputs(
            cfg.temperature if cfg.temperature is not None else _DEFAULT_TEMPERATURE,
            cfg.top_p if cfg.top_p is not None else _DEFAULT_TOP_P,
            cfg.max_tokens if cfg.max_tokens is not None else _DEFAULT_MAX_TOKENS,
        )

    def refresh_context_bar(self) -> None:
        from mlx_tui.history.tokens import (  # noqa: PLC0415
            ContextLimitError,
            prepare_context,
        )

        _, _, max_tok = self._parse_params()
        max_ctx = self._get_max_ctx()
        try:
            window = prepare_context(
                self.messages, self.tui.config.system, max_ctx, max_tok
            )
        except ContextLimitError:
            self.update_ctx_bar(max_ctx)
        else:
            self.update_ctx_bar(window.reserved_tokens)

    def update_ctx_bar(self, ctx_len: int) -> None:
        from mlx_tui.history.tokens import ctx_bar_style, ctx_bar_text  # noqa: PLC0415

        max_ctx = self._get_max_ctx()
        style = ctx_bar_style(ctx_len, max_ctx)
        try:
            bar = self.query_one("#ctx-progress", ProgressBar)
        except NoMatches:
            bar = None
        if bar is not None:
            bar.update(
                total=max_ctx if max_ctx > 0 else 8192,
                progress=max(0, min(ctx_len, max_ctx)),
            )
            bar.remove_class("ctx-bar-amber")
            bar.remove_class("ctx-bar-red")
            if style == "yellow":
                bar.add_class("ctx-bar-amber")
            elif style == "red":
                bar.add_class("ctx-bar-red")
        try:
            label = self.query_one("#ctx-bar", Static)
        except NoMatches:
            return
        label.update(ctx_bar_text(ctx_len, max_ctx))
        label.remove_class("ctx-bar-amber")
        label.remove_class("ctx-bar-red")
        if style == "yellow":
            label.add_class("ctx-bar-amber")
        elif style == "red":
            label.add_class("ctx-bar-red")

    @on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")
    def _on_param_submitted(self, event: Input.Submitted) -> None:
        temp, top_p, max_tok = self._parse_params()
        try:
            self.query_one("#param-temp", Input).value = str(temp)
            self.query_one("#param-top-p", Input).value = str(top_p)
            self.query_one("#param-max-tokens", Input).value = str(max_tok)
        except NoMatches:
            pass

    @work(exclusive=True, group="chat")
    async def _run_turn(  # noqa: PLR0913, PLR0915, PLR0912, PLR0917
        self,
        candidate: list[dict[str, str]],
        pending_user: dict[str, str],
        cold: bool,
        temp: float,
        top_p: float,
        max_tok: int,
        model_at_send: str,
        system_prompt: str | None,
        max_ctx: int,
        url: str,
        port: int,
    ) -> None:
        ctx_len_estimate = 0
        reserved_ctx_len = 0
        self._turn_started = True
        try:
            if self._cancel_requested or (
                self._turn_worker is not None
                and self._turn_worker.cancelled_event.is_set()
            ):
                self._record_cancelled(model_at_send, cold, 0, 0)
                return
            cfg = self.tui.config
            if (
                cfg.temperature != temp
                or cfg.top_p != top_p
                or cfg.max_tokens != max_tok
            ):
                self.tui.config = replace(
                    cfg,
                    temperature=temp,
                    top_p=top_p,
                    max_tokens=max_tok,
                )
            try:
                window = prepare_context(candidate, system_prompt, max_ctx, max_tok)
            except ContextLimitError as exc:
                self._write_system_line(exc.reason, "yellow")
                return
            ctx_len_estimate = window.input_tokens
            reserved_ctx_len = window.reserved_tokens
            self.update_ctx_bar(window.reserved_tokens)
            payload: dict[str, object] = {
                "messages": list(window.messages),
                "stream": True,
                "max_tokens": max_tok,
                "stream_options": {"include_usage": True},
                "temperature": temp,
                "top_p": top_p,
            }
            if model_at_send != "—":
                payload["model"] = model_at_send
            try:
                result = await stream_turn(
                    url,
                    payload,
                    prompt_estimate=window.input_tokens,
                    on_flush=self._update_stream,
                )
            except asyncio.CancelledError:
                self._record_cancelled(
                    model_at_send, cold, ctx_len_estimate, reserved_ctx_len
                )
                raise
            if self._cancel_requested:
                self._record_cancelled(
                    model_at_send, cold, ctx_len_estimate, reserved_ctx_len
                )
                return
            acct = result.accounting
            prompt_tok = acct.prompt_tokens
            out_tok = acct.completion_tokens
            prefill_tok_s: float | None = None
            if not acct.prompt_estimated and prompt_tok > 0 and result.ttft > 0:
                prefill_tok_s = prompt_tok / result.ttft
            ctx_len = prompt_tok if not acct.prompt_estimated else ctx_len_estimate
            record = TurnRecord(
                ts=time.time(),
                model=model_at_send,
                prompt_tok=prompt_tok,
                out_tok=out_tok,
                ttft_s=result.ttft,
                tok_s=acct.tok_s,
                ctx_len=ctx_len,
                cold=cold,
                cancelled=False,
                prefill_tok_s=prefill_tok_s,
                prompt_estimated=acct.prompt_estimated,
                out_estimated=acct.completion_estimated,
            )
            self.tui.history.add(record)
            self.tui._refresh_metrics()
            self.update_ctx_bar(reserved_ctx_len)
            in_label = (
                f"{prompt_tok} (est)" if acct.prompt_estimated else str(prompt_tok)
            )
            out_label = (
                f"{out_tok} (est)" if acct.completion_estimated else str(out_tok)
            )
            if prefill_tok_s is not None:
                stamp = (
                    f"{in_label} in · {out_label} out · "
                    f"{prefill_tok_s:.0f} prefill tok/s · {acct.tok_s:.1f} decode tok/s · TTFT {result.ttft:.2f}s"
                )
            else:
                stamp = (
                    f"{in_label} in · {out_label} out · "
                    f"{acct.tok_s:.1f} tok/s · TTFT {result.ttft:.2f}s"
                )
            notices: list[str] = []
            if result.skipped_frames:
                notices.append(
                    f"{result.skipped_frames} malformed stream frame(s) skipped"
                )
            if result.finish_reason == "length":
                notices.append(
                    f"reply hit the {max_tok}-token cap — ask it to continue"
                )
            if not result.full_text:
                notices.append("model returned no text")
            self._commit_success(pending_user, result.full_text, stamp, cold, notices)
        except ContextLimitError as exc:
            self._write_system_line(exc.reason, "yellow")
        except (httpx.StreamClosed, httpx.ReadError, httpx.RemoteProtocolError):
            if self._cancel_requested:
                self._record_cancelled(
                    model_at_send, cold, ctx_len_estimate, reserved_ctx_len
                )
            else:
                self._write_system_line(f"server unreachable :{port}", "red")
        except (httpx.ConnectError, httpx.TimeoutException):
            self._write_system_line(f"server unreachable :{port}", "red")
        except httpx.HTTPStatusError as exc:
            self._write_system_line(
                f"server error :{port} (HTTP {exc.response.status_code})"
                f"{error_detail(exc.response)}",
                "red",
            )
        except Exception as exc:
            try:
                self.tui.log_app(
                    f"chat failed: {exc.__class__.__name__}: {exc}"[:300], "red"
                )
            except NoMatches:
                pass
            self._write_system_line(f"chat failed: {exc.__class__.__name__}", "red")
        finally:
            self.tui.operations.release(OperationKind.CHATTING)
            self.end_turn()

    def abort(self) -> None:
        if not self._turn_active:
            return
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self._write_system_line("cancellation requested", "dim")
        # Pre-start: let the coroutine's idempotent check settle once without
        # cancelling its task (cancelling before first run would skip cleanup).
        if not self._turn_started:
            return
        worker = self._turn_worker
        if worker is not None:
            worker.cancel()

    def _update_stream(self, text: str) -> None:
        try:
            self.query_one("#chat-stream", Static).update(text)
        except NoMatches:
            pass

    def _commit_success(
        self,
        pending_user: dict[str, str],
        full_text: str,
        stamp: str,
        cold: bool,
        notices: list[str],
    ) -> None:
        # One UI callback with no await between writes: commit pair together.
        self.messages.append(pending_user)
        if full_text:
            self.messages.append({"role": "assistant", "content": full_text})
        self._complete_turn_ui(full_text, stamp, cold, notices)

    def _complete_turn_ui(
        self, full_text: str, stamp: str, cold: bool, notices: list[str]
    ) -> None:
        try:
            log = self.query_one("#chat-log", RichLog)
        except NoMatches:
            return
        stamp_text = f"{stamp} · cold" if cold else stamp
        log.write(Text(f"{_PREFIX}{stamp_text}", style="dim"))
        if full_text:
            log.write(Markdown(full_text))
        for notice in notices:
            log.write(Text(f"{_PREFIX}{notice}", style="yellow"))
        log.write(Text(_SEPARATOR, style="dim"))
        log.write(Text(""))

    def _record_cancelled(
        self,
        model_at_send: str,
        cold: bool,
        ctx_len_estimate: int,
        reserved_ctx_len: int,
    ) -> None:
        record = TurnRecord(
            ts=time.time(),
            model=model_at_send,
            prompt_tok=0,
            out_tok=0,
            ttft_s=0.0,
            tok_s=0.0,
            ctx_len=ctx_len_estimate,
            cold=cold,
            cancelled=True,
        )
        self.tui.history.add(record)
        self.tui._refresh_metrics()
        self.update_ctx_bar(reserved_ctx_len)
        self._write_system_line("cancelled — request aborted", "dim")

    def _write_system_line(self, message: str, style: str) -> None:
        try:
            log = self.query_one("#chat-log", RichLog)
        except NoMatches:
            return
        text = f"{_PREFIX}{message}" if style == "dim" else message
        log.write(Text(text, style=style))

    def end_turn(self) -> None:
        if not self._turn_active:
            return
        try:
            self._update_stream("")
            self._turn_worker = None
        finally:
            self._turn_active = False
            try:
                inp = self.query_one("#chat-input", Input)
            except NoMatches:
                return
            if self.tui.operations.current in (
                OperationKind.IDLE,
                OperationKind.CHATTING,
            ):
                inp.disabled = False
                inp.focus()
            else:
                inp.disabled = False
