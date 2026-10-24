## Plan Review: Unified configuration and Settings

**Verdict:** APPROVE

**Date:** 2026-10-17  
**Plan:** [2026-10-17-unified-configuration.md](../../plans/2026-10-17-unified-configuration.md)  
**Review:** 1

The plan defines a coherent boundary between saved configuration, active request settings, and runtime ownership. No confirmed findings remain after reviewing the persistence design, startup and recovery flows, request-state integration, migration, and verification strategy against the current working tree, including its staged changes. Approval applies to the plan; implementation acceptance still requires the specified automated and manual checks.

### Coverage

No prior review of this plan was found. The existing `docs/reviews` directory determines the review location, with this review filed under its `plans` subdirectory.

| Lens | Status and scope |
| --- | --- |
| Architecture | Checked: `config.py` / proposed `config_io.py` / `presets.py` responsibilities, import direction, disk snapshot ownership, CLI resolution, app initialization, and Setup navigation. |
| Code Quality | Checked: shared validation and ranges, compatibility helpers, one production writer, reuse of atomic writes and request restoration, error propagation, and phase boundaries. |
| Correctness | Checked: managed/attach resolution and partial overrides, exactly-once first-run initialization, Save/Reload/Apply boundaries, nullable model selection, session reconciliation, comparison restoration, and restart comparisons. |
| Security / Safety | Checked: command generation with argv parsing and loopback restriction, diagnostics privacy, invalid-document preservation, byte conflicts, writer locking, atomic replacement, symlink handling, and migration backup/rollback contracts. |
| Compatibility | Checked: flat schema and alias precedence, optional values, legacy preset precedence/import, current session and comparison behavior, explicit test construction, and the proposed TOML writer dependency. |
| Test Coverage | Checked: existing config/preset/diagnostics units, mounted editor/setup/boot/session/comparison tests, fixtures, planned persistence and Settings cases, and installed-artifact smoke boundaries. |
| Standards / Usability | Checked at plan level: labels, keyboard access, validation focus, scrolling, dirty-form confirmation, repair access, effective/default hints, and saved/active/override notices. Actual terminal behavior remains an implementation acceptance check. |
| Documentation | Checked against the referenced README, configuration, activation, usage, architecture, and development guides. The update scope covers changed startup, shortcuts, preset ownership, editor waiting, and save/apply/restart behavior. |
| Portability | Checked: XDG paths, disposable test roots, installed entry points, unchanged platform scope, and explicit separation of HTTP-stub evidence from real MLX behavior. |
| Performance | Not applicable as a separate lens: this is local configuration editing and startup sequencing, with no new continuous workload, remote service, or computational hot path. |
| Database | Not applicable: no database or database schema change. TOML migration and session compatibility were checked under Safety, Correctness, and Compatibility. |

The source review covered the plan's referenced configuration, app, preset, parameter, profile, session, diagnostics, and test interfaces. It was bounded to this change rather than a whole-repository audit. Future Settings implementation, visual terminal QA, installed-artifact execution, and real-provider validation cannot be established by this source review.

### Cross-Cutting Themes

- **Separate saved defaults from active work.** Architecture and Correctness support `config_document` as the saved owner because presets, coding profiles, and comparison restoration already replace `app.config`. Request-only Apply and operation/session guards make this separation enforceable without changing New chat inheritance.
- **Protect user documents at the write boundary.** Safety, Compatibility, and Test Coverage align around preserving unrelated TOML content, rejecting stale snapshots, importing presets only on Save, and retaining originals on failure. The final external-editor replacement race is explicitly acknowledged rather than claimed solved by an advisory lock.
- **Keep runtime ownership stable.** Architecture, Correctness, and Usability agree on deferred initial setup followed by restart-only runtime changes. The tradeoff is delayed activation of saved runtime settings; the endpoint preview and persistent restart notice explain that delay.

No unresolved conflict between lenses requires a product decision before implementation.

### Findings

#### Critical

None.

#### Major

None.

#### Minor

None.

### Strengths

- ✅ One launch resolver removes the current disagreement between CLI endpoint selection and the app constructor, with explicit tests for partial overrides and managed port 8080.
- ✅ The saved-document boundary fits existing preset/profile mutation and comparison restoration. The plan also accounts for `restore_request_state()` suppressing profile-modified marking and explicitly requires that marker afterward.
- ✅ The persistence design reuses `json_util.atomic_write_bytes()` while adding config-specific conflict, symlink, backup, and migration checks around it. The selected [TOML Kit document API](https://tomlkit.readthedocs.io/en/latest/quickstart/) supports style-preserving edits; fixtures must still establish the promised preservation for this app's cases.
- ✅ Canonical-key presence, including `preset = []`, gives migration a clear precedence rule and an explicit opt-out, while preserving the legacy file for rollback.
- ✅ Verification uses the appropriate existing boundaries: pure persistence/resolution units, mounted Settings flows, installed entry-point smoke checks, and a separate real-provider manual check. Existing editor failure and runtime cleanup coverage is retained.

### Verification

Ran the focused existing baseline against the current working tree:

```sh
rtk uv run pytest tests/unit/test_config.py tests/unit/test_presets.py tests/unit/test_diagnostics.py tests/integration/test_config_integration.py
```

**Result:** 94 passed in 6.59s.

This establishes the current baseline only. The planned configuration writer and Settings screen do not exist yet, so these passing tests do not validate their future behavior. Full-suite, lint, typing, artifact smoke, terminal usability, and real MLX checks were not run during this review.

### Recommended Changes

No corrective plan edits are required. Proceed in the specified phase order and retain the planned acceptance checks, especially exactly-once initialization, unchanged active chat state on Save/Reload, and file-preservation failures.

The plan and application code are unchanged. If the plan is revised, re-review the affected lenses and interfaces and append the result to this review.
