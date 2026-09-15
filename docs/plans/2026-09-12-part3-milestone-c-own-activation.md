# Part 3 Milestone C — Own activation

**Date:** 2026-09-12
**Work Item:** n/a
**Status:** In Progress (implementation authorized; Milestone B remains blocked)

Implementation is proceeding at the maintainer's request. The B qualification
and product gate remain unchanged and no C recommendation or user evidence is
being inferred from these code checkpoints.

## Overview

Deliver a versioned wheel installed with uv, an isolated pinned MLX-LM runtime owned by the TUI, and a keyboard-driven setup flow that brings fresh-install users to the existing coding comparison. Implementation and recruitment remain gated on Milestone B's qualification and product evidence; this document plans C without declaring B achieved.

## Current State

- `docs/part3.md:454` defines C: minimum install artifact, pinned runtime, version detection, coding shortlist, recovery, and five fresh-install users across two tiers, with four reaching comparison unassisted. `docs/part3.md:468` requires an exact artifact/resolver choice and TUI-bound subprocess lifetime.
- `docs/compatibility/milestone-b.md:1` records no live B qualification, no second physical tier, and no completed five-operator/14-day gate. The proposed recommendation owner remains unconfirmed. Do not invent a second tier or promote the current profiles to C recommendations.
- `pyproject.toml:1` packages version 0.1.0 with Hatchling and exposes `mlx-tui = mlx_tui.app:main`; `.python-version:1` selects Python 3.13. MLX is not imported into or installed as an inference dependency of the TUI.
- `tests/artifact_smoke.py:132` builds the wheel/sdist, rebuilds the sdist, and installs both outside the checkout. It verifies the CLI and packaged `coding_profiles.toml`; it does not exercise managed runtime installation.
- `src/mlx_tui/config.py:16` contains optional operator start/stop commands and a swap policy, but no attach/managed mode. `src/mlx_tui/app/__init__.py:881` contains the CLI entry point.
- `src/mlx_tui/boot.py:78` runs configured commands and returns only `ServerProbe`. Its local `Popen` is cleaned on failed boot, but no successful child handle is retained by the app. `src/mlx_tui/models_pane.py:236` is its production caller; the three app start/restart paths feed this worker.
- `src/mlx_tui/serverctl.py:157` already launches argv with a new process session and merged log output. `spawn_command`, `spawn_with_grace`, `warm_load`, and `wait_healthy` are reusable. `terminate_failed_process` is deliberately a failed-command cleanup helper, not yet a verified managed ownership API.
- `src/mlx_tui/process.py:149` discovers an endpoint-associated PID/create-time pair; discovery is evidence, never ownership. `src/mlx_tui/app/__init__.py:313` closes comparison work and HTTP resources on unmount but has no managed child shutdown.
- `src/mlx_tui/models.py:87` resolves an exact cached revision offline. `src/mlx_tui/search.py:75` fetches immutable revision metadata, and `download_snapshot` at line 171 supports revision, progress, and cooperative cancellation. `SearchScreen` already handles retries, pinned candidate downloads, and rescan. Cache-table inclusion is a heuristic, not proof that every weight shard/tokenizer asset exists.
- `src/mlx_tui/profiles.py:72` defines pinned profiles; evidence is tier-specific, fingerprint-bound, expiring, and revocable. `src/mlx_tui/coding_profiles.toml:1` has exactly two candidates: `qwen3-1.7b-baseline` and `qwen3.5-4b-baseline`. Both pin runtime commit `74e7cf931e84ef7c2f63e875adf414e20decc1c5`, MLX 0.32.2, and launch settings `127.0.0.1:18080`, INFO logging, pinned snapshot paths.
- `src/mlx_tui/compare_pane.py:586` constructs comparison inputs from verified snapshots, discovered process identity, and operator-entered runtime/install/launch evidence. Its Run path rebuilds readiness; Keep and saved-profile reuse call `apply_coding_profile` (`src/mlx_tui/app/__init__.py:215`), which currently applies request settings and says restart remains operator-managed.
- `src/mlx_tui/comparison_runner.py:1` already performs twelve sequential request slots without owning inference. `src/mlx_tui/comparison_summary.py:21` keeps first-request state conservative and memory inconclusive. C must preserve this experiment rather than insert unmeasured load requests or change its scoring.
- `tests/runtime/test_milestone_b.py:107` verifies an installed environment's MLX-LM direct-URL commit and MLX distribution version. Its launch check compares actual argv, PID/create-time, freeze, and profile settings. Reuse this evidence shape in production; do not import pytest helpers into application code.
- The locally installed pinned server was read during planning: `ModelProvider._load` releases the prior target, and the CLI supports `--model`, `--host`, `--port`, `--log-level`, and opt-in `--trust-remote-code`. This is source evidence, not a fresh inference test. A's retained evidence identifies Python 3.13.1, uv 0.12.7, and macOS 26.6.2 on `local-m4-16gib` (`docs/compatibility/milestone-a.md:9`).

