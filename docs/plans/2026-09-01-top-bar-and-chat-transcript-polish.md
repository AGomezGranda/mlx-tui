# Top Bar and Chat Transcript Polish (TA + CA)

**Date:** 2026-09-01
**Work Item:** n/a
**Status:** Complete

## Overview

Polish two TUI visuals without changing behavior: give the docked status bar (`Horizontal#status-bar`) breathing room and hierarchy so it no longer feels glued to the `Models·Chat·Metrics` tabs, and group each `you › prompt + stats` block with a subtle left-rule and inter-turn separator so it reads as distinct from the surrounding Markdown assistant responses.

## Current State

- **App shell / top bar** `src/mlx_tui/app/__init__.py:40-59` `MlxTuiApp.DEFAULT_CSS`:
  ```css
  #status-bar { dock: top; width: 100%; height: 1; layout: horizontal; }
  #memory-bar { width: 16; height: 1; margin: 0 1; }
  #memory-label { width: auto; content-align: left middle; }
  #status-dot, #status-model, #status-port { width: auto; }
  #app-log { dock: bottom; height: 6; border-top: solid $primary; }
  ```
  `compose()` `app/__init__.py:151-159` yields `Horizontal(id="status-bar")` with 5 children `Static#status-dot`, `Static#status-model`, `ProgressBar#memory-bar`, `Static#memory-label`, `Static#status-port` then `TabbedContent(initial="models")` with `TabPane("Models",id="models")→ModelsPane` + `TabPane("Chat",id="chat")→ChatPane` + `TabPane("Metrics",id="metrics")→MetricsPane` then `RichLog#app-log`. No `background`, `padding`, `border-bottom`, or `margin` on `#status-bar`; `TabbedContent` sits flush below. Rendering delegated to `src/mlx_tui/app/status_bar.py:20` `render_status()` → `src/mlx_tui/status.py:62` `format_status_line()` and `src/mlx_tui/app/polling.py:35` `poll_tick()` every 2 s. Theme tokens in use: `$primary` for `#app-log` border, `$primary` + `padding:0 1` for `#metrics-sparkline` `app/__init__.py:67-72`; available but unused for status bar: `$surface/$panel/$boost`.

- **Chat transcript** `src/mlx_tui/chat_pane/__init__.py:22-73` `ChatPane(Vertical)` owns `Collapsible#params-collapsible` + `Static#chat-stream` + `RichLog#chat-log(markup=False,wrap=True)` + `Input#chat-input` + `ProgressBar#ctx-progress` + `Static#ctx-bar`. Prompt written immediately in `_on_input_submitted` `chat_pane/__init__.py:92` as `log.write(Text(f"you › {text}"))` (plain `Text`, no style/background/border). Stats produced after `stream_turn()` `src/mlx_tui/chat.py:53` (`TurnResult` `tok_in_str/tok_out_str/tok_s/ttft/prefill`) formatted in `src/mlx_tui/chat_pane/turn.py:117-125` into `stamp` (`f"{tok_in} in · {tok_out} out · {prefill} prefill tok/s · {tok_s} decode tok/s · TTFT {ttft:.2f}s"` or fallback) and written in `complete_turn_ui` `turn.py:184-194` as:
  ```python
  log.write(Text(stamp_text, style="dim"))  # turn.py:189
  if full_text:
      log.write(Markdown(full_text))
      log.write("")  # turn.py:192-193
  for notice in notices:
      log.write(Text(notice, style="yellow"))
  ```
  Stream is `Static#chat-stream` throttled `0.1s` `chat.py:20` via `call_from_thread(_update_stream)` `turn.py:84`. No grouping, no container, no separator between turns: previous `Markdown` is directly followed by next `Text("you › …")` with only one blank `Text("")` after each assistant block.

- **Tests / harness** `tests/conftest.py:179` `AppHarness(app,pilot,server)` with `harness.log_lines()` reading `RichLog#chat-log` lines `conftest.py:182`, `wait_for()` `conftest.py:189`, `StubServer` modes `ok/probe/error500/truncated/length_cap/empty/slow/no_usage` `conftest.py:24`, `tests/integration/test_app_integration.py:15` `_STAMP_RE = r"\d+( \(est\))? in · \d+( \(est\))? out · (?:\d+ prefill tok/s · [\d.]+ decode tok/s|[\d.]+ tok/s) · TTFT [\d.]+s( · cold)?"` and assertions `test_status_green_and_chat_stamp_over_stub_http` `test_cancel_closes_stream` `test_chat_renders_markdown` `test_prefill_vs_decode_stamp_and_table`. Unit tests `247 passed` `uv run pytest -q` verified 2026-09-01. `pyproject.toml:7` `textual>=8.2.8`, `tool.pyrefly preset=strict`, `tool.ruff` `select E4,E7,E9,F,I,PL,UP,TID,ASYNC,DTZ`.

