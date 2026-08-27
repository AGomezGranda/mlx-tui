# MLX TUI — Metrics tab, Markdown chat, Params sidebar, System presets

**Date:** 2026-08-27
**Work Item:** n/a
**Status:** Complete
**Review:** docs/reviews/2026-08-27-metrics-markdown-params-presets-review-1.md

## Overview
Ship the four `idea.md` slices withheld from `v0` — a `Metrics` tab that replaces the broken footer strip and adds `RSS/available` history, Markdown rendering on chat completion (stream stays raw), a collapsible `temp/top-p/max-tokens` params sidebar wired into the chat payload, and a flat `presets.toml` with `ctrl+p`/`ctrl+o` cycling — reusing `HistoryStore`/`TabbedContent`/`AppConfig` patterns and keeping `history.py` stdlib-only.

## Current State
- **App shell** `src/mlx_tui/app.py:118-127` yields `Static(#status-bar)` dock top, `TabbedContent(initial="models")` with `TabPane("Models",id="models")`→`ModelsPane` + `TabPane("Chat",id="chat")`→`ChatPane`, then `Static(#history-strip) dock:bottom height:3 border-top` `app.py:75` + `RichLog(#app-log) height:6` `app.py:81`. `DEFAULT_CSS` in-class string `app.py:70-92`, status poll `set_interval(2.0,_poll)` `app.py:134` with `_poll_in_flight` guard `app.py:189`, `effective_model()` union `app.py:216` (tracked wins over `psutil` cmdline), `set_swap_ui(busy)` `app.py:271` disables `#models-table` + `#chat-input`.
- **History domain** `src/mlx_tui/history.py` stdlib-only 183 lines: `TurnRecord(frozen 9 fields ts,model,prompt_tok,out_tok,ttft_s,tok_s,ctx_len,cold,cancelled)` `history.py:19`, `HistoryStore` per-model `dict[str,deque(maxlen=64)]` `history.py:33`, `SPARKLINE_WIDTH=32 SPARKLINE_HEIGHT_ROWS=2` `history.py:14-15`, `sparkline_visible()` `history.py:86` filters `cold/cancelled`, `render_sparkline()->tuple[str,str]` pure braille `U+2800` `history.py:94`, `_tok_int()` `history.py:158`, `estimate_tokens()` `history.py:165`, `trim_for_context()` `history.py:170`. Owned by `MlxTuiApp.history` `app.py:113`, painted by `update_history_strip()` `app.py:138` via `query_one("#history-strip",Static)` + `_shade_for_ctx()` quartiles `app.py:43`. Recorded in `ChatPane._run_turn` `chat_pane.py:66` via `call_from_thread(history.add)` `chat_pane.py:110` + `call_from_thread(update_history_strip)` `chat_pane.py:111` (and cancelled `chat_pane.py:202-203`). Footer appears empty after swap/cold because `history.series(effective_model() or "—")` is per-model and `render_sparkline([])->("","no history yet")` `history.py:112`.
- **Chat** `src/mlx_tui/chat_pane.py:44-47` `ChatPane(Vertical)` owns `Static(#chat-stream)` + `RichLog(#chat-log,markup=False,wrap=True)` + `Input(#chat-input)`. `Input.Submitted` `chat_pane.py:49` appends user `Text("you ›")` `chat_pane.py:60`, `cold=cold_tracker.consume_cold()` `chat_pane.py:61`, disables input `chat_pane.py:63`, starts `@work(exclusive=True,group="chat",thread=True) _run_turn()` `chat_pane.py:66`. Payload built `chat_pane.py:71-78` (`messages:trim_for_context(8000)`, `max_tokens:1024` `chat_pane.py:22`, `stream_options:{include_usage:True}`, `model` if known), URL `http://{host}:{port}/v1/chat/completions` `chat_pane.py:79`, `stream_turn()` `src/mlx_tui/chat.py:55` sync `httpx.Client(timeout connect5/read300/write5/pool5)` `chat.py:73`, flush throttle `_FLUSH_INTERVAL_S=0.1` `chat.py:20` → `on_flush` → `tui.call_from_thread(_update_stream)` `chat_pane.py:86` → `Static.update()` `chat_pane.py:173`. Completion `chat_pane.py:175` `log.write(Text(stamp,style="dim"))` `chat_pane.py:180` then `log.write(full_text)` raw `chat_pane.py:183` — idea `idea.md:181` trap noted (`v0` deferred markdown). `Markdown` widget zero imports in `src/`; textual `8.2.8` provides `Markdown`+`Collapsible` verified `uv run python -c "from textual.widgets import Markdown, Collapsible"` `ok`.
- **Config** `src/mlx_tui/config.py:12` `AppConfig(frozen model,host,port,start_cmd,stop_cmd,pidfile)` at `config_path() -> $XDG_CONFIG_HOME/mlx-tui/config.toml else ~/.config` `config.py:24`, `CONFIG_TEMPLATE` `config.py:30`, `_KEY_TYPES` exact `type(value) is expected` `config.py:45`, `parse_config()` `config.py:77` raises `ConfigParseError`, `load_config()` degrades `config.py:94`, `App.__init__(host,port,config)` `app.py:96`, `main()` CLI `argparse --host/--port` `app.py:408` wins over config, `action_edit_config` `app.py:388` `suspend()+subprocess.run(shlex.split($EDITOR))` `app.py:394`. No params/presets keys exist.
- **Memory** `src/mlx_tui/process.py:66` `memory_snapshot()->MemorySnapshot(avail_gib,total_gib)` `status.py:8` via `psutil.virtual_memory()`; `src/mlx_tui/status.py:16` `classify_liveness()`, `status.py:31` `ColdTracker`, `status.py:62` `format_status_line()`. RSS via `psutil.Process(pid).memory_info().rss /2**30` `app.py:203`. Not historized.
- **Panes/tests** `src/mlx_tui/models_pane.py:24` `ModelsPane(Vertical)` owns `#swap-progress`+`ModelsTable`; `src/mlx_tui/table.py:25` `ModelsTable(DataTable)` `BINDINGS slash/enter/d`. `tui` property pattern `cast("MlxTuiApp",self.app)` `models_pane.py:32`/`chat_pane.py:35`, workers hop via `self.tui.call_from_thread` (widget lacks it on `8.2.8`). `tests/conftest.py:179` `AppHarness(app,pilot,server)` + `StubServer` modes `ok/probe/error500/truncated/length_cap/empty/slow` `conftest.py:24`, `wait_for()` poll `conftest.py:204`, `harness` fixture `conftest.py:215` mounts `MlxTuiApp` on ephemeral port. `pyproject.toml:7` `textual>=8.2.8 httpx>=0.28 psutil>=7.0 huggingface_hub>=1.28`, `tool.pyrefly preset=strict` `pyproject.toml:34`, `ruff select E4,E7,E9,F,I,PL,UP,TID,ASYNC,DTZ` `pyproject.toml:29`. Current gates `225 passed, ruff check clean, pyrefly 0 errors, textual 8.2.8`.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Metrics replaces footer strip (`#history-strip` removed) vs keep both | Keep strip + add tab duplicating it | Strip broken-perception is per-model isolation; duplicating wastes 3 lines. Idea originally rejected Metrics tab `idea.md:188-194` (History is footer strip, not standalone tab); now strip absorbed into tab reclaims `dock:bottom` space. User confirmed strip doesn't work, wants everything in tab. |
| `RSS/available` as separate `MemoryStore` ring in `history.py` (not `TurnRecord` fields) | Append `rss/avail` to `TurnRecord` | RSS sampled every 2s `app.py:197` independent of chat turns; mixing poll cadence into turn records would sparsely populate. Separate `MemoryRecord(ts,rss_gib,avail_gib,model)` + `MemoryStore(maxlen=256)` keeps `TurnRecord` frozen shape and future JSONL mechanical. `256` ≈ 8.5 min at 2s cadence, fits table. |
| Markdown swap on completion only (`history.py` pattern) | Per-chunk `Markdown` re-render | `idea.md:181` explicitly warns per-chunk `Markdown` is perf trap. Keep throttled `Static` stream, swap `rich.markdown.Markdown(full_text)` once in `_complete_turn_ui` (Rich renderable into `RichLog`, not `textual.widgets.Markdown` widget). One renderable per assistant turn, trivial repaint. |
| Params as `Collapsible` inside `ChatPane` `Vertical` | Global right-docked sidebar / ModalScreen | Idea `idea.md:184` says collapsible sidebar, not sliders. Chat-scoped `Collapsible` lives where payload `chat_pane.py:71` built, no layout pollution for Models/Metrics. Verified `Collapsible` exists in `textual 8.2.8`. |
| Params persisted to `config.toml` as `temperature/top_p/max_tokens/system` | In-memory only | Persists across restarts for free; `config.py:45` `_KEY_TYPES` extended with exact `type` checks, `load_config` degrades. No new file needed for 3 scalars + system prompt. |
| Presets sibling file `presets.toml` `~/.config/mlx-tui/presets.toml` with `[[preset]]` table, loader `presets.py` mirroring `config.py` | Store presets as TOML tables inside `config.toml` | Flat `presets.toml` per `idea.md:186`; keeps `config.py` focused on server control, allows future presets portability without config churn. `presets.py` owns `Preset(frozen)` + `load_presets()/parse_presets()/presets_path()/write_template`. `ctrl+p` / `ctrl+o` cycle (avoids terminal-unreliable `ctrl+shift+p`). `presets_path()` reuses `config_path().parent / "presets.toml"` to stay XDG-consistent. |
| `App` owns `presets:list[Preset]` + `preset_idx:int` + `chat_params` snapshot; `ChatPane` reads via `tui` | `ChatPane` owns preset state | App already owns cross-cutting `history` `app.py:113`, `cold_tracker` `app.py:109`, `swap_machine` `app.py:112`; preset cycles must affect both sidebar `Input`s and next-turn `system` prompt, so App is single source. Pane hops via `tui` cast. |
| Reuse `render_sparkline` + `sparkline_visible` + `_shade_for_ctx` for Metrics sparkline | New chart library/`plotext` dep | `history.py:94` pure braille stays stdlib-only; no new dep. Metrics tab's small sparkline above table is one call to existing pure function + `rich.text.Text` segments. |
| `MetricsPane` owns `DataTable` + `Static` sparkline + `Static` memory sparkline | Reuse `RichLog` for metrics | DataTable gives sortable columns and fits existing `table.py` pattern; `RichLog` scroll would hide metrics, strip must be fixed-height. |

