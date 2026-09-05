"""Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast, override

import httpx
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Collapsible, Input, Label, ProgressBar, RichLog, Static

from mlx_tui.app.operations import OperationKind
from mlx_tui.config import AppConfig
from mlx_tui.history.tokens import ContextLimitError, prepare_context

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
        self._turn_active: bool = False
        self._active_response: httpx.Response | None = None

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
        try:
            self.update_ctx_bar(0)
        except Exception:
            pass

    @on(Input.Submitted, "#chat-input")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if not self.tui.operations.try_acquire(OperationKind.CHATTING):
            if self.tui.swap_busy:
                self.tui.log_app("model operation in progress — chat paused", "yellow")
            else:
                self.tui.log_app("operation already in progress", "yellow")
            return
        event.input.clear()
        submitted_message = {"role": "user", "content": text}
        self.messages.append(submitted_message)
        log = self.query_one("#chat-log", RichLog)
        log.write(Text(f"▎ you › {text}", style="bold"))
        # Refresh the bar from the same bounded request contract used by send.
        try:
            max_ctx = int(getattr(self.tui.config, "max_ctx", 8192))
        except (TypeError, ValueError):
            max_ctx = 8192
        try:
            _temperature, _top_p, max_tokens = self._parse_params()
            window = prepare_context(
                self.messages,
                self.tui.config.system,
                max_ctx,
                max_tokens,
            )
            self.update_ctx_bar(window.reserved_tokens)
        except ContextLimitError:
            self.update_ctx_bar(max_ctx)
        except ValueError:
            pass
        cold = self.tui.cold_tracker.consume_cold()
        self._cancel_requested = False
        self._turn_active = True
        event.input.disabled = True
        self._run_turn(list(self.messages), cold, submitted_message)

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

    def _rollback_message(self, message: dict[str, str]) -> None:
        """Remove a rejected submitted message by object identity on the UI thread."""
        for index, candidate in enumerate(self.messages):
            if candidate is message:
                del self.messages[index]
                return

    def update_ctx_bar(self, ctx_len: int) -> None:
        from mlx_tui.history.tokens import ctx_bar_style, ctx_bar_text  # noqa: PLC0415

        try:
            max_ctx = int(getattr(self.tui.config, "max_ctx", 8192))
        except Exception:
            max_ctx = 8192
        style = ctx_bar_style(ctx_len, max_ctx)
        try:
            bar = self.query_one("#ctx-progress", ProgressBar)
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
        except Exception:
            pass
        try:
            label = self.query_one("#ctx-bar", Static)
        except Exception:
            return
        label.update(ctx_bar_text(ctx_len, max_ctx))
        label.remove_class("ctx-bar-amber")
        label.remove_class("ctx-bar-red")
        if style == "yellow":
            label.add_class("ctx-bar-amber")
        elif style == "red":
            label.add_class("ctx-bar-red")

    def set_system_prompt(self, text: str) -> None:
        self.tui.config = replace(self.tui.config, system=text.strip() or None)
        self.apply_config_params(self.tui.config)

    @on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")
    def _on_param_submitted(self, event: Input.Submitted) -> None:
        return params.on_param_submitted(self, event)  # type: ignore[arg-type]

    @work(exclusive=True, group="chat", thread=True)
    def _run_turn(
        self,
        messages: list[dict[str, str]],
        cold: bool,
        submitted_message: dict[str, str],
    ) -> None:
        return turn.run_turn_impl(
            self,  # type: ignore[arg-type]
            messages,
            cold,
            submitted_message,
        )

    def abort(self) -> None:
        return turn.abort(self)  # type: ignore[arg-type]

    def _update_stream(self, text: str) -> None:
        return turn.update_stream(self, text)  # type: ignore[arg-type]

    def _complete_turn_ui(
        self, full_text: str, stamp: str, cold: bool, notices: list[str]
    ) -> None:
        return turn.complete_turn_ui(self, full_text, stamp, cold, notices)  # type: ignore[arg-type]

    def _record_cancelled(
        self,
        model_at_send: str,
        cold: bool,
        ctx_len_estimate: int,
        reserved_ctx_len: int,
    ) -> None:
        return turn.record_cancelled(
            self,  # type: ignore[arg-type]
            model_at_send,
            cold,
            ctx_len_estimate,
            reserved_ctx_len,
        )

    def _write_system_line(self, message: str, style: str) -> None:
        return turn.write_system_line(self, message, style)  # type: ignore[arg-type]

    def end_turn(self) -> None:
        return turn.end_turn(self)  # type: ignore[arg-type]
