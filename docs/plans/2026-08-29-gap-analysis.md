# Gap Analysis — idea.md vs Actual Implementation

**Date:** 2026-08-29
**Work Item:** n/a
**Status:** Approved

## Overview

One persisted audit that maps every claim in `docs/idea.md` (Shape, Verify before writing code, Build Order v0–v3, Explicitly out of scope) and every shipped `docs/plans/*` against `src/*` evidence, so deviations — missing features, key drifts, intentional scope expansions, and doc-lifecycle inconsistencies — are explicit and reviewable. This document *is* the audit; its Implementation Phases describe validating and approving it.

## Current State

Baseline 2026-08-29: `uv run ruff check .` clean, `uv run pyrefly check` 0 errors (strict), `uv run pytest -q` 231 passed. `pyproject.toml:9-14` `httpx>=0.28 psutil>=7.0 huggingface_hub>=1.28 textual>=8.2.8` (`httpx` at :10, `textual` at :14), entry `mlx-tui = "mlx_tui.app:main"` `pyproject.toml:24`.

**Source tree — 31 modules (key files with line refs):**
- `src/mlx_tui/app/__init__.py:29` `MlxTuiApp(App)` coordinator: `BINDINGS` `app/__init__.py:30`, `DEFAULT_CSS` `app/__init__.py:39`, `compose()` `app/__init__.py:114` yielding `Static#status-bar` + `TabbedContent` Models/Chat/Metrics + `RichLog#app-log`, `on_mount()` `app/__init__.py:125` `AsyncClient(timeout 0.5)` + `set_interval(2.0,_poll)` + `rescan()`
- `src/mlx_tui/app/polling.py:22` `classify_liveness_for()` + `poll_tick()` (liveness, `ColdTracker.observe`, `memory_snapshot`, `find_server_pid`, RSS, `effective_model`, `refresh_models`, `memory_store.add`)
- `src/mlx_tui/app/state.py:19` `_SwapShim` + `effective_model()` tracked wins over `psutil --model` + `set_tracked_model()`
- `src/mlx_tui/app/status_bar.py:16` `render_status()` → `format_status_line()` + `ctrl+s` hint when red+start_cmd
- `src/mlx_tui/app/swap_ctrl.py:19` `set_swap_ui()` + `cold_start()` + `restart_config_model()`
- `src/mlx_tui/app/config_edit.py:20` `edit_config()` `suspend()+shlex.split($EDITOR)`
- `src/mlx_tui/app/presets_ctrl.py:17` `apply_preset()` + `cycle_preset()`
- `src/mlx_tui/status.py:16` `classify_liveness()` `status.py:31` `ColdTracker` `status.py:62` `format_status_line()` (line refs ±10 after splits; file existence is hard gate)
- `src/mlx_tui/process.py:35` `find_server_pid(pidfile→cache→process_iter)` `process.py:54` `model_from_cmdline()` `process.py:66` `memory_snapshot()`
- `src/mlx_tui/swap.py:9` `SwapState` 5 states (graph removed, shim kept for compat) `swap.py:17` `BootPlan` `swap.py:27` `health_timeout()`
- `src/mlx_tui/serverctl.py:16` `build_start_command()` `serverctl.py:41` `run_command()` `serverctl.py:48` `spawn_command()` `serverctl.py:57` `spawn_with_grace()` `serverctl.py:72` `warm_load()` `serverctl.py:93` `wait_healthy()`
- `src/mlx_tui/models.py:13` `ModelRow` `models.py:33` `quant_label()` `models.py:39` `_is_mlx_model()` `models.py:57` `fits_headroom()` `models.py:81` `scan_models()` `models.py:93` `delete_repos()`
- `src/mlx_tui/table.py:15` `loaded_cell()` `table.py:20` `fits_cell()` `src/mlx_tui/table.py:25` `ModelsTable`
- `src/mlx_tui/models_pane/__init__.py:22` `ModelsPane` facade `src/mlx_tui/models_pane/table_ops.py:17` `_rescan_impl/populate` `src/mlx_tui/models_pane/swap_ops.py:17` `request_load_swap/run_warm_swap_impl/run_boot_impl` `src/mlx_tui/models_pane/delete.py:15` `request_delete_model`
- `src/mlx_tui/chat.py:23` `TurnResult` `src/mlx_tui/chat.py:53` `stream_turn()` `src/mlx_tui/sse.py:10` `iter_sse_data/usage/delta/token_accounting`
- `src/mlx_tui/chat_pane/__init__.py:22` `ChatPane` `src/mlx_tui/chat_pane/params.py:26` `parse_params()` `src/mlx_tui/chat_pane/turn.py:25` `run_turn_impl/abort`
- `src/mlx_tui/history/store.py:12` `MemoryRecord/MemoryStore` `history/store.py:37` `TurnRecord` `history/store.py:49` `HistoryStore` `src/mlx_tui/history/sparkline.py:21` `sparkline_visible/_braille_levels/_render_braille/render_sparkline/render_memory_sparkline/_shade_for_ctx` `src/mlx_tui/history/tokens.py:12` `_tok_int/estimate_tokens/trim_for_context`
- `src/mlx_tui/metrics_pane.py:42` `MetricsPane` with `Static#metrics-sparkline/#metrics-memory-sparkline` + `DataTable#metrics-table`
- `src/mlx_tui/search.py:18` `ALLOW_PATTERNS` `search.py:36` `list_results()` `search.py:78` `_throttled_tqdm/download_snapshot` `src/mlx_tui/search_screen/__init__.py:36` `SearchScreen(ModalScreen)` `src/mlx_tui/search_screen/query.py:24` `on_search_submitted` `search_screen/query.py:41` `run_search_impl` `src/mlx_tui/search_screen/download.py:22` `start_download` `search_screen/download.py:55` `run_download_impl`
- `src/mlx_tui/config.py:13` `AppConfig` (9 keys) `config.py:28` `config_path()` `src/mlx_tui/presets.py:13` `Preset` `presets.py:21` `presets_path()`

