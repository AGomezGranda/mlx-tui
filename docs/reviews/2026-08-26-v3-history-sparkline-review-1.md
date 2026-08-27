# Plan Review: MLX TUI v3 — History sparkline (degradation at long context)

**Date:** 2026-08-26
**Target:** docs/plans/2026-08-26-v3-history-sparkline.md
**Review:** 1
**Verdict:** REVISE

## Assessment

The plan is well-shaped: per-model ring buffers in `history.py`, a pure braille renderer, and a footer `Static` above `#app-log` are the minimal answer to the idea doc's two-dimensional degradation question. Baseline claims verified (`204 passed`, `ruff` clean, `pyrefly` 0 errors). Implementation cannot proceed as written — a thread-safety contradiction, an underspecified braille encoder, and a ctx_len derivation that conflicts with its own design decision require fixes.

## Cross-Cutting Themes

- **Thread vs UI ownership leak:** Phase 1 claims `HistoryStore` is UI-thread-only, Phase 3 then mutates it from `ChatPane._run_turn` (`@work(thread=True)`). The same split appears for `update_history_strip` (correctly hopped) vs `history.add` (not hopped).
- **Spec vs implementation drift:** Design Decision 5 (ctx_len = usage prompt_tokens when available) vs Phase 3 code (always `estimate_tokens` sum); plain `str` renderer vs Phase 4 `Text` overload; `model="—"` storage vs `if model else []` display.
- **Underspecified pure function:** Braille packing, bar-vs-dot, legend format, and shade handling are described narratively but not pinned enough for a self-contained implementation.

## Findings

### Critical

- **[Correctness] Phase 3 `chat_pane.py:66` — worker-thread mutation of `HistoryStore`:** `_run_turn` runs `thread=True`. Plan writes `self.tui.history.add(record)` directly on that thread then hops only `update_history_strip` via `call_from_thread`. `HistoryStore` is `dict[str, deque]` with no lock. `dict.setdefault`+`deque.append` from a worker thread racing `series()`/`all_records()` on the UI thread is unsafe; CPython GIL does not make `dict` iteration vs mutation safe. Fix: hop the mutation too (`call_from_thread(self.tui.history.add, record)`) or add a `threading.Lock`, and update the "No locks (UI thread only)" claim in Phase 1. `src/mlx_tui/chat_pane.py:65-138`, `src/mlx_tui/app.py:77-83`.
- **[Correctness] Phase 2 `history.py` render_sparkline — braille encoder unspecified to compilable level:** Description says "2 columns × height_rows rows" but never defines whether each turn is a single dot vs a filled bar, how `level` maps to bits across the two stacked chars, or how odd `visible_len` packs (1 char with blank column vs 2 chars). The `level` formula `int((tok_s-lo)/(hi-lo)*(dot_rows-1))` + inversion `row_global = height_rows*4-1-level` is directional, not a bit table. Two implementors will ship different visuals and the Phase 2 tests (`test_render_single_turn_produces_one_braille_char`) will be ambiguous. Fix: specify exact helper e.g. `def _dot(col,row)->bit` table (1→0x01,2→0x02,3→0x04,7→0x40,4→0x08,5→0x10,6→0x20,8→0x80) and whether to fill `0..level` or single dot, and packing rule `chars = (len(visible)+1)//2` with `visible[2*i]` in left column, `visible[2*i+1]` in right.

### Major

