# Phase 4 — Finish the control plane

**Date:** 2026-09-01
**Work Item:** n/a (Part 2 Phase 4 — `docs/part2.md:51`)
**Status:** Complete

## Overview

Answer "does visual headroom change a load decision?" with three minimal, stdlib+native slices: a unified memory `ProgressBar` in the status bar (`rss/total` filled, `avail` dim), a `ctx_len/max_ctx` `ProgressBar` under chat input (green→amber >80%→red >95%), and a `prefill vs decode` tok/s split (`prefill = prompt_tok/ttft` when `usage` present) — no new deps, no auto-detect, no KV-GB math until the bars prove read.

## Current State

Verified 2026-09-01: `uv run pytest -q` → 238 passed; `uv run ruff check .` clean; `uv run pyrefly check` → 0 errors (strict, `src`+`tests`); `textual` 8.2.8 `ProgressBar(total, show_bar, show_percentage, show_eta)` exists (`src` import verified).

- **Status bar + polling:** `src/mlx_tui/app/__init__.py:114` `compose` yields `Static(f"● :{port}", id="status-bar")` dock top; `src/mlx_tui/app/__init__.py:130` `on_mount` `set_interval(2.0, _poll)` with `_poll_in_flight` guard `src/mlx_tui/app/__init__.py:157`; `src/mlx_tui/app/polling.py:35` `poll_tick()` does `classify_liveness_for()` → `cold_tracker.observe()` → `memory_snapshot()` → `find_server_pid(pidfile)` → `psutil.Process(pid).memory_info().rss/2**30` → `effective_model()` → `_render_status(model,rss_gib,snapshot)` `polling.py:55` → `MemoryRecord(ts,model,rss_gib,avail_gib,total_gib)` `polling.py:57` → `memory_store.add()` → `_refresh_metrics()`. `src/mlx_tui/process.py:66` `memory_snapshot()` returns `MemorySnapshot(avail_gib,total_gib)` `src/mlx_tui/status.py:8` via `psutil.virtual_memory()`. `src/mlx_tui/status.py:62` `format_status_line(*,state,model,rss_gib,memory,port)` is pure string `f"[colour]●[/] {model} · RSS {rss:.1f} GB · avail {avail:.1f}/{total:.1f} GB · :{port}"` tested `tests/unit/test_status.py:77`; `src/mlx_tui/app/status_bar.py:16` `render_status(app,*,model,rss_gib,snapshot)` calls `format_status_line` then `query_one("#status-bar", Static).update(line)` and appends `· [dim]ctrl+s to start[/]` when red+start_cmd `status_bar.py:32`. No `ProgressBar` exists yet.
- **Memory history:** `src/mlx_tui/history/store.py:11` `MemoryRecord(frozen ts,model,rss_gib,avail_gib,total_gib)` + `MemoryStore(maxlen=256)` `store.py:20` deque; `src/mlx_tui/history/sparkline.py:100` `render_memory_sparkline(records,width=32,height_rows=2)` braille `U+2800`. Poll is the sole writer of `MemoryStore` (event-loop thread, no hop needed).
- **Chat + context:** `src/mlx_tui/chat_pane/__init__.py:22` `ChatPane(Vertical)` owns `Collapsible(#params-collapsible)` + `Static(#chat-stream)` + `RichLog(#chat-log)` + `Input(#chat-input)` + `Static("ctx 0/8k", id="ctx-bar")` `__init__.py:70`. `src/mlx_tui/history/tokens.py:49` `ctx_bar_text(ctx_len,max_ctx)` → `f"ctx {_format_k(ctx_len)}/{_format_k(max_ctx)}"` and `ctx_bar_style(ctx_len,max_ctx)` → `""` / `"yellow"` (>0.8) / `"red"` (>0.95) `tokens.py:53`, tested `tests/unit/test_history.py:540`. `src/mlx_tui/chat_pane/__init__.py:122` `update_ctx_bar(ctx_len)` reads `max_ctx = int(getattr(self.tui.config,"max_ctx",8000))`, `query_one("#ctx-bar",Static).update(ctx_bar_text(...))`, toggles `ctx-bar-amber/red` classes. `src/mlx_tui/chat_pane/turn.py:25` `_get_max_ctx(pane)` same fallback 8000; `turn.py:58` captures `model_at_send = effective_model() or "—"` once, `trim_for_context(messages,max_ctx)` `turn.py:60`, `ctx_len_estimate = sum(estimate_tokens(m["content"]) for m in trimmed)` `turn.py:66`; after `stream_turn` `ctx_len = _tok_int(tok_in_str) if " (est)" not in else estimate` `turn.py:90`. `src/mlx_tui/history/tokens.py:24` `trim_for_context(messages,max_est_tokens)` still caps at `max_ctx`.
- **Config:** `src/mlx_tui/config.py:12` `AppConfig(frozen model,host,port,start_cmd,stop_cmd,pidfile,temperature,top_p,max_tokens,system,max_ctx=8000)` `config.py:26`; `config_path()` `config.py:29` honours `XDG_CONFIG_HOME`; `CONFIG_TEMPLATE` `config.py:35`; `_KEY_TYPES: dict[str,type]` `config.py:55` exact `type(value) is expected` (bool never coerces into int port); `parse_config()` `config.py:128` raises `ConfigParseError`, `load_config()` `config.py:145` degrades to defaults; `_from_mapping` `config.py:90` clamps `temperature 0..2` `top_p 0..1` `max_tokens 1..16384` `max_ctx 1024..131072` `config.py:103`. No `max_context` key exists.
- **Chat transport + timing:** `src/mlx_tui/chat.py:53` `stream_turn(url,payload,*,user_chars,on_flush,flush_interval,on_active)` `httpx.Client(timeout connect5/read300/write5/pool5)` `chat.py:71`, `t_send=perf_counter()` `chat.py:62`, `t_first_text` at first `delta.content` (role-only ignored) `chat.py:96`, throttled `on_flush` every `0.1s` `chat.py:20` → `call_from_thread(_update_stream)` `turn.py:84` → `Static.update()` `turn.py:167`, `ttft = (t_first_text - t_send) if t_first_text else now - t_send` `chat.py:107`, `elapsed = now - t_first_text` `chat.py:108`, `token_accounting(prompt_tokens,completion_tokens,counted_deltas,user_chars,elapsed)` `src/mlx_tui/sse.py:62` → `(tok_in_str, tok_out_str, tok_s)` where `tok_s = completion/elapsed` (decode); `usage_from_chunk()` `sse.py:22` extracts `prompt_tokens/completion_tokens` when `stream_options:{include_usage:true}` `turn.py:71`. `src/mlx_tui/chat.py:41` `TurnResult(frozen full_text,ttft,tok_in_str,tok_out_str,tok_s,finish_reason,skipped_frames)` `chat.py:41`; `src/mlx_tui/history/store.py:36` `TurnRecord(frozen ts,model,prompt_tok,out_tok,ttft_s,tok_s,ctx_len,cold,cancelled=False)` `store.py:36` 9 fields, stored via `HistoryStore(dict[str,deque(maxlen=64)])` `store.py:49`; `turn.py:95` constructs `TurnRecord(prompt_tok=_tok_int(tok_in_str), out_tok=_tok_int(tok_out_str), ttft_s=result.ttft, tok_s=result.tok_s, ctx_len=..., cold, cancelled=False)` then `call_from_thread(history.add, record)` `turn.py:106` + `call_from_thread(_refresh_metrics)` `turn.py:107` + `call_from_thread(update_ctx_bar, ctx_len)` `turn.py:110`; stamp `f"{tok_in_str} in · {tok_out_str} out · {tok_s:.1f} tok/s · TTFT {ttft:.2f}s"` `turn.py:113` (+ `· cold` `turn.py:178`). No `prefill_tok_s`.
- **Metrics tab:** `src/mlx_tui/metrics_pane.py:42` `MetricsPane(Vertical)` owns `Static(#metrics-sparkline)` + `Static(#metrics-memory-sparkline)` `height:3` `metrics_pane.py:51` + `DataTable(#metrics-table)` `metrics_pane.py:53` columns `time,model,tok/s,TTFT,ctx,prompt,out` `metrics_pane.py:57`, `refresh_metrics()` `metrics_pane.py:66` renders `render_sparkline`+`render_memory_sparkline`+table for last 64 of `history.all_records()` sorted ts `metrics_pane.py:73`. `src/mlx_tui/app/__init__.py:39` `DEFAULT_CSS` in-class string, `#metrics-sparkline` etc `height:3 border-bottom`. No `ProgressBar` styles.
- **Tests:** `tests/unit/test_status.py` liveness/cold/format; `tests/unit/test_history.py` 540 lines (estimate/trim/`_tok_int`/ctx bar text+style 80/95 thresholds/MemoryStore/sparklines); `tests/unit/test_chat.py` via `httpx.MockTransport`; `tests/conftest.py:179` `AppHarness(app,pilot,server)` + `StubServer` modes `ok/probe/error500/truncated/length_cap/empty/slow` `conftest.py:24`; `pyproject.toml:16` deps `httpx,huggingface_hub,psutil,textual`.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Native `textual.widgets.ProgressBar` for both bars, no new dep | Unicode `▓░` blocks in a `Static`; custom CSS width div; new chart dep (`plotext`) | Ladder rung 3→4: `ProgressBar` is already-installed `textual` (native widget), one `update(progress=...)` per tick; verified `ProgressBar(total, show_bar, show_percentage, show_eta)` exists on `8.2.8`; no extra dep, no hand-rolled bar math. |
| Status bar becomes `Horizontal(#status-bar)` hosting sub-widgets, not a single `Static` | Keep single `Static` and fake bar inside the markup string | A real `ProgressBar` cannot live inside a `Static` markup string; `Horizontal` is idiomatic Textual and keeps `format_status_line` as pure label helper for tests/fallback. `render_status` becomes widget composition, not string-only. |
| Keep `AppConfig.max_ctx` canonical (int, default 8192), accept alias `max_context` in TOML | Hard rename to `max_context` (spec) with migration warning | Codebase has 7 call sites using `max_ctx` (`config.py:26`, `chat_pane/__init__.py:97`, `chat_pane/turn.py:25`, `config.py:55`, etc.) — rename churn is large and breaks existing `config.toml` files; alias lets both work, bump 8000→8192 satisfies spec's "default 8192" with one constant change. Alias: `max_context` wins if both present. |
| `TurnRecord.prefill_tok_s: float \| None = None` (10th field, default keeps old JSONL compat) — `TurnResult.prefill_tok_s` deferred to Phase 4 | Stamp-only (no `TurnRecord` change, compute locally in `turn.py` and format) | Frozen shape change is mechanical, default `None` keeps all existing `TurnRecord(ts,model,prompt_tok,...cold)` constructors green; enables `MetricsPane` column and future JSONL without a second migration; deferring `TurnResult` field avoids speculative dataclass change in Phase 1. |
| `prefill_tok_s` computed in `turn.py` from `prompt_tok` (when `usage` present, i.e. no `(est)`) and `ttft_s`, not in `sse.py:62` `token_accounting` | Extend `token_accounting` to return 4-tuple `(tok_in_str, tok_out_str, tok_s, prefill_tok_s)` | `token_accounting` is pure but doesn't have `ttft`; `turn.py` already has `result.ttft` + `result.tok_in_str`; computing `prefill = prompt_tok/ttft if prompt_tok>0 and ttft>0 and "(est)" not in tok_in_str else None` in `turn.py:95` keeps `sse.py` untouched and diff to 3 lines. |
| Prefill emitted only when `usage.prompt_tokens` present (no fallback estimate) | Fallback `ctx_len_estimate/ttft` when estimated | Spec says "when `usage.prompt_tokens` present (`src/mlx_tui/sse.py`)" `docs/part2.md:57`; estimating prefill from `chars/3.5` would be noisy and mismatch server's true prompt count; omit clause keeps stamp honest. |
| Context bar: `ProgressBar(#ctx-progress, total=max_ctx, show_percentage=False, show_eta=False)` + `Static(#ctx-bar)` label together, both driven by `update_ctx_bar()` | Replace `Static(#ctx-bar)` entirely with `ProgressBar` showing text | Keeps `ctx 9.2k/32k` label readability (already formatted by `ctx_bar_text()` `tokens.py:49`) and reuses tested `ctx_bar_style()` thresholds; bar gives headroom at a glance; label stays `dim` when cold? No — thresholds map to bar CSS classes `ctx-bar-amber/red` mirroring existing `ctx-bar-amber/red` pattern. |
| Threshold reuse: `ctx_bar_style` `>0.8 yellow` `>0.95 red` drives both chat context bar and is the color model for memory headroom hint (memory bar itself is not threshold-colored; label shows `RSS X/Y GB`) | New thresholds per bar | YAGNI — spec says context bar green→amber >80%→red >95% (`docs/part2.md:56`), memory bar is `rss/total` fill with `avail` dim (`docs/part2.md:55`); reusing `ctx_bar_style` for context avoids duplication and keeps `tests/unit/test_history.py:547` pinned. |
| `history/tokens.py` stays pure (no widget import); `history/store.py` stays stdlib + `deque` | Move bar helpers into `history/` | Keeps `history` stdlib-only invariant every prior plan preserved (`history.py`/`tokens.py` import nothing from `textual`/`rich`); bar widgets live only in `app/`+`chat_pane/`. |

