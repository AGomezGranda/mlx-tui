"""Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override

import httpx
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Collapsible, Input, Label, RichLog, Static

from mlx_tui.config import AppConfig

from . import params, turn

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class ChatPane(Vertical):
    """Owns #chat-stream, #chat-log and #chat-input plus the turn worker."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.messages: list[dict[str, str]] = []
        self._system_prompt: str = ""
        self._cancel_requested: bool = False
        self._active_response: httpx.Response | None = None

    @property
    def tui(self) -> MlxTuiApp:  # type: ignore[name-defined]
        return cast("MlxTuiApp", self.app)

    @property
    def has_live_turn(self) -> bool:
        return self._active_response is not None

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
        yield Static("ctx 0/8k", id="ctx-bar")

    def on_mount(self) -> None:
        try:
            self.update_ctx_bar(0)
        except Exception:
            pass

    @on(Input.Submitted, "#chat-input")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self.tui.swap_busy:
            self.tui.log_app("model swapping — chat paused", "yellow")
            return
        event.input.clear()
        self.messages.append({"role": "user", "content": text})
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"you › {text}"))
        # refresh ctx bar immediately on user message (estimate, trimmed to max_ctx)
        try:
            from mlx_tui.history.tokens import (  # noqa: PLC0415
                estimate_tokens,
                trim_for_context,
            )

            max_ctx = int(getattr(self.tui.config, "max_ctx", 8000))
            trimmed = trim_for_context(self.messages, max_ctx)
            ctx_est = sum(estimate_tokens(m["content"]) for m in trimmed)
            self.update_ctx_bar(ctx_est)
        except Exception:
            pass
        cold = self.tui.cold_tracker.consume_cold()
        self._cancel_requested = False
        event.input.disabled = True
        self._run_turn(list(self.messages), cold)

    def _parse_params(self) -> tuple[float, float, int]:
        return params.parse_params(self)  # type: ignore[arg-type]

    def _apply_params_to_inputs(
        self,
        temperature: float | None,
        top_p: float | None,
        max_tokens: int | None,
    ) -> None:
        return params.apply_params_to_inputs(self, temperature, top_p, max_tokens)  # type: ignore[arg-type]

    def apply_config_params(self, cfg: AppConfig) -> None:
        return params.apply_config_params(self, cfg)  # type: ignore[arg-type]

    def update_ctx_bar(self, ctx_len: int) -> None:
        from mlx_tui.history.tokens import ctx_bar_style, ctx_bar_text  # noqa: PLC0415

        try:
            max_ctx = int(getattr(self.tui.config, "max_ctx", 8000))
        except Exception:
            max_ctx = 8000
        try:
            bar = self.query_one("#ctx-bar", Static)
        except Exception:
            return
        bar.update(ctx_bar_text(ctx_len, max_ctx))
        style = ctx_bar_style(ctx_len, max_ctx)
        bar.remove_class("ctx-bar-amber")
        bar.remove_class("ctx-bar-red")
        if style == "yellow":
            bar.add_class("ctx-bar-amber")
        elif style == "red":
            bar.add_class("ctx-bar-red")

    def set_system_prompt(self, text: str) -> None:
        self._system_prompt = text.strip()

    @on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")
    def _on_param_submitted(self, event: Input.Submitted) -> None:
        return params.on_param_submitted(self, event)  # type: ignore[arg-type]

    @work(exclusive=True, group="chat", thread=True)
    def _run_turn(self, messages: list[dict[str, str]], cold: bool) -> None:
        return turn.run_turn_impl(self, messages, cold)  # type: ignore[arg-type]

    def abort(self) -> None:
        return turn.abort(self)  # type: ignore[arg-type]

    def _update_stream(self, text: str) -> None:
        return turn.update_stream(self, text)  # type: ignore[arg-type]

    def _complete_turn_ui(
        self, full_text: str, stamp: str, cold: bool, notices: list[str]
    ) -> None:
        return turn.complete_turn_ui(self, full_text, stamp, cold, notices)  # type: ignore[arg-type]

    def _record_cancelled(
        self, model_at_send: str, cold: bool, ctx_len_estimate: int
    ) -> None:
        return turn.record_cancelled(self, model_at_send, cold, ctx_len_estimate)  # type: ignore[arg-type]

    def _write_system_line(self, message: str, style: str) -> None:
        return turn.write_system_line(self, message, style)  # type: ignore[arg-type]

    def end_turn(self) -> None:
        return turn.end_turn(self)  # type: ignore[arg-type]
