# Refactor app.py into pane widgets + slim coordinator

**Date:** 2026-08-26
**Work Item:** n/a
**Status:** Complete

## Overview

Decompose the 767-line `src/mlx_tui/app.py` into two composable pane widgets (`ChatPane`, `ModelsPane`) under a coordinator `MlxTuiApp` that keeps only shared state, the poll loop, global bindings, and the swap/boot workers; relocate misplaced pure code to its domain modules and merge the duplicated restart/cold-start boot workers. Behavior-preserving except where duplication is merged (identical observable output); all gates green at every phase boundary.

## Current State

Baseline verified 2026-08-26: `uv run pytest -q` → 158 passed; `uv run ruff check .` clean; `uv run pyrefly check` → 0 errors (strict, `src`+`tests`). Line counts: `app.py` 767; next largest source files `serverctl.py`/`chat.py` at 130 each.

`MlxTuiApp` currently mixes seven concerns:

1. **Shell/wiring** — `compose` (app.py:134-146), `on_mount` including `ModelsTable` column setup (app.py:148-157), tab-activated rescan (app.py:159-162), `on_unmount` (app.py:164-165).
2. **Poll loop** — `_poll` (app.py:167-189), `_classify_liveness` (app.py:246-256), `_render_status` (app.py:258-269).
3. **Models-table management** — `_rescan_models` worker (app.py:203-206), `_populate_table` poking cells via `Coordinate` (app.py:208-225), `_refresh_markers` likewise (app.py:227-244); glyphs/column indices `_FITS_GLYPHS`, `_COL_FITS`, `_COL_LOADED` (app.py:55-57).
4. **Swap/boot orchestration** — `request_load_swap` (app.py:271-299), `_set_swap_ui` (app.py:301-312), `_progress_line` (app.py:314-320), `_row_size` (app.py:322-328), `_spawn_server` crash-grace loop (app.py:340-356), `_run_warm_swap` (app.py:358-380), `_run_restart_swap` (app.py:382-461), `action_cold_start` (app.py:463-478), `_run_cold_start` (app.py:480-541). `_run_restart_swap` and `_run_cold_start` share ~50 near-identical lines (stream callback, spawn + instant-crash check, `wait_healthy` wiring, FAILED→IDLE teardown, UI release) differing only in: stop_cmd phase, target model source, size source, success/timeout message strings.
5. **Chat turn lifecycle** — `_on_input_submitted` (app.py:543-560), `_run_turn` worker (app.py:562-637), `_abort_chat` socket-shutdown workaround (app.py:639-655), stream/system/stamp writers (app.py:660-679, 725-734).
6. **Delete flow** — `request_delete_model` (app.py:685-699), `_on_delete_confirmed` (app.py:701-707), `_run_delete` worker (app.py:709-723).
7. **Config editing** — `action_edit_config` (app.py:736-754; uses `App.suspend()`, which exists only on App).

Misplaced pure code:

- `_build_start_command` (app.py:330-338) — start-command domain, belongs in `serverctl.py`.
- `_error_detail` (app.py:68-89) — chat-wire domain, belongs in `chat.py`; currently untested.
- `_CONFIG_TEMPLATE` (app.py:59-65) — config domain, belongs in `config.py`.
- Token-heuristic duplication: `sse.py:112` hardcodes `user_chars / 3.5` while `history.py:15` defines `_CHARS_PER_TOKEN_EST = 3.5` — two sources of truth for one heuristic.

Coupling facts:

