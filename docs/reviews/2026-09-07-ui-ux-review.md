# UI / UX review before Part 3

Date: 2026-09-07. Scope: current working tree, including existing staged changes.
Design review and proposal only; no application code changed.

## Recommendation

Keep the one-screen, keyboard-first MLX control plane and the Models / Chat /
Metrics navigation. Improve hierarchy, recoverability and the meaning of state
before adding another product surface. The original “htop for your MLX server”
idea and Part 3's evidence-backed configuration choice fit the same shell.

Models answers “what can I run?”, Chat answers “how does it behave?”, and Metrics
answers “what happened, and which configuration should I keep?” The last question
becomes actionable in Milestone B. Do not create a separate dashboard, persistent
sidebar, onboarding wizard or fourth Compare tab for this review.

## Evidence and limitations

Read `idea.md`, `part3.md`, the draft Milestone A plan, and the current app, status,
model, search, chat, parameter and metrics components. Rendered all three existing
tabs using Textual's headless pilot at 120×40 and 80×24 terminal cells. Cache rows
and streaming text were synthetic; polling was disabled. These captures establish
layout behavior, not real runtime compatibility, inference performance or user
research findings. Search/recovery observations below are from code inspection.

## Prioritized findings

### 1. Restore usable vertical space — before A

The headless capture at 80×24 leaves `chat-log` one row high. The composer occupies
rows 17–19 while the docked log starts at row 18 (zero-based), obscuring part of
the composer. Even at 120×40, the transcript has only 14 rows in this scenario.

Two concrete causes deserve attention before visual styling:

