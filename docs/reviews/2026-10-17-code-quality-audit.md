# Code quality audit — 2026-10-17

The highest priorities are cancellation during the pre-send save, which can strand the shared operation lease, and Clear reporting a successful save without replacing the persisted transcript. This audit confirms nine findings: two P1 and seven P2, all high confidence. Two additional correctness defects are recorded separately.

## Scope and model

Audited the current working tree, including existing uncommitted changes, rather than only a diff. The model is a local Textual MLX control plane: explicit request selection and observed generation evidence are separate; `OperationCoordinator` owns one complete operation; `ChatSession` stores attempts and drafts; comparisons preserve pinned `CodingProfile` snapshots and explicit choices. Evidence: [README](/Users/alvarogomez/code/python/mlx-tui/README.md:3), [architecture contracts](/Users/alvarogomez/code/python/mlx-tui/ARCHITECTURE.md:9), [operation ownership](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/operations.py:21), [session models](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/sessions/models.py:16).

Judged against the repository's strict [Pyrefly configuration](/Users/alvarogomez/code/python/mlx-tui/pyproject.toml:39), validated persistence boundaries, and documented cleanup/durability contracts. SOLID, DRY, typing and error handling were checked; the KISS rubric was consulted, but pure simplification was excluded under the audit skill's explicit boundary. Coverage is detailed below; sampled UI areas are not claimed exhaustively clean.

## Prioritized findings

### Error handling

| Priority | ID | Consequence | Effort |
| --- | --- | --- | --- |
| P1 | CQ-CHAT-01 | Cancellation during persistence leaves chat and other operations blocked | Moderate |
| P1 | CQ-CHAT-02 | Clear reports saved while old conversation remains on disk | Small–moderate |
| P2 | CQ-CHAT-03 | Running checkpoints omit streamed output | Small–moderate |
| P2 | CQ-COMP-02 | Failed comparison restoration strands the global lease | Small |
| P2 | CQ-RUNTIME-02 | Warm loads time out after five seconds despite a larger supplied budget | Small |
| P2 | CQ-CONFIG-01 | Invalid UTF-8 config escapes the recovery contract | Small |

### Typing and boundary validation

| Priority | ID | Consequence | Effort |
| --- | --- | --- | --- |
| P2 | CQ-COMP-01 | Malformed saved results enter typed state and can crash decision controls | Moderate |
| P2 | CQ-RUNTIME-01 | Malformed completion responses count as verified generation | Small |
| P2 | CQ-RUNTIME-03 | Malformed installed metadata prevents runtime repair | Small |

All findings below have **high confidence** and **top-line verification**: the parent read the full implicated implementation file, traced relevant callers, and considered tests and reproduction evidence. This is deeper than checking that the quoted line exists. Effort is separate from severity.

### CQ-CHAT-01 · P1 · Cancellation bypasses pre-send cleanup

**Evidence:** [turns.py:314](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/chat_ui/turns.py:314): `saved = await _chat_persistence._save_session_now(pane)`. This await precedes the `try` whose `finally` calls `pane.end_turn()`.

**Contract and consequence:** Every worker exit must settle the attempt and release the chat lease after cleanup. `_turn_started` is already true, so Escape cancels the worker during this await. `CancelledError` bypasses the later cleanup, leaving the composer disabled, the turn active, and other operations blocked.

**Counterevidence:** A save returning `False` explicitly calls `end_turn()`, but cancellation does not return `False`. Existing cancellation tests cover before coroutine startup, headers and streaming. A controlled blocked-save reproduction observed zero `end_turn()` calls after cancellation.

**Correction:** Put pre-send persistence inside the worker's guaranteed cleanup scope. Handle cancellation there, and retain ownership until an already-started threaded write finishes; merely moving the `finally` must not allow overlapping writes after cancellation.

### CQ-CHAT-02 · P1 · Empty snapshots falsely acknowledge persistence

**Evidence:** [persistence.py:197](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/chat_ui/persistence.py:197): `if not _has_meaningful_content(pane, snapshot):`, followed by `pane._saved_revision = pane._save_revision` and a successful return without a write. [Clear](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/chat_ui/persistence.py:586) trusts that return.

