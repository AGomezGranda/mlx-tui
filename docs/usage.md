# Using the TUI

The screen has **Models**, **Compare**, **Chat**, and **Metrics** tabs. Switch
with a click or the tab bar's arrow keys. The status bar shows endpoint state,
the selected request target, last response evidence, process RSS, available
memory, and port. A cache entry does not prove that a model is resident; RSS
is process memory, not memory used by one model.

## Models, search, and Activity

**Models** lists repositories in the Hugging Face cache with their disk size
and a name-derived quantization hint. Runtime fit is unknown. Select a row and
press `Enter` to load or switch the request target; press `d` to delete the
selected cached repository after confirmation. Press `/` to search
`mlx-community` and download a model. Search displays up to 50 results with
quantization and free-disk indicators. Downloads show progress, resume on
retry, and trigger a cache rescan on success.

Press `F2` to expand **Activity** for server output, downloads, and notices.
Its collapsed summary retains the last unseen warning or error. Incoming
events do not take focus. Press `F3` for an endpoint preview with URLs, model
names, and request examples; see [Local clients](clients.md).

**Metrics** shows the last 64 chat turns with throughput and server-memory
sparklines. **Compare** has its own [workflow guide](comparison.md).

## Chat

Type in the multiline composer. `Enter` inserts a newline, `Ctrl+Enter` sends
once, and paste does not submit. `Tab` moves focus. Responses stream below
their prompts in one scrollable transcript; scrolling to the bottom resumes
following new output. Answers and partial answers offer View/copy. Copy all
preserves exact text; Copy selection preserves the selected text, including
code fences.

The **Params** section summarizes the next request's temperature, top-p, and
maximum tokens. Open it by click or `Enter` on its title, then tab through its
inputs. Accepted ranges are temperature 0–2, top-p 0–1, and maximum tokens
1–16384. `Enter` in a field normalizes it without sending chat.

Each completed turn can show token counts, first-output/answer/total latency,
and client request throughput. A `~` marks client-estimated counts; `—` means
unknown. Throughput divides completion tokens by full request time, so it is
not engine decode speed. Reasoning is displayed separately; tool data is
never executed.

Only a successful user/assistant pair enters the next request's history.
Failed, cancelled, length-capped, tool-only, empty, and incomplete attempts
stay visible but are excluded from future context. The original draft is
restored after failure, context rejection, or cancellation so you can edit and
retry explicitly. A newer draft is never overwritten without asking. `Esc`
requests client-side cancellation; it does not establish that the server
stopped generating. The cancellation notice remains until the HTTP request
has cleaned up.

The request reserves the configured `max_tokens` allowance and protocol/system
overhead before trimming complete user-bound turns. An oversized request is
rejected visibly rather than sent. See [Configuration](configuration.md) for
the context budget.

## Sessions and attachments

Ordinary chat sessions are local versioned JSON snapshots in
`$XDG_STATE_HOME/mlx-tui/sessions/` (default
`~/.local/state/mlx-tui/sessions/`). A file appears only after a draft or
attempt exists. In **Chat**, use Sessions to reopen one, New to create one,
New temporary for an in-memory conversation, or Clear/Delete for the current
content. Reopening does not send a request or add metrics. Only checkpoints
acknowledged as **Saved locally** survive a restart or abrupt shutdown.

The save indicator distinguishes **Saving…**, **Saved locally**,
**Save failed — retry save**, and **Temporary**. Drafts checkpoint shortly
after typing; attempts checkpoint during the response and at important
transitions. If saving fails, the reply stays visible but sending and session
changes pause until Retry save succeeds or work is explicitly discarded.
Retry save never sends an HTTP request. A temporary session writes no draft,
output, or checkpoint; leaving it with work requires explicit discard.

If a reopened session's saved request settings differ, choose **Use saved
request settings** or **Continue with current settings** before sending. Saved
settings restore request fields only, never endpoint credentials, commands, or
process ownership. An interrupted attempt remains visible but is excluded
from future history. Restoring a session does not restore a warm KV cache or
prove model readiness. Storage is local, with no sync, search, or branching.

**Add file…** selects a regular UTF-8 text file and captures an immutable
snapshot. Each file is limited to 256 KiB, with a 1 MiB aggregate limit for
the draft and for each saved attempt. Later edits or deletion of the source
file do not alter preview, retry, or restored requests. Preview shows the
destination, estimated input and reserved output, excluded messages, and the
exact retained messages and file blocks. Estimates are character-based and
are not tokenizer-accurate. Attachments are sent only to loopback endpoints.
Temporary-session snapshots stay in memory.

## Keyboard reference

| Key | Context | Action |
| --- | --- | --- |
| `Enter` | Models row | Load or swap to the selected model |
| `d` | Models | Delete the selected cached repository, with confirmation |
| `/` | Models | Search `mlx-community` |
| `Enter` | Chat composer | Insert a newline |
| `Ctrl+Enter` | Chat composer | Send the message |
| `Tab` | Chat composer or controls | Move focus |
| `Enter` | Params field | Normalize the value without sending |
| `F2` | Main screen | Expand or collapse Activity |
| `F3` | Main screen | Show the endpoint preview |
| `Ctrl+S` | Server startup available | Start the server |
| `Ctrl+G` | Main screen | Open config in `$EDITOR` and reload on save |
| `Ctrl+N` / `Ctrl+O` | Main screen | Cycle presets forward / back |
| `Esc` | Active operation | Request cancellation or close the search modal |
| `Ctrl+Q` | Main screen | Quit |

Download cancellation waits for the worker to acknowledge it, then rescans
completed cache content. See [Configuration](configuration.md) for cold-start
commands and presets.
