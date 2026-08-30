# Architecture

Implementation details for `mlx-tui` — how each pane works under the hood. For user-facing features and usage, see `README.md`.

## Overview

`mlx-tui` is a Textual TUI that talks to a local MLX OpenAI-compatible server over plain HTTP (`mlx_lm.server` / `mlx_vlm.server`). No log tailing, no backend abstraction. MLX-only by design (`docs/idea.md` §Why MLX-only) — one cache layout, one memory model.

Entry point `mlx-tui = "mlx_tui.app:main"` `pyproject.toml:14`. Python 3.13, `textual` 8.2.8, `httpx` 0.28+, `psutil` 7+, `huggingface_hub` 1.28.

## Status bar

Docked `Static(#status-bar)` at the top — never hidden. Polls `GET /v1/models` every 2 s with a 0.5 s `httpx.AsyncClient` timeout (skips the tick if the previous poll is still in flight) `src/mlx_tui/app/polling.py`.

* **Liveness** — `classify_liveness()` `src/mlx_tui/status.py` maps the response: `200` + JSON `data` non-empty list → `green`, transport failure/timeout → `red`, every other complete response (502 HTML, junk JSON, wrong shape) → `amber`.
* **Loaded model** — `effective_model()` `src/mlx_tui/app/state.py` union: tracked model (warm swap path) wins over `psutil` `model_from_cmdline()` `src/mlx_tui/process.py` (`--model` argv); gated on a live pid so a down server shows `—` not stale state.
* **Memory** — `psutil.Process(pid).memory_info().rss` for the server pid (pidfile fast path → cached pid → `process_iter` scan `src/mlx_tui/process.py`) plus `psutil.virtual_memory().available/total` via `memory_snapshot()` → `avail X.X/Y.Y GB`. History sampled every poll into `MemoryStore` (ring 256 ≈ 8.5 min) `src/mlx_tui/history/store.py:39`.

Format: `● <model> · RSS <rss> GB · avail <avail>/<total> GB · :<port>` with `[dim]ctrl+s to start[/]` when red + `start_cmd` is set. `ColdTracker` `src/mlx_tui/status.py` arms `· cold` only on a true `green→red→green` cycle. Rendering via `format_status_line()` `src/mlx_tui/app/status_bar.py`.

## Models tab

`ModelsPane` owns `Static(#swap-progress)` + `ModelsTable(DataTable)` `src/mlx_tui/table.py:25`.

Columns: `model` (repo id), `quant` (parsed suffix `-4bit/-8bit/-bf16/fp16/int4` via `quant_label()` `src/mlx_tui/models.py:33`, `—` when unknown), `size` (`size_on_disk / 2**30` → `X.Y GB`), `fits` headroom hint, `loaded` marker `●`.

* **Rows** — `scan_models()` `src/mlx_tui/models.py:81` maps `scan_cache_dir()` through `collect_rows()` (MLX heuristic: `config.json` + `tokenizer_config.json` + `*.safetensors`, biggest first). Missing cache → empty table. Populate via `src/mlx_tui/models_pane/table_ops.py`.
* **fits** — `fits_headroom()` `src/mlx_tui/models.py:57` with 20% margin: `size * 1.2 ≤ avail * 2**30` → `✓`, else `⚠`, unknown memory → `—` (`fits_cell()` `src/mlx_tui/table.py:20`). Weights-only hint — KV cache growth not counted.
* **Delete** — `d` → `ConfirmScreen` modal `src/mlx_tui/confirm.py` → `delete_repos()` `src/mlx_tui/models.py:93` `delete_revisions(*hashes).execute()`.
* **Load / swap** — `enter` → `request_load_swap()` `src/mlx_tui/models_pane/swap_ops.py`:
  - If `start_cmd` **and** `stop_cmd` configured → restart path: `stop_cmd` (`run_command` streaming), clear tracked marker, then `spawn_with_grace`/`spawn_command` for `build_start_command(start_cmd, model_id)` `src/mlx_tui/serverctl.py` (`{model}` placeholder or `--model` detection, else `--model <id>` appended), streams combined stdout/stderr via daemon thread into the log pane; health-wait `wait_healthy()` `src/mlx_tui/serverctl.py:99` polls `GET /v1/models` until green+model-matched or `health_timeout()` `src/mlx_tui/swap.py:27` expires (`60 s + 10 s/GiB`), `on_tick` drives `#swap-progress`.
  - Otherwise on green → warm path: synchronous one-token probe `warm_load()` `src/mlx_tui/serverctl.py:78` (`POST /v1/chat/completions` `max_tokens:1`).
  - Swap is a single `exclusive(group="swap")` worker; double `enter` blocked. Live chat streams cancelled client-side (`pane.abort()` socket-shutdown on macOS) before `stop_cmd`.
