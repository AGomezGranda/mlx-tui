# mlx-tui

One-screen TUI control plane for a local MLX server (`mlx_lm.server` / `mlx_vlm.server`).

A collapsed Activity disclosure keeps server output, download progress and notices available with `F2`. Its summary retains the last unseen error or warning until opened; incoming events do not steal focus. A one-row Footer shows contextual keyboard actions. The TUI talks to the server over plain local HTTP (`http://host:port` with no TLS, API key, or reverse-proxy base-path configuration).

The current Milestone F candidate is `mlx-tui` 0.3.0. It is packaging-tested
only; managed-runtime support remains limited to the explicitly pinned and
qualified matrix recorded in the [Milestone F support record](docs/compatibility/milestone-f.md).

## Features

**Status bar (always visible)** — state word plus color (Ready/Unexpected/Offline/Unknown), selected request target, last response evidence with request-scoped generation state, sampled process RSS in GiB and available/total GiB, port. Catalogue entries never imply residency; RSS is process memory, not model-exclusive. When the server is down and a start command is configured it hints `ctrl+s to start`.

**Models tab** — every model in your HF cache with a name-derived quantization hint, size on disk in GiB, explicit `runtime fit: unknown`, and a selected marker for the request target. Pinned coding candidates show their dated evidence; direct repository/path selection remains unqualified. Load/swap with `enter`, delete with `d`, search `mlx-community` with `/` — all without leaving the terminal.

**Compare tab** — set up two pinned coding profiles, check readiness, run the fixed `coding-check-v1` experiment, inspect evidence, and explicitly keep or reuse a choice. See the [comparison guide](docs/comparison.md). The workflow is local and stub-testable; it does not establish general coding ability or live Milestone B qualification.

**Chat tab** — streaming answers grow below their prompts in one scrollable transcript and stay in place at completion. Scroll up to read earlier output; reaching the bottom resumes following. The composer stays anchored. Each turn is stamped with prompt/output tokens, first-output/answer/total latency and client request tok/s (completion tokens over full request time, not engine decode speed); client-estimated counts carry `~`, unknowns show `—`. Reasoning renders separately, tool data stays unexecuted, and length-capped/damaged/tool-only/empty outcomes display without entering future history. `esc` requests client-side cancellation of an in-flight turn; engine state stays unknown.

**Search & download** — modal HF search (`mlx-community`, 50 results) with quant and free-disk download indicator. Downloads stream progress and resume on retry; on success the Models table rescans automatically.

**Metrics tab** — view the last 64 chat turns plus chat-throughput and server-memory sparklines. It does not own comparison setup or decisions.

## Requirements

