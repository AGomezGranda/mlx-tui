# De-over-engineering cleanup (code only)

**Date:** 2026-09-08
**Work Item:** n/a
**Status:** Complete

## Overview
Remove verified dead code, single-use wrappers, and test-only prod API from `src/mlx_tui/` while keeping every runtime behavior identical; docs are untouched.

## Current State
- `src/mlx_tui/process.py:28-32` `_pid_from_file` has zero callers; `find_server_process(host, port, pidfile=None)` at `:141-142` never reads `pidfile` in body `:153-172`, but 4 call sites pass it (`app/__init__.py:249,490`, `models_pane.py:205,279`) and `config.py:25,72,146,48` defines/plumbs `pidfile`. `Path` import at `process.py:6` is used only by `_pid_from_file`.
- `src/mlx_tui/sse.py:64` `SSEDecoder.done_seen` is never read/written (distinct from `chat.py:100,114` local). `aiter_sse_data:43-52`, `collect_sse:144-157`, `StreamTerminal/DONE_SEEN/CLEAN_EOF/TRUNCATED:21-24`, `has_pending:67-68` have zero `src` users; prod `chat.py:101` loops `SSEDecoder().feed` directly. Sole consumers are `tests/unit/test_sse.py:11-17,28-33,113-127,245-254`.
- `src/mlx_tui/boot.py:18-27` `PreparedCommands` is constructed once in `prepare_commands:29-98` and consumed once in `execute_boot:130` (`commands.stop/shell/env:139-140`, `commands.display:152`, `commands.start/shell/env:154-160`). `_observe[CallbackValue]:101-112` wraps only `on_line/on_tick` at `:133-134`; covered by `tests/unit/test_boot.py:187-224`.
- `src/mlx_tui/swap.py:18,80` `BootPlan.success_line` is set at `swap.py:80`, `app/__init__.py:513,559`, `tests/unit/test_boot.py:35` and read only at `models_pane.py:283` with no branching. `health_timeout(size, base_s=60.0, per_gib_s=10.0):21-25` is called with defaults only at `boot.py:168` and `models_pane.py:188`; custom args appear only in `tests/unit/test_swap.py:10-16`. `resolve_swap_action:31-57` and `_refuse_reason:60-72` share the same 4 args and are called adjacently at `models_pane.py:140,148`.
- `src/mlx_tui/serverctl.py:68-79` `_argv_from` is used once at `:189` in `_popen`. `_kill_group:104-108` is used 3× inside `terminate_failed_process:124,133,146`.
- Trivial single-use helpers: `models.py:84-85` `_row_sort_key` (used `collect_rows:80`, covered `test_models.py:129`); `history/store.py:58-59` `_ts` (used `all_records:61`); `history/sparkline.py:150-158` `_shade_for_ctx` (used `metrics_pane.py:119`, tested `test_sparkline.py:84-90`); `chat.py:24,84` `_FLUSH_INTERVAL_S` default for `flush_interval` (src caller `chat_pane.py:304-310` omits it; tests pass explicit `0.0` at `test_chat.py:91,124`); `metrics_pane.py:28-53` `_per_col_styles/_fmt_s/_fmt_rate/_fmt_count` + `:25` `_MAX_RECENT` (single-use, no direct test imports); `config.py:85-90` `_clamp/_clamp_int` shared via `presets.py:9,55-59`, duplicated by `params.py:26-45` `_parse_clamped_float/_parse_clamped_int`.
- Verified gates: `uv run ruff check src/mlx_tui/sse.py src/mlx_tui/swap.py` passes; `uv run pytest tests/unit/test_swap.py tests/unit/test_sse.py -q` → 74 passed. Full `uv run pytest -q` exceeds 120s (Textual pilot suite), so phases use targeted test commands.