* **Rescan** — `ModelsPane.rescan()` `@work(thread=True, group="rescan")` on mount, tab activation and after download.

## Chat tab

`ChatPane` `src/mlx_tui/chat_pane/__init__.py:22` owns collapsed `Params` `Collapsible` `src/mlx_tui/chat_pane/params.py`, `Static(#chat-stream)`, `RichLog(#chat-log)` and `Input(#chat-input)`.

* **Transport** — `stream_turn()` `src/mlx_tui/chat.py:53` (`httpx.Client` `connect 5 s/read 300 s/write 5 s`): `POST /v1/chat/completions` `stream:true` `stream_options:{include_usage:true}` `model` (if known). Parses SSE via `iter_sse_data()` `src/mlx_tui/sse.py`, extracts `delta.content`, `usage`, `finish_reason`; malformed frames counted as `skipped_frames`.
* **Instrumentation** — `TTFT` = first text-bearing chunk minus send (role-only deltas ignored); `tok/s = out_tok / (now - first_text)`. `completion_tokens`/`prompt_tokens` from final `usage` when present, else `counted_deltas` + `estimate_tokens(user_chars / 3.5)` with label `(est)` `src/mlx_tui/history/tokens.py`. Every turn → `TurnRecord(ts, model, prompt_tok, out_tok, ttft_s, tok_s, ctx_len, cold, cancelled)` `src/mlx_tui/history/store.py:51` via `HistoryStore` (`dict[str, deque(maxlen=64)]`).
* **Context** — `trim_for_context()` `src/mlx_tui/history/tokens.py:24` keeps newest user-bound window fitting 8000 est tokens; `ctx_len` is `sum(estimate_tokens(trimmed))`; system message (`ChatPane._system_prompt` from `config.system` or presets) prepended when present.
* **Streaming UI** — throttled `on_flush` every 0.1 s `src/mlx_tui/chat.py:20` → `call_from_thread(_update_stream)` into `Static`; on completion `complete_turn_ui()` `src/mlx_tui/chat_pane/turn.py:167` writes dim stamp + notices and swaps raw stream for one `rich.markdown.Markdown(full_text)` renderable into `RichLog` (not per-chunk — avoids Textual perf trap).
* **Cancellation** — `esc` → `ChatPane.abort()` `src/mlx_tui/chat_pane/turn.py:146` (`workers.cancel_group("chat")` + `socket.shutdown(SHUT_RDWR)` on macOS + `response.close()`); records `cancelled=True` `TurnRecord`.
* **Errors** — `httpx.StreamClosed/ReadError/RemoteProtocolError` (cancel-aware), `ConnectError/TimeoutException` → `server unreachable`, `HTTPStatusError` → `server error (HTTP N)` via `error_detail()`.

## Search & download

`/` on Models tab pushes `SearchScreen(ModalScreen)` `src/mlx_tui/search_screen/__init__.py:36` with `Input(#search-input)`, `Static(#search-status)`, `ResultsTable(DataTable)` `height:12`, `Static(#dl-progress)`.

* **Search** — `Input.Submitted` → `@work(group="hf-search", thread=True) _run_search` `src/mlx_tui/search_screen/query.py` → `list_results()` `src/mlx_tui/search.py:36` (`HfApi().list_models(author="mlx-community", search=q, limit=50)`).
* **Exact size** — highlight row → `@work(group="hf-size") _fetch_size` → `repo_files_with_sizes()` (`model_info(files_metadata=True)`) → `filtered_download_size()` via `filter_repo_objects(allow_patterns=ALLOW_PATTERNS)` where `ALLOW_PATTERNS = ["*.safetensors","*.json","tokenizer*"]` → glyph `fits_disk(size, free_disk_bytes())` (`True→✓` `False→⚠` `None→—`, probed via `HF_HUB_CACHE` nearest ancestor). Cache `dict[repo_id,int]` dedupes.
* **Download** — `enter` → `start_download()` `src/mlx_tui/search_screen/download.py` → `@work(group="hf-download") _run_download` → `download_snapshot()` `src/mlx_tui/search.py:138` (`snapshot_download` `allow_patterns=ALLOW_PATTERNS`, `tqdm_class=_throttled_tqdm(on_progress, cancel_event)`) throttled to 0.5 s, raises `CancelledDownload` when `threading.Event` set. `on_progress` hops via `call_from_thread(_progress_line)`. Success → `log_app` + `_rescan_models()` + `dismiss()`; failure → red `#dl-progress`, modal stays open; cancel via `esc`.

