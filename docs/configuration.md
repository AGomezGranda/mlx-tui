# Configuration

Configuration is optional. The file is
`$XDG_CONFIG_HOME/mlx-tui/config.toml`, or
`~/.config/mlx-tui/config.toml` when `XDG_CONFIG_HOME` is unset or empty.
Find that location without creating anything or starting the TUI:

```sh
mlx-tui --config-path
```

This flag cannot be combined with diagnostics, mode, host, or port flags.
Installation, `--help`, `--version`, and `--config-path` do not create config.
Diagnostics reads configuration but does not create or migrate it.

## Settings and active work

Run `mlx-tui` after installing. On a fresh launch, Settings opens before any
connection polling or managed-runtime initialization. Choose a mode and
**Save**, then select/download a model and start it from Models or Runtime
setup. **Continue defaults** or Escape continues once in attach mode without
creating the file. Explicit `--attach`/`--managed` flags bypass first-run
onboarding for that run; `Ctrl+G` remains available afterward.

Press `Ctrl+G` from the main screen to open the same Settings form later.
It shows saved values, the active endpoint, CLI overrides, a form endpoint
preview, and any pending restart. Host and port are attach settings; they
are disabled while Managed is selected. **Runtime setup** remains available
for installation, downloads, and recovery.

| Action | Effect |
| --- | --- |
| Save | Writes only edited fields, updates saved defaults and presets, and imports legacy presets if needed. Active chat controls, draft, attachments, historical requests, and runtime ownership stay unchanged. |
| Apply chat defaults | Copies the saved model, system prompt, temperature, top-p, max tokens, context, seed, and thinking choice into subsequent requests. It sends no request and marks an active coding profile modified. Unsaved form edits are not applied. |
| Reload / return from Open TOML | Reads and validates the file, refreshes saved defaults/presets and the restart notice, and preserves active request controls. |
| Cancel / Escape | Closes without saving unsaved edits; completed Saves remain saved. Dirty edits require discard confirmation, also before Reload or Open TOML. |

Apply is unavailable during chat, comparison, installation, or model operations,
and while a session is read-only or awaits a saved/current settings choice.
You can still edit and Save defaults. Clearing the saved model and applying
clears explicit selection; select a model before sending again. Apply does
not stop a server or establish which model is resident.

New chat inherits the current request controls. Reopening a saved chat retains
its explicit saved-versus-current settings choice. Neither action writes
defaults back to `config.toml`.

Runtime mode, host, port, start/stop commands, shell execution, and swap policy
take effect at the next launch. A **Restart required** notice stays visible
while saved effective runtime settings differ from the launch snapshot.
Reverting those changes clears it. Editing a field masked by a CLI override
does not require restart for that field; overrides last for this process.
Saving never reclassifies an owned managed child as attached or reconnects
the existing HTTP client.

## Fields and defaults

The form edits Server and Chat defaults. Commands, shell execution, swap policy,
and preset definitions are edited through **Open TOML**. Optional blank fields
remove their keys; merely opening or saving an unchanged form does not write
every effective default. Zero and false remain explicit values.

| Key | Unset/default behavior | Accepted values / control |
| --- | --- | --- |
| `runtime_mode` | `"attach"` | `"attach"` or `"managed"`; form |
| `host` | `"127.0.0.1"` for attach | Nonempty string; form |
| `port` | `8080` for attach | Integer 1–65535; form |
| `model` | No explicit request selection | Model identifier or local path; form |
| `system` | No system prompt | String; multiline form |
| `temperature` | `0.7` | Number 0–2; form |
| `top_p` | `1.0` | Number 0–1; form |
| `max_tokens` | `1024` | Integer 1–16384; form |
| `max_ctx` | `8192` | Integer 1024–131072; form |
| `max_context` | Compatibility alias, takes precedence over `max_ctx` | Same context range; TOML. Editing context in the form writes `max_ctx` and removes the alias. |
| `seed` | Provider default | Optional integer; form |
| `enable_thinking` | Provider default | Optional boolean; form Default removes the key, On/Off writes true/false |
| `start_cmd` | No attach startup command | Optional string; TOML or the explicit local-start checkbox |
| `stop_cmd` | No attach stop command | Optional string; TOML |
| `command_shell` | `false` | Boolean; TOML |
| `swap_policy` | `"auto"` | `"auto"`, `"warm"`, or `"restart"`; TOML |
| `preset` | Canonical entries if present; otherwise legacy fallback | Array of preset tables, including `[]`; TOML |

**Enable local server start** creates an argv command using the form's literal
loopback host and port, with `command_shell = false`. Fresh attach onboarding
selects it by default. Existing custom commands stay unchanged and appear as
advanced settings; when changing endpoints, update their arguments in TOML
as needed. The checkbox is unavailable for nonliteral/nonloopback hosts,
existing commands, and Managed mode.

### Endpoint and CLI precedence

`--attach` and `--managed` are mutually exclusive and override the saved mode
for this run. Explicit endpoint flags override only the supplied fields:

| Effective mode | No endpoint flags | Example override |
| --- | --- | --- |
| Attach | Saved host/port, default `127.0.0.1:8080` | `--port 9000` retains the saved host |
| Managed | `127.0.0.1:18080`; saved host/port remain attach settings | `--managed --port 8080` uses port 8080 explicitly |

CLI overrides are never saved. They continue to affect the effective endpoint
and pending-restart comparison after Save, Reload, and editor changes.

## Example