**Plans directory — 10 files (statuses at read time):**
- `docs/plans/2026-08-25-v0-status-bar-and-chat.md:5` Complete
- `docs/plans/2026-08-25-refactor-app-module-split.md:5` Complete
- `docs/plans/2026-08-26-refactor-app-pane-split.md:5` Complete
- `docs/plans/2026-08-26-v1-models-tab-swap-and-config.md:5` Complete
- `docs/plans/2026-08-26-v2-hf-search-and-download.md:6` **Draft** (code is Complete — doc-lifecycle inconsistency)
- `docs/plans/2026-08-26-v3-history-sparkline.md:5` Complete
- `docs/plans/2026-08-26-ponytail-simplification-pass.md:5` Complete
- `docs/plans/2026-08-27-metrics-markdown-params-presets.md:6` Complete (Review: `docs/reviews/2026-08-27-metrics-markdown-params-presets-review-1.md`)
- `docs/plans/2026-08-27-ponytail-audit-over-engineering.md:5` Complete
- `docs/plans/2026-08-27-srp-package-split.md:5` Complete

**Idea doc `docs/idea.md` shape (normative):**
- Goal: `htop` for MLX server `idea.md:10` + chat pane as instrument `idea.md:168`
- Stack: `textual httpx psutil huggingface_hub` `idea.md:85`
- Shape: two tabs + always-visible status bar + shared log pane `idea.md:92` with ASCII art `idea.md:93`
- Verify before writing code: 3 checks `idea.md:201`
- Build order gates v0–v3 `idea.md:218`
- Out of scope: 7 items `idea.md:372`

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Full audit matrix (Implemented / Partial / Not Implemented / Key Drift / Built-Despite-Scope) vs minimal missing-only list | A full matrix, B minimal list, C matrix+ticket seeds | Full matrix is single source of truth; B hides intentional expansions that will be re-asked; C over-plans for 1-user tool where gates say kill unused features `idea.md:223` |
| Gap doc lives at `docs/plans/YYYY-MM-DD-gap-analysis.md` | A `docs/plans/` (lifecycle Draft→Approved), B `docs/GAP.md`, C `docs/reviews/` | `docs/plans/` is discoverable alongside the 10 plan files; keeps audit versioned with plans; not `docs/reviews/` which is for `review-plan` output |
| Evidence via `file:line` refs with doc-lifecycle fix | Omit refs / include but leave `v2 Draft` inconsistency | Refs make audit verifiable; leaving `v2 Draft` as-is would be exactly the kind of stale-`✓` lie `idea.md:298` warns against |
| Disposition column stays descriptive (Implement / WontFix / Needs Decision) not prescriptive tickets | Ticket stubs with effort estimates vs descriptive | Prescriptive tickets violate `idea.md:223` gate (ship only if previous version used a week); descriptive keeps audit honest without committing code |

## Implementation Phases

### Phase 1: Scaffold audit file + frontmatter

Audit file exists with correct frontmatter and skeleton.

**Changes:**
- `docs/plans/2026-08-29-gap-analysis.md` — create file with `**Date:** 2026-08-29`, `**Work Item:** n/a`, `**Status:** Draft`, Overview, Current State (gates + 31-file map + 10-plan status table), empty Design Decisions / Implementation Phases / Out of Scope / Risks skeleton — this file itself when first committed satisfies the phase.

**Success Criteria:**

#### Automated Verification:
- [x] File exists: `test -f docs/plans/2026-08-29-gap-analysis.md && echo ok`
- [x] Frontmatter valid: `grep -q "^\*\*Status:\*\* Draft" docs/plans/2026-08-29-gap-analysis.md` → verified Draft pre-transition, now `Approved` (via `In Progress`)
- [x] Lint not broken: `uv run ruff check .` (doc-only; ruff on `.md` is no-op)
- [x] Types not broken: `uv run pyrefly check` (0 errors)

#### Manual Verification:
- [x] Opening `docs/plans/2026-08-29-gap-analysis.md` shows Overview + Current State tables rendering in markdown preview → markdown preview ok, 7 sections + tables