## Config file

`~/.config/mlx-tui/config.toml` (honours `$XDG_CONFIG_HOME`) `src/mlx_tui/config.py:28`. All keys optional; CLI `--host/--port` win only when explicitly passed `src/mlx_tui/app/__init__.py:246`.

Parsing is strict `type(value) is expected` (`bool` never coerces into `int` `port`), unknown keys ignored, `temperature`/`top_p` accept `int→float` but clamp `temp 0…2` `top_p 0…1` `max_tokens 1…16384`. Missing file or `ConfigParseError` degrades to `AppConfig()` defaults. `write_template()` `src/mlx_tui/config.py:47` creates commented stub. `ctrl+g` (`action_edit_config` `src/mlx_tui/app/config_edit.py:20`) suspends TUI and runs `shlex.split($EDITOR or "vi")`; on success `parse_config()` reloads and `ChatPane.apply_config_params()` syncs; on `ConfigParseError` previous config kept.

Host/port changes log `restart mlx-tui to apply host/port` — no hot rebind.

## Params

Chat tab `Collapsible(title="Params", collapsed=True, id="params-collapsible")` `src/mlx_tui/chat_pane/__init__.py:42` with three `Input`s (`#param-temp`, `#param-top-p`, `#param-max-tokens`). `_parse_params()` `src/mlx_tui/chat_pane/params.py:26` reads `value.strip()`, parses `float`/`int`, falls back to defaults on `ValueError`/`NoMatches`, clamps. `@on(Input.Submitted, ...)` rewrites clamped strings. On every `_run_turn` `src/mlx_tui/chat_pane/turn.py:26` parsed values sync to `app.config` snapshot via `call_from_thread(setattr)` (no file write) and are merged into the payload as `temperature/top_p/max_tokens`.

## Presets

Flat sibling `~/.config/mlx-tui/presets.toml` (`presets_path() = config_path().parent / "presets.toml"`) `src/mlx_tui/presets.py:22`, no management UI.

`Preset(frozen name, system="", temperature/top_p/max_tokens=None)` `src/mlx_tui/presets.py:13`. `parse_presets()` `src/mlx_tui/presets.py:88` expects `[[preset]]`, exact `type` checks with `int→float` coercion, clamps; `load_presets()` degrades to `[]` on `PresetParseError`. `ctrl+n` forward / `ctrl+o` back (`BINDINGS` `src/mlx_tui/app/__init__.py:30`). `presets_ctrl.apply_preset()` `src/mlx_tui/app/presets_ctrl.py` sets `ChatPane._system_prompt` and inputs for present fields only, logs `preset: <name>`. `ctrl+g` reloads and resets `preset_idx = -1`.

## Metrics tab

Third `TabPane("Metrics")` `src/mlx_tui/app/__init__.py:121` → `MetricsPane` `src/mlx_tui/metrics_pane.py:45` (`Static(#metrics-sparkline)` + `Static(#metrics-memory-sparkline)` `height:3` + `DataTable(#metrics-table)`).

* **Table** — last 64 records of `HistoryStore.all_records()` sorted by `ts` (flat over all per-model rings). Columns `time`, `model`, `tok/s` (1 dp), `TTFT` (2 dp), `ctx`, `prompt`, `out`; `out` suffix `· cold`/`· cancelled` dim.
* **Chat sparkline** — `render_sparkline()` `src/mlx_tui/history/sparkline.py:88` braille `U+2800` over `32×2` dot rows, `sparkline_visible()` filters `cold`+`cancelled` and caps `width*2`, buckets `tok/s` into levels, packs 2×4 dots per char. Per-column shading via `_shade_for_ctx()` `src/mlx_tui/history/sparkline.py:152` quartiles `dim/"" /bold`. Empty → dim `no history yet`.
* **Memory sparkline** — `render_memory_sparkline()` `src/mlx_tui/history/sparkline.py:115` buckets `rss_gib` (`None` gaps → blank), flat series nudged to avoid div0. Legend via latest non-`None` `rss`.

