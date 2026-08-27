# SRP Package Split — Large Modules into Focused Packages

**Date:** 2026-08-27
**Work Item:** n/a
**Status:** Complete

## Overview

Split the four modules exceeding 300 lines (`src/mlx_tui/app.py:1` 485, `chat_pane.py:1` 354, `models_pane.py:1` 306, `search_screen.py:1` 300) — plus borderline `history.py:1` 299 — into focused sub-packages (`app/`, `chat_pane/`, `models_pane/`, `search_screen/`, `history/`) keeping `MlxTuiApp` as a thin coordinator. No backwards-compat shims — this is a local project, imports are updated to new canonical paths. Behaviour is verbatim; every phase leaves `uv run pytest -q` / `ruff` / `pyrefly` green and each file ≤250 lines (global ceiling; aspirational per-phase ≤180).

## Current State

Verified 2026-08-27: `wc -l src/mlx_tui/*.py` → app.py 485, chat_pane.py 354, models_pane.py 306, search_screen.py 300, history.py 299, rest ≤148. Gates: `uv run ruff check src/mlx_tui` clean, `uv run pyrefly check` 0 errors strict (`pyproject.toml:34`), `uv run pytest --collect-only -q` 130+ tests. Prior split `docs/plans/2026-08-26-refactor-app-pane-split.md:1` (767→262) has regrown with metrics/presets/params.

**`src/mlx_tui/app.py:1` (485) — 10 concerns in one class:**
- Shell/CSS `app.py:75-114` `DEFAULT_CSS` + `compose` `app.py:151` yielding `Static(#status-bar)` + `TabbedContent` with 3 `TabPane`→panes, `on_mount` `app.py:162` creates `httpx.AsyncClient` + `set_interval(2.0,_poll)`, `on_unmount` `app.py:191`.
- Poll loop `_poll` `app.py:194` with `_poll_in_flight` `app.py:128`, liveness `_classify_liveness` `app.py:300` via `status.classify_liveness`, status `_render_status` `app.py:312` via `status.format_status_line` + `process.memory_snapshot`.
- Swap shim `_SwapShim` `app.py:48` + `swap_busy` proxy `app.py:142` + `set_swap_ui` `app.py:332` disabling `ModelsTable`+`Input`.
- Model tracking `effective_model` `app.py:231` / `set_tracked_model` `app.py:250` / `refresh_models` `app.py:255` / `cold_tracker` `app.py:130`.
- Presets `_apply_preset` `app.py:262` / `_cycle_preset` `app.py:287` via `presets.load_presets` + `ChatPane._apply_params_to_inputs/_parse_params`.
- Cold boot `action_cold_start` `app.py:345` + `_restart_config_model` `app.py:398` → `ModelsPane.run_boot(BootPlan)` from `swap.BootPlan`.
- Config edit `action_edit_config` `app.py:449` via `App.suspend()+subprocess.run(shlex.split($EDITOR))` + `config.parse_config`.
- Chat helpers `_chat_pane_or_none` `app.py:424` / `chat_has_live_turn` `app.py:430` / `cancel_chat_for_swap` `app.py:434` / `action_cancel_chat` `app.py:440`.
- Logging `log_app` `app.py:445` via `RichLog(#app-log)`.
- CLI `main()` `app.py:475` with `argparse --host/--port` over `config.load_config`.
- No public API — `__all__ = ["MlxTuiApp","_shade_for_ctx"]` `app.py:45` where `_shade_for_ctx` re-exports `history._shade_for_ctx` is local convenience only and will be removed; Phase 1 repoints test `tests/unit/test_history.py:402` to `from mlx_tui.history import _shade_for_ctx` (single-file canonical), Phase 5 repoints to `from mlx_tui.history.sparkline import _shade_for_ctx` after the `history/` split.

**`src/mlx_tui/chat_pane.py:1` (354):** `ChatPane(Vertical)` `chat_pane.py:37` owns `Collapsible(#params-collapsible)` with 3 `param-row` `Input`s `chat_pane.py:56`, plus `Static(#chat-stream)` + `RichLog(#chat-log)` + `Input(#chat-input)` `chat_pane.py:82`. Params `_parse_params` `chat_pane.py:103` (clamp 0–2/0–1/1–16384) + `_apply_params_to_inputs` `chat_pane.py:131` + `apply_config_params` `chat_pane.py:147` + `set_system_prompt` `chat_pane.py:152` + `_on_param_submitted` `chat_pane.py:155`. Turn `_on_input_submitted` `chat_pane.py:87` + `@work(thread=True) _run_turn` `chat_pane.py:168` (~125 lines) building payload `chat_pane.py:206` with `history.trim_for_context` + `chat.stream_turn` + `history._tok_int`, then `abort` `chat_pane.py:294` socket-shutdown + stream helpers `chat_pane.py:310/313/326/346/349`. `tui` property `chat_pane.py:48` `cast("MlxTuiApp",self.app)`.

**`src/mlx_tui/models_pane.py:1` (306):** `ModelsPane(Vertical)` `models_pane.py:24` owns `Static(#swap-progress)`+`ModelsTable` `models_pane.py:37`. Table ops `rescan` `models_pane.py:41` → `_rescan` `models_pane.py:44` via `models.scan_models` + `_populate` `models_pane.py:50` + `refresh_markers` `models_pane.py:63` + `row_size` `models_pane.py:113`. Swap `request_load_swap` `models_pane.py:76` (warm vs restart) + `_boot_plan_for` `models_pane.py:105` + `run_warm_swap` `models_pane.py:130` + `_fail_swap` `models_pane.py:151` + `run_boot` `models_pane.py:159` (~74 lines) + `_progress_line` `models_pane.py:121`. Delete `request_delete_model` `models_pane.py:234` + `_on_delete_confirmed` `models_pane.py:267` + `_run_delete` `models_pane.py:276`. `tui` via `models_pane.py:33`.