Planning used the staged, unstaged, and untracked working tree as supplied; do not reset it. All new files, symbols, flags, and future commands explicitly identified below are proposed implementation outputs, **verify during implementation**.

Baseline commands executed during planning:

| Command | Observed result |
|---------|-----------------|
| `rtk proxy uv run ruff check .` | Passed |
| `rtk proxy uv run ruff format --check src tests` | Passed; 76 files formatted |
| `rtk proxy uv run pyrefly check --min-severity warn` | Exit 1: seven existing unnecessary string-conversion warnings in `compare_pane.py`, `comparison_persistence.py`, and `profiles.py` |
| `rtk proxy uv run python tests/artifact_smoke.py` | Passed |
| `rtk proxy uv run pytest -q` | Passed: 548 passed, 8 skipped in 172.93 seconds; opt-in live contracts were skipped |

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Versioned wheel installed with uv | Wheel + uv; native Mac installer | User selected wheel + uv. Build proposed `mlx_tui-0.2.0-py3-none-any.whl`; retain its SHA-256 and deliver those exact bytes to every C tester. No source checkout is required. Do not publish automatically as part of implementation. |
| uv 0.12.7, CPython 3.13.1 | Reuse A's resolver/interpreter; independently choose newer versions | These are A's recorded inputs. Detect them before installation/start. Supply the official versioned uv installation instructions in the activation guide; do not replace an existing uv installation automatically. |
| One private runtime venv | Operator environment; TUI tool environment; separate venv | Use `$XDG_DATA_HOME/mlx-tui/runtimes/74e7cf9-py3131/`, defaulting to `~/.local/share`. Pass its interpreter explicitly to uv, irrespective of the user's active environment. |
| Package A's exact runtime freeze initially | Trim/re-resolve dependencies; preserve A's tested set | Copy the freeze as a package resource and sync it with uv. Keeping its existing test dependencies avoids an unqualified dependency change; a smaller runtime set requires a later retained qualification. The immutable Git dependency requires Git, detected before setup. Build-isolation dependencies also need recorded constraints during Phase 1; the freeze alone does not pin them. |
| Explicit managed opt-in | Adopt a discovered server; explicitly choose mode | Existing configurations remain attach by default. Offer Managed setup or Attach on a genuinely new installation; selecting Managed is authorization for its documented download/install/start actions. Never infer ownership from a port or executable name. |
| Direct upstream child, no inference wrapper | In-process MLX; a new serving backend; upstream subprocess | Reuse subprocess/log/health helpers and retain the actual child. Managed switches outside Compare restart the owned child and explain cache loss. The existing comparison keeps upstream's sequential on-demand model loading. |
| Fixed qualified launch contract for C comparison | Silently choose a free port; preserve profile identity | Managed coding comparison uses `127.0.0.1:18080` because port is currently part of both fingerprints. On conflict offer retry or Attach; do not kill the listener or silently change a fingerprint. A different managed comparison port requires explicit profile revision and requalification. |
| Existing Compare is the destination | A second setup comparison UI; extend current pane | Setup ends on Compare with both snapshots and verified runtime facts. Keep its prompt, trial order, quality check, checkpointing, and decisions. Unknown residency, allocator memory, and engine cancellation remain unknown. |
| Five agreed implementation checkpoints | Larger packaging/lifecycle delivery; split every UI component | User confirmed entry gate/packaging → runtime installation → process ownership → setup/Compare → real-runtime/fresh-install validation. Each phase leaves attach mode working. |

uv's documented [explicit environment targeting](https://docs.astral.sh/uv/pip/environments/) and [CLI](https://docs.astral.sh/uv/reference/cli/) support separate environments and sync. Local uv 0.12.7 help confirmed the relevant flags. Full installation of the proposed packaged runtime, including build constraints, remains a Phase 1/2 verification task; no installer or real inference was run during planning.

## Implementation Phases

### Phase 1: Establish the entry gate and installation inputs

