# Installation and managed activation

The managed runtime is opt-in. Existing configurations remain attach mode;
the operator-owned server commands are not used by managed mode.

Managed setup enforces these implementation constraints: Darwin arm64,
macOS 26.6.2, Python 3.13.1, and uv 0.12.7, plus Git, a writable app data
directory with at least 2 GiB free, and an absolute non-symlinked runtime
path. Other OS/version combinations are rejected by the installer.

## Install the TUI

Install a versioned wheel explicitly and check the installed version:

```sh
uv tool install --python 3.13.1 /absolute/path/to/mlx_tui-<version>-py3-none-any.whl
mlx-tui --version
```

Wheel installation is measured separately from managed-runtime installation
and model downloads. On Apple silicon, the wheel installs MLX-LM and MLX for
attach-mode server startup. It does not download models or install the pinned
managed runtime.

## Upgrade and rollback

Upgrading the TUI and restoring operator data are separate operations.
Ordinary installation never touches backups; restoration only happens by
explicit operator action.

To replace the installed TUI, quit first and wait for the installer, chat,
comparison, and managed child to finish. Then install the new wheel
explicitly as above and run
`mlx-tui --diagnostics /absolute/path/report.json` before treating the new
installation as usable.

Operator backups are a separate manual step. To back up config, presets,
and app state before an upgrade:

```sh
cp -a "${XDG_CONFIG_HOME:-$HOME/.config}/mlx-tui" ./pre-upgrade-config
cp -a "${XDG_STATE_HOME:-$HOME/.local/state}/mlx-tui" ./pre-upgrade-state
```

The commands use the current XDG roots when set and their default locations
otherwise. Do not copy the Python tool environment or the HF cache.
Keep the old wheel and backups until the diagnostics check passes.

Rollback is separate from upgrade. Stop the new installation and its owned child,
make a separate backup of post-upgrade work, reinstall the retained
predecessor wheel, and restore the pre-upgrade backup only by explicit
operator action. Never merge schemas or delete newer work automatically.

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
Only the constrained Darwin arm64 / macOS 26.6.2 / Python 3.13.1 / uv 0.12.7
combination is accepted; anything else is rejected, not merely untested.

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

The runtime directory identity is part of the pin. The current layout keeps
`74e7cf9-py3131` unchanged. Any future runtime commit, Python version, resolved
freeze, or build-constraint change must use a distinct directory name; a TUI
upgrade never syncs new requirements into a live or predecessor runtime.

Managed runtime installation does not download models. Model downloads and
their elapsed time are recorded separately by the later setup flow.

## First-run setup and ownership

Run `mlx-tui` from any directory. With no config file, the keyboard-first setup
screen offers **Managed runtime** and **Attach server**. Managed mode is the
explicit opt-in path; `--managed` and `--attach` are mutually exclusive CLI
shortcuts. Existing config files remain untouched and keep attach mode unless
their `runtime_mode` is changed or a flag is supplied.

Managed setup installs only under the versioned runtime directory above. It
does not use `start_cmd` or `stop_cmd`, does not adopt a discovered process,
and starts the verified runtime with the fixed endpoint
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
