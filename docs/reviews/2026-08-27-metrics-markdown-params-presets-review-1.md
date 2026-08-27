# Plan Review: MLX TUI — Metrics tab, Markdown chat, Params sidebar, System presets

**Date:** 2026-08-27
**Target:** docs/plans/2026-08-27-metrics-markdown-params-presets.md
**Review:** 1
**Verdict:** APPROVE (was REVISE — see Re-Review Pass 2)

## Assessment

The plan is unusually well-grounded in the current codebase — cited paths, line numbers, and test harnesses are mostly accurate and the four-phase sequencing is coherent. Two load-bearing implementation details will break as written (Markdown widget in RichLog, and the global Input.Submitted handler), and the `ctrl+shift+p` binding is terminal-unreliable; fixing those plus a handful of consistency and coverage gaps will make the plan implementable without mid-phase rework.

## Cross-Cutting Themes

**1. Widget vs Renderable confusion (Phases 2 & 3) — breaks UI at runtime.** Phase 2 writes a `textual.widgets.Markdown` Widget into a `RichLog`; Phase 3's `Collapsible` wiring assumes `Input.Submitted` scoping that the current handler doesn't have. Both are the same class of error: Textual widget APIs treated as generic renderables/events.

**2. Copy-paste divergence from `app.py`/`chat_pane.py` conventions.** The pane `tui` cast `chat_pane.py:35`, `call_from_thread` hop pattern `chat_pane.py:86`, and `_poll_in_flight` guard `app.py:189` are correctly cited, but the plan diverges by introducing lambdas that close over `query_one` without `NoMatches` guards, and by moving `_shade_for_ctx` `app.py:43` without unifying imports.

**3. Slice overlap between Config and preset state.** `AppConfig` gains `temperature/top_p/max_tokens/system` `config.py:12` in Phase 3, then Presets drive those same fields via `ChatPane._system_prompt` and `App.config` mutation. The single source of truth (pane inputs vs App config vs `presets.toml`) is ambiguous and the `system` field's persistence story contradicts the plan's own "no file write" note.

## Findings

### Critical

- **[Correctness] Phase 2: `log.write(Markdown(full_text))` with `textual.widgets.Markdown` is invalid** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:115-122` vs `src/mlx_tui/chat_pane.py:183` — `Markdown` at `textual.widgets._markdown.Markdown` is a `Widget` (`Widget` → `DOMNode` → `MessagePump`), not a Rich `RenderableType`. `RichLog.write()` `src/mlx_tui/app.py:18` / `textual.widgets._rich_log` expects `RenderableType | object` rendered via `Console`; writing a Widget renders as `Markdown()` literal and leaks a mounted widget that is never composed. The correct per-turn rendering is `from rich.markdown import Markdown as RichMarkdown` and `log.write(RichMarkdown(full_text))`. The plan's own alternative harness check `harness.chat_pane().query(Markdown)` would then fail because no `textual.widgets.Markdown` is ever mounted. Suggested fix: Phase 2 imports `rich.markdown.Markdown`, not `textual.widgets.Markdown`, and tests assert `rich.markdown.Markdown` renderable in `RichLog.lines` or substring rendering.

- **[Correctness] Phase 3: `@on(Input.Submitted)` without selector collides with param inputs** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:168` vs `src/mlx_tui/chat_pane.py:49` ` @on(Input.Submitted)` — the existing handler has no selector, so every `Input.Submitted` (including `#param-temp`, `#param-top-p`, `#param-max-tokens`) triggers `_on_input_submitted`, which appends a user message and starts a turn with the param string as chat text. Phase 3 adds `@on(Input.Submitted, "#param-temp")` handlers but never narrows the original one to `#chat-input`. Must change `chat_pane.py:49` to `@on(Input.Submitted, "#chat-input")`, and scope param handlers with selectors or a single `@on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")`.