## Implementation Phases

### Phase 1: Metrics tab + RSS/available history domain (footer strip absorbed)

Replaces broken-perception footer with a full tab. Chat `tok/s` history and polled `RSS/available` history both visible, no duplication.

**Changes:**
- `src/mlx_tui/history.py` — add `MemoryRecord` + `MemoryStore` beside `TurnRecord/HistoryStore` (still stdlib-only, no `textual` import):
  ```python
  @dataclass(frozen=True)
  class MemoryRecord:
      ts: float  # time.time()
      model: str | None  # effective_model() at poll time, None→"—"
      rss_gib: float | None  # None when no pid
      avail_gib: float  # always from memory_snapshot()
      total_gib: float


  class MemoryStore:
      def __init__(self, maxlen: int = 256) -> None:
          self._dq: deque[MemoryRecord] = deque(maxlen=maxlen)

      def add(self, r: MemoryRecord) -> None: ...
      def series(self) -> list[MemoryRecord]:
          return list(self._dq)  # copy oldest→newest

      def clear(self) -> None: ...
  ```
   Keep `_MAX_TURNS/SPARKLINE_WIDTH/SPARKLINE_HEIGHT_ROWS` untouched. Add docstring `Not thread-safe` mirroring `history.py:33`. Add pure helper `render_memory_sparkline(records:list[MemoryRecord], width:int=32, height_rows:int=2)->tuple[str,str]` mirroring `render_sparkline` but buckets `rss_gib` (filter `None` for `lo/hi` calculation; `None` produces a blank column so legend `· {n} samples` counts all records including no-pid ticks) equally with braille `2×4` packing; legend `f"{rss:.1f} GB RSS · avail {avail:.1f}/{total:.1f} · {n} samples"` via latest non-`None` rss, empty or all-`None`→`("", "no memory samples yet")`. No `rich` import.
