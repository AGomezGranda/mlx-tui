# MLX TUI v3 — History sparkline (degradation at long context)

**Date:** 2026-08-26
**Work Item:** n/a (implements §v3 of `docs/idea.md`)
**Status:** Complete

## Overview

In-memory per-model ring buffer of turn records `(ts, model, prompt_tok, out_tok, ttft_s, tok_s, ctx_len, cold, cancelled)` plus a footer braille strip docked above `#app-log` that plots y=tok/s over x=turn order with cell shade=ctx depth, cold/cancelled excluded — answering "does this model degrade at long context" without leaving the TUI.

## Current State

Baseline verified 2026-08-26: `uv run pytest -q` → **204 passed**; `uv run ruff check .` clean; `uv run pyrefly check` → 0 errors (strict, `src`+`tests`). Every phase must keep all three green; `uv run ruff format --check src tests` is the checkable set (36 files formatted; `main.py` does not exist so `src tests main.py` fails with `No such file`).

- **History domain today:**
  - `src/mlx_tui/history.py:1-30` — only `CHARS_PER_TOKEN_EST = 3.5`, `estimate_tokens(text)`, `trim_for_context(messages, max_est_tokens)` (trim is user-boundary scan, single reverse pass after ponytail simplifications). No buffer, no record type.
  - `src/mlx_tui/sse.py:7` imports `CHARS_PER_TOKEN_EST` for `token_accounting`; `src/mlx_tui/chat_pane.py:16` imports `trim_for_context`. Both seams are acyclic (`history` imports nothing from `sse`/`chat`).
  - `src/mlx_tui/chat.py:42-53` `TurnResult` frozen dataclass (`full_text, ttft, tok_in_str, tok_out_str, tok_s, finish_reason, skipped_frames`) plus `stream_turn(url, payload, *, user_chars, on_flush, flush_interval, on_active) -> TurnResult` (chat.py:55). `tok_in_str/out_str` carry `"20 (est)"` labelling — numeric parsing needed for history.
  - `src/mlx_tui/status.py:31-59` `ColdTracker` with `observe(state)` and `consume_cold() -> bool` (green→red→green arming). `src/mlx_tui/chat_pane.py:60` captures `cold = self.tui.cold_tracker.consume_cold()` per turn — same value used for the `· cold` stamp (chat_pane.py:161) and must be reused for the record.
  - `src/mlx_tui/chat_pane.py:65-138` `_run_turn(messages, cold)` is the single insertion point: builds payload from `trim_for_context` (chat_pane.py:68), posts via `stream_turn`, stamps `tok/s · TTFT`, appends assistant message, handles cancel (`_cancel_requested` flag, chat_pane.py:88-92 + `abort():139-153` with socket shutdown). Cancel is cooperative; `_run_turn`'s `except (StreamClosed, ReadError, RemoteProtocolError)` branch (chat_pane.py:111) checks `_cancel_requested` to distinguish cancel vs unreachable.
  - `src/mlx_tui/app.py:77-83` shared state: `cold_tracker`, `_tracked_model`, `latest_avail_gib`, `swap_machine` (swap.py), `effective_model() (app.py:138)` is the authoritative model id (union of `_tracked_model` and psutil cmdline). History's per-model series must key on `effective_model()` at record time, not on `config.model`.
- **UI shell:**
  - `src/mlx_tui/app.py:60` `DEFAULT_CSS` docks `#status-bar` top, `#app-log` bottom `height: 6` with `border-top`. `app.py:86-93` `compose` yields `Static(#status-bar)`, `TabbedContent(models/chat)`, `RichLog(#app-log)`. `on_mount:95` wires `httpx.AsyncClient` and `ModelsPane.rescan()`. `set_swap_ui:189` disables `#models-table` + `#chat-input` during swaps; history strip must respect this pattern (a `Static`, not an input, so no disable needed).
  - `src/mlx_tui/models_pane.py:24` `ModelsPane(Vertical)` owns `#swap-progress` + `ModelsTable`; `src/mlx_tui/chat_pane.py:25` `ChatPane(Vertical)` owns `#chat-stream, #chat-log, #chat-input`. Both reach App via `tui` property (`cast("MlxTuiApp", self.app)` at models_pane.py:33 / chat_pane.py:35) and hop workers via `self.tui.call_from_thread(...)` (widget lacks `call_from_thread` on textual 8.2.8, verified in prior plans).
  - No `#history-strip` widget exists yet. Placement approved by user: new `Static(id="history-strip")` docked **just above** `#app-log` (always visible, not tab-scoped). Height ~3 lines (2 braille rows + 1 legend line) → `height: 3` with `border-top: solid $primary` mirroring `#app-log`.
- **Tests:**
  - `tests/unit/test_history.py:1-66` — 7 tests on `estimate_tokens`/`trim_for_context`/`CHARS_PER_TOKEN_EST == 3.5` pin.
  - `tests/unit/test_chat.py:54-127` — `stream_turn` via `httpx.MockTransport` + `SseStreamBuilder`; `tests/conftest.py:24-222` `StubServer/StubHandler` + `AppHarness` (`app, pilot, server, wait_for, log_lines, app_log_lines, models_pane(), chat_pane()`). Integration tests drive the harness with `run_test()` pilot.
  - `tests/builders.py:9` `SseStreamBuilder` fluent builder for canned SSE bytes.
  - `pyproject.toml:7-12` deps `httpx, huggingface_hub, psutil, textual`; `pyproject.toml:42-48` dev `pyrefly, pytest, pytest-asyncio, ruff` strict.

