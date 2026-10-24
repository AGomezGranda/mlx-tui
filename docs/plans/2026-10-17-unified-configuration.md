---
date: 2026-10-17
title: Unified configuration and Settings
status: complete
---

# Unified configuration and Settings

Make `config.toml` the home of user configuration, with an in-app form for common settings and direct access to the same file. First launch and later edits use the same screen and persistence rules. The user approved the three phases below and selected an in-app form plus TOML access.

## Current State

References describe the working tree inspected on 2026-10-17, including the user's existing staged changes. Implementation must preserve those changes.

| Area | Current behavior and evidence |
| --- | --- |
| Location | `config_path()` resolves `$XDG_CONFIG_HOME/mlx-tui/config.toml`, falling back to `~/.config`: [config.py:37](../../src/mlx_tui/config.py#L37). |
| Creation | `write_template()` writes commented examples; `create_config()` writes only mode, endpoint, and an attach start command: [config.py:43](../../src/mlx_tui/config.py#L43). |
| Startup | The CLI reads the config twice, applies overrides, and chooses managed port 18080 separately: [cli.py:45](../../src/mlx_tui/app/cli.py#L45). The app constructor also rewrites a managed endpoint: [app.py:143](../../src/mlx_tui/app/app.py#L143). |
| Setup | Mode selection writes only if the file is missing: [setup_screen.py:194](../../src/mlx_tui/setup_screen.py#L194). Later choices are temporary. `set_runtime_mode()` retains the old endpoint: [ops.py:123](../../src/mlx_tui/app/ops.py#L123). |
| Access | `Ctrl+G` invokes an external editor, then replaces the in-memory config. Only host/port changes produce a restart notice: [ui.py:261](../../src/mlx_tui/app/ui.py#L261). Setup is opened automatically only on first run: [app.py:272](../../src/mlx_tui/app/app.py#L272). |
| Validation | Unknown or mistyped keys silently fall back; numeric values are clamped: [config.py:124](../../src/mlx_tui/config.py#L124). Syntax/encoding errors have a recovery path. |
| Other configuration | User presets live in a sibling file: [presets.py:21](../../src/mlx_tui/presets.py#L21). Chat parameter defaults/ranges are also defined in [params.py:15](../../src/mlx_tui/params.py#L15). |
| Session settings | Presets and coding profiles replace `app.config` in memory: [ui.py:32](../../src/mlx_tui/app/ui.py#L32), [state.py:90](../../src/mlx_tui/app/state.py#L90). Request settings are captured in chat snapshots: [persistence.py:162](../../src/mlx_tui/chat_ui/persistence.py#L162). |
| Separate data | Sessions and comparison results use XDG state storage. The coding-profile catalogue is a packaged, validated resource: [profiles.py:393](../../src/mlx_tui/profiles.py#L393). These are not user configuration files to consolidate. |

## Design Decisions

### One file, compatible shape

- Keep the existing location and flat keys. Add the existing `[[preset]]` structure inside `config.toml`; an explicit `preset = []` means no presets. Avoid a simultaneous migration to nested sections.
- Keep `max_context` as a read alias with existing precedence. When the form changes context length, write `max_ctx` and remove the conflicting alias; otherwise preserve it and explain the alias in validation notices.
- Preserve comments, unrelated keys/tables, and unset optional values. Update only fields edited in the form; do not serialize the current chat or every effective default.
- Use `tomlkit` for document edits, adding it through `rtk uv add tomlkit` and committing the resulting `pyproject.toml` and `uv.lock` changes. There is no existing TOML writer. Its [document API](https://tomlkit.readthedocs.io/en/latest/quickstart/) preserves comments and formatting, avoiding a custom TOML patcher. Do not promise byte-identical layout for every exotic TOML construct; verify semantic preservation of unrelated content.
- Installation itself remains free of user-config side effects. The documented next command, `mlx-tui`, opens Settings on a fresh launch. Saving creates the file. `--help`, `--version`, and diagnostics remain read-only.

### Settings behavior

Use a scrollable modal with visible labels, keyboard navigation, and these groups:

| Group | Controls |
| --- | --- |
| Server | Runtime mode; attach host and port; an optional “Enable local server start” checkbox for creating the bundled attach command. Display the effective endpoint and any CLI override. |
| Chat defaults | Optional model identifier/path, system prompt, temperature, top-p, maximum output tokens, context budget, seed, and thinking choice (default/on/off). Blank optional fields mean unset, with the effective fallback shown as a hint. |
| Advanced | Config path, Open TOML, Reload, preset count/source, and access to Runtime setup. Start/stop commands, shell execution, swap policy, and preset definitions remain editable in TOML. |
| Actions | Save, Cancel, and Apply chat defaults. Save writes defaults; applying them to the current chat is a separate explicit action. |

`Ctrl+G` opens Settings. Open TOML uses the existing editor mechanism, extended to `$VISUAL`, then `$EDITOR`, then `vi`. GUI editors must wait for closing; show this in help. Returning successfully from the editor uses the same reload/validation path as Reload.

Cancel/Escape never saves. With a dirty form, use the existing `ConfirmScreen` to confirm discarding edits before close, Reload, or Open TOML. If the file is missing, Open TOML explicitly offers to create the standard template before opening it; do not create it merely by opening Settings.

### Saved defaults and active values

- Keep `app.config` as the existing active configuration/request snapshot to limit caller churn. Add `app.config_document` as the authoritative disk snapshot, including saved defaults and presets. Form fields always read that snapshot.
- Save and Reload update the disk snapshot and preset list. They do not overwrite an open chat's model, parameters, draft, or historical request snapshots. Show “Saved; apply chat defaults here or restart to use them.”
- Apply chat defaults changes only model, system, temperature, top-p, max tokens, context, seed, and thinking for subsequent requests, using existing request-state helpers. A blank saved model clears explicit selection; it does not stop a server or claim the resident model changed.
- Preserve existing New chat behavior: it inherits current request controls. This change does not silently reset parameters when creating a chat. Reopening a saved chat keeps the current saved-versus-current reconciliation.
- Runtime mode, host, port, start/stop commands, shell execution, and swap policy become active at the next launch. Show a persistent “Restart required” notice while those saved effective runtime fields differ from the launch snapshot; exclude request fields from this comparison. Never relabel an active managed child as attached because a file changed.
- Apply chat defaults is disabled while chat, comparison, installation, or model switching is active, and during unresolved session reconciliation/read-only state. Saving defaults can remain available because it does not mutate those operations.

### Endpoint resolution and overrides

Preserve the documented managed default and the ability to pass explicit CLI overrides:

| Effective mode | Endpoint without CLI endpoint flags | Explicit CLI endpoint flags |
| --- | --- | --- |
| Attach | Saved host/port, falling back to `127.0.0.1:8080` | Override only the fields supplied. |
| Managed | `127.0.0.1:18080`; saved host/port remain attach settings | Override only the fields supplied, including an explicit port 8080. |

CLI mode flags override the saved mode for this run. None of these overrides are written to disk. The form labels host/port as attach settings and disables those controls while Managed is selected, showing the managed endpoint instead. The same resolver serves startup, Settings preview, and pending-restart comparisons. Remove the constructor's independent endpoint rewrite.

First-run Settings appears before connection polling or managed-manager construction. Its first successful Save initializes runtime services from the saved configuration, so choosing Managed cannot inherit attach port 8080. Cancel continues once with default attach settings without writing a file. Existing `--attach`/`--managed` first-run shortcuts still bypass onboarding and remain temporary; the Settings entry is available afterward.

### Validation and safe persistence

- Known wrong types, non-finite numbers, invalid enums, empty host, ports outside 1–65535, and out-of-range sampling/context values produce field errors. Preserve current accepted numeric ranges: temperature 0–2, top-p 0–1, max tokens 1–16384, context 1024–131072. Accept integer TOML values for floating fields, but never booleans as numbers. Seed remains an optional integer without inventing a provider-specific range.
- Unknown keys produce warnings and survive writes. Validate preset entries with their index/name in the error. Do not silently skip invalid presets or hide misspellings.
- Invalid syntax/UTF-8 or unreadable files open Settings in a repair state with the path and error. Disable form Save for an unparseable document; allow editor/reload recovery. Do not overwrite corruption with defaults. At startup, defer server services until repair succeeds or the user explicitly continues with defaults for this run.
- Re-read and compare file bytes before saving. If changed since the form opened, retain the draft and require Reload; do not silently merge or overwrite external edits. Serialize app writers with a short advisory lock on a sibling lock file, and repeat the comparison under that lock. External editors do not honor this lock, so a simultaneous write in the final replacement window remains a documented limitation.
- Write a sibling temporary file, flush/fsync it, and atomically replace the target; clean up on failure. Reuse `json_util.atomic_write_bytes()` with `ConfigWriteError`, despite its module name, instead of building another general storage abstraction. New config files are private (`0600`). Resolve an existing symlink to its target and preserve the link; compare the resolved target again at save to detect retargeting. Test these config-specific behaviors around the helper.
- Before the first legacy-preset import, create an exclusive, byte-for-byte `config.toml.pre-unification.bak` if the config exists. Never overwrite that backup. A failed save keeps the prior document active and preserves the form draft.

### Preset migration and rollback

If `config.toml` contains `preset`, it is authoritative, including an empty list. Otherwise read `presets.toml` as a compatibility fallback and show “Legacy presets: import on Save.” A successful Settings save imports that document's preset array, preserving order and names; it leaves the original file untouched. Recheck legacy bytes before import as well as config bytes. Invalid legacy entries block import with an actionable error rather than being dropped. Users can explicitly set `preset = []` in TOML to opt out of the legacy file.

Read-only startup, Reload, and diagnostics never migrate. Once imported, changes to `presets.toml` have no effect; the notice names the canonical file. On rollback, the old app still has its original preset file and understands existing flat keys. Presets edited after migration require manually copying the `[[preset]]` entries back to `presets.toml`; document this limitation. Restore the backup only by explicit user action.

## Phase 1 — Unify configuration handling

### Implementation progress

- [x] Shared launch resolver and precedence tests.
- [x] Validated document reader, shared ranges, and preset parsing.
- [x] Comment-preserving patch writer, migration, and conflict/failure tests.
- [x] Startup, app, editor, setup, and diagnostics integration.
- [x] Phase 1 automated criteria and disposable-XDG verification.

Implementation notes: direct `MlxTuiApp` construction keeps explicit `AppConfig`/endpoint inputs and does not read disk; the CLI supplies the read-once document. The resolved symlink target is retained privately in `ConfigDocument` for conflict detection. Preset parser tests now expect actionable errors rather than silent filtering, and setup's template assertion permits the shared commented header. The Settings form and deferred first-run services remain Phase 2 work.

Verification (2026-10-17): all Phase 1 success criteria passed. The required test files plus setup, boot, parameter controls, app shutdown, and managed-runtime regression tests passed together: **230 tests**. `rtk uv run ruff check .` passed, `rtk uv run pyrefly check --min-severity warn` reported zero diagnostics, and `rtk git diff --check` passed. A disposable-XDG check inspected the one-field patch diff (comments, commands, and Unicode table contents preserved), ran an actual editor subprocess through the headless app's edit/reload action, and exercised CLI startup dispatch with managed port explicitly overridden to 8080; config bytes stayed unchanged by the CLI. Runtime launch was stubbed for this check; no real MLX server was started. Existing staged work was preserved.

### Overview

Introduce one validated configuration document and safe patch-writing path. Connect existing startup and editor consumers to it before adding the form.

### Changes

- `src/mlx_tui/config.py`: retain `AppConfig` and `config_path()`. Define shared generation defaults/range constants, `ConfigIssue(key: str, message: str, severity: Literal["error", "warning"])`, `LaunchOverrides(host: str | None = None, port: int | None = None, runtime_mode: RuntimeMode | None = None)`, and `resolve_launch(config: AppConfig, overrides: LaunchOverrides) -> AppConfig`. Add `validate_config(data: dict[str, object]) -> tuple[ConfigIssue, ...]`. Keep the pure mapping conversion here. Replace the two divergent config templates with one documented template, including host, port, and a commented preset example. Remove `write_template()` and `create_config()` after retargeting their callers/tests to `config_io.save_config()`; there is one production write path.
- New `src/mlx_tui/config_io.py`: define `ConfigDocument` with `path: Path`, `source: bytes | None`, `config: AppConfig`, `presets: tuple[Preset, ...]`, `issues: tuple[ConfigIssue, ...]`, and `legacy_source: bytes | None`. Add `read_config(path: Path | None = None) -> ConfigDocument` and `save_config(document: ConfigDocument, changes: dict[str, str | int | float | bool | None]) -> ConfigDocument`. `None` deletes an optional key. Validation precedes writing; the function handles alias normalization, legacy import, backup, conflicts, and safe replacement. TOML document objects remain private to this module.
- `src/mlx_tui/presets.py`: retain `Preset`; expose `presets_from_mapping(data: dict[str, object]) -> tuple[tuple[Preset, ...], tuple[ConfigIssue, ...]]` for unified/legacy parsing. Preserve public file loaders only where existing callers still need them, delegating to the shared parser. Avoid a config/presets import cycle: `config.py` does not import presets or config I/O.
- `src/mlx_tui/params.py`: import shared defaults and ranges; retain the current chat-control normalization behavior. Strict Settings validation must not silently clamp form input.
- `src/mlx_tui/app/cli.py`: read once, retain `LaunchOverrides`, and call `resolve_launch()`. Pass the document and overrides into `MlxTuiApp`; repair errors remain visible. Remove the separate managed endpoint branch.
- `src/mlx_tui/app/app.py`: add optional constructor parameters `config_document: ConfigDocument | None` and `launch_overrides: LaunchOverrides | None`, retaining explicit test construction with `AppConfig`. Initialize disk-backed presets from the document; delete implicit managed endpoint rewriting. Store the resolved launch snapshot for restart comparisons.
- `src/mlx_tui/app/ui.py`: make the existing editor action reload through `read_config()` and preserve active runtime fields. Use the unified creation path. Update `_cycle_preset()` guidance to the resolved config path. In `setup_screen.py::_persist_new_mode()`, route the existing create-only behavior through `save_config()` for this phase; Phase 2 removes that separate setup writer altogether.
- `src/mlx_tui/diagnostics.py::_config_info`: use the same validation while retaining the existing privacy contract and status vocabulary (`missing`, `malformed`, `valid`). Never include issue text or config values in diagnostics.
- Update `tests/unit/test_config.py`, `tests/unit/test_presets.py`, `tests/unit/test_diagnostics.py`, and affected CLI/editor fixtures to the unified reader. Retain `parse_config()`/`load_config()` as compatibility helpers with their existing behavior and tests; all production configuration entry points use `read_config()` and expose validation issues.

### Tests first

Unit tests in new `tests/unit/test_config_io.py`, using temporary directories:

- `test_config_patch_preserves_unedited_content`: comments, advanced commands, unrelated tables, Unicode/multiline strings, optional deletion, and alias replacement; assert reparsed values and relevant comments.
- `test_save_rejects_invalid_known_fields`: parameterized type/range/enum errors, bool-as-number, nonfinite values, and invalid preset entries; original bytes stay intact. Unknown keys warn and survive.
- `test_save_conflict_or_io_failure_preserves_original`: external edit/deletion, late creation of a missing file, changed legacy file, retargeted symlink, lock contention, and replace failure; no partial file becomes active.
- `test_preset_import_is_explicit_and_idempotent`: no writes on read, imports on Save, canonical/empty precedence, backup preservation, legacy source unchanged, failed import cannot erase entries.
- `test_config_save_updates_symlink_target`: target updated, symlink preserved, subsequent read equivalent.

In `tests/unit/test_config.py`, add `test_resolve_launch_precedence` covering both modes, each partial CLI override, explicit managed port 8080, and no mutation of saved defaults. Extend existing diagnostics tests for field errors without leaking values. Retain the existing editor integration cases for failed editor launches and malformed TOML.

### Success criteria

- Automated: `rtk uv run pytest tests/unit/test_config.py tests/unit/test_config_io.py tests/unit/test_presets.py tests/unit/test_diagnostics.py tests/integration/test_config_integration.py`.
- Automated: `rtk uv run ruff check .` and `rtk uv run pyrefly check --min-severity warn`.
- Manual: with a disposable XDG directory, open/edit a commented config and inspect the diff; verify a CLI override changes the effective endpoint without changing file bytes.

### What we're NOT doing

No nested schema redesign, alternate config path flag, environment-variable mirror of every field, generic settings registry, session-schema migration, or removal of compatibility aliases.

## Phase 2 — Add Settings and integrate first launch

### Implementation progress

- [x] Settings form reads saved values; Save/Cancel/reopen mounted test.
- [x] Validation, dirty navigation, editor repair, and failed-save coverage.
- [x] Deferred first-run initialization and runtime action guards.
- [x] Runtime setup navigation and removal of temporary mode/advisory controls.
- [x] Explicit Apply action and operation/session guards.
- [x] Phase 2 automated criteria and disposable-XDG layout verification.

Implementation notes: Settings keeps a saved document independently of active request state, including after Open TOML. The existing editor tests now explicitly Apply when testing request changes. First-run cancellation is labeled **Continue defaults** so its effect is explicit. Runtime setup uses a scrollable container and replaces Settings rather than stacking it. `ConfirmScreen` accepts an optional affirmative label for Discard/Create while retaining existing deletion behavior. The footer regression now expects Settings. Small README/usage/configuration/activation edits keep this intermediate phase accurate; the comprehensive Phase 3 documentation and release work remain planned. No substantive scope deviations were needed.

Verification (2026-10-17): the four required integration files plus the footer regression passed together (**51 tests**). Another **179** startup, session, comparison, swap, managed-runtime, configuration, and shutdown regression tests passed across focused runs (**230 distinct tests** in total). `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, `rtk uv run pyrefly check --min-severity warn` (zero diagnostics), and `rtk git diff --check` passed. With disposable XDG directories, mounted keyboard journeys at **80×24** and **120×40** covered scrolling, visible inline errors, reachable Save/Continue defaults, attach/managed Save, reopening Settings, and Runtime setup. This caught and fixed hidden inline error labels. A real-terminal CLI launch also opened first-run Settings and shut down cleanly on SIGTERM. No runtime installation, model download, or real MLX server start was performed. Existing staged work was preserved.

### Overview

Make common configuration editable through one persistent UI entry point. Show the same screen before first-run runtime initialization, and keep installation/recovery accessible afterward.

### Changes

- New `src/mlx_tui/settings_screen.py`: implement `SettingsScreen(ModalScreen[ConfigDocument | None])` with constructor `SettingsScreen(document: ConfigDocument | None, *, error: str | None = None, first_run: bool = False)`. Implement the groups/actions above with Textual `Input`, `Select`, `Checkbox`, `TextArea`, and a scrollable container. Track changed fields separately from displayed effective defaults; maintain errors next to their fields and focus the first invalid control on Save.
- `src/mlx_tui/app/app.py`: bind `Ctrl+G` to `action_settings()` and delegate to `app/ui.py`. Extract existing connection/client, polling, and managed-manager initialization into `_start_runtime_services(config: AppConfig) -> None`, called exactly once after first-run choice or immediately for valid existing configuration. Keep UI construction, local model browsing, and error display available before that point. Guard runtime actions until initialized.
- `src/mlx_tui/app/ui.py`: add `action_settings(app: Any) -> None`, a dismissal handler that updates the disk snapshot, and `action_runtime_setup(app: Any) -> None`. Add `apply_chat_defaults(app: Any) -> None` and wire Apply in this phase, following the request-field whitelist and operation/session guards above. Keep `action_edit_config()` as the Settings Open TOML action; it must return its reload result to the form rather than mutate active request state. Settings and Runtime setup navigate by dismissing the current modal before opening the other; unrelated modal screens cannot stack a Settings screen through the global shortcut.
- `src/mlx_tui/setup_screen.py`: retain install/repair, download, start, retry, previous-model recovery, and Compare actions. Replace mode-selection buttons with a Settings action and show active mode plus any pending restart. Remove `_persist_new_mode()`, `_select_mode()`, and the advisory context/memory toggles; they currently save no real configuration and should not resemble functioning settings. Keep installer cancellation/cleanup intact.
- `src/mlx_tui/app/app.py`: remove `setup_preferences` and the `set_runtime_mode()` delegate; remove `app/ops.py::set_runtime_mode()` after replacing Setup's calls with Settings navigation. Retarget tests of this helper to first-run resolution or saved-mode/restart behavior. Keep `ensure_managed_runtime()` tied to the active launch configuration.
- For fresh local attach setup, the explicitly selected local-start checkbox writes `start_cmd = "mlx_lm.server --host <host> --port <port>"` using shell-safe argument quoting for the existing argv parser. Enable only for literal loopback hosts, with `command_shell = false`. Existing custom commands are preserved and shown as advanced; changing endpoint fields does not rewrite them, and the form warns that command arguments must match. First-run local attach defaults the checkbox on to retain today's working start path.
- Update `tests/conftest.py`/`tests/integration/conftest.py` only where required to provide isolated config documents and deterministic first-run service initialization. Reuse the existing harness, HTTP stub, and subprocess stubs.

### Tests first

Mounted component tests in new `tests/integration/test_settings_integration.py`:

- `test_settings_save_cancel_and_reopen`: edit common values by keyboard, Cancel leaves bytes/active state unchanged, Save persists, and reopening displays saved values independently of current chat controls.
- `test_settings_validation_and_dirty_navigation`: invalid fields focus correctly; removing an optional value is distinguishable from zero/false; discard confirmation protects edits on Escape/Reload/Open TOML.
- `test_settings_failed_save_retains_draft`: inject one writer error; error is visible, draft survives, active state stays unchanged. Atomic-write details remain unit-tested.
- `test_first_run_uses_saved_endpoint_once`: parameterize attach/managed and cancel; assert one service initialization at the expected endpoint, documented config creation, and no second initialization after reopening Settings.
- `test_invalid_startup_can_be_repaired`: malformed file remains untouched; editor repair plus Reload enables continuation without a silent overwrite.

Adapt `tests/integration/test_setup_integration.py` to open Runtime setup from Settings and retain install/download/Compare/cancellation coverage. Replace tests of advisory buttons with reachability and active-mode assertions; do not duplicate installation internals.

### Success criteria

- Automated: `rtk uv run pytest tests/integration/test_settings_integration.py tests/integration/test_setup_integration.py tests/integration/test_boot_integration.py tests/integration/test_config_integration.py`.
- Automated: `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, and `rtk uv run pyrefly check --min-severity warn`.
- Manual: launch in a fresh XDG directory, configure attach, save, reopen Settings, then open Runtime setup. Repeat managed initialization without installing/downloading anything. Check keyboard-only navigation and scrolling at 80×24 and a larger terminal; errors and Save/Cancel must remain reachable.

### What we're NOT doing

No form-based preset catalogue editor, embedded TOML code editor, runtime-version configurator, automatic model downloads, or new installation wizard. Runtime operations keep their existing implementations.

## Phase 3 — Make application behavior predictable and document it

### Implementation progress

- [x] Saved/active request boundaries, explicit Apply, and session/profile regression coverage.
- [x] Persistent runtime summaries, ownership, and CLI override coverage.
- [x] Read-only `--config-path` utility and installed artifact checks.
- [x] Configuration, activation, usage, architecture, and development documentation.
- [x] Phase 3 automated criteria and disposable-XDG verification.

Implementation notes: Phase 2 already supplied the request-field whitelist,
operation/session guards, and override-aware restart calculation; Phase 3 adds
regression coverage without replaying their implementation. Apply now marks
the active profile modified before `restore_request_state()` captures session
settings, fixing a stale `profile_modified = false` snapshot. A persistent main
screen restart notice complements Settings' separate active/saved/CLI/form
summaries. Comparison restoration continues to normalize optional sampling
values to effective defaults; its regression checks request settings rather
than requiring unchanged optional representation. Full-suite verification
exposed a timing-dependent Zen fixture failure: invalid placeholder attachment
hashes could reach autosave and expand its error row. A forced save reproduced
the validation error; those fixtures now use valid content hashes. No runtime
or session policy changes were needed.

Manual verification (2026-10-18): built and installed a wheel into a disposable
target outside the checkout, verified its import location, and ran the mounted
Settings journey with fresh XDG roots at **80×24** and **120×40**. Covered
read-only `--config-path`, first-launch Save, keyboard navigation, inline
validation, save/reopen, an actual editor subprocess, invalid-TOML recovery,
legacy import with exact pre-import backup, explicit Apply, and runtime edits
becoming active after restart. A real **MLX-LM 0.31.3** attach server streamed a
successful request using the existing cached **Qwen3-1.7B-4bit** snapshot in
offline mode. Saving during that conversation preserved current parameters
until Apply and retained its draft/history. The temporary server was stopped
afterward. No model downloads or managed-runtime installation were performed;
the pinned managed installer is not supported on this host's macOS 27.0.1.

Automated verification (2026-10-18): `rtk uv run pytest` passed **890 tests**,
including every Phase 3 focused criterion. The two warnings are existing
multi-threaded `os.fork()` deprecations in session-lock tests. `rtk uv run ruff
check .`, `rtk uv run ruff format --check src tests`, `rtk uv run pyrefly check
--min-severity warn` (zero diagnostics), `rtk uv run python
tests/artifact_smoke.py`, and `rtk git diff --check` passed. Artifact smoke
installed both the fresh wheel and a wheel rebuilt from the sdist outside the
checkout; `--config-path` left all disposable XDG roots absent. Existing staged
and unstaged work was preserved.

### Overview

Finish the boundary between saved configuration and active work, then verify the installation-to-editing journey and explain it in user documentation.

### Changes

- `src/mlx_tui/app/ui.py`: verify `apply_chat_defaults()` sources only saved request fields and preserves active runtime fields. Reuse `restore_request_state()` for model/parameter refresh and session dirty handling, then mark the active profile modified explicitly. Ensure a cleared model also clears explicit selection. Finish removal of the old wholesale-config reload logic; reload uses disk-snapshot updates and a pending-restart calculation using `resolve_launch()` and the preserved CLI overrides.
- `src/mlx_tui/settings_screen.py`: wire Apply chat defaults and persistent active/saved/override/restart summaries. Saving twice, reverting runtime edits, or editing a field masked by a CLI flag must produce accurate pending-restart status. CLI overrides continue for the lifetime of the process, including after Reload.
- `src/mlx_tui/chat_ui/persistence.py` and `src/mlx_tui/app/state.py`: preserve current session/profile behavior; ensure no request-state restore writes `config_document`. Strengthen the existing session persistence test to assert actual stored temperature/system values, replacing the vacuous `n_posts == 0 or True` assertion in `tests/integration/test_sessions.py` if still present when implementation begins.
- `src/mlx_tui/app/cli.py`: add a mutually exclusive utility flag `--config-path` that prints the resolved canonical location without creating directories, launching the TUI, or contacting the server. Reject combinations with diagnostics, mode, or endpoint overrides. Keep richer CLI config commands out of scope because Settings already handles editing and validation.
- `tests/artifact_smoke.py`: extend installed wheel and sdist smoke checks with `--config-path` under disposable XDG roots and assert no files/directories are created. The existing mounted component journey covers editing; do not build a second terminal automation harness.
- `README.md`: make the post-install/checkout flow `mlx-tui` → Settings → Save → model/start, with a visible `Ctrl+G` entry. `docs/configuration.md`: document every key, form versus advanced fields, unset/default behavior, canonical presets, validation, overrides, save/apply/restart rules, editor behavior, and migration/rollback. `docs/activation.md`: describe first launch and permanent Runtime setup access; describe managed endpoint defaults and explicit CLI overrides accurately. `docs/usage.md`: update shortcuts and session/default distinctions. `ARCHITECTURE.md`: record disk snapshot versus active request state and ownership rules. `docs/development.md`: list the focused Settings checks.

### Tests first

- Component: `test_save_and_reload_do_not_change_active_chat` in `test_settings_integration.py`: draft, model, current parameters, attachments, and historical session settings survive; explicit Apply changes the next-request settings and does not send a request.
- Component: `test_pending_runtime_changes_keep_active_ownership`: save attach↔managed and command/endpoint changes; existing HTTP client and owned manager remain unchanged, notice persists, reverting clears it. Keep manager shutdown coverage in existing managed tests.
- Component: `test_cli_overrides_survive_config_reload`: override display and effective endpoint stay consistent; overrides never appear in saved bytes. Pure precedence combinations remain unit-tested in Phase 1.
- Component: `test_apply_defaults_respects_operation_and_session_guards`: busy operations and reconciliation/read-only state block Apply; editing saved defaults remains possible.
- Unit: `test_config_path_flag_has_no_side_effects` and argument-conflict cases in `tests/unit/test_config.py`. Installed artifact checks verify packaging/entry-point wiring only.
- Retain focused regression coverage for profile application, saved-chat reconciliation, comparison restoration, and managed shutdown; add assertions to existing tests instead of duplicating complete flows.

### Success criteria

- Automated: `rtk uv run pytest tests/unit/test_config.py tests/integration/test_settings_integration.py tests/integration/test_config_integration.py tests/integration/test_sessions.py tests/integration/test_compare_integration.py tests/unit/test_managed.py`.
- Release verification: `rtk uv run pytest`, `rtk uv run ruff check .`, `rtk uv run ruff format --check src tests`, `rtk uv run pyrefly check --min-severity warn`, and `rtk uv run python tests/artifact_smoke.py`.
- Manual: in disposable config/state directories, install a wheel, locate config with `--config-path`, complete first launch, edit/save/reopen, open TOML, recover from invalid input, import legacy presets, and verify a restart applies pending runtime changes. During an ordinary chat, saving defaults must preserve its current parameters until Apply. Check one real MLX attach run on supported hardware; the HTTP stub does not establish provider compatibility.

### What we're NOT doing

No live endpoint reconnection or automatic app restart, automatic persistence of every chat adjustment, file watcher, new session reconciliation policy, benchmarking workflow redesign, or expansion of managed-runtime platform support.

## Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Saved defaults accidentally capture a preset/profile/session | Form reads only `config_document`; write only dirty form keys; test with deliberately different active and saved values. |
| File editing loses comments, custom commands, or another edit | Document-preserving TOML edits, byte comparison, short app-writer lock, atomic replacement, and migration backup. Describe the external-editor race honestly. |
| Validation changes previously silent behavior | Explain exact fields and values needing repair; preserve source; provide Open TOML and explicit continue-with-defaults. Unknown keys remain warnings. |
| Saving a runtime mode changes process ownership mid-session | Runtime fields remain from the launch snapshot until restart; first-run initialization is deferred and occurs once. |
| Existing custom start command disagrees with edited endpoint | Preserve the command and show the mismatch caveat; only generate a new bundled command on explicit enablement. |
| Presets disappear or duplicate during migration | Canonical-key presence decides precedence; preserve legacy file/order/names; import once on Save and test empty-list semantics. |
| Rollback loses newly edited presets | Keep the original legacy file and migration backup; document manual backport of post-migration preset changes. |
| Worktree changes move references or test expectations | Recheck named functions and callers before each phase; code wins over this dated plan. Do not reset or overwrite staged work. |

## Delivery and validation assumptions

Implement in phase order. Phase 1 is a coherent configuration backend change used by existing entry points; Phase 2 introduces the form and first-run flow; Phase 3 completes explicit Apply behavior and release documentation. Each phase has its own tests before implementation. If split into PRs, merge in that order and keep user-facing documentation accurate in each intermediate PR.

The current flat format, XDG paths, local HTTP scope, managed pins, and session/comparison schemas are retained. No additional product decision is required before implementation. Validate form usability manually at the terminal sizes above and validate comment preservation with fixtures before committing to the writer. If either fails, adjust the implementation within these contracts rather than silently weakening them.

All three phases are implemented and verified as recorded above.
