# Architecture

`mlx-tui` is a Textual control plane for a local MLX OpenAI-compatible server
(`mlx_lm.server` or `mlx_vlm.server`). The supported endpoint is plain HTTP at
`host:port`; there is no TLS, API-key, or reverse-proxy base-path configuration.
The entry point is `mlx_tui.app:main`.

## Application ownership

`MlxTuiApp` in `src/mlx_tui/app/__init__.py` is the coordinator. It owns the
polling loop, endpoint/process identity, status bar values, config editing,
preset cycling, cross-pane operation UI, and the shared Activity log.
Activity is a native Collapsible above the contextual Footer. Its closed
container clips the allocated RichLog layout so writes still render; the hidden
log leaves keyboard focus order. Its title summarizes the current operation
and latest event, retaining an unseen red/yellow notice until expansion.
This presentation state does not change operation ownership or error deduplication. The only UI-free
lifecycle primitive is `operations.py`, whose `OperationCoordinator` holds one
lease for a complete chat, comparison, load, restart, or delete worker lifetime. Panes
import this module directly; the UI-free coordinator does not import the app.

The app polls `GET /health` and `GET /v1/models` independently every two
seconds with a short async HTTPX timeout. Health green means the pinned
runtime answered `{"status": "ok"}`; catalogue green means a 200 response
with a non-empty model list. A completed but unexpected response is amber;
transport failure is red. Unexpected polling faults keep the last known
identity and are reported with bounded, per-source diagnostics rather than
being presented as ordinary downtime. Stale polls carry a monotonic sequence
so older in-flight results cannot overwrite newer request evidence.
Polling is skipped while a comparison owns the lease, avoiding competing
health/catalogue/process samples during short trials. Cleanup restores the
pre-run request controls as generation-unknown, releases the lease, and forces
one fresh poll.

Selection is the explicit request target from config or an explicit load, via
`select_model()`; `effective_model()` returns it independently of liveness.
Catalogue entries are availability only and never set selection, response
evidence, or residency — even a singleton list. Generation evidence is
dated and request-scoped (`selected_model`, `last_response_model`,
`last_success_at`, `generation_state` unknown/succeeded/failed/
client_cancelled); endpoint or process-generation change resets it to
unknown, and a missing/mismatched response identity is unverified, not proof
of loading. No selection means chat is refused with an explicit-selection
hint; the TUI never silently sends an unselected request. For loopback
servers, `process.py` can add a verified `ProcessIdentity` by matching
listener address/port plus the `mlx_lm.server`/`mlx_vlm.server` token;
ambiguous matches, uncertain localhost family attribution, and permission
denial are unknown. A pidfile only changes candidate order after listener
and process validation; it is never trusted by itself. Cached identities are
also rejected when the listener or process generation no longer matches.
Model identity is not inferred from an arbitrary command-line `--model`
value. RSS sampling stays observational and is never generation evidence;
process-wide RSS is not attributed as exclusive model memory. The app keeps
a bounded deque of RSS and available-memory samples for the Metrics tab;
`MetricsPane` renders those samples and does not own their collection.

`StatusBar` owns the status and memory widgets. The app supplies explicit
status and memory values while retaining ownership of polling, endpoint
identity, and refresh timing; `ChatPane` owns its context bar.

Managed mode adds one `ManagedRuntime` per app. It retains the actual server
`Popen`, PID/create-time identity, verified current/previous snapshot paths,
argv, sanitized environment, and runtime inspection. The same app-owned lock is
held while the child lives, so repair cannot mutate a live runtime. Spawn,
health, and listener checks all must agree before the app reports managed
readiness. Poll discovery can report an ownership mismatch but cannot replace
the shutdown handle or authorize adoption. `close()` wins pending startup and
stops/reaps only the retained identity; Attach has no such shutdown authority.

## Models and server lifecycle

