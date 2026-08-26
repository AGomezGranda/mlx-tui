# MLX TUI — idea doc

## Goal

A standalone terminal UI (own repo, not part of `local-ai`) that acts as a
**control plane for one running MLX server** — what's loaded, what it's costing
in memory, how fast it actually is, and swapping it without a stop/edit/start
dance.

Framing: `htop` for your MLX server, with a chat pane because you need a way to
generate load and eyeball quality.

MLX only (`mlx-lm`/`mlx-vlm`) — no vLLM/GGUF/llama.cpp. That constraint is
deliberate: it removes the need for a backend-abstraction layer or a
multi-engine config schema.

`local-ai` is just one example MLX setup it could point at — the TUI owns its
own config and doesn't read `mise.toml`/`.env` from there.

## Why not just use what exists

Most of the obvious "features" are already solved and shouldn't be rebuilt:

| Want | Already is | So |
|---|---|---|
| Browse/delete HF cache | `hf cache delete` (older: `huggingface-cli delete-cache`) — already interactive | Keep only as a keystroke inside the table we already show |
| Chat with a model | `mlx_lm.chat`, curl, any OpenAI-compatible client | Only justified by the *instrumentation* around it |
| Quantize | `mlx_lm.convert`, one line, run ~never | Cut |
| Edit config | An editor | `e` → `$EDITOR`, no forms |

What has **no** tool today, and is the reason this exists:

- Which model is loaded right now, and how much of 48GB is it holding?
- Do I have headroom for another one, or must I evict?
- Is the server alive, or did it wedge while I was in another window?
- What's real tok/s at 8k context, not on a toy prompt?
- Swap the running model in one keystroke.

## Why MLX-only

Not because "it targets Mac users" — Mac users ≠ MLX users. Most people running
local models on a Mac are on Ollama or LM Studio; llama.cpp runs fine on Apple
Silicon and has a far bigger install base. MLX-only buys a slice of Mac users,
not all of them.

The real reason is **build cost**: one model-loading story, one cache layout
(`scan_cache_dir()`), one memory model (unified — RSS is meaningful), one server
to introspect. Adding llama.cpp means a backend interface, GGUF cache scanning, a
different memory story (VRAM vs RAM vs mmap), and per-backend config schemas.
That's all plumbing and no product.

**The constraint is nearly free to reverse**, which is what makes it an easy call.
Roughly half the app is backend-agnostic by accident, because OpenAI-compatible
HTTP is the de facto shared surface:

| Surface | MLX-specific? |
|---|---|
| Chat tab | **No** — `POST /v1/chat/completions` over SSE works unmodified against Ollama, llama.cpp server, LM Studio, vLLM |
| Status bar liveness | **No** — `GET /v1/models` is universal |
| Status bar memory | Partly — RSS + unified memory is an Apple Silicon read |
| Models tab | **Yes** — HF cache layout, `mlx-community`, repo-id quant parsing |
| Load/swap | **Yes** — depends on mlx-lm server behaviour |

So multi-backend later isn't a wall to knock down — it's the Models tab needing a
sibling implementation. A v3 decision to make with evidence, not a v0 bet made
blind.

**Corollary:** don't build a backend abstraction, but don't go out of the way to
make chat MLX-flavoured either. Talk to it over HTTP, keep the port configurable,
and it stays accidentally portable for free. Optionality by not doing work.

**Counterargument to hold:** LM Studio already ships an MLX engine with a GUI, so
"MLX-native" alone isn't the differentiator. The differentiator is *TUI + control
plane* — always-on, one keystroke, memory headroom visible, swap without a
restart. Nobody serves that. Keep the pitch there.

## Reference