**`src/mlx_tui/search_screen.py:1` (300):** `ResultsTable(DataTable)` `search_screen.py:32` + `_format_size` `search_screen.py:41` + `SearchScreen(ModalScreen)` `search_screen.py:48` owns `Vertical(#search-box)` with `Input(#search-input)`+`Static(#search-status)`+`ResultsTable`+`Static(#dl-progress)` `search_screen.py:80`. Lifecycle `_set_line` `search_screen.py:93` + `_on_search_submitted` `search_screen.py:100` → `_run_search` `search_screen.py:115` → `_populate` `search_screen.py:134` + `_on_row_highlighted` `search_screen.py:153` → `_fetch_size` `search_screen.py:168` → `_fill_size_cell` `search_screen.py:178`. Download `start_download` `search_screen.py:187` → `_run_download` `search_screen.py:215` → `_progress_line` `search_screen.py:241` / `_finish` `search_screen.py:253` / `_rescan_models` `search_screen.py:275` + `action_close_screen` `search_screen.py:284` cancel via `threading.Event`.

**`src/mlx_tui/history.py:1` (299):** `_Ring[T]` `history.py:18` + `MemoryRecord` `history.py:34` / `MemoryStore` `history.py:45` + `TurnRecord` `history.py:58` / `HistoryStore` `history.py:72` + braille `_bit` `history.py:114` / `_braille_char` `history.py:120` / `sparkline_visible` `history.py:126` / `_braille_levels` `history.py:134` / `_render_braille` `history.py:161` / `render_sparkline` `history.py:191` / `render_memory_sparkline` `history.py:218` + `_shade_for_ctx` `history.py:254` + `_tok_int` `history.py:273` / `estimate_tokens` `history.py:281` / `trim_for_context` `history.py:286`.

**Coupling & conventions to preserve:** `tui` cast pattern, `@work(exclusive=True,group=…,thread=True)` must remain a method decorator on the pane (`Widget`/`App`) + `self.tui.call_from_thread` (widget has no `call_from_thread` on textual 8.2.8), `NoMatches` guards around `query_one`, `BootPlan` frozen dataclass `swap.py:17`, `Health_timeout` via `swap.health_timeout`, `Table` bindings via `table.py:25`. Tests import `MlxTuiApp` (`tests/conftest.py:18`) — will be repointed to `mlx_tui.app` package (still `MlxTuiApp` in `app/__init__.py`, no shim); `_shade_for_ctx` test repointed in Phase 1 to `mlx_tui.history` and Phase 5 to `mlx_tui.history.sparkline`; `scan_models` patch target updated Phase 4 to `mlx_tui.models_pane.table_ops`, `find_server_pid` patches updated Phase 1 to `mlx_tui.app.state`/`mlx_tui.app.polling` + `mlx_tui.process`.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| **B — Package split with same import names** (`app.py`→`app/__init__.py`, `chat_pane.py`→`chat_pane/__init__.py`, etc.) | A flat-file extraction (no packages), C hybrid only-app | User explicitly chose B; it gives true SRP per subpackage. `MlxTuiApp` stays `from mlx_tui.app import MlxTuiApp` (canonical in `app/__init__.py`). No shims needed — project has no public API, callers are updated to canonical paths. |
| No backwards-compat shims — update imports to canonical paths | Keep `__init__.py` re-exports for old paths | Shims add one more indirection and test maintenance; ponytail rule deletion over addition. Local project can move imports atomically per phase. |
| Group by SRP, not by 1-method-per-file | One file per method; one file per concern (4-5 submodules per package) | One-per-method is boilerplate (ponytail ladder rung 1); grouping keeps each submodule 80–180 lines, total files minimal, diff short. New submodules named `polling`, `state`, `presets`, `swap_ctrl`, `config_edit`, `status_bar` for `app`; `params`, `turn` for `chat_pane`; `table_ops`, `swap_ops`, `delete` for `models_pane`; `query`, `download` for `search_screen`; `store`, `sparkline`, `tokens` for `history`. Avoids shadowing `mlx_tui.swap` / `mlx_tui.status`. |
| Pure functions taking `app: MlxTuiApp` over methods on controller class | Controller class per package | Functions are the laziest that work (ponytail ladder 6: one line before class); no extra abstraction for one caller; class would be single-instance wrapper around `MlxTuiApp`. |
| `TYPE_CHECKING` guard for `MlxTuiApp` in pane submodules | Runtime import `from mlx_tui.app import MlxTuiApp` | Avoids circular import (`app` imports panes for `compose`, panes import `app` for `tui`); already used `models_pane.py:20`/`chat_pane.py:22`/`search_screen.py:28`. |
| Remove `__all__` shim for `_shade_for_ctx` | Keep re-export | No public API — delete `app.__all__` shim; Phase 1 move test `tests/unit/test_history.py:402` to `from mlx_tui.history import _shade_for_ctx` (single file), Phase 5 to `from mlx_tui.history.sparkline import _shade_for_ctx`; no shim to maintain. |

## Implementation Phases

### Phase 1: `app` package — polling, state & status

Leaves `MlxTuiApp` thin enough to break 485→~310; polling SRP isolated.