`ModelsPane` owns cache-table population, selected/runtime-fit markers,
model selection, delete confirmation, warm loading, cold starts, and restart
workers. The table shows GiB sizes, a name-derived quantization hint, an
explicitly unknown runtime-fit field, and a selected column for the explicit
request target. Disk size and process RSS do not establish runtime fit.
The UI-free `boot.py` boundary owns boot-plan execution and
generation-verified success before request-scoped success is recorded;
residency stays unknown. Deleting the selected or last-observed model is
refused both before confirmation and again immediately before cache
mutation, covering a selection/identity race. The domain adapter in
`models.py` uses public `huggingface_hub` cache types to
scan MLX-compatible repositories and to delete exact revision hashes. Deleting
the active model is refused both before confirmation and again immediately
before cache mutation, covering a selection/identity race.

`swap.py` contains pure swap policy and boot-plan decisions. `serverctl.py`
contains command parsing, streaming subprocess helpers, health checks, and
process cleanup. Commands use argv mode by default (`shlex.split`, no shell
expansion). Exact `--model value` and `--model=value` options are replaced or
deduplicated for a selected target; `{model}` is replaced only after parsing.
`command_shell = true` is an explicit opt-in: the target is passed as
`MLX_TUI_MODEL`, and shell templates must reference `"$MLX_TUI_MODEL"`.

Owned start commands use a new POSIX session. A failed start or stop timeout
terminates only that owned process group, escalating from TERM to KILL within
bounded waits and reaping the direct child. A successfully
generation-verified server is left running; quitting closes HTTP but never
claims to stop an attached server. Discovery never authorizes automatic
ownership. UI callbacks and cleanup are guarded so a logging or teardown
error cannot strand the operation lease or leave controls disabled. Explicit
target verification requires a valid generation response whose response
identity matches the target where supplied; a follow-up `/v1/models` probe
can corroborate availability but never proves residency. One bounded timeout
budget covers owned-launch failure cleanup.

## Chat and context

`ParamsPane` owns native collapsible parameter inputs, their normalized title
summary, and config synchronization. Its expanded contents scroll within five
rows. `ChatPane`
owns the transcript, context accounting, async Textual worker, and cancellation
lifecycle. `chat.stream_turn` uses a per-turn
`httpx.AsyncClient` and `async with client.stream`; it closes both client and
response on success, errors, and cancellation. It returns `TurnResult` with
answer text, preserved reasoning text, indexed tool-call fragments
(unexecuted), response model, nullable first-output/answer/total timings from
one pre-request monotonic clock, cached prompt tokens, typed token
accounting, finish reason, malformed-frame counts, and `stream_complete`
while keeping UI callbacks synchronous. Success requires `[DONE]`, zero
skipped frames, finish `stop`, and non-empty answer text; length-capped,
damaged, tool-only, and empty outcomes are incomplete and never enter future
history. First output means first nonempty reasoning/answer/tool output;
answer start means first answer text; no output means null latency.

The single `VerticalScroll#chat-transcript` contains persistent, presentation-only
`ChatTurn` widgets. Each attempt has literal user text, an assistant role, one
Static response body, a trailing measurement stamp and literal notices. The
active body renders cumulative Rich Markdown; the final returned text updates
that same widget even if no preview callback ran. The worker and
`_commit_success()` retain exclusive ownership of request-history insertion.
Following is deferred until layout and coalesced; upward scroll/target changes,
tab activation and resizing invalidate pending follow intent. Params, composer
and context rows remain outside the transcript viewport.

`sse.py` implements incremental server-sent-event framing. `SSEDecoder` joins
consecutive `data` fields, preserves payload whitespace, ignores comments and
unknown fields, and dispatches only at blank lines. Terminals are explicit:
`DONE_SEEN` vs clean `EOF` vs `TRUNCATED`/protocol error, so the transport
sets `stream_complete` without guessing. Pinned `delta.reasoning` is parsed
separately from `delta.content`; indexed OpenAI-shape tool fragments are
preserved without execution. Boolean/negative token counts are rejected
(`type(x) is int`), and inconsistent cached counts are dropped.