## Design Decisions

| Decision | Options Considered | Rationale |
|----------|--------------------|-----------|
| Top bar → `T-A` surface+padding+gap (keep `height:1`, add `background:$surface` [fallback `$panel` if `$surface` contrast too low], `padding:0 1`, `TabbedContent{padding-top:1}` + `#status-model{width:1fr}`; `border-bottom: solid $primary` deferred — requires `height:2` because Textual draws borders inside `height` (see `#app-log` height 6 + border-top `app/__init__.py:59-63`); keep `height:1` no border to avoid clipping) vs `T-B` pill wrapper (`Vertical#status-wrap` height 3) | T-B stronger separation but adds nesting, eats vertical space, touches `compose()` | T-A is 3 CSS lines, no compose change, enough breathing room; matches existing `$surface`/`$panel` + `padding:0 1` usage in `#metrics-sparkline`/`#app-log`. `padding-top:1` is dock-safe (margin on docked `Horizontal` / `TabbedContent` is ignored). Keep `height:1` so small terminals not clipped; add border only if bumping to `height:2`. |
| Chat grouping → `C-A` left-rule `▎` + dim separator vs `C-B` blank+dim rule only vs `C-C` `Vertical` turn containers with real CSS `border-left` | C-B lighter but less grouping; C-C needs `RichLog` → `VerticalScroll` rewrite, breaks `harness.log_lines()` and streaming pattern | C-A is smallest diff that groups `you › prompt + stamp` visually (prompt `bold` with `▎`, stamp `dim` with `▎`, dim `─` rule between turns). Zero new widgets, zero new deps, stays inside `RichLog` (`Text`/`Markdown`). |
| Keep prompt substring `you ›` at line start (now `▎ you ›`) and stats substring `tok/s` intact | New strings without `you ›`/`tok/s` would break substring checks | Existing harness assertions use `any("you ›" in t)` and `any("tok/s" in t)` — preserving substrings keeps most tests green; only `startswith("you ›")` and `_STAMP_RE.fullmatch` need narrow relaxations (see Phase 2). |
| Stamp line keeps exact `format_status_line`-derived text, only prefixed with `▎ ` | Keep stamp plain `dim` without prefix | Prefix ties stamp visually to prompt block; updating `_STAMP_RE` to allow optional `▎ ` is one-line. |
| No new widget/dep, `DEFAULT_CSS` stays in `app/__init__.py` | External `*.tcss` file, new `TurnWidget` | YAGNI — `DEFAULT_CSS` is the established pattern; a turn widget would duplicate `RichLog` scrollback and require `VerticalScroll` migration. |

## Implementation Phases

### Phase 1: Top bar hierarchy (T-A)

Give the docked bar visual hierarchy and gap from the tabs.

**Changes:**
- `src/mlx_tui/app/__init__.py` — edit `MlxTuiApp.DEFAULT_CSS` string `40-115` to this exact end-state (spiked as dock-safe, no clipping on 80×24):
  ```css
  #status-bar {
      dock: top;
      width: 100%;
      height: 1;
      layout: horizontal;
      background: $surface;
      padding: 0 1;
  }
  #status-dot, #status-port { width: auto; }
  #status-model { width: 1fr; }
  #memory-bar { width: 16; height: 1; margin: 0 1; }
  #memory-label { width: auto; content-align: left middle; }
  TabbedContent { padding-top: 1; }
  ```
  Notes: `background:$surface` (try `$panel` if contrast too low on dark theme); `padding:0 1` is horizontal only so `height:1` stays 1 row. `TabbedContent{padding-top:1}` is used instead of `margin-top` because `dock:top` ignores margin on docked `Horizontal` and on `TabbedContent`. Do NOT add `border-bottom: solid $primary` at `height:1` — Textual draws borders inside `height` (see `#app-log` `height:6` + `border-top` `app/__init__.py:59-63` and `#metrics-sparkline` `height:3` + `border-bottom` `app/__init__.py:70-72`); a border at `height:1` leaves 0 content rows. If a border is desired later, bump `#status-bar` to `height:2` first.
  - No `compose()` change (`app/__init__.py:151-159` stays 5 yields inside `Horizontal#status-bar`).
  - Verify `src/mlx_tui/app/status_bar.py` untouched — `render_status()` still writes `●`, model, port; only CSS changes.

**Success Criteria:**