```toml
runtime_mode = "attach"  # "attach" (default) or "managed"
model = "mlx-community/Qwen3-1.7B-4bit"  # cold-start target
host = "127.0.0.1"
port = 8080
start_cmd = "mlx_lm.server --port 8080"
stop_cmd = "pkill -f mlx_lm.server"
command_shell = false
temperature = 0.7
top_p = 1.0
max_tokens = 1024
seed = 7
enable_thinking = false
system = "You are a helpful assistant."
max_ctx = 8192
swap_policy = "auto"  # "auto", "warm", or "restart"
```

`max_ctx` is the context budget. `max_context` is a compatibility alias; if
both are present, `max_context` wins. Invalid known values open Settings for
repair before connecting. CLI `--managed` and `--attach` select a mode for that
run and are mutually exclusive.

## Server ownership and model switching

**Attach** uses an operator-managed server. The TUI does not stop that server
when it quits. If the endpoint is down and `start_cmd` is configured, the
status bar offers `Ctrl+S` to start it. **Managed** uses the app-owned pinned
runtime at `127.0.0.1:18080`; it owns only the child it started and stops
that child on normal quit. It never adopts a discovered listener. See
[Managed activation](activation.md) for the exact platform requirements,
installation, and recovery behavior.

Commands run as argument vectors by default. Shell metacharacters such as
`;` and `$()` are not executed; quotes only group arguments while parsing.
For `start_cmd`, use
`{model}` or an exact `--model` argument; otherwise the TUI appends `--model`
with the chosen target. Set `command_shell = true` only when you intend shell
execution. In that mode, reference `"$MLX_TUI_MODEL"` instead of `{model}`:

```toml
command_shell = true
start_cmd = "mlx_lm.server --model \"$MLX_TUI_MODEL\" --port 8080"
```

`swap_policy = "auto"` restarts the server and streams its output if both
`start_cmd` and `stop_cmd` are configured, even when the endpoint is healthy.
Otherwise it attempts a warm load against a healthy endpoint. `"warm"`
always requires a healthy endpoint. `"restart"` always requires both
commands. A warm load first records the explicit selection; request-scoped
success requires matching response identity or a follow-up `/v1/models`
probe. Neither the cache catalogue nor a healthy endpoint proves residency.
Server discovery does not give the TUI ownership of an attached process.

The TUI uses plain HTTP to `host:port`, without TLS, an API key, or a
reverse-proxy base path. A non-loopback host is unauthenticated HTTP, not a
secure remote-server setup. See [Local clients](clients.md) before connecting
another client to the same server.

## Presets

Presets live in `config.toml` as `[[preset]]` entries:

```toml
[[preset]]
name = "default"
system = "You are helpful."
temperature = 0.7
top_p = 1.0
max_tokens = 1024
```

`name` is required and must be nonempty. `system` defaults to an empty prompt;
omitted sampling/token fields retain the current controls when cycling. Their
types and ranges match the corresponding config keys. Unknown preset fields
warn and survive; invalid entries identify their index/name and block Save.

Press `Ctrl+N` / `Ctrl+O` to cycle presets forward / back for this session.
Cycling does not change saved defaults. Use Settings → Reload after editing.

### Legacy import and rollback

When `config.toml` has no `preset` key, the sibling `presets.toml` is a
compatibility fallback. Settings shows **Legacy presets: import on Save**.
Read-only startup, Reload, and diagnostics do not migrate it. The next
successful Save imports the preset array in its original order with names
preserved, leaving the legacy file untouched. If config already exists,
`config.toml.pre-unification.bak` stores its exact pre-import bytes; an
existing backup is never overwritten. Invalid legacy entries must be repaired
before import; changes to either file require Reload before Save.

Once `preset` exists in config, it is authoritative and later changes to the
legacy file have no effect. To opt out, put `preset = []` at the root of
`config.toml` (before any table headers). Settings names the canonical source.

The old app can still read flat config keys and the untouched legacy file.
If rolling back, manually copy presets edited after migration back into
`presets.toml`; the old file does not receive those updates automatically.
Restore the pre-unification backup only by explicit action after preserving
newer work. See [upgrade and rollback](activation.md#upgrade-and-rollback).

## Editing, validation, and write conflicts

**Open TOML** uses `$VISUAL`, then `$EDITOR`, then `vi`. Configure GUI editors
to wait for closing, for example `VISUAL="code --wait"`. A missing file is
created from the standard commented template only after explicit confirmation.
Successful editor exit uses the same validation/reload path as Reload.

Known wrong types, booleans used as numbers, non-finite numbers, invalid enums,
empty hosts, and out-of-range values are errors. Integer TOML values are
accepted for floating fields. Form errors appear beside fields and focus the
first invalid control; strict validation never silently clamps values.
Unknown keys/tables produce warnings and survive writes. Alias use is also
reported so the effective context budget is clear.

Invalid TOML/UTF-8 or unreadable files open a repair state showing the path
and error. Form Save is disabled for an unparseable document; repair through
Open TOML and Reload. Startup defers services until repair succeeds or you
explicitly continue with defaults for this run. Corrupt bytes are not replaced
with defaults.

Saves preserve comments, unrelated keys/tables, and unset optional values.
Formatting of unusual TOML constructs may change. A changed/deleted file,
late file creation, changed legacy presets, or a retargeted symlink rejects
Save and retains the draft: Reload before trying again. App writers share a
short advisory lock; lock contention and write failures remain visible.
Writes use a flushed/fsynced sibling temporary file and atomic replacement;
new files have mode `0600`. Existing symlinks are preserved and their resolved
target is updated. External editors do not honor the lock, so an external
write during the final compare/replace window can still race with Save.
