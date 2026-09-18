# Plan Review: Refactor Oversized Modules into Single-Responsibility Files

**Date:** 2026-09-17
**Target:** docs/plans/2026-09-17-refactor-oversized-modules.md
**Review:** 1
**Verdict:** REVISE

## Assessment

The plan's current-state facts are unusually accurate — every line count, symbol, and importer I spot-checked matches the code, and the phase ordering (UI-free leaves first) plus per-phase gates are sound. But two blockers make it unimplementable as written (a `chat.py` vs `chat/` namespace collision and a `json_util`↔`sessions` circular import), and the "pure move with zero behavior change" claim fails for Phase 1's shared validator, whose two sources have irreconcilable error types and type-check semantics.

## Cross-Cutting Themes

- **Stale-import gates miss an entire import idiom.** Phases 2–4 search for `from mlx_tui.sessions import` / `from mlx_tui.managed import`, but the codebase also uses `from mlx_tui import sessions` (`session_screen.py:83,206`, `tests/unit/test_sessions.py:14`, `tests/integration/test_sessions.py:15`) and `from mlx_tui import managed` (`diagnostics.py:19`, `tests/unit/test_managed.py:14`) — none of which match the gates, and `session_screen.py` / `diagnostics.py` are missing from every Changes list.
- **Textual mechanics constrain extraction.** The "free functions + `pane` param, keep methods only if circular imports force it" rule (Phases 5–7) ignores `@on`/`@work`: dozens of slated moves are message handlers or workers that must remain methods (thin delegates at most).
- **String-based test patch targets are invisible to the import-update plan.** Tests monkeypatch `"mlx_tui.chat_pane.stream_turn"`, `"mlx_tui.compare_pane.run_comparison"`, and `comparison_persistence.*` attributes; moving the lookup namespaces silently breaks or voids these patches.

## Findings

### Critical

- **[Correctness] Phase 5: `src/mlx_tui/chat/` collides with existing `src/mlx_tui/chat.py`.** `chat.py` (211 lines: `TurnProgress`, `TurnResult`, `stream_turn`, `error_detail`) exists and is imported by `chat_pane.py:32`, `comparison_runner.py:18`, `tests/unit/test_chat.py:14`, and 4 integration test sites. Python cannot serve both `mlx_tui.chat` (module) and `mlx_tui.chat.pane` (submodule): whichever wins, the other import style raises. The plan never mentions `chat.py`, so Phase 5 fails at import time as written. Fix: pick a different subpackage name (e.g. `chat_ui/`) or rename `chat.py` first as an explicit Phase 0/5a step with its own importer updates.
- **[Correctness] Phase 1: `json_util`↔`sessions` circular import.** The plan has `json_util.json_compatible` import `JSONValue` from `mlx_tui.sessions` (defined `sessions.py:65`, after the top import block) while `sessions.py` imports validators from `json_util` — a hard `ImportError` at startup. Fix: define `JSONValue` in `json_util` (or reuse the identical alias already in `comparison_contracts.py:16`) and have `sessions.py` / `comparison_persistence.py` import it from there; never import `sessions` from `json_util`.

### Major