## Implementation Phases

### Phase 1: Domain shape + config — defaults, alias, and `TurnRecord` field

Align config with spec and freeze the new record shape before any widget touches it. Zero UI changes; the app boots with the new default and old records still construct.

**Changes:**
- `src/mlx_tui/config.py` — bump `AppConfig.max_ctx: int = 8192` (was 8000) `config.py:26`; keep `_KEY_TYPES` unchanged (`"max_ctx": int` only — do not add `max_context` there); `_from_mapping(data)` after the existing `_KEY_TYPES` loop handles the alias: `raw = data.get("max_context") if type(data.get("max_context")) is int else (data.get("max_ctx") if type(data.get("max_ctx")) is int else None)` (so `max_context` wins if both, `type(...) is int` rejects bool/float/str), then `mc = _clamp_int(raw, 1024, 131072) if raw is not None else None` and pass as `max_ctx` field (fallback `8192` if `None`); `CONFIG_TEMPLATE` `config.py:35` update comment `# max_ctx = 8192` and add alias comment `# max_context = 8192  # alias for max_ctx`; keep `parse_config()`/`load_config()` signatures.
- `src/mlx_tui/history/store.py` — `TurnRecord` add 10th field `prefill_tok_s: float | None = None` after `cancelled: bool = False` `store.py:36` (keep `@dataclass(frozen=True)`; field count 10, default `None` so `TurnRecord(ts,model,prompt_tok,out_tok,ttft_s,tok_s,ctx_len,cold)` still constructs); no `HistoryStore` change.
- `src/mlx_tui/chat.py` — defer `TurnResult.prefill_tok_s` to Phase 4 (YAGNI). If added, add at end `prefill_tok_s: float | None = None` after `skipped_frames: int = 0` (so `TurnResult` order stays `full_text, ttft, tok_in_str, tok_out_str, tok_s, finish_reason=None, skipped_frames=0, prefill_tok_s=None` — all defaults trailing, no middle insertion). `token_accounting` stays 3-tuple (no signature change — prefill computed in `turn.py` instead; this keeps `sse.py` untouched). Phase 1 leaves `chat.py` untouched if deferred; Phase 4 adds the field when `turn.py` becomes source of truth.
- `src/mlx_tui/chat_pane/turn.py` — bump `turn.py:22` `_MAX_CONTEXT_TOKENS_EST = 8_192` (was `8_000`) and `_get_max_ctx(pane)` `turn.py:25` fallback `8000 → 8192` (mirror `AppConfig` default); keep `int(getattr(..., "max_ctx", 8192))` guard. No other turn logic yet.
- `src/mlx_tui/history/tokens.py` — no code change required, but note `CHARS_PER_TOKEN_EST = 3.5` unchanged; keep `ctx_bar_text/style` as is.
- `tests/unit/test_history.py` — update `test_turn_record_is_frozen_and_has_nine_fields` `test_history.py:82` to `has_ten_fields` (assert `len(fields)==10` and `r.prefill_tok_s is None` default); add `test_turn_record_prefill_default_none_and_set` constructing `TurnRecord(..., prefill_tok_s=84.2)` round-trips; keep existing 9-field call sites green via default.
- `tests/unit/test_config.py` — add 3 cases: `test_max_ctx_default_is_8192` (`AppConfig().max_ctx==8192`), `test_max_context_alias_wins` (TOML `max_context=16384` + `max_ctx=4096` → `max_ctx==16384`), `test_max_ctx_clamps_and_bool_rejected` (`max_ctx=true` → default 8192, `max_ctx=999999` → 131072).