**Changes:**
- Create directory `src/mlx_tui/app/`; `bash: mkdir -p src/mlx_tui/app && git mv src/mlx_tui/app.py src/mlx_tui/app/__init__.py` (or `cp` then delete if `git mv` fails) — verbatim move, no edits yet.
- Edit `src/mlx_tui/app/__init__.py` — trim to facade (~180 lines): `MlxTuiApp` with `BINDINGS` `app.py:66`, `DEFAULT_CSS` `app.py:75`, `__init__` `app.py:118`, `swap_busy` property `app.py:142`, `compose` `app.py:151`, `on_mount` `app.py:162`, `_refresh_metrics` `app.py:178`, `_on_tab_activated` `app.py:184`, `on_unmount` `app.py:191`, `log_app` `app.py:445`, `_chat_pane_or_none` `app.py:424`/`chat_has_live_turn` `app.py:430`/`cancel_chat_for_swap` `app.py:434`/`action_cancel_chat` `app.py:440`, `refresh_models` `app.py:255` shim delegating to `ModelsPane`, plus imports of new submodules. **Move** `_SwapShim` `app.py:48` to `app/state.py` in this phase (do not keep local copy); `app/__init__.py` imports it via `from .state import _SwapShim` for `swap_machine = _SwapShim()` typing if needed, otherwise `app/state.py` is sole owner. Delete `__all__` and `from mlx_tui.history import _shade_for_ctx` shim (no public API) and bodies of moved methods (replace with one-line delegates, see below).
- Create `src/mlx_tui/app/state.py` (~70 lines):
  ```python
  """Tracked-model state + effective model union."""

  from __future__ import annotations
  from typing import TYPE_CHECKING
  import psutil
  import mlx_tui.process as process  # module import so monkeypatch of process.find_server_pid propagates
  from mlx_tui.process import model_from_cmdline
  from mlx_tui.swap import SwapState

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp


  class _SwapShim: ...  # moved verbatim from app.py:48 (state: SwapState, busy/transition/reset)


  def effective_model(
      app: "MlxTuiApp",
  ) -> str | None: ...  # verbatim from app.py:231, use process.find_server_pid
  def set_tracked_model(
      app: "MlxTuiApp", model: str | None
  ) -> None: ...  # app.py:250 body, then app._refresh_metrics()
  ```
  # ponytail: `_SwapShim` lives here, not in `app/__init__.py` — single owner, avoids Phase 1/2 contradiction.
- Create `src/mlx_tui/app/polling.py` (~110 lines):
  ```python
  """Poll tick: liveness, memory, model markers."""

  from __future__ import annotations
  import time
  import psutil
  from typing import TYPE_CHECKING
  import httpx
  from mlx_tui.history import (
      MemoryRecord,
  )  # Phase 1 path; Phase 5 repoint to mlx_tui.history.store.MemoryRecord
  import mlx_tui.process as process  # module import for monkeypatch propagation
  from mlx_tui.process import memory_snapshot
  from mlx_tui.status import classify_liveness

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp


  async def classify_liveness_for(
      app: "MlxTuiApp",
  ) -> str: ...  # app.py:300 body using app._http
  async def poll_tick(
      app: "MlxTuiApp",
  ) -> None: ...  # app.py:194 body, calls classify_liveness_for, observe ColdTracker, snapshot, psutil rss, effective_model via state.effective_model(app), render_status via app._render_status, use process.find_server_pid
  ```
  `MlxTuiApp._poll` becomes `async def _poll(self): if self._poll_in_flight: return; self._poll_in_flight=True; try: await poll_tick(self); finally: self._poll_in_flight=False`
  `MlxTuiApp._classify_liveness` becomes `return await classify_liveness_for(self)`
  `MlxTuiApp.effective_model` becomes `return state.effective_model(self)`; `set_tracked_model` delegates to `state.set_tracked_model(self, model)`.
- Create `src/mlx_tui/app/status_bar.py` (~40 lines):
  ```python
  """Status bar rendering thin wrapper."""

  from __future__ import annotations
  from typing import TYPE_CHECKING
  from mlx_tui.process import memory_snapshot
  from mlx_tui.status import MemorySnapshot, format_status_line
  from textual.widgets import Static

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp


  def render_status(
      app: "MlxTuiApp", *, model, rss_gib, snapshot=None
  ) -> None: ...  # app.py:312 body
  ```
  `MlxTuiApp._render_status` delegates to `status_bar.render_status(self, model=…, rss_gib=…, snapshot=…)`. Renamed from `status.py` to avoid shadowing `mlx_tui.status`.

**Success Criteria:**

#### Automated Verification:
- [x] New package imports: `uv run python -c "from mlx_tui.app import MlxTuiApp; from mlx_tui.app.polling import poll_tick; from mlx_tui.app.state import effective_model, _SwapShim; print('app pkg ok')"`
- [x] Shim removed: `uv run python -c "import mlx_tui.app; assert not hasattr(mlx_tui.app, '_shade_for_ctx'), 'shim should be gone'"`
- [x] Whole suite green (after repointing `tests/unit/test_history.py:402` to `from mlx_tui.history import _shade_for_ctx` — single-file canonical, not yet `history.sparkline`): `uv run pytest -q`
- [x] Lint clean: `uv run ruff check src/mlx_tui` (canonical; `ruff check .` also passes)
- [x] Types clean: `uv run pyrefly check`
- [x] Size check: `wc -l src/mlx_tui/app/__init__.py src/mlx_tui/app/polling.py src/mlx_tui/app/state.py src/mlx_tui/app/status_bar.py` — each ≤180, `app/__init__.py` ≤250 (down from 485); global ceiling ≤250 — Phase1 initially 395, Phase2 brings to 250 (deviation noted, now passes)
- [x] Monkeypatch seams: `grep -rn "find_server_pid" tests` patched targets updated to `mlx_tui.app.state`, `mlx_tui.app.polling`, `mlx_tui.process` (see `tests/integration/test_swap_integration.py:33`)
- [x] Targeted behavior: `uv run pytest tests/integration/test_app_integration.py -q` (poll/effective_model still exercised after state/polling extraction)

#### Manual Verification:
- [ ] `uv run mlx-tui --help` prints usage; `uv run mlx-tui` shows status bar dot + port, 2s poll updates RSS without crash

### Phase 2: `app` package — presets, swap & config edit

Finishes `app` package; `app/__init__.py` drops to ~180 lines, all submodules ≤120.