- [ParamsPane.compose](../../src/mlx_tui/params.py#L77) overrides Collapsible's
  composition, omitting its title and Contents wrapper. The installed Textual
  implementation uses that wrapper to hide children. Consequently the three
  parameter rows render despite `collapsed=True`, without a disclosure title.
- [The shared log](../../src/mlx_tui/app/__init__.py#L63) permanently reserves six
  rows. The main content also extends into its area in the captured layout.

Use the native Collapsible structure; present a one-line parameter summary when
closed. Allocate the workspace explicitly between header, tabs, content and
footer. Collapse idle logs into a one-line Activity summary, with an explicit
expand action. An error must remain visible in the relevant pane or Activity
summary even when detailed logs are closed. Expansion must not steal input focus.

At 80×24 prioritize the composer, active operation and at least a useful handful
of transcript rows. Reduce optional spacing and hide sparklines before hiding
essential controls. More width can reveal detail; it should not introduce another
permanent pane.

### 2. Give responses a stable place — before A; stream semantics in A

[Chat composition](../../src/mlx_tui/chat_pane.py#L84) places the live stream above
the transcript. In the capture the answer appears above its question. On success,
[completion rendering](../../src/mlx_tui/chat_pane.py#L417) clears that live surface
and adds the answer to the log. This is a discontinuity in reading order.

Render the active response after its user prompt, in the same chronological area
where it will remain. Prefer updating that turn in place; a minimal interim
layout can keep the active stream below history with stable scroll behavior.
Keep the composer anchored. Follow new output only while the user is at the
bottom; do not pull them away from earlier text they are reading.

Lead each turn with role/content. Put a short measurement line after the answer;
reserve detailed accounting for Metrics. Show “Waiting for output…” before text
arrives. Integrate reasoning/tool/answer distinctions with A's verified stream
handling, without adding tool execution.

### 3. Make the header describe facts — A

[The status bar](../../src/mlx_tui/status_bar.py#L45) uses a colored dot and one
model slot. That presentation cannot explain the distinction between endpoint
reachability, request selection, server-reported identity and observed response
identity. A green dot invites a stronger “ready and loaded” interpretation.

Use a compact two-line hierarchy when needed:

1. Endpoint state in words, address, and operator ownership/control mode.
2. Selected model, with evidence/source for any observed identity; compact memory
   figures at the right when space permits.

Examples: “Reachable · readiness unknown”, “Unreachable · retry connection”,
“Selected: …”, “Last response: …”. Show mismatch and stale evidence explicitly.
Only show stronger readiness/residency claims when A's compatibility report
supports them. Do not invent a managed-runtime ownership label for today's
command-configured attach workflow. A future Managed label belongs to C.

Replace zero-looking missing memory bars with “RSS unknown”. Label binary values
GiB throughout; current displays divide by 2**30 but label the result GB. Keep
system available memory separate from server RSS; do not imply they partition
physical RAM. At narrow widths move secondary memory detail into disclosure.

### 4. Make keyboard actions discoverable and contextual — before A

The app and table define bindings but the shell composes no Footer or equivalent
shortcut strip. Search, loading, presets and configuration therefore depend on
prior knowledge or the README.

Add a compact contextual footer using the existing binding machinery. Models
should expose Enter Load, / Search and D Delete; Chat should expose Send and,
during generation, Esc Cancel; global help can expose config and preset controls.
Keep destructive actions secondary and retain deletion confirmation.

Make pane navigation discoverable without plain-letter global bindings that
intercept message typing. Choose any additional shortcuts only after checking
Textual and terminal conflicts. Do not build a command palette just for this.

Disabled actions need an adjacent reason and next action: “Loading model…” or
“Endpoint unreachable · check address/config”. Keep the currently relevant
operation visible across tab changes. Cancellation text must distinguish closing
the client request from verified server cancellation.

### 5. Separate model selection, availability and suitability — A; advice in C

[The models table](../../src/mlx_tui/table.py#L34) has model, quant, size, fits and
loaded columns. “fits ✓” is too strong for a heuristic derived from disk size and
available memory. Selection highlight and a filled dot also need different,
explicit meanings.

Keep a dense table. Prefer Model / Quant / Disk / State, with a short selected-row
detail below it containing the full repository ID and memory caveat. If the
current heuristic remains visible, call it a weights-only estimate; otherwise
show “Runtime fit unknown”. Do not substitute an equally confident “Likely fits”
badge without workload evidence. Use “Cached” for actual cache availability and
distinct labels for selected/requested and observed model identities.

The load action should state its actual policy before activation: “Load in
server” or “Restart & load”. Explain disruptive behavior without adding a modal
to every ordinary selection. Retain authorization/control boundaries from A.

Empty cache: “No cached models · / Search mlx-community”. Search should identify
download size as disk space, disclose that the search is limited to
mlx-community, and make search/download/cancelling/failure states explicit.
Completing a download means cached, not loaded or recommended. Suggested models
and supported-profile claims must wait for the evidence required by Part 3.

### 6. Surface settings and recovery at the point of use — small fixes now

Preset selection is currently reported in the shared log, while controls remain
under Params. Show the current preset and a compact next-request settings summary
near the composer; after edits, show “Modified”. Keep TOML and the editor as the
source for configuration rather than building preset management now. Do not call
today's preset a verified reproducible profile: that stronger contract belongs
to B and its pinned identities/effective settings.

[Submitting clears the composer](../../src/mlx_tui/chat_pane.py#L134), and error
cleanup re-enables it without restoring the draft. The attempted prompt remains
in the visible transcript, but editing and resubmitting it becomes unnecessary
work. Preserve a retryable draft for failure/cancellation. Explain which failed
or cancelled turns are excluded from the next request's context.

Show the configured context budget and estimated input/output reservation in
plain language, with an explicit notice when earlier turns are omitted. The bar
must not imply an exact tokenizer count or validated model context limit.
Multiline editing, copy, durable sessions and files remain D features unless a
specific earlier coding-comparison obstacle justifies pulling one forward.

### 7. Make Metrics support a decision — corrections in A, Compare in B

The current [Metrics pane](../../src/mlx_tui/metrics_pane.py#L61) leads with two
sparklines and an eight-column table. It is a useful inspection view, but has no
explicit route from observations to a saved choice.

A: remove unsupported prefill/decode interpretations, use named timing intervals
and units, distinguish missing measurements from zero, and make result status a
first-class field rather than appending “cancelled” to output token counts. Keep
estimates consistently marked with an explained convention. A chronological
multi-model sparkline is activity history, not a controlled comparison.

B: add a Compare action inside Metrics, leading through two pinned profiles,
one coding check and sequential execution. Keep Recent turns available alongside
it. Compare results should prioritize check outcome, client first-output and
completion latency, and attributed sampled RSS. Expand to inspect five warm
trials, range, failures, first-after-load results and metadata. No global score or
automatic winner. Offer Keep A, Keep B, Retain baseline and Reject both, including
inconclusive/limited evidence. Keeping a choice must state any operator restart
needed. Persist comparison evidence and the chosen profile here, not a new
conversation/session subsystem.

## Visual direction

Use terminal-native typography, restrained separators and one focus accent.
Reserve semantic color for warnings/errors/state and always pair it with text.
Give model/answer content the strongest hierarchy; settings and measurements are
secondary. Avoid additional boxes around every subsection. Keep focus visually
distinct from selection and runtime state. Validate light and dark themes and
long model IDs. No font, icon pack or styling dependency is needed for this pass.

The accompanying interactive concept demonstrates the retained three-tab shell,
compact settings, stable response order, contextual hints and an expandable log.
Its Metrics comparison is explicitly a B proposal; all sample states are
illustrative. Browser reflow is explanatory, not a claim that Textual has gained
responsive behavior.

## Suggested sequence and review gates

1. **Small UI repair before A:** fix Collapsible composition, dock/content sizing,
   chronological stream placement, visible shortcuts and idle log density.
   Preserve draft recovery if it can be isolated cleanly. No roadmap expansion.
2. **A:** apply the state vocabulary, units, unknown/estimated labels and verified
   streaming/measurement semantics throughout this shell. Resolve actual labels
   from the compatibility report rather than coding mock values as facts.
3. **B:** build Compare and profile selection in Metrics after A is trustworthy.
4. **C/D/E:** integrate acquisition, proven daily-use improvements and client
   setup into the same navigation only when their existing product gates pass.

For implementation, use focused pilot checks that catch the observed layout
failures: Params really hides its inputs, the composer/context/footer remain
visible at 80×24 and 120×40, and a live response follows its prompt. Manually
exercise tab navigation, long IDs, an empty cache, unavailable endpoint, loading,
failure and cancellation. Check focus stays useful when expanding logs or
returning from search. Run appropriate existing regression checks after code
changes. This review did not run inference contracts or the full test suite.

Before committing to the B design, observe existing MLX operators finding a model,
explaining what the header proves, recovering a failed request and comparing two
configurations without outside instructions. Those sessions test these UX
hypotheses and complement Part 3's existing decision/return-use gates.