* Python 3.13.1 (`.python-version`) — `uv` manages the toolchain
* [uv](https://docs.astral.sh/uv/) 0.12.7 for the pinned managed runtime
* Managed qualification target: Darwin arm64, macOS 26.6.2, Python 3.13.1 and uv 0.12.7; the 0.3.0 candidate has not established newer-OS support
* An MLX server for real use (optional for dev — tests use an ephemeral stub)

The managed runtime is installed separately from the TUI and requires Git to
build the pinned MLX-LM source. Attach mode remains the default.

## Quick start

Start a server first:

```
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
```

Run the TUI against it:

```
uv sync
uv run mlx-tui --port 8080
# host defaults to 127.0.0.1; explicit CLI flags win over the config file
uv run mlx-tui --host 127.0.0.1 --port 8080
uv run mlx-tui --help
```

To install the versioned TUI wheel outside a checkout, verify the delivered
SHA-256 first, then run:

```sh
shasum -a 256 mlx_tui-0.3.0-py3-none-any.whl
uv tool install --python 3.13.1 /absolute/path/to/mlx_tui-0.3.0-py3-none-any.whl
mlx-tui --help
mlx-tui --version
```

Compare the SHA-256 with the retained artifact record in
`docs/compatibility/milestone-f.md` before installation. This is a manual
candidate installation; it does not establish managed runtime or release
support.

TUI installation time, managed-runtime installation time, and model download
time are separate measurements. Installing the wheel does not download MLX or
models.

## Private diagnostics

When startup itself is unavailable, export an explicit local support snapshot
from any directory:

```sh
mlx-tui --diagnostics /absolute/path/to/mlx-tui-diagnostics.json
```

The JSON contains schema/time, installed TUI and allowlisted dependency
versions, Python and OS/architecture, total RAM, the packaged managed-runtime
pins/resource hashes, a config presence/parse status plus attach/managed mode,
and allowlisted completion-marker values labelled `recorded, not verified now`.
Missing or malformed metadata is represented by fixed status codes and unknown
fields. The command does not start the TUI, contact a server, run uv or other
subprocesses, or inspect sessions/model cache content.

It omits hostnames, URLs, local paths, usernames, environment values, direct
package URLs, logs, prompts, drafts, answers, reasoning, tool data,
attachments, titles, comparisons and exception details. Review the file
locally, share it only after that review, then delete it when no longer needed.
The export refuses overwrite and symlink targets and is written with private
permissions; it is not a readiness or release qualification report.

## Final installed qualification

The retained wheel has an opt-in real-runtime contract. Copy
`tests/runtime/test_milestone_f.py` outside the checkout, install the candidate
wheel into an isolated environment, and run it from an isolated working
directory with `PYTHONPATH` unset:

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

The contract verifies the installed distribution identity, pinned
runtime/profile, managed child ownership, chat/cancel/recovery, saved session
attachments, and before-start/idle/during-generation cleanup. It never installs
packages or downloads models. A skipped contract is not evidence; missing or
invalid opt-in inputs fail. The output contains only allowlisted identities,
versions, timings, process identity and cleanup outcomes.

No server yet and `start_cmd` configured? A red dot shows `ctrl+s to start` — see [Configuration](#configuration).

## Key bindings

| Key | Context | Action |
|-----|---------|--------|
| `enter` | Models — row selected | Load / swap to the selected model |
| `d` | Models | Delete the selected repo from the HF cache (with confirm) |
| `/` | Models | Search `mlx-community` |
| `enter` | Chat composer | Insert a newline |
| `ctrl+enter` | Chat composer | Send the message once |
| `tab` | Chat composer | Move focus; paste never submits |
| `enter` | Params field | Normalize the value without sending |
| `F2` | Main shell | Expand/collapse Activity without leaving the current control |
| `ctrl+s` | Any (red dot + `start_cmd`) | Cold start the server; output streams into Activity |
| `ctrl+g` | Any | Open `~/.config/mlx-tui/config.toml` in `$EDITOR`, reload on save |
| `ctrl+n` | Any | Cycle presets forward |
| `ctrl+o` | Any | Cycle presets back |
| `esc` | Any | Cancel in-flight chat/comparison / close search / cancel download |
| `ctrl+q` | Any | Quit |

Tab switching between Models, Compare, Chat and Metrics works by click or arrow keys.

With no config file, `mlx-tui` opens a keyboard-first setup screen. Choose
**Managed runtime** to install the pinned app-owned runtime and start complete
offline snapshots, or **Attach server** to keep an existing operator-managed
server. The CLI equivalents are `--managed` and `--attach` (mutually exclusive).
Managed mode owns only the child it starts at `127.0.0.1:18080` and stops it on
quit; it never adopts a discovered listener. An attached process is never
stopped by the TUI.

## Configuration

All config is optional; CLI `--host`/`--port` win when explicitly passed. Config lives at `~/.config/mlx-tui/config.toml` (honours `$XDG_CONFIG_HOME`).

```toml
runtime_mode = "attach"  # "attach" (default) | "managed"
model = "mlx-community/Qwen3-1.7B-4bit"   # seeds cold start target
host = "127.0.0.1"
port = 8080
start_cmd = "mlx_lm.server --port 8080"   # argv mode: {model} or exact --model managed, else --model appended
stop_cmd = "pkill -f mlx_lm.server"
command_shell = false  # true: run start_cmd/stop_cmd via shell with $MLX_TUI_MODEL
# shell example: command_shell = true
# start_cmd = "mlx_lm.server --model \"$MLX_TUI_MODEL\" --port 8080"
temperature = 0.7
top_p = 1.0
max_tokens = 1024
seed = 7                    # optional; omitted when not configured
enable_thinking = false     # optional; omitted when not configured
system = "You are a helpful assistant."
swap_policy = "auto"  # "auto" | "warm" | "restart"
```

`runtime_mode = "managed"` opts into the app-owned pinned runtime at
`$XDG_DATA_HOME/mlx-tui/runtimes/74e7cf9-py3131/` (or
`~/.local/share/mlx-tui/runtimes/74e7cf9-py3131/`). Managed setup requires
Darwin arm64, macOS 26.6.2, Python 3.13.1, uv 0.12.7, Git, writable app data,
and free disk space. Invalid or unknown mode values stay in attach mode;
managed installation never uses the active operator virtual environment.

Managed startup verifies the runtime marker and complete cached assets before
spawning MLX-LM. It rejects unknown port ownership, partial snapshots, and
implicit remote model code; a failed switch retains the previous verified path
for the explicit **Reload previous model** action. Runtime installation and
model-download time are recorded separately.

Commands run as argv by default: no shell expansion, `;`, `$()`, and quotes stay literal argument data. Set `command_shell = true` to opt into shell execution; shell commands must reference `"$MLX_TUI_MODEL"` instead of `{model}` and receive the exact target in the environment.

`max_ctx` is the context budget and `max_context` is its compatibility alias. If both are present, `max_context` wins. The request reserves the configured `max_tokens` allowance plus protocol/system overhead before trimming complete user-bound turns; an over-sized request is rejected with a visible notice instead of being sent.

With `swap_policy = "auto"` (the default), model swaps restart the server and stream output when both `start_cmd` **and** `stop_cmd` are configured — even when the endpoint is green — otherwise `auto` uses a warm in-server load against a healthy endpoint. `swap_policy = "warm"` always uses warm load and requires a green endpoint; `swap_policy = "restart"` always restarts and requires both `start_cmd` and `stop_cmd`. A warm load records explicit selection first and only reports request-scoped success when the response identity matches the target (or a follow-up `/v1/models` probe reports it); residency stays unknown and the catalogue never selects by itself. Discovery never authorizes automatic ownership of an attached server. `ctrl+g` creates a commented template if missing and reloads on save — host/port changes need a TUI restart.

Chat history is transactional: the attempted user message remains visible in the transcript, but a user/assistant pair enters the next request's context only after a successful response. Failed transport and cancelled attempts keep any displayed partial answer with “Not included in next request” and no successful measurement stamp. They are excluded from future context. Length-capped, tool-only, empty and incomplete outcomes stay visible but neither side enters future history, so a retry resends the same context.

Failure, context rejection, or cancellation restores the original draft, including surrounding whitespace, into an empty multiline composer. Edit it and press `ctrl+enter` or the visible Send button to retry explicitly; Enter inserts a newline, and paste never submits. Retry restores the latest failed draft for review without sending, and asks before replacing a newer draft. Answers and partial answers have View/copy; Copy all preserves exact text, while Copy selection copies the selected text without stripping code fences. A newer draft is never overwritten. Cleanup does not move focus back from another pane, control, or modal.

**Sessions** — ordinary chat sessions persist locally as one versioned JSON snapshot per session in `$XDG_STATE_HOME/mlx-tui/sessions` (or `~/.local/state/mlx-tui/sessions`); a file is created only after a draft or attempt exists. The Chat tab offers Sessions (explicit picker, newest first), New, New temporary, Clear and Delete. The save indicator shows `Saving…`, `Saved locally`, `Save failed — retry save`, or `Temporary`. Drafts checkpoint ~250 ms after typing and attempts checkpoint at most once per second plus immediately at submit, terminal outcome, switch, clear and shutdown. A failed save keeps the completed reply visible but blocks further sending, clearing or switching until Retry save succeeds or the work is explicitly discarded; Retry save never sends an HTTP request. Reopening a session never sends a request or adds metrics: the transcript renders from saved attempts, successful pairs rebuild the request context, and an interrupted attempt shows `Interrupted — not included in next request`. If saved request settings differ, Send stays blocked until you choose Use saved request settings or Continue with current settings; restoring saved settings merges request fields only (never endpoint credentials, commands or process ownership) and revalidates known cached revisions/profile fingerprints first. `New temporary` never writes drafts, output or checkpoints; leaving it with work requires explicit discard. Limits: local single-file snapshots only (no sync, search or branching); only acknowledged `Saved locally` checkpoints survive a restart or SIGKILL/power loss; restoring a conversation does not restore a warm KV cache or prove model readiness.

**Attachments** — explicitly selected regular UTF-8 text files are captured as immutable snapshots, each up to 256 KiB, with a 1 MiB aggregate limit enforced independently for the draft and every saved attempt. The snapshot retains the selected/resolved paths, exact bytes, length and SHA-256, so later source edits or deletion do not change preview, retry or restored requests; re-adding a changed file creates a new snapshot. On macOS, Add file… opens the native file chooser. Preview shows the destination, estimated input/reserved output, excluded message count and the exact retained messages/file blocks. Estimates are character-based and do not claim tokenizer or model-limit accuracy. Attachments are sent only to loopback destinations; temporary-session snapshots stay in memory, Clear/Delete removes draft snapshots, and earlier attempt snapshots remain part of the transcript record.

The shipped Milestone D subset is sessions, multiline/copy/retry, and selected
text-file snapshots. Its [evidence record](docs/compatibility/milestone-d.md)
keeps product acceptance deferred until consented repeat-use and real-runtime
recovery observations exist; automated checks are not adoption evidence.

External clients are qualification only; shared serving is blocked. The
[client guide](docs/clients.md) defines the unenforceable wrong-target and
lifecycle gate on stock upstream `74e7cf9`, and the
[Milestone E evidence record](docs/compatibility/milestone-e.md) retains the
implemented-but-unrun HTTP/OpenCode qualification suite plus the missing
tool, cancellation, and reuse facts.
A completed implementation plan substitutes for none of the full E gate:
enforcement plus repeated workloads plus observed target-user reuse.

Escape during chat shows “cancellation requested” until the HTTPX request has cleaned up. This is a client-side cancellation and does not claim that the server stopped generating. Download Escape follows the same honest model: the modal remains open until the Hub worker acknowledges cancellation, then it rescans completed cache content and closes.

**Params** — Chat starts with a one-line summary of normalized next-request temperature, top-p and max tokens. Open it by click or Enter on its title, then Tab through the three inputs (Temperature 0–2, Top-p 0–1, Max tokens 1–16384). The expanded fields scroll within a bounded area. Partially typed values stay editable; Enter normalizes a field without sending chat.

**Compare and reuse** — open the Compare tab and follow the [comparison guide](docs/comparison.md) for the complete setup → readiness → run → review → choose → reuse journey. The runner sends the first request plus five repeats for A, then B, without adding synthetic requests to Chat. Download, Start server, Keep, Apply saved profile, and Open result are explicit actions; `esc` cancels the client request, retains the latest checkpoint, and leaves engine state unknown. Metrics remains history-only.

**Presets** — flat sibling file `~/.config/mlx-tui/presets.toml`:

```toml
[[preset]]
name = "default"
system = "You are helpful."
temperature = 0.7
```

`ctrl+n` / `ctrl+o` cycles presets; `ctrl+g` reloads them.

## Develop locally

```bash
git clone <repo>
cd mlx-tui
uv sync --dev          # creates .venv, installs deps
uv run mlx-tui --help
uv run mlx-tui --port 8080

# real server in another terminal
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
```

Tests / lint / typecheck — same gates as CI:

```bash
uv sync --locked --dev
uv run ruff check .
uv run ruff format --check src tests
uv run pyrefly check
uv run pytest -q
```

## Notes & scope

* Local MLX only by design — one cache layout, one memory model, plain HTTP to `host:port` with no TLS, API key, or base-path support; remote/secure servers are not supported. A non-loopback host is best-effort unauthenticated HTTP, not a secure remote-server feature.
* No quantization runner, no multi-backend abstraction, no server-log tailing; conversation persistence is local single-file snapshots only (no sync, search or branching).
* Presets are file + `$EDITOR` only — no management UI.
* Compatibility evidence: [docs/compatibility/milestone-a.md](docs/compatibility/milestone-a.md) pins MLX-LM `74e7cf9` with Qwen3-1.7B-4bit on `local-m4-16gib`. Unit/integration tests use an HTTP stub and cannot prove real-server behavior; real-server contracts in `tests/runtime` are opt-in and skip without `MLX_TUI_CONTRACT_URL`/`MLX_TUI_CONTRACT_MODEL`.

## Architecture

Implementation details, polling/timeouts, swap state machine, SSE parsing, storage and project layout: see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

See the [MIT license](LICENSE).
