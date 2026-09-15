# Managed activation

The C implementation is opt-in. Existing configurations remain attach mode;
the operator-owned server commands are not used by managed mode.

The current F candidate is `mlx-tui` 0.3.0 and is packaging-tested only.
Managed support remains scoped to Darwin arm64 on macOS 26.6.2 with Python
3.13.1 and uv 0.12.7; see the [Milestone F support record](compatibility/milestone-f.md).

## Install the TUI

Use the retained wheel bytes for a run, not a source checkout:

```sh
shasum -a 256 mlx_tui-0.3.0-py3-none-any.whl
uv tool install --python 3.13.1 /absolute/path/to/mlx_tui-0.3.0-py3-none-any.whl
mlx-tui --version
```

The retained F candidate wheel SHA-256 is
`e4dd8c7c5817f3fb358dd43f6454eb2f2815d7381821120c03b2fa417f15e44a`.
The retained pre-F 0.2.0 development checkpoint is kept separately for
upgrade/rollback tests; the historical 0.1.0 and C records are not replaced.
The wheel hash, installed TUI version, Python version, and uv version belong in
the activation record. Wheel installation/network resolution is measured
separately from managed-runtime installation and model downloads.

## Upgrade and rollback

Quit first and wait for the installer, chat, comparison, and managed child to
finish. Record installed package versions and the candidate wheel SHA-256, then
back up the config and presets plus the app state directory:

```sh
uv pip freeze --python "$(uv tool dir)/mlx-tui/bin/python" > pre-upgrade-freeze.txt
cp -a "$XDG_CONFIG_HOME/mlx-tui" ./pre-upgrade-config
cp -a "$XDG_STATE_HOME/mlx-tui" ./pre-upgrade-state
```

Use the current XDG roots when they are set; otherwise use `~/.config` and
`~/.local/state`. Do not copy the Python tool environment or the HF cache.
Verify candidate bytes, replace the tool explicitly, run
`mlx-tui --diagnostics /absolute/path/report.json`, and reopen saved sessions,
comparisons, choices, and attachments before treating the candidate as usable.
Keep the old wheel, freeze output, and backups until that check passes.

The isolated implementation exercise is recorded in [the pre-F manifest](compatibility/evidence/milestone-f/upgrade-pre-f-0.2.0-20260915/manifest.json)
and [the legacy manifest](compatibility/evidence/milestone-f/upgrade-legacy-0.1.0-20260915/manifest.json).
Its exact invocations were:

```sh
rtk proxy uv run python tests/artifact_smoke.py \
  --retain-dir docs/compatibility/evidence/milestone-f/upgrade-pre-f-0.2.0-20260915 \
  --upgrade-from docs/compatibility/evidence/milestone-f/pre-f-0.2.0-development/mlx_tui-0.2.0-py3-none-any.whl \
  --upgrade-from-sha256 27e39d77ce5bac6dcaf1979c52fb2f767b930b2181da00568de333d4b2d66b9c
rtk proxy uv run python tests/artifact_smoke.py \
  --retain-dir docs/compatibility/evidence/milestone-f/upgrade-legacy-0.1.0-20260915 \
  --upgrade-from dist/mlx_tui-0.1.0-py3-none-any.whl \
  --upgrade-from-sha256 226aae53187aa6573120f8bd644a8969d16465ba2981a990c166a9d8a5b99b03
```

Rollback is separate from upgrade. Stop the candidate and its owned child,
make a separate backup of post-upgrade work, reinstall the retained predecessor
using its recorded dependency freeze, and restore the pre-upgrade backup only
by explicit operator action. Never merge schemas or delete newer work
automatically. The 0.1.0 wheel restores attach/config functionality only; it
cannot reopen sessions or run managed mode.

## Private diagnostics

If startup is unavailable, write a local support snapshot without starting the
TUI or contacting an endpoint:

```sh
mlx-tui --diagnostics /absolute/path/to/mlx-tui-diagnostics.json
```

It records schema/time, installed TUI and allowlisted dependency versions,
Python and OS/architecture, total RAM, packaged expected runtime pins and
resource hashes, config presence/parse status and configured attach/managed
mode. Completion-marker versions and hashes are labelled `recorded, not
verified now`; they are not a current readiness result. Missing/corrupt data
uses fixed status codes and unknown fields.

The report excludes hostnames, URLs, paths, usernames, environment/proxy
values, direct package URLs, logs, prompts, drafts, answers, reasoning, tool
arguments, attachments, titles and comparison contents. It refuses overwrites
and symlink targets, writes mode `0600`, and removes a partial file on failure.
Review it locally, share it only after removing anything you do not intend to
share, and delete it when support no longer needs it. There is no automatic
upload or persistent diagnostics log.

## Final installed qualification