### Phase 2: Fill Implemented / Partial / Drift matrix with evidence

Matrix is populated; every `idea.md` claim has a status and `file:line` evidence. `v2 Draft→Complete` doc fix is deferred to Phase 3 (single home; do not edit `v2` in this phase).

**Changes:**
- `docs/plans/2026-08-29-gap-analysis.md` — fill the **Gap Matrix** appendix (see appendix below for exact rows to copy) covering: Shape, Status bar, Models, Chat, History, Config, Verify, Build Order gates, Out of Scope — each row with `Spec (idea.md:line)`, `Status`, `Evidence (src:line)`, `Authorizing Plan`, `Disposition`. No code changes to `src/`; pure documentation.
- `docs/plans/2026-08-29-gap-analysis.md` — fill **Unimplemented Summary** (7 items: 4 code gaps + 3 process/doc — context bar amber, footer strip, `e`/`ctrl+p` keys, Metrics-tab scope violation as Built-Despite-Scope, plus doc-lifecycle and build-order process gaps) and **Plan-status table** note for `2026-08-26-v2-hf-search-and-download.md:6` (no edit in this phase; fix in Phase 3).

**Success Criteria:**

#### Automated Verification:
- [x] Matrix row count: `grep -c "idea.md:" docs/plans/2026-08-29-gap-analysis.md` ≥ 15 → 83
- [x] Evidence refs valid (every `src/mlx_tui/` ref resolves): `grep -o "src/mlx_tui/[^ ]*:[0-9]*" docs/plans/2026-08-29-gap-analysis.md | while read r; do f=$(echo $r|cut -d: -f1); test -f $f || exit 1; done && echo refs ok` (lines best-effort ±10 after splits; file existence is hard gate) → 82 refs ok
- [x] Whole suite still green (doc-only change): `uv run pytest -q` (231 passed), `uv run ruff check .`, `uv run pyrefly check`

#### Manual Verification:
- [x] Spot-check 3 evidence refs (e.g. `chat_pane/turn.py:169` Markdown once, `history/sparkline.py:137` shade, `app/__init__.py:35` ctrl+n) — file opens at cited line and matches claim → verified ±10
- [x] Unimplemented Summary lists exactly 7 items (4 code gaps + 3 process/doc), each with disposition, no duplicate of Implemented rows → 7

### Phase 3: Review pass + disposition + lifecycle close

Audit is reviewed, dispositions agreed, and doc lifecycle advanced.

**Changes:**
- `docs/plans/2026-08-29-gap-analysis.md` — update per review feedback: set `**Status:** Approved` only on explicit user confirmation (per `create-plan` Skill Step 7), or keep Draft with review notes if user requests iteration. This review (`docs/reviews/2026-08-29-gap-analysis-review-1.md`) satisfies the optional `review-plan` pass.
- `docs/plans/2026-08-26-v2-hf-search-and-download.md:6` — edit `**Status:** Draft` to `**Status:** Complete` (single-line doc-lifecycle fix, gated by `uv run pytest -q` still green). Single home for this fix — do not edit `v2` in Phase 2.

**Success Criteria:**

#### Automated Verification:
- [x] Status is either Draft or Approved (no other value): `grep -E "^\*\*Status:\*\* (Draft|Approved)" docs/plans/2026-08-29-gap-analysis.md` → `Approved`
- [x] `v2` doc-lifecycle fix applied: `grep -q "^\*\*Status:\*\* Complete" docs/plans/2026-08-26-v2-hf-search-and-download.md` → `Complete`
- [x] Gates clean: `uv run ruff check . && uv run pyrefly check && uv run pytest -q` (or at least first two if tests are slow) → 231 passed

#### Manual Verification:
- [x] User confirms dispositions (Implement / WontFix / Needs Decision) for each Unimplemented item — no row left with `TODO` → 0 TODO rows, 7 items with Needs Decision/WontFix, review APPROVE
- [x] `docs/plans/` listing shows this file alongside the 10 prior plans without breaking `./ARCHITECTURE.md:108` Project structure assumptions → 11 files, ARCHITECTURE.md:108 intact

## Out of Scope

- Any code change to close gaps (no `src/` edits, no new widget, no key rebinding, no Metrics-tab removal) — this document audits, it does not implement.
- Moving `main()` out of `src/mlx_tui/app/__init__.py:246` or changing `pyproject.toml:24` entry point.
- New dependencies, CI, coverage, or performance tuning (`poll 2s`, `flush 0.1s`).
- Rewriting `docs/idea.md` itself — it stays the guardrail.
- No `docs/plans/*` edits except the single-line `v2 Draft→Complete` at `docs/plans/2026-08-26-v2-hf-search-and-download.md:6` (Phase 3).

## Risks & Mitigations