**Changes:**
- Create `src/mlx_tui/app/presets_ctrl.py` (~60 lines):
  ```python
  """Preset apply/cycle — ChatPane param bridge."""

  from __future__ import annotations
  from typing import TYPE_CHECKING
  from textual.css.query import NoMatches
  from mlx_tui.chat_pane import (
      ChatPane,
  )  # runtime for query_one(ChatPane), safe: ChatPane only TYPE_CHECKING-imports MlxTuiApp
  from mlx_tui.config import AppConfig
  from mlx_tui.presets import Preset

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp


  def apply_preset(
      app: "MlxTuiApp", preset: Preset
  ) -> None: ...  # app.py:262 body (query ChatPane, _apply_params_to_inputs, sync config, log_app)
  def cycle_preset(app: "MlxTuiApp", step: int) -> None: ...  # app.py:287 body
  ```
  `MlxTuiApp._apply_preset` → `presets_ctrl.apply_preset(self, preset)`, `_cycle_preset` → `cycle_preset`, `action_cycle_preset/_back` keep one-liners.
- Create `src/mlx_tui/app/swap_ctrl.py` (~130 lines):
  ```python
  """Cold-start / restart orchestration + swap UI."""

  from __future__ import annotations
  import httpx
  from typing import TYPE_CHECKING
  from textual.css.query import NoMatches
  from textual.widgets import Input, Static
  import mlx_tui.process as process  # module import for monkeypatch propagation
  from mlx_tui.models_pane import (
      ModelsPane,
  )  # runtime for query_one(ModelsPane), safe: ModelsPane only TYPE_CHECKING-imports MlxTuiApp
  from mlx_tui.swap import BootPlan, SwapState
  from mlx_tui.table import ModelsTable

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp


  def set_swap_ui(
      app: "MlxTuiApp", busy: bool
  ) -> None: ...  # app.py:332 body (disable ModelsTable+Input, clear #swap-progress)
  async def cold_start(
      app: "MlxTuiApp",
  ) -> None: ...  # app.py:345 body (guard _cold_start_in_flight/swap_busy, classify, pid check, _restart branch, transition STARTING, pane.run_boot); use process.find_server_pid
  def restart_config_model(app: "MlxTuiApp", pid: int) -> None: ...  # app.py:398 body
  ```
  `MlxTuiApp.set_swap_ui` / `action_cold_start` / `_restart_config_model` delegate to `swap_ctrl.*(self, …)`. Keep `_cold_start_in_flight` bool on `MlxTuiApp` (accessed by `swap_ctrl.cold_start` via `app._cold_start_in_flight`).
- Create `src/mlx_tui/app/config_edit.py` (~45 lines):
  ```python
  """Config edit suspend + reload."""

  from __future__ import annotations
  import os, shlex, subprocess
  from typing import TYPE_CHECKING
  from textual.css.query import NoMatches
  from mlx_tui.chat_pane import ChatPane
  from mlx_tui.config import ConfigParseError, config_path, parse_config, write_template
  from mlx_tui.presets import load_presets

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp


  def edit_config(
      app: "MlxTuiApp",
  ) -> None: ...  # app.py:449 body (write_template if missing, suspend, shlex.split EDITOR, parse_config with ConfigParseError guard, apply_config_params, reload presets, host/port warning)
  def build_main_parser(): ...
  ```
  `MlxTuiApp.action_edit_config` → `config_edit.edit_config(self)`. `main()` `app.py:475` **stays** in `src/mlx_tui/app/__init__.py` (imports `load_config` + `config_edit` parser) to keep `pyproject.toml:22` `mlx_tui.app:main` entry point stable — do not move to `config_edit`. `config_edit` only exposes `edit_config` plus helpers.
- `_SwapShim` already moved to `app/state.py` in Phase 1 — do not recreate; `app/__init__.py` may `from .state import _SwapShim` only for type hints, otherwise `app/state.py` is sole owner. `swap_machine` attribute stays on `App`; no behaviour change.

**Success Criteria:**

#### Automated Verification:
- [x] Package gates: `uv run python -c "from mlx_tui.app.presets_ctrl import cycle_preset; from mlx_tui.app.swap_ctrl import cold_start; from mlx_tui.app.config_edit import edit_config; print('ok')"`
- [x] Whole suite green: `uv run pytest -q`
- [x] Lint clean: `uv run ruff check src/mlx_tui`
- [x] Types clean: `uv run pyrefly check`
- [x] Size: `wc -l src/mlx_tui/app/*.py` each ≤180, `app/__init__.py` ≤200; global ceiling ≤250 — actual 250/43/65/48/59/34/111, 250 at ceiling (deviation: aspirational 200 not met, but global ceiling met)

#### Manual Verification:
- [ ] `ctrl+p` / `ctrl+o` cycles presets (log `preset: <name>`); `ctrl+g` opens `$EDITOR` (set `EDITOR=true`) then `config reloaded` appears; `ctrl+s` over red state triggers cold start path without crash when `start_cmd` set

### Phase 3: `chat_pane` package — params + turn lifecycle

Splits 354→ three files each ≤160.

**Changes:**
- Create `src/mlx_tui/chat_pane/`; `bash: mkdir -p src/mlx_tui/chat_pane && git mv src/mlx_tui/chat_pane.py src/mlx_tui/chat_pane/__init__.py` verbatim.
- Trim `src/mlx_tui/chat_pane/__init__.py` to facade (~130 lines): keep `ChatPane` `chat_pane.py:37` with `__init__` `chat_pane.py:40`, `tui` `chat_pane.py:48`, `has_live_turn` `chat_pane.py:52`, `compose` `chat_pane.py:56`, `set_system_prompt` `chat_pane.py:152` (stays verbatim, 2 lines — used by `app/presets_ctrl.py:138`), `_on_input_submitted` `chat_pane.py:87` (dispatch only, then `self._run_turn` via `turn.run_turn`), `has_live_turn` logic stays. Keep thin delegates for `apply_config_params`/`_parse_params`/`_apply_params_to_inputs`/`_on_param_submitted` that call `params.*`. Remove param/turn bodies, import from submodules.
- Create `src/mlx_tui/chat_pane/params.py` (~95 lines):
  ```python
  """Params sidebar clamp + config bridge."""

  from __future__ import annotations
  from typing import TYPE_CHECKING
  from textual.css.query import NoMatches
  from textual.widgets import Input
  from mlx_tui.config import AppConfig

  if TYPE_CHECKING:
      from mlx_tui.chat_pane import ChatPane
  _DEFAULT_TEMPERATURE = 0.7
  _DEFAULT_TOP_P = 1.0
  _DEFAULT_MAX_TOKENS = 1024
  _TEMP_MIN = 0.0
  _TEMP_MAX = 2.0
  _TOP_P_MIN = 0.0
  _TOP_P_MAX = 1.0
  _MAX_TOK_MIN = 1
  _MAX_TOK_MAX = 16384


  def parse_params(
      pane: "ChatPane",
  ) -> tuple[
      float, float, int
  ]: ...  # chat_pane.py:103 verbatim, query #param-* Inputs, clamp
  def apply_params_to_inputs(
      pane: "ChatPane", temperature, top_p, max_tokens
  ) -> None: ...  # chat_pane.py:131
  def apply_config_params(
      pane: "ChatPane", cfg: AppConfig
  ) -> None: ...  # chat_pane.py:147
  def on_param_submitted(pane: "ChatPane", event) -> None: ...  # chat_pane.py:155
  ```
