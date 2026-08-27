# Diff + v3 Implementation Review: History sparkline

**Date:** 2026-08-26
**Target:** current diff vs `HEAD` (`src/mlx_tui/app.py`, `src/mlx_tui/chat_pane.py`, `src/mlx_tui/history.py`, `src/mlx_tui/models_pane.py`, `README.md`, staged formatting in `src/mlx_tui/search.py:90`, `src/mlx_tui/serverctl.py:51`, `tests/unit/test_chat.py:98`, `tests/unit/test_serverctl.py:154`) and plan `docs/plans/2026-08-26-v3-history-sparkline.md`
**Review:** 1
**Verdict:** APPROVE WITH MINOR FIXES — no blocking correctness bug on the hot path; two minor bugs (whitespace crash, odd-column shade) and two hygiene nits should be fixed.

## Assessment

The diff implements the plan faithfully. `HistoryStore` per-model `deque(maxlen=64)`, frozen `TurnRecord` (9 fields), pure `render_sparkline` (stdlib-only, braille 2×4 dots), `Static(#history-strip)` docked above `#app-log`, `_shade_for_ctx` quartile mapping in `app.py`, and `ChatPane._run_turn` recording via `call_from_thread` all match the design decisions. Prior plan-review criticals are resolved (worker-thread hop, braille bit table, `ctx_len` estimate-vs-usage, `"—"` rendering, split phases). Staged changes are `ruff format` line-wrap only. `uv run pytest -q` → 225 passed, `ruff check` clean, `pyrefly` 0 errors verified.

## Findings

### Major

_None blocking._ The implementation is safe to ship once minors below are addressed.

### Minor

- **[Correctness] `src/mlx_tui/history.py:149` — `_tok_int` crashes on whitespace-only input:** `s.split(maxsplit=1)[0] if s else ""` assumes `s.split` is non-empty when `s` is truthy. ` _tok_int("   ")` raises `IndexError: list index out of range` (reproduced). `token_accounting` (`src/mlx_tui/sse.py:77`) never returns whitespace, so the hot path is safe, but the helper is public-adjacent and unit-tested with sentinel `"—"`; a defensive caller or future format change hits the crash. Fix one line to `part = (s.split() or [""])[0]` or guard `parts = s.split(maxsplit=1); part = parts[0] if parts else ""`. Add edge case `assert _tok_int("   ") == 0` to `tests/unit/test_history.py:394`.

- **[Correctness] `src/mlx_tui/app.py:150` — odd-length shade downgrades `dim` to normal:** `col_styles` folds two turns per braille column; missing right column uses `""` (middle) as fallback. When `n` is odd, last `col_styles` sees `(s_left="dim", s_right="")` → hits `elif "" in (...)` → `""` instead of `"dim"`. A single low-ctx turn (`n=1, ctx=100`) renders normal weight, not dim, contradicting `_shade_for_ctx` intent. Repro: `_shade_for_ctx([100]) == ["dim"]` but `update_history_strip` col-style is `""`. Fix: use `None` sentinel for missing column and skip it, e.g. `s_right = styles[2*k+1] if 2*k+1 < n else None` and test `if "bold" in (s_left, s_right): ... elif s_left == "" or s_right == "": ... else: "dim"` with `None` excluded.

- **[Hygiene] `src/mlx_tui/app.py:144` duplicates `render_sparkline` filtering:** `visible = [r for r in records if not r.cold and not r.cancelled]; visible = visible[-(32*2):]` is copied from `src/mlx_tui/history.py:101`. The width literal `32*2` is also hard-coded inside `render_sparkline(width=32)`. Drift if width changes. Severity low (both currently 32). Fix: have `render_sparkline` return visible slice or expose `visible_for_render = [r ...]` and reuse its length for shade, or derive `width*2` from a shared constant. At minimum add a comment `# keep in sync with render_sparkline(width=32)`.