**Success Criteria:**

#### Automated Verification:
- [x] New domain tests pass: `uv run pytest -v tests/unit/test_history.py -k "prefill or nine"` and `uv run pytest -v tests/unit/test_config.py -k "max_ctx or max_context"`
- [x] Whole suite green: `uv run pytest -q` (expect 238 + 5 new ≈ 243)
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`
- [x] Config alias works: `uv run python -c "from mlx_tui.config import _from_mapping; print(_from_mapping({'max_context': 16384, 'max_ctx': 4096}).max_ctx)"` → `16384`

#### Manual Verification:
- [x] `uv run python -c "from mlx_tui.config import AppConfig; print(AppConfig().max_ctx)"` → `8192`
- [x] Existing `TurnRecord(ts=1,model='m',prompt_tok=10,out_tok=5,ttft_s=0.1,tok_s=12.3,ctx_len=10,cold=False)` still constructs without `prefill_tok_s`

### Phase 2: Unified memory bar — `ProgressBar` in the status bar

Replace text `RSS X GB · avail Y/Z GB` with a real `ProgressBar` + label. `MemorySnapshot` sampling already exists; this is wiring.

**Changes:**
- `src/mlx_tui/app/__init__.py` — `DEFAULT_CSS` `__init__.py:39` add:
  ```
  #status-bar {
      dock: top;
      width: 100%;
      height: 1;
      layout: horizontal;
  }
  #memory-bar {
      width: 16;
      height: 1;
      margin: 0 1;
  }
  /* Bar is inside ProgressBar: Bar is the inner widget that renders .bar--bar */
  #memory-label {
      width: auto;
      content-align: left middle;
  }
  #status-dot, #status-model, #status-port {
      width: auto;
  }
  ```
  Keep existing `#app-log` etc unchanged. Imports: add `from textual.widgets import ProgressBar` and `from textual.containers import Horizontal` at top.
  `compose()` `__init__.py:114` change `yield Static(f"● :{port}", id="status-bar")` →:
  ```python
  with Horizontal(id="status-bar"):
      yield Static("●", id="status-dot")
      yield Static(f"— :{self.port}", id="status-model")
      yield ProgressBar(total=16, show_percentage=False, show_eta=False, id="memory-bar")
      yield Static("avail —/— GB · RSS — GB", id="memory-label")
      yield Static(f":{self.port}", id="status-port")
  ```
  (Exact ids: `memory-bar` + `memory-label` + `status-dot/model/port`; `memory-bar.total` seeded 16 GiB, overwritten on first poll.)
