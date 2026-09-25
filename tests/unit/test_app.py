from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.css.query import NoMatches

from mlx_tui.app import MlxTuiApp


async def test_shutdown_continues_after_chat_cleanup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = MlxTuiApp()
    events: list[str] = []

    class FailingChat:
        def abort(self) -> None:
            raise RuntimeError("abort")

        async def wait_for_cleanup(self) -> None:
            raise RuntimeError("cleanup")

        async def flush_for_shutdown(self) -> None:
            raise RuntimeError("flush")

    class Http:
        async def aclose(self) -> None:
            events.append("http")

    def no_match(*_args: object, **_kwargs: object) -> object:
        raise NoMatches()

    def chat() -> FailingChat:
        return FailingChat()

    def log_app(*_args: object, **_kwargs: object) -> None:
        return

    monkeypatch.setattr(app, "query_one", no_match)
    monkeypatch.setattr(app, "_chat_pane_or_none", chat)
    monkeypatch.setattr(app, "log_app", log_app)
    app.managed_runtime = SimpleNamespace(close=lambda: events.append("managed"))  # type: ignore[assignment]
    app._http = Http()  # type: ignore[assignment]

    await app.on_unmount()

    assert events == ["managed", "http"]