Freeze the delivery contract before adding runtime behavior. This phase starts only after the maintainer records B's passed qualification and product gate, or explicitly revises the product gate in `docs/part3.md` with the reason; approval of this draft alone does not supply missing user evidence.

**Changes:**
- `docs/compatibility/milestone-b.md` — inspect the retained two-order results on both physical tiers and five consenting operator observations. Record actual completion/explanation/return counts and owner confirmation. If absent, leave the gate blocked; do not manufacture or relabel observations.
- `docs/compatibility/milestone-c.md` — proposed new report: record the B-entry decision, exact two physical tier identities and hardware/OS access, artifact identity, resolver/interpreter/runtime pins, supported OS test matrix, and owner. Copy observed tier identities from completed B evidence. No broader Mac support claim follows from arm64 detection alone.
- `pyproject.toml` — set proposed version 0.2.0 for the C artifact; keep MLX-LM out of normal TUI dependencies. Regenerate `uv.lock` after the version change using the existing uv workflow. Do not overwrite a previously distributed 0.2.0 artifact; verify version availability before building the final tester artifact.
- `src/mlx_tui/managed-runtime.txt` — proposed new packaged resource: byte-for-byte copy of `docs/compatibility/milestone-a-runtime.txt`. Record its SHA-256 in the C report; assert its MLX-LM commit/MLX pin match both coding profiles.
- `src/mlx_tui/managed-build-constraints.txt` — proposed new resource: capture exact build backend dependency pins observed when building the pinned MLX-LM source in an isolated test environment with uv 0.12.7. Inspect the pinned source's build requirements, retain the build log and resolved versions, rebuild with those constraints, and fail this checkpoint if reconstruction differs. Exact contents are **verify during implementation**, not inferred from A's runtime freeze.
- `tests/artifact_smoke.py` — verify both new resources are in wheel and rebuilt sdist, compare their packaged bytes to source, and validate their runtime pins against packaged profiles. Keep the existing outside-checkout CLI/import checks.
- `README.md` and proposed `docs/activation.md` — document the exact versioned wheel, checksum verification, uv 0.12.7 and Python 3.13.1 install instructions, Git prerequisite for the pinned runtime source, and the initial install invocation `uv tool install --python 3.13.1 /absolute/path/to/mlx_tui-0.2.0-py3-none-any.whl` (**verify during implementation**, replace the example path with the delivered artifact location in tester instructions). Document installation/network time separately from model download time. Record installed TUI dependency versions with each tester run rather than claiming the wheel locks its transitive dependencies.
- `src/mlx_tui/compare_pane.py`, `src/mlx_tui/comparison_persistence.py`, `src/mlx_tui/profiles.py` — remove only the seven unnecessary `str()` conversions identified by the baseline type check, if still present. Preserve normalization and non-string conversions; this is a small prerequisite to making the existing CI gate green.

**Success Criteria:**

#### Automated Verification:
- [x] Wheel/sdist include immutable runtime resources and work outside the checkout: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Existing behavior remains intact: `rtk proxy uv run pytest -q`.
- [x] Lint and format pass: `rtk proxy uv run ruff check .` and `rtk proxy uv run ruff format --check src tests`.
- [x] Existing CI type gate is green: `rtk proxy uv run pyrefly check --min-severity warn`.

> Implementation deviation: the maintainer authorized Phases 1–2 while the
> Milestone B evidence gate remains blocked; no B evidence or C recommendation
> was manufactured.

#### Manual Verification:
- [ ] Maintainer reviews actual B gate evidence, both physical tiers, and recommendation ownership before C implementation advances.
- [ ] Install the retained wheel on a clean supported Mac account without a checkout; launch the CLI from another directory and record exact installed versions and artifact hash.
- [ ] Reconstruct the pinned runtime with the chosen build constraints; record immutable source identity and dependency/build evidence. No fresh-install recruitment begins while these inputs are unresolved.

### Phase 2: Install and verify the isolated runtime

Implement runtime provisioning independently of Textual; attach remains the default and no inference starts during installation.