**Contract and consequence:** Clear must durably replace the saved session before reporting success. Clearing a conversation with an empty composer removes its visible attempts but leaves the original file; reopening restores them. The meaningful-content predicate also ignores attachment-only drafts.

**Counterevidence:** Avoiding creation of untouched empty sessions is intentional, but it does not justify skipping an explicit update to a persisted session. The existing Clear integration test checks memory rather than reopening the file. A real-store reproduction returned `in_memory_attempts=0, disk_attempts=1, saved_revision=1, revision=1, save_failed=False`.

**Correction:** Distinguish never-persisted empty sessions from updates. Make Clear save its exact cleared snapshot, without repopulating it from the old composer/attachments, and count attachments as meaningful draft content.

### CQ-CHAT-03 · P2 · Progress checkpoints save stale attempts

**Evidence:** [turns.py:736](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/chat_ui/turns.py:736): `_chat_persistence._save_session_async(pane)`. The [snapshot builder](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/chat_ui/persistence.py:178) replaces draft, attachments, settings and timestamp, but does not merge `_progress_*` into the active attempt.

**Contract and consequence:** Periodic checkpoints should preserve received partial answers, reasoning, tool fragments and response identity. They instead save the original empty running attempt. An interruption loses all streamed output even after multiple successful checkpoint writes.

**Counterevidence:** Terminal `_finalize_session_attempt()` does merge these fields; normal completion therefore works. A running-progress reproduction scheduled a checkpoint with `live_answer='partial answer'`, while its save snapshot contained `answer=''`, `reasoning=''`, and `response_model=None`.

**Correction:** Merge accumulated progress into the active attempt when taking the running checkpoint snapshot, preserving its `running` outcome.

### CQ-COMP-02 · P2 · Restoration failure prevents lease release

**Evidence:** [workflow.py:292](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/compare/workflow.py:292): `pane.tui.restore_request_state(previous_config, previous_model)` runs immediately before `pane.tui.operations.release(OperationKind.COMPARING)`.

**Contract and consequence:** Cleanup callbacks must not strand operation ownership. Restoration invokes parameter, context, session and model callbacks that can raise. If it does, release never runs: other operations remain blocked and polling remains suppressed, although the worker handle has already been cleared.

**Counterevidence:** The sequence is inside a `finally`, but its individual statements are not guaranteed after an earlier statement raises. A fault-injection reproduction using the real coordinator ended with `current=comparing` and worker handle `None`. Existing integration coverage verifies successful restoration.

**Correction:** Guarantee lease release independently of restoration, logging and rendering, using nested cleanup. Restore controls best-effort and report the original failure.

### CQ-COMP-01 · P2 · Decoder admits invalid typed result state

**Evidence:** [decoding.py:398](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/comparison/decoding.py:398): `prompt_tokens=table.get("prompt_tokens")`; nearby nullable strings/booleans are also passed unchecked. The decoder checks fingerprints but not the complete two-profile/trial structure.

**Contract and consequence:** Successful decoding must establish the invariants consumers rely on before [replacing the active result](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/compare/workflow.py:368). Wrong token types are presented as observations. A completed result with no profiles is accepted, then [Keep](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/compare/decisions.py:44) indexes a missing profile outside its handler. Duplicate trial keys can reach table insertion.

**Counterevidence:** Required strings, timing values, selected enums, unknown fields and fingerprints are checked; dataclass constructors do not validate the remaining fields/cardinality. In-memory probes accepted `prompt_tokens='invalid count'`, `quality_pass='yes'`, zero profiles with zero fingerprints, and duplicate trial keys. Accessing the accepted Keep slot raised `IndexError`.

**Correction:** Validate nullable scalar types, two distinct profiles, aligned input arrays, and unique valid trial keys before returning domain state. Preserve legitimate partial runs while enforcing completed-result decision invariants.

### CQ-RUNTIME-01 · P2 · Truthy choices are treated as valid generation

**Evidence:** [serverctl.py:408](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/serverctl.py:408): `if not isinstance(body, dict) or not body.get("choices"):`.

**Contract and consequence:** `warm_load()` promises rejection of odd responses; its callers use it as actual generation verification. A matching model plus any truthy `choices` passes, so malformed responses can mark a load verified.

