from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.css.query import NoMatches
from textual.widgets import Collapsible, Input, Static

from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.operations import OperationKind
from mlx_tui.status import ServerProbe
from tests.conftest import AppHarness

_STAMP_RE = re.compile(
    r"(?:▎ )?(\d+~?|—) in · (\d+~?|—) out · "
    r"first (\d+\.\d+s|—) · answer (\d+\.\d+s|—) · "
    r"total \d+\.\d+s · (?:\d+\.\d+ client request tok/s|— client request tok/s).*"
)
_REPLY = "Hello world this is MLX."


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


async def test_status_green_and_chat_stamp_over_stub_http(harness: AppHarness) -> None:
    await harness.app._poll()
    assert harness.app.status_state == "green"
    harness.app.select_model(harness.server.model_id)
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert inp.text == ""

    def stamp_visible(a: MlxTuiApp) -> bool:
        return any("tok/s" in t for t in harness.log_lines())

    assert await harness.wait_for(stamp_visible), (
        f"stamp never appeared; log={harness.log_lines()}"
    )
    texts = harness.log_lines()
    assert _REPLY in texts
    joined = " ".join(texts)
    assert "client request tok/s" in joined
    assert "12 in" in joined and "6 out" in joined
    assert "~" not in joined
    assert "cold" not in joined
    assert "first" in joined and "answer" in joined and "total" in joined
    assert harness.chat_pane()._active_turn is None
    assert "Waiting for output…" not in "\n".join(harness.log_lines())


async def test_non_mlx_http_shows_amber(harness: AppHarness) -> None:
    harness.server.mode = "html"
    await harness.app._poll()
    assert harness.app.status_state == "amber"


async def test_health_and_catalogue_are_independent(harness: AppHarness) -> None:
    harness.server.mode = "catalog_html"
    await harness.app._poll()
    assert harness.app.status_state == "green"
    probe = await harness.app._fetch_probe()
    assert probe.catalogue_state == "amber"
    assert probe.available_models == ()

    harness.server.mode = "health_down"
    probe = await harness.app._fetch_probe()
    assert probe.state == "amber"
    assert probe.catalogue_state == "green"