**Changes:**
- `src/mlx_tui/managed.py` — proposed new module with `runtime_root() -> Path`, `inspect_runtime(path: Path) -> dict[str, JSONValue]`, and `install_runtime(*, on_line: Callable[[str], None], cancel_event: threading.Event) -> Path`. Keep one concrete implementation, using stdlib subprocess/path/import metadata and existing server command streaming.
- `src/mlx_tui/managed.py` — detect Darwin/arm64, the qualified OS matrix, Python 3.13.1, uv 0.12.7, Git availability, writable app data, and disk space before mutation. Unknown OS combinations display untested and are excluded from C recruitment until qualified. Missing tooling provides a specific install action; no fallback into an active operator venv.
- `src/mlx_tui/managed.py` — hold a nonblocking `fcntl.flock` on an app-owned install lock for the complete installation/repair. Create the venv directly at its final path; venv launch scripts contain absolute paths and must not be relocated. Use `uv venv --python 3.13.1 <runtime-root>` and `uv pip sync --python <runtime-root>/bin/python --strict --build-constraints <packaged-build-constraints> <packaged-runtime-freeze>` with argv and `--no-config` (**full operations verify during implementation**). Sanitize Python path/home and uv/pip environment overrides that could redirect the install; preserve legitimate proxy/certificate configuration. Surface an unsupported index override rather than silently consuming it.
- `src/mlx_tui/managed.py` — inspect the target interpreter's version and distribution metadata in a subprocess; verify MLX 0.32.2, MLX-Metal 0.32.2, MLX-LM 0.32.0 with the full VCS commit in `direct_url.json`, all frozen package versions, and console entry point. Capture actual freeze, resource hashes, Python/uv versions, and install provenance. Presence of a marker never substitutes for inspecting the current environment before start.
- `src/mlx_tui/managed.py` — write a versioned completion marker atomically only after inspection passes, using the small temporary-write/fsync/replace pattern in `comparison_persistence.py`. Cancellation terminates/reaps only the install subprocess and leaves the environment incomplete. Retry/Repair may rebuild only the named app-owned runtime under its lock, after confirming no owned server is using it. Refuse unexpected symlink destinations or directories without app ownership evidence; never recursively remove an arbitrary configured path.
- `src/mlx_tui/config.py` — add proposed `runtime_mode: Literal["attach", "managed"] = "attach"` and exact-value parsing/template guidance. Unknown values remain attach; malformed config must not trigger managed installation. Keep existing operator command fields intact. Managed mode ignores those commands and explains that boundary in the UI.
- `tests/unit/test_managed.py` — proposed new focused test module for missing/mismatched versions, direct-URL commit mismatch, wrong platform, concurrent installer, cancelled/failed install, incomplete marker, safe repair paths, and explicit interpreter targeting despite an active foreign venv. Mock network installation; inspect argv rather than downloading in unit tests.
- `tests/unit/test_config.py` — cover default/invalid/managed mode parsing and preservation of existing attach settings.

**Success Criteria:**