## Design Decisions
| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Move test-only SSE helpers into tests, don't delete coverage | Delete helpers outright vs move to tests | `test_sse.py` parity/terminal tests pin framing behavior; moving preserves coverage with zero prod surface |
| Fold `_refuse_reason` into caller instead of tuple-return change | Return `tuple[SwapAction,str]` from one function vs inline reason strings at `models_pane.py:148` | Tuple return changes `resolve_swap_action` signature pinned by exhaustive `test_swap.py:19-44`; inlining at the single caller is the smaller diff |
| Replace `PreparedCommands` with a plain 5-tuple unpacked in `execute_boot` | 5-tuple vs dict vs keep dataclass | Tuple preserves field order (`start, stop, shell, env, display`) with no new type; single producer/consumer makes the dataclass pure ceremony |
| Replace `_observe` with inline `contextlib.suppress` wrappers | `contextlib.suppress` vs try/except vs keep generic | `suppress(Exception)` at the two wrapper defs is the stdlib form of the same swallow; keeps `test_boot.py:187-224` green |
| Unify clamp via existing `config._clamp/_clamp_int` reuse in `params.py` | Rename to public `clamp_*` vs duplicate inline `max/min` vs reuse private | Reuse matches the existing `presets.py:9` pattern and avoids a public-API rename; smallest behavior-preserving diff |
| Keep `flush_interval` param, inline only the constant | Remove param vs inline constant `0.1` as default | Tests pass explicit `flush_interval=0.0` for determinism; removing the param breaks them |

## Implementation Phases

### Phase 1: Dead process plumbing
Delete the never-read pidfile plumbing while keeping listener-scan behavior identical.

**Changes:**
- `src/mlx_tui/process.py` — delete `_pid_from_file:28-32`; change `find_server_process(host, port, pidfile=None)` to `find_server_process(host, port)`; remove now-unused `from pathlib import Path` import at `:6`.
- `src/mlx_tui/config.py` — remove `pidfile: str | None = None` field at `:25`; remove `"pidfile": str` entry at `:72`; remove `pidfile=...` mapping at `:146`; remove `# pidfile = ...` template line at `:48`.
- `src/mlx_tui/app/__init__.py` — at `:249` and `:490`, change `process.find_server_process(host, port, pidfile)` / `find_server_process(self.host, self.port, self.config.pidfile)` to two-arg calls.
- `src/mlx_tui/models_pane.py` — at `:205` and `:279`, drop the `pidfile` argument to `find_server_process`; remove `pidfile = cfg.pidfile` local at `:249` if it becomes unused.
- `tests/unit/test_process.py` — drop `pidfile=` kwargs at `:129-141,144-151,282-291,336-360` (keep the same scan assertions: wrong-port falls through, garbage pidfile case becomes plain scan).

**Success Criteria:**

#### Automated Verification:
- [x] Process unit tests pass: `uv run pytest tests/unit/test_process.py -q`
- [x] No pidfile references remain in src: `uv run ruff check src/mlx_tui/process.py src/mlx_tui/config.py src/mlx_tui/app/__init__.py src/mlx_tui/models_pane.py`
- [x] Format clean: `uv run ruff format --check src/mlx_tui/process.py src/mlx_tui/config.py src/mlx_tui/app/__init__.py src/mlx_tui/models_pane.py tests/unit/test_process.py`

#### Manual Verification:
- [ ] App Models pane still shows same process-identity behavior with a local server running (green health, no pidfile config key needed)

### Phase 2: SSE prod-surface trim
Drop the dead decoder field and relocate test-only framing helpers out of prod.

**Changes:**
- `src/mlx_tui/sse.py` — delete `done_seen: bool = False` field at `:64`; delete `has_pending` property at `:67-68`; delete `aiter_sse_data:43-52`; delete `collect_sse:144-157`; delete `StreamTerminal/DONE_SEEN/CLEAN_EOF/TRUNCATED:21-24`; trim the `collections.abc` import to `Iterable, Iterator` (drop `AsyncIterable, AsyncIterator`).
- `tests/unit/test_sse.py` — inline the moved helpers into the test file: define local `_collect_async` (existing at `:33`) as the async parity helper, define local `collect_sse` equivalent plus `DONE_SEEN/CLEAN_EOF/TRUNCATED` constants (or import from a new `tests/helpers_sse.py` if the file prefers; keep all three terminal assertions at `:245-254` and async parity at `:113-127` green without importing them from `mlx_tui.sse`).

**Success Criteria:**

#### Automated Verification:
- [x] SSE + chat unit tests pass: `uv run pytest tests/unit/test_sse.py tests/unit/test_chat.py -q`
- [x] Lint clean: `uv run ruff check src/mlx_tui/sse.py tests/unit/test_sse.py`
- [x] Format clean: `uv run ruff format --check src/mlx_tui/sse.py tests/unit/test_sse.py`

#### Manual Verification:
- [ ] Chat streaming against a local server still completes with `[DONE]` → success stamp (no framing regression)

### Phase 3: Boot/swap/serverctl inlines
Fold single-producer/consumer transfer objects and tiny wrappers.