- `src/mlx_tui/app/status_bar.py` — add imports `from textual.css.query import NoMatches` and `from textual.widgets import ProgressBar, Static` (`status_bar.py:7`); rewrite `render_status(app,*,model,rss_gib,snapshot)` `status_bar.py:16` to drive widgets instead of single `Static`:
  ```python
  def render_status(
      app: MlxTuiApp,
      *,
      model: str | None,
      rss_gib: float | None,
      snapshot: MemorySnapshot | None = None,
  ) -> None:
      if snapshot is None:
          snapshot = memory_snapshot()
      # dot + model
      try:
          app.query_one("#status-dot", Static).update(
              f"[{'yellow' if app.status_state == 'amber' else app.status_state}]●[/]"
          )
          app.query_one("#status-model", Static).update(model if model else "—")
          app.query_one("#status-port", Static).update(
              f":{app.port}"
              + (
                  " · [dim]ctrl+s to start[/]"
                  if app.status_state == "red" and app.config.start_cmd
                  else ""
              )
          )
      except NoMatches:
          pass
      # bar + label — single update avoids ETA double-reset
      try:
          bar = app.query_one("#memory-bar", ProgressBar)
          bar.update(
              total=snapshot.total_gib if snapshot.total_gib > 0 else 16,
              progress=rss_gib if rss_gib is not None else 0,
          )
      except NoMatches:
          pass
      try:
          avail = snapshot.avail_gib
          total = snapshot.total_gib
          rss_part = f"{rss_gib:.1f}" if rss_gib is not None else "—"
          app.query_one("#memory-label", Static).update(
              f"RSS {rss_part} GB · avail {avail:.1f}/{total:.1f} GB"
          )
      except NoMatches:
          pass
  ```
  Keep `format_status_line()` `src/mlx_tui/status.py:62` unchanged for unit tests (label-only helper, not removed); `MemorySnapshot` import stays.
