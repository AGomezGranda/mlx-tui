# MLX TUI v1 — Models tab, hybrid swap, config file

**Date:** 2026-08-26
**Work Item:** n/a (implements §v1 of `docs/idea.md`)
**Status:** Complete

## Overview
Build v1 on top of the completed v0: a Models tab (`DataTable` over `scan_cache_dir()` with size / quant / fits-in-headroom ✓⚠ / loaded-now columns, `enter`=load/swap, `d`=delete behind a confirm modal), a hybrid swap mechanism that owns the wait (warm probe-load when the server is green, `stop_cmd`/`start_cmd` restart with streamed output otherwise, cold-start from the red dot), and the arrival of `~/.config/mlx-tui/config.toml` edited in place via `e` → `$EDITOR`. The UI adopts the idea doc's shape: two tabs above a shared log pane under the always-visible status bar.

## Current State
Baseline verified 2026-08-26: `uv run pytest -q` → **78 passed**; `uv run ruff check .` clean; `uv run pyrefly check` clean (strict over `src`+`tests`); CI (`.github/workflows/ci.yml`) additionally runs `uv run ruff format --check src tests main.py` — every phase must keep all four green.

- **Modules (all UI-free except `app.py`):**
  - `src/mlx_tui/app.py` — Textual shell. Widgets: `Static(id="status-bar")` docked top (app.py:75), `Static(id="chat-stream")`, `RichLog(id="chat-log", markup=False, wrap=True)`, `Input(id="chat-input")` inside a `Vertical` (app.py:76-79). Layout CSS in `src/mlx_tui/app.tcss`. Key machinery to reuse: `_poll` (2s interval, overlap guard, app.py:91), `_classify_liveness` delegating to pure `classify_liveness` (app.py:113), `_render_status` (app.py:125), chat turn worker `_run_turn` with error mapping (app.py:153), Esc-cancel with socket-shutdown workaround (`action_cancel_chat`, app.py:224), `_end_turn_ui` re-enables input (app.py:262). Bindings today: `ctrl+q` quit, `escape` cancel_chat (app.py:52-55). `MlxTuiApp.__init__(self, host, port)` (app.py:61).
  - `src/mlx_tui/status.py` — `MemorySnapshot(NamedTuple)` (status.py:8), pure `classify_liveness(status_code, body) -> str` green⇔200+non-empty `data` list (status.py:18), `ColdTracker` (status.py:33), `format_status_line(*, state, model, rss_gib, memory, port) -> str` (status.py:64).
  - `src/mlx_tui/process.py` — `ServerProcessFinder` with validated pid cache (process.py:12), `_matches_server_tokens` suffix matcher (process.py:44), `model_from_cmdline(proc) -> str | None` (process.py:48), `memory_snapshot() -> MemorySnapshot` (process.py:60).
  - `src/mlx_tui/chat.py` — `ChatClient.stream_turn(url, payload, *, user_chars, on_flush, flush_interval) -> TurnResult` (chat.py:51); `TurnResult` frozen dataclass (chat.py:23); `active_response` cross-thread cancel seam (chat.py:49).
  - `src/mlx_tui/sse.py`, `src/mlx_tui/history.py` — pure parsers/accounting/context-trim; untouched by v1.
- **Tests:** `tests/conftest.py` — `StubServer`/`StubHandler` (modes `ok/html/slow/error500/truncated/length_cap/empty`, conftest.py:25), `stub_server_factory` fixture (conftest.py:126), `AppHarness` with `.port`, `.log_lines()`, `.wait_for(predicate)` (conftest.py:144), `harness` fixture constructing `MlxTuiApp(host="127.0.0.1", port=<ephemeral>)` (conftest.py:170). `tests/builders.py` — `SseStreamBuilder`. Suites: `tests/unit/test_{sse,status,process,chat,history}.py` (unit, monkeypatch/MockTransport based — pattern shown in tests/unit/test_process.py:22 FakeProcess) and `tests/integration/test_app_integration.py` (pilot-based, queries the widget IDs listed above).
- **Packaging:** `pyproject.toml` deps (pyproject.toml:7-14): `httpx`, `psutil`, `textual`, dev tools. `[dependency-groups] dev = ["pytest-asyncio>=1.4"]`, `[tool.pytest.ini_options] asyncio_mode = "auto"` (pyproject.toml:44-49). Ruff selects `E4,E7,E9,F,I,PL,UP,TID,ASYNC,DTZ` with `PLR0913` firing at **6+ args** (verified empirically in the v0 refactor plan); `PLR2004` ignored in tests only. pyrefly `preset = "strict"` with `search-path = [".", "src", "tests"]` (pyproject.toml:36-42).
- **`huggingface_hub` is not installed** (verified). Everything below was introspected against **huggingface_hub 1.28.0** via `uv run --with huggingface_hub`:
  - Ships `py.typed`. `scan_cache_dir(cache_dir: str | Path | None = None) -> HFCacheInfo`.
  - `HFCacheInfo`: fields `size_on_disk: int`, `repos: frozenset[CachedRepoInfo]`, `incomplete_files`, `warnings: list[CorruptedCacheException]`; method `delete_revisions(*revisions: str) -> DeleteCacheStrategy`.
  - `CachedRepoInfo` dataclass fields: `repo_id: str`, `repo_type: Literal["model","dataset","space"]`, `repo_path: Path`, `size_on_disk: int`, `nb_files: int`, `revisions: frozenset[CachedRevisionInfo]`, `last_accessed: float`, `last_modified: float`.
  - `CachedRevisionInfo` fields: `commit_hash: str`, `snapshot_path: Path`, `size_on_disk: int`, `files: frozenset[CachedFileInfo]`, `refs`, `last_modified`.
  - `CachedFileInfo` fields: `file_name: str`, `file_path: Path`, `blob_path: Path`, `size_on_disk: int`, …
  - `DeleteCacheStrategy`: fields `expected_freed_size: int`, …; method `.execute()`.
  - **`scan_cache_dir()` raises `CacheNotFound` when the cache directory does not exist** (verified empirically; the class lives at the public `huggingface_hub.errors.CacheNotFound`) — must be caught and mapped to an empty table.
  - All classes are plain dataclasses → constructible directly in unit tests without touching disk.
  - This machine's real cache: 1 repo, `ornith-ai/Ornith-1.5-9B-MLX-4bit`, 5,058,243,519 bytes.