[mlx-lm](https://github.com/ml-explore/mlx-lm) (and `mlx` itself) is the point of
reference — the TUI wraps its CLI/APIs (`generate`, `convert`, server) rather
than reimplementing model loading, quantization, or serving.

## Stack

Textual (Python) — tables/inputs/tabs/markdown for free instead of hand-rolled
curses. Own venv, separate from `local-ai`'s `.venv`. Deps: `textual`, `httpx`,
`psutil`, `huggingface_hub`.

## Shape

Two tabs, one always-visible status bar, one shared log pane.

```
┌ ● qwen3.8-27b-4bit · 16.4/48 GB · 14.8 tok/s · :8080 ─────┐
│  [ Models ]  [ Chat ]                                     │
│  …                                                        │
├───────────────────────────────────────────────────────────┤
│ log: downloading mlx-community/… 3.1/16.4 GB ▓▓▓░░░ 19%   │
└───────────────────────────────────────────────────────────┘
```

### Status bar (not a tab — never hidden)

The app's reason to exist, so it's always on screen.

- **Liveness + loaded model:** `GET /v1/models` every 2s, `httpx`, 500ms timeout.
  Timeout → red dot, not a crash. Same signal a real client would see; no log
  parsing. When the dot is red and `start_cmd` is configured, offer a cold
  start — one keystroke on the red dot, output streamed into the log pane —
  rather than auto-running the server as a side effect of opening the TUI. A
  surprising side effect is worse than one extra keystroke.
- **Memory:** `psutil.Process(pid).memory_info().rss` on the server pid. RSS is
  the honest number — MLX allocates in unified memory, and `mx.get_peak_memory()`
  isn't reachable across a process boundary. Pid from the configured pidfile if
  there is one, else `psutil.process_iter()` matching `mlx_lm.server` /
  `mlx_vlm.server` in the cmdline. Caveat: RSS can undercount right after load if
  weight pages are mmap'd and touched lazily — expect it to climb over the first
  few requests rather than jump to steady-state immediately. Treat it as "honest
  enough", not exact.
- **Headroom:** `psutil.virtual_memory().available` — this is what makes "can I
  load a second model" answerable. Caveat: macOS's purgeable/compressed-memory
  accounting makes `available` an approximation, not a hard number — it can read
  low while a load would still succeed. Don't hard-block a load on this number;
  use it to color the fits-in-headroom ✓/⚠ column as a hint, and let an actual
  load attempt (and its failure) be the ground truth. Second caveat: size-on-disk
  is weights only — KV cache grows with context (~0.1–0.5 MB/token depending on
  architecture, so GBs at 8k–32k ctx), meaning ✓ against weights alone drifts
  toward ⚠ as the context fills. The column stays a weights-only hint;
  long-context loads are the risky end.
- One `Static`, `set_interval(2, poll)`, reactive update. ~40 lines.

### Models — a loader, not a browser

One `DataTable`, rows = models actually in the HF cache. Columns: name, size on
disk, quant bits (parsed from the repo id — `-4bit`/`-8bit`/`-bf16`), fits-in-
headroom weights-only (✓/⚠ — see the status-bar caveat on KV cache), loaded-now
marker.

Keys: `enter` load/swap · `d` delete · `/` search HF.

- **Rows:** `huggingface_hub.scan_cache_dir()` → `.repos`, filtered to MLX-ish
  repo ids. Gives `repo_id`, `size_on_disk`, `revisions` for free.
- **Delete:** `scan.delete_revisions(*hashes).execute()` behind a confirm modal.
  Two lines. Worth keeping only because it's one keystroke inside something
  already open.
- **Load/swap** — the interesting one, and the one that needs verifying:
  - If `mlx_lm.server` loads on demand from the request's `model` field: "load"
    is a 1-token completion against the target id, with a spinner.
  - If not: `stop_cmd` + `start_cmd --model X` from config, output streamed into
    the log pane.
  - Either way the TUI owns the *waiting* — a progress bar instead of `tail -f`
    and vibes. That's the actual win.
  - If swap is a restart and Chat has an open stream, that request just dies
    when the process goes down. Cheapest fix: cancel the in-flight `httpx`
    request client-side before issuing `stop_cmd`, and surface "cancelled —
    model swapping" in the chat pane instead of letting it error silently.
- **Search:** `HfApi().list_models(author="mlx-community", search=q, limit=50)`;
  `↓` = `snapshot_download` in a `@work(thread=True)` worker, progress into the
  log pane. A search box and a download key — not a full HF browser. Scoping to
  `author=mlx-community` misses MLX repos published elsewhere — accepted until a
  wanted model doesn't show up; widening is then a one-line change to plain
  `search=`.

### Chat — an instrument, not a chat client

Justified by what it measures, not by markdown rendering.

Every assistant turn is stamped: `1,284 tok in · 512 out · 13.9 tok/s · TTFT 0.8s
· ctx 9.2k/32k`, with a context bar that goes amber near the limit.

- **Transport:** `POST /v1/chat/completions`, `stream: true`, plain `httpx` SSE.
- **Numbers:** TTFT = clock at first chunk; tok/s = output tokens ÷ (now − first
  chunk). Client-measured — more honest than log-scraping, and zero new infra.
  The first turn after a load/swap is stamped **cold** — its TTFT includes
  weight paging and graph compile, not serving speed. Cold turns are tagged in
  the pane and excluded from the history sparkline, so a fresh load doesn't
  paint a fake degradation cliff.
- **Prompt tokens:** from `usage` in the final chunk if present (may require
  `stream_options.include_usage` — verify); else `len(text)/3.5`, labelled as an
  estimate. No tokenizer dependency just for a status line.
- **Rendering:** append streamed chunks to a `RichLog`/`Static`, swap in the
  rendered `Markdown` widget on completion. Re-rendering the whole `Markdown`
  per chunk is the standard Textual perf trap.
- **Params:** temp / top-p / max-tokens as three `Input`s in a collapsible
  sidebar. Not sliders — typing a number beats dragging in a terminal.
- **System presets:** flat `presets.toml`, `ctrl+p` cycles. No management UI.

### History (footer strip, not a tab)

Last N turns of `(model, ctx_len, tok/s)` kept in memory, rendered as a braille
sparkline. Answers "does this model degrade at long context". ~30 lines. Once the
status bar has memory and chat has per-turn tok/s, there's nothing left for a
standalone Metrics tab.

### Config (no tab)

Single file, `~/.config/mlx-tui/config.toml`: `model`, `host`, `port`,
`start_cmd`, `stop_cmd`, `pidfile`. `e` opens `$EDITOR` on it, reload on save.
Five strings don't need form widgets, validation, and a save flow.

## Verify before writing code

Cheap to check against a running server — ~15 minutes of curl total — and the
design leans on all three. Run them before touching code, v0 scaffolding
included.

1. Does `mlx_lm.server` load on demand from the request's `model` field, or is
   swapping a restart? Answer this first, not in parallel with writing v0/v1 —
   the build order below is written as if the answer is "restart" (the safer
   assumption per v1's own text), but if it's actually hot-swap, v1's design
   changes shape rather than just gaining an optimisation.
2. Does it emit `usage` on streamed responses, and does that need
   `stream_options.include_usage`?
3. Does `mlx-vlm`'s server match `mlx-lm`'s API surface? The current model here
   is a VLM. **If they diverge, pick one** — supporting both is exactly the
   abstraction layer this doc swore off.

## Build order

Each version is gated: it ships only if the previous one is still being used a
week later. The gates matter more than the features — this is a tool for one
user, and the failure mode is building all four and using none.

### v0 — "is the premise true?"

**Question it answers:** is a permanently-visible status bar worth a terminal
split, or is `curl /v1/models` enough?

One file, ~200 lines, `textual` + `httpx`. Status bar (liveness dot, loaded
model id, RSS, available memory, port) polling every 2s, plus a chat pane that
streams `/v1/chat/completions` and stamps each turn with TTFT and tok/s.

Do the three checks in *Verify before writing code* first — all of v0 assumes
the answers, and #3 (mlx-lm vs mlx-vlm API parity) can change what gets built.

Shortcuts taken on purpose: no config file (host/port hardcoded or `--port`),
no markdown rendering (raw text into a `RichLog`), no params sidebar, no
history. Prompt tokens estimated as `len/3.5` unless `usage` shows up free.

Implementation notes worth fixing early, while everything still fits on one
screen:

- **Poll overlap:** guard the 2s poll — if the previous request is still in
  flight, skip the tick. A wedged server plus a naive loop piles up sockets.
- **Pid discovery cost:** `psutil.process_iter()` walks every process on the
  box; run the scan once, cache the pid, re-scan only when the cached pid dies.
- **Threading:** chat streams in a `@work(exclusive=True)` worker; chunks cross
  into widgets via `call_from_thread`. Widgets are not thread-safe — this is
  the classic Textual hang, and v0 is where it gets built or avoided.
- **TTFT definition:** clock at the first chunk *carrying text*, not the first
  byte — some servers send a role-only delta first. Fix the definition now;
  every cold-vs-warm comparison later depends on it not moving.
- **Liveness isn't just TCP:** a proxy answering 502 HTML or an unrelated
  service squatting on :8080 must read amber, not green — require parsed JSON
  with a non-empty `data` list before the dot goes solid.

**Done when:** it runs for a week in a tmux pane during real work.
**Kill if:** after that week the answer to "is the server up" still comes from
somewhere else, or the chat pane is only ever used for hello-world prompts —
the instrumentation is the product, and if the numbers aren't being read, there
is no product. An afternoon lost, not a month.

### v1 — "swap without the stop/edit/start dance"

**Question it answers:** does one keystroke actually change behaviour, or does
model-swapping happen rarely enough that a shell alias covers it?

`DataTable` from `scan_cache_dir()` — name, size on disk, quant bits, fits-in-
headroom ✓/⚠ (weights only, computed against the status bar's available-memory
number, which is why this comes after v0), loaded-now marker. `enter`
loads/swaps, `d` deletes behind a confirm modal.

This is where the config file arrives, because swap needs `start_cmd` /
`stop_cmd` / `pidfile` — five strings in `~/.config/mlx-tui/config.toml`, `e`
opens `$EDITOR`. Whether swap is a 1-token warm request or a full process
restart depends on check #1; the restart path is the one to build if there's
any doubt, since it works either way and the warm path is an optimisation.

The real deliverable is not the table — it's owning the *wait*. A progress
bar and a log pane instead of `tail -f` and guessing whether it's stuck.

Swap is a state machine, because every failure mode lives between states:

```
idle → stopping → starting → waiting-health → idle
          │           │             │
          └───────────┴─────────────┴→ failed → log pane
```

- **waiting-health is the real progress bar.** Poll `/v1/models` until the new
  id appears, timeout scaled to model size (~60s base + a margin per GB).
  mlx-lm emits no structured load progress, so the bar is phase labels plus
  streamed stderr in the log pane — indeterminate, but never silent.
- **One swap at a time.** `enter` disabled on the table while a swap is in
  flight; double-press is the first bug this screen meets.
- **Marker hygiene:** clear the loaded-now marker the moment `stop_cmd` fires,
  not when the next poll notices — the status bar lags up to one cycle, and a
  stale ✓ next to an evicted model is exactly the kind of lie this app exists
  to prevent.
- **fits-in-headroom margin:** ⚠ when `size_on_disk > available − margin`,
  margin ≈ 20% of size — slack for lazy-touch RSS climbing and some KV growth.
- **Config reload:** re-read the TOML when `$EDITOR` exits; five strings don't
  justify an mtime watcher.

**Done when:** a model swap is one keystroke and the terminal never leaves the
TUI.
**Kill if:** it turns out only one model ever gets loaded — then the table is a
`ls` with extra steps, and only the delete keystroke was worth having.

### v2 — "acquire new models without leaving"

**Question it answers:** is finding-and-downloading frequent enough to be
in-app, given `hf download` exists?

Search box over `HfApi().list_models(author="mlx-community", search=q,
limit=50)`, `↓` triggers `snapshot_download` in a `@work(thread=True)` worker
with progress into the log pane. Results show repo id, quant, and download
size against free disk. Scoped to `mlx-community`; widen only when a wanted
repo isn't there (see Search above).

Weakest version of the four, and the most tempting to over-build. The line is:
a search box and a download key. Not filters, not sorting, not model cards, not
a README preview, not a favourites list. If it grows past ~80 lines it has
stopped being v2 and become an HF browser, which is out of scope.

Where the 80 lines actually go:

- **`allow_patterns`** on `snapshot_download` (`*.safetensors`, `*.json`,
  `tokenizer*`). mlx-community repos often carry multiple formats; a blind pull
  is gigabytes you'll never load. This one argument is the difference between
  a downloader and a disk-filler.
- **Resume is free** — `huggingface_hub` picks up partial downloads on retry.
  Rely on it silently; building UI for it is already scope creep.
- **Pre-flight:** `shutil.disk_usage()` against expected size, warn when short.
- **Handoff:** on completion, rescan and refresh the Models table rows — v2's
  exit feeds v1's entrance, and that seam working cleanly *is* the feature.

**Done when:** a new model can be found, downloaded, and loaded without a
second terminal.
**Kill if:** new models get pulled once a month — a monthly `hf download` is
not a pain worth code.

### v3 — "does this model degrade at long context?"

**Question it answers:** the one thing that needs *accumulated* data rather
than a live reading, and therefore genuinely can't be answered by any existing
tool.

Footer strip: last N turns of `(model, ctx_len, tok/s)` in memory, drawn as a
braille sparkline, cold first-turns excluded. ~30 lines. Gated on v0 proving the app, not on v1/v2 — if
the chat instrumentation is the part being used, this is the natural next
increment and v1/v2 can be skipped entirely.

In memory only. The moment it wants persistence it's append-JSONL and a
`--since` flag, not a database and not a Metrics tab.

Fix the buffer record now so the persistence path stays mechanical:
`(ts, model, prompt_tok, out_tok, ttft_s, tok_s, ctx_len, cold)` — ring buffer,
one series per model, so a swap doesn't smear two models into one line. Cold
and cancelled turns are already flagged upstream; the plot just honours the
flags rather than re-deriving them.

Render two variables, not one: y = tok/s buckets, x = turn order, cell shade =
ctx depth. The failure worth seeing — fast at 1k, slow at 24k — is inherently
two-dimensional, which is why a plain tok/s line chart answers the wrong
question.

**Done when:** the sparkline has actually changed a decision (evicted a model,
capped a context length).
**Kill if:** it's decoration. A number nobody acts on is a number to delete.

## Explicitly out of scope

- Multi-backend (vLLM, llama.cpp, GGUF).
- Quantization runner — `mlx_lm.convert` is one line, run ~never.
- Config/preset management UI — flat files + `$EDITOR`.
- Custom cache/disk-usage scanning — `scan_cache_dir()` already does it.
- Server log tailing for metrics — a format we don't control, breaks on every
  mlx-lm bump, and gives nothing `/v1/models` + psutil + client timing don't.
- Conversation persistence, branching, multi-session. Add when you actually want
  yesterday's chat back — and then it's append-JSONL, not a feature.
- Agentic/tool-calling harness in chat.

## Status

Idea stage — not yet scaffolded.
