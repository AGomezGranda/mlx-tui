# Plan Review: Part 3 Milestone A — Trust the facts

**Date:** 2026-09-07
**Target:** docs/plans/2026-09-07-part3-milestone-a-trust-the-facts.md
**Review:** 1
**Verdict:** REVISE

## Assessment
The plan correctly diagnoses residency, stream-termination, and metric dishonesty and scopes Milestone A to evidence plus honest labels. Verification confirms baselines hold (450 passed, ruff/pyrefly clean, 153 focused passed) and most file/symbol claims check out. It is not yet implementable as written: Phase 1 demands unavailable hardware, several core contracts (SSE termination, no-selection chat, rate boundary, commit policy) are ambiguous, and Phase 2/3 steps are too coarse for edit-and-check execution.

## Cross-Cutting Themes
- **Honest unknowns vs. executable steps.** The plan is admirably explicit about what is unverified ("verify during implementation" ×7, single-tier hardware), but that honesty leaves Phase 1/2 without zamknięte definitions the implementer needs (SSE terminal API, rate estimated-vs-unknown, commit matrix).
- **State ownership is split across phases.** `selected_model` / `generation_state` are introduced in Phase 2, but their writers (chat turns vs. load probes vs. polls) and invalidation rules are described in different bullets, risking stale evidence.
- **Line-number drift.** The staged tree has already moved several refs (e.g. `effective_model`, `ColdTracker`); one ref points past end-of-file. Prefer symbol names over volatile lines.

## Findings

### Critical
_None._