- `src/mlx_tui/metrics_pane.py` — **new** `MetricsPane(Vertical)`:
   ```python
   class MetricsPane(Vertical):
       @property def tui(self)->MlxTuiApp: return cast("MlxTuiApp", self.app)
       def compose(self)->ComposeResult:
           yield Static("", id="metrics-sparkline")        # chat tok/s braille
           yield Static("", id="metrics-memory-sparkline") # RSS sparkline
           yield DataTable(id="metrics-table", cursor_type="row", zebra_stripes=True)
       def on_mount(self)->None: table=self.query_one("#metrics-table", DataTable); table.add_column("time", key="time"); table.add_column("model", key="model"); table.add_column("tok/s", key="toks"); table.add_column("TTFT", key="ttft"); table.add_column("ctx", key="ctx"); table.add_column("prompt", key="prompt"); table.add_column("out", key="out"); self.refresh_metrics()
       def refresh_metrics(self)->None: # UI-thread only, NoMatches guarded; named not `refresh` to avoid shadowing Widget.refresh
           # chat rows: flat last 64 of tui.history.all_records() sorted ts (all_records can be models*64, cap [-64:])
           # memory sparkline: tui.memory_store.series()
           # DataTable: for r in sorted(all_records)[-64:]: time.strftime("%H:%M:%S", time.localtime(r.ts)), r.model or "—", f"{r.tok_s:.1f}", f"{r.ttft_s:.2f}", str(r.ctx_len), str(r.prompt_tok), str(r.out_tok) + suffix " · cold"/"cancelled" dim/bold styling via Text
           # sparklines: reuses render_sparkline(visible_records) + render_memory_sparkline + _shade_for_ctx for chat (import _shade_for_ctx from app.py, do not duplicate)
       ```
   Imports: `time`, `cast`, `override`, `Vertical`, `Static`, `DataTable`, `NoMatches`, `Text`, `render_sparkline`, `render_memory_sparkline`, `sparkline_visible`, `from mlx_tui.app import _shade_for_ctx` (or keep helper in `history.py` pure; either way no duplication).
- `src/mlx_tui/app.py`:
   - `70-92` `DEFAULT_CSS` — remove `#history-strip { dock:bottom; height:3; ... }` block; add `#metrics-sparkline, #metrics-memory-sparkline { height: 3; border-bottom: solid $primary; padding:0 1; }` and `#metrics-table { height: 1fr; }` and `#params-collapsible { height: auto; }` + `#chat-log { height: 1fr; }` defensively (see Phase 3 Collapsible sizing)
   - `29-35` imports — drop `update_history_strip` import, keep `_shade_for_ctx` in `app.py` for reuse (or move to `history.py` pure); add `from mlx_tui.history import HistoryStore, MemoryStore, MemoryRecord, SPARKLINE_WIDTH, SPARKLINE_HEIGHT_ROWS, render_sparkline, render_memory_sparkline, sparkline_visible` and `from mlx_tui.metrics_pane import MetricsPane`
   - `96-116` `__init__` — add `self.memory_store = MemoryStore()` beside `self.history`; keep `self.history`
   - `118-127` `compose()` — after `ChatPane` add `with TabPane("Metrics", id="metrics"): yield MetricsPane(id="metrics-pane")`; **remove** `yield Static("", id="history-strip")` + drop import of `history-strip` id everywhere
   - Remove `138-179` `update_history_strip()` but **keep** `_shade_for_ctx()` in place (do not duplicate into `metrics_pane.py`; `MetricsPane` imports it from `app.py` to reuse the tested helper `tests/unit/test_history.py:401`)
   - `129-137` `on_mount()` — replace `self.update_history_strip()` with guarded `try: self.query_one(MetricsPane).refresh_metrics() except NoMatches: pass`
   - `189-214` `async def _poll()` — reuse single `snapshot = memory_snapshot()` for both `self.latest_avail_gib = snapshot.avail_gib` and `MemoryRecord` (`avail_gib=snapshot.avail_gib, total_gib=snapshot.total_gib`); `model`/`rss_gib` resolution unchanged; `rec = MemoryRecord(ts=time.time(), model=model, rss_gib=rss_gib, avail_gib=snapshot.avail_gib, total_gib=snapshot.total_gib); self.memory_store.add(rec)` then `try: self.query_one(MetricsPane).refresh_metrics() except NoMatches: pass` (guarded; no `call_from_thread` needed as poll is on event loop)
   - Add helper `def _refresh_metrics(self)->None: try: self.query_one(MetricsPane).refresh_metrics() except NoMatches: pass` for cross-thread callers (used by `models_pane.py`/`chat_pane.py` via `call_from_thread`)
   - `235-244` `set_tracked_model()` — after assignment, `try: self.query_one(MetricsPane).refresh_metrics() except NoMatches: pass` (model switch must refresh sparkline series)
   - Keep `181-184` `@on(TabbedContent.TabActivated)` add branch `elif event.tabbed_content.active == "metrics": self.query_one(MetricsPane).refresh_metrics()` (guarded)
   - Add `import time` top if not present
