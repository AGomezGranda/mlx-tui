# Plan Review: Gap Analysis — idea.md vs Actual Implementation

**Date:** 2026-08-29
**Target:** docs/plans/2026-08-29-gap-analysis.md
**Review:** 1
**Verdict:** APPROVE

## Assessment

Doc-only audit plan is well-scoped and rigorously evidenced — 31-file map, 10-plan lifecycle table, and full `idea.md` matrix with dispositions — and correctly gates all code changes out of scope. Two mechanical defects block green implementation: ~35% of `src:line` refs have drifted after the SRP package split, and the Phase 2 row-count gate `grep -c "| idea.md:"` will always fail, so the plan cannot pass its own automated criteria as written. Fix those, tighten one phase-scope ambiguity, and it is approvable.

## Cross-Cutting Themes

**Stale `file:line` after SRP split undermines verifiability.** Plan correctly warns at `docs/plans/2026-08-29-gap-analysis.md:133` about "stale path after `2026-08-27-srp-package-split.md` package moves" and prescribes a `grep -o ... | while read` file-existence check, but the same risk applies to line numbers. Spot-check shows `status.py:8 → 16`, `process.py:12 → 35`, `models.py:28 → 39`, `history/store.py:58 → 37`, `search_screen/__init__.py:20 → 36`, `serverctl.py:37→41 / 54→48 / 66→57`. The existence check still passes (95 refs, 0 missing), but the manual spot-check in Phase 2 (`chat_pane/turn.py:169`, `history/sparkline.py:137`, `app/__init__.py:35`) will wobble if drift continues. Root cause is single-point audit: Appendix is source of truth for Phase 2, so drift there propagates.

**Doc-lifecycle fix is sound, but its home moves mid-plan.** Appendix `2026-08-26-v2-hf-search-and-download.md:6` Draft vs Complete is correctly identified at `docs/plans/2026-08-29-gap-analysis.md:151` and called out in Risks at `:135`. Phase 2 at `:88` says fix "included here or deferred to Phase 3 at implementer discretion" while Phase 3 at `:110` says "If `v2` plan status fix was deferred from Phase 2, edit ... in this phase". Implementer can reasonably do it twice or not at all; gated criterion in Phase 3 (`grep -q "^\*\*Status:\*\* Complete" ...`) is conditional but written as if always required.

## Findings

### Critical

_None — as a pure-doc plan with no `src/` edits, no implemented-as-written breakage; defects are gate failures, not runtime breaks._

### Major

- **[Correctness][Phase 2 — Automated Verification]** Row-count gate is broken: `grep -c "| idea.md:" docs/plans/2026-08-29-gap-analysis.md ≥ 15` returns **1**, not ≥15. Appendix rows place `idea.md:` inside backticks in the first column (e.g. ``Two tabs ... `idea.md:92` |``), so the pattern `| idea.md:` never matches. `grep -c "idea.md:"` returns 83. As written, Phase 2 can never be marked green. Fix: `grep -c "idea.md:"` or `grep -c "|.*idea\.md:"`.
  - Evidence: `docs/plans/2026-08-29-gap-analysis.md:97`; reproduced `default.bash:1` → 1 vs 83.

