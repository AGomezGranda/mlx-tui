# MLX TUI — Part 2: Next phases

> Tracking doc for work after `docs/idea.md` v0–v3. Mirrors the todo
> pile (`todo:1-44`) against what shipped, and gates the next increments
> the same way the idea doc does: *ships only if the previous is still
> being used a week later* (`docs/idea.md:220`).

## Where we are

Idea doc v0–v3 is **done**, with one shape divergence:

| Version | Idea doc | Shipped |
|---|---|---|
| **v0** Status bar + chat (`docs/idea.md:224`) | `GET /v1/models` 2s/0.5s, RSS via `psutil`, TTFT/tok/s stamp | `src/mlx_tui/app/polling.py:22` `src/mlx_tui/status.py:16` `src/mlx_tui/chat.py:53` `src/mlx_tui/process.py` |
| **v1** Models swap (`docs/idea.md:264`) | `DataTable` + `scan_cache_dir()` + hybrid warm/restart + config | `src/mlx_tui/models.py:81` `src/mlx_tui/models_pane/swap_ops.py` `src/mlx_tui/serverctl.py:99` `src/mlx_tui/config.py:28` `src/mlx_tui/swap.py` |
| **v2** Search/download (`docs/idea.md:311`) | `HfApi().list_models` + `snapshot_download` + `allow_patterns` | `src/mlx_tui/search.py:36` `src/mlx_tui/search_screen/` |
| **v3** History (`docs/idea.md:343`) | footer braille strip, `TurnRecord`, ring `64` | **Metrics tab** instead of strip: `src/mlx_tui/history/store.py:36` `src/mlx_tui/history/sparkline.py:73` `src/mlx_tui/metrics_pane.py:42` (chat sparkline shaded by ctx + memory RSS sparkline + 64-row table) |
| **Extras** | out of scope | Params `src/mlx_tui/chat_pane/params.py:26` + Presets `src/mlx_tui/presets.py:88` (`ctrl+n/o`) — not in idea doc |

Current entry `mlx-tui = "mlx_tui.app:main"` `pyproject.toml:23`, `README.md:7`, `ARCHITECTURE.md:1` remain the reference.

## Todo vs thesis — what to keep, what to cut

`todo` (`todo:1-44`) reads as four buckets plus research/ops:

| Todo line | Ask | Thesis fit | Verdict |
|---|---|---|---|
| `todo:19-24` telemetry: memory bar, context bar, prefill vs decode | **Core** — "htop for your MLX server" `docs/idea.md:10` | **Keep** — 1 day, no dep |
| `todo:26-33` perf/sampler: speculative, top-p/k, penalties, structured, kv quant | Mixed — instrumentation vs generation tuning | **Keep cheap knobs only**, gate rest on `curl` proof server exposes them |
| `todo:35-38` LoRA hot-swap, template manager, A/B split | Weak — backend abstraction `docs/idea.md:52` | **Cut**. Presets already cover templates (`src/mlx_tui/presets.py:13`); LoRA/A/B = second Models tab |
| `todo:40-44` vim, file inject, session persist, vision | Mixed | **Keep file-inject + JSONL**, vim is 5 lines, **cut vision** per `docs/idea.md:372` |
| `todo:8` server-log tailing | Explicit out-of-scope `docs/idea.md:379` | **Cut** — client timing + `psutil` is the contract |
| `todo:9` "separate docs/plan to its own repo" | Meta | **YAGNI** |
| `todo:14-16` MIT + brew/pypi + hold public | Ops | MIT done `pyproject.toml:6`; brew/pypi hold per `todo:16` until Metrics gate passes |

Research pile `todo:1-5` (quant runner, mlx-lm inference tricks, harness Pi, benchmarks) is pre-work, not features. Quant is cut per `docs/idea.md:28` (`mlx_lm.convert` one-liner). Others need a 15 min `curl` spike before any code (see *Verify*).

## Verify before writing code (repeat of `docs/idea.md:201`, new surface)

Run against a real `mlx_lm.server` before touching Part 2:

1. **Sampler params:** does `POST /v1/chat/completions` honour `top_k`, `repetition_penalty`, `min_p`, `seed`? Or does it silently drop them / 400? Determines Phase 5 scope.
2. **KV / speculative / structured / LoRA:** does the server expose `--kv-bits`, `--draft-model`, `response_format`/`grammar`, `--adapter-path` via the OpenAI surface, or only via CLI flags at boot? If CLI-only, TUI work is `start_cmd` templating, not a chat payload.
3. **Context length source:** where does max ctx come from for the bar — `config.json` `max_position_embeddings`, server's `GET /v1/models` `context_length`, or must the user set it? Pick one, don't auto-discover three ways.
4. **mlx-vlm parity** (`docs/idea.md:214`): if building vision, does `mlx_vlm.server` match `mlx_lm.server` SSE shape? If not, pick one.

## Build order — Part 2

Each phase answers one question and has a kill line. Ponytail ladder applies: stdlib → native → installed dep → one line → minimal code.

### Phase 4 — Finish the control plane (the only must-do)

**Q:** does visual headroom change a load decision? (`todo:19-24` trimmed)