#### Automated Verification:
- [x] New installer failure/identity tests and attach regressions pass: `rtk proxy uv run pytest -q`.
- [x] Packaged install resources remain usable outside the checkout: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Static checks pass: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, and `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Install into a clean app data directory; capture actual versions, freeze, build provenance, and elapsed runtime-install time. The operator environment's files/packages remain unchanged.
- [ ] Cancel installation, restart the app, and repair successfully; the incomplete environment is never reported ready. Launching a second installer reports busy without damaging the first.
- [ ] After a successful install, disable networking and inspect the runtime without invoking a resolver or reinstalling it.

### Phase 3: Own startup, recovery, and shutdown

Add an explicit owner of the upstream child and integrate it into all lifecycle entry points before exposing managed setup.

**Changes:**
- `src/mlx_tui/managed.py` — add proposed concrete `ManagedRuntime` with retained `Popen[str] | None`, `ProcessIdentity | None`, current/previous verified model paths, effective argv/environment, install evidence, cancellation event, and closed/shutting-down state. Methods: `start(model: Path, *, on_line: Callable[[str], None]) -> ServerProbe`, `stop() -> None`, and `close() -> None`. Keep start/stop serialized; close must win a race with a pending start. Runtime methods never access widgets.
- `src/mlx_tui/managed.py` — hold the same runtime lock used by installation for the owned server's lifetime, preventing a second TUI from repairing its live environment. Immediately register the child on spawn, before health waiting or a UI callback. Launch the verified runtime interpreter and its `mlx_lm.server` entry point as argv, with the pinned model path, `--host 127.0.0.1 --port 18080 --log-level INFO`. Use a private working directory, sanitized Python environment, and offline Hub mode for a verified complete snapshot. Never pass `--trust-remote-code` implicitly.
- `src/mlx_tui/serverctl.py` — reuse streaming/spawn primitives. Add cooperative cancellation to health waiting, checked before probes and after each HTTP return, with bounded read timeouts. Preserve existing attach behavior by default. Add proposed `terminate_owned_process(proc, identity) -> None`: validate the owned PID/create-time before signalling; TERM, bounded wait, KILL if needed, reap, and report failure rather than swallowing it. Do not apply discovery-based killing or blindly reuse a stale process-group number.
- `src/mlx_tui/managed.py` — detect an occupied port before launch, then verify the actual listening identity matches the retained child before accepting any health/generation response. Preflight is not race-proof; a competing listener or child exit between probe and acceptance is failure. Refuse unknown attribution rather than treating another server's green health as managed success.
- `src/mlx_tui/managed.py` — verify runtime and target assets before stopping the previous child. For an explicit switch, clear generation readiness, stop/reap the owned child, then launch the new target. On failed load/start retain the last successfully verified target as a recovery option; show the actual failed/offline state. `Reload previous model` is an explicit retry, not a fabricated rollback success. Successful HTTP generation establishes dated request evidence, never residency.
- `src/mlx_tui/app/__init__.py` — instantiate one manager for explicit managed mode; route `action_cold_start`, config-model restart, and managed shutdown through it. A managed process discovered later must not be adopted. Keep ownership separate from `ServerIdentity` so poll updates cannot replace the handle that shutdown is allowed to stop.
- `src/mlx_tui/models_pane.py` — route managed load requests through the same manager before `resolve_swap_action`; retain existing `execute_boot` for operator-configured attach commands. Freeze worker inputs at acquisition, hold `OperationKind.RESTARTING` until process cleanup ends, and update the manager even if a UI callback fails. Do not change `execute_boot`'s attach return contract merely to add managed ownership.
- `src/mlx_tui/app/__init__.py` and `src/mlx_tui/chat_pane.py` — add an awaitable chat cleanup method mirroring `ComparePane.wait_for_cleanup`. A single idempotent app shutdown path sets closing first, prevents new work, cancels/awaits chat and Compare, cancels/joins pending managed startup/install work, closes the manager, then closes HTTP. Ensure `main()` also invokes manager cleanup in `finally` if Textual startup/run fails; route SIGINT/SIGTERM through this cleanup and restore handlers afterward. Do blocking process waits off the UI thread.
- `src/mlx_tui/status_bar.py` — display Attach or Managed and, for Managed, `stops on quit`, plus separate starting/stopping/failed and generation evidence. Polling a replacement listener must clear readiness and show an ownership mismatch. Never retain a Ready word after owned-child failure.
- `tests/unit/test_managed.py`, `tests/unit/test_serverctl.py`, and `tests/integration/test_boot_integration.py` — add real short-lived local subprocess checks for quit during boot, immediate child crash, occupied-port races, matching/foreign/recycled identities, TERM escalation, UI callback failure, reload failure then previous-model recovery, and double-close. Assert foreign processes survive. Preserve all attach command tests.

**Success Criteria:**

#### Automated Verification:
- [x] Lifecycle tests prove the spawned child exits/reaps and foreign listeners survive, including boot cancellation and startup exceptions: `rtk proxy uv run pytest -q`.
- [x] Static checks pass: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, and `rtk proxy uv run pyrefly check --min-severity warn`.

Implementation verification (2026-09-12): the full suite passed with 570 tests
and 9 opt-in skips; artifact and static gates also passed. The live MLX and
manual ownership checks below remain unverified.

#### Manual Verification:
- [ ] On a qualified Mac, start/quit, SIGINT, SIGTERM, quit while loading, and fail an explicit switch. Check the owned PID is gone after cleanup; a separately started attach endpoint stays alive.
- [ ] Occupy port 18080 with another service. Managed startup refuses with Retry/Attach actions and never terminates that service.
- [ ] Fail loading a target, observe accurate status, and use Reload previous model successfully. Cache loss is explained before switching.
- [ ] Verify Ctrl+Q remains responsive during bounded cleanup and shutdown failure is visible. SIGKILL/power loss cannot execute Python cleanup; record that limitation rather than claiming a parent-death guarantee.

### Phase 4: Guide setup into the existing comparison

Expose the managed path with a short setup flow and verified evidence, preserving the current comparison and saved choices.

**Changes:**
- `src/mlx_tui/setup_screen.py` — proposed new `SetupScreen(ModalScreen[None])`: choose Managed/Attach; show detected hardware and tested tier; offer the two pinned coding candidates, their revisions, runtime requirement, fixed 8192 context/32 output comparison budget, download sizes/free disk, and dated recommendation status. Ask whether that context budget meets the user's intended work and whether memory is their priority; retain the answer as a preference, not an alteration to the fixed experiment. Explain that larger-context advice is untested. Show memory fit as unknown unless matching reviewed evidence supports a qualified statement; never infer tier from RAM alone.
- `src/mlx_tui/setup_screen.py` — provide Install/Repair runtime, Download missing candidates, Start, Retry, Reload previous model, and Open Compare actions driven by actual prerequisites. Use `ManagedRuntime` and existing pinned `SearchScreen`, `repo_snapshot`, `download_snapshot`, and cache resolution. Install and download progress remain distinct. Each action disables competing operations for its complete worker lifetime and keeps failure next actions keyboard-accessible.
- `src/mlx_tui/models.py` — add proposed `verify_cached_assets(snapshot: Path) -> None` for load readiness: require config, tokenizer config and the pinned profile's tokenizer/template files; parse the weight index when present, require a nonempty shard map, ensure every referenced shard exists, and require standalone weights otherwise. Reject absolute/traversing shard names and broken links, while allowing normal HF snapshot symlinks to that repository's `blobs` directory. Use this common check before managed launch and comparison snapshot verification; Hub cache listing alone is insufficient. Local non-HF paths use their own directory as the asset boundary.
- `src/mlx_tui/comparison_runner.py` — call the common asset completeness check from `verify_profile_snapshot`, retaining existing pinned asset hashes. Update snapshot fixtures in `tests/unit/test_comparison.py` and `tests/unit/test_models.py` with complete minimal snapshots so failures distinguish missing weights from hash mismatches; retain existing UI preflight mocks in `tests/integration/test_compare_integration.py`. Do not execute model-supplied code while checking files.
- `src/mlx_tui/search_screen.py` — preserve exact pinned revisions in the setup path, map gated/unauthorized repositories, missing assets, offline metadata lookup, cancellation, and disk-full errors to specific retry/access/free-space actions. If a pinned snapshot already passes local completeness and hash checks, bypass Hub metadata calls entirely. A downloaded `.py` file is data; execution stays disabled. If a model needs remote code, report unsupported in this C flow and retain Attach as the explicit escape hatch.
- `src/mlx_tui/operations.py` — add proposed `INSTALLING` and `DOWNLOADING` operation kinds to the existing lease where necessary, with releases only after acknowledged cleanup. `SearchScreen` must acquire the same lease so closing its modal cannot permit deletion of a still-downloading candidate. Adapt existing search callers/tests together; do not add a second coordinator.
- `src/mlx_tui/app/__init__.py` — add proposed `--managed` and `--attach` mutually exclusive flags. Existing config stays attach unless explicitly opted in. Offer setup on a missing config; invalid/unreadable config shows a repair message rather than acting like a new installation. Persist only a new config's selected mode/endpoint using atomic creation; do not rewrite an existing user's TOML. Existing config can use the mode field or CLI flag. Mode/endpoint edits require restart so ownership cannot change mid-run.
- `src/mlx_tui/app/__init__.py` — use managed port 18080 only when selected explicitly by the managed flow; explain conflicting CLI/config endpoint choices. Keep arbitrary direct repository/local-path selection available as unqualified Models/Attach usage, without adding it to the recommended shortlist or comparison pair.
- `src/mlx_tui/compare_pane.py` — fill runtime/install/launch/provenance fields from a fresh managed inspection plus actual child argv, identity, manifest hashes, hardware/OS, and qualified tier. Render these as inspected/owned facts, separate from user-entered power/concurrent-load observations. Preserve attach's operator-entered evidence. Revalidate child/runtime/assets at Run, invalidate readiness on repair/restart/profile changes, and pass the resulting facts through existing `ComparisonInput` dictionaries.
- `src/mlx_tui/compare_pane.py` — keep twelve requests, existing timing labels, and the existing summary. Do not restart or issue extra warm-up probes between comparison slots. If owned-child identity changes or exits, fail/retain the checkpoint; no successful continuation across a silent restart. On cancellation, await client cleanup and, in managed mode, explicitly stop/reap the owned server before offering Restart; distinguish confirmed process exit from client cancellation.
- `src/mlx_tui/app/__init__.py` and `src/mlx_tui/compare_pane.py` — separate durable choice saving from managed activation. Keep/Apply saved profile validates the same fingerprint and launches/verifies the chosen target through the manager when needed; ensure both existing callers await its completion. Display `Choice saved; activation failed` on failure, preserve the choice, and offer Retry/Reload previous. Apply request settings only with an explicit selected target and do not leave a stale ready claim. Attach retains its restart instructions.
- `src/mlx_tui/coding_profiles.toml` — populate only reviewed B-qualified evidence for the observed tiers, with confirmed owner, exact result references/hashes, date/expiry, and current fingerprint. Expired/revoked profiles remain inspectable as historical evidence but leave the recommended shortlist. If two current qualified candidates are unavailable, show the blocker rather than inventing replacement recommendations.
- `tests/integration/test_setup_integration.py` — proposed new stub-based setup journey tests: empty config/cache → installation → pinned downloads → owned readiness → Compare → Keep → next chat; missing runtime, corrupt snapshot, disk/access failure, offline cached start, attach choice, keyboard focus, and cancellation. Extend `tests/integration/test_compare_integration.py`, `tests/integration/test_search_integration.py`, `tests/unit/test_models.py`, and existing profile/config tests for the changed contracts.
- `README.md`, `docs/activation.md`, `docs/comparison.md`, and `ARCHITECTURE.md` — document mode/ownership, runtime location, setup/recovery, fixed comparison contract, qualified platforms, offline startup, and shutdown limits. Keep implementation details out of the user-facing setup controls.

**Success Criteria:**

#### Automated Verification:
- [x] Full stub journey, saved-choice failure semantics, common snapshot validation, download lease, and attach regressions pass: `rtk proxy uv run pytest -q`.
- [x] Packaged setup resources and CLI work outside the checkout: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Static checks pass: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, and `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Keyboard-only setup reaches Compare without a server command, TOML edit, or hand-authored runtime evidence. Check 80×24 and 120×40 terminals, long paths/names, visible focus, and scrollable error actions.
- [ ] A fully cached, verified runtime and both model snapshots reach the same comparison with networking disabled; a partial cache produces a specific missing-asset/retry message.
- [ ] Run/Keep and saved-choice reuse activate the intended exact target; failure saves the choice truthfully and recovery works. Prior comparison records retain their frozen identities.
- [ ] Users can distinguish verified runtime installation/ownership, endpoint health, last generation result, and unknown residency/memory fit. Expired advice is not a recommendation.