`ModelsPane` swap success and `ChatPane` turn recording both hop via `app._refresh_metrics()` `src/mlx_tui/app/__init__.py:141` (`NoMatches`-guarded); tab activation repaints too.

## Markdown

Kept out of the hot path. Chat streams as raw `Static` at ~10 Hz; on completion `complete_turn_ui()` writes one `rich.markdown.Markdown(full_text)` renderable into `RichLog` scrollback `src/mlx_tui/chat_pane/turn.py:167` (one renderable per turn, not per chunk).

## Project structure

```
src/mlx_tui/
  app/               # MlxTuiApp coordinator — polling, state, status bar, swap/cold-start, presets, config edit
    __init__.py      # BINDINGS, DEFAULT_CSS, compose (status bar + TabbedContent + log), on_mount, CLI main()
    polling.py       # poll_tick / classify_liveness_for
    state.py         # _SwapShim + effective_model / set_tracked_model
    status_bar.py    # render_status → format_status_line
    swap_ctrl.py     # set_swap_ui / cold_start / restart_config_model
    presets_ctrl.py  # apply_preset / cycle_preset
    config_edit.py   # edit_config (suspend + $EDITOR + reload)
  chat_pane/         # Chat tab
    __init__.py      # ChatPane compose, Input.Submitted dispatch, @work stubs
    params.py        # _parse_params / apply_params_to_inputs / on_param_submitted
    turn.py          # run_turn_impl / abort / update_stream / complete_turn_ui
  models_pane/       # Models tab
    __init__.py      # ModelsPane compose, @work stubs, row_size
    table_ops.py     # _rescan_impl / populate / refresh_markers
    swap_ops.py      # request_load_swap / boot_plan_for / run_warm_swap_impl / run_boot_impl
    delete.py        # request_delete_model / _run_delete_impl
  search_screen/     # / modal
    __init__.py      # ResultsTable + SearchScreen compose, BINDINGS
    query.py         # on_search_submitted / run_search_impl / _fetch_size
    download.py      # start_download / run_download_impl / progress/finish / cancel
  history/           # pure domain, stdlib-only except Text/Rich usage in sparkline
    store.py         # _Ring[T], MemoryRecord/MemoryStore, TurnRecord/HistoryStore
    sparkline.py     # braille helpers, render_sparkline/render_memory_sparkline, _shade_for_ctx
    tokens.py        # _tok_int / estimate_tokens / trim_for_context
  metrics_pane.py    # MetricsPane — DataTable + two sparklines
  table.py           # ModelsTable — set_rows/refresh_markers + BINDINGS (enter/d/slash)
  search.py          # HfApi seam, filtered_download_size, free/fits_disk, throttled tqdm, download_snapshot
  chat.py            # stream_turn + TurnResult (SSE loop, token accounting)
  sse.py             # iter_sse_data / delta_content / usage / finish_reason helpers
  config.py          # AppConfig, config_path, parse/load_config, write_template
  presets.py         # Preset, presets_path, parse/load_presets
  status.py          # classify_liveness, ColdTracker, format_status_line
  swap.py            # SwapState + BootPlan + health_timeout
  serverctl.py       # build_start_command, run/spawn_command, warm_load, wait_healthy
  process.py         # find_server_pid, model_from_cmdline, memory_snapshot
  confirm.py         # delete ConfirmScreen modal
```

`pyproject.toml:14` `packages = ["src/mlx_tui"]` (hatchling auto-discovers subpackages).

## Testing / CI

Same gates as CI `/.github/workflows/ci.yml:16`:

```bash
uv run ruff check .
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -q   # 231 tests, ~35 s (Textual via AppHarness)
```

`pyproject.toml:50` `asyncio_mode = auto`; integration tests `monkeypatch` hub seams **at the usage site** (`mlx_tui.search_screen.*`, `mlx_tui.models_pane.table_ops`, `mlx_tui.app.state/polling`/`mlx_tui.process` for `find_server_pid`) so no test touches the network. `uv build --wheel && tar tzf dist/*.whl | grep mlx_tui/app` confirms subpackages are included. Test harness `tests/conftest.py:179` `AppHarness` with `StubServer` modes `ok/probe/error500/truncated/length_cap/empty/slow`.
