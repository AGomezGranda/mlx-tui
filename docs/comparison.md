# Compare pinned coding profiles

The Compare tab helps an MLX operator choose between two packaged, pinned
coding profiles on the attached local server. It runs one fixed reproduction
task, records the evidence and checkpoints, then lets the operator save or
reuse an explicit choice. It is not a general coding benchmark: generated code
is never executed, and this reproduction task does not establish general coding
ability.

## Task and terminology

Every run uses `coding-check-v1`:

> Synthetic coding check v1. Return exactly this one-line Python function and
> nothing else: `def answer(): return 42`

The expected answer is exactly:

```python
def answer(): return 42
```

A **model** is a repository or snapshot that the server can request. A **coding
profile** is a packaged entry in `src/mlx_tui/coding_profiles.toml`: it gives a
profile ID and name, a pinned repository revision, template/runtime identities,
launch metadata, and the exact request settings. The comparison freezes those
profile entries and their verified cached snapshots into the result. Changing a
candidate for the next run cannot relabel an older result.

Attach remains an operator-managed control plane: Compare does not stop an
attached server, replace its model, or claim that launch settings were applied.
Managed mode is the explicit alternative. It uses the verified app-owned
runtime at `127.0.0.1:18080`, retains the child identity, starts only complete
offline snapshots, and stops that child on quit. Managed readiness requires the
retained PID/create-time to own the listener; a green response from an unknown
process is not enough. Keep and Apply saved profile settings in the TUI, while
any attached-server restart remains an explicit operator action.

## Before starting

- In Attach mode, run a local MLX server on an explicit loopback HTTP endpoint.
  TLS, API keys, remote hosts, and reverse-proxy base paths are outside this
  workflow.
- In Managed mode, use the setup screen to install the pinned runtime, verify
  the two complete snapshots, and choose **Start**. Do not hand-author runtime
  evidence; the app records inspected runtime and owned-launch facts.
- Keep both pinned profile revisions in the local cache. If either snapshot is
  missing, use **Download** to open the pinned-revision download flow, then
  return to Compare.
- Keep the packaged profile catalogue readable. A broken catalogue shows its
  actual error and disables setup/run; opening an existing result remains
  available.
- Record what is known about the machine tier, runtime, installation, launch,
  power/load conditions, and restart/eviction isolation. Enter `unknown` when a
  fact is not known.

The four tabs have separate responsibilities:

| Tab | Responsibility |
| --- | --- |
| **Models** | Browse cache contents, download/delete, and choose the ordinary request target. |
| **Compare** | Set up A/B, check readiness, run, inspect evidence, choose, and reopen results. |
| **Chat** | Send ordinary prompts and keep the in-memory draft/transcript. |
| **Metrics** | Show ordinary chat-throughput and server-memory history. |

The Compare candidate pair never changes the ordinary Models selection, Chat
draft/history, Params, or saved profile state.

## The workflow

### 1. Setup

Open **Compare** and choose distinct **Candidate A** and **Candidate B**. The
pane shows each profile name and ID, pinned revision, cache status, and catalogue
evidence status for the entered machine tier. Expand **Task and exact settings**
to see the prompt, expected answer, and frozen request settings. Expand
**Runtime evidence and test conditions** to review the evidence fields.

Enter the machine tier and operator evidence, or leave each value unknown. This
text is an operator declaration, not independent verification. Unknown,
expired, revoked, withdrawn, or unqualified catalogue evidence may still allow
a runnable experiment, but it cannot earn an advantage recommendation.

If a snapshot is absent, choose **Download**. If the configured server needs to
be launched, choose **Start server**. In Attach mode that label means the
configured cold-start action; it is not a general restart operation. In Managed
mode it starts the selected verified snapshot through the retained manager. Both
actions invalidate a prior readiness check.

### 2. Readiness and run

Press **Check readiness**. It validates the distinct pair, the explicit loopback
endpoint, cached pinned assets and hashes, and the process identity. Managed
readiness additionally verifies the retained child, runtime inspection, and
listener ownership. Readiness is a set of preconditions, not proof that the
runtime applied every profile setting or that a model remains resident.

Press **Run comparison** only after readiness succeeds. The runner executes twelve
sequential slots in this order:

```text
A first request, A repeat 1, A repeat 2, A repeat 3, A repeat 4, A repeat 5,
B first request, B repeat 1, B repeat 2, B repeat 3, B repeat 4, B repeat 5
```

