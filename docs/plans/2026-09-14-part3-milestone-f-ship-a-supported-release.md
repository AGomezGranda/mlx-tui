# Milestone F — Ship a Supported Release

**Date:** 2026-09-14
**Work Item:** n/a
**Status:** In Progress

## Overview

Prepare a verifiable wheel/sdist release with private local diagnostics, explicit uv upgrades and rollback, and tested managed-runtime recovery. Qualify only the available Mac; keep broader hardware support, public-release readiness, and adoption claims blocked until their actual evidence exists.

The user selected manual upgrades/rollback and the five phases below, and confirmed that no additional Mac/macOS is available. These decisions approve the planning scope, not the completed plan or a release. No application code is changed by this document.

## Current State

- `docs/part3.md:467` defines F's packaging/version matrix, diagnostics, upgrades/recovery, clean installation and offline startup, no orphaned managed process, one week of personal use, and two weeks of use by multiple external target users. B–D remain unvalidated and E's shared-serving guarantees remain blocked; see the milestone status sections and `docs/compatibility/milestone-e.md:1`.
- `pyproject.toml:3` identifies version 0.2.0, Python >=3.13, Hatchling wheel/sdist packaging and `mlx_tui.app:main`. TUI dependencies use lower bounds; the wheel does not freeze its entire dependency resolution. MLX remains a separate runtime dependency.
- `tests/artifact_smoke.py:51`, `:76`, `:114`, and `:169` inspect packaged resources, rebuild the sdist, and install both wheels outside the checkout. Version assertions hard-code 0.2.0. There is no upgrade/downgrade exercise, retained-artifact option, or installed CLI diagnostic check.
- `.github/workflows/ci.yml:1` already runs Linux/macOS checks, highest/floor dependency resolutions, and Linux artifact smoke. A CI runner's successful Python checks do not establish real MLX compatibility; architecture and OS must be recorded from the runner itself.
- `src/mlx_tui/managed.py:36` pins MLX-LM commit `74e7cf931e84ef7c2f63e875adf414e20decc1c5`, MLX/MLX-Metal 0.32.2, MLX-LM 0.32.0, Python 3.13.1, uv 0.12.7, and macOS 26.6.2. `_preflight` at `:160` enforces installation conditions. `inspect_runtime` at `:256` checks runtime packages and source identity, but uses uv during inspection and emits a recorded uv pin rather than independently checking that executable's version there.
- `install_runtime` at `src/mlx_tui/managed.py:485` installs/repairs the final app-owned versioned path under a lock; `ManagedRuntime` at `:564` holds the runtime lock while serving, retains PID/create-time, and starts cached snapshots with offline environment flags. `_terminate_install` at `:418` terminates only the installer handle although its subprocess starts a new session; installer descendants and shutdown completion need explicit coverage.
- `src/mlx_tui/app/__init__.py:364` aborts and waits for Compare/Chat before managed close; an earlier cleanup exception can bypass later cleanup. `main` at `:1216` provides a synchronous managed-close fallback and SIGINT/SIGTERM handlers, but no `--version` or diagnostic export.
- `src/mlx_tui/setup_screen.py:104` calls an existing completion-marker file “ready” without a fresh inspection. Its installer worker (`:231`) receives cancellation on unmount (`:293`), but app shutdown does not explicitly join that installer to establish that cleanup finished.
- `src/mlx_tui/serverctl.py:143` validates child identity before terminating its process group, waits for exit, and escalates if the leader remains alive. Existing unit coverage includes process groups, but all exit scenarios need external process assertions rather than just cleared Python fields.
- `src/mlx_tui/sessions.py:67`, `:839`, `:999`, and `:1074` support session schemas 1/2, strict decoding, atomic durable snapshots and safe interrupted-session recovery. `src/mlx_tui/comparison_persistence.py:595`, `:675`, and `:716` implement schema-1 results/choices. Reuse these stores; no new migration framework is needed.
- `src/mlx_tui/config.py:37`, `src/mlx_tui/presets.py:21`, `src/mlx_tui/sessions.py:848`, and `src/mlx_tui/comparison_persistence.py:36` define the exact config/presets/state locations. Backups must cover these locations, including XDG overrides, without including the HF model cache or copying a Python virtual environment.