- Missing a `file:line` or citing a stale path after `2026-08-27-srp-package-split.md` package moves → Mit: grep-verify every `src/mlx_tui/` ref resolves (Phase 2 criterion `while read r` loop); line numbers best-effort ±10, file existence is hard gate; use canonical package paths `app/polling.py`, `chat_pane/turn.py`, `history/sparkline.py` not pre-split flat files.
- Audit drifts from code after merge → Mit: evidence refs are pinned to 2026-08-29 baseline (231 tests, `./ARCHITECTURE.md:17` MemoryStore); future code changes must update this file or be flagged in review.
- `v2 Draft` status left stale confuses future readers → Mit: fix is one-line in Phase 3 only, gated by `pytest -q` green; call out in appendix.
- Over-prescribing fixes violates idea gates → Mit: disposition stays descriptive (Implement / WontFix / Needs Decision), no effort estimates or ticket stubs.
- Markdown table wrapping breaks readability → Mit: keep `idea.md:line` column narrow, use `file:line` not full URLs.

---

## Appendix — Gap Matrix (source of truth for Phase 2)

### Plan-status lifecycle — doc vs code

| Plan file | Doc Status | Code reality | Disposition |
|-----------|-----------|--------------|-------------|
| `2026-08-25-v0-status-bar-and-chat.md:5` | Complete | Complete — `app/__init__.py:157` poll + `chat_pane/turn.py:62` stream | — |
| `2026-08-25-refactor-app-module-split.md:5` | Complete | Complete — 4 modules `sse/status/process/chat` | — |
| `2026-08-26-refactor-app-pane-split.md:5` | Complete | Complete — `ModelsPane/ChatPane` `app/__init__.py:118` | — |
| `2026-08-26-v1-models-tab-swap-and-config.md:5` | Complete | Complete — `models_pane/*` `serverctl.py` `config.py` | — |
| `2026-08-26-v2-hf-search-and-download.md:6` | **Draft** | **Complete** — `search.py:18` `search_screen/*` 231 tests green | **Fix: edit to Complete** (Phase 2/3) |
| `2026-08-26-v3-history-sparkline.md:5` | Complete | Complete but superseded by Metrics tab relocation | Note drift below |
| `2026-08-26-ponytail-simplification-pass.md:5` | Complete | Complete | — |
| `2026-08-27-metrics-markdown-params-presets.md:6` | Complete | Complete — `metrics_pane.py:42` `chat_pane/turn.py:169` `chat_pane/params.py:26` `presets.py:13` | — |
| `2026-08-27-ponytail-audit-over-engineering.md:5` | Complete | Complete | — |
| `2026-08-27-srp-package-split.md:5` | Complete | Complete — `app/ chat_pane/ models_pane/ search_screen/ history/` packages | — |

### Shape — `idea.md:92` `┌● qwen…┐ [Models][Chat] log:…┘`

| Spec | Status | Evidence | Authorizing plan | Disposition |
|------|--------|----------|------------------|-------------|
| Two tabs + always-visible status bar + shared log pane `idea.md:92` | **Partial — tabs expanded to three** | `src/mlx_tui/app/__init__.py:116` `TabbedContent` has 3 `TabPane` id `models/chat/metrics`; status `Static#status-bar dock:top width:100%` `app/__init__.py:40`, log `RichLog#app-log dock:bottom height:6` `app/__init__.py:44` | `2026-08-27-metrics-markdown-params-presets.md:20` | **WontFix** — Metrics tab reclaims footer space; reverting would lose `MemoryStore` history. Keep 3 tabs, update `idea.md` shape note if desired. |
| `80` col doc shape — status shows `● qwen3.8-27b-4bit · 16.4/48 GB · 14.8 tok/s · :8080` `idea.md:94` | **Implemented with drift** | `src/mlx_tui/status.py:62` `format_status_line()` renders `● {model} · RSS x.x GB · avail x.x/y.y GB · :port` `app/status_bar.py:16`; tok/s is per-turn stamp `chat_pane/turn.py:164` not status bar | `2026-08-25-v0-status-bar-and-chat.md` | **WontFix** — status bar correctly shows RSS/avail/port, tok/s correctly on turn stamp; doc shape was illustrative. |

### Status bar — `idea.md:102-131`