Before a request, `history/tokens.py` trims complete user-bound turns and
reserves the configured `max_tokens` allowance plus system/protocol overhead.
`max_context` is an alias for `max_ctx` and wins if both are present. The
pending user message is visible in the transcript but is not committed to
future request context until transport succeeds. Only successful turns commit
the user and non-empty assistant pair together. Length-capped, tool-only,
empty, damaged and incomplete outcomes stay visible with an unsuccessful
notice, no successful stamp, and no insertion into later context, so a retry
resends the same context. Failed transport and cancelled attempts retain any
displayed partial response the same way.

Escape is client-side cancellation. The pane records one cancellation intent,
cancels the retained worker after it starts, and keeps the chat lease until
the async request cleanup finishes. It reports “cancellation requested” and
does not claim that the remote generation stopped. Cleanup is idempotent,
including a worker cancelled before its coroutine starts.

`_pending_draft` captures the original submission after lease acquisition,
while the outgoing prompt remains stripped. `_commit_success()` clears this
marker. `end_turn()`
releases the chat lease before refreshing controls, restores a remaining draft
only into an empty composer, and clears the marker once handled. A different
operation keeps the composer disabled. Focus lost by disabling the composer
is restored only on the active base Chat pane when the reader has not moved
focus elsewhere. Setup failures and unmounted/repeated cleanup use the same
path.

Local sessions live in `sessions.py` as one versioned JSON snapshot per
session under `$XDG_STATE_HOME/mlx-tui/sessions` (or
`~/.local/state/mlx-tui/sessions`), with strict validation, per-session
advisory `fcntl` locks, atomic sibling-file replacement and directory sync
before durability is acknowledged. `ChatPane` owns the active `ChatSession`,
its lock handle and a save revision counter; `messages` stays the
successful-pair projection while attempts are authoritative for restoring
presentation. One `capture_request_settings()`/`mark_dirty` path serves every
next-request mutation. Drafts coalesce at 250 ms, writes serialize on one
async lock via `asyncio.to_thread`, and the latest snapshot is taken after
lock acquisition so an older save never marks newer edits saved. Progress
accumulates from `TurnProgress` deltas (never from the tool-count activity
string) with checkpoints at most once per second plus immediately at submit,
terminal outcome, switch, clear and shutdown. Pre-send persists the running
attempt before HTTP or sends nothing; terminal outcomes persist every final
state including identity mismatch, context, transport and cancellation facts.
A save failure keeps the visible outcome but blocks send/clear/switch until
Retry save (no HTTP) or explicit discard. `SessionScreen` lists labels by
updated time off-thread with locked/corrupt states and opens only by explicit
acquire-lock-then-replace; corrupt/newer files delete explicitly without
auto-overwrite. Reopening renders via `render_session_turn()` with derived
stamps/notices, adds no metrics or requests, maps unowned `running` to
`interrupted` only after proving no writer, reconciles differing saved
request settings through an explicit choice, and flushes best-effort on quit
(`action_quit` offers Retry save versus explicit quit-with-unsaved-work;
`on_unmount` stays idempotent). Restoring a transcript never restores a warm
KV cache, credentials, commands or process ownership.

Selected attachments are handled by the UI-free `attachments.py` boundary.
It opens only the explicit path, verifies a regular file through the opened
descriptor, bounds the read at 256 KiB and rejects invalid UTF-8, NUL/binary
content, directories, special files and unreadable paths. Each
`AttachmentSnapshot` retains the selected and resolved paths, exact content,
byte length and SHA-256. `render_user_content()` is the single deterministic
prompt-plus-file-block format used by both preview and POST payloads.

`ChatPane` reads files off-thread and snapshots them once when attached. Draft
attachments are saved in session schema v2 with a 1 MiB aggregate limit;
attempt snapshots are immutable and independently bounded, while v1 sessions
load with empty attachment lists and are written back as v2. Temporary
sessions keep snapshots in memory. The next request is prepared from the
successful message projection plus the rendered current draft, and the same
prepared window supplies preview, included-message IDs and send payload.
Non-loopback destinations are rejected before POST. Character-based estimates
are labelled as estimates and never imply tokenizer/model-limit guarantees.