- **[Correctness] Phase 1: `_poll` duplicate `memory_snapshot()` and `MemoryRecord` model typing** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:77` vs `src/mlx_tui/app.py:189-214` — the plan does `mem = memory_snapshot(); rss_gib` then `rec = MemoryRecord(ts=time.time(), model=model, ... rss_gib=rss_gib, avail_gib=mem.avail_gib)` after `self.latest_avail_gib = memory_snapshot().avail_gib` already fetched a fresh snapshot. Two independent snapshots can diverge by one poll window; reuse the snapshot already taken for `_render_status`. Also `MemoryRecord.model: str | None` with `effective_model() or "—"` later collapsed to `"—"` string is inconsistent: either preserve `None` for "unknown" or normalise at `MemoryStore.add` time, not per-field ad hoc. Suggested: `snapshot = memory_snapshot()` once per `_poll`, derive `rss_gib` and `rec` from it.

- **[Correctness] Phase 4: `BINDINGS` `("ctrl+shift+p", "cycle_preset_back", ...)` is terminal-unreliable** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:216` — on most macOS/Linux terminals `Ctrl+Shift+P` emits the same bytes as `Ctrl+P` (0x10), so `action_cycle_preset_back` never fires. Textual `8.2.8` binding parser accepts the string but the OS never delivers it. Replaces a working forward cycle with a dead backward cycle. Suggested: use a distinct, non-shift binding such as `ctrl+o` / `ctrl+alt+p` or reuse `ctrl+p` with a direction flag and document the terminal limitation. At minimum add manual verification fallback and a conditional guard `if self.presets` before index arithmetic.

### Major

- **[Architecture] Phase 3: `ChatPane` `Collapsible` nesting changes layout but `DEFAULT_CSS` isn't updated for it** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:156-165` vs `src/mlx_tui/app.py:70-92` `DEFAULT_CSS` — wrapping chat children in `Vertical > Collapsible > Inputs` plus `#chat-stream, #chat-log, #chat-input` without CSS for `#params-collapsible` / `#param-*` leaves the collapsible with default `height:auto` and `Input` widths unstyled; the plan's manual verification "typing 2.5 clamps to 2.0" may be clipped on `height:1fr` logic. Either add explicit CSS (`#params-collapsible { height: auto; }` etc.) or keep `ChatPane` as `Vertical` with `Collapsible` as first child and set `#chat-log { height: 1fr }` defensively.

- **[Architecture] Phase 1: `MetricsPane.refresh` spec duplicates table population logic and leaks `_shade_for_ctx`** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:62-68` vs `src/mlx_tui/app.py:43-59` — copying `_shade_for_ctx` into `metrics_pane.py` duplicates quartile logic that is already unit-tested at `tests/unit/test_history.py:401`. The plan says "either way delete old method" but keeps the tested helper in `app.py` only to move it. Better: keep `_shade_for_ctx` in `history.py` (pure, stdlib-adjacent) or import it from `app.py` and avoid duplication. Also `MetricsPane.refresh` as sketched does `for r in tui.history.all_records() sorted by ts` without capping; `all_records()` `history.py:56` can be up to `models * 64` entries, and the DataTable would grow unbounded across model switches. Plan text says "capped 64" but code sketch doesn't cap. Pin to `sorted(all_records)[-64:]` or `tui.memory_store.series()[-64:]` equivalent.

- **[Plan Mechanics] Phase 3: `AppConfig` extension leaves type/naming inconsistent across phases** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:146-149` vs `src/mlx_tui/config.py:45-52` `_KEY_TYPES` — Phase 3 adds `temperature: float | None` etc. but Phase 1 already added `MemoryRecord(total_gib: float)` and Phase 4's `_preset_from_mapping` coerces `int → float` for `temperature/top_p`. Config side's `_from_mapping` uses exact `type(value) is expected` (`config.py:63`) and would reject `temperature = 1` (int) from TOML while presets accepts it; the two parsers diverge. Either normalise both (coerce `int` → `float` for temperature/top_p in `config.py`) or keep exact checks and document that `temperature` must be `0.7` not `1`.

- **[Test Coverage] Phase 2 markdown harness test queries the wrong widget type** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:126-127` — `assert len(harness.chat_pane().query(Markdown)) == 1` will pass only if `Markdown` is `textual.widgets.Markdown` and it was mounted; once fixed to `rich.markdown.Markdown`, `query(Markdown)` returns 0 because rich Markdown is a renderable inside `RichLog`, not a Widget. Test should assert `any(isinstance(renderable, RichMarkdown) for line in log.lines)` via `RichLog` internals or substring `"# hi"` rendered as heading (rich markdown renders headings with style, not raw `#`). Needs a concrete, renderable-aware assertion.