The live F contract must run from the candidate wheel's isolated interpreter,
outside the checkout and with `PYTHONPATH` unset. Copy
`tests/runtime/test_milestone_f.py` to the isolated work directory and provide
the exact artifact hash, test-owned verified runtime root, pinned model
snapshot, lowercase machine-tier slug, and fresh absolute output directory:

```sh
MLX_TUI_F_QUALIFY=1 \
MLX_TUI_F_ARTIFACT=/absolute/candidate/mlx_tui-0.3.0-py3-none-any.whl \
MLX_TUI_F_ARTIFACT_SHA256=<lowercase-sha256> \
MLX_TUI_F_RUNTIME_ROOT=/absolute/test-owned/runtime \
MLX_TUI_F_MODEL_PATH=/absolute/pinned/hf/snapshot \
MLX_TUI_F_MACHINE_TIER=local-m4-16gib \
MLX_TUI_F_OUTPUT=/absolute/fresh/evidence \
  /absolute/candidate-python -m pytest -q /absolute/copied/test_milestone_f.py
```

No opt-in skips. Opted-in missing or invalid inputs fail. The contract checks
the installed wheel hash/version, package import location, pinned host/runtime
and model assets, a fixed coding request, client cancellation and recovery,
session/attachment reopen, and test-owned process exit/port release before
start, while idle, and during generation. It does not install or download
anything; `HF_HUB_OFFLINE=1` alone is not offline qualification. Its
`metadata.json` and `results.json` are evidence records, not release approval.

## Managed runtime inputs

Managed setup uses the packaged `managed-runtime.txt` freeze and
`managed-build-constraints.txt` at the app-owned final path:

```text
$XDG_DATA_HOME/mlx-tui/runtimes/74e7cf9-py3131/
~/.local/share/mlx-tui/runtimes/74e7cf9-py3131/
```

The runtime is MLX-LM 0.32.0 from commit
`74e7cf931e84ef7c2f63e875adf414e20decc1c5`, MLX 0.32.2, and MLX-Metal 0.32.2.
Git is required because MLX-LM is installed from that immutable source commit.
The supported implementation input is Darwin arm64 on macOS 26.6.2 with
Python 3.13.1 and uv 0.12.7. Other OS/version combinations are untested and
remain excluded from C recruitment.

Installation uses the target interpreter explicitly and does not fall back to
an active virtual environment:

```text
uv --no-config venv --allow-existing --python 3.13.1 <runtime-root>
uv --no-config pip sync --python <runtime-root>/bin/python --strict \
  --build-constraints <packaged-build-constraints> <packaged-runtime-freeze>
```

The app records the actual freeze, package metadata, direct URL commit,
resource hashes, and install provenance only after inspection passes. A
cancelled or failed install is incomplete and can be repaired under the same
app-owned lock.

The runtime directory identity is part of the pin. This candidate keeps
`74e7cf9-py3131` unchanged. Any future runtime commit, Python version, resolved
freeze, or build-constraint change must use a distinct directory name; a TUI
upgrade never syncs new requirements into a live or predecessor runtime.

Managed runtime installation does not download models. Model downloads and
their elapsed time are recorded separately by the later setup flow. No live
Milestone B qualification or C recommendation is implied by this artifact.

## First-run setup and ownership

Run `mlx-tui` from any directory. With no config file, the keyboard-first setup
screen offers **Managed runtime** and **Attach server**. Managed mode is the
explicit opt-in path; `--managed` and `--attach` are mutually exclusive CLI
shortcuts. Existing config files remain untouched and keep attach mode unless
their `runtime_mode` is changed or a flag is supplied.

Managed setup installs only under the versioned runtime directory above. It
does not use `start_cmd` or `stop_cmd`, does not adopt a discovered process,
and starts the verified runtime with the fixed C endpoint
`127.0.0.1:18080`. Model downloads remain separate from runtime installation.
Only complete snapshots (tokenizer/config files and all indexed or standalone
Safetensors weights) can be started offline; partial caches produce a repair or
download action. Remote model code is not enabled.

The manager retains the exact child PID/create-time, argv, runtime inspection,
and sanitized launch environment needed for comparison evidence. A green
endpoint from another process is never accepted as managed readiness. Port
conflicts, identity changes, failed loads, and cancellation stay visible as
failure or unknown state; an owned child is stopped and reaped on ordinary
quit, while an attached process is left alone. SIGKILL and power loss cannot
run Python cleanup and are outside this guarantee.

After a failed model switch, **Reload previous model** is an explicit recovery
action. The prior verified path is retained as a recovery option, but a failed
activation is never presented as a successful rollback. Once the runtime and
both pinned snapshots are verified, **Open Compare** enters the existing fixed
`coding-check-v1` workflow; see [the comparison guide](comparison.md).