This is the shipped Milestone D subset: durable sessions,
multiline/copy/retry, and selected text-file snapshots. The
[Milestone D evidence record](docs/compatibility/milestone-d.md) separates
implementation checks from product acceptance, which remains deferred pending
consented repeat-use and named-Mac real-runtime recovery observations.

Each turn enters the per-model `HistoryStore` ring of 64 records with an
explicit outcome. Complete turns with server usage show first output, answer
start, total, and client request tok/s (all server completion tokens over
full request duration, explicitly not engine speed); estimates carry `~` and
unknowns show `—`. Complete-but-usage-missing output is estimated and marked;
incomplete outcomes stay unknown regardless of usage, and failed/cancelled
counts/timings are unknown, never fabricated zeros. Unsuccessful/unknown
rates are filtered from the sparkline consistently. Cached-token usage means
server-reported reuse, not guaranteed residency; ordinary chat has unknown
load/cache state. Memory uses GiB throughout; RSS is sampled process memory
distinct from allocator peaks, and a latest-unknown sample never displays an
older value as current. Context shows estimated input plus reserved output
against the configured budget with an excluded-turn count; estimates use
character heuristics, not the runtime tokenizer.

## Search and downloads

`SearchScreen` owns the search modal, result table, size lookup, revision
selection, download worker, progress, and cancellation acknowledgement.
`search.py` remains UI-free: it lists `mlx-community`, filters the supported
MLX file set, computes disk-fit hints, and wraps `snapshot_download` with a
throttled progress class.

A highlighted result records the Hub revision hash and downloads with that
revision and the same allow patterns used for size estimation. Escape sets a
cooperative event and leaves the modal mounted with “cancellation requested”
until the Hub worker returns. Success, acknowledged cancellation, and errors
share one terminal UI path; cancellation rescans the cache without deleting
partial or completed files. A blocked Hub operation is not promised to stop
immediately.

Search callbacks carry a generation guard so stale results from a superseded
query cannot overwrite the current result table or download state.

## Pinned profile comparisons

`profiles.py` strictly loads the packaged `coding_profiles.toml` catalogue and
fingerprints every effective request, runtime, launch, revision, and template
asset identity. `MlxTuiApp` owns the selected pair, latest result, saved choice,
and active-profile/modified state; Models owns ordinary request-target selection,
while Compare renders or requests updates to comparison state. Metrics only
renders ordinary history.

The headless comparison facade in `comparison.py` re-exports the split
contracts, persistence, summary, and runner modules. The UI-free runner owns
the twelve slots: first request plus five repeats for each profile in selected
order. It accepts only an explicit-IP loopback chat-completions URL, verifies
cached pinned assets and process identity around every request, samples process
RSS off the event loop, and checkpoints before notifications or later requests.
Terminal failure or cancellation sends no later trial. Its summary keeps first
requests separate, limits quality to the exact synthetic reproduction check,
and reports latency or memory as inconclusive when identity, evidence, cache
reuse, conditions, or RSS attribution cannot support the narrower claim.

`ComparePane` owns Textual composition, setup/readiness/run/review/choice/reuse
controls, and worker/cancellation lifetime. `comparison_presenter.py` contains
pure result/progress/trial builders. The pane gathers explicit operator evidence
and runs one async comparison without changing Chat messages, draft, or live
Params. Compare and Keep never execute launch settings or stop/restart an
attached server; Download and Start server remain separate explicit controls. A
saved profile is revalidated against the packaged fingerprint, cached revision,
and asset hashes before explicit reuse. Ordinary setting/model/config changes
mark it modified. The app lease suppresses polling during a comparison and
preserves distinct cleanup and cancellation paths. Decisions are frozen to the
displayed result; persistence and application report separately, so a saved but
not-applied choice remains visible.

The Compare tab can reopen a result by explicit path. Loading validates before
replacing the displayed result, binds the opened path as authoritative for later
decisions, and leaves setup, saved choice, active settings, and files unchanged.
Partial results are inspectable but not decidable.

See [docs/comparison.md](docs/comparison.md) for the operator journey and
evidence limitations.