### Major
- **[Plan Mechanics] Phase 1: second memory tier cannot be named from available hardware.** Phase 1 Changes: "Name two memory tiers from actual available hardware. Only M4 16 GiB is established now." Only `local-m4-16gib` exists; no second tier is available during planning. As written Phase 1 cannot complete. Fix: allow a single-tier report with an explicit B-entry blocker, and define the second tier as "to be resolved before B" rather than a Phase 1 deliverable.
- **[Plan Mechanics] Phase 1: install/resolver commands are proposals, not steps.** Phase 1 Changes marks `docs/compatibility/milestone-a-runtime.txt`, `tests/runtime/*` paths, and `pytest -q tests/runtime` outcomes as "**verify during implementation**" with "no inference installation or model download was performed." A zero-context implementer cannot execute Phase 1 without discovering the resolver (`uv pip` vs `pip`), freeze format, launch argv, and skip-guard shape. Fix: scope Phase 1 step 1 as explicit discovery (record actual commands in the report) and specify the freeze artifact format (e.g. `uv pip freeze`) and conftest skip contract (missing env → `pytest.skip`, non-loopback URL → hard fail).
- **[Correctness] Phase 3: SSE termination evidence has no API.** Phase 3 Changes (`src/mlx_tui/sse.py`): "retain framing helpers but expose termination evidence to the transport." `src/mlx_tui/sse.py:20-45` currently swallows `[DONE]` (`break` with no signal), so `stream_complete` in `chat.py` cannot distinguish clean DONE vs. graceful premature EOF vs. truncation. Fix: define the terminal contract now (e.g. decoder returns `DONE_SEEN` / `EOF` / `TRUNCATED`, or `iter_sse_data` returns `(payloads, terminal)`), then derive `stream_complete` from it.
- **[Correctness] Phase 2: chat with no selection is undefined.** Phase 2 Changes (`src/mlx_tui/app/__init__.py`): "`effective_model` becomes selected request target." On fresh attach with a multi-model catalogue and no explicit selection, selected is `None`; `src/mlx_tui/chat_pane.py:125` currently omits `model` when `"—"`. The plan never states whether chat sends no `model` field, refuses, or forces selection, nor what the pinned runtime does with a missing field. Fix: state the rule (e.g. "no selection → chat refused with explicit-selection hint" or "send without model only if Phase 1 proves server default behavior").
- **[Correctness] Phase 3: estimated-vs-unknown rate boundary is contradictory.** Phase 3 Changes (`src/mlx_tui/sse.py`, `history/store.py`, `chat_pane.py`): "Without usage, estimate all observed output and mark the rate estimated; incomplete output accounting means unknown." A complete stream with no usage and an incomplete stream with no usage both lack usage — one would be estimated, the other unknown. Fix: rule such as "complete + missing usage → estimated; incomplete → unknown regardless of usage."
- **[Plan Mechanics] Phase 2/3 bullets are not 2–15 minute actions.** Phase 2 `app/__init__.py` bullet bundles probe split, selection derivation, retention/invalidation, stale-poll guard, and every-caller migration; Phase 3 `chat.py`/`chat_pane.py` bullets bundle parsing, result extension, UI, history, and lease handling. These will not survive contact with the staged tree as single steps. Fix: split Phase 2 into ordered edit-and-check steps (state records → probe/identity → callers → panes → tests) and Phase 3 into parse → transport → UI/history → metrics order with per-step checks.
- **[Test Coverage] Phase 4: runtime acceptance gate has no owner/runner.** Phase 4 Automated Verification: "`pytest -q tests/runtime` passes for all claimed-supported surfaces with the report's exported configuration … Skipped inference does not satisfy this acceptance gate," while "ordinary CI remains model-free." No runner, machine, or trigger is named, so the gate is unexecutable in CI and unowned locally. Fix: name the gate owner (maintainer on `local-m4-16gib` with frozen env) and state CI stays skip-only.
- **[Data/Migrations] Phase 3: commit policy for non-ordinary outcomes is underspecified.** Phase 3 Changes (`src/mlx_tui/chat_pane.py`): "Commit complete ordinary answers to future request history; display length-capped, damaged and tool-only outcomes without inserting invalid/incomplete conversation pairs." Unclear whether `pending_user` is also withheld, what retry sends, and where tool-only output goes (transcript only vs. history). `src/mlx_tui/chat_pane.py:403-415` currently commits user+assistant together. Fix: explicit commit matrix (ordinary / length-capped / damaged / tool-only / empty × user retained? assistant retained? history inserted?).
- **[Correctness] Phase 2: Current State line ref is invalid.** Plan Current State: "`history/tokens.py:157` displays context without qualifying the character estimate." `src/mlx_tui/history/tokens.py` is 137 lines; the unqualified label is `ctx_bar_text` at `src/mlx_tui/history/tokens.py:125` (`f"ctx {_format_k(ctx_len)}/{_format_k(max_ctx)}"`). Fix the ref to `:125` or drop the line number.

### Minor
- **[Plan Mechanics]** Volatile line refs have drifted: `effective_model` is at `src/mlx_tui/app/__init__.py:237` not `:240`; `ColdTracker` at `src/mlx_tui/status.py:57` not `:58`; `src/mlx_tui/history/sparkline.py:83` is a docstring (render at `:73`); `src/mlx_tui/models_pane.py:183` is the `run_warm_swap` def line, fallback logic is at `:200-208` with the "loaded" log at `:222`. Refresh or drop `:line` suffixes.
- **[Plan Mechanics]** `tests/runtime` and `docs/compatibility/` do not exist yet (verified). `pytest -q tests/runtime` on a missing path exits 5 (no tests collected), not "clearly skip." Ensure Phase 1 creates `tests/runtime/conftest.py` with the skip guard before the success criterion applies.
- **[Correctness]** Tool-call shape is ad-hoc: `tool_calls: tuple[dict[str, object], ...]` with "merge tool fragments by index" but no minimal schema. Define `{index, id, type, name, arguments}` (or the captured OpenAI shape) now so parsing, display, and tests agree.
- **[Correctness]** Stale-poll guard mechanism unspecified ("Guard older in-flight polls from overwriting newer request results"). `src/mlx_tui/app/__init__.py:157-208` uses a boolean `_poll_in_flight`, not a sequence. Specify a monotonic poll sequence/generation counter.
- **[Correctness]** "Permit explicit model paths omitted by the catalogue" (`serverctl.py`, `boot.py` bullet) is vague. Define the selection source of truth (config/explicit load) and how catalogue disagreement is displayed when the path never appears in `/v1/models`.
- **[Correctness]** "Revalidate generation when sampling RSS in `app/__init__.py`" (`process.py` bullet) reads as if RSS sampling sets generation evidence. Clarify RSS stays observational and never writes `last_success_at` / `generation_state`.
- **[Test Coverage]** Concurrent-contracts recording is thin: "two simultaneous requests" with no stated observables. Specify per-request latency + aggregate behavior to record (per Part 3 contention guidance).
- **[Correctness]** `wait_healthy` strictness needs a documented edge: "Missing/mismatched identity is unverified" means servers omitting `model` fail boot even if loaded. Acceptable for the pinned runtime (Phase 1 must prove it echoes `requested_model`), but state the fallback (reject pin vs. operator override) explicitly.