- **[Correctness] Phase 1: one shared validator cannot preserve both behaviors.** Sessions helpers use strict `type(x) is` checks raising `SessionValidationError` with messages like `"is missing 'k'"` / `"has an unexpected field"`; comparison helpers use `isinstance` raising `ComparisonValidationError` with `"k is required"` / `"has unknown keys: ..."` (`sessions.py:202-220` vs `comparison_persistence.py:168-194`). `_json_compatible` strictly rejects non-`str` keys and non-JSON types while `_json_value` coerces `Path`→`str` and `str(key)` (`sessions.py:294-312` vs `comparison_persistence.py:45-60`). `_atomic_write` differs too: comparison does `target.parent.mkdir(parents=True, exist_ok=True)` (`comparison_persistence.py:515-518`), sessions does not (`sessions.py:890-...`). "Byte-for-byte moves... unify only naming, not semantics" is self-contradictory — a single function has one error type and one coercion policy. Fix: specify the reconciliation explicitly (e.g. shared core parameterized by exception factory, plus thin domain wrappers; state which `_atomic_write` semantics wins with test evidence) or drop the "zero behavior change" claim for named differences.
- **[Correctness/Architecture] Phases 5–7: extraction rule ignores `@on`/`@work`.** Slated "free function" moves include `@on` handlers (`chat_pane.py:678-884` incl. `_on_draft_changed:843`, `_on_param_changed:878`, `_on_input_submitted:884`; all session-button handlers `:2076-2424`; `compare_pane.py:552-566`, `:693`, `:890-1063` decision buttons; `app/__init__.py:356`, `:1153`) and `@work` workers (`chat_pane.py:744`, `:1127`; `compare_pane.py:798`; `:1024`; plus `_on_turn_progress:1541` fed by the worker). As free functions, Textual dispatch/worker-lifecycle breaks. The hedge ("keep methods only if circular imports force it") names the wrong condition. Fix: change the rule to "keep `@on`/`@work`/lifecycle/`action_*` methods on the pane as thin delegates; extract only pure logic into helpers."
- **[API/Compatibility] Phases 2/3/5/6: monkeypatch targets not enumerated.** Tests patch lookup namespaces, not just import paths: `"mlx_tui.chat_pane.stream_turn"` (`test_chat_cancellation.py:402,463`, `test_chat_integration.py:539,777`), `"mlx_tui.compare_pane.run_comparison|resolve_cached_snapshot|verify_profile_snapshot|process.find_server_process"` (`test_compare_integration.py:81-108,196,239,412`, `test_app_integration.py:619-669`), and `comparison_persistence` attributes incl. `hasattr(comparison_persistence, "_decision_payload")` (`test_comparison.py:47,235,246,268,322,346,358`). Changes sections list only static imports. Fix: per phase, list patch-target retargets to the new lookup namespace (e.g. `mlx_tui.chat.turns.stream_turn`, `mlx_tui.compare.workflow.run_comparison`, `mlx_tui.comparison_store.*`) and note that patching the `comparison.py` facade will not intercept `comparison_store`-internal globals.
- **[Plan Mechanics] Phases 2/4: gates miss the `from mlx_tui import X` idiom; importers omitted.** The Phase 2 gate (`from mlx_tui.sessions import|import mlx_tui.sessions`) misses `from mlx_tui import sessions as S` (`session_screen.py:83,206`, `tests/unit/test_sessions.py:14`, `tests/integration/test_sessions.py:15`); the Phase 4 gate misses `from mlx_tui import managed` (`diagnostics.py:19`, `tests/unit/test_managed.py:14` — the latter cited in Changes but unfindable by the given `rg`). Neither `session_screen.py` nor `diagnostics.py` appears in any Changes list, so both break (`S.list_sessions()`, `managed.RUNTIME_COMMIT`, etc. have no home without an `__init__` re-export, which the break-imports decision forbids). Conversely the Phase 2 gate matches the plan's own prescribed `import mlx_tui.sessions.store as _sessions_store`, so it can never return empty. Fix: gates like `rg -n "mlx_tui\.sessions" src tests` (and `.managed`), and add `session_screen.py` + `diagnostics.py` to the Phase 2/4 importer lists.
- **[Plan Mechanics] Phase 7 (and 4): attachment mechanism is a placeholder; intra-package maps missing.** "Import and attach methods... assign as methods, or thin delegating methods — verify during implementation whichever" is not an implementable step, and `action_*`/`@on` methods must stay on the class anyway (see above). Phase 4 has the same gap concretely: `runtime.py`'s `ManagedRuntime` uses `_check_owner`, `inspect_runtime`, `_python_path`, `_sanitized_environment`, `runtime_root` (verified `managed.py:690-716`), and `install.py` uses `_runtime_lock`/`_prepare_runtime`/`inspect_runtime` — none of these cross-module imports are specified. Fix: mandate thin delegates for Phase 7 and add explicit intra-package import maps for Phases 4 and 7.
- **[Correctness] Phase 2: homeless symbols after `sessions.py` deletion.** No destination is named for `PROVENANCE:86`, the `JSONValue` definition `:65`, `_optional_bool:235`, `_count_value:242`, `_required_count:253`, `_finite_value:260`, `_session_uuid:273`, `_timestamp_value:285`, `_provenance_value:313`. Natural home is `codec.py` (with the alias in `models.py`, or in `json_util` per the C2 fix). Fix: assign them explicitly; also clarify that `chat_turn.py:14` is a `TYPE_CHECKING`-only import (verified) so its update is annotation-safe.