| Spec | Status | Evidence | Disposition |
|------|--------|----------|-------------|
| `GET /v1/models` every 2s `httpx` 500ms timeout `idea.md:106` | **Implemented** | `src/mlx_tui/app/__init__.py:130` `set_interval(2.0,_poll)` `src/mlx_tui/app/__init__.py:128` `AsyncClient(timeout 0.5)` `src/mlx_tui/app/polling.py:22` `classify_liveness_for()` | — |
| Timeout→red not crash; liveness via parsed JSON non-empty `data` `idea.md:107,253` | **Implemented** | `src/mlx_tui/status.py:16` `green⇔200+non-empty data else amber` `src/mlx_tui/app/polling.py:24` `except→red` `src/mlx_tui/status.py:25` | — |
| Red dot cold-start offer streamed into log pane `idea.md:109` | **Implemented** | `src/mlx_tui/app/status_bar.py:32` `if red+start_cmd add ctrl+s hint` `src/mlx_tui/app/swap_ctrl.py:33` `cold_start()` `serverctl.py:36` streamed via `log_app [swap]` | — |
| Memory `psutil.Process(pid).memory_info().rss` unified `idea.md:112` | **Implemented** | `src/mlx_tui/app/polling.py:46` `rss/2**30` `src/mlx_tui/process.py:66` `memory_snapshot()` | — |
| Pid from pidfile else `process_iter` matching `mlx_lm.server/mlx_vlm.server` `idea.md:114` | **Implemented** | `src/mlx_tui/process.py:35` `find_server_pid(pidfile→_pid_cache→process_iter)` `process.py:11` tokens | — |
| Cache pid, re-scan only when died `idea.md:246` | **Implemented** | `src/mlx_tui/process.py:43` `if _pid_cache and _cmdline_matches return` | — |
| Poll overlap guard skip tick `idea.md:243` | **Implemented** | `src/mlx_tui/app/__init__.py:158` `if _poll_in_flight return` | — |
| Headroom `virtual_memory().available` hint 20% margin `idea.md:121,301` | **Implemented** | `src/mlx_tui/process.py:68` `vm.available/2**30` `src/mlx_tui/models.py:57` `fits_headroom size*1.2≤avail*2**30` | — |
| One `Static` `set_interval(2,poll)` ~40 lines `idea.md:130` | **Implemented (split)** | `src/mlx_tui/app/polling.py:22` 65 + `status.py:77` + `process.py:69` + `status_bar.py:34` — line count accurate but split per SRP | — |

### Models — a loader, not a browser — `idea.md:132-163` + `v1:263-309`

| Spec | Status | Evidence | Disposition |
|------|--------|----------|-------------|
| One `DataTable` rows=HF cache cols name/size/quant/fits/loaded `idea.md:134` | **Implemented** | `src/mlx_tui/table.py:25` `ModelsTable` `table.py:34` `add_column model/quant/size/fits/loaded` `table.py:48` `add_row repo,quant,"X GB",fits,loaded` | — |
| Rows `scan_cache_dir().repos` filtered MLX-ish `idea.md:141` | **Implemented** | `src/mlx_tui/models.py:81` `scan_models()` `models.py:64` `collect_rows` filter `models.py:39` `_is_mlx_model` (config+tokenizer+safetensors, no `.index.json` per plan) | — |
| Delete `scan.delete_revisions(*hashes).execute()` behind confirm `idea.md:143` | **Implemented** | `src/mlx_tui/models.py:93` `delete_repos()` `src/mlx_tui/models_pane/delete.py:15` `request_delete_model→ConfirmScreen` | — |
| Load/swap `enter` warm 1-token vs `stop_cmd+start_cmd --model X` `idea.md:146` | **Implemented** | `src/mlx_tui/table.py:27` `enter→request_load_swap` `src/mlx_tui/models_pane/swap_ops.py:26` green→warm `serverctl.py:72` else restart `serverctl.py:41` `serverctl.py:16` `build_start_command()` `idea.md:277` verbatim | — |
| TUI owns waiting — progress bar + log pane `idea.md:152` | **Implemented** | `src/mlx_tui/models_pane/table_ops.py:51` `progress_line waiting for {id}… Ns` `src/mlx_tui/serverctl.py:93` `wait_healthy` streamed | — |
| Cancel in-flight `httpx` before `stop_cmd` `idea.md:154` | **Implemented** | `src/mlx_tui/models_pane/swap_ops.py:34` `cancel_chat_for_swap()→ChatPane.abort()` `src/mlx_tui/chat_pane/turn.py:146` `socket.SHUT_RDWR` + `workers.cancel_group` | — |
| Swap state machine `idle→stopping→starting→waiting-health→failed→log` `idea.md:284` | **Partial — graph removed** | `src/mlx_tui/swap.py:9` `SwapState` 5 states kept, `_ALLOWED` graph deleted (kept shim `app/state.py:19` `_SwapShim` `busy` bool); guards now `if swap_busy` `models_pane/swap_ops.py:18` `app/swap_ctrl.py:20` | **WontFix** — validated per `2026-08-27-ponytail-audit-over-engineering.md:92` `grep SwapMachine` shim; re-adding graph is YAGNI for single-user TUI. |
| `waiting-health` polls until new id timeout 60s+10s/GiB `idea.md:290` | **Implemented** | `src/mlx_tui/serverctl.py:93` `wait_healthy poll 1s` `src/mlx_tui/swap.py:27` `health_timeout()` `idea.md:291` | — |
| One swap at a time `enter` disabled `idea.md:294` | **Implemented** | `src/mlx_tui/models_pane/swap_ops.py:18` `swap_busy` guard `src/mlx_tui/app/swap_ctrl.py:20` `set_swap_ui disabled` `@work exclusive group=swap` | — |
| Marker hygiene clear on `stop_cmd` fires `idea.md:298` | **Implemented** | `src/mlx_tui/models_pane/swap_ops.py:96` `set_tracked None before run_command` | — |
| Fits margin 20% slack `idea.md:301` | **Implemented** | `src/mlx_tui/models.py:30` `_HEADROOM_MARGIN 0.2` `models.py:57` | — |
| Config reload on `$EDITOR` exit no mtime watcher `idea.md:302` | **Implemented** | `src/mlx_tui/app/config_edit.py:29` `parse_config` after `suspend()` | — |
| Search `HfApi().list_models(author mlx-community search q limit50)` `idea.md:157` + `v2` | **Implemented** | `src/mlx_tui/search.py:36` `list_results(author mlx-community limit50)` `src/mlx_tui/search_screen/query.py:41` `HfApi()` | — |