- **[Correctness] Phase 3 ctx_len derivation contradicts Design Decision 5:** Design Decisions table says "when `usage.prompt_tokens` available use that as true ctx_len, else `estimate_tokens` of trimmed payload". Phase 3 instead computes `ctx_len = sum(estimate_tokens(m["content"]) for m in trimmed)` *before* `stream_turn` and never overrides with `prompt_tok` parsed from `TurnResult`. Result: ctx shade is systematically low when server reports real prompt tokens (which include history and can be 2-4× the estimate at long context). The degradation signal the sparkline exists to show is then wrong. Fix: compute `ctx_len_estimate` pre-send, then after `stream_turn` set `ctx_len = _parse_tok(result.tok_in_str) if " (est)" not in result.tok_in_str else ctx_len_estimate`, or keep estimate-only and amend the design decision to match `src/mlx_tui/sse.py:62-83`.
- **[Correctness] Phase 3 `app.py:86-93` dock order assumption unverified:** Plan asserts "`#history-strip` declared before `#app-log` so it sits directly above the log (textual docks bottom-up in source order)". Textual dock stacking is order-sensitive and easy to invert; no baseline test proves this. If inverted, the 3-line strip will be at the very bottom and the 6-line log will be above it (or vice versa), failing manual verification. Fix: note as explicit manual check or add a one-line textual smoke test in Phase 3 integration, and state fallback `height:2` alternative already in Risks.
- **[Correctness] Phase 3 unknown-model display leak:** `model_at_send = self.tui.effective_model() or "—"` stores cancelled/normal turns under key `"—"` when `effective_model()` is `None` (`src/mlx_tui/app.py:138`). `update_history_strip` then does `records = self.history.series(model) if model else []` so the `"—"` series is never rendered and grows invisibly (per-model cap bounds it, but user sees perpetual "no history yet" despite turns existing). Fix: either store under `""`/`"unknown"` and show that series, or change `update_history_strip` to `series(model or "—")`, and document intended behavior for the `None` case (`src/mlx_tui/app.py:138`, `src/mlx_tui/chat.py:73`).
- **[Plan Mechanics] Phase 3 is too large for a single incremental step:** One phase touches `app.py` (CSS+compose+`update_history_strip`+`on_mount` call), `chat_pane.py` (capture `ctx_len`/`model_at_send`, add `history.add` on 3 branches, `_parse_tok` helper), `models_pane.py` (2 swap hooks), and 2 async harness tests. Estimate 30-45 min, violates the skill's 2-15 min action guideline and makes failure attribution hard. Fix: split into 3a wiring (store+strip, no recording) and 3b recording (chat_pane branches) — or at minimum list each file edit as a checked sub-step.
- **[Test Coverage] Phase 3 integration tests underspecified/flaky:** `test_history_records_one_turn_and_shows_strip` uses `await harness.pilot.press("enter")` without first activating the Chat tab or focusing `#chat-input` via `harness.chat_pane()` helper, and waits on `a.query_one("#history-strip", Static).renderable` but harness fixtures are created with `TabbedContent(initial="models")`. Missing coverage: cancelled-excluded (`cancelled=True` filtered, still in `all_records`), error path not recorded, swap switching asserts on `sorted(models())`. Fix: define exact harness steps (activate chat tab, `inp.focus()`, `pilot.press`, `wait_for` on `len(app.history.series(model))==1`), and add the cancelled/error cases to prevent regression.
- **[Architecture] Phase 4 shade polish violates `history.py` acyclic stdlib-only invariant:** Plan's Risks note says `history.py` must stay stdlib-only (no `textual`/`rich`). Phase 4 then proposes `render_sparkline` return a `rich.text.Text` with per-segment `dim`/`bold` styles, importing `rich` into `history.py`. This couples the domain module to the UI toolkit. Fix: keep `render_sparkline` returning `(str,str)` and add a pure helper `ctx_shade(ctx_lens) -> list[str]` or a second function `sparkline_text(...) -> Text` in `app.py`/`history.py` under `TYPE_CHECKING` guard, or keep shade logic in `app.update_history_strip` where `Text` already lives (`src/mlx_tui/app.py:14`).

### Minor

- **[Correctness] `chat_pane.py:73` double `effective_model()` call:** Payload's `model` is derived from a second `effective_model()` call after `model_at_send` was captured. A swap landing between lines mis-attributes the request body vs history record. Fix: compute `model_at_send` once and reuse `if model_at_send != "—": payload["model"]=model_at_send`.
- **[Correctness] Phase 3 `_parse_tok` fragile:** `int(s.split()[0]) if s.split()[0].isdigit() else 0` assumes `tok_in_str` never contains sign or decimal; `token_accounting` (`src/mlx_tui/sse.py:77`) now returns `f"{user_chars/3.5:.0f} (est)"` which is digit-only, but future change to `"—"` sentinel would silently produce 0. Fix: use `s.split()[0].isdigit()` guard is fine, but name it `_tok_int` at module level and add one unit test.
- **[Correctness] Legend format drift:** Phase 2 spec says `f"{tok_s:.1f} tok/s · ctx {ctx_len} · {len(visible)} turns"`; Risks/out-of-scope say `"12.3 tok/s · ctx 9.2k/32k"`; test pins `"10.0 tok/s"` substring only. Pin one format in the plan (recommend the numeric form without k-scaling for Phase 2, defer k-formatting to Phase 4 polish).
- **[Plan Mechanics] Cancelled-record snippet incomplete:** Phase 3 cancelled path shows `TurnRecord(..., tok_s=0.0, ...)` with `...` placeholders; required fields `ts`, `model`, `prompt_tok`, `ttft_s`, `ctx_len` omitted. Implementor must guess. Fix: expand to full constructor mirroring success path with `prompt_tok=_parse_tok(result?)` or `0`, `ctx_len=ctx_len`, `ts=time.time()`.
- **[Verification] Baseline `ruff format` command references missing file:** `uv run ruff format --check src tests main.py` in Current State fails with `main.py: No such file (os error 2)`; the checkable set is `src tests` (36 files formatted). The Phase success criteria correctly use `ruff check .` — align the narrative to `src tests`.
- **[Architecture] History store helper proliferation:** `all_records()` sorted by `ts` across models was added for future JSONL but not used until persistence. YAGNI — keep it but note it is uncovered until persistence phase, or drop from Phase 1 and add with JSONL.
- **[Test Coverage] Missing unit for `series()` immutability:** `series()` returns `list(deque)` copy — test should assert mutating the returned list does not affect store (a common defect when returning the deque view directly).