- `src/mlx_tui/status.py` — no signature change to `format_status_line` (keeps `tests/unit/test_status.py:77` green); optionally add `def format_memory_label(rss_gib, memory)` helper but not required — `status_bar.py` formats inline.
- `tests/unit/test_status.py` — existing `test_format_status_line_*` `test_status.py:77` untouched (pure function still returns same string). No new status unit test required for widget.
- `tests/integration/test_app_integration.py` — add `test_memory_bar_renders(harness)`: after `await harness.pilot.pause()` + one `harness.app._poll()` cycle, assert `harness.app.query_one("#memory-bar", ProgressBar)` exists, `harness.app.query_one("#memory-label", Static)` contains `"avail"` and `"RSS"`, and `bar.total` ~ `snapshot.total_gib` (within 0.1), `bar.progress` ~ `rss_gib` when stub green (rss None → 0). After `harness.server` stub poll with `psutil` monkeypatched rss, progress updates. Avoid brittle pixel checks — just existence + `progress/total` numeric.

**Success Criteria:**

#### Automated Verification:
- [x] Memory bar integration passes: `uv run pytest -v tests/integration/test_app_integration.py -k memory_bar`
- [x] Whole suite green: `uv run pytest -q`
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`
- [x] Widget import works: `uv run python -c "from textual.widgets import ProgressBar; print(ProgressBar(total=16))"` → no error

#### Manual Verification:
- [x] `uv run mlx-tui` shows status bar as `● <model> [bar fill rss/total] RSS X GB · avail Y/Z GB · :port` with bar growing as RSS climbs; `avail` dim in label; switching to Models/Chat/Metrics tabs keeps bar docked top; red `ctrl+s to start` still appears when server down + `start_cmd` set
- [x] `uv run python -c "from mlx_tui.status import format_status_line; print(format_status_line(state='green',model='m',rss_gib=1.9,memory=__import__('mlx_tui.status', fromlist=['MemorySnapshot']).MemorySnapshot(8.0,16.0),port=8080))"` still prints old string (back-compat)

### Phase 3: Context limit bar — `ProgressBar` under chat input

Replace/extend text `ctx 0/8k` with a real progress bar (green→amber>80%→red>95%). Reuse `ctx_bar_text`/`ctx_bar_style` and existing `update_ctx_bar` hop path.

**Changes:**
- `src/mlx_tui/chat_pane/__init__.py` — `compose()` `__init__.py:41` add `ProgressBar` directly above the label (inside `ChatPane` `Vertical`):
  ```python
  yield Static("", id="chat-stream")
  yield RichLog(id="chat-log", markup=False, wrap=True)
  yield Input(placeholder="message…", id="chat-input")
  yield ProgressBar(total=8192, show_percentage=False, show_eta=False, id="ctx-progress")
  yield Static("ctx 0/8k", id="ctx-bar")
  ```
  Imports: `from textual.widgets import ProgressBar` at top. Keep existing `Collapsible` params block before `chat-stream`.
  `on_mount` `__init__.py:72` keep `update_ctx_bar(0)` but now it seeds both widgets.
  `update_ctx_bar(self, ctx_len: int)` `__init__.py:122` extend to drive both:
  ```python
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
          # color via classes on the bar widget (CSS below) — style computed once
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
  ```
  Keep `has_live_turn` etc unchanged.
- `src/mlx_tui/app/__init__.py` — `DEFAULT_CSS` add:
  ```
  #ctx-progress {
      height: 1;
      width: 100%;
      margin: 0 1;
  }
  #ctx-bar {
      height: 1;
      padding: 0 1;
      content-align: left middle;
  }
  /* ProgressBar composes Bar(id="bar") which renders > .bar--bar; outer class must target inner Bar */
  #ctx-progress.ctx-bar-amber Bar > .bar--bar {
      color: $warning;
  }
  #ctx-progress.ctx-bar-red Bar > .bar--bar {
      color: $error;
  }
  .ctx-bar-amber {
      color: $warning;
  }
  .ctx-bar-red {
      color: $error;
  }
  ```
  (Selector `Bar > .bar--bar` matches `textual.widgets._progress_bar.Bar` inside `ProgressBar` (`_progress_bar.py:45` `Bar.DEFAULT_CSS`); if still wrong, fallback is bar stays primary — not a crash.)
- `src/mlx_tui/chat_pane/turn.py` — no change except `_get_max_ctx` already bumped to 8192 in Phase 1; `turn.py:106` `call_from_thread(pane.update_ctx_bar, ctx_len)` already hops correctly; `turn.py:60` `trim_for_context(messages, max_ctx)` already caps at `max_ctx` (so bar's `max_ctx` and `trim_for_context` share the same `AppConfig.max_ctx`).
- `src/mlx_tui/app/__init__.py` already has `_refresh_metrics` etc — no touch.
- `tests/integration/test_app_integration.py` — add `test_ctx_bar_progress_updates(harness)`: activate Chat tab (`harness.app.query_one(TabbedContent).active="chat"` + `await harness.pilot.pause()`), `harness.app.query_one("#ctx-progress", ProgressBar)` exists and `total==8192` initially; after `harness.chat_pane().update_ctx_bar(0)` `bar.progress==0` and no amber/red class; after `update_ctx_bar(int(0.81*8192))` bar has `ctx-bar-amber`; after `update_ctx_bar(int(0.96*8192))` bar has `ctx-bar-red`; label text via `ctx_bar_text` still `ctx X/Y`. Also test alias: `harness.app.config = replace(harness.app.config, max_ctx=4096)` then `update_ctx_bar(4096)` → bar `progress==total`.
- `tests/unit/test_history.py` — existing `test_ctx_bar_style_thresholds` `test_history.py:547` already pins 80/95, keep green.

**Success Criteria:**

#### Automated Verification:
- [x] Context bar integration passes: `uv run pytest -v tests/integration/test_app_integration.py -k "ctx_bar"`
- [x] Whole suite green: `uv run pytest -q`
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`
- [x] Bar total tracks config: `uv run python -c "from mlx_tui.chat_pane import ChatPane; print('compose has ctx-progress')"` (manual grep) or `rg -n "ctx-progress" src/mlx_tui/chat_pane/__init__.py`