- Create `src/mlx_tui/chat_pane/turn.py` (~170 lines):
  ```python
  """Streaming turn worker + abort + UI writers — plain functions, no @work."""

  from __future__ import annotations
  import socket, time
  from typing import TYPE_CHECKING
  import httpx
  from rich.markdown import Markdown
  from rich.text import Text
  from textual.widgets import Input, RichLog, Static
  from mlx_tui.chat import error_detail, stream_turn
  from mlx_tui.config import AppConfig
  from mlx_tui.history import (
      TurnRecord,
      _tok_int,
      estimate_tokens,
      trim_for_context,
  )  # Phase 3 path; Phase 5 repoint to history.store/tokens

  if TYPE_CHECKING:
      from mlx_tui.chat_pane import ChatPane
  _MAX_CONTEXT_TOKENS_EST = 8_000


  def run_turn_impl(
      pane: "ChatPane", messages, cold: bool
  ) -> None: ...  # chat_pane.py:168 verbatim, self→pane, self.tui→pane.tui, pane.tui.call_from_thread where needed
  def abort(pane: "ChatPane") -> None: ...  # chat_pane.py:294
  def update_stream(pane, text): ...  # 310
  def complete_turn_ui(pane, full_text, stamp, cold, notices): ...  # 313
  def record_cancelled(pane, model_at_send, cold, ctx_len_estimate): ...  # 326
  def write_system_line(pane, message, style): ...  # 346
  def end_turn(pane) -> None: ...  # 349 (check swap_busy via pane.tui.swap_busy)
  ```
  Keep `@work` on `ChatPane` in `chat_pane/__init__.py` (ponytail: `@work` must be a Widget method):
  ```python
  # in ChatPane
  @work(exclusive=True, group="chat", thread=True)
  def _run_turn(self, messages, cold):
      return turn.run_turn_impl(self, messages, cold)


  def abort(self):
      return turn.abort(self)
  ```
  `ChatPane._parse_params` → `params.parse_params(self)`, `_apply_params_to_inputs` → `params.apply_params_to_inputs`, `apply_config_params` → `params.apply_config_params(self,cfg)`, `_on_param_submitted` → `params.on_param_submitted(self,event)`. `on_mount`/`tui`/`has_live_turn`/`compose`/`_on_input_submitted` stay on facade. Preserve `ponytail:` socket comment `chat_pane.py:301`.
- Update `src/mlx_tui/app/presets_ctrl.py` imports that touched `pane._parse_params` / `pane._apply_params_to_inputs` — now call via `pane._parse_params()` stays on facade (delegates), so no caller change in `app`. Keep `ChatPane` TYPE_CHECKING imports in `app` to avoid cycle.
- No re-export shim needed — `ChatPane` canonical in `chat_pane/__init__.py` (`from mlx_tui.chat_pane import ChatPane` stays valid as package `__init__`, not a shim).

**Success Criteria:**

#### Automated Verification:
- [x] `uv run python -c "from mlx_tui.chat_pane import ChatPane; from mlx_tui.chat_pane.params import parse_params; from mlx_tui.chat_pane.turn import abort; print('chat pkg ok')"`
- [x] Whole suite green: `uv run pytest -q` (presets/config still touch ChatPane)
- [x] Lint clean: `uv run ruff check src/mlx_tui`
- [x] Types clean: `uv run pyrefly check`
- [x] Size: `wc -l src/mlx_tui/chat_pane/*.py` each ≤180, `chat_pane/__init__.py` ≤150; global ceiling ≤250 — actual 133/86/217, turn 217 >180 but ≤250 (deviation: aspirational not met, global ok)
- [x] Targeted check: `uv run pytest tests/unit/test_history.py::test_tok_int_parses_est_and_digit_guards_sentinel tests/integration/test_chat_integration.py -q` or `uv run python -c "from mlx_tui.chat_pane.params import parse_params; print('params ok')"`

#### Manual Verification:
- [ ] Chat tab renders Collapsible params; entering `0.9` temperature then sending a message includes `temperature:0.9` in `conftest StubServer.requests[-1]` (check harness); `Esc` mid-stream via `chat_has_live_turn` cancels and shows dim `cancelled` line

### Phase 4: `models_pane` package — table ops, swap, delete

306→ four files each ≤140.