**Counterevidence:** Model identity is checked and missing/empty choices are rejected. Some existing fixtures intentionally use `[{}]`, but those fixtures do not establish a valid completion. Mock HTTP probes with `choices=true`, `choices="invalid"`, and `choices=[{}]` all produced green, target-matched probes through `wait_healthy()`.

**Correction:** Require a nonempty list of correctly shaped completion choices. Retain the expected one-token `length` outcome; do not apply normal full-chat completion requirements to the load probe.

### CQ-RUNTIME-02 · P2 · Warm-load response timeout ignores its budget

**Evidence:** [serverctl.py:390](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/serverctl.py:390): `read=min(5.0, max(0.1, timeout_s)),`.

**Contract and consequence:** The warm-swap caller supplies a model-size-dependent load allowance, but any nonstreaming response taking more than five seconds is reported as a timeout even if the configured allowance is much longer. The server may still be loading when the UI reports failure and releases the lease.

**Counterevidence:** Startup `wait_healthy()` retries; direct `run_warm_swap()` calls once and reports failure. A short connect timeout is sensible and does not require the same response timeout. Inspection of the effective HTTP request showed `timeout_s=120` becoming `read=5.0`.

**Correction:** Honor the supplied response-read budget. If startup cancellation requires a separate short probe interval, make that caller policy explicit rather than silently applying it to direct warm loads.

### CQ-RUNTIME-03 · P2 · Invalid metadata escapes the repair error contract

**Evidence:** [inspect.py:177](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/managed/inspect.py:177): `direct.get("vcs_info", {}).get("commit_id")`.

**Contract and consequence:** Invalid installed metadata must become `ManagedRuntimeError`, which the installer interprets as requiring repair. A `vcs_info` value of null or another non-object instead raises `AttributeError`, aborting Install / repair before it repairs anything.

**Counterevidence:** The enclosing `direct` mapping is checked, but the nested value is not. [Installation](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/managed/install.py:105) handles only `ManagedRuntimeError` at this boundary; its outer UI reports the failure without repairing it. A temporary-runtime reproduction with subprocess seams mocked produced `AttributeError` and zero repair commands.

**Correction:** Validate/narrow the nested mapping and translate malformed metadata to `ManagedRuntimeError`, preserving its cause when wrapping an exception.

### CQ-CONFIG-01 · P2 · Invalid encoding bypasses config recovery

**Evidence:** [config.py:193](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/config.py:193): `except (OSError, tomllib.TOMLDecodeError) as exc:`.

**Contract and consequence:** `parse_config()` promises a `ConfigParseError` for unreadable/invalid content, and `load_config()` promises defaults for bad content. Invalid UTF-8 raises `UnicodeDecodeError` instead. CLI startup calls `load_config()` before opening the repair UI, and reload catches only `ConfigParseError`, so this corrupt-file case escapes both recovery paths.

**Counterevidence:** Malformed TOML and missing files already follow the expected contract. A temporary file containing `b'host = "\xff"'` made both `parse_config()` and `load_config()` raise `UnicodeDecodeError`; no live config was touched.

**Correction:** Include decoding errors in the translated config-read failures, retaining `raise ... from exc`.

## Explicitly fine

- **SOLID:** No confirmed violation in the reviewed contracts. The comparison presenter returns text/rows without owning widgets or worker lifecycle ([presenter.py](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/compare/presenter.py:1)); managed runtime ownership retains the actual child and identity ([runtime.py:136](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/managed/runtime.py:136)).
- **DRY:** No confirmed duplicated business-rule defect. Attachment preview and request content share [render_user_content](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/attachments.py:78); download sizing and download selection share `ALLOW_PATTERNS` in [search.py](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/search.py:104).
- **KISS:** No eligible finding. [SearchCache](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/catalog/cache.py:11) protects locking, expiration and copy isolation, so its wrapper has a concrete purpose. Helper/file counts alone were not treated as defects.
- **Typing, bounded:** [Profile validation](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/profiles.py:269) rejects mutable revisions, invalid hashes, booleans as numbers and invalid budgets. [Session reference validation](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/sessions/codec.py:474) rejects invalid prior-turn references.
- **Error handling, bounded:** The [choice transaction](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/comparison/store.py:153) distinguishes pending and committed states. Chat transport uses managed client/response contexts; ordinary stream failure does not silently commit successful history.