### Chat — an instrument, not a chat client — `idea.md:164-187`

| Spec | Status | Evidence | Disposition |
|------|--------|----------|-------------|
| Transport `POST /v1/chat/completions stream:true plain httpx SSE` `idea.md:171` | **Implemented** | `src/mlx_tui/chat.py:53` `stream_turn stream:true` `chat.py:71` `httpx.Client connect5/read300/write5` `sse.py:10` `iter_sse_data` | — |
| TTFT clock at first chunk carrying text not first byte `idea.md:252` | **Implemented** | `src/mlx_tui/chat.py:96` `if content is not None and t_first_text is None` `sse.py:43` `delta_content_from_chunk role-only→None` | — |
| tok/s output÷(now−first chunk) client-measured `idea.md:172` | **Implemented** | `src/mlx_tui/chat.py:108` `elapsed now-first` `sse.py:82` `tok_s` | — |
| First turn after load stamped cold excluded from sparkline `idea.md:175` | **Implemented** | `src/mlx_tui/status.py:31` `ColdTracker green→red→green` `src/mlx_tui/chat_pane/__init__.py:83` `consume_cold()` `src/mlx_tui/chat_pane/turn.py:164` `· cold` `src/mlx_tui/history/sparkline.py:25` filter | — |
| Prompt tokens from `usage` else `len/3.5` labelled `(est)` no tokenizer `idea.md:178` | **Implemented** | `src/mlx_tui/sse.py:62` `token_accounting` `history/tokens.py:9` `3.5` `chat.py:86` `usage_from_chunk` `chat_pane/turn.py:62` `include_usage` | — |
| Rendering append chunks to `RichLog/Static` swap to `Markdown` on completion `idea.md:181` | **Implemented** | `src/mlx_tui/chat_pane/__init__.py:67` `Static#chat-stream` throttled `chat.py:101` `0.1s→update_stream` `src/mlx_tui/chat_pane/turn.py:169` `log.write(Markdown(full_text))` once `idea.md:181` trap noted | — |
| Params temp/top-p/max-tokens three `Input`s collapsible sidebar `idea.md:184` | **Implemented** | `src/mlx_tui/chat_pane/__init__.py:42` `Collapsible Params` 3 `Input`s `src/mlx_tui/chat_pane/params.py:26` `parse_params` clamp `0-2/0-1/1-16384` `chat_pane/turn.py:26` payload merge | — |
| Inputs not sliders `idea.md:185` | **Implemented** | `Input` with `Label` not sliders | — |
| System presets flat `presets.toml` `ctrl+p` cycles no management UI `idea.md:186` | **Partial — keys drift** | `src/mlx_tui/presets.py:21` `presets.toml` sibling `src/mlx_tui/app/__init__.py:35` `ctrl+n` forward / `ctrl+o` back `src/mlx_tui/app/presets_ctrl.py:41`; spec `ctrl+p` | **Needs Decision — keep `ctrl+n/o` (terminal `ctrl+p` unreliable per `2026-08-27-metrics-markdown-params-presets.md:235` plan, verified pilot quirk) or remap to `ctrl+p` and accept `ctrl+o` as back. Recommend keep `ctrl+n/o` and update `idea.md` note.** |
| **Context bar `9.2k/32k` amber near limit `idea.md:168`** | **Not implemented** | No widget; `history/tokens.py:24` `trim_for_context(8000)` `chat_pane/turn.py:22` hardcoded `8000` (≈8k) `history/store.py:44` `TurnRecord ctx_len` tracked + `metrics_pane.py:92` per-col shade as proxy, but no `ctx 9.2k/32k` bar | **Needs Decision — Implement (add `Static#ctx-bar` with `ctx_len/ max_ctx` and amber `>0.8*max` style, `max_ctx` from config or `AppConfig` field) or WontFix (ctx via Metrics table+shade is sufficient per `2026-08-27-metrics` review; adding bar costs 1 line but duplicates sparkline).** |
| Threading `@work(exclusive) call_from_thread` Classic hang avoided `idea.md:248` | **Implemented** | `src/mlx_tui/chat_pane/__init__.py:109` `@work exclusive group=chat thread` `chat.py:75` `on_flush→call_from_thread` `chat_pane/turn.py:97` same | — |

### History (footer strip, not a tab) — `idea.md:188-194` + `v3:343-370`