### Suggestions

- Keep braille `width` default at `32` but derive from terminal width at runtime in Phase 3 (`self.size.width // 2 - margin`) — a one-line upgrade when available, with 32 as floor.
- Reuse `SseStreamBuilder` (`tests/builders.py:9`) in new integration tests for explicit `usage` injection rather than relying on `harness.server.mode="ok"` global stub — less flaky than hunting the last POST in `server.requests`.
- Consider exposing `HistoryStore` as `history: HistoryStore = HistoryStore()` at module level for quick repl checks (`python -c` example already in Manual Verification) — low value, leave out unless repeatedly useful.

## Strengths

- Per-model `deque(maxlen=64)` correctly isolates model series so a swap does not smear — the only stdlib ring buffer needed, no custom eviction.
- `TurnRecord` frozen dataclass with `ts` via `time.time()` is future-proof for append-JSONL; flags `cold`/`cancelled` honour upstream signals instead of re-deriving.
- Pure `render_sparkline(records)->(str,str)` keeps rendering unit-testable without mounting Textual; `cold`/`cancelled` filtered at render not storage preserves auditability.
- Insertion point `ChatPane._run_turn` is right — `chat.py:55` stays UI-free and testable via `httpx.MockTransport`, while the pane owns `cold`, `effective_model()`, and trimmed payload size.
- Update hooks on warm/restart success (`models_pane.py:144`/`212`) switch the strip immediately without waiting for the next 2 s poll — marker hygiene matches `swap.py:71` timeout math.

## Recommended Changes

1. **Hop or lock `HistoryStore` mutations** — change Phase 3 to `self.tui.call_from_thread(self.tui.history.add, record)` or add `threading.Lock` in `HistoryStore` and wrap `add/series/models/all_records/clear` (Critical #1).
2. **Fully specify braille encoding** — add bit table, fill rule, and packing rule to Phase 2 implementation notes and pin `test_render_single_turn_produces_one_braille_char` to `U+2800` range + exact `chars == (len(visible)+1)//2` (Critical #2).
3. **Unify `ctx_len` definition** — amend either Design Decision 5 or Phase 3 capture to match: `ctx_len = parsed prompt_tokens when non-est else estimate` with code snippet (Major #1).
4. **Verify dock order** — add manual verification "strip sits directly above log, not below" or a harness assertion on widget geometry; keep `height:3` but note `height:2` fallback (Major #2).
5. **Fix unknown-model display** — define whether `"—"` is rendered or hidden and adjust `update_history_strip` accordingly (Major #3).
6. **Split Phase 3** into 3a (store+strip scaffold + one `update_history_strip` unit test) and 3b (chat recording branches + integration tests) to stay within 15-min steps (Major #4).
7. **Flesh out integration tests** — require Chat tab activation, `Inp.focus()`, and `wait_for(len(series)==1)`; add cancelled-filtered and error-not-recorded cases (Major #5).
8. **Keep `history.py` free of `rich`** — move `Text` styling to `app.py` helper or add `sparkline_text` in `app.py` instead of returning `Text` from `history.py` (Major #6).
9. **Align commands and legend** — change Current State's `ruff format --check` to `src tests`, pin legend format to `"{tok_s:.1f} tok/s · ctx {ctx_len} · {n} turns"` for Phase 2, and expand cancelled `TurnRecord` snippet to full constructor (Minor #4-5).
10. **Deduplicate `effective_model()` capture** in `_run_turn` — capture once before payload construction (Minor #1).

