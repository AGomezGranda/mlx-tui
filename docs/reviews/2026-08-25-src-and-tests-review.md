# Code review — src/ and tests/ vs docs

**Date:** 2026-08-25
**Scope:** all uncommitted work (`src/mlx_tui/*`, `tests/*`) reviewed against
`docs/idea.md`, `docs/plans/2026-08-25-v0-status-bar-and-chat.md`, and
`docs/plans/2026-08-25-refactor-app-module-split.md`.
**Baseline:** gates verified locally at review time — `pytest -q` 54 passed,
`ruff check .` clean, `pyrefly check` clean.

## Conformance summary

The implementation matches both plan docs closely: module split (`sse` ←
`chat` ← `app`, `status` ← `process` ← `app`) is as specified, moved code is
behaviour-preserving, tests mirror the planned cases almost 1:1, and the two
documented deviations (socket-shutdown cancel workaround, `_HTTP_OK` constant)
are present with their rationale comments. The findings below are gaps *around*
the documented behaviour, not drift from it.

## Findings

### 1. HIGH — Server dying mid-stream crashes the whole TUI

`_run_turn` (src/mlx_tui/app.py:156-170) handles exactly four exception types:
`(httpx.StreamClosed, httpx.ReadError)` and
`(httpx.ConnectError, httpx.TimeoutException)`. A server killed or OOM-ing
**during** generation raises `httpx.RemoteProtocolError` from
`iter_lines()` — confirmed empirically against a raw socket that truncates a
chunked SSE stream:

```
0.4s raised: httpx.RemoteProtocolError - malformed chunk footer … (expected b'\r\n')
```

`RemoteProtocolError` sits under `ProtocolError`, not `ReadError`
(httpx hierarchy verified), so it matches no clause, propagates out of the
thread worker, and hits Textual's `@work` default `exit_on_error=True`
(worker.py:149) → `WorkerFailed` → `app._handle_exception` (worker.py:382) →
crash screen / exit. This contradicts the idea doc's "not a crash" stance and
Phase 3's "red system line instead of a crash".

Trigger realism: high. Babysitting a server that gets killed mid-turn is this
app's core scenario. Note the same gap affects the **cancel path**: if the
socket shutdown races in-flight bytes, the induced error may surface as
`RemoteProtocolError` rather than clean EOF/`StreamClosed`, skipping the
`if self._cancel_requested` branches entirely.

**Fix:** add `httpx.RemoteProtocolError` (or broaden to `httpx.TransportError`)
to the handled set, keeping the `_cancel_requested` check first so cancel still
yields the dim notice.

### 2. HIGH — Non-SSE error responses produce fake successful turns

`ChatClient.stream_turn` (src/mlx_tui/chat.py:64-92) never checks
`response.status_code` and never calls `raise_for_status()`. A 4xx/5xx JSON
error body (what mlx-lm/FastAPI returns for e.g. a failed model load) parses as
zero matching chunks, so the turn "completes" successfully. Confirmed against a
stub returning `500 {"detail": …}`:

```
TurnResult(full_text='', ttft=0.031…, tok_in_str='3 (est)',
           tok_out_str='0 (est)', tok_s=0.0)
```

The UI then stamps `3 (est) in · 0 (est) out · 0.0 tok/s · TTFT 0.03s` — a
bogus measurement recorded as if real. For a tool whose product *is* the
instrumentation, silent fabricated numbers are worse than an error line.
Neither unit tests nor the stub server exercise non-200 POST responses, so
nothing pins the correct behaviour either.

**Fix:** raise on unexpected status (e.g. `response.raise_for_status()` inside
the stream context, or explicit `status_code != 200` check) so the existing
error mapping in `app.py` turns it into a red system line. Add a stub mode +
unit test for the non-200 case.

### 3. MEDIUM — Failed/cancelled turns leave stale text in the streaming pane

`_update_stream("")` runs only on the success path
(`_complete_turn_ui`, src/mlx_tui/app.py:202). Neither the
`cancelled`/`unreachable` except paths (app.py:156-170) nor the early-return
cancel path (app.py:144-148) clear `#chat-stream`. If a turn dies after at
least one throttled flush (~100 ms into streaming), the half-finished reply
stays rendered above the scrollback indefinitely — it looks like a live reply
sitting next to a red `server unreachable` line, and persists across turns
until the next successful flush overwrites it.

Related narrow race: Esc landing between the last SSE chunk being processed and
the `if self._cancel_requested` check (app.py:144) discards a **fully received**
reply and writes `cancelled — request aborted` instead of completing the turn —
also without clearing the stream pane.

**Fix:** clear the stream `Static` in `_end_turn_ui` (or in every exit path),
which also covers the race.

### 4. LOW — Input retains its value after submit; Enter re-sends duplicates

Textual's `Input.action_submit` does not clear the widget (verified in
textual 8.2.8 `_input.py`), and `_on_input_submitted` (src/mlx_tui/app.py:115)
never clears it either. After a turn completes, pressing Enter again re-appends
the identical message to `self.messages` and starts a duplicate turn. Typical
chat UX clears after submit.

**Fix:** `event.input.clear()` after capturing the text (or document
edit-and-resend as intended).

### 5. LOW — Cached pid is never re-validated against pid recycling

`ServerProcessFinder.find` (src/mlx_tui/process.py:23-29) validates the cache
with `psutil.Process(pid).is_running()`, which is existence-only. If the server
dies and the OS recycles the pid to an unrelated process, every subsequent poll
reports that process's RSS (and no model) until the TUI restarts. The
scan-once policy itself matches the v0 plan; this is just the known cost of it.
Rare on macOS, but cheap to harden: on cache hit, confirm the cmdline still
ends with one of `_SERVER_TOKEN_SUFFIXES` before trusting it.

### 6. LOW — `test_active_response_exposed` pins a weak invariant

tests/test_chat.py:121-128 asserts `client.active_response is not None`
*after* `stream_turn` returns — at which point the two context managers have
already closed the response. The seam only matters mid-stream (that's when
`action_cancel_chat` reads it), so the test would still pass if the assignment
moved somewhere useless (e.g. after the loop). It guards against removal, not
misplacement. Consider asserting during streaming via a flush callback, e.g.
`on_flush=lambda _: self.assertIs(client.active_response, ...)`-style capture.

### 7. INFO — `data:` frames require the trailing space

`iter_sse_data` (src/mlx_tui/sse.py:24) accepts only lines starting with
`"data: "` (with space). RFC-legal SSE also permits `"data:{json}"` without a
space. mlx-lm always emits the spaced form (per the plan's upstream research),
so this is correct for the target server — flagging only so the module
docstring's "OpenAI-style" claim isn't read as full SSE-spec compliance.

## Not flagged (checked and intentionally OK)

- pytest/ruff/pyrefly living in `[project].dependencies` — explicitly
  out-of-scope in the refactor plan.
- psutil scan blocking the event loop on cache miss — accepted trade-off,
  documented in the v0 plan.
- Stamp-above-reply ordering, `max_tokens=512`, no `model` field, TTFT/tok-s
  denominators, cold arming rule, amber/green/red classification, overlap
  guard — all verified to match the plan docs verbatim.