**Changes:**
- `src/mlx_tui/boot.py` — delete `PreparedCommands:18-27`; change `prepare_commands(config, plan)` to return `tuple[str | list[str], str | list[str] | None, bool, dict[str, str] | None, str]` as `(start, stop, shell, env, display)` with identical branch logic (`shell=True:63-69` raw passthrough + `MLX_TUI_MODEL` env; `shell=False:92-98` via `build_start_command` + `shlex.split` stop + `{model}` replace + `shlex.join` display); update `execute_boot:130` to `start, stop, shell, env, display = prepare_commands(config, plan)` and replace `commands.stop/shell/env/display/start` uses at `:139-140,152,154-160`.
- `src/mlx_tui/boot.py` — delete `_observe:101-112`; in `execute_boot` define `def stream(value: str) -> None: with suppress(Exception): on_line(value)` and `def tick(value: int) -> None: with suppress(Exception): on_tick(value)` using `from contextlib import suppress` (keep `test_boot.py:187-224` failing-callback behavior).
- `src/mlx_tui/swap.py` — remove `success_line: str` field at `:18`; change `boot_plan_for` at `:75-80` to `BootPlan(model_id=row.repo_id, size_on_disk=row.size_on_disk, stop_first=stop_first)`; change `src/mlx_tui/models_pane.py:283` from `log_app(plan.success_line)` to `log_app(f"✓ request succeeded for {plan.model_id}; residency unknown")`; update `src/mlx_tui/app/__init__.py:513,559` BootPlan constructions and `tests/unit/test_boot.py:35` (`"✓ serving"`) to the two-arg form.
- `src/mlx_tui/swap.py` — change `health_timeout(size_on_disk, base_s=60.0, per_gib_s=10.0)` to `health_timeout(size_on_disk)` returning `60.0 + 10.0 * (size_on_disk / 2**30)`; update `tests/unit/test_swap.py:10-16` to assert defaults only (`0 → 60.0`, `5GiB → 110.0`) and drop custom-arg assertions.
- `src/mlx_tui/swap.py` — delete `_refuse_reason:60-72`; inline its four reason strings into `src/mlx_tui/models_pane.py:148` as the `refuse` branch if/else (amber / warm-not-green / restart-missing-commands / unreachable-no-start_cmd); remove `_refuse_reason` from the `models_pane.py:26` import.
- `src/mlx_tui/serverctl.py` — delete `_argv_from:68-79`; inline into `_popen:189` non-shell branch (`list(cmd)` copy + empty check, else `shlex.split` with `ValueError("invalid command: ...")` + empty check).
- `src/mlx_tui/serverctl.py` — delete `_kill_group:104-108`; at `:124,133,146` replace `_kill_group(pgid, sig)` with `with suppress(OSError): os.killpg(pgid, sig)` adding `from contextlib import suppress` (`PermissionError` is a subclass of `OSError`, preserving the swallow set).

**Success Criteria:**

#### Automated Verification:
- [x] Boot/swap/serverctl tests pass: `uv run pytest tests/unit/test_boot.py tests/unit/test_swap.py tests/unit/test_serverctl.py tests/integration/test_boot_integration.py -q`
- [x] Lint clean: `uv run ruff check src/mlx_tui/boot.py src/mlx_tui/swap.py src/mlx_tui/serverctl.py src/mlx_tui/models_pane.py`
- [x] Format clean: `uv run ruff format --check src/mlx_tui/boot.py src/mlx_tui/swap.py src/mlx_tui/serverctl.py src/mlx_tui/models_pane.py`

> Deviation (Phase 3, per user decision): kept `BootPlan.success_line` — `app/__init__.py` cold-start uses `"✓ generation verified; residency unknown"` vs `swap.boot_plan_for` `"✓ request succeeded..."`. Unifying broke 4 integration tests; field restored to keep behavior identical.

#### Manual Verification:
- [ ] Cold start + restart from Models pane still boots, health-waits, and logs the same success line; stop-timeout still terminates the owned process group

### Phase 4: Trivial helper inlines
Inline single-use formatting/sort/key helpers and unify clamp reuse.

