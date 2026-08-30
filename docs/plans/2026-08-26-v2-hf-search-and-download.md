# MLX TUI v2 — HF search + in-app download

**Date:** 2026-08-26
**Work Item:** n/a (implements §v2 of `docs/idea.md`)
**Status:** Complete

## Overview
Build v2 on top of the completed v0/v1/pane-split codebase: a `/`-opened modal over the Models tab ("a search box and a download key") that searches `mlx-community` via `HfApi().list_models`, lists repo id / quant / exact post-`allow_patterns` download size vs free disk, and downloads the selected repo through `snapshot_download(allow_patterns=…)` in a cancellable thread worker with throttled byte progress — handing off on completion to the existing rescan so the new model is one `enter` away from loading. Feature set is held exactly to the idea doc's §v2 enumeration; nothing more.

## Current State
Baseline verified 2026-08-26: `uv run pytest -q` → **183 passed**; `uv run ruff check .` clean; `uv run ruff format --check src tests main.py` clean; `uv run pyrefly check` → 0 errors. Every phase must keep all four green.

- **Wiring points this plan touches:**
  - `src/mlx_tui/table.py:28-31` — `ModelsTable.BINDINGS` currently `[("enter", …), ("d", …)]`; the still-unbound `/` belongs here (v1 explicitly deferred it: "`/` key stays unbound"). `action_load_swap`/`action_delete_model` (table.py:73-83) establish the lazy-import-with-`# noqa: PLC0415` pattern for breaking `table ↔ models_pane` cycles.
  - `src/mlx_tui/models_pane.py:50-57` — `ModelsPane.rescan()` is the v2 completion-handoff target (`@work(group="rescan", thread=True)` → `_populate`). `request_delete_model` (models_pane.py:244-258) shows the house `push_screen(modal, callback)` flow.
  - `src/mlx_tui/app.py:39-44` — app-level `BINDINGS` (`ctrl+q`/`escape`/`ctrl+s`/`ctrl+g`); untouched by v2 (the `/` binding is table-scoped). `log_app(message, style)` (app.py:282-284) writes styled `Text` lines to `RichLog(#app-log)`.
  - `src/mlx_tui/confirm.py` — the modal precedent: `ModalScreen[bool]`, centered `DEFAULT_CSS` box, key `BINDINGS`, `dismiss()`.
  - `src/mlx_tui/models.py:33-36` — `quant_label(repo_id)` reused verbatim for result rows. `models.py` imports nothing from UI modules.
  - `src/mlx_tui/chat_pane.py` / `models_pane.py` — established pane↔app contract: typed `tui` property (`cast("MlxTuiApp", self.app)`), `@work(exclusive=True, group=…, thread=True)` workers, every widget write from worker threads via `self.app.call_from_thread` (widget lacks `call_from_thread` on textual 8.2.8), `NoMatches` guards around `query_one` in teardown paths.
- **Tests:** `tests/conftest.py:178-212` — `AppHarness` (`.app`, `.pilot`, `.wait_for`, `.app_log_lines()`), `harness` fixture mounting against an ephemeral stub server (irrelevant to v2 but present). Unit-test conventions: hub objects built directly (tests/unit/test_models.py `_file/_revision/_repo` helpers) and module attributes monkeypatched **at their usage site** (test_models.py:175 patches `mlx_tui.models.scan_cache_dir`). `get_cell_at(Coordinate(row, col))` is the established cell-read (table.py:66-71).
- **Dependencies:** `huggingface_hub>=1.28` already in `[project].dependencies` (pyproject.toml:9) — installed version **1.28.0**, introspected below. **No dependency changes in v2.**

### Verified runtime facts backing this plan (performed 2026-08-26 against installed packages)