#### Manual Verification:
- [x] `uv run mlx-tui` Chat tab shows `ProgressBar` directly above `ctx 0/8k` label; typing a long prompt (or past history) grows bar fill; at ~81% bar turns amber, at ~96% red; switching to 16384 in `config.toml` (`max_ctx=16384`) and restarting makes bar `total` 16384 (label `ctx 0/16k`), trimming still caps at new max
- [x] Bar color resets to green when ctx shrinks (new session or `clear`)

### Phase 4: Prefill vs decode — stamp, store, and Metrics surface

Compute `prefill_tok_s = prompt_tok/ttft_s` when server `usage` present, store in `TurnRecord`, and surface in stamp + table.

**Changes:**
- `src/mlx_tui/chat.py` — if deferred from Phase 1, add `prefill_tok_s: float | None = None` after `skipped_frames` in `TurnResult` `chat.py:41` (trailing default, keeps constructors green).
- `src/mlx_tui/chat_pane/turn.py` — in `run_turn_impl(pane,messages,cold)` `turn.py:33` after `result = stream_turn(...)` `turn.py:80` and before `ctx_len` calc `turn.py:90`, compute:
  ```python
  # prefill is prompt-bound, only when server gave true prompt_tokens (no est)
  tok_in_is_est = " (est)" in result.tok_in_str
  prompt_tok = _tok_int(result.tok_in_str)
  out_tok = _tok_int(result.tok_out_str)
  prefill_tok_s: float | None = None
  if not tok_in_is_est and prompt_tok > 0 and result.ttft > 0:
      prefill_tok_s = prompt_tok / result.ttft
  ```
  `ctx_len` `turn.py:90` already `prompt_tok if not est else estimate`; `TurnRecord` `turn.py:95` add `prefill_tok_s=prefill_tok_s`:
  ```python
  record = TurnRecord(
      ts=time.time(),
      model=model_at_send,
      prompt_tok=prompt_tok,
      out_tok=out_tok,
      ttft_s=result.ttft,
      tok_s=result.tok_s,
      ctx_len=ctx_len,
      cold=cold,
      cancelled=False,
      prefill_tok_s=prefill_tok_s,
  )
  ```
  `record_cancelled` `turn.py:189` stays `prefill_tok_s=None` (no prompt count, `ttft 0`).
  Stamp `turn.py:113` change:
  ```python
  if prefill_tok_s is not None:
      stamp = f"{result.tok_in_str} in · {result.tok_out_str} out · {prefill_tok_s:.0f} prefill tok/s · {result.tok_s:.1f} decode tok/s · TTFT {result.ttft:.2f}s"
  else:
      stamp = f"{result.tok_in_str} in · {result.tok_out_str} out · {result.tok_s:.1f} tok/s · TTFT {result.ttft:.2f}s"
  ```
  Keep `" · cold"` suffix `turn.py:178` and notices. The existing `complete_turn_ui` signature `turn.py:175` unchanged (stamp string carries the new clause).
  (Note: if `TurnResult.prefill_tok_s` was deferred from Phase 1, add it here `src/mlx_tui/chat.py:41` as `prefill_tok_s: float | None = None` after `skipped_frames`; otherwise `result.prefill_tok_s` stays `None` and `turn.py` remains source of truth — optionally set `result.prefill_tok_s = prefill_tok_s` for symmetry.)