- **Unified memory bar** `src/mlx_tui/status.py:62` `src/mlx_tui/app/polling.py:39`: replace text `RSS X GB · avail Y/Z GB` with a `Textual ProgressBar` + label. `rss/total` filled, `avail` dim. `MemorySnapshot` + `memory_snapshot()` already polls `psutil.virtual_memory()` (`src/mlx_tui/process.py`). No new dep.
- **Context limit bar** `src/mlx_tui/chat_pane/__init__.py:42` `src/mlx_tui/history/store.py:44`: `ctx_len / max_ctx` bar under the input (green → amber >80% → red >95%). `ctx_len` already in `TurnRecord`; `max_ctx` = new optional `AppConfig.max_context: int | None` (default 8192, set in `config.toml`), not auto-parsed. `trim_for_context` (`src/mlx_tui/history/tokens.py:24`) still caps at `max_ctx`.
- **Prefill vs decode** `src/mlx_tui/chat.py:106` `src/mlx_tui/history/store.py:36`: add `prefill_tok_s = prompt_tok / ttft` when `usage.prompt_tokens` present (`src/mlx_tui/sse.py`), keep `tok_s` as decode. Stamp `TTFT 0.8s · prefill 84 tok/s · decode 14.8 tok/s`. Already have `prompt_tok`/`ttft_s` per turn.

> skipped: swap-memory warning, exact `config.json` ctx auto-detect, per-token KV-GB math. Add when bar proves read.

**Done when:** the bar has prevented or caused a swap at long ctx.
**Kill if:** it is decoration (`docs/idea.md:370`).

### Phase 5 — Sampler parity (30 min)

**Q:** does tweaking beyond temp change quality?

- Add `top_k: int | None` + `repetition_penalty: float | None` (+ optional `seed: int | None`) to `AppConfig` `src/mlx_tui/config.py:12`, `Preset` `src/mlx_tui/presets.py:13`, `Params` collapsible `src/mlx_tui/chat_pane/params.py:26`, and payload `src/mlx_tui/chat_pane/turn.py:26`. Same clamp + `type is` guard as `temperature`/`top_p` (`src/mlx_tui/config.py:71`). Reuse existing `@on(Input.Submitted)` clamp flow (`src/mlx_tui/chat_pane/params.py`).

Gated on verify #1 — if server drops the keys, don't add them.

> skipped: `min_p`, `frequency_penalty`, `presence_penalty`, structured regex/grammar. Add only after verify #2 shows `response_format` works.

**Done when:** presets pinning `top_k`/`repetition_penalty` produce a visibly different turn.
**Kill if:** `temperature`/`top_p` already cover the tuning.

### Phase 6 — Local workflow (power-user unlock)

**Q:** do you want yesterday's chat back? (`todo:42-43`)

- **Session persist** `src/mlx_tui/history/store.py:56`: append JSONL `~/.local/share/mlx-tui/history.jsonl` (honour `XDG_DATA_HOME`) on `HistoryStore.add`. Read `--since` on boot. Reuse frozen `TurnRecord` + `MemoryRecord` shape — no DB, no migration. KV-cache restore is explicitly not attempted.
- **File / code context inject** `src/mlx_tui/chat_pane/__init__.py:72`: `ctrl+f` or `/file <path>` → read file (cap 32 k chars, binary → dim notice) → append as fenced block to `messages` before `trim_for_context`. Stdlib only.
- **Vim** `src/mlx_tui/table.py:25`: add `j→cursor_down`, `k→cursor_up` to `ModelsTable.BINDINGS`. 5 lines.

> skipped: vim modal editing, session branching/graph, export md/pdf, drag-drop images. Add when JSONL shows daily use.

**Done when:** a session was resumed after a TUI restart.
**Kill if:** persistence is never read.

### Backlog — research-gated, do not build yet

| Item | Gate | Why parked |
|---|---|---|
| Speculative decoding (`todo:28`) | verify #2: `--draft-model` in OpenAI payload? | Likely CLI-only; TUI work would be `build_start_command` templating (`src/mlx_tui/serverctl.py`), not chat |
| KV-cache quant (`todo:33`) | verify #2: `--kv-bits 4/8` | Same — boot flag, not per-turn |
| Structured output (`todo:32`) | verify #2: `response_format` | Needs server grammar support |
| LoRA hot-swap (`todo:36`) | verify #2: `--adapter-path` hot? | Restart path already exists (`src/mlx_tui/app/swap_ctrl.py`); hot is new server capability |
| A/B split (`todo:38`) | — | Second chat pane = idea doc's "two tabs" broken; complexity without evidence |
| Vision / drag-drop (`todo:44`) | verify #4 | New backend (`mlx_vlm.server` divergence), explicitly out-of-scope `docs/idea.md:372` |

## Explicitly not in Part 2

- Multi-backend, quant runner, server-log tailing, agentic harness (`docs/idea.md:372` + `README.md:123` — unchanged).
- Config/preset management UI — flat files + `$EDITOR` (`src/mlx_tui/app/config_edit.py:20`).
- `docs/plans` split to another repo (`todo:9`).
- Brew / PyPI publish (`todo:15`) — hold until Phase 4 gate passes (`todo:16`).

## Status

Part 2 not started. Next action: 15 min verify spike (the 4 curls above), then Phase 4 behind the 1-week gate.