1. **No bulk sizes:** `HfApi().list_models(author=…, search=…, limit=…)` returns `ModelInfo` with `.id`; `expand=["used_storage"]` → HTTP 400 (not in the API's allowed expand set, error message enumerated live). Exact download size therefore requires one `api.model_info(repo_id, files_metadata=True)` per repo — verified live: `mlx-community/Qwen3-1.7B-4bit` → siblings carry `.rfilename`/`.size` (`.size` `None` on non-metadata responses → treat as 0), filtered sum 982 MB.
2. **Pattern filtering:** `snapshot_download(allow_patterns=…)` filters via `huggingface_hub.utils.filter_repo_objects` (module `huggingface_hub.utils._paths`); for non-string items it requires `key=` callable — verified `filter_repo_objects(items=[{…}], allow_patterns=["*.safetensors", "*.json", "tokenizer*"], key=lambda f: f["path"])` keeps `"sub/w.safetensors"` (fnmatch `*` crosses `/`) and drops `README.md`/`*.gguf`.
3. **Progress + cancellation seam:** `snapshot_download(tqdm_class=…)` builds two aggregate bars via `_create_progress_bar(cls=tqdm_class, desc=…, total=0, initial=0, unit="B", unit_scale=True, bar_format=…, name=…)` — descs `"Downloading bytes"` (xet network transfer) and `"Reconstructing (incomplete total…)"` (`_snapshot_download.py:316-340`). Per-file `_AggregatedTqdm` grows both parents' `.total` toward the full expected bytes and routes every byte delta through their `.update()`; the xet reporter's `_update_transfer_bar(bar, inc)` likewise ends in `bar.update(inc)` (`utils/_xet_progress_reporting.py:24-35`). **`http_get` retries only `(httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)`** — any other exception raised from `update()` propagates out of `snapshot_download`, and a shared `threading.Event` makes every bar instance raise at its next chunk. Empirically demonstrated with a subclass constructed exactly like hub constructs bars (`disable=True` silences tty output).
4. **Double-count hazard:** xet reports network-transfer and on-disk-reconstruction counters separately; summing both overstates progress. Displayed bytes must come from the reconstruction bar only (role-detectable via the bar's `desc` prefix `"Downloading"`).
5. **Resume:** partial downloads resume natively on retry (documented hub behaviour) — relied on silently, no UI.
6. **Cache location:** `huggingface_hub.constants.HF_HUB_CACHE` (default `~/.cache/huggingface/hub`); `shutil.disk_usage` needs an existing path → nearest-existing-ancestor walk verified.
7. **textual 8.2.8:** binding key name `"slash"` exists for `/`; `ModalScreen.set_focus` present; `DataTable` posts `RowHighlighted` on cursor move and `RowSelected` on `enter` (its own `enter → select_cursor` binding; source lines 1036/2593 of the installed widget); `@work(thread=True)` works on Widget descendants (screens included — same mixin `ModelsPane` already uses).

### Decisions locked with the user
1. **Modal over the Models tab** opened by `/` (keeps the idea doc's fixed two-tab shape; reuses the `ConfirmScreen` pattern). Forced consequence accepted: a `ModalScreen` covers `#app-log`, so live download progress shows in a `Static` *inside* the modal and phase/result lines still land in `#app-log` once visible.
2. **Lazy exact size per cursor row** — one cached `model_info(files_metadata=True)` request per highlighted result fills the size + ✓/⚠-vs-free-disk cell; other rows show `—` until visited (bulk sizing verified impossible).
3. **`enter` on a result row downloads**; arrows navigate normally (consistent with the Models tab where `enter` acts on the row).
4. **`esc` cancels an in-flight download** (shared-event → `CancelledDownload` abort; partial blobs remain; hub resumes on the next attempt) and closes the modal.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| New modules `search.py` (pure hub domain) + `search_screen.py` (modal UI), mirroring the `chat.py`/`chat_pane.py` split | grow `models.py`/`models_pane.py`; one combined file | functional-core convention every module since the refactor follows; the pure half unit-tests without mounting; combined file would entangle testable domain logic with UI |
| Import-cycle breaks stay lazy: `table.action_search_hf` lazy-imports `SearchScreen`; `SearchScreen._rescan_models` lazy-imports `ModelsPane` (both `# noqa: PLC0415` with the existing comment style) | top-level imports; adding `App.rescan_models()` | top-level edges would cycle (`table → search_screen → models_pane → table`); an extra App surface method has one caller — YAGNI; mirrors table.py:74-76 precedent exactly |
| `HubApi` structural `Protocol` (list_models/model_info) types the seam | `Any` params; passing real `HfApi` everywhere | pyrefly strict rejects duck-typed stubs in tests; a two-method protocol keeps production signatures honest and lets unit tests pass plain stub classes |
| Size column = exact post-pattern bytes from `model_info(files_metadata=True)`, cached in `dict[repo_id, int]` per modal instance | bulk listing (impossible, verified); `used_storage` (absent); no column | user choice; the number shown is what the downloader will actually fetch, so the ✓/⚠ vs free disk is honest |
| Download trigger = `enter` via a `BINDINGS` entry on the results-table subclass; navigation untouched | literal `↓`-starts-download | user choice; `↓` remains cursor movement, matching Models-tab semantics |
| Cancellation = shared `threading.Event` checked at the top of `ProgressTqdm.update()`, raising `CancelledDownload` | `worker.cancel()` alone (cooperative — cannot interrupt a blocked socket read, the v0 lesson) | verified: hub's retry whitelist lets the exception propagate; the shared event kills every concurrent file thread within one chunk; resume makes the cost of abort ≈ zero |
| Byte accounting role-split by bar `desc`: only the reconstruction bar feeds the displayed counter; the `"Downloading bytes"` xet-transfer bar is ignored for display (still cancel-checked) | summing both parents | xet reports network and reconstruction separately — summed they exceed the true downloaded size; reconstruction equals bytes-on-disk on both CDN and xet paths |
| Throttle inside the accumulator (`DownloadProgress.record`, ≥0.5 s between forwarded snapshots) plus a final flush in `close()` | forwarding every `update()` (chunk-frequency `call_from_thread` storm); no progress line | mirrors the chat pane's ~10 Hz throttle decision; `close()` guarantees the completion numbers paint even when the last chunk lands inside the throttle window |
| Handoff = `_rescan_models()` + auto-dismiss on success; no auto-load | auto-load the freshly downloaded model; keep modal open | idea doc: "on completion, rescan and refresh the Models table rows — that seam working cleanly *is* the feature"; loading stays one deliberate `enter` away (auto-load could surprise-restart a serving server) |
| Disk pre-flight **warns, never blocks** (`fits_disk` false → yellow line, download proceeds) | hard-block on low disk | idea doc §Status-bar headroom caveat applied to disk: "let an actual load attempt (and its failure) be the ground truth"; doc says "warn when short" |
| Scope pinned by constants `_SEARCH_AUTHOR = "mlx-community"`, `_SEARCH_LIMIT = 50`; results rendered in upstream order (no re-sorting) | wider search; client-side sorting/filtering UI | idea doc: widen later is "a one-line change to plain `search=`"; sorting/filtering are explicitly out of v2 |

## Implementation Phases

### Phase 1: Pure search domain module
`search.py` carries everything that talks to the hub or the filesystem — result listing, exact-size math, disk math, the progress/cancel tqdm machinery, and the download wrapper — fully unit-tested, zero UI changes, app untouched.

**Changes:**
- create `src/mlx_tui/search.py`:
  ```python
  """HF search + snapshot download: a search box and a download key (idea doc §v2)."""

  from __future__ import annotations

  import shutil
  import threading
  import time
  from collections.abc import Callable, Iterable, Iterator
  from dataclasses import dataclass, field
  from pathlib import Path
  from typing import Protocol

  from huggingface_hub import HfApi, snapshot_download
  from huggingface_hub.constants import HF_HUB_CACHE
  from huggingface_hub.hf_api import ModelInfo
  from huggingface_hub.utils import filter_repo_objects
  from huggingface_hub.utils import tqdm as hf_tqdm

  ALLOW_PATTERNS = ["*.safetensors", "*.json", "tokenizer*"]
  _SEARCH_AUTHOR = "mlx-community"
  _SEARCH_LIMIT = 50
  _PROGRESS_MIN_INTERVAL_S = 0.5


  class HubApi(Protocol):
      """The slice of HfApi v2 uses; stubs satisfy this structurally."""

      def list_models(
          self, *, author: str, search: str, limit: int
      ) -> Iterable[ModelInfo]: ...

      def model_info(self, repo_id: str, *, files_metadata: bool) -> ModelInfo: ...


  class CancelledDownload(Exception):
      """Raised inside the hub download loop once the user cancels."""


  def list_results(api: HubApi, query: str) -> list[str]:
      """Repo ids under mlx-community matching query; upstream order preserved."""
      return [
          info.id
          for info in api.list_models(
              author=_SEARCH_AUTHOR, search=query, limit=_SEARCH_LIMIT
          )
      ]


  def filtered_download_size(files: Iterable[tuple[str, int]]) -> int:
      """Sum of sizes whose filenames survive ALLOW_PATTERNS (hub's own matcher)."""
      kept = filter_repo_objects(
          [{"path": name, "size": size} for name, size in files],
          allow_patterns=ALLOW_PATTERNS,
          key=lambda f: f["path"],
      )
      return sum(entry["size"] for entry in kept)


  def repo_files_with_sizes(api: HubApi, repo_id: str) -> list[tuple[str, int]]:
      """(filename, size-bytes) per sibling; one model_info request; missing → 0."""
      info = api.model_info(repo_id, files_metadata=True)
      return [(s.rfilename, s.size or 0) for s in info.siblings or []]


  def free_disk_bytes(cache_dir: Path | None = None) -> int | None:
      """Free bytes on the volume hosting the HF cache; None when unmeasurable."""
      probe = Path(cache_dir) if cache_dir is not None else Path(HF_HUB_CACHE)
      while not probe.exists():
          parent = probe.parent
          if parent == probe:
              return None
          probe = parent
      try:
          return shutil.disk_usage(probe).free
      except OSError:
          return None


  def fits_disk(size_bytes: int, free_bytes: int | None) -> bool | None:
      """Hint only — an actual download attempt is the ground truth (idea doc)."""
      if free_bytes is None:
          return None
      return size_bytes < free_bytes


  @dataclass
  class DownloadProgress:
      """Accumulator shared by every bar instance of one snapshot_download run."""

      cancel_event: threading.Event = field(default_factory=threading.Event)
      on_progress: Callable[[int, int], None] | None = None  # (disk_bytes, expected)
      _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
      _disk_bytes: int = 0
      _expected: int = 0
      _last_flush: float = 0.0

      def record(self, *, disk_delta: int, expected: int) -> tuple[int, int] | None:
          """Fold one update in; a flushable snapshot or None while throttled."""
          now = time.monotonic()
          with self._lock:
              self._disk_bytes += max(disk_delta, 0)
              self._expected = max(self._expected, expected)
              if (
                  self.on_progress is not None
                  and now - self._last_flush >= _PROGRESS_MIN_INTERVAL_S
              ):
                  self._last_flush = now
                  return (self._disk_bytes, self._expected)
          return None

      def totals(self) -> tuple[int, int]:
          with self._lock:
              return (self._disk_bytes, self._expected)


  def make_progress_tqdm(progress: DownloadProgress) -> type[hf_tqdm]:
      """tqdm subclass forwarding throttled totals; raises once cancel_event sets.

      http_get retries only httpx transport errors, so CancelledDownload escaping
      update() aborts the download; the shared event stops every file thread.
      Only the reconstruction bar feeds the display: xet's separate
      "Downloading bytes" network counter would double-report progress.
      """

       class ProgressTqdm(hf_tqdm):
           def update(self, n: float | None = 1) -> bool:
               if progress.cancel_event.is_set():
                   raise CancelledDownload("cancelled by user")
               desc = str(getattr(self, "desc", "") or "")
               # Xet's "Downloading bytes" bar reports the network transfer; it
               # must not feed the display (role-split) and must not emit a
               # spurious (0, network_total) snapshot. The cancel check above
               # still runs for every bar so the abort reaches network threads.
               if desc.startswith("Downloading"):
                   return super().update(n)
               snapshot = progress.record(
                   disk_delta=int(n or 0),
                   expected=int(getattr(self, "total", 0) or 0),
               )
               if progress.on_progress is not None and snapshot is not None:
                   progress.on_progress(*snapshot)
               return super().update(n)

           def close(self) -> None:
               desc = str(getattr(self, "desc", "") or "")
               if (
                   not desc.startswith("Downloading")
                   and progress.on_progress is not None
                   and not progress.cancel_event.is_set()
               ):
                   progress.on_progress(*progress.totals())
               super().close()

      return ProgressTqdm


  def download_snapshot(
      repo_id: str,
      *,
      cache_dir: str | Path | None = None,
      on_progress: Callable[[int, int], None] | None = None,
      cancel_event: threading.Event | None = None,
  ) -> None:
      """Download repo_id's MLX-relevant files only; resume of partials is native."""
      progress = DownloadProgress()
      if cancel_event is not None:
          progress.cancel_event = cancel_event
      progress.on_progress = on_progress
      snapshot_download(
          repo_id,
          allow_patterns=ALLOW_PATTERNS,
          tqdm_class=make_progress_tqdm(progress),
          cache_dir=cache_dir,
      )
  ```
- create `tests/unit/test_search.py`:
  - A module-level `_StubApi` class implementing `HubApi` structurally — `list_models(**kwargs)` records kwargs and returns preset `ModelInfo(id=…)` instances; `model_info(repo_id, files_metadata=True)` returns a stub whose `.siblings` are `SimpleNamespace(rfilename=…, size=…)` objects.
  - `test_list_results_maps_ids_and_pins_scope`: stub returning two infos → ids in order; captured kwargs exactly `{"author": "mlx-community", "search": <q>, "limit": 50}`.
  - `test_filtered_download_size_matches_downloader`: `[("model.safetensors", 100), ("README.md", 50), ("tokenizer.json", 10), ("config.json", 5), ("weights.gguf", 999)]` → `115`; nested `("sub/w.safetensors", 7)` included (fnmatch crosses `/`) → `122`.
  - `test_repo_files_with_sizes_maps_siblings`: siblings sized `100`, `None`, `50` → `[(name, 100), (name, 0), (name, 50)]`.
  - `test_free_disk_bytes_existing_and_missing_leaf(tmp_path)`: existing dir → positive `int`; `tmp_path/"no"/"pe"` walks up to an existing ancestor → positive `int`.
  - `test_fits_disk_parametrized`: `(100, 200)` → True; `(200, 100)` → False; `(100, None)` → None.
  - `test_progress_forwards_throttled_snapshots`: collector list; bar built exactly like hub builds them — `T(desc="Reconstructing (incomplete total...)", total=0, initial=0, unit="B", unit_scale=True, bar_format="{l_bar}{bar}| {n_fmt:>5}B / {total_fmt:>5}B", name="x", disable=True)`; set `bar.total = 1000` (as `_AggregatedTqdm` does); `update(400)` → one snapshot `(400, 1000)` (first call flushes immediately: `_last_flush` starts 0.0); set `progress._last_flush = time.monotonic()` then `update(100)` → suppressed, no new snapshot; reset `progress._last_flush = 0.0`, `update(100)` → `(500, 1000)`.
  - `test_progress_ignores_xet_network_bar`: second bar `desc="Downloading bytes"` (bar_format `"{desc}: {bar}| {n_fmt:>5}B{postfix:>12}"`), `total=982`, `update(982)` → disk total unchanged; the reconstruction bar's `update(500)` still counts.
  - `test_progress_negative_delta_never_reduces_display`: `update(300)` then `update(-50)` (the hub resume path emits negatives) → totals `(300, …)`, no exception.
  - `test_cancel_raises_and_is_shared_across_bars`: `progress.cancel_event.set()` → `pytest.raises(CancelledDownload)` around both the existing bar's and a freshly constructed bar's `update(1)` (same `make_progress_tqdm(progress)` class).
  - `test_close_flushes_final_totals`: updates inside the throttle window, then `bar.close()` → collector receives `(disk, expected)` exactly once more.
  - `test_download_snapshot_pins_patterns_and_plumbs_event(monkeypatch)`: `monkeypatch.setattr(search_mod, "snapshot_download", recorder)` capturing kwargs; call `download_snapshot("org/m", on_progress=f, cancel_event=ev)`; assert `allow_patterns is ALLOW_PATTERNS`, `cache_dir is None`, and the captured `tqdm_class` is a class whose instance (constructed with the hub kwargs above) raises `CancelledDownload` on `update` once `ev.set()` and does not before.

**Success Criteria:**

#### Automated Verification:
- [ ] new unit tests pass: `uv run pytest -v tests/unit/test_search.py`
- [ ] whole suite green: `uv run pytest -q`
- [ ] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [ ] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] live smoke (network): `uv run python -c "from huggingface_hub import HfApi; from mlx_tui.search import list_results; print(list_results(HfApi(), 'Qwen3')[:5])"` prints five `mlx-community/…` ids

### Phase 2: Search modal — open, query, browse
The `/` key opens a modal that searches and browses results with lazily-fetched exact sizes. No download wiring yet — this phase leaves a working checkpoint.

**Changes:**
- create `src/mlx_tui/search_screen.py`:
  ```python
  """Search modal: find mlx-community models, download one, hand off to Models."""


  class ResultsTable(DataTable[str]):
      """Result rows; gains its enter/download binding in Phase 3."""


  class SearchScreen(ModalScreen[None]):
      BINDINGS = [("escape", "close_screen", "Close")]

      DEFAULT_CSS = """
      SearchScreen {
          align: center middle;
          #search-box {
              width: 90%;
              max-height: 80%;
              padding: 1 2;
              border: solid $primary;
              background: $surface;
              #search-input { width: 100%; }
              #search-results { height: 12; }
              Static { height: auto; }
          }
      }
      """

      def __init__(self) -> None:
          super().__init__()
          self._repo_ids: list[str] = []
          self._sizes: dict[str, int] = {}  # repo_id -> exact download bytes
          self._downloading: str | None = None  # consumed by Phase 3
          self._cancel_event: threading.Event | None = None  # consumed by Phase 3

      @property
      def tui(self) -> MlxTuiApp:
          return cast("MlxTuiApp", self.app)

      @override
      def compose(self) -> ComposeResult:
          with Vertical(id="search-box"):
              yield Input(placeholder="search mlx-community…", id="search-input")
              yield Static("", id="search-status")
              yield ResultsTable(id="search-results", cursor_type="row")
              yield Static("", id="dl-progress")

      @override
      def on_mount(self) -> None:
          table = self.query_one("#search-results", ResultsTable)
          for column_key in ("model", "quant", "download"):
              table.add_column(column_key, key=column_key)
          self.query_one("#search-input", Input).focus()
      ```
      plus these members (exact bodies specified; every worker-side widget write hops via `self.app.call_from_thread`, and every `query_one` in a path that can run after dismissal gets the house `except NoMatches: return` guard):
      - `def _status(self, message: str, style: str | None = None) -> None` and `def _dl_line(self, message: str, style: str | None = None) -> None` — write `Text(message)` / `Text(message, style=style)` into `#search-status` / `#dl-progress` respectively (guarded `NoMatches`).
      - `@on(Input.Submitted, "#search-input") def _on_search_submitted(self, event: Input.Submitted) -> None` — `query = event.value.strip()`; empty → dim `type a search` via `_status`; else start `_run_search(query)`, `_status("searching…", "dim")`.
      - `@work(exclusive=True, group="hf-search", thread=True) def _run_search(self, query: str) -> None` — `ids = list_results(HfApi(), query)`; `self.app.call_from_thread(self._populate, ids)`.
      - `def _populate(self, ids: list[str]) -> None` — `NoMatches`-guarded table lookup; store `self._repo_ids = ids`; `table.clear()`; per id `table.add_row(rid, quant_label(rid), "—", key=rid)`; empty → `_status("no results", "yellow")` and leave input focused; non-empty → `_status(f"{len(ids)} results", "dim")` and `table.focus()`.
      - `@on(DataTable.RowHighlighted, "#search-results") def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None` — `repo_id = event.row_key.value`; skip when falsy, already in `self._sizes`, or `self._downloading is not None` (Phase 3 consumes the last clause); else start `_fetch_size(repo_id)`. (Duplicate fetches from rapid events are idempotent and accepted; the dict check on the UI thread is the dedupe.)
      - `@work(exclusive=True, group="hf-size", thread=True) def _fetch_size(self, repo_id: str) -> None` — `size = filtered_download_size(repo_files_with_sizes(HfApi(), repo_id))`; on exception, return silently leaving the cell `—` (the next highlight retries); on success `self._sizes[repo_id] = size`, compute `glyph = {True: "✓", False: "⚠", None: "—"}[fits_disk(size, free_disk_bytes())]`, then `call_from_thread(self._fill_size_cell, repo_id, size, glyph)`.
      - `def _fill_size_cell(self, repo_id: str, size: int, glyph: str) -> None` — `NoMatches`-guarded; `table.update_cell(repo_id, "download", f"{size / 2**30:.1f} GB {glyph}")`.
      - `def action_close_screen(self) -> None` — `self.dismiss(None)` (Phase 3 prepends the cancel-event set).
  - Imports for the module: `threading`; `typing` `TYPE_CHECKING, Any(unused→omit), cast, override`; `rich.text.Text`; `textual` `on, work`; `ComposeResult`; `Vertical`; `NoMatches`; `ModalScreen`; `DataTable, Input, Static`; `from mlx_tui.models import quant_label`; `from mlx_tui.search import CancelledDownload, download_snapshot, filtered_download_size, fits_disk, free_disk_bytes, list_results, repo_files_with_sizes`; `if TYPE_CHECKING: from mlx_tui.app import MlxTuiApp`. (Unused-until-Phase-3 imports are added in Phase 3, not now.)
- `src/mlx_tui/table.py`:
  - `ModelsTable.BINDINGS` gains `("slash", "search_hf", "Search HF")`.
  - ```python
    def action_search_hf(self) -> None:
        # Local import: search_screen hands off to models_pane lazily, so a
        # module-top import would cycle (same reason as the ModelsPane imports).
        from mlx_tui.search_screen import SearchScreen  # noqa: PLC0415

        self.app.push_screen(SearchScreen())
    ```
- create `tests/integration/test_search_integration.py` (uses the shared `harness` fixture; all hub seams patched **at the usage site** `mlx_tui.search_screen.*` before the modal opens, so no test touches the network):
  - Module constants: `ROW = "mlx-community/stub-test-4bit"`; `SIZE_PAIRS = [("model.safetensors", 2_000_000_000), ("README.md", 10)]`.
  - Local helper `async def open_search(harness, monkeypatch) -> SearchScreen`: installs the patches below, focuses the cache table, presses `/`, waits until `isinstance(harness.app.screen, SearchScreen)`, returns the screen.
    - `monkeypatch.setattr("mlx_tui.search_screen.list_results", lambda api, q: [ROW])`
    - `monkeypatch.setattr("mlx_tui.search_screen.repo_files_with_sizes", lambda api, rid: SIZE_PAIRS)`
    - `monkeypatch.setattr("mlx_tui.search_screen.free_disk_bytes", lambda: 10 * 2**30)` (10 GB free → the glyph is deterministically `✓`, keeping the rendered-cell assertion hermetic)
  - `test_open_query_and_browse(harness, monkeypatch)`: open; `await harness.pilot.press(*"qwen", "enter")`; `wait_for(len(screen._repo_ids) == 1)`; assert `screen.query_one("#search-results", ResultsTable).get_cell_at(Coordinate(0, 1)) == "4bit"` and column 0 cell == `ROW`; `wait_for(ROW in screen._sizes)` (lazy fetch ran); assert `get_cell_at(Coordinate(0, 2)) == "1.9 GB ✓"` (`2_000_000_000 / 2**30 → 1.9`); press `"escape"`; `wait_for(not isinstance(harness.app.screen, SearchScreen))`.
  - `test_empty_query_keeps_table_empty(harness, monkeypatch)`: open; press `"enter"` with empty input; `wait_for` dim `type a search` in `#search-status`; table row count stays 0 (`screen._repo_ids == []`).
  - Note: `Coordinate` imported from `textual.coordinate`; the size cell asserts the *rendered* string so a formatter regression cannot hide.

**Success Criteria:**

#### Automated Verification:
- [ ] new integration tests pass: `uv run pytest -v tests/integration/test_search_integration.py`
- [ ] whole suite green: `uv run pytest -q`
- [ ] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [ ] types clean: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui`: `/` on the Models tab opens the modal with the input focused; searching a real term lists `mlx-community` repos with correct quant labels; arrowing onto a row fills its exact GB + ✓/⚠ vs free disk after a beat; `esc` closes back to the Models tab

### Phase 3: Download flow, cancellation, handoff
`enter` on a result owns the wait: pre-flight warning, throttled byte progress in the modal, esc-cancel mid-flight, and the completion handoff into v1's table.

**Changes:**
- `src/mlx_tui/search_screen.py`:
  - Extend imports with `CancelledDownload, download_snapshot` (both already exported from `mlx_tui.search`).
  - `ResultsTable` gains `BINDINGS = [("enter", "start_download", "Download")]` and `def action_start_download(self) -> None: cast("SearchScreen", self.screen).start_download()`.
  - ```python
    def start_download(self) -> None:
        if self._downloading is not None:
            self._dl_line(f"already downloading {self._downloading}", "yellow")
            return
        table = self.query_one("#search-results", ResultsTable)
        if not self._repo_ids or not 0 <= table.cursor_row < len(self._repo_ids):
            self._dl_line("no result selected", "dim")
            return
        repo_id = self._repo_ids[table.cursor_row]
        size = self._sizes.get(repo_id)
        free = free_disk_bytes()
        if size is not None and fits_disk(size, free) is False:
            # Warn, never block: an actual failure is the ground truth (idea doc).
            self._dl_line(
                f"warning: needs {size / 2**30:.1f} GB,"
                f" only {(free or 0) / 2**30:.1f} GB free — downloading anyway",
                "yellow",
            )
        self._downloading = repo_id
        self._cancel_event = threading.Event()
        self.query_one("#search-input", Input).disabled = True
        table.disabled = True
        self._run_download(repo_id, self._cancel_event)
    ```
    (`size is not None and fits_disk(size, free) is False` guards the warn branch: when the exact size is still unknown, `fits_disk(None, free)` would raise `TypeError` (`None < free`), so the size check must gate the whole branch; an unknown size proceeds silently as intended.)
  - `@work(exclusive=True, group="hf-download", thread=True) def _run_download(self, repo_id: str, cancel_event: threading.Event) -> None`:
    - `on_progress(done, expected)` → `self.app.call_from_thread(self._progress_line, repo_id, done, expected)`.
    - `try: download_snapshot(repo_id, on_progress=on_progress, cancel_event=cancel_event)`
    - `except CancelledDownload:` → `self.tui.log_app(f"download cancelled: {repo_id}", "yellow")` then `call_from_thread(self._finish_aborted, repo_id)`; return.
    - `except Exception as exc:` → `detail = f"{exc.__class__.__name__}: {exc}"[:200]`; `call_from_thread(self.tui.log_app, f"download failed: {detail}", "red")`; `call_from_thread(self._finish_error, detail)`; return.
    - success falls through → `call_from_thread(self.tui.log_app, f"✓ downloaded {repo_id}")`; `call_from_thread(self._finish_success)`.
  - `def _progress_line(self, repo_id: str, done: int, expected: int) -> None` — `NoMatches`-guarded; `pct = f" ({done * 100 // expected}%)" if expected else ""`; `#dl-progress` ← `f"downloading {repo_id}… {done / 2**30:.1f}/{expected / 2**30:.1f} GB{pct}"`.
  - `def _finish_success(self) -> None` — `self._downloading = None`; `self._cancel_event = None`; `self._rescan_models()`; `self.dismiss(None)` (widgets die with the screen; no reset needed).
  - `def _finish_error(self, detail: str) -> None` — `self._downloading = None`; `self._cancel_event = None`; `try: self._reset_widgets() except NoMatches: return`; `_dl_line(f"download failed: {detail}", "red")` — modal stays open so the user can retry (`enter` again) or leave (`esc`).
  - `def _finish_aborted(self, repo_id: str) -> None` — resets the two state attrs; the requesting `escape` usually already dismissed the screen, so wrap every widget touch in the `NoMatches` guard: try `_reset_widgets()` and clearing `#dl-progress`, returning quietly on `NoMatches`.
  - `def _reset_widgets(self) -> None` — re-enables `#search-input` and `#search-results` (raises `NoMatches` naturally when dismissed; callers guard).
  - `def _rescan_models(self) -> None` — the handoff:
    ```python
    # Local import: models_pane imports table which lazy-imports this module.
    from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

    try:
        self.tui.query_one(ModelsPane).rescan()
    except NoMatches:
        return
    ```
  - `action_close_screen` becomes:
    ```python
    def action_close_screen(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()  # the worker aborts at its next chunk
        self.dismiss(None)
    ```
    (Post-dismissal worker callbacks are all `NoMatches`-guarded; `log_app` targets the app, not the screen, and stays valid.)
- `tests/integration/test_search_integration.py` — four more tests sharing a `blocked_download` helper: a fake `download_snapshot(repo_id, *, on_progress=None, cancel_event=None, cache_dir=None)` recording its kwargs and, when `blocking=True`, looping `while not cancel_event.is_set(): time.sleep(0.01)`:
  - `test_enter_downloads_hands_off_and_dismisses(harness, monkeypatch)`: patches as in Phase 2 plus a non-blocking recording downloader; spy the handoff — `calls: list[int] = []`, `monkeypatch.setattr(ModelsPane, "rescan", lambda self: calls.append(1))`; open, search, `await harness.pilot.press("enter")`; `wait_for("✓ downloaded mlx-community/stub-test-4bit" in harness.app_log_lines())`; assert recorded kwargs `repo_id == ROW` and `allow_patterns is ALLOW_PATTERNS`; assert `calls == [1]`; `wait_for(not isinstance(harness.app.screen, SearchScreen))`.
  - `test_escape_mid_download_cancels(harness, monkeypatch)`: blocking downloader; open, search, enter; `wait_for(screen._downloading == ROW)`; capture `ev = screen._cancel_event`; press `"escape"`; `wait_for(not isinstance(harness.app.screen, SearchScreen))`; `wait_for(ev.is_set())`; `wait_for("download cancelled" in harness.app_log_lines())`.
  - `test_download_failure_keeps_modal_usable(harness, monkeypatch)`: downloader raises `RuntimeError("disk full")`; open, search, enter; `wait_for("download failed: RuntimeError: disk full" in harness.app_log_lines())`; assert screen still current; assert `#search-input` and `#search-results` re-enabled (`disabled is False`); assert red-styled failure line present in `#dl-progress` (`strip.style` contains `"red"` when reading `query_one("#dl-progress", Static).renderable` — assert on the rendered text containing `"download failed"` instead if styles prove awkward).
  - `test_low_disk_warns_then_proceeds(harness, monkeypatch)`: additionally patch `"mlx_tui.search_screen.free_disk_bytes"` → `lambda: 1_000_000_000` (less than the 2 GB size); open, search, enter; `wait_for` the yellow `warning: needs 1.9 GB` line in `#dl-progress` **and** the eventual `✓ downloaded` log line (warn did not block).
- `tests/unit/test_search.py` — no changes (domain behaviour already covered).

**Success Criteria:**

#### Automated Verification:
- [ ] new integration tests pass: `uv run pytest -v tests/integration/test_search_integration.py`
- [ ] whole suite green: `uv run pytest -q`
- [ ] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [ ] types clean: `uv run pyrefly check`

#### Manual Verification (Apple Silicon + real server, per v0/v1 setup):
- [ ] end-to-end done-condition: pick a small real repo (e.g. `mlx-community/Qwen3-1.7B-4bit`, ~0.98 GB filtered — verified) → `/` → search → `enter` → progress counts up in the modal → modal closes, `✓ downloaded` lands in `#app-log`, the row appears in the Models table → `enter` loads it against the running server. No second terminal at any point.
- [ ] start a multi-GB download, `esc` mid-flight → modal closes, `download cancelled` logged; repeat the same download → hub resumes from partials (bytes continue from ≈ where it stopped).

### Phase 4: README + final gates
Documentation catch-up and the full-gate finale; no behavioral edits.

**Changes:**
- `README.md` — extend the Models-tab keys list with `/`; add a short "Search & download" section stating: `/` opens search over `mlx-community` (limit 50); arrows navigate, `enter` downloads the selected repo (exact size vs free disk appears as you browse; a short-disk warning never blocks); `esc` closes the modal and cancels an in-flight download (partials resume next attempt); downloads pull only `*.safetensors`, `*.json`, `tokenizer*`; completed downloads rescan the Models table automatically.
- Final sweep: confirm no dead imports survived Phases 2–3 (ruff flags them anyway); run the whole gate matrix.

**Success Criteria:**

#### Automated Verification:
- [ ] whole suite green: `uv run pytest -q`
- [ ] lint clean: `uv run ruff check . && uv run ruff format --check src tests main.py`
- [ ] types clean: `uv run pyrefly check`
- [ ] entry intact: `uv run mlx-tui --help` prints usage

#### Manual Verification:
- [ ] full v2 matrix against a live server: search → size glyphs → download with progress → handoff rescan → load → chat stamp shows the new model's cold turn; README claims match observed behaviour

## Out of Scope
- Everything the idea doc excludes for v2: filters, sorting controls, model cards, README previews, favourites, download queues/multi-select, a standalone Search tab, widening past `author=mlx-community`.
- Auto-loading a freshly downloaded model (load stays a deliberate `enter`).
- Background/non-modal downloads; progress that survives closing the modal (closing cancels — chosen semantics).
- Config-file additions (nothing new to configure); host/port-independent endpoints.
- Persistence of search history or size caches beyond a modal instance.
- v3 work (history sparkline) and all other idea-doc out-of-scope items (multi-backend, quantization runner, server-log tailing, conversation persistence, agentic harness).

## Risks & Mitigations
- **Coupling to hub internals** (bar `desc` prefixes for role-splitting, `update()`-raise cancellation, `_AggregatedTqdm` total growth) → all pinned by unit tests asserting the exact construction kwargs and descs verified on hub 1.28.0; a hub bump that shifts behaviour fails those tests loudly rather than corrupting numbers silently.
- **pyrefly strict friction on the tqdm subclass** (upstream `update` is loosely typed) → keep the override's annotations narrow and local; if a signature clash persists, adapt the override's annotation rather than weakening the checker config (house rule from prior plans).
- **Workers outliving the dismissed screen** (esc-cancel path) → every post-dismissal widget access is `NoMatches`-guarded by construction; `log_app` targets the app and stays valid; the shared event stops the hub loop within one chunk, so the orphan thread is short-lived.
- **`Input` swallowing `escape`** while the search input is focused → textual's `Input` binds no `escape` action on 8.2.8 (bindings bubble to the screen); verify at implementation — fallback is an explicit `("escape", …)` binding on the input delegating to the screen action.
- **Rapid `RowHighlighted` storms** issuing duplicate size fetches → dedupe via the `_sizes` dict checked on the UI thread; duplicates are idempotent single-request calls, and `exclusive=True` collapses queued workers.
- **Xet double-counting progress** → role-split accounting (display = reconstruction bytes only), covered by a dedicated unit test using the real bar descs.
- **Scope discipline** — the implementation will land near ~250 lines across the two new modules versus the idea doc's "~80" figure, which was written before the modal/lazy-size/cancel decisions existed; the *feature* line is held exactly to the §v2 enumeration (user-approved trade-off recorded here).
- **Network dependence in manual checks only** → every automated test stubs the hub at its usage site; CI never sees the network.