The first request is reported separately from the five repeats. It is a
first-request interval, not a measured cold-load time. Repeat statistics use
passing repeats only. A trial that was never attempted is unknown, not zero.

While running, the operation lease disables setup edits, evidence edits,
decisions, result opening, and profile application. Progress identifies the
current profile and first/repeat slot. **Cancel** or `Esc` requests client-side
cancellation; the latest checkpoint is retained and engine state remains
unknown. A cancelled or failed run needs a new readiness check before another
run.

### 3. Review

The result header names the run ID and the frozen A-then-B or B-then-A order.
The measurement table shows first-request total time, passing repeats out of
five, and repeat total median/range for each profile. The latency conclusion is
subject to a product heuristic: a difference must be at least 5% and all quality,
identity, evidence, and protocol conditions must be eligible. A smaller or
otherwise unsupported difference is inconclusive.

Memory is currently always **inconclusive**. The recorded range is sampled RSS
for the server process while a request was in progress; it is not engine peak,
allocator usage, or per-profile residency and cannot prove a model fits.
Cached-token counts and process identity are displayed as observed fields, not
as a guarantee that every setting was applied.

Expand **Individual trials and failures** and select a row to inspect its state,
quality result/reason, answer and reasoning, response identity, cached tokens,
and RSS sample range/count. Text supplied by the model or operator is rendered
literally; it is not Rich markup and generated code is not run.

### 4. Choose

After a completed result, **Keep A** and **Keep B** use the profiles frozen in
that displayed result, even if the setup selectors now show a different pair.
The app saves the choice first. It then revalidates the packaged fingerprint
and cached assets before applying the profile to the TUI. If application fails
after persistence, the result remains saved. Attach shows the existing
operator-restart guidance; Managed shows **Choice saved; activation failed** with
the actual reason and offers retry or previous-model recovery.

**Keep current settings** maps to the existing `retain` decision. **Reject both**
requires a nonblank reason and maps to `reject`. Both decisions replace the
single reusable saved choice, record no reusable profile, and change no current
settings. Neither restores a historical baseline.

### 5. Reuse and open results

At startup, a committed saved choice and its result are displayed but never
applied automatically. **Apply saved profile** explicitly revalidates its
fingerprint and cached pinned assets, then applies it. A later ordinary model,
parameter, or config change marks the active profile **modified**.

**Go to Chat** only selects the Chat tab and focuses the composer. It sends
nothing and preserves the existing draft and transcript.

Expand **Open saved result**, enter a path, and press **Open result**. `~` is
expanded and the file is validated before it replaces the displayed result. The
opened path becomes authoritative for a later decision, so deciding on a copied
result updates the copy rather than the original. Opening is read-only: it does
not change candidates, saved choice, active settings, or any file.

Completed and partial results can be inspected; partial/running records are not
decidable. A corrupt or invalid path leaves the currently displayed result in
place and shows the error. Pending or mismatched choice transactions remain
uncommitted and are never repaired or auto-applied.

## Keyboard and narrow terminals

Switch tabs by clicking or with the tab bar's arrow-key navigation. `Tab` and
`Shift+Tab` move through controls; `Enter` activates buttons and disclosure
titles. The pane scrolls vertically, stacks candidate fields and actions in a
narrow terminal, and keeps long profile names and paths in readable details.
Completion, failure, cancellation, and resize do not move focus to another
pane. `F2` opens the shared Activity disclosure, `Esc` cancels the live chat or
comparison, and `Ctrl+Q` quits.

## Evidence limits and state

The comparison is a reproducible local experiment, not Milestone B
qualification. Operator-entered evidence is a declaration. Unknown, expired,
revoked, withdrawn, or unqualified evidence blocks an advantage label. The 5%
latency threshold is a product heuristic, not a statistical confidence test.
Memory remains inconclusive because process RSS is not per-profile residency.
Older stored summaries are shown as recorded; the latency arithmetic fix does
not silently recalculate historical JSON. Run a new comparison for a new
summary.

Result checkpoints live under:

```text
$XDG_STATE_HOME/mlx-tui/comparisons/
```

When `XDG_STATE_HOME` is unset, the default is
`~/.local/state/mlx-tui/comparisons/`. The committed choice is the sibling
`choice.json`. These are local JSON records; preserve a result file if it is
needed for review or qualification evidence.

For implementation ownership and storage/transaction contracts, see
[ARCHITECTURE.md](../ARCHITECTURE.md#pinned-profile-comparisons). For the
qualification status and unmet live/product gates, see
[Milestone B compatibility status](compatibility/milestone-b.md).
