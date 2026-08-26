# Plan Review: MLX TUI v1 — Models tab, hybrid swap, config file

**Date:** 2026-08-26
**Target:** docs/plans/2026-08-26-v1-models-tab-swap-and-config.md
**Review:** 1
**Verdict:** APPROVE (initial pass: REVISE — see Re-Review at bottom)

## Assessment

Exceptionally well-grounded plan: nearly every file:line citation, config claim, and third-party API assertion I checked was accurate (baseline 78-pass reproduced; hub 1.28.0 dataclass fields, `DeleteCacheStrategy.execute`, `CacheNotFound`, `py.typed`, PLR0913-at-6, `App.suspend`, DataTable methods all verified). But empirical probing of the four load-bearing runtime claims found real breakage: the `TabbedContent` ids crash at mount on the pinned textual 8.2.8, `httpx.get` ignores the plan's transport-patching seam, and two test specs contradict their own production specs. All fixable with targeted edits; none undermine the architecture.

## Cross-Cutting Themes

1. **Verified-vs-assumed third-party surface.** The plan's hub facts are solid, but its textual 8.x and httpx assumptions were asserted, not probed — and those are exactly where the Critical findings live (tab ids, `event.tabbed_content`, `httpx.get` patching). Anything the suite depends on should carry the same "verified empirically" badge the hub section has.
2. **Tests contradicting production specs.** Four places specify a behavior in one paragraph and assert its opposite in the test bullets of the same phase (config type-check, quant case folding, `IDLE→STARTING` legality, restart-path precondition). Each will surface as an immediately failing or wrong-branching test.
3. **Thread/UI boundary is mostly disciplined, with three soft spots**: message ownership in `_abort_chat`, the never-created swap-progress widget, and a mid-plan self-correction left in prose.

## Findings

### Critical

- **[Correctness]** Phases 2, 4, 7 — **Tab ids crash at mount.** `TabbedContent(initial="models")` with `TabPane(id="models-pane")` raises `ValueError: No Tab with id '--content-tab-models'` on textual 8.2.8 (reproduced; the reactive validator rejects unknown ids at mount). Same error for `.active = "models"` / `.active = "chat"` in the Phase 2 harness fallback and the Phase 7 tests. Fix: pass `initial="models-pane"` and set `.active = "models-pane"` / `"chat-pane"` (verified working; `active` then returns the plain pane id) — or simpler, name the panes `id="models"` / `id="chat"` and keep the plan's strings.
- **[Correctness]** Phase 1 — **Config type-check makes optional keys unloadable.** "a key whose value fails `isinstance(value, type(default))` is skipped" rejects every valid string for `model`/`start_cmd`/`stop_cmd`/`pidfile`, whose defaults are `None` (`isinstance("x", NoneType)` is False). The phase's own round-trip tests can never pass as specified. Fix: explicit per-key schema (e.g. `_KEY_TYPES: dict[str, type]` with `str` for the optionals, `int` for port; optionally reject `bool` for port since `isinstance(True, int)` is True).
- **[Correctness / Test Coverage]** Phase 6 — **`wait_healthy` tests can't intercept `httpx.get`.** The plan patches `httpx.Client` (the `install_transport` pattern), but `serverctl.wait_healthy` calls top-level `httpx.get`, which resolves `Client` from `httpx._main`'s own globals — the patch does not apply (verified: real DNS lookup, `ConnectError`). Tests would hammer the network and fail. Fix: implement `wait_healthy` with an explicit `with httpx.Client() as client: client.get(...)` (module-attribute lookup, patchable — same as `warm_load`), or inject a client/transport factory parameter.
- **[Correctness]** Phase 7 — **Restart-swap integration test exercises the wrong branch.** Dispatch is `if green → warm; elif cmds configured → restart`, and the test runs stub mode `ok` (green) with cmds configured — so it takes the warm path against an SSE stub, `resp.json()` throws, and the `[swap] bye`/`[swap] booting` assertions never appear. Fix: use mode `"html"` (persistent non-green) for this test; the warm test already covers the green branch.

### Major