### Phase 5: Qualify managed behavior and observe fresh installation

Prove the artifact and managed runtime on actual target hardware, then measure activation with fresh-install users.

**Changes:**
- `tests/runtime/test_milestone_c.py` — proposed new opt-in contract. Require `MLX_TUI_C_QUALIFY=1`, absolute `MLX_TUI_C_OUTPUT`, and an observed `MLX_TUI_C_MACHINE_TIER`; fail missing inputs after opt-in, skip without it. Use production managed install/inspection/lifecycle and comparison code with a test-owned app data root, verified cached candidate snapshots, and the pinned endpoint. Never accept an arbitrary supplied PID as ownership.
- `tests/runtime/test_milestone_c.py` — run the existing comparison in both orders with separate retained outputs; check exact identities, all twelve slots, quality checks, and durable reload. Exercise failed load then previous-model reload, version mismatch before spawn, cache-complete offline start, occupied port, cancellation, and shutdown during startup and active requests. Verify the owned PID/create-time is no longer live and a control attach process survives. Record failures as evidence, not skipped success.
- `tests/runtime/test_milestone_c.py` — retain a versioned record of artifact SHA/version, Python/uv/MLX/MLX-LM versions and commit, runtime/build manifests, actual launch argv, snapshots/profile hashes, hardware/OS/tier, process identities, comparison result hashes, failure/recovery outcomes, and install/download/setup durations separately. No ordinary chat prompts or access tokens enter diagnostic output.
- `tests/artifact_smoke.py` — include new CLI flags and packaged setup imports in outside-checkout checks. Extend with an explicit opt-in macOS managed-install smoke entry if needed; ordinary Linux/macOS CI must not download models or run inference.
- `docs/compatibility/milestone-c.md` — retain exact successful/failed commands and hashes for each physical tier, documenting which assertions require live MLX rather than stubs. The future command is `rtk proxy env MLX_TUI_C_QUALIFY=1 uv run pytest -q tests/runtime/test_milestone_c.py` after the output/tier environment is set (**verify during implementation**). Record the actual pass count; zero skipped tests are required for an accepted opted-in contract run.
- `docs/compatibility/milestone-c.md` — add a consent-based observation table for five fresh-install target users across the two qualified tiers: anonymous ID, consent, starting tooling/cache state, artifact hash, hardware/OS/tier, download time, other setup time, comparison reached, assistance, failure/recovery, ownership/shutdown understanding, and dropout/alternative reason. Record real observations; missing observations are not completion.
- `docs/part3.md` — append C implementation versus validation status separately. C is achieved only after at least four of five fresh-install users reach the same trustworthy comparison without manual server setup or developer intervention and ownership/shutdown checks pass. Count dropouts/failures and explain them even if the threshold passes.