async def test_stale_poll_cannot_overwrite_request_evidence(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_probe(self: MlxTuiApp) -> ServerProbe:
        entered.set()
        await release.wait()
        return ServerProbe("green", None, ("catalogue-only",), "green")

    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", delayed_probe)
    task = asyncio.create_task(harness.app._poll())
    await entered.wait()
    harness.app.select_model("explicit/model")
    harness.app.record_generation_success("explicit/model", "explicit/model")
    release.set()
    await task

    assert harness.app.effective_model() == "explicit/model"
    assert harness.app.server_identity.last_response_model == "explicit/model"
    assert harness.app.server_identity.generation_state == "succeeded"


async def test_memory_bar_renders(harness: AppHarness) -> None:
    from textual.widgets import ProgressBar  # noqa: PLC0415

    await harness.pilot.pause()
    await harness.app._poll()
    await harness.pilot.pause()
    bar = harness.app.query_one("#memory-bar", ProgressBar)
    label = harness.app.query_one("#memory-label", Static)
    assert bar is not None
    assert bar.total is not None and bar.total > 0
    # progress should be 0 or rss_gib when stub green (rss None → 0)
    assert bar.progress is not None and bar.progress >= 0
    text = str(label.render())
    assert "avail" in text.lower()
    assert "RSS" in text
    # total should be around snapshot total (or default 16 when unknown)
    # we don't assert exact since psutil virtual_memory varies per CI host
    assert bar.total is not None and bar.total > 0


async def test_polling_keeps_explicit_selection_not_catalogue_or_cmdline(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    import psutil  # noqa: PLC0415

    import mlx_tui.process as proc_mod  # noqa: PLC0415

    harness.server.model_id = "mlx-community/server-a"
    harness.app.select_model("explicit/request-target")
    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    proc_mod._net_denied = False  # type: ignore[attr-defined]

    fake_pid = 4242
    other_cmdline = ["python", "-m", "mlx_lm.server", "--model", "other/model"]

    class _FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def cmdline(self) -> list[str]:
            return other_cmdline

        def create_time(self) -> float:
            return 111.0

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=1 * 2**30)

    def _fake_conns(kind: str = "tcp") -> list[SimpleNamespace]:
        assert kind == "tcp"
        return [
            SimpleNamespace(
                pid=fake_pid, status="LISTEN", laddr=("127.0.0.1", harness.port)
            )
        ]

    monkeypatch.setattr(psutil, "Process", _FakeProc)
    monkeypatch.setattr(psutil, "net_connections", _fake_conns)

    await harness.app._poll()
    assert harness.app.status_state == "green"
    assert harness.app.server_identity.last_response_model is None
    assert harness.app.effective_model() == "explicit/request-target"
    assert harness.app.server_identity.pid == fake_pid

    # Selection follows the explicit request target, not catalogue or cmdline.
    from mlx_tui.table import selected_cell  # noqa: PLC0415

    assert (
        selected_cell("explicit/request-target", harness.app.effective_model()) == "●"
    )
    assert selected_cell("mlx-community/server-a", harness.app.effective_model()) == ""

    # Chat payload follows explicit selection.
    harness.server.requests.clear()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def reply_done(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(reply_done)
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts
    assert posts[-1].get("model") == "explicit/request-target"


async def test_external_catalogue_change_does_not_replace_selection(
    harness: AppHarness,
) -> None:
    import mlx_tui.process as proc_mod  # noqa: PLC0415

    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    proc_mod._net_denied = False  # type: ignore[attr-defined]
    harness.server.model_id = "mlx-community/v1"
    harness.app.select_model("explicit/request-target")
    await harness.app._poll()
    assert harness.app.effective_model() == "explicit/request-target"
    first = harness.app.server_identity
    harness.server.model_id = "mlx-community/v2"
    await harness.app._poll()
    assert harness.app.effective_model() == "explicit/request-target"
    assert harness.app.server_identity.last_response_model is None
    assert harness.app.server_identity.selected_model == first.selected_model
    assert harness.app.server_identity.generation_state == first.generation_state
    assert harness.app.server_identity.available_models == ("mlx-community/v2",)


def _poll_failed_lines(harness: AppHarness) -> list[str]:
    return [t for t in harness.app_log_lines() if "poll" in t and "failed" in t]


async def test_log_error_once_dedups_until_cleared(harness: AppHarness) -> None:
    app = harness.app
    app.log_error_once("poll", RuntimeError("boom"))
    app.log_error_once("poll", RuntimeError("boom"))
    assert len(_poll_failed_lines(harness)) == 1
    app.log_error_once("poll", ValueError("other"))
    assert len(_poll_failed_lines(harness)) == 2
    app.clear_error("poll")
    app.log_error_once("poll", RuntimeError("boom"))
    assert len(_poll_failed_lines(harness)) == 3
    assert "RuntimeError: boom" in _poll_failed_lines(harness)[-1]


async def test_poll_connection_failure_stays_red_with_bounded_diagnostics(
    harness: AppHarness,
) -> None:
    harness.app.select_model("explicit/request-target")
    harness.server.shutdown()
    harness.server.server_close()
    for _ in range(3):
        await harness.app._poll()
    assert harness.app.status_state == "red"
    assert harness.app.effective_model() == "explicit/request-target"
    assert harness.app.server_identity.last_response_model is None
    assert harness.app._poll_in_flight is False
    lines = _poll_failed_lines(harness)
    assert len(lines) == 2, harness.app_log_lines()
    assert all("ConnectError" in line for line in lines)
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", ChatInput).disabled


async def test_poll_unexpected_fault_keeps_state_and_logs_once(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await harness.app._poll()
    assert harness.app.status_state == "green"
    harness.app.select_model("explicit/request-target")
    assert harness.app.server_identity.last_response_model is None
    orig_probe = MlxTuiApp._fetch_probe

    async def boom_probe(self: MlxTuiApp):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    async def other_probe(self: MlxTuiApp):  # type: ignore[no-untyped-def]
        raise ValueError("other")

    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", boom_probe)
    await harness.app._poll()
    assert harness.app.status_state == "green"
    assert harness.app.effective_model() == "explicit/request-target"
    assert harness.app._poll_in_flight is False
    assert len(_poll_failed_lines(harness)) == 1
    assert "RuntimeError: boom" in _poll_failed_lines(harness)[0]

    # Identical repeats stay silent; a changed error logs immediately.
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 1
    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", other_probe)
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 2
    assert harness.app.status_state == "green"

    # Recovery clears the source so a recurrence becomes visible again.
    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", orig_probe)
    await harness.app._poll()
    assert harness.app.status_state == "green"
    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", boom_probe)
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 3

    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", ChatInput).disabled


async def test_poll_malformed_json_is_amber_with_bounded_diagnostics(
    harness: AppHarness,
) -> None:
    harness.server.mode = "html"
    await harness.app._poll()
    assert harness.app.status_state == "amber"
    assert harness.app.effective_model() == harness.server.model_id
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 2, harness.app_log_lines()

    harness.server.mode = "ok"
    await harness.app._poll()
    assert harness.app.status_state == "green"
    harness.server.mode = "html"
    await harness.app._poll()
    assert harness.app.status_state == "amber"
    assert len(_poll_failed_lines(harness)) == 4


@pytest.mark.parametrize("initial", [(80, 24), (120, 40)])
async def test_chat_layout_bounds_and_resize(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, initial: tuple[int, int]
) -> None:
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415
    from mlx_tui.params import ParamsPane  # noqa: PLC0415

    def no_rescan(self: ModelsPane) -> None:
        pass

    def no_process(*args: object) -> None:
        return None

    monkeypatch.setattr(ModelsPane, "rescan", no_rescan)
    monkeypatch.setattr("mlx_tui.process.find_server_process", no_process)
    harness.app.query_one(TabbedContent).active = "chat"
    params = harness.app.query_one(ParamsPane)
    other = (120, 40) if initial == (80, 24) else (80, 24)
    for size in (initial, other, initial):
        await harness.pilot.resize_terminal(*size)
        for params_closed, activity_closed in (
            (True, True),
            (False, True),
            (True, False),
            (False, False),
        ):
            params.collapsed = params_closed
            activity = harness.app.query_one("#activity", Collapsible)
            activity.collapsed = activity_closed
            await harness.pilot.pause()
            selectors = [
                "#chat-transcript",
                "#chat-context",
                "#chat-input",
                "#activity",
                "Footer",
            ]
            regions = [harness.app.query_one(selector).region for selector in selectors]
            assert regions[0].height >= (6 if params_closed and activity_closed else 1)
            for region in regions:
                assert region.y >= 0 and region.bottom <= size[1], regions
                assert region.x >= 0 and region.right <= size[0], regions
            for before, after in zip(regions, regions[1:]):
                assert before.bottom <= after.y, regions
            meter = harness.app.query_one("#ctx-progress").region
            count = harness.app.query_one("#ctx-bar").region
            assert meter.y == count.y
            assert meter.right <= count.x
            if not params_closed:
                fields = list(params.query(Input))
                assert len({field.region.y for field in fields}) == 1
                for field in fields:
                    assert params.region.contains_region(field.region)
            if not activity_closed:
                log_region = harness.app.query_one("#app-log").region
                assert regions[-2].contains_region(log_region)
            model_id = "organization/" + "very-long-model-name" * 20
            harness.app.select_model(model_id)
            harness.app._render_status(rss_gib=2.0)
            await harness.pilot.pause()
            model = harness.app.query_one("#status-model")
            port = harness.app.query_one("#status-port")
            assert model_id in str(model.tooltip)
            assert model.region.right <= port.region.x
            assert port.region.right <= size[0]


async def test_activity_retains_literal_events_and_focus(harness: AppHarness) -> None:
    from textual.widgets import RichLog, TabbedContent  # noqa: PLC0415

    activity = harness.app.query_one("#activity", Collapsible)
    tabs = harness.app.query_one(TabbedContent)
    tabs.active = "chat"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.focus()
    message = "[red]literal[/] " + "long message " * 20
    harness.app.log_app(message, "yellow")
    harness.app.log_app("ordinary update")
    await harness.pilot.pause()
    assert activity.collapsed
    assert "Last warning: [red]literal[/]" in activity.title
    assert "ordinary update" not in activity.title
    assert activity.size.height == 1
    assert len(activity.title) <= 76
    assert "[red]literal[/]" in " ".join(harness.app_log_lines())
    assert "ordinary update" in harness.app_log_lines()
    await harness.pilot.press("f2")
    assert not activity.collapsed
    assert harness.app.focused is inp
    assert "Last warning" not in activity.title
    assert "ordinary update" in activity.title
    rendered = "".join("".join(harness.app_log_lines()).split())
    assert "".join(message.split()) in rendered
    log = activity.query_one(RichLog)
    log.focus()
    await harness.pilot.press("f2")
    assert activity.collapsed
    assert harness.app.focused is activity.query_one("CollapsibleTitle")
    for tab in ("models", "compare", "chat", "metrics"):
        tabs.active = tab
        await harness.pilot.pause()
        assert harness.app.screen.can_view_entire(activity)


async def test_compare_tab_ownership_and_candidate_isolation(
    harness: AppHarness,
) -> None:
    from textual.css.query import NoMatches  # noqa: PLC0415
    from textual.widgets import Select, TabbedContent, TabPane  # noqa: PLC0415

    from mlx_tui.chat_ui.pane import ChatPane  # noqa: PLC0415
    from mlx_tui.compare.pane import ComparePane  # noqa: PLC0415
    from mlx_tui.metrics_pane import MetricsPane  # noqa: PLC0415
    from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415
    from mlx_tui.params import ParamsPane  # noqa: PLC0415

    panes = list(harness.app.query(TabPane))
    assert [pane.id for pane in panes] == ["models", "compare", "chat", "metrics"]

    compare = harness.app.query_one("#compare-pane", ComparePane)
    assert compare is not None
    for selector in (
        "#comparison-profile-a",
        "#comparison-profile-b",
        "#comparison-preflight",
        "#comparison-run",
        "#comparison-keep-a",
    ):
        assert compare.query_one(selector) is not None
    for pane_type in (ModelsPane, MetricsPane, ChatPane):
        pane = harness.app.query_one(pane_type)
        for selector in (
            "#comparison-profile-a",
            "#comparison-preflight",
            "#comparison-run",
        ):
            try:
                pane.query_one(selector)
            except NoMatches:
                pass
            else:
                raise AssertionError(f"{pane_type.__name__} owns {selector}")
    # The Models selector is gone; Metrics keeps only its history widgets.
    assert harness.app.query(TabPane).first() is not None
    with pytest.raises(NoMatches):
        harness.app.query_one("#coding-profile-selector", Select)
    harness.app.query_one("#metrics-table")

    # Candidate changes must not touch the ordinary request target or chat state.
    assert len(harness.app.profile_entries) >= 2
    harness.app.select_model("explicit/request-target")
    before_target = harness.app.effective_model()
    before_config = (
        harness.app.config.model,
        harness.app.config.temperature,
        harness.app.config.top_p,
        harness.app.config.max_tokens,
    )
    before_params = harness.app.query_one(ParamsPane).read_values()
    harness.chat_pane().messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "answer"},
    ]
    before_messages = list(harness.chat_pane().messages)
    draft = harness.app.query_one("#chat-input", ChatInput)
    draft.text = "draft stays"
    before_history = len(harness.app.history.all_records())

    ids = harness.app.comparison_profile_ids
    harness.app.update_comparison_candidate(0, ids[1])
    await harness.pilot.pause()

    assert harness.app.comparison_profile_ids == (ids[1], ids[0])
    assert harness.app.effective_model() == before_target
    assert (
        harness.app.config.model,
        harness.app.config.temperature,
        harness.app.config.top_p,
        harness.app.config.max_tokens,
    ) == before_config
    assert harness.app.query_one(ParamsPane).read_values() == before_params
    assert harness.app.query_one("#chat-input", ChatInput).text == "draft stays"
    assert harness.chat_pane().messages == before_messages
    assert len(harness.app.history.all_records()) == before_history
    assert compare.query_one("#comparison-profile-a", Select).value == ids[1]
    assert compare.query_one("#comparison-profile-b", Select).value == ids[0]

    tabs = harness.app.query_one(TabbedContent)
    for tab in ("models", "compare", "chat", "metrics"):
        tabs.active = tab
        await harness.pilot.pause()


async def test_native_footer_keys_and_single_send(harness: AppHarness) -> None:
    from textual.widgets import TabbedContent  # noqa: PLC0415

    table = harness.app.query_one("#models-table")
    table.focus()
    await harness.pilot.pause()
    labels = {
        binding.binding.description
        for binding in harness.app.screen.active_bindings.values()
        if binding.binding.show
    }
    assert {"Load", "Delete", "Search", "Activity", "Config", "Quit"} <= labels
    assert "Cancel" not in labels
    harness.app.query_one(TabbedContent).active = "chat"
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.focus()
    await harness.pilot.pause()
    labels = {
        binding.binding.description
        for binding in harness.app.screen.active_bindings.values()
        if binding.binding.show
    }
    assert "Send" in labels
    assert not {"Load", "Delete", "Search"} & labels
    harness.app.select_model(harness.server.model_id)
    await harness.pilot.press(*"d/[literal]", "ctrl+enter")
    assert await harness.wait_for(lambda app: len(harness.chat_pane().messages) == 2)
    assert len(harness.server.requests) == 1
    assert harness.chat_pane().messages[0]["content"] == "d/[literal]"


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_compare_keyboard_journey_preserves_focus_and_draft(  # noqa: PLR0915
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    import os  # noqa: PLC0415
    from collections.abc import Callable  # noqa: PLC0415
    from dataclasses import replace  # noqa: PLC0415

    from textual.widgets import (  # noqa: PLC0415
        Button,
        Collapsible,
        DataTable,
        Select,
        TabbedContent,
    )

    from mlx_tui import comparison  # noqa: PLC0415
    from mlx_tui.compare.pane import ComparePane  # noqa: PLC0415
    from mlx_tui.comparison_summary import _summary  # noqa: PLC0415
    from mlx_tui.process import ProcessIdentity  # noqa: PLC0415

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    snapshots = {
        entry.profile.repo_id: tmp_path / entry.profile.id
        for entry in harness.app.profile_entries
    }
    for snapshot in snapshots.values():
        snapshot.mkdir(exist_ok=True)

    def resolve(repo_id: str, _revision: str):
        return snapshots[repo_id]

    def verify(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {}

    identity = ProcessIdentity(os.getpid(), 1.0)

    def same_identity(_host: str, _port: int) -> ProcessIdentity:
        return identity

    monkeypatch.setattr("mlx_tui.compare.workflow.resolve_cached_snapshot", resolve)
    monkeypatch.setattr("mlx_tui.app.state.resolve_cached_snapshot", resolve)
    monkeypatch.setattr("mlx_tui.compare.workflow.verify_profile_snapshot", verify)
    monkeypatch.setattr("mlx_tui.app.state.verify_profile_snapshot", verify)
    monkeypatch.setattr(
        "mlx_tui.compare.workflow.process.find_server_process", same_identity
    )

    async def complete(
        value: comparison.ComparisonInput,
        *,
        on_progress: Callable[[comparison.ComparisonResult], None],
    ) -> comparison.ComparisonResult:
        trials = tuple(
            comparison.TrialResult(
                entry.profile.id,
                index,
                state="completed",
                quality_pass=True,
                total_s=1.0 if idx == 0 else 2.0,
                first_output_s=0.1,
                answer_started_s=0.2,
                cached_prompt_tokens=10,
                process_identity_before=identity,
                process_identity_after=identity,
                payload={
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "max_tokens": 32,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "seed": 7,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            for idx, entry in enumerate(value.profiles)
            for index in range(6)
        )
        provisional = comparison.ComparisonResult(
            run_id="kbd-run",
            status="completed",
            comparison=value,
            trials=trials,
        )
        result = replace(provisional, summary=_summary(provisional))
        comparison.save_comparison(result)
        on_progress(result)
        return result

    monkeypatch.setattr("mlx_tui.compare.workflow.run_comparison", complete)

    await harness.pilot.resize_terminal(*size)
    await harness.pilot.pause()

    harness.chat_pane().messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "answer"},
    ]
    before_messages = list(harness.chat_pane().messages)
    draft = harness.app.query_one("#chat-input", ChatInput)
    draft.text = "draft stays"
    requests_before = len(harness.server.requests)

    tabs = harness.app.query_one(TabbedContent)
    tabs.active = "compare"
    await harness.pilot.pause()
    pane = harness.app.query_one(ComparePane)

    # Setup selectors are keyboard-focusable.
    pane.query_one("#comparison-profile-a", Select).focus()
    await harness.pilot.pause()
    assert harness.app.focused is pane.query_one("#comparison-profile-a", Select)
    await harness.pilot.press("tab")
    await harness.pilot.pause()

    # Disclosures open via keyboard.
    for collapsible_id in ("#compare-task", "#compare-open"):
        collapsible = pane.query_one(collapsible_id, Collapsible)
        collapsible.query_one("CollapsibleTitle").focus()
        await harness.pilot.pause()
        was_collapsed = collapsible.collapsed
        await harness.pilot.press("enter")
        await harness.pilot.pause()
        assert collapsible.collapsed is not was_collapsed
        await harness.pilot.press("enter")
        await harness.pilot.pause()

    # Check readiness and Run via keyboard activation.
    pane.query_one("#comparison-preflight", Button).focus()
    await harness.pilot.pause()
    await harness.pilot.press("enter")
    await harness.pilot.pause()
    assert pane._preflight_input is not None

    focused_before_run = harness.app.focused
    pane.query_one("#comparison-run", Button).focus()
    await harness.pilot.pause()
    await harness.pilot.press("enter")
    assert await harness.wait_for(
        lambda app: (
            app.operations.current is OperationKind.IDLE
            and app.last_comparison is not None
            and app.last_comparison.status == "completed"
        )
    )
    # Completion must not steal focus to another pane.
    assert harness.app.focused is not None
    assert pane.is_mounted

    # Trial table is keyboard-selectable and shows details.
    trials = pane.query_one("#comparison-trials", DataTable)
    trials.focus()
    await harness.pilot.pause()
    trials.move_cursor(row=3)
    await harness.pilot.pause()
    await harness.pilot.press("enter")
    await harness.pilot.pause()
    detail = pane.query_one("#comparison-trial-detail", Static)
    assert "cached prompt tokens" in str(detail.render()).lower()

    # Keep via keyboard saves and applies the frozen profile.
    pane.query_one("#comparison-keep-a", Button).focus()
    await harness.pilot.pause()
    await harness.pilot.press("enter")
    await harness.pilot.pause()
    assert harness.app.saved_choice is not None
    assert harness.app.active_profile_id is not None

    # Resize preserves focus and the unchanged chat draft.
    other = (120, 40) if size == (80, 24) else (80, 24)
    await harness.pilot.resize_terminal(*other)
    await harness.pilot.pause()
    await harness.pilot.resize_terminal(*size)
    await harness.pilot.pause()
    assert harness.app.query_one("#chat-input", ChatInput).text == "draft stays"
    assert harness.chat_pane().messages == before_messages

    # Go to Chat only navigates and focuses; it never sends the draft.
    pane.query_one("#comparison-go-chat", Button).focus()
    await harness.pilot.pause()
    await harness.pilot.press("enter")
    await harness.pilot.pause()
    assert tabs.active == "chat"
    assert harness.app.focused is harness.app.query_one("#chat-input", ChatInput)
    assert harness.app.query_one("#chat-input", ChatInput).text == "draft stays"
    assert harness.chat_pane().messages == before_messages
    assert len(harness.server.requests) == requests_before
    assert focused_before_run is not None


async def test_endpoint_f3_from_tabs_and_escape_preserves_draft(
    harness: AppHarness,
) -> None:
    from textual.widgets import TabbedContent, TextArea  # noqa: PLC0415

    from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

    tabs = harness.app.query_one(TabbedContent)
    draft = harness.app.query_one("#chat-input", ChatInput)
    draft.text = "draft stays"
    for tab in ("models", "compare", "chat", "metrics"):
        tabs.active = tab
        await harness.pilot.pause()
        await harness.pilot.press("f3")
        await harness.pilot.pause()
        preview = harness.app.screen
        assert isinstance(preview, TextPreviewScreen)
        title = str(preview.query_one("#text-preview-title", Static).render())
        assert "Endpoint" in title and "unqualified" in title
        assert "/v1" in preview.query_one("#text-preview-area", TextArea).text
        await harness.pilot.press("escape")
        await harness.pilot.pause()
        assert not isinstance(harness.app.screen, TextPreviewScreen)
        assert harness.app.query_one("#chat-input", ChatInput).text == "draft stays"


async def test_endpoint_preview_absent_present_freshness_mismatch(
    harness: AppHarness,
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    await harness.app._poll()
    assert harness.app.status_state == "green"
    # Health-only success reads Reachable via the status bar mapping.
    harness.app._render_status(rss_gib=None)
    await harness.pilot.pause()
    dot = str(harness.app.query_one("#status-dot", Static).render())
    assert "Reachable" in dot
    assert "Ready" not in dot

    harness.app.select_model("explicit/request-target")
    text = harness.app.endpoint_preview_text()
    assert "selected request model: explicit/request-target" in text
    assert "last response model: —" in text
    assert "last verified success: — (never)" in text
    assert "reachability: Reachable" in text
    assert "residency: unknown" in text
    assert "curl:" in text

    harness.app.record_generation_success(
        "explicit/request-target", "explicit/request-target"
    )
    first_success = harness.app.server_identity.last_success_at
    assert first_success is not None
    text = harness.app.endpoint_preview_text()
    assert "last response model: explicit/request-target" in text
    assert "— (never)" not in text
    assert "mismatched response" not in text

    # Mismatch after an older success must not present the old timestamp as new.
    harness.app.select_model("explicit/other-target")
    text = harness.app.endpoint_preview_text()
    assert "selected request model: explicit/other-target" in text
    assert "last response model: explicit/request-target" in text
    assert "mismatched response is not the verified success" in text
    assert "older success, not this response time" in text

    # A later successful response refreshes the timestamp and clears the note.
    harness.app.record_generation_success(
        "explicit/other-target", "explicit/other-target"
    )
    second_success = harness.app.server_identity.last_success_at
    assert second_success is not None and second_success >= first_success
    text = harness.app.endpoint_preview_text()
    assert "last response model: explicit/other-target" in text
    assert "mismatched response" not in text

    # Absent target omits runnable recipes.
    harness.app.server_identity = replace(
        harness.app.server_identity, selected_model=None
    )
    text = harness.app.endpoint_preview_text()
    assert "select a model first" in text
    assert "curl:" not in text
    assert "opencode config:" not in text


async def test_endpoint_preview_non_loopback_omits_recipes(
    harness: AppHarness,
) -> None:
    harness.app.host = "192.0.2.1"
    harness.app.select_model("explicit/request-target")
    text = harness.app.endpoint_preview_text()
    assert "http://192.0.2.1:" in text
    assert "unsupported scope" in text
    assert "curl:" not in text
    assert "opencode config:" not in text
    # Attach connection support itself is unchanged: polling still runs.
    await harness.app._poll()
    assert harness.app.effective_model() == "explicit/request-target"


async def test_endpoint_ipv6_app_bracketed_urls_end_to_end() -> None:
    from mlx_tui.comparison_contracts import parse_loopback_url  # noqa: PLC0415

    app = MlxTuiApp(
        host="::1", port=18080, config=AppConfig(model="explicit/ipv6-target")
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        base = str(app._http.base_url)
        assert "http://[::1]:18080" in base
        text = app.endpoint_preview_text()
        assert "http://[::1]:18080/v1" in text
        assert "explicit/ipv6-target" in text
        chat_line = next(
            line for line in text.splitlines() if line.startswith("chat URL:")
        )
        chat_url = chat_line.split("chat URL:")[1].strip()
        assert parse_loopback_url(chat_url).url == chat_url
        # Preview action mounts the same bracketed snapshot.
        app.action_endpoint_info()
        await pilot.pause()
        from textual.widgets import TextArea  # noqa: PLC0415

        from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

        assert isinstance(app.screen, TextPreviewScreen)
        assert "[::1]" in app.screen.query_one("#text-preview-area", TextArea).text


async def test_endpoint_preview_hostile_model_stays_literal(
    harness: AppHarness,
) -> None:
    hostile = 'evil"; rm -rf /; echo "hi'
    harness.app.select_model(hostile)
    text = harness.app.endpoint_preview_text()
    assert hostile in text
    # JSON-serialized payload keeps the hostile value literal; shell-joined
    # curl keeps it as one quoted -d argument, never executable text.
    assert '"model": "evil\\"; rm -rf /; echo \\"hi"' in text or hostile in text
    assert "curl:" in text
    assert "opencode config:" in text


async def test_endpoint_unhealthy_and_catalogue_no_residency(
    harness: AppHarness,
) -> None:
    harness.app.select_model("explicit/request-target")
    await harness.app._poll()
    assert "Reachable" in harness.app.endpoint_preview_text()

    harness.server.mode = "health_down"
    await harness.app._poll()
    text = harness.app.endpoint_preview_text()
    assert "residency: unknown" in text
    assert "Ready" not in text
    assert " Residential" not in text
    assert "selected request model: explicit/request-target" in text

    harness.server.mode = "ok"
    harness.server.model_id = "mlx-community/changed"
    await harness.app._poll()
    text = harness.app.endpoint_preview_text()
    assert "residency: unknown" in text
    assert "catalogue" in text
    assert "mlx-community/changed" in text
    assert harness.app.effective_model() == "explicit/request-target"


async def test_endpoint_long_model_80x24_usable(harness: AppHarness) -> None:
    from textual.widgets import TabbedContent, TextArea  # noqa: PLC0415

    from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

    long_model = "organization/" + "very-long-model-name" * 20
    harness.app.select_model(long_model)
    await harness.pilot.resize_terminal(80, 24)
    await harness.pilot.pause()
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    await harness.pilot.press("f3")
    await harness.pilot.pause()
    preview = harness.app.screen
    assert isinstance(preview, TextPreviewScreen)
    assert long_model in preview.query_one("#text-preview-area", TextArea).text
    await harness.pilot.press("escape")
    await harness.pilot.pause()
    assert harness.app.query_one("#chat-input", ChatInput).text == ""
    await harness.pilot.resize_terminal(120, 40)
    await harness.pilot.pause()