### Verified planning inputs and baseline

The current host reports Darwin arm64, macOS 26.6.2, Python 3.13.1 and uv 0.12.7. Historical hardware evidence names `local-m4-16gib` (Mac mini Mac16,10, Apple M4, 16 GiB); confirm that identity during live qualification instead of deriving it from the OS version alone.

The existing `dist/mlx_tui-0.1.0-py3-none-any.whl` has SHA-256 `226aae53187aa6573120f8bd644a8969d16465ba2981a990c166a9d8a5b99b03`. Archive metadata confirms version 0.1.0 and no managed-runtime or session module. It is a candidate attach/config upgrade baseline, not proof of a published release or a session-capable predecessor. The 0.2.0 checksum in `docs/activation.md` belongs to a historical C build and must not be assigned to newly built bytes.

| Command run during planning | Result |
|---|---|
| `rtk proxy uv run ruff check .` | Passed |
| `rtk proxy uv run ruff format --check src tests` | Failed: app entry module, models pane and managed unit tests need formatting |
| `rtk proxy uv run pyrefly check --min-severity warn` | Failed: 13 unnecessary conversion warnings and one deprecated contextmanager return annotation |
| `rtk proxy uv run python tests/artifact_smoke.py` | Passed: wheel and rebuilt sdist install/import/CLI checks |
| `rtk proxy uv run pytest -q tests/unit/test_managed.py tests/unit/test_serverctl.py tests/unit/test_sessions.py tests/integration/test_setup_integration.py` | Passed; two existing multithreaded-fork deprecation warnings |
| `rtk proxy uv run pytest -q` | Passed (exit 0); opt-in runtime contracts skipped; two existing multithreaded-fork deprecation warnings |

`uv tool install --help` confirms `--force`, `--python`, and `--constraints`; `uv pip freeze --help` confirms inspection of a specified interpreter. Upgrade execution itself has not been performed. New files, CLI options and test names explicitly described below are proposed additions, **verify during implementation**; they are not existing APIs.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Wheel/sdist, explicit uv replacement and rollback | Existing artifacts; native installer; self-updater/channels | User chose manual upgrades. Reuse the existing resolver/build workflow. |
| One physically available Mac; separate packaging and inference matrices | Advertise broad macOS/Python support; evidence-labelled matrix | User has only this Mac/macOS. Broaden artifact checks, not untested runtime claims. Keep current managed platform/runtime pins. |
| Proposed F candidate version 0.3.0 | Reissue 0.2.0 bytes; unique version | Distinguish new bytes from historical C evidence. Confirm the version is unused in the intended distribution location before retaining a candidate. |
| Allowlisted local JSON diagnostics via CLI | Export raw logs/config/state; new support UI or uploader | Works when startup fails, needs no network, and avoids private content by construction. |
| Preserve current data formats and versioned runtime paths | Migration framework; in-place runtime upgrades | Existing stores suffice. Runtime pin/resource changes must get a new directory identity rather than mutate a predecessor's environment. |
| Retained old bytes plus pre-upgrade data backup | Automatic downgrade/migration; copying virtualenvs | Old versions cannot necessarily read new session formats. Preserve post-upgrade work separately before restoring a backup. |
| Test and strengthen existing lifecycle ownership | New daemon/supervisor; arbitrary process discovery/kill | Reuse PID/create-time and process-group handling. Never kill an operator-owned listener or infer ownership from a port. |
| Separate implementation from F acceptance | Treat green CI as release qualification | Multi-user usage, wider hardware evidence, and upstream E enforcement remain unresolved. No publication is part of this plan. |

## Implementation Phases

### Phase 1: Release artifacts and an honest support matrix