#### Automated Verification:
- [x] Status green still: `uv run pytest -v tests/integration/test_app_integration.py -k "test_status_green_and_chat_stamp_over_stub_http or test_memory_bar_renders"`
- [x] Whole suite green: `uv run pytest -q` (expect 247 passed, no new failures)
- [x] CSS snapshot: `python3 -c 'from mlx_tui.app import MlxTuiApp; css=MlxTuiApp.DEFAULT_CSS; assert "background: $surface" in css or "background: $panel" in css; assert "padding: 0 1" in css; assert "TabbedContent" in css and "padding-top: 1" in css; assert "#status-model" in css and "width: 1fr" in css'` (prevents visual regression without rendering)
- [x] Lint: `uv run ruff check .`
- [x] Format: `uv run ruff format --check src tests`
- [x] Types: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui` (stub or real server): top bar has muted surface/panel background, 1-char side padding, and a 1-row gap above the `Models | Chat | Metrics` tab row (via `TabbedContent` padding). Bar stays 1 row tall; no clipping on 80×24 terminal. Model name expands, memory/port stay right-aligned. No `border-bottom` at `height:1` (would clip); border only if height bumped to 2.

### Phase 2: Chat transcript grouping (C-A)

Group `you › prompt` + `stats` as a visually distinct block between Markdown responses, subtle left-rule + separator.

**Changes:**
- `src/mlx_tui/chat_pane/__init__.py` — in `_on_input_submitted` `92` change:
  ```python
  # before:
  log.write(Text(f"you › {text}"))
  # after:
  log.write(Text(f"▎ you › {text}", style="bold"))
  ```
  Keep `log = self.query_one("#chat-log", RichLog)` and `Text` import. Substring `you ›` preserved for existing `any("you ›" in t)` checks.

- `src/mlx_tui/chat_pane/turn.py` — in `complete_turn_ui` `184-194`:
  ```python
  def complete_turn_ui(
      pane: ChatPane, full_text: str, stamp: str, cold: bool, notices: list[str]
  ) -> None:
      stamp_text = f"{stamp} · cold" if cold else stamp
      log = pane.query_one("#chat-log", RichLog)
      # stats grouped with prompt via same left-rule
      log.write(Text(f"▎ {stamp_text}", style="dim"))
      if full_text:
          pane.messages.append({"role": "assistant", "content": full_text})
          log.write(Markdown(full_text))
      for notice in notices:
          log.write(Text(f"▎ {notice}", style="yellow"))
      # inter-turn separator (subtle, not heavy box) — replaces the old post-Markdown blank at turn.py:193
      log.write(Text("─" * 40, style="dim"))
      log.write(Text(""))
  ```
  Notes: preserves `log.write(Text(..., style="dim"))` for stamp (now prefixed), `Markdown` for response unchanged (`turn.py:192`), `notices` yellow stays but prefixed for grouping. Separator is `Text("─"*40, style="dim")` + blank line; width 40 fits 80-col with margin; if wrapping observed on <60 cols, reduce to 30 or use `Text("─" * max(20, pane.size.width - 4), style="dim")` (keep 40 as default). Extract `_PREFIX = "▎ "` and `_SEPARATOR = "─" * 40` as module constants so the glyph swap (`▎`→`│`) is one edit.

- `src/mlx_tui/chat_pane/turn.py` — in `write_system_line` `223-224` (used by `record_cancelled` `turn.py:218-219` for `esc` cancel):
  ```python
  # before:
  def write_system_line(pane: ChatPane, message: str, style: str) -> None:
      pane.query_one("#chat-log", RichLog).write(Text(message, style=style))


  # after (prefix only dim/cancelled grouping, leave red server errors unprefixed):
  def write_system_line(pane: ChatPane, message: str, style: str) -> None:
      text = f"▎ {message}" if style == "dim" else message
      pane.query_one("#chat-log", RichLog).write(Text(text, style=style))
  ```
  This makes `esc` cancelled turn (`record_cancelled` → `pane._write_system_line("cancelled — request aborted", "dim")` `turn.py:218-219`) satisfy Manual "▎ cancelled — request aborted" while keeping `server unreachable`/`server error` red lines (`style="red"`) unprefixed as system errors. Alternative if all system lines should group: `f"▎ {message}"` unconditionally.

- `tests/integration/test_app_integration.py` — minimal relaxations to keep suite green:
  - `15` `_STAMP_RE = re.compile(r"(?:▎ )?\\d+( \\(est\\))? in · \\d+( \\(est\\))? out · (?:\\d+ prefill tok/s · [\\d.]+ decode tok/s|[\\d.]+ tok/s) · TTFT [\\d.]+s( · cold)?")` — allow optional `▎ ` prefix (so `fullmatch` on `▎ 12 in …` passes after stripping or matching prefix).
  - `172` `test_cancel_closes_stream`: change `any(t.startswith("you ›") for t in harness.log_lines())` → `any(t.lstrip("▎ ").startswith("you ›") for t in harness.log_lines())` (keeps anchoring while allowing `▎ ` prefix; bare `in` would be false-positive if assistant Markdown contained `you ›`).
  - If `test_status_green_and_chat_stamp_over_stub_http` uses `harness.log_lines()` + `_STAMP_RE.fullmatch` on dim line, it now matches because of optional prefix; no other change needed. Keep `test_chat_renders_markdown` `322` `any("hi" in line …)` / `any("bold" in line …)` — still passes (Markdown unchanged).
  - No production code outside `chat_pane` touched for this phase; `src/mlx_tui/history/*` unchanged.

**Success Criteria:**

#### Automated Verification:
- [x] Chat integration: `uv run pytest -v tests/integration/test_app_integration.py -k "test_status_green_and_chat_stamp_over_stub_http or test_cancel_closes_stream or test_chat_renders_markdown or test_prefill_vs_decode_stamp_and_table or test_empty_response_warns_instead_of_silence or test_length_capped_reply_shows_notice or test_chat_payload_carries_effective_model"`
- [x] Whole suite green: `uv run pytest -q` (expect 247 passed)
- [x] Visual grouping present: after stub `hi` turn via `AppHarness`, `assert any(t.lstrip("▎ ").startswith("you ›") for t in harness.log_lines())` and `assert any("▎" in t and "tok/s" in t for t in harness.log_lines())` and `assert any("─" * 10 in t for t in harness.log_lines())` (or at least `▎ you ›` + dim `─` separator exist; add to `test_chat_renders_markdown` or a new tiny `test_chat_grouping_prefix` if preferred).
- [x] Lint: `uv run ruff check .`
- [x] Format: `uv run ruff format --check src tests`
- [x] Types: `uv run pyrefly check`

#### Manual Verification:
- [ ] `uv run mlx-tui`: send `hi` → first line reads `▎ you › hi` in bold, second line `▎ 12 in · 6 out · … tok/s · TTFT …` dim, both with left `▎` visually grouped. Assistant Markdown renders below without `▎`, then a dim `────────────────` rule + blank before next prompt. The `▎` block is clearly separate from previous Markdown and from the next response, but not a heavy box.
- [ ] Two sequential turns show: `… Markdown` → `─ rule` → `▎ you › second prompt` → `▎ stats` → `Markdown` — gap is consistent. `esc` cancelled turn also shows `▎ cancelled — request aborted` grouping.
- [ ] Theme neutrality: no custom hex colors; uses `bold`/`dim`/`yellow` + `▎`/`─`; reads well in both light/dark Textual themes.

## Out of Scope

- Log pane (`RichLog#app-log` `height:6` `dock:bottom`) rethink — explicitly deferred per intake Q4.
- New deps, new `*.tcss`, new `TurnWidget`/`VerticalScroll`, syntax-highlighted Markdown code blocks, history persistence (`JSONL`/`--since`), prompt stats prefill timing changes, `health_timeout`/`fits_headroom`/`ColdTracker` logic, host/port hot-rebind, `textual` version bump.
- Host/port or `start_cmd`/`stop_cmd` behavior, swap state machine, SSE parsing.

## Risks & Mitigations

- `dock: top` ignores `margin` on `TabbedContent` or `#status-bar` → gap invisible → Mitigation: use `TabbedContent{padding-top:1}` (dock-safe; margin on docked `Horizontal` is ignored). `height:1` + `border-bottom` also invalid — border lives inside `height` (see `#app-log`/`#metrics-sparkline` precedent), so keep `height:1` no border; border only if height bumped to 2. Verify manually on 80×24.
- `▎` glyph missing/blank on some terminal fonts → grouping lost → Mitigation: extract `_PREFIX="▎ "` / `_SEPARATOR="─"*40` constants; glyph alternative `│` if `▎` renders blank; fallback is still `bold` + dim `─` rule, so grouping degrades gracefully. Keep `you ›` substring intact.
- `_STAMP_RE.fullmatch` / `startswith("you ›")` break after prefix → Mitigation: relax regex to `(?:▎ )?` and change `startswith` to `lstrip("▎ ").startswith` in tests (listed in Phase 2 Changes); no prod regex changed.
- `RichLog` `Text("─"*40)` wraps on narrow terminals → looks like two rules → Mitigation: width 40 is safe for 60+ cols; if wrapping observed, reduce to 30 or use `Text("─" * max(20, pane.size.width - 4), style="dim")` (verify during impl, keep 40 as default).
- `pyrefly` strict on `Text`/`Markdown` imports → Mitigation: existing imports `from textual.widgets import RichLog, Static` + `from rich.text import Text` + `from rich.markdown import Markdown` already present in `turn.py:11-13`; no new `implicit-any`.
- `ripple` on `HistoryStore`/`MetricsPane` → Mitigation: no changes to `history/store.py` or `metrics_pane.py`; only `app/__init__.py` CSS and `chat_pane` text writes (`__init__.py`, `turn.py` `complete_turn_ui` + `write_system_line`) touched.