- **[Correctness] Phase 1: cross-thread `query_one(MetricsPane).refresh()` via `call_from_thread(lambda: ...)` captures `MetricsPane` class at import time before it exists** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:81-82` — `src/mlx_tui/models_pane.py:1` imports nothing from `metrics_pane`; the lambda `lambda: self.tui.query_one(MetricsPane).refresh()` evaluated on the UI thread still requires `MetricsPane` to be imported in `models_pane.py`/`chat_pane.py`. The plan only adds the import to `app.py:72`. Either add `from mlx_tui.metrics_pane import MetricsPane` to both pane modules or add a helper `MlxTuiApp._refresh_metrics()` in `app.py` and have panes call `self.tui.call_from_thread(self.tui._refresh_metrics)`.

- **[Security] Phase 4: `presets.toml` loader `write_template` and `presets_path` ignore existing `config_path` XDG logic drift** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:206-212` vs `src/mlx_tui/config.py:24-27` — `config_path()` honours `$XDG_CONFIG_HOME` else `~/.config`; the sketched `presets_path()` duplicates that logic with `os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")` but uses `os` not `os.environ` consistency and would diverge if `config.py` later changes precedence. Reuse `config_path().parent / "presets.toml"` instead of reimplementing.

### Minor

- **[Plan Mechanics] Success criteria shell commands drift: `ruff format --check src tests` vs `ruff check .`** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:93-94` vs `234-235` — Phase 1 lists `uv run ruff format --check src tests` while Phase 4 lists `uv run ruff check . && uv run ruff format --check src tests`; the latter is correct per `pyproject.toml:29` (`ruff check` lints, `ruff format` formats). Harmonise to `uv run ruff check .` and `uv run ruff format --check .` across all phases.

- **[Test Coverage] Phase 1 `MemoryStore` tests omit thread-hop and `MemoryRecord` frozen shape** — the plan adds 6 memory tests mirroring `render_sparkline` but doesn't pin `MemoryRecord` frozen dataclass (9-field equivalent) nor that `MemoryStore` mutations from `@work(thread=True)` must hop via `call_from_thread`. Add one frozen test like `test_memory_record_is_frozen` and note `Not thread-safe` docstring mirroring `history.py:33`.

- **[Performance] `render_memory_sparkline` bucket description "equally with braille 2×4 packing" is ambiguous for `None` `rss_gib`** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:53` — when `rss_gib is None` (no pid `app.py:200-203`), skipping `None` entries changes `n` and legend `· {n} samples` drifts from time axis. Clarify: skip `None` for `lo/hi` calculation but still count the sample as blank column, or filter `None` entirely and note that poll ticks with no pid produce no sparkline point.

- **[Architecture] `MetricsPane.refresh` is a method name that shadows `Widget.refresh`** `docs/plans/2026-08-27-metrics-markdown-params-presets.md:63` `def refresh(self)->None` — `Widget.refresh()` is the Textual repaint trigger; overriding it to mean "rebuild table + sparklines" is confusing and risks infinite recursion if `super().refresh()` is ever called. Name it `refresh_metrics()` / `rebuild()` and call it explicitly from `app.py:77`.

- **[Plan Mechanics] Plan cites `idea.md:193` rejection of Metrics tab but current `docs/idea.md:188-194` History is footer strip, not Metric** — citation is correct intent-wise, but `idea.md:188` explicitly says "Once the status bar has memory... there's nothing left for a standalone Metrics tab." The plan's Decision "Idea originally rejected Metrics tab" should cite `idea.md:188-194` not `idea.md:193` for reviewer traceability.

- **[API/Compatibility] `chat_pane.py:156-165` compose `Collapsible(title="params", ...)` title casing** — `Collapsible` `title` `textual 8.2.8` renders as header text verbatim; `"params"` lowercase will appear as such. Use `title="Params"` for UI consistency with `TabPane("Models")` / `TabPane("Chat")` `app.py:122-125`.

### Suggestions

- Consider `history.py` owning both stores but `MemoryStore` using `deque(maxlen=256)` 8.5 min window is short for "degrade at long context" — a 1-hour window (`maxlen=1800` at 2 s) costs ~1800*5*8 bytes trivially and makes the sparkline useful over a real session. Call it out as a one-line tuning knob, not a design change.

- Preset cycling could reuse the existing `log_app(..., "dim")` pattern `app.py:385` for `preset: a` notices rather than introducing `_preset_display` no-op `chat_pane.py:223`; the no-op can be deleted.

- For manual verification, add a `curl`-style payload inspection via `harness.server.requests` already used in `tests/integration/test_app_integration.py:66-68` — Phase 3's payload test already does this; replicate the same inspection in manual steps (`--verbose` is vague).

## Strengths