- `table.py:9-10` imports `MlxTuiApp` under TYPE_CHECKING purely to call `app.request_load_swap()`/`app.request_delete_model()` (table.py:19-25) — the table's actions will belong to its pane after this refactor.
- `main()` stays in `app.py` (prior plan declared the entry point out of scope; unchanged here). `main.py` (repo root) is a shim importing `mlx_tui.app:main`.
- Test seams that poke App privates and will be repointed: `monkeypatch.setattr("mlx_tui.app.scan_models", …)` (test_swap_integration.py:39 — patches the **usage site**, so it must follow the move), `harness.app._rescan_models()` / `harness.app._rows` (test_swap_integration.py:51-52), `harness.app._current_model_supplier = …` (test_swap_integration.py:100, 148, 217, 273), `harness.app._chat.active_response` (test_swap_integration.py:351), `harness.app.messages` (test_app_integration.py:53-59 etc.), `monkeypatch.setattr(MlxTuiApp, "_classify_liveness", …)` (test_swap_integration.py:142 — `_classify_liveness` **stays on App**, unaffected).
- Established conventions to follow: UI-free functional-core modules with docstrings; frozen dataclasses for value objects; `@work(exclusive=True, group=…, thread=True)` + `call_from_thread` for cross-thread UI updates; `NoMatches` guards around `query_one` in teardown paths; exact-command gates `uv run pytest -q` / `uv run ruff check .` / `uv run pyrefly check`.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Extract `ChatPane(Vertical)` and `ModelsPane(Vertical)` widgets; App keeps shared state + poll + swap workers | B: flat App + more helper modules; C: swap workers into ModelsPane | User choice A. Panes own their widgets/handlers/workers (`@work` works on widgets — worker groups cancel node-scoped via `workers.cancel_group(node, group)`; but `call_from_thread` exists only on App in textual 8.2.8, verified against the installed package, so pane workers hop to the UI thread via `self.tui.call_from_thread(...)`); swap/boot spans status bar, chat pane, models pane and the app log, so it stays on the coordinator; C would make panes reach sideways worse than up |
| Panes reach App via a typed `tui` property (`cast("MlxTuiApp", self.app)`) and a small public App surface | Passing an explicit context object into pane constructors; panes posting messages to App | Context object is ceremony at this code size (violates repo's DRY/YAGNI stance); message indirection spreads trivial coordination across handlers. Public surface renamed from `_x` to `x` where panes touch it: `effective_model()`, `log_app()`, `set_swap_ui()`, `progress_line()`, `refresh_markers()`, `run_boot()`, `run_warm_swap()`, `cold_tracker`, `swap_machine`, `current_model_supplier`, `latest_avail_gib` |
| Single `_run_boot(plan: BootPlan)` worker replaces `_run_restart_swap` + `_run_cold_start` | Keep both workers; parameterize per-call with loose kwargs | Frozen-dataclass `BootPlan(model_id, size_on_disk, stop_first, success_line)` makes each call site self-documenting; every log string is reproduced verbatim (success/timeout lines derived per branch exactly as today) |
| Crash-grace window moves into `ServerController.spawn_with_grace(cmd, on_line, grace_s, poll_s)` | Leave `_spawn_server` on App | Policy-free timing mechanics belong beside `spawn_command`; grace constants stay on App (orchestration policy) and are passed in |
| Cell rendering (`loaded_cell`, `fits_cell`, glyphs, column indices) moves into `table.py`; `ModelsTable.set_rows()/refresh_markers()` replace App-side `Coordinate` poking | Keep rendering in App; move only constants | Rendering is the table's concern; pure cell functions are unit-testable without mounting; App stops knowing column geometry |
| `scan_models` usage-site patch target moves to `"mlx_tui.models_pane.scan_models"` | Patch `mlx_tui.models.scan_models` | Repo convention (established at test_swap_integration.py:39) is patching the importing module, keeping the stub injection honest at the call site |
| Transcript `messages`, `ChatClient` instance, `_cancel_requested` move onto `ChatPane` | Keep transcript on App | Nothing outside chat reads `messages` except tests (updated via a `harness.chat_pane` accessor); shrinks App state |
| `ColdTracker` instance stays on App as public `cold_tracker` | Move into ChatPane | `_poll` (App) observes liveness into it; ChatPane consumes it — genuinely shared |
| Constants `_MAX_OUTPUT_TOKENS`, `_MAX_CONTEXT_TOKENS_EST` move into `chat_pane.py` | Leave on App | Pure chat policy; App no longer needs them |
| chars/token constant: `history.CHARS_PER_TOKEN_EST` (renamed public) imported by `sse.py` | Duplicate the literal; define in `sse.py` and import into `history` | Estimation heuristic belongs to the context-budget domain (`history.py`); `sse` already imports nothing from mlx_tui and `history` imports nothing back — acyclic |

## Implementation Phases

### Phase 1: Pure-code relocations

Move four pieces of misplaced/duplicated pure code to their domain modules, with new unit tests where coverage was missing. No structural/UI change.

**Changes:**
- `src/mlx_tui/serverctl.py` — add `import shlex` and the module-level function (body moved verbatim from app.py:330-338, `self.` dropped):
  ```python
  def build_start_command(start_cmd: str, model_id: str | None) -> str:
      """Inject the target model into a configured start command."""
      if "{model}" in start_cmd:
          return start_cmd.replace("{model}", model_id or "")
      if "--model" in start_cmd:
          return start_cmd
      if model_id is None:
          return start_cmd
      return f"{start_cmd} --model {shlex.quote(model_id)}"
  ```
- `src/mlx_tui/app.py` — delete `_build_start_command`; replace both call sites: `self._build_start_command(start_cmd, row.repo_id)` (app.py:414) → `build_start_command(start_cmd, row.repo_id)`; `self._build_start_command(start_cmd, self.config.model)` (app.py:492) → `build_start_command(start_cmd, self.config.model)`; extend the `mlx_tui.serverctl` import.
- `src/mlx_tui/config.py` — add module-level constant and helper:
  ```python
  CONFIG_TEMPLATE = """\
  # mlx-tui config — model/host/port/start_cmd/stop_cmd/pidfile
  # model = "ornith-ai/Ornith-1.5-9B-MLX-4bit"
  # start_cmd = "mlx_lm.server --port 8080"
  # stop_cmd = "pkill -f mlx_lm.server"
  # pidfile = "/tmp/mlx-server.pid"
  """.encode()


  def write_template(path: Path) -> None:
      """Create parent directories and write the commented default config."""
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(CONFIG_TEMPLATE)
  ```
- `src/mlx_tui/app.py` — delete `_CONFIG_TEMPLATE` (app.py:59-65); in `action_edit_config` replace the `mkdir` + `exists` + `write_bytes` trio (app.py:737-740) with `if not path.exists(): write_template(path)`; extend the `mlx_tui.config` import with `write_template`.
- `src/mlx_tui/chat.py` — move `_error_detail` from app.py:68-89 verbatim as public `error_detail(response: httpx.Response) -> str`, same docstring.
- `src/mlx_tui/app.py` — delete `_error_detail`; import `error_detail` from `mlx_tui.chat` (alongside `ChatClient`); call site app.py:632 becomes `f"{error_detail(exc.response)}"`.
- `src/mlx_tui/history.py` — rename `_CHARS_PER_TOKEN_EST` → `CHARS_PER_TOKEN_EST` (update its single use in `estimate_tokens`, history.py:20); module docstring already documents the heuristic, unchanged.
- `src/mlx_tui/sse.py` — in `token_accounting` replace the literal: `f"{user_chars / CHARS_PER_TOKEN_EST:.0f} (est)"`; add `from mlx_tui.history import CHARS_PER_TOKEN_EST`.
- `tests/unit/test_serverctl.py` — add `build_start_command` cases: `("{model} --port 8080", "org/m-4bit")` → `"org/m-4bit --port 8080"`; `("{model}", None)` → `""`; `("mlx_lm.server --port 8080", "org/m")` → `"mlx_lm.server --port 8080 --model org/m"`; `("mlx_lm.server --port 8080", "or g/m")` → `"mlx_lm.server --port 8080 --model 'or g/m'"` (shlex quoting); `("s --model x", "org/m")` → `"s --model x"` (already has --model); `("s", None)` → `"s"`.
- `tests/unit/test_chat.py` — add `error_detail` cases using `httpx.Response(status, json={...})`: `(500, {"detail": "boom"})` → `": boom"`; `(500, {"error": {"message": "nope"}})` → `": nope"`; `(500, {"error": "flat"})` → `": flat"`; `(500, {"detail": "   "})` → `""`; `httpx.Response(502, text="<html>")` → `""` (non-JSON body).
- `tests/unit/test_config.py` — add: `write_template(tmp_path / "sub" / "config.toml")` creates parents; file bytes start with `b"# mlx-tui config"` and contain `b'start_cmd = "mlx_lm.server --port 8080"'`; `parse_config` on the written file yields all-default `AppConfig` (every line commented).
- `tests/unit/test_history.py` — add pin: `CHARS_PER_TOKEN_EST == 3.5` (two modules depend on this value's stability).

**Success Criteria:**

#### Automated Verification:
- [x] New/updated unit tests pass: `uv run pytest -v tests/unit/test_serverctl.py tests/unit/test_chat.py tests/unit/test_config.py tests/unit/test_history.py`
- [x] Whole suite green: `uv run pytest -q` (172 passed)
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`

#### Manual Verification:
- [x] `uv run mlx-tui --help` prints usage (imports intact after the moves)

### Phase 2: Dedupe boot workers + crash-grace into ServerController

Merge `_run_restart_swap` and `_run_cold_start` into one parameterized worker; move the crash-grace window into `ServerController`. All log strings preserved verbatim.

**Changes:**
- `src/mlx_tui/serverctl.py` — add method (`import time` already present at serverctl.py:7):
  ```python
  def spawn_with_grace(
      self,
      cmd: str,
      *,
      on_line: Callable[[str], None],
      grace_s: float,
      poll_s: float,
  ) -> tuple[subprocess.Popen[str], bool]:
      """Start a long-running server command, then watch a crash-grace window.

      Returns ``(proc, monitor)``. ``monitor`` is False when the process was
      already gone within ``grace_s`` — either it crashed (rc checked by the
      caller) or it detached itself (e.g. a trailing ``&``), in which case
      health waiting must run unmonitored.
      """
      proc = self.spawn_command(cmd, on_line=on_line)
      deadline = time.monotonic() + grace_s
      while time.monotonic() < deadline and proc.poll() is None:
          time.sleep(poll_s)
      return proc, proc.poll() is None
  ```
- `src/mlx_tui/swap.py` — add `from dataclasses import dataclass` and the frozen dataclass (module level, after `health_timeout`; placed here because it is swap/boot domain state and Phase 4's `models_pane` must import it without touching `app`, avoiding an import cycle):
  ```python
  @dataclass(frozen=True)
  class BootPlan:
      """Everything one boot/restart worker run needs; config pre-checked by caller."""

      model_id: str | None
      size_on_disk: int
      stop_first: bool
      success_line: str
  ```
- `src/mlx_tui/app.py`:
  - Extend the existing `mlx_tui.swap` import with `BootPlan`.
  - Delete `_spawn_server` (app.py:340-356); delete `_run_restart_swap` (app.py:382-461) and `_run_cold_start` (app.py:480-541); add:
    ```python
    def _fail_swap(self, message: str | None) -> None:
        """FAILED→IDLE reset, optional red log line, then UI release."""
        self._swap_machine.transition(SwapState.FAILED)
        self._swap_machine.transition(SwapState.IDLE)
        if message is not None:
            self.call_from_thread(self._log_app, message, "red")
        self.call_from_thread(self._set_swap_ui, False)

    @work(exclusive=True, group="swap", thread=True)
    def _run_boot(self, plan: BootPlan) -> None:
        def stream(line: str) -> None:
            self.call_from_thread(self._log_app, f"[swap] {line}")

        if plan.stop_first:
            if self.config.start_cmd is None or self.config.stop_cmd is None:
                # Config rewritten mid-swap; bail honestly.
                self._fail_swap("[swap] commands vanished from config")
                return
            # Marker hygiene: clear tracked state the moment stop fires.
            self._tracked_model = None
            self.call_from_thread(self._refresh_markers)
            stop_rc = self._server_ctl.run_command(
                self.config.stop_cmd, on_line=stream
            )
            if stop_rc != 0:
                self._fail_swap(f"[swap] stop_cmd exited {stop_rc}")
                return
            # Graph legality (swap.py:16-26): STOPPING may not reach IDLE or
            # WAITING_HEALTH directly; mirrors app.py:413. Restart-only — the
            # cold-start entry already sits in STARTING (starting→starting is
            # illegal).
            self._swap_machine.transition(SwapState.STARTING)
        start_cmd = self.config.start_cmd
        if start_cmd is None:  # config rewritten mid-boot; bail honestly
            self._fail_swap(None)
            return
        start_full = build_start_command(start_cmd, plan.model_id)
        self.call_from_thread(self._log_app, f"[swap] starting: {start_full}")
        proc, monitor = self._server_ctl.spawn_with_grace(
            start_full, on_line=stream, grace_s=_CRASH_GRACE_S, poll_s=_CRASH_POLL_S
        )
        start_rc = proc.poll()
        if start_rc is not None and start_rc != 0:
            # An instantly-crashing start must not burn the health deadline.
            self._fail_swap(f"[swap] start_cmd exited {start_rc}")
            return
        self._swap_machine.transition(SwapState.WAITING_HEALTH)
        deadline = health_timeout(plan.size_on_disk)

        def tick(seconds: int) -> None:
            self.call_from_thread(
                self._progress_line, plan.model_id or "server", seconds
            )

        ok = self._server_ctl.wait_healthy(
            f"http://{self.host}:{self.port}/v1/models",
            HealthWatch(
                target_model=plan.model_id,
                current_model=self._current_model_supplier,
                is_running=(lambda: proc.poll() is None) if monitor else None,
            ),
            timeout_s=deadline,
            on_tick=tick,
        )
        if ok:
            self._tracked_model = plan.model_id or self._effective_model()
            self._swap_machine.transition(SwapState.IDLE)
            self.call_from_thread(self._log_app, plan.success_line)
            self.call_from_thread(self._refresh_markers)
        elif monitor and proc.poll() is not None:
            self._fail_swap(f"[swap] start_cmd exited {proc.returncode}")
        else:
            timeout_line = (
                f"swap timed out after {int(deadline)}s — check the log pane"
                if plan.stop_first
                else "server did not come up in time — check the log pane"
            )
            self._fail_swap(timeout_line)
        # Unconditional in both originals (app.py:461, 541): the success branch
        # must release the UI too — _fail_swap only covers the failure arms.
        self.call_from_thread(self._set_swap_ui, False)
    ```
  - Update call sites: `_run_restart_swap(row)` (app.py:293) → `self._run_boot(BootPlan(model_id=row.repo_id, size_on_disk=row.size_on_disk, stop_first=True, success_line=f"✓ {row.repo_id} is serving"))`; `_run_cold_start()` (app.py:478) → `self._run_boot(BootPlan(model_id=self.config.model, size_on_disk=self._row_size(self.config.model), stop_first=False, success_line="✓ server is up"))`. Entry transitions in `request_load_swap`/`action_cold_start` unchanged (STOPPING / STARTING respectively, set before invoking the worker).
  - `_run_warm_swap` unchanged this phase.
- Tests: no asserted string changes — every asserted string survives verbatim: `"[swap] bye"`, `"[swap] booting"`, `"[swap] starting:"`, `"--model"`, `"✓ … is serving"`, `"✓ server is up"`, `"[swap] start_cmd exited N"`, `"[swap] stop_cmd exited N"`, `"swap already in progress"`, `SwapState.IDLE/STARTING` states, and `health_timeout` scaling (`_RecordingController.timeouts` records `deadline` unchanged). But "no changes required" ≠ "safe to skip": the untouched swap-integration suite is precisely the tripwire for the two state-machine edges and the unconditional UI release above — omit any of them and every happy-path boot wedges mid-worker, so the suite's success-string waits never resolve.
- `tests/unit/test_serverctl.py` — add `spawn_with_grace` cases: instant-crash `cmd=f'{sys.executable} -c "raise SystemExit(3)"'` with `grace_s=1.0, poll_s=0.01` → returns `(proc, False)`, `proc.returncode == 3`; long-running `cmd=f"{sys.executable} -c \"import time; time.sleep(5)\""` with `grace_s=0.05, poll_s=0.01` → `monitor is True`, then `proc.terminate()` + `proc.wait(timeout=5)` in cleanup.

**Success Criteria:**

#### Automated Verification:
- [x] Swap integration suite passes unchanged: `uv run pytest -v tests/integration/test_swap_integration.py` (11 passed)
- [x] New unit tests pass: `uv run pytest -v tests/unit/test_serverctl.py` (22 passed)
- [x] Whole suite green: `uv run pytest -q` (174 passed)
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`

> Deviation note: the `_run_boot` STOPPING-legality comment's `mirrors app.py:413`
> cross-reference was reworded to "mirrors the STOPPING entry transition in
> request_load_swap" since app.py:413 no longer exists after the merge. Code and
> strings otherwise verbatim.

#### Manual Verification:
- [ ] With the real server down and `start_cmd` configured: `uv run mlx-tui`, press ctrl+s — log shows `[swap] starting: …` then `✓ server is up`, progress line counts seconds; behavior identical to before the merge

### Phase 3: Table rendering API

Glyphs, column geometry, and cell rendering become `table.py` concerns behind `set_rows`/`refresh_markers`; App stops poking cells via `Coordinate`.

**Changes:**
- `src/mlx_tui/table.py` — rewrite to own rendering:
  ```python
  """Models DataTable: rendering API plus load/delete key bindings."""

  from __future__ import annotations

  from textual.coordinate import Coordinate
  from textual.widgets import DataTable

  from mlx_tui.models import ModelRow, fits_headroom

  _FITS_GLYPHS: dict[bool | None, str] = {True: "✓", False: "⚠", None: "—"}
  _COL_FITS = 3
  _COL_LOADED = 4


  def loaded_cell(repo_id: str, effective_model: str | None) -> str:
      """The ● marker when repo_id is the model currently serving."""
      return "●" if effective_model == repo_id else ""


  def fits_cell(size_on_disk: int, avail_gib: float | None) -> str:
      """✓/⚠ headroom hint; em-dash when system memory is unknown."""
      return _FITS_GLYPHS.get(fits_headroom(size_on_disk, avail_gib), "—")


  class ModelsTable(DataTable[str]):
      BINDINGS = [
          ("enter", "load_swap", "Load/swap"),
          ("d", "delete_model", "Delete"),
      ]

      def on_mount(self) -> None:
          for column_key in ("model", "quant", "size", "fits", "loaded"):
              self.add_column(column_key, key=column_key)

      def set_rows(
          self,
          rows: list[ModelRow],
          *,
          effective_model: str | None,
          avail_gib: float | None,
      ) -> None:
          """Replace every row; renders quant/size/fits/loaded cells."""
          self.clear()
          for row in rows:
              self.add_row(
                  row.repo_id,
                  row.quant,
                  f"{row.size_on_disk / 2**30:.1f} GB",
                  fits_cell(row.size_on_disk, avail_gib),
                  loaded_cell(row.repo_id, effective_model),
                  key=row.repo_id,
              )

      def refresh_markers(
          self,
          rows: list[ModelRow],
          *,
          effective_model: str | None,
          avail_gib: float | None,
      ) -> None:
          """Update fits/loaded cells whose rendered value changed."""
          for index, row in enumerate(rows):
              new_loaded = loaded_cell(row.repo_id, effective_model)
              new_fits = fits_cell(row.size_on_disk, avail_gib)
              if self.get_cell_at(Coordinate(index, _COL_LOADED)) != new_loaded:
                  self.update_cell(row.repo_id, "loaded", new_loaded)
              if self.get_cell_at(Coordinate(index, _COL_FITS)) != new_fits:
                  self.update_cell(row.repo_id, "fits", new_fits)

      def action_load_swap(self) -> None:
          self.app.query_one(ModelsPane).request_load_swap()

      def action_delete_model(self) -> None:
          self.app.query_one(ModelsPane).request_delete_model()
  ```
  Notes: the `TYPE_CHECKING`/`MlxTuiApp` import disappears; `action_*` forward-references `ModelsPane`, which is defined in Phase 4 — **to keep this phase green standalone, declare the actions' targets via a lazy lookup**: define `action_load_swap`/`action_delete_model` bodies as `getattr(self.app, "request_load_swap")()` / `getattr(self.app, "request_delete_model")()` in this phase (App still hosts those methods), and switch them to the `ModelsPane` query in Phase 4 when the methods move. `Coordinate` import replaces the App-side one.
- `src/mlx_tui/app.py` — delete `_FITS_GLYPHS`, `_COL_FITS`, `_COL_LOADED` (app.py:55-57); delete the `add_column` loop in `on_mount` (now in `ModelsTable.on_mount`); rewrite `_populate_table` body after the `NoMatches` guard as:
  ```python
  table.set_rows(
      rows, effective_model=self._effective_model(),
      avail_gib=self._latest_avail_gib,
  )
  self._rows = rows
  ```
  and `_refresh_markers` body as:
  ```python
  table.refresh_markers(
      self._rows, effective_model=self._effective_model(),
      avail_gib=self._latest_avail_gib,
  )
  ```
  (empty-rows early return and `NoMatches` guard kept). Drop the now-unused `Coordinate` import.
  Note: unlike today's render path, `set_rows` recomputes the fits glyph from the *current* `latest_avail_gib` instead of reusing the precomputed `row.fits` captured at scan time — identical output in production up to a sub-second poll race, and self-correcting at the next `refresh_markers`. Test-injected rows whose `.fits` disagrees with recomputation (e.g. `avail=None` → `"—"`) simply render the recomputed value; the manual pixel-equivalence claim applies to the production flow only.
- `tests/unit/test_table.py` — new file, pure-function tests: `loaded_cell`: `("m", "m") → "●"`, `("m", "other") → ""`, `("m", None) → ""`; `fits_cell`: known-fit size + ample `avail_gib` → `"✓"`, oversized vs available → `"⚠"`, `avail_gib=None` → `"—"`. Construct `ModelRow` values directly (pattern of tests/unit/test_models.py).

**Success Criteria:**

#### Automated Verification:
- [x] New unit tests pass: `uv run pytest -v tests/unit/test_table.py` (6 passed)
- [x] Whole suite green (populated-table paths exercised by swap integration): `uv run pytest -q` (180 passed)
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`

> Deviation note: the plan's `ModelsTable.on_mount` snippet lacked `@override`;
> pyrefly strict flags `missing-override-decorator`, so the decorator + import
> were added.

#### Manual Verification:
- [ ] `uv run mlx-tui` against a running server: Models table renders all five columns with correct glyphs and the ● marker — pixel-equivalent to before

### Phase 4: Extract `ModelsPane`

Models-tab UI (progress line, table, rescan/populate/markers, load/delete requests, confirm modal, delete worker) moves into a `Vertical` pane widget. App exposes the small public surface panes need.

**Changes:**
- create `src/mlx_tui/models_pane.py`:
  ```python
  """Models tab pane: cache table, load/delete requests, swap progress."""

  from __future__ import annotations

  from typing import TYPE_CHECKING, Any, cast, override

  from textual import work
  from textual.app import ComposeResult
  from textual.containers import Vertical
  from textual.css.query import NoMatches
  from textual.widgets import Static

  from mlx_tui.confirm import ConfirmScreen
  from mlx_tui.models import CacheNotFound, ModelRow, delete_repos, scan_models
  from mlx_tui.swap import BootPlan
  from mlx_tui.table import ModelsTable

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp


  class ModelsPane(Vertical):
      """Owns #swap-progress and #models-table; reads shared state via tui."""

      def __init__(self, **kwargs: Any) -> None:
          super().__init__(**kwargs)
          self.rows: list[ModelRow] = []
          self._pending_delete_row: ModelRow | None = None

      @property
      def tui(self) -> MlxTuiApp:
          return cast("MlxTuiApp", self.app)

      @override
      def compose(self) -> ComposeResult:
          yield Static("", id="swap-progress")
          yield ModelsTable(id="models-table", cursor_type="row")

      def rescan(self) -> None:
          self._rescan()

      @work(exclusive=True, group="rescan", thread=True)
      def _rescan(self) -> None:
          rows = scan_models(self.tui.latest_avail_gib)
          # Widget has no call_from_thread in textual 8.2.8 — hop via App.
          self.tui.call_from_thread(self._populate, rows)

      def _populate(self, rows: list[ModelRow]) -> None:
          try:
              table = self.query_one("#models-table", ModelsTable)
          except NoMatches:
              # A rescan landing during shutdown has no table left to fill.
              return
          table.set_rows(
              rows,
              effective_model=self.tui.effective_model(),
              avail_gib=self.tui.latest_avail_gib,
          )
          self.rows = rows

      def refresh_markers(self) -> None:
          if not self.rows:
              return
          try:
              table = self.query_one("#models-table", ModelsTable)
          except NoMatches:
              return
          table.refresh_markers(
              self.rows,
              effective_model=self.tui.effective_model(),
              avail_gib=self.tui.latest_avail_gib,
          )

      def request_load_swap(self) -> None:
          # body moved verbatim from app.py:271-299 with these substitutions:
          #   self._swap_machine → self.tui.swap_machine
          #   self.status_state  → self.tui.status_state
          #   self._log_app      → self.tui.log_app
          #   self._set_swap_ui  → self.tui.set_swap_ui
          #   self._run_warm_swap(row)     → self.tui.run_warm_swap(row)
          #   self._abort_chat()           → see below
          #   self._swap_machine.transition(...) → unchanged via self.tui.swap_machine
          #   self._run_restart_swap(row)  → self.tui.run_boot(self._boot_plan_for(row))
          ...
      def _boot_plan_for(self, row: ModelRow) -> BootPlan:
          return BootPlan(
              model_id=row.repo_id,
              size_on_disk=row.size_on_disk,
              stop_first=True,
              success_line=f"✓ {row.repo_id} is serving",
          )
      def row_size(self, repo_id: str | None) -> int:
          ...  # moved verbatim from app.py:322-328, self._rows → self.rows
          #   (public: App.action_cold_start queries it — see below)
      def request_delete_model(self) -> None:
          ...  # moved verbatim from app.py:685-699; push_screen via
          #   self.app.push_screen(ConfirmScreen(...), self._on_delete_confirmed);
          #   self._log_app → self.tui.log_app; self._rows/self.cursor indexing
          #   over self.rows + queried table
      def _on_delete_confirmed(self, confirmed: bool | None) -> None:
          ...  # moved verbatim from app.py:701-707
      @work(exclusive=True, group="delete", thread=True)
      def _run_delete(self, row: ModelRow) -> None:
          ...  # moved verbatim from app.py:709-723; final self._rescan_models()
          #   → self.rescan(); self._log_app → self.tui.log_app;
          #   self.call_from_thread → self.tui.call_from_thread
  ```
  The ellipses above are descriptive only — implementations are verbatim moves of the cited app.py bodies with exactly the listed name substitutions and nothing else. For the restart branch of `request_load_swap`, the abort interaction becomes: `chat = self.tui.query_one(ChatPane)` guarded by `NoMatches`… — **concretely:** `if self.tui.chat_has_live_turn(): self.tui.cancel_chat_for_swap()` (two one-line App methods added in this phase; `chat_has_live_turn()` returns False on `NoMatches`, `cancel_chat_for_swap()` calls `ChatPane.abort()` and logs `"cancelled — model swapping"` yellow — their bodies are lifted from app.py:288-290 and finalized in Phase 5).
- `src/mlx_tui/app.py`:
  - `compose` Models tab becomes:
    ```python
    with TabPane("Models", id="models"):
        yield ModelsPane(id="models-pane")
    ```
  - `on_mount`: replace `self._rescan_models()` with `self.query_one(ModelsPane).rescan()`; tab-activated handler likewise.
  - Rename to public (bodies unchanged): `_effective_model`→`effective_model`, `_log_app`→`log_app`, `_set_swap_ui`→`set_swap_ui`, `_progress_line`→`progress_line`, `_refresh_markers`→deleted (poll calls the pane instead — see below), `_latest_avail_gib`→public attribute `latest_avail_gib` (assigned in `_poll` as today), `_current_model_supplier`→`current_model_supplier`, `_swap_machine`→`swap_machine`, `_server_ctl`→`server_ctl`.
  - Rename `_run_warm_swap`→`run_warm_swap` (still on App; body verbatim, internal `self._set_swap_ui`/`self._log_app`/`self._refresh_markers` refs updated to the pane calls: `self.call_from_thread(self.query_one(ModelsPane).refresh_markers)` guarded — implement as a small `def _refresh_models(self) -> None:` on App that try/queries `ModelsPane` and calls `refresh_markers()` catching `NoMatches`; workers call `self.call_from_thread(self._refresh_models)`).
  - Rename `_run_boot`→`run_boot` (Phase 2 product) — called by ModelsPane. Extend the `mlx_tui.swap` import with `BootPlan`.
  - `action_cold_start`: its `self._row_size(self.config.model)` argument becomes `self.query_one(ModelsPane).row_size(self.config.model)` (guarded by the same call being inside the already-validated path; wrap in try/`NoMatches` → `0`).
  - `_poll`: replace `self._refresh_markers()` with `self._refresh_models()`.
  - Delete from App: `request_load_swap`, `_row_size`, `request_delete_model`, `_on_delete_confirmed`, `_run_delete`, `_pending_delete_row`, `_rows` (all moved); drop now-unused imports (`delete_repos`, `CacheNotFound` stays only if still referenced — it won't be; `ConfirmScreen` moves to models_pane).
  - Add the two chat-coordination one-liners described above (`chat_has_live_turn`, `cancel_chat_for_swap`) with `NoMatches`-safe `query_one(ChatPane)` lookups — ChatPane does not exist until Phase 5, so in THIS phase they operate on the App-resident chat state still living on App (`self._chat.active_response is not None` / `self._abort_chat()`), and Phase 5 repoints their bodies. This keeps both phases independently green.
  - `table.py` action bodies switch (this phase) from `getattr(self.app, …)` to `self.app.query_one(ModelsPane).request_load_swap()` / `.request_delete_model()`. Import `ModelsPane` **locally inside each action method** — a module-top import cycles (`models_pane` imports `table` for `ModelsTable`), and a `TYPE_CHECKING`-only import is insufficient because `query_one(ModelsPane)` needs the class object at call time (it would raise `NameError` on first `enter`/`d`).
- `tests/integration/test_swap_integration.py`:
  - Patch target: `"mlx_tui.app.scan_models"` → `"mlx_tui.models_pane.scan_models"` (line 39).
  - `_select_row` (lines 46-55): `harness.app._rescan_models()` → `harness.app.query_one(ModelsPane).rescan()`; `a._rows == [ROW]` → `a.query_one(ModelsPane).rows == [ROW]`; add `from mlx_tui.models_pane import ModelsPane`.
  - `harness.app._current_model_supplier = …` → `harness.app.current_model_supplier = …` (lines 100, 148, 217, 273).
  - `harness.app._populate_table([ROW, big_row])` (line 327) → `harness.app.query_one(ModelsPane)._populate([ROW, big_row])`.
  - `harness.app._tracked_model` assertions (lines 74, 114, 160, 231) — `_tracked_model` stays a private App attr (only App workers write it): unchanged.
  - `harness.app._server_ctl = ctl` → `harness.app.server_ctl = ctl` (lines 219, 275, 330).
  - `harness.app._swap_machine` → `harness.app.swap_machine` (lines 73, 80, 88, 113, 159, 179, 230, 254, 284).
  - `harness.app._end_turn_ui()` (line 362) — unchanged this phase (still App method until Phase 5).
- `tests/conftest.py` — add to `AppHarness`:
  ```python
  def models_pane(self) -> ModelsPane:
      return self.app.query_one(ModelsPane)
  ```
  (import under `TYPE_CHECKING`-style local import to avoid a cycle: `from mlx_tui.models_pane import ModelsPane` at module top is safe — models_pane imports app only under TYPE_CHECKING.)

**Success Criteria:**

#### Automated Verification:
- [x] Swap integration green after seam repoint: `uv run pytest -v tests/integration/test_swap_integration.py` (11 passed)
- [x] Whole suite green: `uv run pytest -q` (180 passed)
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`
- [x] `grep -n "_rows\|request_load_swap\|request_delete_model" src/mlx_tui/app.py` returns no hits (fully moved)

> Deviations:
> - test_app_integration.py:47/65 also set `_current_model_supplier`; the rename
>   made those writes dead (pyrefly flagged), so they were repointed to
>   `current_model_supplier` like the swap-integration seams.
> - The prescribed function-level imports in table.py's actions trip the repo's
>   PLC0415 (`PL` rules are selected); kept the local import and added
>   `# noqa: PLC0415`.
> - Two app.py comment references to "request_load_swap" were reworded
>   ("the pane's load action" / "ModelsPane's load request") so the grep
>   criterion returns zero hits without leaving stale names.

#### Manual Verification:
- [ ] `uv run mlx-tui`: Models tab lists your cached models; `enter` on a row warm-loads it (✓ line in app log); `d` opens the confirm modal, `escape` keeps the model; progress line counts during a load

### Phase 5: Extract `ChatPane`

Chat-tab UI (stream/log/input, turn lifecycle, abort-with-socket-shutdown, transcript) moves into the pane; App's `escape` binding delegates.

**Changes:**
- create `src/mlx_tui/chat_pane.py`:
  ```python
  """Chat tab pane: streaming transcript, turn lifecycle, cancellation."""

  from __future__ import annotations

  import socket
  from typing import TYPE_CHECKING, Any, cast, override

  import httpx
  from rich.text import Text
  from textual import on, work
  from textual.app import ComposeResult
  from textual.containers import Vertical
  from textual.widgets import Input, RichLog, Static

  from mlx_tui.chat import ChatClient, error_detail
  from mlx_tui.history import trim_for_context

  if TYPE_CHECKING:
      from mlx_tui.app import MlxTuiApp

  _MAX_OUTPUT_TOKENS = 1024
  # Ceiling, in estimated tokens, for the window sent to the server; the local
  # transcript itself is kept whole.
  _MAX_CONTEXT_TOKENS_EST = 8_000


  class ChatPane(Vertical):
      """Owns #chat-stream, #chat-log and #chat-input plus the turn worker."""

      def __init__(self, **kwargs: Any) -> None:
          super().__init__(**kwargs)
          self.messages: list[dict[str, str]] = []
          self._cancel_requested: bool = False
          self._chat = ChatClient()

      @property
      def tui(self) -> MlxTuiApp:
          return cast("MlxTuiApp", self.app)

      @property
      def has_live_turn(self) -> bool:
          return self._chat.active_response is not None

      @override
      def compose(self) -> ComposeResult:
          yield Static("", id="chat-stream")
          yield RichLog(id="chat-log", markup=False, wrap=True)
          yield Input(placeholder="message…", id="chat-input")

      def _on_input_submitted(self, event: Input.Submitted) -> None:
          # verbatim from app.py:544-560 with:
          #   self._swap_machine.busy → self.tui.swap_machine.busy
          #   self._log_app → self.tui.log_app
          #   self._cold_tracker.consume_cold() → self.tui.cold_tracker.consume_cold()
          ...
      @work(exclusive=True, group="chat", thread=True)
      def _run_turn(self, messages: list[dict[str, str]], cold: bool) -> None:
          # verbatim from app.py:563-637 with:
          #   url/payload building unchanged (constants now module-level here);
          #   self._current_model_supplier() → self.tui.current_model_supplier()
          #   self.host / self.port → self.tui.host / self.tui.port
          #     (the URL at app.py:576 and every f"… :{self.port}" error line)
          #   self.call_from_thread → self.tui.call_from_thread
          #     (Widget has no call_from_thread in textual 8.2.8)
          #   self._update_stream / _write_system_line / _complete_turn_ui /
          #     _end_turn_ui resolve on self (same class)
          ...
      def abort(self) -> None:
          # verbatim from app.py:_abort_chat (640-655); public name
      def _update_stream(self, text: str) -> None: ...
      def _complete_turn_ui(self, full_text: str, stamp: str, cold: bool, notices: list[str]) -> None: ...
      def _write_system_line(self, message: str, style: str) -> None: ...
      def end_turn(self) -> None:
          # verbatim from app.py:_end_turn_ui (725-734);
          #   self._swap_machine.busy → self.tui.swap_machine.busy
  ```
  Ellipses are descriptive; bodies are verbatim moves of the cited app.py methods with exactly the listed substitutions. The stamp string, notices list, exception→message mapping, and the socket-shutdown comment all carry over byte-for-byte.
- `src/mlx_tui/app.py`:
  - `compose` Chat tab becomes:
    ```python
    with TabPane("Chat", id="chat"):
        yield ChatPane(id="chat-pane")
    ```
  - Delete from App: `_MAX_OUTPUT_TOKENS`, `_MAX_CONTEXT_TOKENS_EST`, `messages`, `_cancel_requested`, `_chat`, `_on_input_submitted`, `_run_turn`, `_abort_chat`, `action_cancel_chat`, `_update_stream`, `_complete_turn_ui`, `_write_system_line`, `_end_turn_ui`; drop now-unused imports (`socket`, `trim_for_context`, `Text` still needed? `format_status_line` output is a styled string — `Text` was only used by chat/log writers and `log_app` — `log_app` keeps using `Text`, keep the import).
  - Repoint the two coordination one-liners from Phase 4: `chat_has_live_turn()` → `return self._chat_pane_or_none() is not None and self._chat_pane_or_none().has_live_turn` style — concretely add:
    ```python
    def _chat_pane_or_none(self) -> ChatPane | None:
        try:
            return self.query_one(ChatPane)
        except NoMatches:
            return None

    def chat_has_live_turn(self) -> bool:
        pane = self._chat_pane_or_none()
        return pane is not None and pane.has_live_turn

    def cancel_chat_for_swap(self) -> None:
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane.abort()
            self.log_app("cancelled — model swapping", "yellow")
    ```
  - `BINDINGS`/`escape`: add `def action_cancel_chat(self) -> None: pane = self._chat_pane_or_none(); if pane is not None: pane.abort()` (binding row unchanged).
  - `set_swap_ui`: `live_turn = self._chat.active_response is not None` → `live_turn = self.chat_has_live_turn()`.
  - `__init__`: delete `self.messages/_cancel_requested/_chat` initializers.
- `tests/integration/test_app_integration.py`:
  - Replace every `len(a.messages) == N` predicate with `len(harness.chat_pane.messages) == N` via the new harness accessor; `harness.app.messages[...]` assertions likewise.
- `tests/integration/test_swap_integration.py`:
  - Line 351: `harness.app._chat.active_response = object()` → `harness.chat_pane()._chat.active_response = object()` (type-ignore retained).
  - Line 361: `harness.app._chat.active_response = None` → `harness.chat_pane()._chat.active_response = None`.
  - Line 362: `harness.app._end_turn_ui()` → `harness.chat_pane().end_turn()`.
- `tests/conftest.py` — add to `AppHarness`:
  ```python
  def chat_pane(self) -> ChatPane:
      return self.app.query_one("#chat-pane", ChatPane)
  ```
  `log_lines()` keeps working unchanged (it queries `#chat-log` by id, whose parent merely changed).

**Success Criteria:**

#### Automated Verification:
- [x] App integration green: `uv run pytest -v tests/integration/test_app_integration.py`
- [x] Swap integration green: `uv run pytest -v tests/integration/test_swap_integration.py`
- [x] Whole suite green: `uv run pytest -q` (180 passed)
- [x] Lint clean: `uv run ruff check .`
- [x] Types clean: `uv run pyrefly check`
- [x] `wc -l src/mlx_tui/app.py src/mlx_tui/chat_pane.py src/mlx_tui/models_pane.py` — app.py 262 (≤ 260 missed by 2), panes 198 / 277

> Deviations:
> - **User-directed:** the ~130-line swap/boot worker block (`run_warm_swap`,
>   `run_boot`, `_fail_swap`, `_progress_line`, crash-grace constants) moved off
>   App into `ModelsPane`, superseding Design Decisions row 1 ("swap/boot stays
>   on the coordinator"). Shared state (swap_machine, server_ctl, tracked model,
>   supplier, config, host/port) remains on App; workers reach it via the typed
>   `tui` surface. `App.action_cold_start` keeps its guards/entry transition and
>   delegates via `pane.run_boot(...)` (early-return if the pane is missing).
>   `_refresh_models` became public `refresh_models` for cross-object calls;
>   `_tracked_model` stayed private (pyrefly strict accepts the pane writes).
>   Consequence: `models_pane.py` (277) exceeds the plan's per-pane ≤ 230 bound,
>   which this note supersedes; app.py landed at 262.
> - test_app_integration predicates use `harness.chat_pane().messages` (the
>   accessor is a method); `_cold_tracker` was renamed public `cold_tracker`
>   per the Design Decisions public surface, tests repointed.

#### Manual Verification:
- [ ] `uv run mlx-tui` against the running server: prompt streams into one growing block, dim stamp lands (`N in · N out · X tok/s · TTFT Ys`), Esc mid-stream prints the dim cancelled line and re-enables input; ctrl+g opens `$EDITOR` on the config and reloads on save

### Phase 6: Final sweep

Documentation pass and full-gate finale; no behavioral edits expected.

**Changes:**
- `src/mlx_tui/app.py` — module docstring: "Textual coordinator: shared state, polling, swap/boot workers, bindings; tab UIs live in the pane modules."
- `src/mlx_tui/chat_pane.py`, `src/mlx_tui/models_pane.py` — verify module/class docstrings explain the pane↔App contract (panes render and handle their widgets; shared state and cross-pane orchestration live on `MlxTuiApp`, accessed via the typed `tui` property).
- Remove any import left dead by the preceding phases (ruff flags them anyway — zero-tolerance pass).
- Confirm `README.md` makes no file-layout claims that the split invalidated (it describes features, not modules — expected no-op; verify, don't edit unless stale).
- Final gate run of everything below, plus a line-count summary recorded in the PR description.

**Success Criteria:**

#### Automated Verification:
- [x] `uv run pytest -q` — all tests green (180 passed; baseline 158 + 22 added in Phases 1–3)
- [x] `uv run ruff check .`
- [x] `uv run ruff format --check src tests` — only the one pre-existing offender remains (`tests/integration/test_swap_integration.py`, hunks at lines ~247 and ~269; `ruff format` checks Python files only) unless separately fixed
- [x] `uv run pyrefly check` — 0 errors
- [x] `uv run python -c "from mlx_tui.app import main; print('entrypoint ok')"`

#### Manual Verification:
- [ ] Full smoke against a live `mlx_lm.server`: status dot green within ~2 s with model id + RSS; Models table glyphs/marker correct; warm load via `enter`; chat streams with stamp; Esc cancels; ctrl+s cold-boots from red; ctrl+g edits config; delete modal opens and dismisses — i.e., the refactor changed nothing observable

## Out of Scope

- Moving `main()`/argparse out of `app.py` (entry-point surface frozen by the v0 refactor plan).
- Any change to `mlx_tui/sse.py` parsing semantics, `swap.py` state graph, `status.py` classification rules, `config.py` parse strictness, `models.py` scanning/deletion logic.
- New features from the idea doc (HF search/download, params sidebar, sparkline, markdown rendering, persistence).
- Performance tuning (flush cadence, poll interval, psutil scan frequency).
- Fixing the one pre-existing `ruff format` offender (`tests/integration/test_swap_integration.py`; separate trivial commit if desired).
- CI workflows, coverage tooling.

## Risks & Mitigations

- **`@work` on plain widgets** — `@work` is supported for `App`/`Screen`/`Widget` in Textual 8.x (worker groups are node-scoped: `self.workers.cancel_group(self, "chat")` in the moved `abort()` correctly targets the pane's own group). `call_from_thread` is **not** on `Widget` in textual 8.2.8 (verified against the installed package) — that is not a risk but a fact baked into every phase's snippets: pane workers use `self.tui.call_from_thread(...)`. Residual verification when Phase 4 lands: confirm a worker actually starts on the widget; if it fails to start, fall back to keeping that worker method on App and having the pane call `self.tui.<worker>()` (mechanical, contained to the pane module).
- **`push_screen` is App/Screen-only** — ModelsPane therefore calls `self.app.push_screen(...)` with a pane-method callback (specified in Phase 4); if the callback's sender context matters, capture the pane explicitly in a closure.
- **Extra container level shifts layout** — wrapping TabPane children in a `Vertical` pane adds one nesting layer; IDs and the stylesheet are unchanged, so risk is low but real (e.g., `#chat-log { height: 1fr }` resolving against the pane instead of the TabPane) → compare rendered layout before/after each pane extraction (Phase 4/5 manual checks); if a height collapses, scope a fix in the pane's `DEFAULT_CSS` rather than touching `app.tcss`.
- **pyrefly strict on cross-class access** — panes read App state through the typed `tui` property and public renames specified per phase; if pyrefly still flags a protected-member access, widen that specific name to public rather than weakening the checker config.
- **Monkeypatch staleness** — `scan_models` is patched at its usage site by convention; the plan names the new target (`mlx_tui.models_pane.scan_models`) explicitly, and Phase 4's first criterion runs the swap-integration file alone to catch a stale target immediately.
- **Cross-thread ordering regressions during the boot merge** — `_fail_swap` reproduces the original transition-then-log ordering, and every user-visible string is asserted by the untouched swap-integration suite, which is the tripwire for Phase 2.