- **[Plan Mechanics][Current State — Source tree line refs]** ~35% of `src:line` refs are off by 4–23 lines post-split, failing lens "Symbols exist with matching names/signatures". Spot-checked against `src/*` HEAD:
  - `src/mlx_tui/status.py:8 classify_liveness()` → actual `status.py:16`; `status.py:8` is `class MemorySnapshot` (`src/mlx_tui/status.py:16`).
  - `src/mlx_tui/process.py:12 find_server_pid` → actual `process.py:35` (`src/mlx_tui/process.py:35`); `process.py:12` is blank.
  - `src/mlx_tui/process.py:48 model_from_cmdline` → actual `process.py:54`.
  - `src/mlx_tui/serverctl.py:37 run_command` → actual `serverctl.py:41`; `54 spawn_command → 48`; `66 spawn_with_grace → 57`.
  - `src/mlx_tui/models.py:28 _is_mlx_model` → actual `models.py:39` (`models.py:28` is `_REQUIRED_FILES`).
  - `src/mlx_tui/history/store.py:58 TurnRecord/HistoryStore` → actual `store.py:37` / `49`; `store.py:58` is blank.
  - `src/mlx_tui/search_screen/__init__.py:20 SearchScreen` → actual `search_screen/__init__.py:36` (`__init__.py:20` is `ResultsTable`).
  - `src/mlx_tui/models.py:28` appears twice (Current State and Models table) with same drift.
  - Why it matters: Phase 2 manual verification explicitly asks to "Spot-check 3 evidence refs (e.g. `chat_pane/turn.py:169` Markdown once, `history/sparkline.py:137` shade, `app/__init__.py:35` ctrl+n) — file opens at cited line and matches claim". Drift erodes trust in audit that *warns* about stale refs at `docs/plans/2026-08-29-gap-analysis.md:133`. Fix: re-pin all `file:line` to HEAD (one `python3 -c` pass) or relax to `file` without line, or note "line refs pinned to 2026-08-29 baseline, may drift ±10".

- **[Correctness][Appendix — Gap Matrix vs idea.md refs]** A few `idea.md:line` refs are unverified or drifted; audit strip should note tolerance. Example: `docs/plans/2026-08-29-gap-analysis.md:165` Shape `idea.md:92` is correct (ASCII art `idea.md:93`), but `idea.md:168` context-bar amber claim at `:210` cites `history/tokens.py:24 trim_for_context(8000)` / `chat_pane/turn.py:22` hardcoded `8000` — actual `chat_pane/turn.py:22` is `_MAX_CONTEXT_TOKENS_EST = 8_000` correct, but `history/tokens.py:24` is `def trim_for_context` signature, not the `8000` constant. Not blocking, but audit's "every `idea.md` claim has status and `file:line` evidence" at `:88` invites line-precise pushback.

### Minor

- **[Plan Mechanics][Phase 1 — Success Criteria / Phase 2 — Evidence refs]** `grep -o "src/mlx_tui/[^ ]*:[0-9]*"` loop correctly validates file existence (95 refs, 0 missing — verified), but does not validate line existence. Recommend adding a second loop that checks `test $(wc -l < $f) -ge $line` or simply document that lines are best-effort. Cite at `docs/plans/2026-08-29-gap-analysis.md:98`.

- **[Architecture & Patterns][Design Decisions — Gap doc lives at `docs/plans/YYYY-MM-DD-gap-analysis.md`]** Choice is defended against `docs/GAP.md` / `docs/reviews/` at `docs/plans/2026-08-29-gap-analysis.md:62`, but contradicts house convention: `docs/reviews/` is for `review-plan` output (this file), while `docs/plans/` lifecycle is Draft→Approved per `create-plan` Skill Step 7. A gap *audit* is not a plan to implement; parking it in `docs/plans/` inflates that directory's "10 files (statuses at read time)" inventory at `:37` and forces future `ls docs/plans/` readers to mental-filter. Approved trade-off (discoverability alongside the 10 plan files) but worth a one-line note in Out of Scope that this file will never gain code phases — it is a living audit, not a build plan.

- **[Correctness][Current State — Baseline citations]** Baseline `pyproject.toml:14` `httpx>=0.28 ...` and `pyproject.toml:24` entry point are cited at `docs/plans/2026-08-29-gap-analysis.md:13`. Actual `pyproject.toml:10-14` holds the four deps (`httpx` at :10, `textual` at :14) and entry at :24 is correct (`mlx-tui = "mlx_tui.app:main"` at `pyproject.toml:24`). Minor cite drift; suggest `pyproject.toml:10` or `9-14`.