- **[Correctness]** Phase 3 — `quant_label` test expects `foo/model-8BIT → "8bit"` but `_QUANT_RE` has no `re.IGNORECASE`, so nothing uppercase matches and the label degrades to `"—"`. Compile with `re.IGNORECASE`.
- **[Plan Mechanics]** Phase 6 — `IDLE→STARTING` appears in **both** the legal-edge list (cold-start edge, required by Phase 8's `action_cold_start` and allowed by `_ALLOWED`) and the illegal-edge list. Delete it from the illegal list.
- **[Correctness]** Phase 4 — the `@on(TabbedContent.TabActivated)` handler reads `event.tabbed.active`; the event's attributes are `tabbed_content` and `tab` (verified constructor signature). As written, every tab switch raises `AttributeError`.
- **[Plan Mechanics]** Phase 7 — `_progress_line` updates `Static(id="swap-progress")`, but no change item ever adds that widget to `compose()`; first progress tick dies on `query_one`. Add an explicit compose line (and its emptying on completion) to the phase.
- **[Correctness]** Phase 7 — `_abort_chat(reason)` spec is self-contradictory: "extract it verbatim" yet the method now "writes the reason line" — but today the cancelled line is written by `_run_turn`'s handlers, not by `action_cancel_chat`. As described, a real in-flight turn produces both `cancelled — model swapping` *and* `cancelled — request aborted`, violating the "exactly once" manual criterion. Decide the message owner (recommendation: keep `_abort_chat` verbatim/parameterless; have the swap caller log its own line; adjust the criterion wording, or suppress `_run_turn`'s duplicate when a swap initiated the abort).
- **[Test Coverage]** Phase 8 — `test_cold_start_boots_server` forces `status_state = "red"` but the 2s `_poll` keeps recomputing green against the `ok`-mode stub; if a tick lands before the keypress, the guard dims a no-op line and the test flakes. Pin liveness deterministically (e.g. monkeypatch `MlxTuiApp._classify_liveness` to return `"red"`).

### Minor