**Success Criteria:**

#### Automated Verification:
- [x] Ordinary test suite passes with live contracts safely opt-in: `rtk proxy uv run pytest -q`.
- [x] Distributable wheel/sdist smoke passes: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Static checks pass: `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check src tests`, and `rtk proxy uv run pyrefly check --min-severity warn`.
- [ ] Proposed real C contract command above passes with zero skips on each qualified physical tier, with retained results and no surviving owned process. **Verify during implementation**; it cannot run before the new contract exists and the B gate is met.

Implementation verification (2026-09-12): ordinary tests passed with 570 tests
and 9 opt-in skips; the real contract was not invoked because the required
qualified runtime, snapshots, tier, and B evidence are not available.

#### Manual Verification:
- [ ] Recruit five consenting fresh-install target users only after the exact artifact, resolver, build constraints, platform matrix, and two coding profiles are qualified. At least four reach comparison without manual server setup or developer intervention.
- [ ] Observe shutdown and recovery, including a failed load. Verify process ownership against OS process state and ensure attach environments/processes remain unchanged.
- [ ] Retain completion/assistance/dropout reasons and separate downloads from setup friction. Do not replace observed activation with developer-run smoke checks.
- [ ] Maintainer reviews the evidence and records achieved/blocked status; implementation completion alone does not close Milestone C.