- **Upstream mlx-lm facts carried over from the completed v0 plan research** (`docs/plans/2026-08-25-v0-status-bar-and-chat.md`): the request body accepts an optional `model` field and a differing id triggers a synchronous in-server reload (on-demand/warm load works); `/v1/models` lists cache repos passing `probably_mlx_lm()`, **not** the loaded model — hence restart-path health detection combines liveness-green with the psutil cmdline `--model` check; SSE keepalive frames exist.
- **textual 8.2.8 verified:** `DataTable.add_column/add_row/remove_row/clear/update_cell/coordinate_to_cell_key/cursor_row`, `TabbedContent.active`, `ModalScreen`, and `App.suspend()` (context manager yielding `None`) all present.

### Decisions locked with the user
1. **Hybrid swap** — warm probe-load when the dot is green; restart via `stop_cmd`/`start_cmd` when both are configured; cold-start possible from the red dot.
2. **Cold start included in v1**, bound to `ctrl+s` (global, footer-visible), active only when red and `start_cmd` configured.
3. **Config precedence** — CLI `--host/--port` override `config.toml` when explicitly passed; `config.model` seeds cold-start only; swaps always target the selected row.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Hybrid swap (locked) | restart-only (doc's literal state machine, needs configured cmds); warm-only (zero config, invisible wait) | user choice: warm probe works day-one against a hand-started server; configured users get streamed progress + cold start |
| Effective loaded-model = cmdline `--model` if found, else `self._tracked_model` (app-side, set on TUI-driven swap/cold-start completion, cleared when `stop_cmd` fires) | tracked-only; cmdline-only | cmdline alone misses warm swaps (in-server reload leaves argv untouched); tracked alone lies after externally-driven restarts; the union covers both; external *warm* swaps remain invisible — accepted, strictly better than v0's zero visibility |
| MLX-ish filter: `repo_type=="model"` AND snapshot files contain `config.json` AND `tokenizer_config.json` AND at least one `*.safetensors` | repo-id substring heuristics (`mlx-community`/`-MLX`); upstream-exact (`probably_mlx_lm()` incl. `model.safetensors.index.json`) | file-presence check costs no IO (names are already in `CachedRevisionInfo.files`); dropping the `.index.json` requirement keeps single-shard models visible; mirrors upstream's own loadability signal; isolated in one function, widen later per idea doc |
| Fits-in-headroom: ✓ iff `size_on_disk * 1.2 <= available_bytes`; ⚠ otherwise; `—` when available unknown; weights-only | hard-block loads below threshold | idea doc: margin ≈ 20% of size, hint not gate; actual load attempt is ground truth |
| New modules mirror the established functional-core split: `config.py` (pure TOML), `models.py` (pure cache→rows + delete exec), `swap.py` (pure state machine + timeout math), `serverctl.py` (subprocess/probe/health-wait with injected callbacks) | grow `app.py`; service-class DI container | matches the refactor-plan architecture the codebase already follows; every new module unit-testable without UI, seams via callables like `ChatClient.on_flush` |
| `serverctl.run_command` executes `start_cmd`/`stop_cmd` with `shell=True` | `shlex.split` + argv | the doc defines these as free-form shell strings; config is user-owned local trust domain (same as a Makefile/alias); combined stdout+stderr streamed line-by-line |
| Start-command model injection: `{model}` placeholder substituted if present; else if `--model` already in the command, run verbatim; else append `--model <quoted-id>` | always append | placeholder gives explicit control; verbatim-with-flag avoids duplicated/conflicting args; plain commands just work |
| Swap health wait: poll `GET /v1/models` every 1s via sync httpx classifying with existing pure `classify_liveness`, plus injected `current_model()` callable; deadline `60s + 10s/GiB` from `size_on_disk` | fixed timeout; async polling inside event loop | reuses the exact green classifier; injected callable keeps it UI-free and testable; scaled timeout per idea doc |
| Shared log pane = new `RichLog(id="app-log")` docked bottom; lifecycle/process/delete/config lines land there; chat stamps/errors stay in `#chat-log` inside the Chat tab | route everything through `#chat-log` | idea doc shape: "one shared log pane"; keeps the instrument pane uncluttered |
| `ctrl+s` for cold start (locked) | focusable status-bar dot + enter | global binding is discoverable in the Textual footer regardless of focus; the doc's "keystroke on the red dot" intent is preserved by the hint appended to the bar when armed |
| Config live-reload applies `start_cmd/stop_cmd/pidfile/model` immediately (read from `self.config` at use time); `host/port` changes log a "restart mlx-tui to apply" notice | rebuild AsyncClient hot | five strings don't justify client rebinding; honest notice beats silent ignore |
| `pidfile` support folded into `ServerProcessFinder.find(pidfile=None)` as an optional kwarg tried before cache/scan | separate lookup in app | existing unit tests keep compiling unchanged; one discovery path, validated against server-token suffixes before trusting |
| Delete runs `info.delete_revisions(*all_revision_commit_hashes).execute()` in a thread worker; `expected_freed_size` reported to the log pane | per-revision deletes | a repo row represents all its revisions; freed-size line makes the effect visible |
| Rows sorted by `size_on_disk` descending, tie-broken by `repo_id` | alphabetical | eviction thinking puts big models first |

## Implementation Phases

### Phase 1: Config foundation
The five-string config file exists, loads safely, and CLI flags win when explicitly passed.

**Changes:**
- create `src/mlx_tui/config.py`:
  ```python
  """Loading of the single TOML config file (~/.config/mlx-tui/config.toml)."""

  @dataclass(frozen=True)
  class AppConfig:
      model: str | None = None
      host: str = "127.0.0.1"
      port: int = 8080
      start_cmd: str | None = None
      stop_cmd: str | None = None
      pidfile: str | None = None

  def config_path() -> pathlib.Path:
      # $XDG_CONFIG_HOME if set else Path.home()/".config", joined with "mlx-tui/config.toml"

  _KEY_TYPES: dict[str, type] = {
      "model": str, "host": str, "port": int,
      "start_cmd": str, "stop_cmd": str, "pidfile": str,
  }

  def load_config(path: pathlib.Path | None = None) -> AppConfig:
      # path defaults to config_path(); missing file -> AppConfig()
      # tomllib.load; for each known key present in the file, accept the value
      # iff type(value) is _KEY_TYPES[key] — an exact-type check, so the
      # Optional fields (default None) accept their strings and a TOML bool
      # can never sneak into port (isinstance(True, int) is True; type(True)
      # is bool); rejected/absent keys degrade to defaults; unknown keys
      # ignored; TOML parse error -> AppConfig() (never crash the TUI)
  ```
- `src/mlx_tui/app.py`:
  - `MlxTuiApp.__init__(self, host: str = "127.0.0.1", port: int = 8080, config: AppConfig | None = None)` — store `self.config = config or AppConfig()`; existing positional/keyword call sites (conftest harness, `main()`) stay valid.
  - `main()` — argparse flags become `default=None` sentinels; resolution order: CLI value → `cfg.<field>` → hardcoded default (`"127.0.0.1"` / `8080`); construct `MlxTuiApp(host=resolved_host, port=resolved_port, config=cfg)`.
- create `tests/unit/test_config.py` (tmp_path based):
  - missing file → all defaults; `model`/`start_cmd`/`stop_cmd`/`pidfile` round-trip as strings; `host`/`port` round-trip;
  - `port = "not-a-number"` → degrades to default `8080`; unknown extra key ignored; malformed TOML (`"<<<"` bytes) → defaults, no raise;
  - `config_path()` honours `XDG_CONFIG_HOME` via monkeypatch and falls back to `~/.config` when unset.
- `tests/integration` — no changes needed this phase (harness kwargs unaffected).

**Success Criteria:**

#### Automated Verification:
- [x] new unit tests pass: `uv run pytest -v tests/unit/test_config.py`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [x] `uv run mlx-tui --help` shows `--host/--port` (now optional-sentinel based)
- [ ] with a temp config setting `port`, `uv run mlx-tui` connects to that port; `uv run mlx-tui --port 9999` overrides it

### Phase 2: Tabbed shell + shared log pane
Behavior-preserving restructure into the idea doc's shape: two tabs over a shared bottom log pane. All existing widget IDs survive, so the whole suite remains the tripwire.

**Changes:**
- `src/mlx_tui/app.py`:
  - imports: `from textual.containers import Vertical` stays; add `TabbedContent, TabPane` from `textual.widgets`, `RichLog` already imported.
  - `compose()` becomes:
    ```python
    yield Static(f"● :{self.port}", id="status-bar")
    with TabbedContent(initial="models"):
        with TabPane("Models", id="models"):
            yield Static("v1: model table lands in Phase 4", id="models-placeholder")
        with TabPane("Chat", id="chat"):
            with Vertical():
                yield Static("", id="chat-stream")
                yield RichLog(id="chat-log", markup=False, wrap=True)
                yield Input(placeholder="message…", id="chat-input")
    yield RichLog(id="app-log", markup=False, wrap=True)
    ```
    (`RichLog(id="app-log")` is the shared log pane; `initial="models"` points at
    the `TabPane` id — verified on textual 8.2.8 that `active` then reads
    `"models"`/`"chat"` and that an `initial` naming a non-existent tab id
    raises `ValueError` at mount.)
  - no handler changes: `_update_stream`, `_complete_turn_ui`, `_write_system_line` query the same IDs.
  - add private helper `def _log_app(self, message: str, style: str | None = None) -> None` writing a `Text(message, style=style)` line to `#app-log` (used by every later phase).
- replace `src/mlx_tui/app.tcss` content with:
  ```
  #status-bar { dock: top; width: 100%; }
  #app-log { dock: bottom; height: 6; border-top: solid $primary; }
  #chat-log { height: 1fr; }
  ```
- `tests/integration/test_app_integration.py` — no assertion changes expected: verified on textual 8.2.8 that inactive-tab children stay mounted and `query_one`-able, and the tests focus `#chat-input` explicitly. Reserve fallback if something focus-shaped surfaces anyway: activate `"chat"` after mount (`tabbed.active = "chat"`) rather than touching production code.

**Success Criteria:**

#### Automated Verification:
- [x] entire suite unchanged-green: `uv run pytest -q` (78 passed)
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui` opens with Models tab active (placeholder text), Chat tab reachable by click/keys, shared log strip visible at the bottom, status bar on top; a chat turn still streams and stamps correctly; `esc` cancel still works

### Phase 3: Models domain module + hub dependency
`scan_cache_dir()` becomes a typed, pure-mapped row list with quant parsing, headroom hints, and the delete executor — all UI-free and unit-tested without touching the real cache.

**Changes:**
- `pyproject.toml` — add `"huggingface_hub>=1.28"` to `[project].dependencies` (after `psutil`); run `uv sync` to refresh `uv.lock` (CI installs `--locked`, so the lockfile must be committed with this phase).
- create `src/mlx_tui/models.py`:
  ```python
  """Pure mapping from the HF cache to Models-table rows, plus deletion."""

  @dataclass(frozen=True)
  class ModelRow:
      repo_id: str
      size_on_disk: int          # bytes, summed across the repo's revisions by hub
      quant: str                 # parsed label, "—" when unknown
      fits: bool | None          # None when system available-memory unknown
      revision_hashes: tuple[str, ...]

  _QUANT_RE = re.compile(
      r"[-_]([0-9]+(?:\.[0-9]+)?bit|bf16|fp16|f16|f32|int[48])$", re.IGNORECASE
  )  # IGNORECASE so "…-8BIT" matches too; label is lowercased on return

  def quant_label(repo_id: str) -> str:
      # match _QUANT_RE against repo_id.rsplit("/", 1)[-1]; return group(1).lower() or "—"
      # e.g. "ornith-ai/Ornith-1.5-9B-MLX-4bit" -> "4bit"; "Qwen3-1.7B-8bit" -> "8bit"

  def _is_mlx_model(repo: CachedRepoInfo) -> bool:
      # repo.repo_type == "model" and, across any revision's files,
      # {"config.json", "tokenizer_config.json"} ⊆ {f.file_name} and
      # any(f.file_name.endswith(".safetensors"))

  _HEADROOM_MARGIN_RATIO = 0.2

  def fits_headroom(size_on_disk: int, avail_gib: float | None) -> bool | None:
      # None when avail_gib is None; else size_on_disk * (1 + _HEADROOM_MARGIN_RATIO)
      # <= avail_gib * 2**30   (idea doc: ⚠ when size > available − 20%-of-size margin)

  def collect_rows(info: HFCacheInfo, avail_gib: float | None) -> list[ModelRow]:
      # filter(_is_mlx_model, info.repos) -> ModelRow(
      #     repo_id=r.repo_id, size_on_disk=r.size_on_disk,
      #     quant=quant_label(r.repo_id),
      #     fits=fits_headroom(r.size_on_disk, avail_gib),
      #     revision_hashes=tuple(sorted(rev.commit_hash for rev in r.revisions)),
      # ) sorted by (-size_on_disk, repo_id)

  def scan_models(avail_gib: float | None) -> list[ModelRow]:
      # the only IO touchpoint: scan_cache_dir(); catch CacheNotFound -> []

  def delete_repos(revision_hashes: tuple[str, ...]) -> int:
      # info = scan_cache_dir(); strategy = info.delete_revisions(*revision_hashes)
      # freed = strategy.expected_freed_size; strategy.execute(); return freed
      # (raises CacheNotFound if the cache vanished — caller maps to a log line)
  ```
  Imports: `re`, `dataclasses.dataclass`, `from huggingface_hub import scan_cache_dir`, `from huggingface_hub.errors import CacheNotFound` (public home of the class — same object as the private alias), `from huggingface_hub.utils._cache_manager import CachedRepoInfo, HFCacheInfo`.
- create `tests/unit/test_models.py` — build `CachedFileInfo`/`CachedRevisionInfo`/`CachedRepoInfo` dataclasses directly (all fields required, `Path("x")` placeholders fine; a local `_repo(repo_id, size, file_names)` helper keeps it terse):
  - `quant_label` parametrize: `…/Ornith-1.5-9B-MLX-4bit`→`4bit`; `Qwen3-1.7B-8bit`→`8bit`; `foo/model-bf16`→`bf16`; `foo/model-int4`→`int4`; `foo/Llama-3B`→`—`; `foo/model-8BIT`→`8bit` (case-insensitive, lowercased).
  - `_is_mlx_model`: model with config+tokenizer+safetensors → True; missing tokenizer_config → False; dataset repo_type → False; json-only (no safetensors) → False.
  - `fits_headroom`: `(5_000_000_000, 10.0)` → True (≈5.99GiB needed); `(9_000_000_000, 10.0)` → False (margin pushes past); `(100, None)` → None.
  - `collect_rows`: mixed repos filtered to MLX ones, order big→small, `revision_hashes` sorted; empty `repos` → `[]`.
  - `scan_models` raises-path: monkeypatch `mlx_tui.models.scan_cache_dir` to raise `CacheNotFound("gone")` → `[]`.
  - `delete_repos`: monkeypatch `mlx_tui.models.scan_cache_dir` returning a stub with `delete_revisions(*hashes)` recording args and returning strategy stub (`expected_freed_size=123`, `execute()` flag) → returns 123, hashes passed through, execute called once.

**Success Criteria:**

#### Automated Verification:
- [x] dep resolves and lockfile refreshed: `uv sync` exits 0, `git status` shows `uv.lock` updated
- [x] new unit tests pass: `uv run pytest -v tests/unit/test_models.py`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [x] `uv run python -c "from mlx_tui.models import scan_models; print(scan_models(None))"` prints your real cache row(s) (e.g. the Ornith repo at ~5.06 GB)

### Phase 4: Models tab UI
The placeholder becomes a live `DataTable`: rescan in a thread worker, columns name/quant/size/fits/loaded, marker driven by existing process discovery.

**Changes:**
- create `src/mlx_tui/table.py` (widget subclass, thin on purpose):
  ```python
  """Models DataTable with load/delete key bindings."""


  class ModelsTable(DataTable):
      BINDINGS = [
          ("enter", "load_swap", "Load/swap"),
          ("d", "delete_model", "Delete"),
      ]
  ```
  The subclass delegates to App-level hooks: `action_load_swap` calls `self.app.request_load_swap()` and `action_delete_model` calls `self.app.request_delete_model()` (Textual widgets expose `self.app`). Those two public App methods are added in this phase with no-op guard bodies (`self._log_app("models: action not wired yet", "dim")`) and gain real logic in Phases 5–7. (`cursor_row`, `disabled`, `remove_row`/`update_cell` verified present on textual 8.2.8 `DataTable`.)
- `src/mlx_tui/app.py`:
  - imports: `ModelsTable`, `from mlx_tui.models import ModelRow, scan_models`.
  - compose: replace the Models placeholder `Static` with `ModelsTable(id="models-table", cursor_type="row")`.
  - columns set up in `on_mount`: `("model", "quant", "size", "fits", "loaded")` via `add_column(key=…)`.
  - instance state: `self._rows: list[ModelRow] = []` (index-aligned with table rows), `self._tracked_model: str | None = None`, `self._latest_avail_gib: float | None = None`.
  - `@work(exclusive=True, group="rescan", thread=True) def _rescan_models(self) -> None` — `rows = scan_models(self._latest_avail_gib)` then `self.call_from_thread(self._populate_table, rows)`.
  - `def _populate_table(self, rows: list[ModelRow]) -> None` — `table.clear()`; for each row `add_row(str-cell values, key=row.repo_id)` with cells: `repo_id`, `quant`, `f"{size/2**30:.1f} GB"`, fits rendered `"✓"/"⚠"/"—"`, loaded marker `"●"` iff `self._effective_model() == row.repo_id`; store `self._rows = rows`.
  - `def _effective_model(self) -> str | None` — cmdline model via existing finder (`ServerProcessFinder.find()` cached + `model_from_cmdline`), falling back to `self._tracked_model`. (Extracted so `_poll` and the table share one definition.)
  - triggers: call `self._rescan_models()` in `on_mount`; add `@on(TabbedContent.TabActivated)` handler rescanning when `event.tabbed_content.active == "models"` (the event exposes `tabbed_content` — verified constructor signature on 8.2.8; `event.tabbed` would raise `AttributeError`).
  - `def _refresh_markers(self) -> None` — iterates `self._rows`, and for each row `update_cell`s the `loaded` cell (`"●"` iff `self._effective_model() == row.repo_id`) and the `fits` cell (recomputed via `fits_headroom(row.size_on_disk, self._latest_avail_gib)`); only touches cells whose rendered value changed. Extracted so the poll loop, swap workers, and post-delete rescan share one definition.
  - `_poll`: after `memory_snapshot()`, stash `self._latest_avail_gib = snapshot.avail_gib`; after computing `model`, call `self._refresh_markers()`. Public hooks `request_load_swap`/`request_delete_model` this phase: the Phase 4 no-op stub bodies.
  - status bar unchanged this phase.
- `tests/unit/test_models.py` / others — unchanged; interaction coverage arrives in Phase 7's integration tests (nothing observable to assert yet beyond suite-green).

**Success Criteria:**

> Implementation note: a teardown race surfaced while verifying — an in-flight
> rescan worker could deliver `_populate_table` after app shutdown, raising
> `NoMatches("#models-table")` at fixture teardown. Fixed by guarding
> `_populate_table`/`_refresh_markers` with `except NoMatches: return`.

#### Automated Verification:
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [x] `uv run mlx-tui` shows your real cached model(s) with correct size and quant label (Ornith row → `4bit`); switching to Chat and back refreshes; no crash with an empty/absent HF cache (`HF_HUB_CACHE=/tmp/empty uv run mlx-tui` → empty table)

### Phase 5: Delete behind a confirm modal
`d` deletes the selected repo's revisions from the HF cache after an explicit y/n.

**Changes:**
- create `src/mlx_tui/confirm.py`:
  ```python
  """Reusable yes/no modal screen."""


  class ConfirmScreen(ModalScreen[bool]):
      BINDINGS = [
          ("y", "confirm", "yes"),
          ("n", "dismiss_no", "no"),
          ("escape", "dismiss_no", "keep"),
      ]

      def __init__(self, prompt: str) -> None: ...

      # compose: Vertical centered: Static(prompt), Horizontal(Button("delete", id="btn-yes"),
      # Button("keep", id="btn-no"))
      # action_confirm -> self.dismiss(True); action_dismiss_no -> self.dismiss(False)
      # Button @on handlers route to the same dismisses
  ```
- `src/mlx_tui/app.py`:
  - `request_delete_model` gains its body: guards — `self._swap_machine` not IDLE is impossible before Phase 6/7, so guard only on empty `self._rows` or out-of-range `cursor_row` → dim log line; else `row = self._rows[table.cursor_row]`; `self.push_screen(ConfirmScreen(f"delete {row.repo_id} ({size:.1f} GB)? y/n"), self._on_delete_confirmed)` (callback form — `push_screen(screen, callback)`).
  - `def _on_delete_confirmed(self, confirmed: bool | None) -> None` — falsy → dim `kept` line; truthy → start `@work(exclusive=True, group="delete", thread=True) def _run_delete(self, row: ModelRow)`: `freed = delete_repos(row.revision_hashes)` (import from `mlx_tui.models`); `call_from_thread(self._log_app, f"deleted {row.repo_id} — freed {freed/2**30:.1f} GB")`; then `self._rescan_models()`. Exceptions (`CacheNotFound`, `OSError`) → red log line, table left as-is.
  - `escape` on the open modal is bound directly on `ConfirmScreen` (dismissing `False`). Unbound, the key bubbles focused-widget → screen → app and the app-level `escape → cancel_chat` fires behind the modal — the screen-level binding shadows it, so there is no chat-cancel side effect.

**Success Criteria:**

#### Automated Verification:
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] with a throwaway copy of an HF cache (`HF_HUB_CACHE=/tmp/hf-test uv run mlx-tui` after copying a small repo in): `d` shows the modal; `n`/esc keeps; `y` deletes, freed-size line lands in the shared log pane, the row disappears

### Phase 6: Server control + swap core modules
Two new UI-free modules carry everything dangerous about swapping: process commands, warm probe, health wait, and the state machine — fully unit-tested before any wiring.

**Changes:**
- create `src/mlx_tui/swap.py`:
  ```python
  """Swap state machine and health-wait timeout math (idea doc §v1)."""
  class SwapState(Enum):
      IDLE = "idle"; STOPPING = "stopping"; STARTING = "starting"
      WAITING_HEALTH = "waiting-health"; FAILED = "failed"

  _ALLOWED: dict[SwapState, frozenset[SwapState]] = {
      IDLE:           frozenset({STOPPING, STARTING, WAITING_HEALTH}),
      # STOPPING/STARTING = restart path; STARTING alone also serves cold-start;
      # WAITING_HEALTH from IDLE = warm probe path
      STOPPING:       frozenset({STARTING, FAILED}),
      STARTING:       frozenset({WAITING_HEALTH, FAILED}),
      WAITING_HEALTH: frozenset({IDLE, FAILED}),
      FAILED:         frozenset({IDLE}),                        # acknowledged reset
  }

  class InvalidTransition(Exception): ...

  class SwapMachine:
      # self.state: SwapState = SwapState.IDLE
      # def transition(self, new: SwapState) -> None: raise InvalidTransition if new not in _ALLOWED[self.state]
      # @property def busy(self) -> bool: return self.state is not SwapState.IDLE

  def health_timeout(size_on_disk: int, base_s: float = 60.0, per_gib_s: float = 10.0) -> float:
      # base_s + per_gib_s * (size_on_disk / 2**30)   — ~60s base + margin per GB
  ```
- create `src/mlx_tui/serverctl.py`:
  ```python
  """Stop/start commands, warm-load probe, health waiting — no UI knowledge."""
  class ServerController:
      def run_command(self, cmd: str, *, on_line: Callable[[str], None]) -> int:
          # subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT, text=True)
          # for line in proc.stdout: on_line(line.rstrip("\n"))   (skip trailing empty)
          # return proc.wait()

      def warm_load(self, url: str, repo_id: str, *, timeout_s: float) -> None:
          # httpx.Client(timeout=httpx.Timeout(connect=5.0, read=timeout_s, write=5.0, pool=5.0))
          # POST url, json={"model": repo_id,
          #                 "messages": [{"role": "user", "content": "hi"}],
          #                 "max_tokens": 1}
          # response.raise_for_status(); resp.json() must parse and contain
          # non-empty "choices" else RuntimeError("unexpected probe response")
          # httpx exceptions propagate (UI maps them, same contract as ChatClient)

      def wait_healthy(self, url: str, target_model: str | None, *,
                       timeout_s: float,
                       current_model: Callable[[], str | None],
                       on_tick: Callable[[int], None] | None = None) -> bool:
          # deadline = time.monotonic() + timeout_s
          # with httpx.Client(timeout=0.5) as client:   # ONE client, created via
          #   while True:                               # module-attribute lookup —
          #     elapsed = int(timeout_s - remaining)    # NOT top-level httpx.get:
          #     on_tick(elapsed) if on_tick             # that helper binds Client
          #     green = False                           # inside httpx._main, which
          #     try:                                    # the tests' httpx.Client
          #         resp = client.get(url)              # monkeypatch never reaches
          #         body = resp.json()                  # (verified empirically)
          #         green = classify_liveness(resp.status_code, body) == "green"
          #     except (httpx.HTTPError, ValueError): pass
          #     if green and (target_model is None or current_model() == target_model):
          #         return True
          #     if time.monotonic() >= deadline: return False
          #     time.sleep(1.0)
  ```
  Imports: `subprocess`, `time`, `collections.abc.Callable`, `httpx`, `from mlx_tui.status import classify_liveness`.
- create `tests/unit/test_swap.py`:
  - legal edges succeed (`IDLE→WAITING_HEALTH→IDLE`, `IDLE→STOPPING→STARTING→WAITING_HEALTH→IDLE`, `IDLE→STARTING→WAITING_HEALTH→IDLE` (cold-start edge), `…→FAILED→IDLE`);
  - illegal edges raise `InvalidTransition` (`IDLE→FAILED`, `FAILED→FAILED`, `STOPPING→IDLE`, `WAITING_HEALTH→STARTING`) — note `IDLE→STARTING` is *legal* (cold start) and asserted in the legal list above;
  - `busy` true in every non-IDLE state;
  - `health_timeout(0) == 60.0`; `health_timeout(5 * 2**30) == 110.0`; custom base/per-gib respected.
- create `tests/unit/test_serverctl.py`:
  - `run_command` with a string command built as `f"{sys.executable} -c \"print('a'); import sys; print('e', file=sys.stderr)\""`: collects `["a", "e"]` via `on_line` (stderr merged into stdout), returns 0; failing command (`f"{sys.executable} -c \"raise SystemExit(3)\""` or `exit 3` via shell builtin) → returncode 3, no raise;
  - `warm_load` happy path via `install_transport` monkeypatch pattern copied from tests/unit/test_chat.py:23 (`monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=transport))`): 200 JSON `{"choices": [{"message": {"content": "hi"}}]}` → returns None; asserts request JSON carried `"model": repo_id`, `"max_tokens": 1`, no `"stream"` key;
  - `warm_load` 500 → `pytest.raises(httpx.HTTPStatusError)`; 200 non-JSON → `RuntimeError`; connect error → propagates;
  - `wait_healthy`: transport-backed GET sequence green-on-first-poll + `current_model=lambda: "m"` → True quickly (`timeout_s=5`); green-but-wrong-model-then-right (callable flips after 2 calls) → True; always-red → False after `timeout_s=1` (asserts elapsed ≥ ~1s); `target_model=None` → True on first green regardless of callable.

**Success Criteria:**

#### Automated Verification:
- [x] new unit tests pass: `uv run pytest -v tests/unit/test_swap.py tests/unit/test_serverctl.py`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`