- **[Thread hygiene] `src/mlx_tui/models_pane.py:144` and `src/mlx_tui/models_pane.py:169,213` — `_tracked_model` mutated directly on worker threads:** `run_warm_swap` and `run_boot` set `self.tui._tracked_model = ...` on `thread=True` workers, while UI thread reads it via `effective_model()` (`src/mlx_tui/app.py:208`) and `update_history_strip` (hopped via `call_from_thread`). Prior code did the same, so not a new regression, but `HistoryStore` hop was fixed while `HistoryStore`'s key source was not. `dict`/`str` assignment is GIL-atomic but the hop ordering (`call_from_thread(self.refresh_models)` after the assignment) gives no happens-before guarantee to `update_history_strip`. Fix: hop the mutation too (`self.tui.call_from_thread(setattr, self.tui, "_tracked_model", row.repo_id)` or a dedicated `App.set_tracked_model` helper) or add a one-line comment `# ponytail: GIL-atomic, single writer` if keeping as-is. Precedent already uses `call_from_thread` for the UI effects on the next two lines (`src/mlx_tui/models_pane.py:147`).

- **[Duplication] `src/mlx_tui/chat_pane.py:92` and `src/mlx_tui/chat_pane.py:147` — identical cancelled-record block twice:** Same 12-line `TurnRecord(..., cancelled=True)` / `history.add` / `update_history_strip` / system line appears in the success-cancel and `StreamClosed` cancel branches. Not a bug, but a one-function helper `def _record_cancelled(self, model_at_send, cold, ctx_len_estimate)` would shrink diff and prevent future drift (e.g., if `prompt_tok` convention changes).

### Nit / Style

- **[Docs] `README.md:70` says shade via dim/normal/bold quartiles but does not mention braille cell packing or filtering detail:** Matches implementation, but could note "2 braille rows = 8 dot rows" to orient users expecting height 3.

- **[Formatting] Staged diff (`src/mlx_tui/search.py:90`, `src/mlx_tui/serverctl.py:51`, `tests/unit/test_chat.py:98`):** Pure `ruff format` line breaks. No behavior change; reviewers can ignore.

## Positive Notes

- **Thread hop fixed:** `ChatPane._run_turn` now correctly does `self.tui.call_from_thread(self.tui.history.add, record)` on all paths (`src/mlx_tui/chat_pane.py:103`, `125`, `158`), resolving prior critical.
- **ctx_len unified:** `ctx_len = _tok_int(result.tok_in_str) if " (est)" not in result.tok_in_str else ctx_len_estimate` (`src/mlx_tui/chat_pane.py:109`) matches Design Decision table.
- **Single model capture:** `model_at_send = effective_model() or "—"` once before payload (`src/mlx_tui/chat_pane.py:68`) fixes double-call race.
- **Braille encoding pinned:** `_bit` table (`src/mlx_tui/history.py:72`) and `chars=(n+1)//2` packing (`src/mlx_tui/history.py:122`) match spec; `height_rows` dot-row math correct; flat-series `hi=lo+1.0` avoids div0.
- **Unknown model rendered:** `model = effective_model() or "—"` then `series(model)` (`src/mlx_tui/app.py:137`) stores and shows the `"—"` series instead of hiding it.

## Recommended Changes

1. Guard `_tok_int` against whitespace-only: `parts = s.split(maxsplit=1); part = parts[0] if parts else ""` (`src/mlx_tui/history.py:149`).
2. Fix odd-column shade fallback to `None` not `""` (`src/mlx_tui/app.py:150`).
3. Deduplicate `visible` filtering or add sync comment with constant (`src/mlx_tui/app.py:144` vs `history.py:101`).
4. Either hop `_tracked_model` assignment via `call_from_thread` or document GIL-atomic single-writer intent (`src/mlx_tui/models_pane.py:144`).
5. Optional: extract `_record_cancelled` helper to remove duplication (`src/mlx_tui/chat_pane.py:92`).

## Verification

- `uv run ruff check .` → All checks passed
- `uv run pyrefly check` → 0 errors
- `uv run pytest -q` → 225 passed
- Manual edge check: `python -c "from mlx_tui.history import _tok_int; _tok_int('   ')"` → `IndexError` before fix, `0` after proposed fix