| Spec | Status | Evidence | Disposition |
|------|--------|----------|-------------|
| Last N `(model,ctx_len,tok/s)` in memory braille sparkline cold excluded `idea.md:190` `v3:349` | **Partial — rendered in Metrics tab not footer, expanded** | `src/mlx_tui/history/store.py:49` `HistoryStore dict→deque64` `src/mlx_tui/history/sparkline.py:21` braille `32×2` `history/sparkline.py:25` filter cold/cancelled `src/mlx_tui/metrics_pane.py:51` renders via `app/__init__.py:121` `TabPane Metrics` | **WontFix — footer strip intentionally absorbed into Metrics tab `2026-08-27-metrics-markdown-params-presets.md:20` to reclaim `dock:bottom` space; keeps `HistoryStore` pure. Re-adding footer would waste 3 lines. Keep 3rd tab, update `idea.md:188` if desired.** |
| In-memory only ring buffer one series per model `v3:358` | **Implemented** | `history/store.py:49` per-model `deque64` `history/sparkline.py:25` per-model `series()` | — |
| Record `(ts,model,prompt,out,ttft,tok_s,ctx_len,cold)` `v3:358` | **Implemented +1 field** | `history/store.py:37` `TurnRecord` 9 fields adds `cancelled: bool=False` (stores but filters) — superset | — |
| y tok/s x turn order cell shade ctx depth 2D `v3:363` | **Implemented** | `history/sparkline.py:137` `_shade_for_ctx quartiles dim/"" /bold` `metrics_pane.py:92` per-col shade `bold wins` `metrics_pane.py:55` two sparklines height 3 | — |
| **Footer strip `~30 lines` `v3:350` vs extra Memory sparkline** | **Built-Despite-Scope — memory sparkline added** | `history/store.py:20` `MemoryStore deque256` `history/sparkline.py:100` `render_memory_sparkline` `app/polling.py:57` `MemoryRecord every 2s ≈8.5min` `./ARCHITECTURE.md:17` MemoryStore — never in `idea.md` | **WontFix — memory sparkline is value-add alongside RSS poll; keep.** |
| Persistence `append-JSONL --since` not a DB `v3:354` | **Not implemented (deferred per spec)** | `history/store.py` in-memory only; no file IO | **WontFix per `idea.md:372` gate — add when yesterday's chat is actually wanted; shape frozen `TurnRecord` keeps it mechanical.** |

### Config (no tab) — `idea.md:195-200`

| Spec | Status | Evidence | Disposition |
|------|--------|----------|-------------|
| Single file `~/.config/mlx-tui/config.toml` `model/host/port/start_cmd/stop_cmd/pidfile` `idea.md:198` | **Implemented + expanded** | `src/mlx_tui/config.py:28` `config_path() XDG` `config.py:13` `AppConfig` 9 keys (adds `temperature/top_p/max_tokens/system` `config.py:22`) | **WontFix — chat defaults belong in same TOML; 5→9 keys still editor-only, no form.** |
| `e→$EDITOR` reload on save `idea.md:199` | **Partial — key drift** | `src/mlx_tui/app/config_edit.py:20` `suspend()+shlex.split($EDITOR)` reload `config.py:121` `parse_config` | **Needs Decision — spec `e` vs actual `ctrl+g` `app/__init__.py:34` (avoids Textual Input `e` insert). Recommend keep `ctrl+g`, update `idea.md:199`.** |
| Five strings don't need form widgets `idea.md:199` | **Implemented (still no form)** | `config_edit.py:20` file only; strict `type is` `config.py:53` + clamps | — |
| Host/port changes log restart notice `v1: Host/port live-reload` | **Implemented** | `app/config_edit.py:42` `log restart mlx-tui to apply` | — |

### Verify before writing code — `idea.md:201-217`

| Spec | Status | Evidence |
|------|--------|----------|
| 1 Does `mlx_lm.server` load on demand via `model` field or restart? `idea.md:206` | **Implemented — both paths handled** | `serverctl.py:72` `warm_load POST model hi max_tokens1` + `models_pane/swap_ops.py:26` green→warm else red+cmds→restart `idea.md:277` "restart path works either way" |
| 2 Does it emit `usage` on streamed + `include_usage`? `idea.md:212` | **Implemented with fallback** | `chat_pane/turn.py:62` `include_usage True` `chat.py:86` `usage_from_chunk` `sse.py:62` `len/3.5 est` fallback |
| 3 Does `mlx-vlm` match `mlx-lm` API? `idea.md:214` | **Implemented — both suffixes scanned** | `process.py:11` `mlx_lm.server/mlx_vlm.server` `idea.md:215` "pick one if diverge" satisfied via suffix match |

### Build Order gates — `idea.md:218-371`