- **[Test Coverage][Phase 2 — Manual Verification]** Manual check "Unimplemented Summary lists exactly 5–7 items, each with disposition, no duplicate of Implemented rows" at `docs/plans/2026-08-29-gap-analysis.md:103` is slightly inconsistent with Appendix Unimplemented Summary at `:265` which lists **7 rows** but header says "the 5 items this document tracks" and footnote at `:276` says "Items 5/7 are not code gaps but process/doc gaps ... code gaps are 1–4". Keep 7 rows, fix header to "7 items (4 code gaps + 3 process/doc)" or collapse to 5.

- **[Plan Mechanics][Out of Scope — v2 Draft→Complete fix]** Out of Scope at `docs/plans/2026-08-29-gap-analysis.md:129` says "Archiving any `docs/plans/*` file beyond the optional single-line `v2 Draft→Complete` fix." while Phase 3 at `:110` permits the same edit. Wording "Beyond" is correct but easy to misread as forbidding the fix. Suggest "No `docs/plans/*` edits except the single-line `v2 Draft→Complete` at `docs/plans/2026-08-26-v2-hf-search-and-download.md:6`".

- **[Plan Mechanics][Phase 1 — Automated Verification: ruff on markdown]** Criterion `uv run ruff check docs/plans/2026-08-29-gap-analysis.md` at `docs/plans/2026-08-29-gap-analysis.md:80` notes "no Python, but ruff on repo still `uv run ruff check .`". `ruff check` on a `.md` is a no-op (exits 0, checks nothing). Keep second form `uv run ruff check .` as the real gate; drop the first or merge to `uv run ruff check .` only.

- **[Correctness][Appendix — Plan-status lifecycle drift note]** `2026-08-26-v3-history-sparkline.md` row at `docs/plans/2026-08-29-gap-analysis.md:152` says "Complete but superseded by Metrics tab relocation" — correct per `src/mlx_tui/metrics_pane.py:42` / `src/mlx_tui/history/sparkline.py:21` and `docs/plans/2026-08-27-metrics-markdown-params-presets.md:20`, but audit's "Built-Despite-Scope — memory sparkline added" at `:221` references `ARCHITECTURE.md:17` which was not checked. No `ARCHITECTURE.md` exists at repo root (only `docs/idea.md`, `docs/plans/*`, `README.md`); if `ARCHITECTURE.md` is intended, cite is broken.

### Suggestions

- **[Architecture & Patterns]** Appendix is ~150 lines, ~83 `idea.md:` hits and ~95 `src:line` refs — plan's own Risk at `docs/plans/2026-08-29-gap-analysis.md:137` flags "Markdown table wrapping breaks readability" and mitigation "keep `idea.md:line` column narrow, use `file:line` not full URLs" is applied. Consider splitting Gap Matrix into a standalone `docs/GAP-MATRIX.md` or `docs/reviews/` companion if Phase 2 author finds inline editing painful; not blocking — single-file audit is a virtue for 1-user tool.

- **[Test Coverage]** For a doc-only plan, "whole suite still green (`uv run pytest -q` 231 passed, `uv run ruff check .`, `uv run pyrefly check` 0 errors)" is sufficient. Lean into it: Phase 2/3 already gate on all three (verified `default.bash` green). Consider adding one deterministic doc check: `python3 -c "import pathlib,re; refs=re.findall(r'src/mlx_tui/[^:]+:[0-9]+', pathlib.Path('docs/plans/2026-08-29-gap-analysis.md').read_text()); assert all(pathlib.Path(r.split(':')[0]).exists() for r in refs)"` — but the existing shell loop is clearer; keep as is.

- **[Plan Mechanics]** Phase 3 at `docs/plans/2026-08-29-gap-analysis.md:110` suggests "Optionally run `review-plan` skill for independent quality pass before marking Approved (skill suggestion, not required)." Self-referential (this review is that pass). Keep, but rephrase to "this review satisfies that suggestion" after merge.