**Changes:**
- Create `src/mlx_tui/models_pane/`; `bash: mkdir -p src/mlx_tui/models_pane && git mv src/mlx_tui/models_pane.py src/mlx_tui/models_pane/__init__.py`.
- Trim `src/mlx_tui/models_pane/__init__.py` to facade (~100 lines): keep `ModelsPane` with `__init__` `models_pane.py:27`, `tui` `models_pane.py:33`, `compose` `models_pane.py:37`, `row_size` `models_pane.py:113` (or delegate), `refresh_markers` shim, plus delegating `request_load_swap`/`request_delete_model`/`run_warm_swap`/`run_boot` to submodules.
- Create `src/mlx_tui/models_pane/table_ops.py` (~85 lines):
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  from textual.css.query import NoMatches
  from mlx_tui.models import scan_models
  from mlx_tui.table import ModelsTable

  if TYPE_CHECKING:
      from mlx_tui.models_pane import ModelsPane


  def _rescan_impl(
      pane: "ModelsPane",
  ) -> (
      None
  ): ...  # models_pane.py:44 using pane.tui.latest_avail_gib, pane.tui.call_from_thread
  def populate(pane, rows): ...  # 50
  def refresh_markers(pane) -> None: ...  # 63
  def progress_line(pane, repo_id, seconds): ...  # 121
  ```
  # ponytail: no public rescan() helper — ModelsPane.rescan() calls its own @work _rescan() directly; only _rescan_impl lives here.
  Keep `@work` stubs on `ModelsPane` in `models_pane/__init__.py`:
  ```python
  @work(exclusive=True, group="rescan", thread=True)
  def _rescan(self) -> None:
      return table_ops._rescan_impl(self)
  ```
- Create `src/mlx_tui/models_pane/swap_ops.py` (~135 lines):  # renamed from swap.py to avoid shadowing mlx_tui.swap
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  from mlx_tui import serverctl
  from mlx_tui.swap import BootPlan, SwapState, health_timeout
  from mlx_tui.models import ModelRow

  if TYPE_CHECKING:
      from mlx_tui.models_pane import ModelsPane


  def request_load_swap(
      pane: "ModelsPane",
  ) -> (
      None
  ): ...  # models_pane.py:76 (checks swap_busy, table cursor, green vs restart vs error)
  def boot_plan_for(row: ModelRow) -> BootPlan: ...  # 105
  def run_warm_swap_impl(pane: "ModelsPane", row: ModelRow) -> None: ...  # 130
  def fail_swap(pane, message): ...  # 151
  def run_boot_impl(
      pane: "ModelsPane", plan: BootPlan
  ) -> None: ...  # 159 (stream, tick, wait_healthy, tracked_model)
  ```
  Stubs on `ModelsPane`:
  ```python
  @work(exclusive=True, group="swap", thread=True)
  def run_warm_swap(self, row: ModelRow) -> None:
      return swap_ops.run_warm_swap_impl(self, row)


  @work(exclusive=True, group="swap", thread=True)
  def run_boot(self, plan: BootPlan) -> None:
      return swap_ops.run_boot_impl(self, plan)
  ```
- Create `src/mlx_tui/models_pane/delete.py` (~90 lines):
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  from textual.css.query import NoMatches
  from mlx_tui.confirm import ConfirmScreen
  from mlx_tui.models import CacheNotFound, ModelRow, delete_repos

  if TYPE_CHECKING:
      from mlx_tui.models_pane import ModelsPane


  def request_delete_model(pane: "ModelsPane") -> None: ...  # 234
  def on_delete_confirmed(pane, confirmed): ...  # 267
  def _run_delete_impl(pane: "ModelsPane", row: ModelRow) -> None: ...  # 276
  ```
  Stub on `ModelsPane`:
  ```python
  @work(exclusive=True, group="delete", thread=True)
  def _run_delete(self, row: ModelRow) -> None:
      return delete._run_delete_impl(self, row)
  ```
- Keep `serverctl.build_start_command` and `wait_healthy` usage as before; no new deps. `ModelsTable` actions that `query_one(ModelsPane)` stay valid (`table.py:72` local import `from mlx_tui.models_pane import ModelsPane` now resolves to package).
- Update patch targets in tests: `pytest` seams change `mlx_tui.models_pane.scan_models` → `mlx_tui.models_pane.table_ops.scan_models` and `mlx_tui.app.find_server_pid` / `mlx_tui.process.find_server_pid` → `mlx_tui.app.state` + `mlx_tui.app.polling` (module-imported `process.find_server_pid` propagates via `mlx_tui.process` patch; or patch `mlx_tui.app.state.find_server_pid` / `mlx_tui.app.polling.find_server_pid` explicitly) in `tests/integration/test_swap_integration.py:33`; no shim re-export.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run python -c "from mlx_tui.models_pane import ModelsPane; from mlx_tui.models_pane.swap_ops import run_boot_impl; from mlx_tui.models_pane.delete import request_delete_model; print('models pkg ok')"`
- [x] Whole suite green: `uv run pytest -q` (swap integration exercises all branches)
- [x] Lint clean: `uv run ruff check src/mlx_tui`
- [x] Types clean: `uv run pyrefly check`
- [x] Size: `wc -l src/mlx_tui/models_pane/*.py` each ≤180 (aspirational ≤150), `__init__.py` ≤150 (aspirational ≤120); global ceiling ≤250 — actual 88/88/161/57, all ≤180 (swap_ops 161 >150 aspirational but ≤180 ok)
- [x] Targeted check: `uv run pytest tests/integration/test_swap_integration.py::test_warm_swap_happy_path -q`

#### Manual Verification:
- [ ] `uv run mlx-tui` Models tab: `rescan` populates rows, `enter` warm-loads (mock via stub), `d` opens confirm then deletes (check `ModelsTable` updates)

### Phase 5: `search_screen` package + `history` package + final sweep

Finishes split, all files ≤250, no `*.py` over ceiling.