### Decisions locked with the user

1. **Docked strip above `#app-log`**, always visible (not inside `#app-log`, not chat-tab-only). Mirrors idea doc's "footer strip, not a tab" and keeps the shared log pane intact.
2. **Braille 2D rendering (spec fidelity)** — y = tok/s buckets, x = turn order, cell shade = ctx depth quartiles. User chose Option A over ponytail single-row blocks; the simpler blocks remain a one-function fallback if the sparkline proves decoration.
3. **In-memory only, ring buffer per model** — one `deque(maxlen=N)` per repo id, so a swap doesn't smear two models into one line. Persistence shape fixed now (`TurnRecord` frozen) so append-JSONL + `--since` stays mechanical later.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| New state lives in `history.py`, no new top-level module | new `history_store.py` + `sparkline.py` split; grow `app.py` | `history.py` is already the token/context domain, has zero UI imports, and stays acyclic (`sse` → `history` only). One file + ~80 lines keeps YAGNI; split later if it exceeds ~150 lines. Follows `chat.py`/`history.py` functional-core convention every prior plan used. |
| `TurnRecord` frozen dataclass with 9 fields: `ts, model, prompt_tok, out_tok, ttft_s, tok_s, ctx_len, cold, cancelled` | 8-field idea-doc tuple (no `cancelled`, no `finish_reason`); include `skipped_frames` | idea doc lists `(ts, model, prompt_tok, out_tok, ttft_s, tok_s, ctx_len, cold)` — `cancelled` is required because cancelled turns are already flagged upstream (chat_pane `_cancel_requested`) and the plot must honour the flag rather than re-derive it; `cancelled` + `cold` → excluded from sparkline. `ts` via `time.time()` (wall clock for future JSONL), not `perf_counter`. `prompt_tok/out_tok` are ints (parsed from `TurnResult.tok_*_str` or 0 when cancelled). `ctx_len` is prompt-side context length at send time (see below). |
| `HistoryStore` = `dict[str, deque[TurnRecord]]` with per-model cap `_MAX_TURNS = 64` | global cap; `list` + manual eviction; N=32 or N=128 | 64 per model → 32 braille chars wide (2 columns per char) fits an 80-col terminal with margin; global cap would evict the idle model's history — idea doc says one series per model so a swap doesn't smear. 64 is the idea doc's "last N turns" with a concrete N; per-model deques are the stdlib ring buffer (no custom code). |
| `ctx_len` = numeric `prompt_tokens` from `TurnResult` when `tok_in_str` is non-est (usage path), else `estimate_tokens` of the *trimmed payload* actually sent | `prompt_tok + out_tok`; `len(messages)`; always `estimate_tokens` sum | `prompt_tokens` from mlx-lm's `include_usage` is the server's true context length (includes history); when missing or `tok_in_str` contains `" (est)"` the trimmed payload's `estimate_tokens` is the best client-side proxy and equals what `trim_for_context` computed. Sum of prompt+out would conflate prefill vs decode and is not "ctx depth". Phase 3 computes `ctx_len_estimate` pre-send, then after `stream_turn` sets `ctx_len = _tok_int(result.tok_in_str) if " (est)" not in result.tok_in_str else ctx_len_estimate` (`src/mlx_tui/sse.py:62-83`). |
| Braille renderer is a pure function `render_sparkline(records, *, width=32, height_rows=2) -> tuple[str, str]` that filters `cold or cancelled` turns, buckets `tok_s` into `height_rows * 4` dot rows, and returns plain braille + legend; shade is applied by the caller via `rich.text.Text` segments (`dim`/`""`/`bold`) based on `ctx_len` quartiles | single-row `▁▇` blocks for tok/s only; `plotext`/`rich.sparklines` dep; returning `Text` from `history.py` | idea doc explicitly rejects a plain tok/s line: "fast at 1k, slow at 24k — is inherently two-dimensional". Braille 2×4 dots per char gives height without extra terminal rows; shade via caller-applied style keeps it 2D in one widget without coupling `history.py` to `rich`/`textual`. `history.py` stays stdlib-only (see Risks). No new dep; ~30 lines (ponytail ceiling: if it exceeds 50, fall back to blocks). |
| HistoryStore owned by `MlxTuiApp` as `self.history: HistoryStore` | owned by `ChatPane`; module global singleton | App already owns shared `cold_tracker` (status.py:31) observed by poll and consumed by chat; history is cross-cutting (swap changes `effective_model`, sparkline must switch series immediately). App ownership makes `effective_model()` and `swap_machine` visible for series switching; `ChatPane` records via `self.tui.call_from_thread(self.tui.history.add, record)` hop (thread-safe; see Phase 1). |
| Footer widget = `Static(id="history-strip")` docked bottom just above `#app-log` | render into `#app-log` as styled lines; add as `RichLog` | idea doc: "Footer strip: last N turns ... drawn as a braille sparkline, cold first-turns excluded. ~30 lines." A `Static` is the established streaming pattern (`#chat-stream` at chat_pane.py:44, `#swap-progress` at models_pane.py:38). `RichLog` would scroll; a strip must be fixed-height. Textual dock order: `#history-strip` declared before `#app-log` so it sits directly above the log — **must be manually verified** (strip directly above log, not below) and has a one-line harness smoke assertion in Phase 3a; fallback `height:2` (one braille row + legend) if layout overflows. |
| Record insertion point = `ChatPane._run_turn` success path after `stream_turn` returns and after cancel branches, via `tui.call_from_thread` for both `history.add` and `update_history_strip` | record inside `chat.stream_turn` (UI-free) | `stream_turn` is UI-free and knows nothing about model ids or cold flags; `ChatPane` owns `cold`, `effective_model()`, and the trimmed payload size for `ctx_len`. Recording in the pane keeps `chat.py` pure and testable without UI. All `HistoryStore` mutations from the `thread=True` worker are hopped via `call_from_thread`; direct `self.tui.history.add(...)` on the worker thread is forbidden. |
| Exclude `cold` and `cancelled` from render, not from storage | drop cold/cancelled entirely; mark but still plot | idea doc: "cold first-turns excluded [from sparkline]" and "Cold and cancelled turns are already flagged upstream; the plot just honours the flags". Storage keeps them for future JSONL completeness; renderer filters `if r.cold or r.cancelled: continue`. |