- Cites exact file:line anchors and verifies the Textual version (`uv run python -c "from textual.widgets import Markdown, Collapsible"`), then stays inside the existing `HistoryStore`/`AppConfig`/`TabbedContent` patterns instead of introducing new frameworks.
- Keeps `history.py` stdlib-only and braille rendering pure, reusing `render_sparkline`/`sparkline_visible`/`_shade_for_ctx` correctly — no new deps, as `pyproject.toml:7` promises.
- Threading discipline is explicit: `call_from_thread` for `HistoryStore.add` from `@work(thread=True)` `chat_pane.py:110` and event-loop-direct `MetricsPane` refresh from `_poll` `app.py:189` — mirrors the proven `history.py:33` comment.
- Phased success criteria split Automated vs Manual with concrete `uv run pytest -k memory/metrics/markdown/params/preset`, `ruff`, and `pyrefly` gates; existing baseline `225 passed, ruff check clean, pyrefly 0 errors, textual 8.2.8` is verified in the review pass.

## Recommended Changes

1. **Fix Phase 2 Markdown (Critical):** Change `chat_pane.py` import to `from rich.markdown import Markdown` and `log.write(Markdown(full_text))`; update Phase 2 Harness assertion to inspect `RichLog` renderables, not `query(Markdown)` widget count. Map to Critical 1 + Major 4.

2. **Fix Phase 3 Input routing (Critical):** Narrow `chat_pane.py:49` to `@on(Input.Submitted, "#chat-input")`; consolidate param inputs under one selector handler that clamps `temp 0.0..2.0` `top_p 0.0..1.0` `max_tokens 1..16384` and rewrites `event.input.value`; add `Collapsible` CSS in `app.py:70-92`. Map to Critical 2 + Major 1.

3. **Fix Phase 4 binding (Critical):** Replace `ctrl+shift+p` with a non-shift binding (e.g. `ctrl+o` / `ctrl+alt+p`) and keep `ctrl+p` for forward cycle; guard `action_cycle_preset*` with `if not self.presets` and `presets_path = config_path().parent / "presets.toml"`. Map to Critical 4 + Major 6.

4. **Harmonise Phase 1 metrics refresh:** Rename `MetricsPane.refresh` → `refresh_metrics`, guard `query_one` with `try: ... except NoMatches`, cap table to last 64 `all_records()[-64:]`, and add `_refresh_metrics()` helper in `app.py` for panes to call via `call_from_thread`. Reuse single `memory_snapshot()` per `_poll`. Map to Critical 3 + Major 2/5.

5. **Unify type coercion:** Normalise `temperature/top_p` int→float in both `config.py:_from_mapping` and `presets.py:_preset_from_mapping` with exact `type is` guards for `bool`, and document rejected vs coerced cases. Map to Major 3.

6. **Tidy Plan Mechanics:** Align lint/format commands to `uv run ruff check .` / `uv run ruff format --check .`, fix `idea.md` citation, capitalise `Collapsible(title="Params")`, and list which `test_history_strip_*` tests are removed vs renamed in Phase 1 success criteria.

## Re-Review (Pass 2)

**Date:** 2026-08-27
**Verdict:** APPROVE

All targeted plan edits were applied to `docs/plans/2026-08-27-metrics-markdown-params-presets.md:1`. Re-checked against `src/mlx_tui/app.py:70`, `src/mlx_tui/chat_pane.py:49`, `src/mlx_tui/config.py:45`, `src/mlx_tui/history.py:86`.

### Previous Findings

- **[Correctness] Phase 2: `log.write(Markdown)` with `textual.widgets.Markdown` — Resolved.** Phase 2 now imports `from rich.markdown import Markdown` and notes "Rich renderable into `RichLog`, not `textual.widgets.Markdown` widget" `docs/plans/2026-08-27-metrics-markdown-params-presets.md:106`; harness check inspects `RichLog` `lines`/`_deferred_renders` for `rich.markdown.Markdown` and explicitly warns not to use `query(Markdown)`. Manual verification updated to `rich.markdown.Markdown` path.

- **[Correctness] Phase 3: `@on(Input.Submitted)` without selector — Resolved.** Plan now narrows existing handler to `@on(Input.Submitted, "#chat-input")` and consolidates param validation into single `@on(Input.Submitted, "#param-temp, #param-top-p, #param-max-tokens")` handler `docs/plans/2026-08-27-metrics-markdown-params-presets.md:168-170`.

- **[Correctness] Phase 1: `_poll` duplicate `memory_snapshot()` — Resolved.** `_poll` now reuses single `snapshot = memory_snapshot()` for `latest_avail_gib` and `MemoryRecord` `docs/plans/2026-08-27-metrics-markdown-params-presets.md:77`.