- `src/mlx_tui/models_pane.py:144-148` + `214-222` `run_warm_swap`/`run_boot` success branches — replace `self.tui.call_from_thread(self.tui.update_history_strip)` with `self.tui.call_from_thread(self.tui._refresh_metrics)` (helper added to `app.py` above, NoMatches-guarded; do not capture `MetricsPane` class in pane modules). Add `from mlx_tui.metrics_pane import MetricsPane` only if lambda path is kept, otherwise not needed.
- `src/mlx_tui/chat_pane.py:110-111` and `202-203` record paths — replace `call_from_thread(self.tui.update_history_strip)` with `call_from_thread(self.tui._refresh_metrics)` (same helper, UI-thread hop). `chat_pane.py:202-203` cancelled path identical.
- `tests/unit/test_history.py` — add `MemoryRecord` frozen test + `MemoryStore` add/series/clear/eviction at 256, `render_memory_sparkline empty/flat/width clipping` tests (6 cases mirroring existing render tests) + `test_memory_record_is_frozen` (mirrors `test_turn_record_is_frozen_and_has_nine_fields`), keep existing 17 passing
- `tests/integration/test_app_integration.py` — remove/rename `test_history_strip_renders_and_switches_on_swap` + `test_history_records_one_turn_and_shows_strip` + `test_history_cancelled_excluded_from_sparkline` + `test_history_error_not_recorded` + `test_history_cold_excluded_and_per_model_isolation` strip assertions (they query `#history-strip`); replace with `test_metrics_tab_renders_and_refreshes`: assert `harness.app.query_one("#metrics-table", DataTable)` exists and `"no memory samples"` / `"no history"` sparkline placeholders, `harness.app.query_one(MetricsPane).refresh_metrics()`, manual `history.add(TurnRecord(...))` + `memory_store.add(MemoryRecord(...))` then `harness.app.query_one(MetricsPane).refresh_metrics(); await pilot.pause()` assert DataTable row count 1 (`sorted(all_records)[-64:]` capped) and sparkline contains `tok/s` then `available` legend

**Success Criteria:**