| Gate | Spec question | Actual | Disposition |
|------|---------------|--------|-------------|
| v0 `is premise true?` 1-file ~200 lines no config/raw text `idea.md:223` | **Superseded** — now `config.py+presets+params+Markdown+history` all present; premise (poll+stamp) intact `app/__init__.py:130` `chat.py:53` `idea.md:243-255` notes all implemented | **WontFix — v0 shortcuts explicitly abandoned per `2026-08-27-metrics` review; not a regression.** |
| v1 `swap without dance` `idea.md:263` | **Implemented** | `table.py:25` `models_pane/*` `serverctl.py` `swap.py` `config.py` | — |
| v2 `acquire without leaving` `idea.md:311` `~80 lines` `search box+download key` | **Implemented — line count exceeded but scope held** | `search.py:148` `search_screen/*:423` still no filters/sorting/cards/favourites | **WontFix — `~80 lines` was pre-modal estimate `2026-08-26-v2:474` notes actual ~250 across 2 modules vs 80; feature line held.** |
| v3 `degrade at long context` `idea.md:343` gated on v0 can skip v1/v2 | **Implemented but gate ignored** — built linearly v1+v2+v3, not gated on v0 alone | **No action — timeline shows `2026-08-26-v3` after `v1/v2`; gate was advisory `idea.md:221`.** |
| v0/v1/v2/v3 Kill if unused `idea.md:258,307,340,369` | **Not evaluated** — no kill decision recorded | **Needs Decision — run 1-week tmux trial `idea.md:256` or explicitly mark gates N/A for this repo.** |

### Explicitly out of scope — `idea.md:372-383`

| Forbidden | Built? | Status |
|-----------|--------|--------|
| Multi-backend (vLLM, llama.cpp, GGUF) `idea.md:374` | Not built — `process.py:11` MLX-only tokens, but `chat.py:53` HTTP stays portable `idea.md:70` accidentally | **Respected** |
| Quantization runner `mlx_lm.convert` `idea.md:375` | Not built | **Respected** |
| Config/preset management UI `idea.md:376` | Not built — `config_edit.py:20` `$EDITOR` only | **Respected** |
| Custom cache scanning `idea.md:377` | Not built — `models.py:81` `scan_cache_dir()` `search.py:59` `shutil.disk_usage` | **Respected** |
| Server log tailing `idea.md:378` | Not built — `polling.py` `/v1/models` + `psutil` + `chat.py` timing only | **Respected** |
| Conversation persistence/branching/multi-session `idea.md:381` | Not built — `history/store.py:49` `deque64` in-memory only | **Respected** |
| Agentic/tool-calling `idea.md:382` | Not built — `chat.py` payload fixed | **Respected** |
| **Metrics tab `idea.md:192` "nothing left for a standalone Metrics tab"** | **Built anyway** | **Built-Despite-Scope — `src/mlx_tui/metrics_pane.py:42` `TabPane Metrics` `app/__init__.py:121` — intentionally per `2026-08-27-metrics-markdown-params-presets.md:20` + `2026-08-27-srp-package-split.md:10`. Review marked Approved. Keep; update `idea.md:192` if desired.** |

### Unimplemented Summary — the 7 items this document tracks (4 code gaps + 3 process/doc gaps)

| # | Gap | idea.md ref | Actual | Disposition | Effort if Implemented |
|---|-----|-------------|--------|-------------|-----------------------|
| 1 | **Context limit bar `9.2k/32k` amber near limit** `idea.md:168` "with a context bar that goes amber" | Not built — no widget; ctx tracked `history/store.py:44` + shade proxy `history/sparkline.py:137` | **Needs Decision** | Small — `Static#ctx-bar` + `AppConfig max_ctx int` + `trim_for_context(max_ctx)` + style `amber>0.8`; ~20 lines in `chat_pane` |
| 2 | **Footer history strip `~30 lines` `idea.md:188` `v3:350`** | Not built as footer; absorbed into Metrics tab `metrics_pane.py:42` `app/__init__.py:55` height 3 x2 | **WontFix** | — |
| 3 | **Key `e→$EDITOR` `idea.md:199`** | Drift to `ctrl+g` `app/__init__.py:34` `README.md:58` | **WontFix (keep `ctrl+g`)** — `e` conflicts with `Input` focus; doc-only fix if desired | — |
| 4 | **Key `ctrl+p cycles presets` `idea.md:186`** | Drift to `ctrl+n/o` `app/__init__.py:35` `presets_ctrl.py:41` pilot `ctrl+p` quirk `2026-08-27-metrics:289` | **WontFix (keep `ctrl+n/o`)** | — |
| 5 | **Memory `avail` purgeable caveat blocking hard-block `idea.md:121` + KV cache GBs at 8k–32k `idea.md:125` warning** | Implemented as hint `models.py:57` `fits_headroom` vs hard-block; KV drift noted but not plotted per-model | **WontFix — hint correctly not a gate; KV shade covers 2D degradation `metrics_pane.py:92`** | — |
| 6 | **Doc-lifecycle `v2 Draft` vs code Complete `docs/plans/2026-08-26-v2-hf-search-and-download.md:6`** | Doc still `Draft` | **Fix: edit to `Complete`** (1 line) | Trivial |
| 7 | **Build-order kill evaluations `idea.md:258,307,340,369`** | Never recorded | **Needs Decision — run 1-week trial or mark N/A** | Process not code |

*Items 5–7 are process/doc gaps included for completeness; code gaps are 1–4.*