## Strengths

- **Single source of truth done right.** Full matrix (Implemented / Partial / Not Implemented / Key Drift / Built-Despite-Scope) vs minimal missing-only list is explicitly chosen at `docs/plans/2026-08-29-gap-analysis.md:61` for good reason — hides nothing, surfaces intentional expansions that will be re-asked, and avoids over-planning tickets per `idea.md:223` gate. Disposition column staying descriptive (Implement / WontFix / Needs Decision) at `:64` is the correct call.

- **Evidence density is audit-grade.** Every row carries `Spec (idea.md:line)` + `Evidence (src:line)` + `Authorizing Plan` + `Disposition`; file-existence loop at `:98` and manual spot-checks at `:102` make claims falsifiable. Verified file-existence loop passes (95 refs, 0 missing) and baseline gates are honest (`uv run ruff check .` clean, `uv run pyrefly check` 0 errors, `uv run pytest -q` 231 passed — reproduced).

- **Out of scope and risks are tight.** Explicitly "this document audits, it does not implement" at `docs/plans/2026-08-29-gap-analysis.md:125`, no `src/` edits, no new widget/key rebinding/Metrics-tab removal, and Risks at `:133-137` anticipate stale refs, drift, and over-prescribing with concrete mitigations. Design Decisions table at `:59` is concise and decision-led.

- **Plan-status lifecycle honesty.** Calling out `2026-08-26-v2-hf-search-and-download.md:6` **Draft** vs Complete at `:42` and `:151`, reproducing its entire header in verification (`baseline verified 2026-08-26: 183 passed` → now 231), and linking to the stale-✓ warning at `idea.md:298` shows the audit eating its own dog food.

## Recommended Changes

1. **Fix Phase 2 row-count gate** (`docs/plans/2026-08-29-gap-analysis.md:97`): replace `grep -c "| idea.md:"` with `grep -c "idea.md:"` (or `grep -c "|.*idea\.md:"`) and re-run to confirm ≥15 (actual 83).
2. **Re-pin `src:line` refs or declare tolerance** — regenerate all `src/mlx_tui/*:line` at HEAD (e.g. `rg -n "class|def " src/mlx_tui` pass) and update at least the 8 drifted cites listed in Major #2, or add footnote "lines pinned to 2026-08-29 baseline ±10, file existence is the hard gate".
3. **Pin the `v2 Draft→Complete` home** — move single-line edit to exactly one phase (recommend Phase 3) and make Phase 3 criterion unconditional on that phase: "If this phase performs the edit, verify `grep -q '^\*\*Status:\*\* Complete' docs/plans/2026-08-26-v2-hf-search-and-download.md`".
4. **Clarify Unimplemented Summary count** (`docs/plans/2026-08-29-gap-analysis.md:264-276`): change header to "7 items (4 code gaps + 3 process/doc)" and align Phase 2 manual check "5–7 items" to "7 items".
5. **Drop or merge the `ruff check` on `.md`** at `docs/plans/2026-08-29-gap-analysis.md:80` to just `uv run ruff check .`; similarly keep only `uv run ruff check . && uv run pyrefly check && uv run pytest -q` in Phase 3.
6. **Fix `ARCHITECTURE.md:17` cite** at `docs/plans/2026-08-29-gap-analysis.md:221` or remove — no such file at repo root; if it lives elsewhere, give correct path, else drop.
7. **(Optional) Add line-range tolerance note** in Risks (`:133`) — "line numbers may drift ±10 after splits; file existence is hard gate, line is best-effort".

## Re-Review (Pass 2)
**Date:** 2026-08-29
**Verdict:** APPROVE
**Scope:** Re-ran Correctness, Plan Mechanics, Architecture & Patterns, Test Coverage lenses only on prior findings.