Each run is an atomic JSON checkpoint in `$XDG_STATE_HOME/mlx-tui/comparisons`
or `~/.local/state/mlx-tui/comparisons`. A decision commits as pending run,
atomic sibling `choice.json`, then finalized run. Reopen accepts the choice only
when its run ID and complete decision match the finalized run; corrupt, newer,
pending, or mismatched files remain visible errors and are never auto-applied.

## Configuration and presets

`config.py` parses the optional TOML file at `$XDG_CONFIG_HOME/mlx-tui/config.toml`
or `~/.config/mlx-tui/config.toml`. Known values require their declared TOML
types, numeric controls are clamped, optional `seed`/`enable_thinking` are sent
only when configured, and invalid files degrade to defaults at
startup. The editor action creates a commented template when needed, runs a
shell-split `$EDITOR` while Textual is suspended, and reloads only after a
successful zero exit and valid TOML parse. Editor launch failures and nonzero
exits are reported without claiming a reload. Host/port changes require a TUI
restart.

`presets.py` reads the sibling `presets.toml`; applying a preset updates the
chat inputs and current config snapshot. Presets are intentionally a flat file
and `$EDITOR` workflow, not a second management subsystem.

## Project layout

```text
src/mlx_tui/
  app/__init__.py          # coordinator, polling, status, config edit, presets
  boot.py                  # UI-free boot execution and verified success boundary
  chat.py                  # async HTTPX stream and typed result
  chat_pane.py             # chat UI, transcript, context, worker lifecycle
  chat_turn.py             # presentation-only persistent attempted turn
  coding_profiles.toml     # packaged pinned shortlist and dated evidence
  comparison.py            # stable facade for comparison modules
  comparison_contracts.py  # immutable comparison inputs/results/trials
  comparison_persistence.py # JSON checkpoints and choice transaction
  comparison_summary.py    # pure evidence and conclusion summaries
  comparison_runner.py     # sequential twelve-slot execution
  comparison_presenter.py  # pure Textual result/progress builders
  compare_pane.py          # Compare tab workflow and worker lifecycle
  confirm.py               # delete confirmation modal
  history/                 # token bounds, turn/memory rings, sparklines
  metrics_pane.py          # metrics table and sparklines
  models.py                # public HF cache adapter and ModelRow
  models_pane.py           # model UI and lifecycle workers
  operations.py             # UI-free operation lease
  params.py                # parameter input pane and config synchronization
  process.py               # listener-based process identity and memory
  presets.py                # flat preset loading and application data
  profiles.py              # strict profile/evidence parsing and fingerprints
  search.py                # UI-free Hub/search/download adapters
  search_screen.py         # search UI and acknowledged download lifecycle
  sessions.py              # versioned local session records and atomic storage
  attachments.py            # bounded UTF-8 file snapshots and prompt rendering
  session_screen.py        # explicit session picker without startup reads
  setup_screen.py          # keyboard-first managed/attach setup
  managed.py               # pinned runtime inspection and owned child lifecycle
  serverctl.py             # argv/shell commands, health, process groups
  sse.py                   # incremental SSE decoder and token helpers
  status.py                # endpoint probes and status formatting
  swap.py                  # pure swap policy and boot-plan rules
  table.py                 # model table rendering and key bindings
```

## Verification

The locked project gates are:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check src tests
uv run pyrefly check --min-severity warn
```

CI runs those gates on Ubuntu and macOS 14, reports the macOS architecture,
and runs isolated highest-resolution and declared-direct-floor suites. The
packaging job builds a fresh wheel and sdist, checks license and entry-point
metadata, rebuilds a wheel from the extracted sdist, and installs both wheels
outside the checkout before running `mlx-tui --help` and an import-path check.

Unit/integration gates use an HTTP stub and cannot prove real-server
behavior. Real-server compatibility is pinned in
[docs/compatibility/milestone-a.md](docs/compatibility/milestone-a.md)
(MLX-LM `74e7cf9`, Qwen3-1.7B-4bit, `local-m4-16gib`) with opt-in contracts
in `tests/runtime` that skip without `MLX_TUI_CONTRACT_URL` and
`MLX_TUI_CONTRACT_MODEL`.