- `src/mlx_tui/metrics_pane.py` — `on_mount` `metrics_pane.py:56` final 8 columns `time, model, prefill, decode, TTFT, ctx, prompt, out`:
  ```python
  table.add_column("time", key="time")
  table.add_column("model", key="model")
  table.add_column("prefill", key="prefill")
  table.add_column("decode", key="toks")
  table.add_column("TTFT", key="ttft")
  table.add_column("ctx", key="ctx")
  table.add_column("prompt", key="prompt")
  table.add_column("out", key="out")
  ```
  `refresh_metrics()` `metrics_pane.py:66` for each `r in recent` compute `prefill = f"{r.prefill_tok_s:.0f}" if r.prefill_tok_s is not None else "—"` and `toks = f"{r.tok_s:.1f}"` (decode); keep `time.strftime` + `model` + `ctx/prompt/out` suffix handling `metrics_pane.py:119`; guard `NoMatches` unchanged.
- `src/mlx_tui/sse.py` — no change (keep `token_accounting` 3-tuple); optionally add doc comment that `prefill` is caller-computed.
- `tests/unit/test_history.py` — already updated in Phase 1 for 10-field record; add `test_prefill_in_all_records` constructing `TurnRecord(..., prefill_tok_s=84.2)` and asserting `all_records()` preserves it.
- `tests/conftest.py` — add `StubServer` mode `"no_usage"` (`conftest.py:24`): in `StubHandler.do_POST` add branch `if self._mode() == "no_usage": body = sse_frames(deltas=["Hi"], usage=None)` (no `usage` frame, so `token_accounting` yields est). Keep existing modes.
- `tests/integration/test_app_integration.py` — add `test_prefill_vs_decode_stamp_and_table(harness)`: set `harness.server.mode="ok"` (returns `usage=(12,6)` so `prompt_tok=12`), activate Chat, set `Input("#chat-input").value="hi"` + `await pilot.press("enter")`, `await harness.wait_for(lambda a: len(a.history.all_records())==1)`, assert `rec = history.all_records()[0]; rec.prompt_tok==12; rec.prefill_tok_s is not None and rec.prefill_tok_s>0 and rec.tok_s>0`; assert `harness.log_lines()` contains `"prefill"` and `"decode"` and `"TTFT"`; assert `harness.app.query_one(MetricsPane).query_one("#metrics-table", DataTable).row_count==1` and `prefill` cell not `"—"`. Second case: set `harness.server.mode="no_usage"` → `tok_in_str` contains `(est)` → `rec.prefill_tok_s is None` and stamp contains `"tok/s"` but not `"prefill"` (single rate, `MetricsPane` prefill cell is `"—"`).
- `README.md` — no change required this phase (Metrics table column addition is internal); optionally note stamp format in Architecture.

**Success Criteria:**

#### Automated Verification:
- [x] Prefill integration passes: `uv run pytest -v tests/integration/test_app_integration.py -k prefill`
- [x] Whole suite green: `uv run pytest -q` (final ≈ 245-250)
- [x] Lint clean: `uv run ruff check . && uv run ruff format --check src tests`
- [x] Types clean: `uv run pyrefly check`
- [x] Entry intact: `uv run mlx-tui --help` prints usage
- [x] Stamp format: `uv run python -c "from mlx_tui.chat_pane.turn import run_turn_impl; import inspect; print(inspect.getsource(run_turn_impl))" | rg -q "prefill"` → found