**Changes:**
- `src/mlx_tui/models.py` — delete `_row_sort_key:84-85`; change `collect_rows:80` to `rows.sort(key=lambda r: (-r.size_on_disk, r.repo_id))`.
- `src/mlx_tui/history/store.py` — delete `_ts:58-59`; change `all_records:61` to `flat.sort(key=lambda r: r.ts)`.
- `src/mlx_tui/history/sparkline.py` — simplify `_shade_for_ctx:150-158` to `s = sorted(ctx_lens); if not s: return []; if len(s) < 2: return [""] * len(ctx_lens); try: q1, _, q3 = statistics.quantiles(s, n=4) except statistics.StatisticsError: q1, q3 = s[len(s) // 4], s[3 * len(s) // 4]` then the existing map.
- `src/mlx_tui/chat.py` — delete `_FLUSH_INTERVAL_S = 0.1` at `:24`; change signature default at `:84` to `flush_interval: float = 0.1` (keep the param; tests passing `0.0` stay green).
- `src/mlx_tui/metrics_pane.py` — delete `_per_col_styles:28-39`, `_fmt_s:42-43`, `_fmt_rate:46-47`, `_fmt_count:50-53`, `_MAX_RECENT = 64` at `:25`; inline at use sites `:101-105` (`all_records()[-64:]`), `:120` (pairwise style logic), `:150-156` (`f"{v:.2f}" if v is not None else "—"`, `f"{v:.1f}"` rate, `f"{v}~"` estimated-count forms).
- `src/mlx_tui/params.py` — delete `_parse_clamped_float:26-35` and `_parse_clamped_int:38-45` bodies' manual clamp; reuse `from mlx_tui.config import _clamp, _clamp_int` (same pattern as `presets.py:9`) keeping empty→default, `ValueError`/nonfinite→default, and clamp ranges identical in `read_values:127-130`.

**Success Criteria:**

#### Automated Verification:
- [x] Unit tests pass: `uv run pytest tests/unit/test_models.py tests/unit/test_history.py tests/unit/test_tokens.py tests/unit/test_sparkline.py tests/unit/test_chat.py tests/unit/test_params.py tests/unit/test_presets.py tests/unit/test_config.py -q`
- [x] Lint clean: `uv run ruff check src/mlx_tui/models.py src/mlx_tui/history/store.py src/mlx_tui/history/sparkline.py src/mlx_tui/chat.py src/mlx_tui/metrics_pane.py src/mlx_tui/params.py`
- [x] Format clean: `uv run ruff format --check src tests`
- [x] Typecheck clean: `uv run pyrefly check --min-severity warn`

> Deviation (Phase 4): added targeted `type: ignore[implicit-any-lambda]` comments to the two planned inline sort lambdas because Pyrefly cannot infer their parameter types; runtime behavior is unchanged.

#### Manual Verification:
- [ ] Metrics pane still renders `—`/`~` placeholders and sparklines identically; Params pane clamping/normalize-on-submit unchanged

## Out of Scope
- Docs changes (`docs/`, `ARCHITECTURE.md`) — excluded by request.
- Tool-call pipeline (`sse.delta_tool_fragments_from_chunk`, `chat._merge_tool_fragments`, `TurnResult.tool_calls`) — live feature used by `chat_pane.py:346,362,401,403,459`.
- Process identity cache, `_conn_*` helpers, `memory_snapshot`, `_throttled_tqdm`, `on_activity`, `wait_healthy` URL logic, `terminate_failed_process` non-POSIX branch — live behavior or pinned by tests.
- `table.refresh_markers` vs `set_rows`, `ModelsTable` actions/`BINDINGS`, `ResultsTable`, `_search_generation`, `fits_disk` vs `fits_headroom`, `ConfirmScreen` file, `ChatInput`, commit/display merge, `waiting`/`finished` merge, `rescan`/`_pending_delete_row`, `apply_config` vs `apply_values`, `PresetParseError`/`presets_path`, `swap_busy`/`effective_model` proxies — each encodes live policy or a pinned test contract per research.
- Full-suite `uv run pytest -q` (exceeds 120s locally); phases use the targeted commands above.

## Risks & Mitigations
- `pidfile` removal breaks external configs still setting `pidfile` → unknown TOML keys already fall back to defaults (`config._from_mapping` ignores unknown keys); no crash path.
- SSE helper move breaks `test_sse.py` imports → keep all terminal/parity assertions in the same phase; run Phase 2's targeted tests before proceeding.
- `health_timeout` signature change breaks custom-arg callers → only defaults are used in src; test file is updated in the same phase.
- `PreparedCommands` tuple ordering confusion → keep `(start, stop, shell, env, display)` order documented in `prepare_commands` return annotation; single consumer unpacks positionally once.
- Clamp reuse via private import widens private surface → matches existing `presets.py:9` precedent; no public API rename in this plan.