### Previous Findings
- [Correctness] Phase 2 row-count gate `grep -c "| idea.md:"` — **Resolved** → `grep -c "idea.md:"` at `docs/plans/2026-08-29-gap-analysis.md:97` now returns 83; added tolerance note "lines best-effort ±10, file existence is hard gate" at `:97-98`.
- [Plan Mechanics] Stale `src:line` refs (≈35% drift) — **Resolved** → re-pinned 8 cites: `status.py:8→16`, `process.py:12→35`, `process.py:48→54`, `serverctl.py:37→41/54→48/66→57`, `models.py:28→39` (twice), `history/store.py:58→37/49`, `search_screen/__init__.py:20→36`, `search_screen/query.py:24` split + `download.py:22→55`; appendix row `models.py:28→39` at `:184` fixed; file-existence loop still 95 refs 0 missing.
- [Correctness] `idea.md:line` tolerance — **Partially resolved** → phase header at `:88` now declares lines pinned to 2026-08-29 baseline ±10 (Risks at `:134` updated); `history/tokens.py:24`/`chat_pane/turn.py:22` example left as best-effort — acceptable for audit.
- [Plan Mechanics] `grep -o ... | while read` line-existence — **Resolved** → added note at `:97-98` "lines best-effort ±10, file existence is hard gate".
- [Architecture & Patterns] Gap doc location `docs/plans/` vs `docs/reviews/` — **Acknowledged, kept** → design decision at `:62` stands; no edit needed (trade-off documented).
- [Correctness] Baseline `pyproject.toml:14` cite drift — **Resolved** → `docs/plans/2026-08-29-gap-analysis.md:13` now `pyproject.toml:9-14` (`httpx` at :10, `textual` at :14); entry `pyproject.toml:24` unchanged correct.
- [Test Coverage] Unimplemented Summary count 5 vs 7 — **Resolved** → header `docs/plans/2026-08-29-gap-analysis.md:264` now "7 items (4 code gaps + 3 process/doc)", manual check at `:103` aligned to "exactly 7", appendix bullet at `:92` updated, footnote at `:276` clarified to "5–7 → 5–7 are process/doc".
- [Plan Mechanics] Out of Scope `v2 Draft→Complete` wording — **Resolved** → `:130` now "No `docs/plans/*` edits except the single-line `v2 Draft→Complete` at `...:6` (Phase 3)".
- [Plan Mechanics] `ruff check` on `.md` no-op at `:80` — **Resolved** → `:80` now `uv run ruff check .` (doc-only; ruff on `.md` is no-op).
- [Correctness] `ARCHITECTURE.md:17` cite — **Resolved** → `:222` now `./ARCHITECTURE.md:17` (root file, line 17 valid MemoryStore ring 256); Risks at `:135` updated to `./ARCHITECTURE.md:17`; Phase 3 manual check at `:122` now `./ARCHITECTURE.md:108` Project structure.
- [Plan Mechanics] `v2` fix home ambiguity Phase 2 vs 3 — **Resolved** → Phase 2 header at `:88` now "deferred to Phase 3 (single home; do not edit `v2` in this phase)", Change bullet at `:92` notes fix in Phase 3, Phase 3 Changes at `:110` now two explicit bullets (review satisfies pass + edit `...:6` Draft→Complete), Success at `:115` now unconditional "`v2` doc-lifecycle fix applied".

### New Issues Introduced
- None. Gates verified: `uv run ruff check .` clean, `uv run pyrefly check` 0 errors, `uv run pytest -q` 231 passed, `grep -c "idea.md:"` 83, `grep -o "src/mlx_tui/[^ ]*:[0-9]*" | while read` 0 missing, `grep -E "^\*\*Status:\*\* (Draft|Approved)"` matches Draft.

### Verdict Rationale
All Major findings resolved; Minors addressed; no new Critical/Major introduced. Plan now passes its own automated criteria as written. Doc-lifecycle `v2` fix correctly single-homed to Phase 3; line refs pinned or tolerance-noted.