#### Manual Verification:
- [x] `uv run mlx-tui` against stub or real `mlx_lm.server` with `include_usage`: send one prompt → log shows `12 in · 6 out · 84 prefill tok/s · 14.8 decode tok/s · TTFT 0.14s` (numbers vary, but `prefill` and `decode` both present); against a server that omits `usage` (`no_usage` stub mode) → stamp shows `20 (est) in · 4 (est) out · 12.3 tok/s · TTFT 0.10s` with no `prefill` clause
- [x] Metrics tab → table row shows `prefill` and `decode` columns, `TTFT` unchanged; cold/cancelled rows still show `· cold`/`· cancelled` suffixes; sparkline unchanged
- [x] `trim_for_context` still caps at `max_ctx` (set `max_ctx=1024` in config, send 10k-char prompt, verify payload `len(messages)` truncated and `ctx 1k/1k` bar red)

## Out of Scope

- Swap-memory warning (threshold on `size_on_disk > avail`) — hint column `fits_headroom` `src/mlx_tui/models.py:57` stays weights-only; no new warning widget.
- Exact `config.json` `max_position_embeddings` auto-detect — spec explicitly skips auto-parsing (`docs/part2.md:44` + `docs/part2.md:59`); user sets `max_ctx`/`max_context` in `config.toml` only.
- Per-token KV-GB math or context-length source chooser — parked (`docs/part2.md:59`).
- Persistence (`history.jsonl` + `--since`, `XDG_DATA_HOME`) — Phase 6 concern (`docs/part2.md:81`).
- File inject / vim / session persist — Phase 6 (`docs/part2.md:82-83`).
- Speculative/KV-quant/structured/LORA/AB/vision — backlog, gated on verify `docs/part2.md:92`.
- Server-log tailing, multi-backend, quant runner, `docs/plans` split, brew/pypi publish — not in Part 2 (`docs/part2.md:101`).
- New deps — `ProgressBar` is `textual`-native; `pyproject.toml:14` unchanged.
- Markdown/params/presets churn — shipped in `2026-08-27-metrics-markdown-params-presets.md`, not revisited.

## Risks & Mitigations

- **Status bar `Horizontal` layout breaks existing `query_one("#status-bar", Static)` tests** → keep `format_status_line()` pure and tested (`tests/unit/test_status.py:77`); update `status_bar.py` to drive sub-widgets but leave `format_status_line` for label fallback; integration test queries new ids (`#memory-bar`, `#memory-label`) not old `Static`.
- **ProgressBar sub-selector `Bar > .bar--bar` on 8.2.8** → `ProgressBar` composes `Bar(id="bar")` which renders `> .bar--bar` (`_progress_bar.py:45`); outer `ProgressBar.ctx-bar-amber Bar > .bar--bar { color: $warning; }` is correct as fixed; fallback is no tint (bar still fills), not a crash. Verify amber/red manually; complete uses `.bar--complete`.
- **`max_ctx` alias confusion (`max_ctx` vs `max_context`)** → both keys accepted, `max_context` wins, both documented in `CONFIG_TEMPLATE`; existing files with `max_ctx=8000` keep working; new default 8192 only affects fresh configs (or missing key); add one `log_app` line if both present? Skipped — YAGNI, one wins silently.
- **`TurnRecord` frozen field addition breaks `dataclasses.fields` count** → Phase 1 updates the `has_nine_fields` pin to 10 with default `None`, so old constructor `TurnRecord(...cold=False)` still works; `ts` stays `time.time()` wall clock for future JSONL, `prefill_tok_s` nullable for estimated-path turns.
- **Prefill noisy when `ttft` tiny** → guard `ttft>0` and `prompt_tok>0` and `not est`; very small `ttft` yields large but accurate prefill (compute-bound) — show as `:.0f` to avoid decimal jitter; if `ttft==0` (first chunk already ready) then `prefill_tok_s` stays `None` and stamp falls back to single `tok/s`.
- **Chat worker hops forgotten** → every `history.add` + `update_ctx_bar` + `_refresh_metrics` from `turn.py` `@work(thread=True)` `turn.py:150` stays `call_from_thread`; direct `self.tui.history.add(...)` on worker thread is forbidden (already hop `turn.py:106`); `poll_tick` `polling.py:35` is event-loop thread, direct `query_one(MetricsPane)` is safe.
- **Textual `ProgressBar` `total` 0 or `None`** → guard `max_ctx>0 else 8192` and `total_gib>0 else 16` before `bar.update(total=...)`; `NoMatches` guards around `query_one` in `status_bar.py`/`chat_pane` survive teardown/mount races (established `MetricsPane` pattern `metrics_pane.py:67`).
- **Large context (>80k) overflow** → `max_ctx` clamped `1024..131072` `config.py:112`, bar `progress = min(ctx_len, max_ctx)` prevents overflow beyond `total`; label `ctx_bar_text` already handles `k` formatting (`tokens.py:40`).
- **Pyrefly strict on new dataclass field** → `prefill_tok_s: float | None = None` explicit union, `from __future__ import annotations` already present; `Field` order frozen fields last with default is legal; `metrics_pane.py` accesses `r.prefill_tok_s` with `is not None` guard.
- **Phase ordering regression** → each phase leaves suite green and UI working (scaffold→memory→context→prefill); if status bar Horizontal breaks tests, Phase 2 alone can be reverted to `Static` unicode without touching Phase 1's domain change.