## Implementation Phases

### Phase 1: Pure history domain — TurnRecord + HistoryStore

The record shape is frozen for the future JSONL path; the store is a per-model ring buffer. Zero UI changes, fully unit-tested, app untouched.

**Changes:**

- `src/mlx_tui/history.py` — extend from 30 lines to ~85 lines (imports: `time`, `collections.deque`, `dataclasses.dataclass`):

  ```python
  """Context-window trimming + history ring buffer for the sparkline (idea doc §v3)."""

  from __future__ import annotations

  import time
  from collections import deque
  from dataclasses import dataclass

  CHARS_PER_TOKEN_EST = 3.5  # existing
  _MAX_TURNS = 64


  @dataclass(frozen=True)
  class TurnRecord:
      """One chat turn, frozen so the future JSONL path is mechanical."""

      ts: float  # time.time(), wall clock
      model: str  # effective_model() at record time, or "—" when unknown
      prompt_tok: int
      out_tok: int
      ttft_s: float
      tok_s: float
      ctx_len: int  # prompt-side context length (usage or estimate of trimmed payload)
      cold: bool
      cancelled: bool = False


  class HistoryStore:
      """Per-model ring buffers; each model gets deque(maxlen=_MAX_TURNS).

      Not thread-safe — all mutations from ChatPane's thread=True worker
      must be hopped via App.call_from_thread (see Phase 3b). No internal lock;
      single UI-thread owner keeps the implementation stdlib-only and trivial.
      If a second writer ever appears, add a threading.Lock around
      add/series/models/all_records/clear.
      """

      def __init__(self, max_turns: int = _MAX_TURNS) -> None: ...

      def add(self, record: TurnRecord) -> None: ...

      def series(self, model: str) -> list[TurnRecord]: ...  # copy, oldest→newest

      def models(self) -> list[str]: ...  # sorted keys

      def all_records(
          self,
      ) -> list[
          TurnRecord
      ]: ...  # flat copy, sorted by ts, for future JSONL (uncovered until persistence)

      def clear(self, model: str | None = None) -> None: ...  # None → clear all
  ```

  Bodies: `__init__` stores `self._max = max_turns` and `self._by_model: dict[str, deque[TurnRecord]] = {}`. `add` does `self._by_model.setdefault(record.model, deque(maxlen=self._max)).append(record)`. `series` returns `list(self._by_model.get(model, ()))` (copy, not view). `models` returns `sorted(self._by_model)`. `all_records` returns flattened oldest→newest across models sorted by `ts` (YAGNI until persistence — kept for shape-freeze but uncovered until JSONL phase). `clear` either `del` one key or `self._by_model.clear()`.

- `tests/unit/test_history.py` — add 8 cases (existing 7 untouched):

  - `test_turn_record_is_frozen_and_has_nine_fields`: `TurnRecord(...).model == "m"` and `pytest.raises(FrozenInstanceError)` on assignment.
  - `test_store_add_and_series_preserves_order`: add 3 records for `"m"` with `ts=1,2,3` → `series("m")` is that order.
  - `test_store_per_model_isolation`: add 2 for `"a"`, 1 for `"b"` → `series("a")` len 2, `series("b")` len 1, `models() == ["a","b"]`.
  - `test_store_ring_eviction_at_64`: `HistoryStore(max_turns=3)`, add 4 for `"m"` → `series("m")` is last 3 (`ts=2,3,4`), first evicted.
  - `test_store_cold_and_cancelled_flags_survive`: add `cold=True` and `cancelled=True` records → flags round-trip, both appear in `all_records()`.
  - `test_store_clear_one_model`: add for `"a"` and `"b"`, `clear("a")` → `series("a")==[]`, `series("b")` unchanged; `clear()` empties all.
  - `test_max_turns_constant_is_64`: `HistoryStore()._max == 64` pins the "last N" choice.
  - `test_series_returns_copy_mutating_does_not_affect_store`: `lst = store.series("m"); lst.clear(); assert store.series("m") == original` — guards against returning the deque view directly.