- **[Correctness]** Phase 5 — with `ConfirmScreen.BINDINGS` lacking `escape`, the key bubbles focused-widget → screen → app and the app-level `cancel_chat` fires while the modal is open. The plan hedges ("if it leaks"); the leak is near-certain, so just add `("escape", "dismiss_no", "no")` to `ConfirmScreen.BINDINGS` outright.
- **[Correctness]** Phase 7 — the restart path checks `stop_rc != 0` but ignores a nonzero `start_cmd` exit: an instantly-crashing start command still burns the full health-timeout deadline. Mirror the stop-path fast-fail for the start return code.
- **[Architecture]** Phase 3 — import `CacheNotFound` from the public `huggingface_hub.errors` rather than the private `utils._cache_manager` (same class, verified; the public path is the stability contract you're pinning `>=1.28` for).
- **[Plan Mechanics]** Phase 7 — the literal mid-plan deliberation ("— wait: worker thread must not touch widgets; define … inline instead") should be rewritten as a single instruction stating the final design.
- **[Plan Mechanics]** Phase 7 — `test_cannot_swap_without_commands_when_red` runs stub mode `html`, i.e. amber, and the neither-branch doesn't inspect color at all; rename to match reality (e.g. `…_when_unreachable_and_unconfigured`).

### Suggestions

- Add direct unit tests for `_build_start_command` (pure, three branches plus the `{model}` placeholder) instead of relying on two integration tests to cover it.
- Phase 5's delete flow is manual-verification only; a small integration test (monkeypatch `mlx_tui.app.delete_repos`, drive `d` → `y` through the pilot) would protect the modal wiring.
- Phase 3 test comment "(≈5.99GiB needed)" is arithmetically off (5 GB × 1.2 ≈ 5.59 GiB); assertions are correct regardless.
- Consider triggering one `_poll()`/rescan at mount so the fits column isn't `—` for the first 2 seconds after launch.

## Strengths

- Factual density is outstanding: every file:line citation I checked (~30) matched the code exactly; baseline, CI steps, ruff/pyrefly configuration, and the hub introspection block all verified against reality.
- Architecture is continuous with the v0 refactor: pure modules (`config.py`, `models.py`, `swap.py`, `serverctl.py`) with callable seams, thin UI layer, thread workers crossing only via `call_from_thread` — and the plan knows where its thread boundaries are (including catching and correcting its own `on_line` slip).
- Product fidelity: marker hygiene at `stop_cmd` time, hint-not-gate headroom margin (algebraically equivalent to the idea doc's ⚠ rule), hybrid swap honoring both locked decisions, and an honest out-of-scope list including the known external-warm-swap blind spot.
- Every phase ends green with Automated/Manual criteria split, and Phase 3 correctly ties the `uv.lock` commit to CI's `--locked` install.

## Recommended Changes

1. Fix the four Critical items: tab ids (`models-pane`/`chat-pane` or rename panes), explicit per-key config type map, `wait_healthy` via explicit `httpx.Client` instance, restart test on non-green stub mode. (→ Critical 1–4)
2. Reconcile the four spec/test contradictions: IGNORECASE in `_QUANT_RE`, drop `IDLE→STARTING` from illegal edges, `event.tabbed_content`, `_abort_chat` message ownership + "exactly once" wording. (→ Major)
3. Add the missing `Static(id="swap-progress")` compose item and make the cold-start test deterministic. (→ Major)
4. Sweep the Minors: proactive escape binding on `ConfirmScreen`, start-rc fast-fail, public `errors` import, prose cleanup, test rename. (→ Minor)

## Re-Review (Pass 2)
**Verdict:** APPROVE

All 15 findings addressed by targeted plan edits (no restructuring). Verified by grep sweep: no stale `models-pane`/`chat-pane`, `event.tabbed.`, `_swap_line`, `httpx.get(`, `isinstance(value`, or `when_red` references remain.

### Previous Findings
- [Correctness] Tab ids crash at mount — **Resolved** (panes renamed `id="models"`/`id="chat"` so every existing string stays valid; parenthetical documents the verified 8.2.8 behavior).
- [Correctness] Config type-check rejects Optional keys — **Resolved** (`_KEY_TYPES` exact-type map; bool-into-port edge documented).
- [Correctness/Test] `wait_healthy` can't intercept top-level `httpx.get` — **Resolved** (explicit `with httpx.Client(...)` via module-attribute lookup, rationale recorded in the sketch).
- [Correctness] Restart-swap test exercised the warm branch — **Resolved** (`server.mode = "html"` + explicit `_poll()`; comment states why green must be excluded).
- [Correctness] quant regex missing IGNORECASE vs its own test — **Resolved**.
- [Plan Mechanics] `IDLE→STARTING` listed legal and illegal — **Resolved** (replaced with genuinely illegal `STOPPING→IDLE`; clarifying note added).
- [Correctness] `event.tabbed` AttributeError — **Resolved** (`event.tabbed_content.active`).
- [Plan Mechanics] swap-progress Static never composed — **Resolved** (compose bullet added; clearing folded into `_set_swap_ui(False)` so no exit path leaves stale progress).
- [Correctness] `_abort_chat` message ownership ambiguity — **Resolved** (parameterless verbatim extraction; caller logs its own yellow line; manual criterion reworded to "alongside `_run_turn`'s own dim cancelled line").
- [Test Coverage] Cold-start test races the 2s poll — **Resolved** (monkeypatched `always_red` `_classify_liveness` + one applied `_poll`).
- [Minor] escape-while-modal leak hedge — **Resolved** (bound proactively on `ConfirmScreen`).
- [Minor] nonzero start_cmd burns health deadline — **Resolved** (start_rc fast-fail mirrors stop path).
- [Minor] private `CacheNotFound` import — **Resolved** (`huggingface_hub.errors`; Current State bullet updated too).
- [Minor] mid-plan deliberation prose — **Resolved** (single instruction; lambda reused for both streams).
- [Minor] `…_when_red` misnomer — **Resolved** (renamed `test_cannot_swap_when_unreachable_and_unconfigured`).

### New Issues Introduced
- None. The wait_healthy pseudocode keeps per-request 0.5s semantics with one client; the restart worker streams both commands through the same `call_from_thread` lambda; all `.active = "models"/"chat"` strings now match the renamed pane ids.