### Suggestions
- Drop `:line` suffixes in Current State where the staged tree is moving; file + symbol is enough.
- Split Phase 4 docs edits (`README.md`, `ARCHITECTURE.md`, `docs/part3.md`) into separate checkable items with exact claims to correct (catalogue/health, GiB, estimate labels).
- Record evidence checksums with `sha256sum` in `docs/compatibility/milestone-a.md` alongside locations.

## Strengths
- Residency-unknown stance is correct and consistently applied: catalogue ≠ residency, response `model` is request attribution, prior success is dated history only.
- Isolation and safety are well-scoped: separate frozen env, loopback-only fixtures, dedicated operator-launched server, finite deadlines, two-request bound, never start/kill/reconfigure an attached server.
- Correctly identifies real defects verified in code: `bool`-is-`int` in `usage_from_chunk` (`src/mlx_tui/sse.py:90-100`), swallowed `[DONE]` (`src/mlx_tui/sse.py:20-45`), `prompt_tokens / TTFT` mislabeled prefill (`src/mlx_tui/chat_pane.py:304-306`), GiB-labeled-GB (`src/mlx_tui/status_bar.py:78-80`), length-cap still committed (`src/mlx_tui/chat_pane.py:346-352`).
- Out-of-scope and B-blocker handling are crisp: no Compare/managed install/tokenizer/tool execution in A; second tier stays a visible blocker rather than simulated evidence.
- Test inventory is complete and paths check out: all listed unit/integration files exist; planning baselines reproduce (450 passed in ~96s; focused 153 passed; ruff/format/pyrefly clean).

## Recommended Changes
1. Fix the invalid `history/tokens.py:157` ref → `:125` (or remove line numbers) and refresh drifted refs (Major 9, Minor 1).
2. Reword Phase 1 to allow a single-tier report on `local-m4-16gib` with an explicit B-entry blocker, and turn install/freeze/launch into a discovery step with specified artifact format and conftest skip/fail contract (Major 1–2).
3. Define the SSE terminal API before transport work (DONE vs EOF vs truncated) and derive `stream_complete` from it (Major 3).
4. State the no-selection chat rule and verify missing-`model` server behavior in Phase 1 contracts (Major 4).
5. Replace the rate sentence with "complete + missing usage → estimated; incomplete → unknown" and apply the same filter in sparkline/metrics (Major 5).
6. Split Phase 2 and Phase 3 bullets into ordered 2–15 minute edit-and-check steps with per-step checks (Major 6).
7. Name the Phase 4 runtime-gate owner/runner (maintainer + frozen env on named Mac; CI stays skip-only) (Major 7).
8. Add the history commit matrix for ordinary / length-capped / damaged / tool-only / empty outcomes (Major 8).