**Success Criteria:**

#### Automated Verification:

- [x] new unit tests pass: `uv run pytest -v tests/unit/test_history.py`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:

- [x] `uv run python -c "from mlx_tui.history import HistoryStore, TurnRecord; s=HistoryStore(); s.add(TurnRecord(ts=1,model='m',prompt_tok=10,out_tok=5,ttft_s=0.1,tok_s=12.3,ctx_len=10,cold=False)); print(s.series('m'))"` prints one record

> Deviation (Phase 1): removed unused `import time` from `history.py` to keep `ruff check` clean; `time` will be imported in `chat_pane.py` where `time.time()` is actually used (Phase 3b). No behavior change.

### Phase 2: Braille sparkline renderer (pure)

One pure function that turns a filtered series into a fixed-height braille string plus a legend. No widget yet — this phase leaves a tested checkpoint the UI can call. `history.py` stays stdlib-only — no `textual`/`rich` imports.

**Changes:**

- `src/mlx_tui/history.py` — add pure renderer (stdlib only, returns `str`):

  ```python
  def render_sparkline(
      records: list[TurnRecord],
      *,
      width: int = 32,
      height_rows: int = 2,
  ) -> tuple[str, str]:
      """Braille sparkline for the sparkline strip.

      Filters ``cold``/``cancelled`` turns, buckets ``tok_s`` into
      ``height_rows * 4`` dot rows, encodes 2×4 dots per braille char
      (U+2800 base), shade is caller-applied via ctx quartiles (see
      app.py helper). Returns ``(braille_lines, legend)`` where
      ``braille_lines`` is ``height_rows`` lines joined by ``\\n`` and
      ``legend`` is ``f"{tok_s:.1f} tok/s · ctx {ctx_len} · {n} turns"``
      for the latest visible turn. Empty/filtered input →
      ``("", "no history yet — chat to build it")``.
      """
  ```

  Implementation notes (specified exactly so the plan is self-contained):

  - Filter: `visible = [r for r in records if not r.cold and not r.cancelled]`.
  - If empty: return `("", "no history yet — chat to build it")` (exact string pinned).
  - Slice to `width * 2` visible turns (each braille char is 2 columns wide) — `visible = visible[-(width * 2):]`.
  - Compute `tok_vals = [r.tok_s for r in visible]`; `lo = min(tok_vals)`, `hi = max(tok_vals)`; if `hi == lo`: set `hi = lo + 1.0` to avoid div/0, then clamp `level = 0` (bottom) — flat series renders as a flat bottom line; alternative `dot_rows//2` mid-height was considered but bottom is the braille analogue of a flat `▁`. Document the choice.
  - `dot_rows = height_rows * 4`; for each turn `i`, `level = int((tok_s - lo) / (hi - lo) * (dot_rows - 1))` clamped `0..dot_rows-1` (`0`=lowest, `dot_rows-1`=highest).
  - Braille encode — **single dot per turn** (not a filled bar `0..level`): each visible turn contributes exactly one raised dot at `row_global = dot_rows - 1 - level`, `col = i % 2` within the char. Packing: `chars = (len(visible) + 1) // 2` braille chars per row; `visible[2*k]` in left column of `chars[k]`, `visible[2*k+1]` in right column of `chars[k]` (odd `visible_len` → last char's right column blank). For each char position `(k, row_char)` where `row_char` is `0..height_rows-1` top-to-bottom, the two dots in that char are those turns whose `row_global` falls in `[row_char*4, row_char*4+3]` (char-local row `row_local = row_global - row_char*4`).
  - Bit table (standard U+2800): dot numbers / bits:
    ```
    1 4  →  0x01 0x08
    2 5  →  0x02 0x10
    3 6  →  0x04 0x20
    7 8  →  0x40 0x80
    ```
    Helper `def _bit(col: int, row_local: int) -> int:` where `col` 0=left,1=right, `row_local` 0..3 top-to-bottom inside the char:
    `bits = [0x01,0x02,0x04,0x40][row_local]` for `col==0`, `bits = [0x08,0x10,0x20,0x80][row_local]` for `col==1`. Helper `_braille_char(bits: int) -> str` returns `chr(0x2800 + bits)` with `bits==0` → `chr(0x2800)` replaced by `" "` for readability (blank cell). This table is the contract; two implementors must produce identical chars.
  - Shade: this function does **not** emit Rich styles. Shade is computed by the caller (`app.py:_shade_for_ctx` helper in Phase 4) from `ctx_len` quartiles `q1,q2,q3 = sorted(ctx_lens)[n//4], [n//2], [3*n//4]` → `dim`/`""`/`bold`. Keeping it in the caller preserves `history.py`'s stdlib-only invariant.
  - Legend: always `f"{visible[-1].tok_s:.1f} tok/s · ctx {visible[-1].ctx_len} · {len(visible)} turns"` — numeric `ctx_len` without k-scaling for Phase 2; k-formatting (`9.2k`) is deferred to Phase 4 polish. Exact format pinned by tests (substring `"X.Y tok/s"`).

  Alternative simpler fallback (documented, not implemented unless braille proves unreadable): `if height_rows == 1: use " ▁▂▃▄▅▆▇█"` blocks (ponytail lite).

- `tests/unit/test_history.py` — add 6 renderer tests (no mounting, pure):

  - `test_render_empty_and_cold_filtered_return_placeholder`: `[]` → `("", "no history yet — chat to build it")`; `[cold=True]` → same.
  - `test_render_single_turn_produces_one_braille_char_plus_legend`: `1` visible record with `tok_s=10` → `braille_lines` contains exactly one braille char in `U+2800..U+28FF` (chars == `(1+1)//2` == 1 per row, so `len(braille_lines.replace("\n","").strip()) == 1` when `height_rows==2` only one of the two rows is non-blank), second element contains `"10.0 tok/s"`.
  - `test_render_flat_tok_s_does_not_div0`: three records all `tok_s=5.0` → renderer returns without exception, all chars identical.
  - `test_render_cancelled_excluded`: 2 records, one `cancelled=True` → visible count 1, legend says `"1 turns"`.
  - `test_render_width_clipping`: 70 visible records with `width=32` → `chars == (min(64,70)+1)//2 == 32`; each braille line `len(line) == 32`, total `chars == 32` per row.
  - `test_render_height_rows_two_produces_two_lines`: `height_rows=2` → `braille_lines.count("\n") == 1` (two lines), `height_rows=1` → no newline.

**Success Criteria:**

#### Automated Verification:

- [x] new renderer tests pass: `uv run pytest -v tests/unit/test_history.py -k render`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:

- [x] `uv run python -c "from mlx_tui.history import HistoryStore, TurnRecord, render_sparkline; s=HistoryStore(); [s.add(TurnRecord(ts=i,model='m',prompt_tok=10,out_tok=5,ttft_s=0.1,tok_s=10+i,ctx_len=1000+i*100,cold=False)) for i in range(10)]; print(render_sparkline(s.series('m'))[0])"` prints braille

### Phase 3a: Footer strip wiring — store, strip, and switch (no recording)

Scaffold `HistoryStore` ownership, the always-visible strip, and model-switch hooks. No `ChatPane` recording yet — this phase is 2-file wiring plus one harness smoke test, stays within 15 min.

**Changes:**

- `src/mlx_tui/app.py`:

  - Imports: `from mlx_tui.history import HistoryStore, render_sparkline` (keep `from rich.text import Text` already imported), `from textual.css.query import NoMatches` already imported.
  - `__init__`: `self.history = HistoryStore()` after `self.swap_machine` (app.py:80).
  - `DEFAULT_CSS`: add

    ```
    #history-strip {
        dock: bottom;
        height: 3;
        border-top: solid $primary;
        padding: 0 1;
    }
    ```

    Declare `#history-strip` **before** `#app-log` in CSS and in `compose` so it sits directly above the log (textual docks bottom-up in source order). Explicitly call out as manual verification item and harness assertion; keep `height:3` with `height:2` fallback in Risks.
  - `compose` (app.py:86): insert `yield Static("", id="history-strip")` immediately before `yield RichLog(id="app-log", ...)` (i.e., inside the root, after `TabbedContent`). Leave `#status-bar` first.
  - Add `def update_history_strip(self) -> None:` (UI-thread only):

    ```python
    def update_history_strip(self) -> None:
        try:
            strip = self.query_one("#history-strip", Static)
        except NoMatches:
            return
        # Unknown model "—" has its own series and is rendered, not hidden —
        # otherwise cancelled/normal turns stored under "—" would grow invisibly.
        model = self.effective_model() or "—"
        records = self.history.series(model)
        braille, legend = render_sparkline(records, width=32, height_rows=2)
        if not braille:
            strip.update(Text(legend, style="dim"))
        else:
            strip.update(Text(f"{braille}\n{legend}", style=""))
    ```

  - Also call `self.update_history_strip()` in `on_mount` once to paint the empty placeholder after mounting.
  - Expose `self._tracked_model` already is App-private; history keying uses `effective_model() or "—"` so warm/restart paths and the unknown-model case are consistent.

- `src/mlx_tui/models_pane.py`:

  - After a successful warm swap (`run_warm_swap:144` where `_tracked_model = row.repo_id`) and after a successful boot (`run_boot:211` where `_tracked_model = plan.model_id ...`), add `self.tui.call_from_thread(self.tui.update_history_strip)` right after `self.tui.call_from_thread(self.tui.refresh_models)`. This switches the strip to the newly loaded model's series without waiting for the next 2 s poll. On failure paths do not switch (strip stays on old model).

- `tests/integration/test_app_integration.py` — add one harness smoke test (async):

  - `test_history_strip_renders_and_switches_on_swap(harness)`: activate Chat tab via `harness.pilot` or `harness.app` query, assert `harness.app.query_one("#history-strip", Static)` exists and initially shows `"no history yet"`; manually `harness.app.history.add(TurnRecord(..., model=(harness.app.effective_model() or "—"), cold=False))` for current model, call `harness.app.update_history_strip()`, assert `str(strip.renderable)` no longer contains `"no history yet"` and contains `"tok/s"`. Then monkeypatch `scan_models` to return a new repo, trigger `run_warm_swap` for that repo (or directly set `harness.app._tracked_model = "other/model"` and call `update_history_strip`), assert strip switches to `"no history yet"` for the new model, and switching back restores the old sparkline. Also assert dock order: `strip` is rendered above `#app-log` (query order or visual check in manual verification).

- `tests/unit/test_history.py` — no new unit tests this phase (Phase 1/2 cover domain).

**Success Criteria:**

#### Automated Verification:

- [x] new integration smoke test passes: `uv run pytest -v tests/integration/test_app_integration.py -k history_strip`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:

- [x] `uv run mlx-tui` — empty footer shows `no history yet — chat to build it` dim; `#history-strip` sits directly above `#app-log`, not below (if inverted, swap declaration order of the two yields in `compose`); fallback `height:2` noted.

> Deviation (Phase 3a): `Static` content checked via `strip.render()`/`strip.content` rather than `strip.renderable` (textual 8.2.8 API); import sorting fixed (`history` before `models_pane`) to keep `ruff check` clean. New test uses explicit `_tracked_model` switching instead of full `run_warm_swap` mock.

### Phase 3b: Record turns — ChatPane branches

Wire `ChatPane._run_turn` to record every completed turn (success and cancelled) into `HistoryStore` with correct `ctx_len` and thread hopping. This is the second 15-min increment after the scaffold.

**Changes:**

- `src/mlx_tui/history.py` — add module-level helper for tok parsing (extracted from Phase 3a's inline helper so it is unit-testable):

  ```python
  def _tok_int(s: str) -> int:
      """Parse tok_in_str/tok_out_str; "20 (est)" -> 20, digit-only check, else 0."""
      part = s.split()[0] if s else ""
      return int(part) if part.isdigit() else 0
  ```

- `src/mlx_tui/chat_pane.py`:

  - Imports: `import time`, `from mlx_tui.history import TurnRecord, _tok_int, estimate_tokens, trim_for_context` (or reuse existing import and add `_tok_int`).
  - Module-level `_tok_int` may live in `history.py`; if so import it here. Add one unit test for it in `tests/unit/test_history.py` (`test_tok_int_parses_est_and_digit_guards_sentinel`).
  - In `_run_turn` (chat_pane.py:66), **capture `model_at_send` once** and reuse (fixes double `effective_model()` call at chat_pane.py:73):

    ```python
    model_at_send = self.tui.effective_model() or "—"
    trimmed = trim_for_context(messages, _MAX_CONTEXT_TOKENS_EST)
    ctx_len_estimate = sum(estimate_tokens(m["content"]) for m in trimmed)
    payload: dict[str, object] = {
        "messages": trimmed,
        "stream": True,
        "max_tokens": _MAX_OUTPUT_TOKENS,
        "stream_options": {"include_usage": True},
    }
    if model_at_send != "—":
        payload["model"] = model_at_send
    ```

  - After `result = stream_turn(...)` success path (chat_pane.py:79), compute the true `ctx_len` per Design Decision (unified with `sse.py:62-83`):

    ```python
    ctx_len = (
        _tok_int(result.tok_in_str)
        if " (est)" not in result.tok_in_str
        else ctx_len_estimate
    )
    record = TurnRecord(
        ts=time.time(),
        model=model_at_send,
        prompt_tok=_tok_int(result.tok_in_str),
        out_tok=_tok_int(result.tok_out_str),
        ttft_s=result.ttft,
        tok_s=result.tok_s,
        ctx_len=ctx_len,
        cold=cold,
        cancelled=False,
    )
    self.tui.call_from_thread(self.tui.history.add, record)
    self.tui.call_from_thread(self.tui.update_history_strip)
    ```

    Note: `cold` is the `cold` arg passed into `_run_turn` (from `consume_cold()` at chat_pane.py:60). No direct `self.tui.history.add(...)` on the worker thread — always hopped.

  - Cancelled path: the existing `if self._cancel_requested:` early return (chat_pane.py:88) and the `except (StreamClosed, ...)` cancelled branch (chat_pane.py:112) both must also record a cancelled turn so the flag survives for future JSONL, but the sparkline will filter it. Add before each `return`/system-line (full constructor, no `...` placeholders):

    ```python
    # cancelled — no tok/s, filtered from sparkline but stored for JSONL
    cancelled_record = TurnRecord(
        ts=time.time(),
        model=model_at_send,
        prompt_tok=0,
        out_tok=0,
        ttft_s=0.0,
        tok_s=0.0,
        ctx_len=ctx_len_estimate,
        cold=cold,
        cancelled=True,
    )
    self.tui.call_from_thread(self.tui.history.add, cancelled_record)
    self.tui.call_from_thread(self.tui.update_history_strip)
    ```

    `prompt_tok/out_tok` for cancelled are `0` (no tokens produced); `ctx_len` is the estimate (no `result` available). `ts` is `time.time()` wall clock, matching the success path.

  - Error (unreachable) path: do **not** record a history entry (no turn completed) — matches idea doc's "cold and cancelled turns are already flagged upstream; the plot just honours the flags" — only real turns (including cancelled) enter the buffer. Use `call_from_thread` only for the system line and `end_turn`.

  - `end_turn` (chat_pane.py:174) stays unchanged; input re-enable still gated by `swap_machine.busy`.

- `tests/unit/test_history.py` — add `test_tok_int_parses_est_and_digit_guards_sentinel`: `assert _tok_int("20 (est)") == 20; assert _tok_int("12") == 12; assert _tok_int("—") == 0; assert _tok_int("") == 0`.

- `tests/integration/test_app_integration.py` — add harness-driven tests (async, using `AppHarness`, `Static`, `Input`):

  - `test_history_records_one_turn_and_shows_strip(harness)`: activate Chat tab (`await harness.pilot.click("#chat")` or `harness.app.query_one(TabbedContent).active = "chat"` then `await harness.pilot.pause()`), focus input (`harness.chat_pane().query_one("#chat-input", Input).focus(); await harness.pilot.pause()`), clear `harness.app.history.clear()`, set `harness.server.mode = "ok"; await harness.app._poll()` to go green, set `inp.value = "hi"; inp.focus(); await harness.pilot.press("enter")`, then `assert await harness.wait_for(lambda a: len(a.history.series(a.effective_model() or "—")) == 1)`; assert `series[0].cold is False`, `tok_s > 0`, `model == (harness.app.effective_model() or "—")`, and `str(harness.app.query_one("#history-strip", Static).renderable)` is not `"no history yet"` and contains `"tok/s"`.

  - `test_history_cancelled_excluded_from_sparkline(harness)`: set `harness.server.mode = "slow"; await harness.app._poll()`, activate Chat tab and focus input as above, `harness.app.history.clear()`, submit `"hi"`, wait for `any("you ›" in t for t in harness.log_lines())`, then `await harness.pilot.press("escape")`, wait for `"cancelled — request aborted"` in log and `len(history.all_records()) == 1` with `cancelled is True`, then `harness.app.update_history_strip(); await harness.pilot.pause()` and assert strip still shows `"no history yet"` (cancelled filtered).

  - `test_history_error_not_recorded(harness)`: `harness.server.mode = "error500"; await harness.app._poll()`, activate Chat tab + focus, `harness.app.history.clear()`, submit `"hi"`, wait for `"server error"` in log, `await asyncio.sleep(0.3)`, assert `harness.app.history.all_records() == []` and strip still shows `"no history yet"`.

  - Extend `test_history_per_model_isolation_and_cold_excluded` (from 3a) or add `test_history_cold_excluded`: manually `harness.app.history.add(TurnRecord(ts=time.time(), model=(harness.app.effective_model() or "—"), prompt_tok=10, out_tok=5, ttft_s=0.1, tok_s=12.3, ctx_len=100, cold=True))`; `harness.app.update_history_strip(); await harness.pilot.pause()` → assert strip shows `"no history yet"` (cold filtered); then submit one normal turn and assert `series` len includes the cold record but sparkline legend shows `"1 turns"` not `"2 turns"`, and `all_records()` len is 2. Swap model via `harness.app._tracked_model = "other/model"` + `update_history_strip` and assert isolation (`series(old_model)` len 2, `series(new_model)` empty).

  Note: `AppHarness.wait_for` polls the App; strip content is checked via `harness.app.query_one("#history-strip", Static).renderable` (a `Text` or `str`; `str(renderable)` contains braille). Initial `Tab` is `"models"` per `tests/conftest.py:220`, so Chat tab activation + `Input.focus()` is required before `press("enter")`.

**Success Criteria:**

#### Automated Verification:

- [x] new integration tests pass: `uv run pytest -v tests/integration/test_app_integration.py -k history`
- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`

#### Manual Verification:

- [x] `uv run mlx-tui` against the stub or a real server: after one prompt the braille appears (2 rows) with legend `X.Y tok/s · ctx N · 1 turns` (Phase 2 format); TTFT/tok_s stamps still appear in `#chat-log`
- [x] cold first turn after a simulated red→green cycle is stamped `cold` and does **not** move the braille sparkline (filtered) but appears in `history.all_records()`
- [x] cancel a slow stream with `esc` → dim `cancelled — request aborted` appears and the strip does not advance (cancelled filtered, but `history.all_records()` increments by one with `cancelled=True`)
- [x] swap models (warm or restart) → strip switches to the target model's series (old model's sparkline reappears when switching back); unknown-model `"—"` series is rendered when `effective_model()` is `None`
- [x] error path (`error500`/truncated) → no history record added, no fake stamp

### Phase 4: Polish, README, final gates

Shade, empty-state polish, documentation catch-up, and the full-gate finale. No behavioral shape changes beyond the strip's shade/text polish. `history.py` stays stdlib-only.

**Changes:**

- `src/mlx_tui/app.py` — shade polish (keeps `history.py` acyclic): add helper `_shade_for_ctx(ctx_lens: list[int]) -> list[str]` or inline quartile logic in `update_history_strip` that maps each visible turn's `ctx_len` to a Rich style `dim` (low quartile), `""` (mid), `bold` (high) and builds a `Text` with per-segment styles for the braille chars. Keep `render_sparkline` returning `(str, str)` for unit tests; `update_history_strip` is the only place that imports `Text` and applies styles. If shade proves noisy, keep the plain braille — the phase is feature-complete either way. Do **not** add `rich` imports to `history.py`.

- `README.md` — add under Keys/Config: "History strip — footer braille sparkline above the log, always visible; shows last 64 turns of the current model as y=tok/s over x=turn order (shade=ctx depth via dim/normal/bold quartiles), cold/cancelled excluded; in-memory only — future `--since` will read JSONL." Keep existing sections intact.

- `tests/unit/test_history.py` — if shade helper was added to `app.py`, add one test that exercises the helper directly (e.g. `from mlx_tui.app import _shade_for_ctx; assert _shade_for_ctx([100,200,300,400])[0]=="dim"` etc) or a harness test that low-ctx vs high-ctx records produce different styles in `strip.renderable`. No `Text` return from `history.py` to test.

- Final sweep: remove any dead import left by the preceding phases (ruff flags them); confirm no new dep was added to `pyproject.toml`.

**Success Criteria:**

#### Automated Verification:

- [x] whole suite green: `uv run pytest -q`
- [x] lint clean: `uv run ruff check .`
- [x] types clean: `uv run pyrefly check`
- [x] entry intact: `uv run mlx-tui --help` prints usage

#### Manual Verification:

- [x] full matrix against a live `mlx_lm.server` (or the stub's ok/slow modes): cold turn excluded, cancellation excluded, per-model isolation holds, shade varies with prompt length (short prompt dim vs long history-fed prompt bold), 64-turn cap evicts oldest
- [x] README claims match observed behavior

## Out of Scope

- Persistence (append-JSONL + `--since` flag) — shape is frozen now; implementation is a follow-up when yesterday's chat is actually wanted. No file I/O, no database, no migration.
- A Metrics tab or dedicated history tab — idea doc explicitly says "Once the status bar has memory and chat has per-turn tok/s, there's nothing left for a standalone Metrics tab."
- Sorting/filtering/searching history, per-model comparison view, or cross-model overlay — one series per model, current model only.
- Multi-backend support, quantization runner, server-log tailing, conversation persistence, agentic harness (idea doc out-of-scope).
- New config keys, host/port hot-rebind, pidfile changes (v1 concerns).
- HF search/download changes (v2 concerns); `/` binding stays on `ModelsTable`.
- A new `tqdm`-style progress bar for history (no background work to report).
- Any change to `health_timeout` math (`swap.py:71`), `fits_headroom` margin (20%), or `ColdTracker` arming rules.

## Risks & Mitigations

- **Braille readability vs terminal font** → braille chars (U+2800 block) render blank on some fonts; provide the ponytail fallback documented in Phase 2 (`▁▇` blocks when `height_rows==1`) and keep the legend line (`"12.3 tok/s · ctx N · M turns"` pinned format) always visible so the strip is useful even if braille is faint. Tests assert the legend always contains numbers, not just graphic.
- **Blocked UI thread from rendering** → renderer is pure, <1 ms for 64 records; no worker needed. `update_history_strip` runs on the UI thread only, enqueued via `call_from_thread` from chat workers — never does I/O. `HistoryStore` mutations are also hopped via `call_from_thread`; no lock needed while single-writer holds.
- **Model-id churn mis-attributes a record** → key on `model_at_send = effective_model() or "—"` captured *before* `stream_turn` (at send time), not after, so a swap landing mid-turn doesn't steal the record. Verified against `models_pane.run_warm_swap` tracking semantics (`_tracked_model` authoritative over cmdline). Reuse of `model_at_send` for both payload and record avoids the double-call race at old chat_pane.py:73.
- **Cancelled turns pollute tok/s average** → cancelled records are stored with `tok_s=0` and `cancelled=True`, filtered before every render; integration test asserts cancelled doesn't advance the strip.
- **pyrefly strict on new dataclass/HistoryStore** → `history.py` is stdlib-only (no `textual`/`httpx`/`rich`), ships with explicit types; `app.py`/`chat_pane.py` import it with `HistoryStore` type; add `from __future__ import annotations` already present.
- **Strip height changes layout** → `#history-strip` is `height: 3` (≈2 braille rows + legend) and docks bottom-up above `#app-log` (`height: 6`). Keep `TabbedContent` `height: 1fr` so it shrinks rather than overflows; manual verification checks both tabs still fill and `#history-strip` is directly above `#app-log`. If layout overflows on small terminals, lower strip to `height: 2` (one braille row + legend) — one-line CSS change.
- **Ring buffer growth unbounded if model ids are ever untrusted** → keys come only from `effective_model() or "—"` (server-proven or cached scan), not user input; attacker-controlled ids would already be in `scan_cache_dir()` semantics. Cap to `_MAX_TURNS` per key bounds memory at `models * 64 * record_size` (~tens of KB).
- **Empty/cold-only series shows blank strip** → placeholder `"no history yet — chat to build it"` dim line prevents a confusing empty strip; tests pin both empty and cold-only paths.
- **Textual Static render race after mount** → `update_history_strip` guards `NoMatches` (strip missing during early mount/teardown) and is idempotent; workers outliving the screen are already `NoMatches`-guarded per repo convention.