Make candidate artifacts uniquely identifiable and restore the existing release checks before adding behavior.

**Changes:**
- Before changing versions, extend `tests/artifact_smoke.py:169` with an explicit retention-directory argument. Refuse existing output filenames; retain wheel/sdist bytes, SHA-256 values, source revision plus dirty-tree status, Python/uv versions, and installed dependency versions from both smoke environments. Keep the default temporary-directory behavior. Retain a fresh pre-F 0.2.0 checkpoint for session/managed upgrade tests; label it a new development checkpoint, not the historical C wheel.
- `tests/artifact_smoke.py:51` and `:76` — read the expected project version using stdlib `tomllib`; compare built wheel and sdist metadata with it instead of repeating 0.2.0 literals. Preserve license, entry-point and exact packaged-resource checks.
- `pyproject.toml` and `uv.lock` — set the proposed unique F candidate version to 0.3.0 and regenerate the lock through uv. Keep runtime pins, package dependency scope and Python requirement unchanged. If 0.3.0 is already used, resolve the release identity before building; never overwrite an existing candidate.
- `src/mlx_tui/app/__init__.py:1216` — add argparse `--version` from installed `importlib.metadata.version("mlx-tui")`; exit before config loading, setup, polling or runtime construction. Source execution is already an installed uv project; do not add a duplicate version constant.
- `tests/artifact_smoke.py:114` — verify installed `--version` against wheel metadata in both outside-checkout environments and record actual installed TUI dependency versions.
- `.github/workflows/ci.yml` — run artifact smoke on the existing Linux and macOS runner targets, recording actual OS/architecture/interpreter. Preserve existing floor/highest jobs and attach their resolved package versions to support evidence. Never infer real MLX qualification from these jobs.
- `src/mlx_tui/chat_pane.py` — remove only the 12 redundant casts reported in `capture_request_settings`, `_update_action_visibility`, and `end_turn`; retain conditions and exception behavior. `src/mlx_tui/session_screen.py:176` — remove the reported redundant `str`. `src/mlx_tui/sessions.py:931` — use the checker-supported `Generator[SessionLock]` return annotation and import, preserving lock semantics.
- `src/mlx_tui/app/__init__.py`, `src/mlx_tui/models_pane.py`, `tests/unit/test_managed.py` — apply only the formatter's reported changes. This is baseline check repair, not unrelated refactoring.
- New `docs/compatibility/milestone-f.md` — create separate tables for artifact/dependency tests and real runtime qualification. Include exact artifact hashes, Python/uv, macOS/architecture, runtime freeze/build-resource hashes, machine tier, date and evidence path. Seed historical A/C information as historical only; all F live rows start “not run”.
- `README.md` and `docs/activation.md` — link the candidate/support record, distinguish current F candidate instructions from historical C evidence, and explicitly scope managed support to the tested combination. Do not advertise newer OS/Python versions from metadata alone.

**Success Criteria:**

#### Automated Verification:
- [x] Wheel/sdist packaging and installed entry points pass: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Existing behavior remains green: `rtk proxy uv run pytest -q`.
- [x] Lint passes: `rtk proxy uv run ruff check .`.
- [x] Formatting passes: `rtk proxy uv run ruff format --check src tests`.
- [x] Strict type check passes with no diagnostics: `rtk proxy uv run pyrefly check --min-severity warn`.

#### Manual Verification:
- [ ] Inspect retained 0.2.0 checkpoint and F candidate manifests; each hash matches its own bytes, and no historical artifact was replaced.
- [ ] Review matrix labels: packaging-tested, runtime-qualified, historical, untested and blocked cannot be mistaken for one another. Wider physical-tier rows remain blocked.

> Implementation note: live host inspection on 2026-09-15 reports macOS 27.0.0
> on the named arm64 Mac, while the managed runtime matrix remains pinned to
> macOS 26.6.2. F runtime qualification therefore remains unrun/unsupported
> on this host; no newer-OS support is inferred.