## Out of Scope

- Declaring B passed, inventing hardware/user evidence, or recruiting/publishing recommendations before the gates are satisfied.
- Native installers, app bundles, signing/notarization, auto-updates, channels, broad compatibility, general upgrade recovery, and background operation after quit (Milestone F or a separate background-serving plan).
- Inference-engine rewrites, custom scheduling, telemetry wrappers, multiple resident targets, speculative drafts, optimization profiles, allocator peaks, or new benchmark/scoring systems.
- Durable chat, attachments, multiline editor expansion, retrieval, remote/LAN serving, external-client concurrency guarantees, and model-emitted tool execution.
- Automatically executing remote model code. The C coding flow is limited to profiles that work without it; arbitrary repositories remain explicitly unqualified.
- Guaranteed child termination after SIGKILL or power loss. C must cover ordinary quit, handled signals, and exceptions; an independent parent-death supervisor requires a separate justified change if this limitation blocks activation.

## Risks & Mitigations

- **B remains unproven** → leave C gated and the document Draft. Completing this plan is not authorization to fabricate product evidence or bypass the product decision.
- **A's freeze is not a complete build lock** → capture and constrain build dependencies in Phase 1, verify reproduction, and retain actual install provenance before testers. Git/bootstrap tooling is measured activation friction, not hidden from the denominator.
- **The second tier or its OS requires different dependencies/settings** → stop qualification and revise the pinned contract explicitly; re-run affected B evidence instead of silently broadening compatibility.
- **Server startup races with shutdown or another port owner** → retain ownership at spawn, close before accepting new work, verify live listener identity, join workers, and test failure/quit interleavings with real short-lived subprocesses.
- **A successful health response comes from another server** → require the retained child's identity to match the listener before managed readiness; discovery alone cannot authorize stop.
- **Cancelled client request leaves engine work running** → describe client cancellation accurately; managed cancellation may stop/reap the owned child before restart, while attach makes no engine-stop claim.
- **Virtualenv relocation breaks scripts** → create at the final owned path under a lock; commit a completion marker only after verification, rather than renaming a staging venv.
- **Partial HF cache looks loadable** → common shard/tokenizer completeness checks plus profile hashes and live generation; offline startup never resolves repository HEAD.
- **Comparison provenance improves but metrics remain limited** → keep first-request labels, sampled process RSS scope, and inconclusive conclusions. Owned launch does not prove residency, effective sampler behavior, or per-profile memory.
- **Working tree evolves during planning** → re-read cited functions and run the baseline before each phase; preserve existing unrelated edits. Future files/signatures/commands are explicitly proposals to verify during implementation.