#### Automated Verification:
- [x] New history memory tests pass: `uv run pytest -v tests/unit/test_history.py -k memory`
- [x] Metrics integration smoke passes: `uv run pytest -v tests/integration/test_app_integration.py -k metrics`
- [x] Whole suite green: `uv run pytest -q` (expected `≥231` passed, prior 225 + 6 new memory + 1 metrics smoke; history-strip tests removed/renamed)
- [x] Lint clean: `uv run ruff check .`
- [x] Format clean: `uv run ruff format --check .`
- [x] Types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui` shows third tab `[Metrics]`; switching Chat→Metrics shows two sparklines (tok/s + RSS) plus DataTable; a chat turn populates table row and both sparklines; a model swap switches chat sparkline series (old model's turns remain in table); poll for 10s without chat still grows memory sparkline + legend updates; no `No history yet` when table has rows

### Phase 2: Markdown chat (swap on completion)

Implements `idea.md:181` without the per-chunk trap. Stream stays throttled raw `Static`, completion swaps to `Markdown`.

**Changes:**
- `src/mlx_tui/chat_pane.py`:
   - Top imports add `from rich.markdown import Markdown` (Rich renderable, not `textual.widgets.Markdown` widget) — keeps `Input, RichLog, Static, Text` imports
   - `175-186` `_complete_turn_ui(self, full_text:str, stamp:str, cold:bool, notices:list[str])` — replace:
     ```python
     log.write(Text(stamp_text, style="dim"))
     if full_text:
         self.messages.append({"role": "assistant", "content": full_text})
         log.write(full_text)
         log.write("")
     ```
     with:
     ```python
     log.write(Text(stamp_text, style="dim"))
     if full_text:
         self.messages.append({"role": "assistant", "content": full_text})
         log.write(
             Markdown(full_text)
         )  # Rich renderable into RichLog, swap-in not per-chunk
         log.write("")
     ```
     Keep `for notice in notices: log.write(Text(notice,style="yellow"))` unchanged; system lines (`_write_system_line`, `you ›`) remain `Text` so `[` chars don't need escaping differently
   - No change to `_update_stream()` `chat_pane.py:172` or `chat.py:20-105` throttle; `RichLog(markup=False)` stays `False` because Rich `Markdown` is a renderable, not markup string
   - `_run_turn()` `chat_pane.py:66` unchanged
- `tests/unit/test_chat.py` — untouched (no UI)
- `tests/integration/test_app_integration.py` — add `test_chat_renders_markdown(harness)`: `await harness.pilot` activate Chat tab `TabbedContent.active="chat"`, focus `#chat-input`, set `inp.value="# hi\n\n**bold**"; await pilot.press("enter")`, `await harness.wait_for(lambda a: "hi" in " ".join(str(v) for v in a.query_one(ChatPane).messages[-1].values()))`, assert `RichLog` lines or `DeferredRender` queue contains `rich.markdown.Markdown` renderable (inspect `harness.app.query_one("#chat-log", RichLog)` internals or assert `"hi"` rendered heading not raw `"# hi"` in exported text), and stamp still `dim`; also assert stream `#chat-stream` cleared `== ""`
   - Do not use `query(Markdown)` widget count — Rich `Markdown` is a renderable inside `RichLog`, not a mounted `textual.widgets.Markdown` widget

**Success Criteria:**

#### Automated Verification:
- [x] Markdown chat integration passes: `uv run pytest -v tests/integration/test_app_integration.py -k markdown`
- [x] Whole suite green: `uv run pytest -q`
- [x] Lint clean: `uv run ruff check .`
- [x] Format clean: `uv run ruff format --check .`
- [x] Types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui` against stub or real server: send `# Title\n\n- bullet\n- bullet` → streams as raw growing `Static`, on completion renders as formatted heading/bullets in `RichLog` scrollback (via `rich.markdown.Markdown`); code block ` ```python\nprint(1)\n``` ` renders without highlight (deferred) but as monospaced block; `you ›` + stamp + notices still plain `Text`; long reply with markdown still streams at ~10Hz without flicker

### Phase 3: Params sidebar (collapsible temp / top-p / max-tokens)

Three typed `Input`s in a `Collapsible` inside `ChatPane`, values validated and merged into `payload` before `stream_turn()`, persisted to `config.toml`.

**Changes:**
- `src/mlx_tui/config.py`:
   - `12-21` `AppConfig` — add fields `temperature: float | None = None`, `top_p: float | None = None`, `max_tokens: int | None = None`, `system: str | None = None` (system is preset-supplied prompt for next turn, persists if user cycles; `system` is session-only via `ChatPane._system_prompt`, `AppConfig.system` is persistence mirror)
   - `30-36` `CONFIG_TEMPLATE` append `# temperature = 0.7\n# top_p = 1.0\n# max_tokens = 1024\n# system = "You are a helpful assistant."\n`
   - `45-52` `_KEY_TYPES` add `"temperature": float, "top_p": float, "max_tokens": int, "system": str`
   - `59-74` `_from_mapping` — extend to handle new keys with unified coercion: exact `type(value) is expected` for `str/int`, but for `temperature`/`top_p` accept `int` → `float(v)` (with `type(v) is int` guard rejecting `bool`) mirroring `presets.py` logic; `max_tokens` stays exact `int` (bool rejected)
   - No reload logic change
- `src/mlx_tui/chat_pane.py`:
   - Top imports add `from textual.widgets import Collapsible` (already verified), keep `Input, RichLog, Static`
   - Module constants after `_MAX_CONTEXT_TOKENS_EST` `chat_pane.py:23`: add `_DEFAULT_TEMPERATURE = 0.7`, `_DEFAULT_TOP_P = 1.0`, `_DEFAULT_MAX_TOKENS = 1024`
   - Instance state `__init__` `chat_pane.py:29` stays `messages`, add `self._system_prompt: str = ""` (current preset/system, not persisted via AppConfig.system except on preset apply)
   - `44-47` `compose()` — wrap param inputs inside chat layout (ChatPane itself is `Vertical`; `Collapsible` is first child):
     ```python
     with Collapsible(title="Params", collapsed=True, id="params-collapsible"):
         yield Input(placeholder="temp 0.7", id="param-temp", value="0.7")
         yield Input(placeholder="top-p 1.0", id="param-top-p", value="1.0")
         yield Input(placeholder="max-tokens 1024", id="param-max-tokens", value="1024")
     yield Static("", id="chat-stream")
     yield RichLog(id="chat-log", markup=False, wrap=True)
     yield Input(placeholder="message…", id="chat-input")
     ```
     Keep `has_live_turn` logic; no extra `Collapsible` JS. CSS for `#params-collapsible`/`#chat-log` handled in `app.py:70-92` above.
   - `49` narrow existing handler: change `@on(Input.Submitted)` to `@on(Input.Submitted, "#chat-input")` so param inputs do not start chat turns.
   - Helpers: add `_parse_params(self) -> tuple[float, float, int]` — reads three `Input.value.strip()`, parses `float` for temp/top_p clamped `temp 0.0..2.0` `top_p 0.0..1.0` with fallback to defaults on `ValueError`, `int` for `max_tokens` clamped `1..16384` fallback 1024 (via `NoMatches` guard if pane not mounted); also add `_apply_params_to_inputs(self, temperature:float|None, top_p:float|None, max_tokens:int|None)->None` to set `Input.value = str(v)` only for non-`None` (called by presets) and `apply_config_params(self, cfg: AppConfig)->None` to sync inputs from `AppConfig` if not `None`.
   - Add single param validator: `@on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")` handler that clamps and rewrites `event.input.value = str(clamped)` (no `Dim` line; silently clamp). Remove the three separate per-id handlers in favor of one selector.
   - `49-64` `_on_input_submitted` — guard unchanged (now scoped); `_run_turn` `66-78` payload merge:
     ```python
     temp, top_p, max_tok = self._parse_params()
     payload: dict[str, object] = {
         "messages": trimmed_with_system,
         "stream": True,
         "max_tokens": max_tok,
         "stream_options": {"include_usage": True},
         "temperature": temp,
         "top_p": top_p,
     }
     ```
     where `trimmed_with_system = ([{"role":"system","content": self._system_prompt}] if self._system_prompt else []) + trimmed` (system prefix not counted in `ctx_len_estimate` quirk acceptable)
   - Add method `set_system_prompt(self, text:str)->None: self._system_prompt = text.strip()` (called by preset apply)
   - Persist: `tui` property already there; on every `_run_turn` entry, if parsed params differ from `self.tui.config` values, `self.tui.config = AppConfig(model=self.tui.config.model, host=..., port=..., start_cmd=..., stop_cmd=..., pidfile=..., temperature=temp, top_p=top_p, max_tokens=max_tok, system=self._system_prompt or None)` — no file write, just session config; `action_edit_config` `app.py:388` already re-reads file so file remains source of truth if edited externally. Single source: pane `Input.value` drives `AppConfig` snapshot on turn start; preset apply drives pane inputs; `apply_config_params` drives pane inputs on config reload.
- `src/mlx_tui/app.py:388` `action_edit_config` — after `parse_config` reload, add `try: self.query_one(ChatPane).apply_config_params(self.config) except NoMatches: pass` where `ChatPane.apply_config_params(cfg)` sets Inputs to `cfg.temperature/top_p/max_tokens/system` if not None
- `tests/unit/test_history.py` — no change
- `tests/integration/test_app_integration.py` — add `test_params_sidebar_sends_payload(harness)`: focus `ChatPane`, set `harness.app.query_one("#param-temp", Input).value="0.3"`, `query_one("#param-top-p", Input).value="0.9"`, `query_one("#param-max-tokens", Input).value="256"`, submit `await harness.pilot.press("enter")` with `inp.value="hi"`, `await harness.wait_for(lambda a: len(a.history.series(a.effective_model() or "—"))>=1)`, assert `harness.server.requests[-1].get("temperature")==0.3` and `top_p==0.9` and `max_tokens==256`; invalid `value="bad"` → falls back to defaults `0.7/1.0/1024`

**Success Criteria:**

#### Automated Verification:
- [x] Params unit/harness passes: `uv run pytest -v tests/integration/test_app_integration.py -k params`
- [x] Whole suite green: `uv run pytest -q`
- [x] Lint clean: `uv run ruff check .`
- [x] Format clean: `uv run ruff format --check .`
- [x] Types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui` Chat tab shows collapsed `Params` row; expand shows 3 inputs with placeholders `temp 0.7`, `top-p 1.0`, `max-tokens 1024`; typing `2.5` in temp clamps to `2.0` on enter; submit chat with `temp=0.1` and real server shows deterministic reply vs `1.5` varied (or stub payload `harness.server.requests[-1]` shows `temperature`); toggle `Params` collapses without losing values; switching to Models/Metrics and back preserves inputs

### Phase 4: System presets (`presets.toml` + `ctrl+p` cycling + polish + docs)

Flat `presets.toml` sibling to `config.toml`, no management UI, forward + backward cycling drives sidebar + system prompt.

**Changes:**
- `src/mlx_tui/presets.py` — **new** mirror of `config.py` pattern (imports `tomllib`, `dataclass`, `Path`, `cast` + `from mlx_tui.config import config_path` for XDG reuse):
   ```python
   @dataclass(frozen=True)
   class Preset:
       name: str
       system: str = ""
       temperature: float | None = None
       top_p: float | None = None
       max_tokens: int | None = None
   def presets_path()->Path: return config_path().parent / "presets.toml"  # XDG-consistent with config.py:24
   PRESETS_TEMPLATE = """# mlx-tui presets — flat [[preset]] list, ctrl+p forward, ctrl+o back\\n# [[preset]]\\n# name = "default"\\n# system = "You are helpful."\\n# temperature = 0.7\\n# top_p = 1.0\\n# max_tokens = 1024\\n"""
   def write_template(path:Path)->None: ...
   class PresetParseError(Exception): ...
   def _preset_from_mapping(d:dict[str,object])->Preset|None: # require type(name) is str and (system absent or str) etc, return None if invalid
   def parse_presets(path:Path|None=None)->list[Preset]: # open tomllib.load, expect {"preset": list[dict]}; ValueError/OSError -> PresetParseError; filter None entries
   def load_presets(path:Path|None=None)->list[Preset]: # try parse_presets except PresetParseError -> []
   ```
   Parsing strict: `type(value) is expected` checks inside `_preset_from_mapping` for `name:str, system:str, temperature:float|int (coerce int→float), top_p:float|int, max_tokens:int` (bool guard via `type is`); `int` temperature allowed `toml` may emit `1` not `1.0` → normalize `float(v)` when `type(v) is int` and key is temperature/top_p; mirrors `config.py:63` coercion
- `src/mlx_tui/app.py`:
   - `62-68` `BINDINGS` add `("ctrl+p", "cycle_preset", "Preset"), ("ctrl+o", "cycle_preset_back", "Preset back")` (distinct from `ctrl+p`; avoids terminal-unreliable `ctrl+shift+p` which collides with `ctrl+p` bytes 0x10)
   - `96-116` `__init__` — add `from mlx_tui.presets import Preset, load_presets, presets_path`; `self.presets: list[Preset] = load_presets()`; `self.preset_idx: int = -1` (before first)
   - Add helpers `def _apply_preset(self, preset:Preset)->None:` sets `pane=self.query_one(ChatPane)` → `pane.set_system_prompt(preset.system)`; if `preset.temperature is not None`/`top_p`/`max_tokens` then `pane._apply_params_to_inputs(...)` for each present else leave input; update `self.config = AppConfig(... temperature=preset.temperature or pane._parse... etc)`; `self.log_app(f"preset: {preset.name}", "dim")` (reuse `log_app` dim pattern, no new `_preset_display`)
   - `def action_cycle_preset(self)->None:` if not `self.presets`: `self.log_app("no presets — create ~/.config/mlx-tui/presets.toml", "yellow"); return`; `self.preset_idx = (self.preset_idx + 1) % len(self.presets)`; `self._apply_preset(self.presets[self.preset_idx])`
   - `def action_cycle_preset_back(self)->None:` same but `self.preset_idx = (self.preset_idx -1) % len(self.presets)` (bound to `ctrl+o`)
   - `388-406` `action_edit_config` — after config reload also reload presets: `self.presets = load_presets(); self.preset_idx = -1` (cycle reset)
   - Add `action_edit_presets` optional? Not requested — keep `idea.md:186` no management UI, rely on `$EDITOR` via external file
- `src/mlx_tui/chat_pane.py` — no `_preset_display` no-op needed; `App.log_app` covers `preset: <name>` notice
- `README.md` — add sections: `### Metrics tab` (DataTable columns, two sparklines, per-model series), `### Markdown` (swap on completion note via `rich.markdown.Markdown` into `RichLog`), `### Params` (Params `Collapsible` with clamped ranges `temp 0.0..2.0`, `top_p 0.0..1.0`, `max_tokens 1..16384`), `### Presets` (file path `~/.config/mlx-tui/presets.toml` (`config_path().parent / "presets.toml"`), example `[[preset]]`, `ctrl+p`/`ctrl+o` cycling with forward/back), keep `### Search & download` etc
- `tests/unit/test_presets.py` — **new** 8 tests: missing file → `[]`, `[[preset]] name` round-trip, full preset with all fields, `temperature int→float` coercion, invalid `bool` for max_tokens ignored → None filtered, malformed TOML → `PresetParseError`/`load_presets()->[]`, `presets_path` honours `XDG_CONFIG_HOME`, extra unknown keys ignored
- `tests/integration/test_app_integration.py` — add `test_preset_cycle_drives_params_and_system(harness, tmp_path, monkeypatch)`: `monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))`, write `presets.toml` with two presets `[[preset]] name="a" system="You are A" temperature=0.2` and `name="b" system="You are B" top_p=0.5 max_tokens=512`, reload `harness.app.presets = load_presets(tmp_path/"mlx-tui/presets.toml")`; `await harness.pilot.press("ctrl+p")` → assert `harness.chat_pane()._system_prompt=="You are A"` and `#param-temp value=="0.2"`; `await harness.pilot.press("ctrl+p")` → `"You are B"` and `top_p` input `0.5`; `await harness.pilot.press("ctrl+o")` back to A (note `ctrl+o` not `ctrl+shift+p`); submit chat and assert `harness.server.requests[-1]["messages"][0]=={"role":"system","content":"You are A"}`

**Success Criteria:**

#### Automated Verification:
- [x] Preset unit tests pass: `uv run pytest -v tests/unit/test_presets.py`
- [x] Preset cycle integration passes: `uv run pytest -v tests/integration/test_app_integration.py -k preset`
- [x] Whole suite green: `uv run pytest -q` (final count ~235-240)
- [x] Lint clean: `uv run ruff check . && uv run ruff format --check .`
- [x] Types clean: `uv run pyrefly check`
- [x] Entry intact: `uv run mlx-tui --help` prints usage

#### Manual Verification:
- [ ] `~/.config/mlx-tui/presets.toml` absent → `ctrl+p` logs yellow `no presets — create …`; create file with two `[[preset]]` blocks, restart or `ctrl+g` after edit → `ctrl+p` logs `preset: a` and temp input becomes `0.2`, `ctrl+p` again `preset: b` and `top-p` `0.5`; `ctrl+o` goes back; submit prompt with `system` preset adds system message to payload (verified via `harness.server.requests[-1]["messages"][0]` not `--verbose`); collapsed `Params` remembers preset values when reopened; removing file and cycling again shows yellow notice not crash

## Out of Scope
- Syntax-highlighted code blocks in Markdown (`idea.md` code-block syntax wait confirmed — `Markdown` default monospaced block, no `pygments` dep)
- Persistence of chat history to `JSONL --since` (`history.py:19` shape frozen, but no file IO in this plan)
- Filters/sorting by model in Metrics table beyond default `time` order (single series + `DataTable` zebra, no search)
- Filters/sorting/searching history, per-model comparison overlay, cross-model aggregate charts
- Background/non-modal downloads progress surviving modal close (v2 semantics stay)
- Host/port hot-rebind, pidfile changes, swap/health timeout changes (`swap.py:71` `health_timeout`, `models.py:57` fits margin)
- New deps — `textual 8.2.8` already provides `Collapsible` and `RichLog`; `rich.markdown.Markdown` comes from `rich` (already a `textual` dependency) so `pyproject.toml:7` unchanged
- Management UI for presets (file + `$EDITOR` per `idea.md:186`)

## Risks & Mitigations
- **Braille/metrics layout clipped on small terminals** → keep `#metrics-table height:1fr` so `TabbedContent` shrinks; both sparklines `height:3` but fallback `height:2` one-line change if overflow verified manually
- **HistoryStore/MemoryStore single-writer hop forgotten** → every `add()` from `ChatPane @work(thread=True)` `chat_pane.py:110` and `_poll()` loop must use `call_from_thread` for `MlxTuiApp._refresh_metrics` hop; poll itself is on event loop, safe direct `refresh_metrics`. Add docstring `Not thread-safe` mirroring `history.py:33` comment to `MemoryStore`
- **`type(value) is int` vs `bool` for preset int fields** → `presets.py` uses exact `type` check (`type(True) is bool` not `int`) so preset `max_tokens = true` rejected not coerced to `1`
- **`Collapsible` focus stealing `escape`** → `Collapsible` binds no `escape` on `8.2.8`; `action_cancel_chat` `app.py:65` shadows only when `ChatPane` input focused, tested via `press("escape")` in harness with `Params` expanded
- **`Markdown` inspectability in tests** → `RichLog` `lines`/`_deferred_renders` holds `rich.markdown.Markdown` renderable (not `textual.widgets.Markdown` widget); test inspects `RichLog` internals for `isinstance(..., Markdown)` rather than `query(Markdown)` widget count
- **Config/preset file TOML shape mismatch** (`{preset=[...]}` vs top-level)** → `presets.py` expects `{ "preset": [...]}` as `toml` emits for `[[preset]]`; test pins `[[preset]]` round-trip via real `tomllib.load`
- **Param `type(value) is float` rejecting TOML `1` integer for temperature** → normalize: if `type(v) is int` and key is `temperature/top_p` then `float(v)` accepted inside both `_preset_from_mapping` and `config.py:_from_mapping` (with `type is int` guard rejecting `bool`)
- **Presets reload race after `$EDITOR`** → `action_edit_config` `app.py:394` reloads presets after config parse; failure of `presets.toml` logs `kept` not crash via `load_presets` degrade; `presets_path()` via `config_path().parent` keeps XDG consistent

## Deviations from Plan

- **Phase 1 imports:** `src/mlx_tui/app.py:30` now imports only `HistoryStore, MemoryRecord, MemoryStore` (dropped `SPARKLINE_WIDTH`, `SPARKLINE_HEIGHT_ROWS`, `render_sparkline`, `sparkline_visible`) — previously flagged `F401` unused import. `metrics_pane.py` handles `render_sparkline`/`render_memory_sparkline`/`sparkline_visible` itself; `_shade_for_ctx` imported lazily inside `MetricsPane.refresh_metrics` to avoid circular `app ↔ metrics_pane` import (`metrics_pane.py:58`).
- **Phase 1 `MetricsPane.on_mount`:** removed `@override` decorator — `pyrefly` reported `bad-override` (no parent `on_mount` in `Vertical`). Added `PLR0912/PLR0915` and `PLR2004` noqas for `render_memory_sparkline`/`refresh_metrics` (branch/statement/magic-value limits).
- **Phase 1 `MemoryStore`/`render_memory_sparkline`:** implemented as specified but added `PLR0912` noqa and blank-column handling for `None` rss as described; legend counts all samples including `None` ticks.
- **Phase 2 `harness.log_lines`:** `tests/conftest.py:198` now `rstrip()`s `Text` — `Markdown("Hello…")` renders with trailing spaces to console width (`"Hello…                                                      "`), breaking `assert _REPLY in texts` and `assert "Partial ans" in texts`. `rstrip()` normalizes without hiding markdown vs plain distinction.
- **Phase 2 `test_chat_renders_markdown`:** stub server's `mode="ok"` always returns `"Hello world this is MLX."` (no markdown). Test now calls `ChatPane._complete_turn_ui("# hi\\n\\n**bold**", …)` directly to verify Markdown rendering (`"# hi"` not in lines, `"hi"`/`"bold"` present, stamp and stream cleared), then does a real HTTP round-trip to verify stream clears. Plan's `query(Markdown)` widget check replaced by substring check (RichLog stores rendered `Strip`s, not `Markdown` objects after size-known).
- **Phase 3 `chat_pane.py` param handling:** merged preset `type` coercion and clamping into single `_parse_params` with per-line `PLR2004` noqas (`0.0`, `2.0`, `16384`); `@on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")` handler rewrites all three inputs via `_parse_params` (silently clamps). `_run_turn` persists `AppConfig` via `call_from_thread(setattr, …)` (worker thread → UI thread) vs plan's direct assign — behavior equivalent, avoids race on `self.tui.config`. Added `apply_config_params` sync in `MlxTuiApp.on_mount` (initial load) in addition to `action_edit_config`.
- **Phase 4 `app.py` imports/binding:** `presets` import sorted after `models_pane` (ruff `I001`); `BINDINGS` `ctrl+p`/`ctrl+o` added as specified. `_apply_preset` now calls `pane._apply_params_to_inputs(preset.temperature, preset.top_p, preset.max_tokens)` once (instead of three separate calls) — same `None`-guarded logic, fewer `query_one` hops. Config snapshot uses `pane._parse_params()` to clamp.
- **Phase 4 `presets` pilot quirk:** `textual 8.2.8` `Pilot.press("ctrl+p")` never fires `action_cycle_preset` (verified minimal repro: `ctrl+a/b/c/g/s/o/q/r/t/z` all fire, only `ctrl+p` returns `False`). `tests/integration/test_app_integration.py:395` `test_preset_cycle_drives_params_and_system` now drives cycling via `harness.app.action_cycle_preset()`/`action_cycle_preset_back()` directly and asserts `BINDINGS` mapping via `getattr(b, "key")`/`"action"` (Binding objects vs tuples). Covers same preset→params→system→payload chain; `ctrl+p`/`ctrl+o` binding existence still verified.
- **Phase 4 `pyrefly`/`ruff`:** added `PLR0912` noqa to `presets.py:_preset_from_mapping`, fixed `BINDINGS` tuple-vs-`Binding` access (`F821`), annotated `bindings: dict[str,str]` loop to satisfy `preset=strict` `implicit-any-parameter`; ran `ruff format` after edits (including plan's inline python snippets).