- **[Correctness] Phase 4: `ctrl+shift+p` terminal-unreliable — Resolved.** `BINDINGS` changed to `("ctrl+p", ...), ("ctrl+o", "cycle_preset_back", ...)` `docs/plans/2026-08-27-metrics-markdown-params-presets.md:219`; overview, design decisions, tests, and manual verification all updated to `ctrl+p`/`ctrl+o` with note that `ctrl+shift+p` collides at 0x10.

- **[Architecture] Phase 3 Collapsible CSS — Resolved.** `DEFAULT_CSS` now adds `#params-collapsible { height: auto; }` + `#chat-log { height: 1fr; }` defensively `docs/plans/2026-08-27-metrics-markdown-params-presets.md:71`; `compose()` clarified as `ChatPane` is `Vertical` with `Collapsible` first child, title capitalised to `"Params"`.

- **[Architecture] Phase 1 `MetricsPane.refresh` duplication — Resolved.** Renamed to `refresh_metrics` to avoid shadowing `Widget.refresh` `docs/plans/2026-08-27-metrics-markdown-params-presets.md:63`, `_shade_for_ctx` kept in `app.py` and imported (no duplication), table capped to `sorted(all_records)[-64:]` with explicit note `all_records can be models*64`.

- **[Plan Mechanics] Phase 3 `AppConfig` type coercion — Resolved.** `_from_mapping` now notes unified `int → float` coercion for `temperature`/`top_p` with `type is int` bool guard `docs/plans/2026-08-27-metrics-markdown-params-presets.md:151`, mirroring `presets.py:217`.

- **[Test Coverage] Phase 2 markdown harness test — Resolved.** Uses RichLog renderable inspection, not `query(Markdown)` widget count `docs/plans/2026-08-27-metrics-markdown-params-presets.md:127-128`.

- **[Correctness] Phase 1 cross-thread `query_one(MetricsPane)` — Resolved.** Added `MlxTuiApp._refresh_metrics()` helper `docs/plans/2026-08-27-metrics-markdown-params-presets.md:78` and changed both `models_pane.py`/`chat_pane.py` to `call_from_thread(self.tui._refresh_metrics)` (NoMatches-guarded), avoiding class capture in panes.

- **[Security] Phase 4 `presets_path` XDG drift — Resolved.** Now `def presets_path()->Path: return config_path().parent / "presets.toml"` `docs/plans/2026-08-27-metrics-markdown-params-presets.md:209` with imports via `from mlx_tui.config import config_path`; `PRESETS_TEMPLATE` updated to `ctrl+p forward, ctrl+o back`.

- **[Plan Mechanics] Success criteria drift — Resolved.** Phases 1-3 now split `Lint clean: uv run ruff check .` + `Format clean: uv run ruff format --check .` `docs/plans/2026-08-27-metrics-markdown-params-presets.md:93-94,135-136,188-189`; Phase 4 kept `&&` combined form, all consistent with `pyproject.toml:29`.

- **[Test Coverage] `MemoryStore` frozen test — Resolved.** Added `test_memory_record_is_frozen` plus `Not thread-safe` docstring note `docs/plans/2026-08-27-metrics-markdown-params-presets.md:53,84`.

- **[Performance] `render_memory_sparkline` `None` ambiguity — Resolved.** Clarified filter `None` for `lo/hi` but blank column for legend count `docs/plans/2026-08-27-metrics-markdown-params-presets.md:53`.

- **[Architecture] `MetricsPane.refresh` shadowing — Resolved** (see rename above).

- **[Plan Mechanics] `idea.md:193` citation — Resolved.** Changed to `idea.md:188-194` with parenthetical `History is footer strip, not standalone tab` `docs/plans/2026-08-27-metrics-markdown-params-presets.md:21`.

- **[API/Compatibility] `Collapsible(title="params")` casing — Resolved.** Now `title="Params"` `docs/plans/2026-08-27-metrics-markdown-params-presets.md:159`.

### New Issues Introduced

- None — all edits were targeted string replacements; no new placeholders or cross-phase naming drift introduced. `rich.markdown.Markdown` is available via `rich` (transitive dep of `textual 8.2.8`) so `pyproject.toml:7` unchanged remains accurate.

### Verdict Rationale

No Critical or Major findings remain; Minor/Suggestions either resolved or accepted. Plan is implementable as written phase by phase.

