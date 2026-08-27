# mlx-tui

One-screen TUI control plane for a local MLX OpenAI-compatible server (`mlx_lm.server` / `mlx_vlm.server`).

Always-visible status bar (liveness, loaded model, RSS, available memory) plus three tabs:

* **Models** — rows from your HF cache (`scan_cache_dir`) with quant label, size on disk, fit headroom and loaded marker; load/swap, delete, and HF search without leaving the terminal
* **Chat** — streaming SSE chat (`POST /v1/chat/completions`) with per-turn TTFT/tok/s instrumentation, cold-vs-warm tagging, context trimming, markdown rendering and param/preset control
* **Metrics** — last 64 turns across all models in a `DataTable` and two braille sparklines (chat tok/s shaded by context depth + RSS/available memory)

A shared log pane (`#app-log`) shows swap/boot output, download progress and notices. The server is spoken to over plain OpenAI-compatible HTTP — no log tailing, no backend abstraction.

## Requirements

* Python 3.13 (`.python-version`) — `uv` manages the toolchain
* [uv](https://docs.astral.sh/uv/) ≥ 0.8
* macOS Apple Silicon for the memory story to be meaningful (unified memory, `psutil` RSS); the chat HTTP layer works against any OpenAI-compatible server
* An MLX server for real use (optional for dev — tests use an ephemeral `httpx` stub)

```
uv tool install mlx-lm
```

## Quick start

Start a server first:

```
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
# or: mlx_lm.server --model ornith-ai/Ornith-1.5-9B-MLX-4bit --port 8080
```

Run the TUI against it:

```
uv sync
uv run mlx-tui --port 8080
# host defaults to 127.0.0.1; explicit CLI flags win over the config file
uv run mlx-tui --host 127.0.0.1 --port 8080
uv run mlx-tui --help
```

No server yet and `start_cmd` configured? A red dot shows `ctrl+s to start` — see [Config file](#config-file).

## Key bindings

| Key | Context | Action |
|-----|---------|--------|
| `enter` | Models — row selected | Load / swap to the selected model |
| `d` | Models | Delete the selected repo from the HF cache (confirm `ModalScreen`) |
| `/` | Models | Search `mlx-community` (limit 50) — see [Search & download](#search--download) |
| `ctrl+s` | Any (red dot + `start_cmd`) | Cold start the server; streams output into the log pane |
| `ctrl+g` | Any | Open `~/.config/mlx-tui/config.toml` in `$EDITOR`, reload on save |
| `ctrl+n` | Any | Cycle presets forward (`presets.toml` `[[preset]]`) |
| `ctrl+o` | Any | Cycle presets back |
| `esc` | Any | Cancel an in-flight chat turn; also closes the search modal / cancels a download |
| `ctrl+q` | Any | Quit |

Tab switching between Models, Chat and Metrics works by click or arrow keys on the `TabbedContent`.

## Status bar

Docked `Static(#status-bar)` at the top — never hidden. Polls `GET /v1/models` every 2 s with a 0.5 s `httpx.AsyncClient` timeout (skips the tick if the previous poll is still in flight).

* **Liveness** — `classify_liveness()` maps the response: `200` + JSON `data` non-empty list → `green`, transport failure/timeout → `red`, every other complete response (502 HTML, junk JSON, wrong shape) → `amber` (yellow dot)
* **Loaded model** — `effective_model()` union: tracked model (warm swap path) wins over `psutil` `model_from_cmdline()` (`--model` argv); gated on a live pid so a down server shows `—` not stale state
* **Memory** — `psutil.Process(pid).memory_info().rss` for the server pid (pidfile fast path → cached pid → `process_iter` scan) plus `psutil.virtual_memory().available/total` via `memory_snapshot()` → `avail X.X/Y.Y GB`. History sampled every poll into `MemoryStore` (ring 256 ≈ 8.5 min)

Format: `● <model> · RSS <rss> GB · avail <avail>/<total> GB · :<port>` with `[dim]ctrl+s to start[/]` appended when red + `start_cmd` is set. Cold-start tracking (`ColdTracker`) arms `· cold` only on a true `green→red→green` cycle, so the first turn against an hours-warm server is never mislabelled.

## Models tab

`ModelsPane` owns `Static(#swap-progress)` + `ModelsTable(DataTable)` `src/mlx_tui/table.py:25`.

Columns: `model` (repo id), `quant` (parsed suffix `-4bit/-8bit/-bf16/fp16/int4` etc via `quant_label()` `src/mlx_tui/models.py:33`, `—` when unknown), `size` (`size_on_disk / 2**30` → `X.Y GB`), `fits` headroom hint, `loaded` marker `●`.

* **Rows** — `scan_models()` `src/mlx_tui/models.py:81` maps `scan_cache_dir()` through `collect_rows()` (MLX heuristic: `config.json` + `tokenizer_config.json` + `*.safetensors`, biggest first). Missing cache → empty table
* **fits** — `fits_headroom()` `src/mlx_tui/models.py:57` with 20% margin: `size * 1.2 ≤ avail * 2**30` → `✓`, else `⚠`, unknown memory → `—` (`fits_cell()` `src/mlx_tui/table.py:20`). Weights-only hint — KV cache growth with context is not counted
* **Delete** — `d` → `ConfirmScreen` modal → `delete_repos()` `src/mlx_tui/models.py:93` `delete_revisions(*hashes).execute()`; bytes freed logged
* **Load / swap** — `enter` → `request_load_swap()` `src/mlx_tui/models_pane/swap_ops.py`:
  - If `start_cmd` **and** `stop_cmd` are configured → restart path: `stop_cmd` (`run_command` streaming output), clear tracked marker immediately, then `spawn_with_grace`/`spawn_command` for `build_start_command(start_cmd, model_id)` (`{model}` placeholder or `--model` detection, else `--model <id>` appended), streams combined stdout/stderr via a daemon thread into the log pane; health-wait `wait_healthy()` `src/mlx_tui/serverctl.py:99` polls `GET /v1/models` until green+model-matched or `health_timeout()` `src/mlx_tui/swap.py:27` expires (`60 s + 10 s/GiB`), `on_tick` drives `#swap-progress`; timeout scales with model size
  - Otherwise on a green server → warm path: synchronous one-token probe `warm_load()` `src/mlx_tui/serverctl.py:78` (`POST /v1/chat/completions` `max_tokens:1`, raises on non-2xx/odd body)
  - Swap is a single `exclusive(group="swap")` worker; double `enter` is blocked and logged `model swapping — chat paused`. Live chat streams are cancelled client-side (`pane.abort()` socket-shutdown on macOS) before `stop_cmd` so they surface `cancelled — model swapping` not a silent error
* **Rescan** — `ModelsPane.rescan()` `@work(thread=True, group="rescan")` re-scans the cache on mount, on tab activation and after a successful download

## Chat tab

`ChatPane` `src/mlx_tui/chat_pane/__init__.py:22` owns a collapsed `Params` `Collapsible` `src/mlx_tui/chat_pane/params.py`, `Static(#chat-stream)`, `RichLog(#chat-log)` and `Input(#chat-input)`.

* **Transport** — `stream_turn()` `src/mlx_tui/chat.py:53` (`httpx.Client` `connect 5 s/read 300 s/write 5 s`): `POST /v1/chat/completions` `stream:true` `stream_options:{include_usage:true}` `model` (if known). Parses SSE via `iter_sse_data()` `src/mlx_tui/sse.py`, extracts `delta.content`, `usage`, `finish_reason`; malformed frames counted as `skipped_frames`
* **Instrumentation** — `TTFT` = first text-bearing chunk minus send (not first byte — role-only deltas ignored); `tok/s = out_tok / (now - first_text)`. `completion_tokens`/`prompt_tokens` from final `usage` when present, else `counted_deltas` + `estimate_tokens(user_chars / 3.5)` with label `(est)`. Every turn → `TurnRecord(ts, model, prompt_tok, out_tok, ttft_s, tok_s, ctx_len, cold, cancelled)` `src/mlx_tui/history/store.py:51` via `HistoryStore` (`dict[str, deque(maxlen=64)]` per model); cold/cancelled flagged
* **Context** — `trim_for_context()` `src/mlx_tui/history/tokens.py:24` keeps the newest user-bound window fitting 8000 est tokens (newest message always kept); `ctx_len` is `sum(estimate_tokens(trimmed))`; on turn start a `system` message (`ChatPane._system_prompt` from `config.system` or presets) is prepended when present (not counted as extra ctx quirk)
* **Streaming UI** — throttled `on_flush` every 0.1 s `src/mlx_tui/chat.py:20` → `call_from_thread(_update_stream)` into `Static`; on completion `complete_turn_ui()` `src/mlx_tui/chat_pane/turn.py:167` writes a dim stamp `"{in} in · {out} out · {tok/s} tok/s · TTFT {s}s · cold?"` plus notices (`malformed frame(s) skipped`, `hit the N-token cap — ask it to continue`, `model returned no text`) and swaps the raw stream for one `rich.markdown.Markdown(full_text)` renderable into `RichLog` (not per-chunk — avoids the Textual perf trap). `you ›` prompts and stamps stay plain `Text`
* **Cancellation** — `esc` → `ChatPane.abort()` `src/mlx_tui/chat_pane/turn.py:146` (`workers.cancel_group("chat")` + `socket.shutdown(SHUT_RDWR)` on macOS to wake blocking `iter_lines`) + `response.close()`; if a turn was cancelled the worker records a `cancelled=True` `TurnRecord` (`prompt_tok/out_tok/ttft/tok_s = 0`) and writes dim `cancelled — request aborted`
* **Errors** — `httpx.StreamClosed/ReadError/RemoteProtocolError` (cancel-aware), `ConnectError/TimeoutException` → red `server unreachable :<port>`, `HTTPStatusError` → red `server error :<port> (HTTP N): <detail>` via `error_detail()` (parses `detail`/`error.message`)

## Search & download

`/` on the Models tab pushes `SearchScreen(ModalScreen)` `src/mlx_tui/search_screen/__init__.py:36`.

Modal box `Vertical(#search-box)`: `Input(#search-input)` (`search mlx-community…`), `Static(#search-status)`, `ResultsTable(DataTable)` `height:12`, `Static(#dl-progress)`. `esc` closes and cancels.

* **Search** — `Input.Submitted` → `@work(group="hf-search", thread=True) _run_search` → `list_results()` `src/mlx_tui/search.py:36` (`HfApi().list_models(author="mlx-community", search=q, limit=50)`). Result rows show `model` / `quant` (`quant_label`) / `download` (`—` until visited). Empty query → dim `type a search`
* **Exact size** — highlighting a row fires `@work(group="hf-size") _fetch_size` → `repo_files_with_sizes()` (`model_info(files_metadata=True)` → `(rfilename,size)` siblings, `size None→0`) → `filtered_download_size()` via `filter_repo_objects(allow_patterns=ALLOW_PATTERNS)` where `ALLOW_PATTERNS = ["*.safetensors","*.json","tokenizer*"]` → glyph `fits_disk(size, free_disk_bytes())` (`True→✓` `False→⚠` `None→—`, probed via `HF_HUB_CACHE` nearest existing ancestor). `size → "X.Y GB ✓"` cell. Cache `dict[repo_id,int]` dedupes; failures leave `—` (retry on next highlight)
* **Download** — `enter` on `ResultsTable` → `start_download()` `src/mlx_tui/search_screen/download.py` checks `already downloading` guard and cursor validity; warns `warning: needs X.Y GB, only A.B GB free — downloading anyway` yellow when `fits_disk is False` but never blocks (ground truth is the attempt). Disables input+table, creates `threading.Event`, fires `@work(group="hf-download") _run_download` → `download_snapshot()` `src/mlx_tui/search.py:138` (`snapshot_download` `allow_patterns=ALLOW_PATTERNS`, `tqdm_class=_throttled_tqdm(on_progress, cancel_event)`) which throttles progress to 0.5 s, routes only the reconstruction bar (`desc` not starting `Downloading`) and raises `CancelledDownload` when the event is set (hub retries only transport errors, so the exception propagates and kills every file thread within one chunk). `on_progress(done,expected)` hops via `call_from_thread(_progress_line)` → `downloading <id>… X.Y/Y.Y GB (N%)`. Partials resume natively on next attempt
* **Handoff** — success → `log_app("✓ downloaded <id>")` → `_rescan_models()` `src/mlx_tui/search_screen/download.py:143` (`ModelsPane.rescan()`) → `dismiss()`; modal dies with its widgets. Failure → `log_app("download failed: …","red")` + red `#dl-progress`, modal stays open so `enter` retries, inputs re-enabled. Cancel via `esc` (`_cancel_event.set()` then `dismiss()`) → `download cancelled: <id>` yellow, orphan callbacks are `NoMatches`-guarded

## Config file

`~/.config/mlx-tui/config.toml` (honours `$XDG_CONFIG_HOME`) `src/mlx_tui/config.py:28`. All keys optional; CLI `--host/--port` win only when explicitly passed `src/mlx_tui/app/__init__.py:246`.

```toml
model = "mlx-community/Qwen3-1.7B-4bit"   # seeds cold start target
host = "127.0.0.1"
port = 8080
start_cmd = "mlx_lm.server --port 8080"   # {model} placeholder or --model appended if missing
stop_cmd = "pkill -f mlx_lm.server"
pidfile = "/tmp/mlx-server.pid"
temperature = 0.7
top_p = 1.0
max_tokens = 1024
system = "You are a helpful assistant."
```

Parsing is strict `type(value) is expected` (`bool` never coerces into `int` `port`), unknown keys ignored, `temperature`/`top_p` accept `int→float` but clamp `temp 0…2` `top_p 0…1` `max_tokens 1…16384`. Missing file or `ConfigParseError` degrades to `AppConfig()` defaults (`host 127.0.0.1` `port 8080`). `write_template()` `src/mlx_tui/config.py:47` creates a commented stub. `ctrl+g` (`action_edit_config` `src/mlx_tui/app/config_edit.py:20`) writes the template if missing, `suspend()`s the TUI and runs `shlex.split($EDITOR or "vi")`; on success `parse_config()` reloads, `ChatPane.apply_config_params()` syncs the Params inputs, `load_presets()` reloaded and `preset_idx` reset; on `ConfigParseError` the previous config is kept and `config kept — parse failed: …` red is logged. Host/port changes log `restart mlx-tui to apply host/port` yellow — no hot rebind.

Swapping semantics: with `start_cmd` **and** `stop_cmd` → restart with streamed output; otherwise warm in-place probe. `start_cmd` may be a long foreground server — its output streams while the TUI stays interactive and the health poll decides when it is up. In-server loads keep both models resident briefly — the `⚠` fits column is the hint for that pressure.

## Params

Chat tab `Collapsible(title="Params", collapsed=True, id="params-collapsible")` `src/mlx_tui/chat_pane/__init__.py:42` with three `Input`s:

* `Temperature (0–2)` `placeholder 0.7` (`#param-temp`, tooltip `Creativity: 0 deterministic → 2 very random`)
* `Top-p (0–1)` `placeholder 1.0` (`#param-top-p`, `Nucleus sampling: lower = more focused`)
* `Max tokens (1–16384)` `placeholder 1024` (`#param-max-tokens`, `Maximum reply length`)

`_parse_params()` `src/mlx_tui/chat_pane/params.py:26` reads `value.strip()`, parses `float`/`int`, falls back to `0.7/1.0/1024` on `ValueError`/`NoMatches`, clamps. `@on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")` rewrites clamped strings back. On every `_run_turn` entry `src/mlx_tui/chat_pane/turn.py:26` parsed values sync to `app.config` snapshot via `call_from_thread(setattr)` (no file write — `ctrl+g` is the file source of truth) and are merged into the payload as `temperature/top_p/max_tokens` alongside the optional `system` message.

## Presets

Flat sibling file `~/.config/mlx-tui/presets.toml` (`presets_path() = config_path().parent / "presets.toml"` — XDG-consistent) `src/mlx_tui/presets.py:22`, no management UI:

```toml
[[preset]]
name = "default"
system = "You are helpful."
temperature = 0.7
top_p = 1.0
max_tokens = 1024

[[preset]]
name = "creative"
system = "You are a creative assistant."
temperature = 1.2
```

`Preset(frozen name, system="", temperature/top_p/max_tokens=None)` `src/mlx_tui/presets.py:13`. `parse_presets()` `src/mlx_tui/presets.py:88` expects `[[preset]]` (`{preset: list[dict]}`), filters `None` entries, exact `type` checks with `int→float` coercion for `temperature/top_p` (`type is int` guard rejects `bool`), clamps; `load_presets()` degrades to `[]` on `PresetParseError`. `PRESETS_TEMPLATE` written on demand.

`ctrl+n` forward / `ctrl+o` back (`BINDINGS` `src/mlx_tui/app/__init__.py:30`, avoids unreliable `ctrl+shift+p`; `ctrl+p` is the Textual command palette). `presets_ctrl.apply_preset()` `src/mlx_tui/app/presets_ctrl.py` sets `ChatPane._system_prompt` and `_apply_params_to_inputs` for present fields only, snapshots `AppConfig`, logs dim `preset: <name>`. No file → yellow `no presets — create …`. `ctrl+g` reloads and resets `preset_idx = -1`.

## Metrics tab

Third `TabPane("Metrics")` `src/mlx_tui/app/__init__.py:121` → `MetricsPane` `src/mlx_tui/metrics_pane.py:45` (`Static(#metrics-sparkline)` + `Static(#metrics-memory-sparkline)` `height:3` + `DataTable(#metrics-table)` `height:1fr`).

* **Table** — last 64 records of `HistoryStore.all_records()` sorted by `ts` (flat over all per-model rings). Columns `time (%H:%M:%S)`, `model` (`—` when none), `tok/s` (`1 dp`), `TTFT` (`2 dp`), `ctx`, `prompt`, `out`; `out` suffix `· cold`/`· cancelled` dim
* **Chat sparkline** — `render_sparkline()` `src/mlx_tui/history/sparkline.py:88` braille `U+2800` over `32×2` (`height_rows*4` dot rows), `sparkline_visible()` filters `cold`+`cancelled` and caps `width*2`, buckets `tok/s` into levels, packs 2×4 dots per char. Legend `"{tok/s} tok/s · ctx {ctx_len} · {n} turns"`, empty → dim `no history yet — chat to build it`. Per-column shading via `_shade_for_ctx()` `src/mlx_tui/history/sparkline.py:152` quartiles `dim/"" /bold` applied as `rich.text.Text` segments (bold wins when a braille column pairs two turns)
* **Memory sparkline** — `render_memory_sparkline()` `src/mlx_tui/history/sparkline.py:115` buckets `rss_gib` (`None` gaps → blank columns), flat series nudged to avoid div0. Legend `"{rss:.1f} GB RSS · avail {avail:.1f}/{total:.1f} · {n} samples"` via latest non-`None` `rss`; empty or all-`None` → dim `no memory samples yet`. Collected every poll into `MemoryStore` `src/mlx_tui/history/store.py:39` (`deque(maxlen=256)` via `_Ring[T]`)

`ModelsPane` swap success and `ChatPane` turn recording both hop via `app._refresh_metrics()` `src/mlx_tui/app/__init__.py:141` (`NoMatches`-guarded); `Metrics` tab activation repaints too.

## Markdown

Kept out of the hot path. Chat streams as raw growing `Static` at ~10 Hz; on completion `complete_turn_ui()` writes one `rich.markdown.Markdown(full_text)` renderable into the `RichLog` scrollback `src/mlx_tui/chat_pane/turn.py:167` (one renderable per assistant turn, not per chunk, so no per-chunk `Markdown` widget trap per `docs/idea.md:181`). `you ›`, stamps and notices remain plain `Text` with `markup=False`.

## Develop locally

### Setup

```bash
git clone <repo>
cd mlx-tui
uv sync --dev          # creates .venv, installs textual httpx psutil huggingface_hub + dev deps
uv run mlx-tui --help  # textual 8.2.8, httpx 0.28+, psutil 7+, huggingface_hub 1.28
```

Python 3.13 is pinned in `.python-version`; `uv` fetches it if missing.

### Run against the test stub or a real server

```bash
uv run mlx-tui --port 8080
uv run mlx-tui --host 127.0.0.1 --port 8080

# real server in another terminal
uv tool install mlx-lm
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
```

During dev the 2 s poll, `Ctrl` bindings and `Esc` cancel can be exercised against the in-tree harness (`tests/conftest.py:179` `AppHarness` with `StubServer` modes `ok/probe/error500/truncated/length_cap/empty/slow`) without a live model.

### Tests / lint / typecheck / format

Same gates as CI `/.github/workflows/ci.yml:16`:

```bash
uv sync --locked --dev
uv run ruff check .                 # E4,E7,E9,F,I,PL,UP,TID,ASYNC,DTZ per pyproject.toml:28
uv run ruff format --check src tests
uv run pyrefly check               # preset=strict, python 3.13
uv run pytest -q                   # 231 tests, ~35 s (integration mounts Textual via AppHarness)
uv run pytest -v tests/unit/test_history.py -k memory
uv run pytest -v tests/integration/test_app_integration.py -k metrics
```

`pyproject.toml:50` `asyncio_mode = auto`; integration tests `monkeypatch` hub seams **at the usage site** (`mlx_tui.search_screen.*`, `mlx_tui.models_pane.table_ops`, `mlx_tui.app.state/polling`/`mlx_tui.process` for `find_server_pid`) so no test touches the network. `uv build --wheel && tar tzf dist/*.whl | grep mlx_tui/app` confirms subpackages are included.

### Project structure

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
  chat.py            # stream_turn + TurnResult (SSE loop, token_accounting)
  sse.py             # iter_sse_data / delta_content / usage / finish_reason helpers
  config.py          # AppConfig, config_path, parse/load_config, write_template
  presets.py         # Preset, presets_path, parse/load_presets
  status.py          # classify_liveness, ColdTracker, format_status_line
  swap.py            # SwapState + BootPlan + health_timeout
  serverctl.py       # build_start_command, run/spawn_command, warm_load, wait_healthy
  process.py         # find_server_pid, model_from_cmdline, memory_snapshot
  confirm.py         # delete ConfirmScreen modal
```

`pyproject.toml:14` `packages = ["src/mlx_tui"]` (hatchling auto-discovers subpackages); entry point `mlx-tui = "mlx_tui.app:main"`.

## Notes & scope

* MLX-only by design (idea doc §Why MLX-only) — one cache layout, one memory model; the chat HTTP layer is accidentally portable to any OpenAI-compatible server on a configurable host/port
* No quantization runner, no multi-backend abstraction, no server-log tailing, no conversation persistence beyond the in-memory rings (shape is frozen for a future JSONL `—since` path, no file IO yet)
* Presets are file + `$EDITOR` only — no management UI
* Braille sparklines are `32×2` with `height:3` containers; on very small terminals `height:1fr` on the table keeps them clipped not crashing

## License

Same as the repo — see `pyproject.toml:2`.