> Implementation note: the streamed-output test needed `print('a', flush=True)`
> — piped stdout is block-buffered, so the unbuffered stderr line would
> otherwise overtake it and make the merged order unstable.

#### Manual Verification:
- [ ] none (no behavior wired yet — app untouched apart from imports-free additions)

### Phase 7: Swap orchestration
`enter` on the Models table performs a hybrid load/swap that owns the wait; chat yields politely; the marker never lies.

**Changes:**
- `src/mlx_tui/app.py`:
  - instance state (ctor): `self._swap_machine = SwapMachine()`, `self._server_ctl = ServerController()`, `self._current_model_supplier: Callable[[], str | None] = self._effective_model` (**seam**: integration tests override this attribute; production default derives from cmdline/tracked).
  - compose: the Models pane gains `Static("", id="swap-progress")` above the table (empty → zero height; TCSS `#swap-progress { height: auto; }`) — `_progress_line` writes `waiting for {repo_id}… {s}s` here, and `_set_swap_ui(False)` empties it so no exit path leaves stale progress.
  - `def _build_start_command(self, start_cmd: str, model_id: str | None) -> str` — `"{model}"` in `start_cmd` → replace with `model_id or ""`; `"--model"` in `start_cmd` → verbatim; else `start_cmd` verbatim when `model_id is None`, else `f"{start_cmd} --model {shlex.quote(model_id)}"` (module import `shlex`).
  - `request_load_swap` body replaces the Phase 4 stub:
    1. guards: `self._swap_machine.busy` → `self._log_app("swap already in progress", "yellow")`, return; empty/out-of-range selection → dim line, return; `row = self._rows[self.query_one("#models-table", ModelsTable).cursor_row]`.
    2. dispatch:
       - **green + warm:** `if self.status_state == "green":` transition `IDLE→WAITING_HEALTH`; disable table+input (`_set_swap_ui(True)` helper: `ModelsTable.disabled = True`, `Input.disabled = True`; `_set_swap_ui(False)` reverses both **and** clears `#swap-progress`); `self._log_app(f"loading {row.repo_id} (in-server load)…")`; start `_run_warm_swap(row)`.
       - **commands configured:** `elif self.config.start_cmd and self.config.stop_cmd:` — cancel any in-flight chat first: if the chat worker group has a live turn (`self._chat.active_response is not None`), extract the verbatim body of `action_cancel_chat` into `def _abort_chat(self) -> None` (parameterless — the cancelled line is written by `_run_turn`'s handlers, not by the action); `action_cancel_chat` becomes `self._abort_chat()`. The swap caller then logs its own distinct line: `self._log_app("cancelled — model swapping", "yellow")`. Transition `IDLE→STOPPING`; `_set_swap_ui(True)`; start `_run_restart_swap(row)`.
       - **neither:** red log line `cannot load {id}: server unreachable and no start_cmd configured`.
  - `@work(exclusive=True, group="swap", thread=True) def _run_warm_swap(self, row: ModelRow) -> None`:
    - `timeout = health_timeout(row.size_on_disk)`
    - `try: self._server_ctl.warm_load(f"http://{self.host}:{self.port}/v1/chat/completions", row.repo_id, timeout_s=timeout)`
    - success: `self._tracked_model = row.repo_id`; transition `WAITING_HEALTH→IDLE`; `call_from_thread`: `_log_app(f"✓ {row.repo_id} loaded")`, `_refresh_markers()`, `_set_swap_ui(False)`.
    - `except Exception as exc:` transition `WAITING_HEALTH→FAILED` then `→IDLE`; red line `load failed: {exc.__class__.__name__}: {exc}` (first 200 chars); `_set_swap_ui(False)`. (`InvalidTransition` cannot occur here — states are only touched by this worker.)
  - `@work(exclusive=True, group="swap", thread=True) def _run_restart_swap(self, row: ModelRow) -> None`:
    - `stop_rc = self._server_ctl.run_command(self.config.stop_cmd, on_line=lambda line: self.call_from_thread(self._log_app, f"[swap] {line}"))` — worker threads never touch widgets; every output line hops through `call_from_thread`. Reuse the same lambda for the start command's stream.
    - **marker hygiene before firing stop:** actually clear *before* invoking `run_command`: prior to the stop call, `self._tracked_model = None` and `call_from_thread(self._refresh_markers)` (doc: "the moment stop_cmd fires").
    - `stop_rc != 0` → transition `STOPPING→FAILED→IDLE`, red `[swap] stop_cmd exited {stop_rc}` line, `_set_swap_ui(False)`, return.
    - transition `STOPPING→STARTING`; `start_full = self._build_start_command(self.config.start_cmd, row.repo_id)`; `self.call_from_thread(self._log_app, f"[swap] starting: {start_full}")`; `start_rc = run_command(start_full, on_line=<same lambda>)`; `start_rc != 0` → transition `STARTING→FAILED→IDLE`, red `[swap] start_cmd exited {start_rc}` line, `_set_swap_ui(False)`, return (an instantly-crashing start must not burn the whole health deadline).
    - transition `STARTING→WAITING_HEALTH`; `deadline = health_timeout(row.size_on_disk)`; `ok = self._server_ctl.wait_healthy(f"http://{self.host}:{self.port}/v1/models", row.repo_id, timeout_s=deadline, current_model=self._current_model_supplier, on_tick=lambda s: self.call_from_thread(self._progress_line, s))` where `_progress_line(seconds)` writes `waiting for {repo_id}… {s}s` into `Static(id="swap-progress")`.
    - `ok` → `self._tracked_model = row.repo_id`; `WAITING_HEALTH→IDLE`; `✓ {repo_id} is serving` line + `_refresh_markers()`; else `WAITING_HEALTH→FAILED→IDLE` + red `swap timed out after {int(deadline)}s — check the log pane` line. `_set_swap_ui(False)` in both paths (which also clears `#swap-progress`).
  - `_on_input_submitted`: prepend guard — `if self._swap_machine.busy: self._log_app("model swapping — chat paused", "yellow"); return` (before appending to `self.messages`).
  - `_poll` unchanged (marker refresh flows through `_effective_model`, which reads `_tracked_model`).
- create `tests/integration/test_swap_integration.py` (reuses `harness` fixture + `builders`):
  - `test_warm_swap_happy_path(harness, monkeypatch)`: add stub mode `"probe"` in `tests/conftest.py` `do_POST` — `200 application/json` body `{"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}`; monkeypatch `mlx_tui.app.scan_models` → `[ModelRow("mlx-community/stub-test", 1_000_000_000, "4bit", True, ("abc123",))]`; `await harness.app._poll()` (green); switch tab: `harness.app.query_one(TabbedContent).active = "models"`; populate happens via the rescan worker — `await harness.wait_for(lambda a: len(a._rows) == 1)`; press enter on the focused table (`await harness.pilot.press("enter")`); `wait_for` `✓ mlx-community/stub-test loaded` in app-log lines (helper `app_log_lines()` added to `AppHarness`: `[strip.text for strip in app.query_one("#app-log", RichLog).lines]`); assert `app._swap_machine.state is SwapState.IDLE` and `app._tracked_model == "mlx-community/stub-test"`.
  - `test_swap_blocked_while_busy(harness, monkeypatch)`: same setup; force `harness.app._swap_machine.transition(SwapState.WAITING_HEALTH)`; press enter; `wait_for` `swap already in progress`; state unchanged.
  - `test_restart_swap_streams_and_completes(harness, monkeypatch)`: `harness.server.mode = "html"` then `await harness.app._poll()` — status must be non-green, since dispatch checks green first and would otherwise take the warm branch against an SSE stub; `harness.app.config = AppConfig(start_cmd=f"{sys.executable} -c \"print('booting')\"", stop_cmd=f"{sys.executable} -c \"print('bye')\"")`; `harness.app._current_model_supplier = lambda: row.repo_id` (flips to target immediately); monkeypatched `scan_models` row; enter; `wait_for` `✓ … is serving`; assert log contains `[swap] bye` and `[swap] booting` lines (proves both commands ran and streamed) and final `state is IDLE`.
  - `test_cannot_swap_when_unreachable_and_unconfigured(harness, monkeypatch)`: mode `html` (amber ≠ green, no cmds configured — the neither-branch doesn't inspect color) → enter → `wait_for` `cannot load`.
  - conftest additions: `app_log_lines()` on `AppHarness`; nothing else.

**Success Criteria:**

#### Automated Verification:
- [x] new integration tests pass: `uv run pytest -v tests/integration/test_swap_integration.py`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`

> Implementation notes:
> - The plan's sketch reused the shared `harness` fixture, but a mount-time
>   rescan against the **real** HF cache is a thread worker whose late
>   `_populate_table` overwrites stub rows even after exclusive cancellation
>   (threads can't be killed). The integration module therefore defines a
>   local `stub_harness` fixture that patches `mlx_tui.app.scan_models`
>   *before* app mount, making every rescan deliver identical rows.
> - Setting `TabbedContent.active = "models"` while Models is already active
>   fires no `TabActivated`; `_select_row` calls `_rescan_models()` explicitly.
> - The restart-path test flips the stub back to `"ok"` *after* dispatch so
>   the health wait can succeed without racing the 2s background poll.
> - `_select_row` focuses `#models-table` explicitly; tab activation does not
>   guarantee keyboard focus lands on the table for the `enter` press.
> - `_progress_line(repo_id, seconds)` takes the repo id as an argument
>   (the plan's `_progress_line(seconds)` left its source unspecified).
> - The leftover Phase-4 no-op stub of `request_load_swap` was removed — it
>   was defined later in the class body and shadowed the real implementation.

#### Manual Verification (Apple Silicon + real server, per v0 setup):
- [ ] warm swap: server running model A → `enter` on model B → spinner-phase line, `✓ B loaded`, status bar shows B (via tracked model), RSS climbs on next use
- [ ] restart swap: configure `stop_cmd`/`start_cmd` in config.toml → `enter` on B → `[swap]` stderr lines stream, marker clears at stop, `✓ B is serving`, health wait bounded
- [ ] chat mid-swap: submit a prompt, immediately swap → the yellow `cancelled — model swapping` line lands exactly once (alongside `_run_turn`'s own dim cancelled line), input re-enabled after swap completes

### Phase 8: Cold start, editor, pidfile
Config becomes editable in place; a dead server is one keystroke from booting; pidfile support lands; docs catch up.

**Changes:**
- `src/mlx_tui/process.py` — extend discovery:
  ```python
  def find(self, pidfile: str | None = None) -> int | None:
      # 1. pidfile path: read int (OSError/ValueError -> skip); validate
      #    self._cmdline_matches(pid); hit -> cache & return
      # 2. existing cached-pid validation
      # 3. existing process_iter scan
  ```
  (kwarg-only addition; existing callers and tests compile unchanged.)
- `src/mlx_tui/app.py`:
  - `_poll` / `_effective_model`: pass `self.config.pidfile` into `find()`.
  - `BINDINGS` += `("ctrl+s", "cold_start", "Start server")`, `("e", "edit_config", "Edit config")`.
  - `def action_cold_start(self) -> None` — guards: `self.status_state != "red"` → dim no-op line; `not self.config.start_cmd` → yellow `set start_cmd in the config to enable cold start`; else transition `IDLE→STARTING`, `_set_swap_ui(True)`, start `_run_cold_start` worker.
  - `@work(exclusive=True, group="swap", thread=True) def _run_cold_start(self) -> None` — `cmd = self._build_start_command(self.config.start_cmd, self.config.model)` (streamed like restart); transition `STARTING→WAITING_HEALTH`; `wait_healthy(url, self.config.model, …, current_model=self._current_model_supplier, on_tick=…)`; success → `_tracked_model = self.config.model or cmdline model`, `→IDLE`, `✓ server is up`; failure → `→FAILED→IDLE` + red timeout line; `_set_swap_ui(False)`.
  - status bar hint: in `_render_status`, after building the formatted line, if `self.status_state == "red" and self.config.start_cmd`: append `" · [dim]ctrl+s to start[/]"` to the string before `update()` (formatter itself untouched — `format_status_line` tests unaffected).
  - `def action_edit_config(self) -> None` — `path = config_path()`; `path.parent.mkdir(parents=True, exist_ok=True)`; if missing, write starter template bytes:
    ```toml
    # mlx-tui config — model/host/port/start_cmd/stop_cmd/pidfile
    # model = "mlx-community/Qwen3-1.7B-8bit"
    # start_cmd = "mlx_lm.server --port 8080"
    # stop_cmd = "pkill -f mlx_lm.server"
    # pidfile = "/tmp/mlx-server.pid"
    ```
    then capture `before = (self.config.host, self.config.port)`; `with self.suspend(): subprocess.run([*shlex.split(os.environ.get("EDITOR", "vi")), str(path)])` (imports `subprocess`, `os`, `shlex`; `suspend()` verified on textual 8.2.8); `self.config = load_config(path)`; `_log_app("config reloaded")`; if `(host, port) != before` → yellow `restart mlx-tui to apply host/port`.
- `README.md` — add: config file section (path, six keys, example), Models tab keys (`enter`/`d`, tab switching), `ctrl+s` cold start, `e` edit; keep run instructions.
- `tests/unit/test_process.py` — add pidfile cases using `tmp_path`: valid pidfile with matching cmdline → returned and cached; garbage contents → falls through to scan; matching-less pid (unrelated cmdline) → falls through to scan.
- `tests/integration/test_swap_integration.py` — add `test_cold_start_boots_server(harness, monkeypatch)`: pin liveness red deterministically — monkeypatch `MlxTuiApp._classify_liveness` with `async def always_red(self): return "red"` and run one `await harness.app._poll()` (assigning `status_state = "red"` alone races the 2s poll, which recomputes green against the ok-mode stub); then `harness.app.config = AppConfig(model="mlx-community/stub-test", start_cmd=f'{sys.executable} -c "print(\'starting\')"')`; `harness.app._current_model_supplier = lambda: "mlx-community/stub-test"`; press `ctrl+s`; `wait_for` `✓ server is up` in app-log; assert `[swap]`-style line shows the `--model` flag appended and `state is IDLE`.
- note: `App.suspend()` is never pressed in tests (would detach the pilot terminal); editor flow is manual-verification only.

**Success Criteria:**

#### Automated Verification:
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [x] types clean: `uv run pyrefly check`
- [x] entry intact: `uv run mlx-tui --help` prints usage

> Implementation note: `_run_cold_start` also checks the streamed command's
> exit code before the health wait (`[swap] start_cmd exited {rc}`), mirroring
> the restart path so an instantly-crashing start does not burn the deadline;
> the editor invocation passes `check=False` explicitly (ruff PLW1510).

#### Manual Verification (full v1 matrix, real server + real cache):
- [ ] `e` opens `$EDITOR` on `~/.config/mlx-tui/config.toml` (template created if absent); saving a `start_cmd` and exiting lands `config reloaded` in the log pane; changing `port` produces the restart notice
- [ ] kill the server → red dot within ~2.5s with the `ctrl+s to start` hint; `ctrl+s` boots it, streams startup lines, recovers to green showing the config model
- [ ] `pidfile` set to the server's pid file → status bar RSS/model appear even while another unrelated process churns pids
- [ ] week-trial dry run: swap A→B→A twice, chat between swaps, delete a junk repo — the terminal never leaves the TUI (v1's done-condition from the idea doc)

## Out of Scope
- v2: HF search box + `snapshot_download` (`/` key stays unbound), `allow_patterns`, disk pre-flight.
- v3: history sparkline / metrics ring buffer.
- Markdown rendering, params sidebar, system presets (v0 shortcuts stand).
- Multi-backend support; mlx-vlm-specific endpoints (`/health`, `/unload`).
- Conversation persistence; agentic/tool-calling harness.
- Hot-rebinding `host`/`port` from the editor; mtime watching of the config.
- Quantization runner; custom cache scanning; server log tailing.
- Making external *warm* (in-server) swaps visible — documented limitation of the cmdline+tracked model source.

## Risks & Mitigations
- **Transient double memory during warm swap** (old weights resident while new model pages in) → fits-column margin already warns; restart path is the escape hatch; noted in README wording ("in-server load").
- **`shell=True` on user-configured commands** → config is a user-owned local file (same trust as shell aliases); commands are never written by the app; output is only displayed.
- **pyrefly strict friction on hub/subprocess types** → hub ships `py.typed` (verified); narrow locals (`rc: int`), `cast` only where unavoidable; gates run every phase.
- **Inactive-tab widget visibility** → verified empirically on textual 8.2.8: inactive `TabPane` children stay mounted and `query_one`-able, and the integration tests focus `#chat-input` explicitly; the harness fallback (activate `"chat"` after mount) stays in reserve; suite is the tripwire.
- **`App.suspend()` edge cases in some terminals** → never exercised by the suite; worst case is a manual-use annoyance, not a crash path; `$EDITOR` unset falls back to `vi`.
- **Health-wait false positives** (green `/v1/models` but model still paging) → restart path additionally requires cmdline `==` target (mlx-lm binds the port only after loading); warm path's completion signal is the synchronous probe response itself; residual gap accepted per idea doc's "indeterminate but never silent".
- **`PLR0913` creep on new signatures** → callbacks grouped/injected (`on_tick`, `current_model`), constructors capped well below 6 args.
- **Stub-server drift from real mlx-lm probe responses** → the probe parser accepts OpenAI-shaped JSON generically; the weekly real-server manual matrix (Phase 7/8) is the ground truth.
- **CI runs `uv sync --locked`** → Phase 3 commits the refreshed `uv.lock` together with the dependency bump; never hand-edit the lock.