**Changes:**
- Create `src/mlx_tui/search_screen/`; `bash: mkdir -p src/mlx_tui/search_screen && git mv src/mlx_tui/search_screen.py src/mlx_tui/search_screen/__init__.py` (~90 lines facade): keep `ResultsTable` `search_screen.py:32` + `_format_size` `search_screen.py:41` (define before submodule imports to avoid cycle) + `SearchScreen` with `__init__` `search_screen.py:67`, `tui` `search_screen.py:76`, `compose` `search_screen.py:80`, `on_mount` `search_screen.py:87`, `_set_line` `search_screen.py:93`, plus delegates. In `__init__.py` define `_format_size` first, then `from . import query, download` (or import inside methods) so `download.py` can safely `from mlx_tui.search_screen import _format_size`. No shim — canonical `SearchScreen` in `__init__.py`.
- Create `src/mlx_tui/search_screen/query.py` (~120 lines):
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  from huggingface_hub import HfApi
  from mlx_tui.models import quant_label
  from mlx_tui.search import (
      list_results,
      repo_files_with_sizes,
      filtered_download_size,
      fits_disk,
      free_disk_bytes,
  )

  if TYPE_CHECKING:
      from mlx_tui.search_screen import SearchScreen


  def on_search_submitted(
      screen: "SearchScreen", event
  ) -> None: ...  # search_screen.py:100
  def run_search_impl(screen: "SearchScreen", query: str) -> None: ...  # 115
  def populate(screen, ids): ...  # 134
  def on_row_highlighted(screen, event): ...  # 153 (plain, no @on)
  def fetch_size_impl(screen: "SearchScreen", repo_id: str) -> None: ...  # 168
  def fill_size_cell(screen, repo_id, size, glyph): ...  # 178
  ```
  Keep `@work`/`@on` on `SearchScreen` in `__init__.py`:
  ```python
  @on(Input.Submitted, "#search-input")
  def _on_search_submitted(self, event):
      return query.on_search_submitted(self, event)


  @work(exclusive=True, group="hf-search", thread=True)
  def _run_search(self, query):
      return query.run_search_impl(self, query)


  @on(DataTable.RowHighlighted, "#search-results")
  def _on_row_highlighted(self, event):
      return query.on_row_highlighted(self, event)


  @work(exclusive=True, group="hf-size", thread=True)
  def _fetch_size(self, repo_id):
      return query.fetch_size_impl(self, repo_id)
  ```
- Create `src/mlx_tui/search_screen/download.py` (~135 lines):
  ```python
  from __future__ import annotations
  import threading
  from typing import TYPE_CHECKING
  from textual.css.query import NoMatches
  from textual.widgets import Input, Static
  from mlx_tui.search import (
      CancelledDownload,
      download_snapshot,
      free_disk_bytes,
      fits_disk,
  )
  from mlx_tui.search_screen import (
      _format_size,
  )  # safe: __init__.py defines _format_size before importing download

  if TYPE_CHECKING:
      from mlx_tui.search_screen import SearchScreen


  def start_download(screen: "SearchScreen") -> None: ...  # search_screen.py:187
  def run_download_impl(
      screen: "SearchScreen", repo_id: str, cancel_event: threading.Event
  ) -> None: ...  # 215
  def progress_line(screen, repo_id, done, expected): ...  # 241
  def finish(screen, outcome, detail=None): ...  # 253
  def rescan_models(screen): ...  # 275 (local import ModelsPane)
  def close_screen(screen): ...  # 284 (cancel logic)
  ```
  # ponytail: _format_size lives in search_screen/__init__.py and is defined before `from . import download` to avoid circular init; alternative is lazy import inside progress_line.
  Keep `@work` on `SearchScreen`:
  ```python
  @work(exclusive=True, group="hf-download", thread=True)
  def _run_download(self, repo_id, cancel_event):
      return download.run_download_impl(self, repo_id, cancel_event)
  ```
- Create `src/mlx_tui/history/`; `bash: mkdir -p src/mlx_tui/history && git mv src/mlx_tui/history.py src/mlx_tui/history/__init__.py` then split into `store.py`/`sparkline.py`/`tokens.py` and `git rm src/mlx_tui/history/__init__.py` in the same commit (ponytail: `# ponytail: history package — no shim, canonical imports are store/sparkline/tokens` explains the deletion). No `history/__init__.py` re-exports remain — `history` becomes a package with only the three canonical submodules; do not keep a shim. Update every `grep -rn "from mlx_tui.history" src` hit:
- Create `src/mlx_tui/history/store.py` (~90 lines): `_Ring[T]` `history.py:18` + `MemoryRecord` `history.py:34` + `MemoryStore` `history.py:45` + `TurnRecord` `history.py:58` + `HistoryStore` `history.py:72` (verbs: `add/series/models/all_records/clear`).
- Create `src/mlx_tui/history/sparkline.py` (~130 lines): `_bit` `history.py:114` / `_braille_char` `history.py:120` / `sparkline_visible` `history.py:126` / `_braille_levels` `history.py:134` / `_render_braille` `history.py:161` / `render_sparkline` `history.py:191` / `render_memory_sparkline` `history.py:218` / `_shade_for_ctx` `history.py:254` / `SPARKLINE_WIDTH/HEIGHT/CONSTANTS`.
- Create `src/mlx_tui/history/tokens.py` (~50 lines): `CHARS_PER_TOKEN_EST` `history.py:12` + `_tok_int` `history.py:273` + `estimate_tokens` `history.py:281` + `trim_for_context` `history.py:286`.
- Update imports across repo to canonical paths (enumerate all `from mlx_tui.history` hits):
  - `src/mlx_tui/app/__init__.py:30` `HistoryStore, MemoryStore` → `from mlx_tui.history.store import HistoryStore, MemoryStore` (delete stale `from mlx_tui.history import ...` after Phase 1); `src/mlx_tui/app/polling.py`: `MemoryRecord` → `from mlx_tui.history.store import MemoryRecord` (also repoint after Phase 1's `from mlx_tui.history import MemoryRecord`); `src/mlx_tui/app/state.py` has no history imports — verify no stale `from mlx_tui.history` remains
  - `src/mlx_tui/chat_pane/turn.py`: `TurnRecord` → `history.store`, `_tok_int`/`estimate_tokens`/`trim_for_context`/`CHARS_PER_TOKEN_EST` → `history.tokens`
  - `src/mlx_tui/metrics_pane.py:14` → `from mlx_tui.history.store import TurnRecord` / `from mlx_tui.history.sparkline import _shade_for_ctx, render_sparkline, render_memory_sparkline, sparkline_visible`
  - `src/mlx_tui/sse.py:7` → `from mlx_tui.history.tokens import CHARS_PER_TOKEN_EST`
  - `tests/unit/test_history.py:1` → split imports accordingly; `tests/unit/test_history.py:402` → `from mlx_tui.history.sparkline import _shade_for_ctx`
  No circular import — `history/store.py`, `sparkline.py`, `tokens.py` are independent and contain no `app` imports.
- Final sweep edits (no new files): remove dead imports flagged by `ruff`, ensure `src/mlx_tui/app/__init__.py` module docstring `Textual coordinator…` stays, add `ponytail:` comments for any deliberate ceiling (e.g., `# ponytail: package facade, keep delegates thin; merge submodules if any stays <40 lines`).
- Verify `pyproject.toml:14` `packages = ["src/mlx_tui"]` picks up subpackages; hatchling auto-discovers subpackages (verified with `uv build`), but do not assume — verify via `uv build --wheel && tar tzf dist/*.whl | grep -E "mlx_tui/(app|history)"` and add explicit `tool.hatch.build.targets.wheel.packages` entries if missing.

**Success Criteria:**

#### Automated Verification:
- [x] No file over ceiling: `bash -c 'wc -l src/mlx_tui/app/*.py src/mlx_tui/chat_pane/*.py src/mlx_tui/models_pane/*.py src/mlx_tui/search_screen/*.py src/mlx_tui/history/*.py 2>/dev/null | awk '\''$1>250 {print "OVER:", $2, $1; exit 1}'\'' && echo "all ≤250"'` (global ceiling ≤250; aspirational per-package ≤180) — actual all ≤250, largest 250
- [x] Canonical imports: `uv run python -c "from mlx_tui.app import MlxTuiApp; from mlx_tui.chat_pane import ChatPane; from mlx_tui.models_pane import ModelsPane; from mlx_tui.search_screen import SearchScreen; from mlx_tui.history.store import HistoryStore; from mlx_tui.history.sparkline import _shade_for_ctx; from mlx_tui.history.tokens import trim_for_context; print('imports ok')"`
- [x] Whole suite green (after updating `tests/unit/test_history.py:402` to `from mlx_tui.history.sparkline import _shade_for_ctx` and `tests/conftest.py:18` if needed): `uv run pytest -q`
- [x] Lint clean: `uv run ruff check src/mlx_tui`
- [x] Format check: `uv run ruff format --check src tests` (pre-format `src/mlx_tui/search.py:92` in a prior commit so the gate passes; do not exempt `test_swap_integration.py` — the baseline failure is `search.py:92`) — after `ruff format` 10 files reformatted, now clean
- [x] Types clean: `uv run pyrefly check`
- [x] Entrypoint: `uv run python -c "from mlx_tui.app import main; print('entrypoint ok')"`
- [x] Wheel includes subpackages: `uv build --wheel && tar tzf dist/*.whl | grep -E "mlx_tui/(app|history)" | head`
- [x] Targeted history check: `uv run python -c "from mlx_tui.history.sparkline import _shade_for_ctx; assert _shade_for_ctx([100,200,300,400])[0]=='dim'"`

#### Manual Verification:
- [ ] Full smoke on live `mlx_lm.server`: status dot green + model/RSS, Models `enter` warm-load, Chat streams with `tok/s` stamp +markdown, `Esc` cancels, `/` search → download → `Models` rescan, `ctrl+p/o` preset cycle, `ctrl+g` config edit reload
- [ ] `grep -rn "from mlx_tui.history" src` shows canonical `history.store`/`sparkline`/`tokens` paths, no stale `from mlx_tui.app import _shade_for_ctx`

## Out of Scope

- Moving `main()`/`argparse` out of `app` package entry point, or changing `pyproject.toml` `mlx_tui.app:main` beyond re-export.
- Semantics of `sse.py`, `swap.py` state graph, `status.py` classify rules, `config.py` strictness, `models.py` scan/delete, `serverctl.py` spawn/grace/health.
- New features (persistence beyond history, new tabs).
- Performance tuning (poll 2s, flush 0.1s, psutil scan).
- CI / coverage tooling.

## Risks & Mitigations

- **Package vs file import clash** (`app.py`→`app/__init__.py`) → Mit: `git mv` in single commit plus import fixes; verify canonical imports in Phase 1 criterion before proceeding.
- **Circular imports** (`app` ↔ panes for `compose`/`tui`) → Mit: panes import `MlxTuiApp` only under `TYPE_CHECKING`; runtime `cast("MlxTuiApp", self.app)` stays; `app/presets_ctrl.py` imports `ChatPane` and `app/swap_ctrl.py` imports `ModelsPane` at runtime (safe: those panes only TYPE_CHECKING-import `MlxTuiApp`), verified in prior split.
- **Textual `@work` / `call_from_thread` on widget** → `@work` is a Widget-method decorator only (node-scoped) — keep thin `@work` stubs on `ChatPane`/`ModelsPane`/`SearchScreen` delegating to plain `*_impl` in submodules; `call_from_thread` only on `App` in 8.2.8 — pane workers must use `self.tui.call_from_thread`; each phase keeps this hop, test with `harness` pilot.
- **`push_screen` only on App/Screen** → `models_pane/delete.py` and `table.py` continue via `self.app.push_screen` / `self.tui.app.push_screen`; covered by swap integration suite.
- **`query_one` missing pane during shutdown** → keep `NoMatches` guards as today in all delegates.
- **Monkeypatch stale target** (`scan_models`, `find_server_pid`) → update `tests/integration/test_swap_integration.py:33` patches to `mlx_tui.models_pane.table_ops.scan_models` and `mlx_tui.app.state`/`mlx_tui.app.polling` (or `mlx_tui.process` when using module-import style); no shim — patches updated atomically per phase.
- **Hatch package discovery** → `packages = ["src/mlx_tui"]` may not auto-discover subpackages; verify with `uv build --wheel && tar tzf dist/*.whl | grep mlx_tui/app`; if missing, add explicit `tool.hatch.build.targets.wheel.packages` entries — gated in Phase 5 (hatchling does auto-discover when verified with `uv build`).