### Phase 2: Private diagnostics that work without a running TUI

Provide a small support snapshot that does not collect user content or contact an endpoint.

**Changes:**
- New `src/mlx_tui/diagnostics.py` — add proposed `collect_diagnostics() -> dict[str, object]` and `write_diagnostics(path: Path) -> None`. Emit schema version 1, UTC creation time, TUI version, Python version, OS/architecture, total RAM, allowlisted TUI dependency versions, packaged expected runtime pins/resource hashes, config presence/parse status and configured attach/managed mode. Use installed metadata, `platform`, existing config parsing, `psutil`, `hashlib`, and stdlib JSON/filesystem APIs.
- In the same module, distinguish expected runtime pins from recorded installation metadata. Inspect only a bounded, regular non-symlink completion marker and allowlist version/hash values; label them “recorded, not verified now”. Missing/corrupt marker or unavailable package metadata yields fixed status codes plus unknown fields. Do not execute the runtime interpreter, installer, configured commands or HTTP probes just to export diagnostics.
- Exclude hostnames, endpoint URLs, local paths, usernames, environment/proxy values, arbitrary package direct URLs, raw exception strings, logs, system prompts, drafts, answers, reasoning, tool arguments, attachments, session titles and comparison contents. Do not traverse session/HF-cache directories or hash private prompts/files. Bound marker reads to 1 MiB; validate versions/hashes before emitting values. Synthetic secret-canary tests cover both successful and failure paths.
- `write_diagnostics` writes only to the explicitly supplied local path with exclusive creation and mode 0600. Refuse overwrite and symlink targets; remove a partial file on a failed write, report a fixed error category, and return a nonzero CLI exit. No upload, automatic export, content opt-in flag or default persistent diagnostic log.
- `src/mlx_tui/app/__init__.py:1216` — add `--diagnostics PATH`, handled before ordinary config/startup and app construction. Reject combinations with endpoint/runtime-mode overrides so the snapshot's scope is unambiguous. A malformed config still permits a report; its error detail is not copied into JSON.
- New `tests/unit/test_diagnostics.py` — exercise missing and malformed config/markers, invalid version/hash values, unavailable package metadata, secret exclusion, no network/subprocess execution, private permissions, overwrite/symlink refusal and partial-write cleanup. Reuse pytest/monkeypatch conventions; no new framework.
- `tests/artifact_smoke.py:114` — invoke installed `--diagnostics` from the outside-checkout work directory using isolated XDG roots. Parse JSON, assert schema and installed version, and verify no setup/config/state mutation occurred.
- `README.md` and `docs/activation.md` — document the proposed command, exact collected fields, omitted content, local review/delete steps and the distinction between recorded install metadata and current readiness.

**Success Criteria:**

#### Automated Verification:
- [x] Diagnostic privacy and CLI regression tests pass as part of `rtk proxy uv run pytest -q`.
- [x] Installed wheel/sdist diagnostics and version reporting pass: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] All three static commands from Phase 1 pass unchanged.

#### Manual Verification:
- [ ] Export with a broken config and no running server; inspect JSON for useful version/status fields and absence of paths, secrets and conversation content.
- [ ] Read the help/error messages in a narrow terminal; output location and recovery action are clear, and no report is sent anywhere.

### Phase 3: Manual upgrades and rollback without lost state

Exercise the actual installed-artifact replacement path and document its limits.