### Minor

- **[Plan Mechanics] Phase 3: `__all__` count is 34, not 35** (counted via AST on `comparison.py`). Trivial, but the facade-edit step should say "unchanged" without the number.
- **[Plan Mechanics] Phase 4: "`_resource` helpers" names nothing** — actual names are `_resource_bytes:93` / `_resource_hash:97`. (The `RUNTIME_COMMIT:37`–`COMPLETION_MARKER:49` range itself correctly covers all intervening constants — verified.)
- **[Plan Mechanics] Phases 2–7: `ARCHITECTURE.md` prose goes stale beyond layout lines** (e.g. `:10` `MlxTuiApp` in `app/__init__.py`, `:172` sessions prose). Either widen the doc step or accept the drift explicitly.
- **[Plan Mechanics] Phase 5→6 ordering: `compare_pane.py:1065` `ChatInput` edit is transient** (the file is deleted next phase). Phase 6 must carry that import into `compare/decisions.py` (near `_go_chat:1064`); say so.
- **[Test Coverage] Phase 3: spell out the `comparison_runner.py:35-39` split** — `_json_value` → `comparison_encoding`, `comparison_dir`/`save_comparison` → `comparison_store` — so the mechanical edit can't misfile them.
- **[Test Coverage] Manual verification is a full TUI pass ×7 phases.** Consider trimming to affected-tab smoke (session round-trip, compare commit, chat send/abort) except Phase 7's boot/poll/quit checklist, which is appropriately thorough.

### Suggestions

- Record a target size cap per new module (e.g. "no new file >350 lines") so reviewers can verify the breakup goal was met, not just that tests pass.
- State the new-subpackage packaging explicitly (`history/` precedent is namespace packages with no `__init__.py` — verified — so new `sessions/`, `managed/`, `compare/`, `chat*/` dirs work the same way with absolute imports; no `__init__.py` needed precisely because there are no shims).

## Strengths

- Current State is fully verified: all six line counts exact (2463/1323/1141/1078/778/701), cited symbols/method names match, importer lists check out against `rg`, and the `mlx-tui = "mlx_tui.app:main"` entry point plus `history/` no-`__init__` precedent are correctly observed.
- Phase ordering is right: storage/runtime leaves with fast unit tests before the high-risk TUI splits; each phase keeps the tree green with the full gate set (`pytest`, `ruff`, `format`, `pyrefly`, smoke).
- Honest scoping: the long-method risk (`_run_turn` 258 lines etc.) is named as merely moved with follow-up out of scope, and the `app:main` packaging risk has a real gate (`artifact_smoke.py`).
- Baseline confirmed green: 467 unit tests pass, `ruff check`, `ruff format --check`, and `pyrefly check --min-severity warn` (0 diagnostics) all pass pre-change.

## Recommended Changes

1. Resolve the `chat.py` vs `chat/` collision before anything else (Critical 1) — new subpackage name or a `chat.py`-rename pre-step with importer updates.
2. Give `JSONValue` a cycle-free home (`json_util` or `comparison_contracts`) and forbid `json_util`→`sessions` imports (Critical 2).
3. Specify Phase 1's semantic reconciliation: error-type parameterization/thin wrappers + which `_atomic_write`/`_json_*` semantics wins, evidenced by tests (Major 1).
4. Rewrite the Phases 5–7 extraction rule: `@on`/`@work`/lifecycle/`action_*` stay as thin methods; only pure logic becomes free functions (Major 2).
5. Enumerate string patch-target retargets per phase, including the `comparison_persistence` attribute patches and the facade-vs-store lookup caveat (Major 3).
6. Fix the stale-import gates to match `mlx_tui\.(sessions|managed|...)` broadly and add `session_screen.py` + `diagnostics.py` to importer lists (Major 4).
7. Pin Phase 7 to thin delegates and add intra-package import maps for Phases 4/7 (Major 5).
8. Assign homeless sessions validators/constants to `codec.py`/`models.py`/`json_util` (Major 6).
9. Apply Minor 1–6 (counts, names, doc scope, transient edit, runner split, trimmed manual checks).