## Out of scope, flagged anyway

These are confirmed adjacent correctness defects, not forced into a SOLID/DRY/typing category. Each was reproduced and the parent read its full implementation file. A focused behavior review or ponytail-review is the appropriate follow-up; no separate architecture/security/performance finding is asserted.

- **CQ-CHAT-X1 · P2, high:** [store.py:248](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/sessions/store.py:248) reconstructs `ChatSession` while converting `running` attempts to `interrupted`, omitting draft attachments. Reproduction: one saved draft attachment becomes zero on reopen. Ordinary non-running loads preserve it. Use `replace(session, attempts=...)` to preserve unrelated fields.
- **CQ-CHAT-X2 · P2, high:** [persistence.py:480](/Users/alvarogomez/code/python/mlx-tui/src/mlx_tui/chat_ui/persistence.py:480) confirms temporary→durable discard, then calls `_start_empty_session()` with unchanged state. Approval queues another confirmation indefinitely. `_confirm_discard()` clears errors, not temporary state/content. Execute the approved transition directly or carry an explicit confirmation state.

## Verification, coverage and unresolved concerns

**Checks run:** `pyrefly check --min-severity warn` reported zero diagnostics, with 285 suppressed. The focused unit run covering config, sessions, comparison, server control and managed runtime passed **175/176** tests. `test_terminate_failed_process_cleans_term_ignoring_grandchild` failed at `tests/unit/test_serverctl.py:349`, and failed again in isolation. The first observed PID was absent on subsequent inspection; the root cause remains unresolved. Next probe: capture descendant PID/create-time/status through TERM/KILL and reaping to distinguish a surviving process from a zombie or timing issue. Do not describe the suite as green.

Isolated reproduction probes exercised save/cancellation state, JSON boundaries and HTTP mocks; they did not use a live MLX server or personal session/config files. No application or test code was changed. This report is the only artifact added by this audit.

| Area | Coverage and dimensions |
| --- | --- |
| `chat.py`, `sse.py`, `chat_ui/*`, `chat_turn.py`, `sessions/*`, `attachments.py`, `history/tokens.py` | Full source reads and relevant callers; SOLID, DRY, typing and errors checked; KISS screened under the boundary above |
| `comparison/*`, `compare/*`, `profiles.py`, `presets.py` | Full sources inspected, result-loading/decision/cleanup callers traced; all applicable dimensions checked |
| `managed/*`, `catalog/*`, `serverctl.py`, `boot.py`, `swap.py`, `process.py`, `models.py`, `search.py` | Full sources read; all applicable dimensions checked; relevant tests/callers sampled |
| `app/*`, `config.py`, `params.py`, `json_util.py`, `operations.py`, `status.py`, `history/store.py` | State, config, lifecycle and persistence contracts checked; app endpoint-preview presentation sampled |
| Models/discovery/setup/metrics/session screens and remaining presentation helpers | Relevant lifecycle/error/boundary paths sampled; exhaustive SOLID/DRY/presentation analysis unverified |
| Tests | Relevant assertions used as counterevidence; five unit files executed; integration tests inspected selectively but not run |
| Documentation/resources/build tooling | Used for contracts and conventions; not comprehensively audited |
| Architecture, security, performance, dependency health, test strategy | Excluded by skill scope |
| Live MLX, Hub, installation, downloads, full test suite | Unverified; no live operations performed |

Additional unresolved leads, excluded from the confirmed findings:

- Attached comparison trials fall back to the preflight process identity when fresh discovery returns `None`. Establish whether stale identity is intentionally permitted before classifying it; next probe is unavailable discovery after valid preflight and inspection of the recorded evidence.
- Session decoding permits some success-labeled tool-only records that live chat does not produce. Trace supported producers and reopening behavior before claiming a broken invariant.

Widespread `Any` annotations were not counted as defects without a demonstrated invalid state or caller-visible consequence. Rejected speculative findings are intentionally omitted.