**Changes:**
- `tests/artifact_smoke.py` — add proposed optional `--upgrade-from PATH` with a required expected SHA-256 argument. Validate wheel metadata/name/hash before installation. Use isolated `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, XDG config/state/data/cache roots and an outside-checkout working directory so no personal tool installation, configuration, state or runtime is touched.
- In the same script, install the retained predecessor, replace it with the candidate using `uv tool install --force --python 3.13.1 <wheel>`, then reinstall the same retained predecessor bytes. Capture exact dependency resolutions and exit status for each stage. The complete replacement command is **verify during implementation**; help flags were checked during planning, execution was not.
- Seed synthetic legacy TOML config and presets using the existing file locations before replacement. Assert byte preservation and candidate parsing. For the retained pre-F 0.2.0 checkpoint additionally create synthetic sessions (including a draft, acknowledged answer and attachment snapshot), a comparison and committed choice through that installed predecessor's public store APIs. After upgrade, reopen through the candidate's APIs without requests and verify content/settings/snapshots/choice are preserved. Never use the developer's real conversations.
- Exercise failed replacement with a test-owned invalid artifact and verify reinstalling the predecessor plus reopening the preserved files recovers. Preserve and hash state before each operation. A repeat install of the candidate should not change data. Do not require uv to implement an undocumented atomic-update guarantee.
- `tests/unit/test_sessions.py` and `tests/unit/test_comparison.py` — extend existing schema tests only where missing: unknown newer versions fail without rewriting their source bytes; loading legacy session v1 maps in memory and does not silently overwrite until an explicit save. No schema bump is planned.
- `docs/activation.md` — document quit/flush first, record current package versions and artifact hash, back up config/presets plus the app's state directory, verify candidate bytes, explicitly replace the tool, inspect diagnostics, reopen saved work and reverify the runtime. Keep the old wheel, dependency constraints and backup until the candidate qualifies. Use the current XDG locations, not only default paths.
- Document rollback separately: stop the candidate and owned child; make a separate backup of any post-upgrade work; reinstall the retained predecessor using its recorded dependency constraints; restore the pre-upgrade backup only by explicit operator action. Never merge incompatible schemas automatically or delete newer work. A 0.1.0 rollback restores legacy functionality only; that wheel cannot resume sessions or run managed mode.
- `src/mlx_tui/managed.py` and `docs/activation.md` — retain the current versioned runtime directory unchanged for this candidate. State and test the rule that any future runtime commit/Python/freeze/build-constraint change must receive a distinct directory identity. Repair remains explicit and locked; never sync new requirements into a live/previous runtime as part of a TUI upgrade.
- `docs/compatibility/milestone-f.md` — retain separate records for 0.1.0 attach/config replacement and the new pre-F 0.2.0 development checkpoint's session/managed preservation. Missing predecessor bytes are a blocked row, never replaced by fabricated fixtures labelled as historical upgrade evidence.

**Success Criteria:**

#### Automated Verification:
- [x] Default build/install smoke still passes: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] Optional artifact upgrade mode runs against both retained predecessors and passes installation, byte-preservation, reopen and rollback assertions. Its new exact CLI invocation is **verify during implementation** and is recorded alongside hashes in `docs/compatibility/milestone-f.md`.
- [x] Persistence and lifecycle regression baseline passes: `rtk proxy uv run pytest -q tests/unit/test_managed.py tests/unit/test_serverctl.py tests/unit/test_sessions.py tests/integration/test_setup_integration.py`.
- [x] Full tests and all three Phase 1 static checks pass.

#### Manual Verification:
- [ ] On this Mac, perform candidate replacement and rollback in an isolated tool installation, reopen saved work, verify unchanged attach config and confirm no operator-owned server was stopped.
- [ ] Follow the backup/rollback recipe with a post-upgrade synthetic session: preserve it separately, restore the old backup deliberately, and verify the newer session remains recoverable with its compatible version.

### Phase 4: Shutdown and failure recovery with observable outcomes

Close known lifecycle gaps using existing ownership and cleanup functions.

**Changes:**
- `src/mlx_tui/setup_screen.py:231` — retain completion of the existing installer worker with a `threading.Event` set in `finally`, independent of UI callbacks. Add proposed `async cancel_install_and_wait() -> None` to signal cancellation and await that completion without blocking Textual's event loop. Do not release the operation while installer cleanup is still running; UI callback failure during unmount must not prevent subprocess cleanup.
- `src/mlx_tui/app/__init__.py:364` — cancel/await active setup installation before shutdown finishes. Arrange Compare, Chat, managed runtime and HTTP cleanup with independent `try/finally` boundaries so failure in one does not skip the others. Preserve ordinary quit's save-failure retry/discard behavior and `main`'s final owned-runtime close fallback. Reuse the installer wait path for SIGINT/SIGTERM-driven exit.
- `src/mlx_tui/managed.py:418` — route installer cancellation through existing `serverctl.terminate_failed_process`, since the installer starts a dedicated session. Preserve bounded waits and verify exit instead of reporting cancellation complete while descendants still run. Keep failed install markers incomplete and allow retry only after lock release.
- `src/mlx_tui/serverctl.py:143` — add a failing regression for an owned parent exiting on TERM while a known descendant ignores TERM. Finish cleanup of the originally owned group without signalling reused/unrelated identities; preserve bounded escalation and errors when ownership cannot be verified. Reuse existing process-group handling rather than creating a process registry or daemon.
- `src/mlx_tui/setup_screen.py:104` — replace marker-only “ready” with “installation recorded; verification required”. Label successful installation as runtime installation verified, not model-ready; actual generation readiness continues to require the existing target probe.
- `tests/unit/test_managed.py`, `tests/unit/test_serverctl.py`, `tests/integration/test_setup_integration.py`, and `tests/integration/test_app_integration.py` — cover quit during install/start/chat/comparison, cleanup callback exceptions, repeated close, SIGINT/SIGTERM, port conflicts, child exit, failed load and repair/retry. Use real short-lived Python child processes for ownership tests, record PID/create-time before shutdown and assert process/descendant exit and port release after it. An unrelated listener must survive every scenario. Use events and finite deadlines, not arbitrary long sleeps.
- `docs/activation.md` — make quit and signal behavior explicit. SIGKILL/power-loss cannot run in-process cleanup; record abrupt-termination behavior honestly and provide manual port-conflict recovery without adoption or broad `pkill`. An orphan in any claimed supported shutdown path blocks release. If F is interpreted to require automatic SIGKILL cleanup too, that remains an unresolved release blocker requiring a separately reviewed ownership design, not an implied guarantee from these changes.
- `docs/compatibility/milestone-f.md` — separate automated dummy-process results from real-MLX shutdown results and include failed/unknown cleanup outcomes rather than deleting them.

**Success Criteria:**

#### Automated Verification:
- [x] Real dummy-process lifecycle/ownership assertions pass: `rtk proxy uv run pytest -q tests/unit/test_managed.py tests/unit/test_serverctl.py tests/unit/test_sessions.py tests/integration/test_setup_integration.py`.
- [x] App exit, save-recovery and signal tests pass with the full suite: `rtk proxy uv run pytest -q`.
- [x] Artifact smoke and all three Phase 1 static checks pass.

#### Manual Verification:
- [ ] On the named Mac, quit and send SIGINT/SIGTERM during managed installation, model startup and generation; verify owned PID/create-time disappearance, descendants gone and port reusable, then successfully restart.
- [ ] Induce a failed load and partial installation; observe an accurate state and explicit retry/repair/reload action. An independently started attach server stays alive.
- [ ] Perform an isolated abrupt-termination exercise and retain its actual outcome. Do not translate an untested or orphan-producing path into a no-orphan claim.

### Phase 5: Retained release qualification and usage gates

Exercise final installed bytes and record the evidence still needed for an actual release decision.

**Changes:**
- New `tests/runtime/test_milestone_f.py` — follow the opt-in/fail-closed pattern of `tests/runtime/test_milestone_c.py` and the finally-written failure records in `tests/runtime/test_milestone_e.py`. Proposed required inputs: `MLX_TUI_F_QUALIFY=1`, absolute `MLX_TUI_F_ARTIFACT`, expected `MLX_TUI_F_ARTIFACT_SHA256`, absolute test-owned `MLX_TUI_F_RUNTIME_ROOT`, pinned `MLX_TUI_F_MODEL_PATH`, slug `MLX_TUI_F_MACHINE_TIER`, and absolute fresh `MLX_TUI_F_OUTPUT`. No opt-in means skip; opted-in missing/invalid inputs fail. Derive actual installed version/hash rather than accepting a caller's label as proof.
- Run the live contract from the installed candidate environment, outside the checkout, with isolated writable state and explicit runtime ownership. Verify installed imports are outside source, artifact bytes match, runtime inspection passes, model revision/assets match a packaged profile, the port is unused, and the host matches the declared matrix row. Do not install/download models implicitly in the runtime test.
- Use `ManagedRuntime.start/close`, the existing `chat.stream_turn`, and the Textual test harness pattern already used by E to run a fixed synthetic coding request, cancellation and subsequent recovery; save/reopen a synthetic session and verify acknowledged content plus immutable attachment snapshot. Independently observe process exit/port release, including before-start, idle and during-generation shutdown. Keep actual engine cancellation unknown unless separately established.
- Retain per-scenario start/end, pass/fail/unknown, artifact/runtime/profile identity, hardware/OS, exact package versions, PID/create-time and cleanup result in `finally`, even when setup or a request fails. Ordinary prompts, private file content, arbitrary exception payloads and tokens must not enter the evidence record. Give every subprocess/request a finite timeout and clean up only test-owned processes.
- `docs/compatibility/milestone-f.md` — specify and record a clean-account install of the final wheel with empty TUI/runtime state on this same Mac, the Phase 3 predecessor upgrades, and an offline restart with complete runtime/model assets after networking is disabled by the operator. Verify a request succeeds with no runtime/model download. `HF_HUB_OFFLINE=1` alone is not proof of network-disconnected operation. Capture install/runtime/download times separately and retain failed attempts.
- Record the release workload checklist: Chat success, draft persistence, client cancellation, endpoint/context error, attachment snapshot after source changes, Compare plus saved choice, failed-load recovery, and keyboard/narrow-terminal use. Require no unhandled crash, no loss of acknowledged saved work, and an accurate state plus next action after every failed load. Cite D/B/E evidence only where the exact tested artifact/workflow remains applicable; missing evidence stays open.
- Treat the proposed 5% upstream latency target as a separate unverified claim. For any release performance claim, run matching synthetic prompt/model/template/budgets/settings on the same Mac in alternating app/direct-upstream batches, at least five repeated requests per batch across three batches, reporting first-after-load separately and medians/ranges plus power/load conditions. A failed/inconclusive check prevents that claim; it is not a reason to relabel client timings as engine timings. Do not add a benchmark platform in F.
- Add consent-based observation tables for seven days of personal real use and fourteen days of use by at least two external target users (two is this draft's minimum interpretation of “multiple”, not a measured threshold from Part 3). Record anonymous IDs, consent, actual dates/days, opportunities, completed/assisted tasks, restarts/recovery, saved-work losses, endpoint reuse if applicable, dropouts and alternatives. Keep content out and do not invent users or count missing observations as successful days.
- `docs/part3.md`, `README.md`, `docs/activation.md`, and `docs/compatibility/milestone-f.md` — update implementation versus qualification status after checks run. Preserve B/C/D adoption blockers and E shared-serving restrictions. Record the final release decision as blocked until reliability/adoption requirements are met; broader hardware remains unsupported with only this Mac available. No publishing, tagging, uploading or participant messaging is authorized by implementing this plan.

**Success Criteria:**

#### Automated Verification:
- [x] Full default suite passes and live checks remain opt-in: `rtk proxy uv run pytest -q`.
- [x] Final wheel/sdist smoke passes: `rtk proxy uv run python tests/artifact_smoke.py`.
- [x] All three Phase 1 static checks pass.
- [ ] New live qualification command, **verify during implementation**: `rtk proxy "$MLX_TUI_F_PYTHON" -m pytest -q "$MLX_TUI_F_CONTRACT"` with validated F inputs. Set `MLX_TUI_F_PYTHON` to the absolute candidate-environment interpreter and `MLX_TUI_F_CONTRACT` to the absolute copied contract file outside the checkout; install only the existing test dependencies in that environment and record its resulting resolution. Run from the isolated work directory with no source `PYTHONPATH`; assert candidate imports come from that installation. Require zero skips and retain failures/cleanup outcomes.

> Phase 5 implementation (2026-09-15): added the fail-closed installed-artifact
> contract at `tests/runtime/test_milestone_f.py`, isolated evidence metadata and
> results, and the README/activation/Part 3 instructions. The default contract
> skips without opt-in; an opt-in run with missing inputs fails as required.
> No validated candidate environment, runtime/model inputs, or live run exists
> on the current macOS 27.0 host, so the live command remains unchecked.

#### Manual Verification:
- [ ] Final artifact passes clean-account installation, both applicable upgrade paths, deliberate rollback and genuinely offline startup on this Mac; hashes and observed package versions are retained.
- [ ] Real-MLX workload/recovery and shutdown results meet the declared scope; no fake-process test is offered as inference evidence.
- [ ] Personal seven-day and external fourteen-day observation windows complete with real consented records. Review assistance, losses and dropouts before deciding whether the release gate passes.
- [ ] Review all B–F gates and outstanding hardware/lifecycle limits explicitly. If evidence or guarantees remain missing, retain “release blocked”; implementation completion does not mark Milestone F achieved.

## Out of Scope

- Automatic updates, update notifications, release channels, app signing/notarized native installers, additional package managers and public publishing.
- New MLX/runtime versions, speculative OS/Python allowlists, a second physical tier inferred from RAM limits, or claims about unavailable Macs.
- A daemon, multi-user/LAN serving, model routing, enforcing E's missing upstream policy, background analytics, diagnostic uploads or arbitrary process adoption.
- A migration framework, automatic downgrade, automatic backup deletion, copying virtual environments, cache migration, and new chat/session features.
- Promising zero loss of unsaved work after SIGKILL/power loss, guaranteed engine cancellation, warm KV restoration or unmeasured performance.

## Risks & Mitigations

- The working tree already contains extensive milestone work → retain pre-F bytes and provenance before editing, preserve unrelated changes, and do not assign old hashes to new builds.
- Baseline checks currently fail → Phase 1 fixes the exact reported formatting/type diagnostics; re-run before broadening fixes because concurrent edits may change the baseline.
- Only one Mac and no adoption records are available → build release preparation now, keep broader support and F acceptance blocked rather than fabricate qualification.
- A wheel allows dependency resolution drift → retain tested resolved versions/constraints with every candidate and rollback record; use existing floor/highest jobs as separate compatibility evidence.
- Recorded install markers can be stale or contain private/malicious text → allowlist and bound diagnostic reads, label them unverified, and use actual runtime inspection for startup qualification.
- Installer cancellation and process-group cleanup can outlive the UI → independently observe worker completion and process identities; cleanup failures are visible failures and block the supported shutdown guarantee.
- Older versions cannot interpret newer saved work → preserve post-upgrade state separately, retain compatible predecessor bytes, and require explicit backup restoration instead of lossy automatic conversion.
- Live contracts can disturb a running endpoint → require a test-owned runtime, free fixed port, explicit opt-in and isolated state; never terminate discovered operator processes.
- SIGKILL cannot execute app cleanup → retain the abrupt-termination result and its scope limitation; do not silently waive a broader no-orphan acceptance requirement.

## Final Plan Review

- Requirements map to phases: packaging/matrix → 1; diagnostics/privacy → 2; upgrade/rollback → 3; ownership/recovery → 4; clean install/offline checks and real-use evidence → 5.
- No new runtime/profile pin, storage schema or dependency is required. Proposed additions are identified as such, and unexecuted upgrade/live commands are explicitly marked for implementation verification.
- Recommend the `review-plan` skill for an independent technical pass before approval. Keep **Status: Draft** until the user explicitly approves the completed plan.
