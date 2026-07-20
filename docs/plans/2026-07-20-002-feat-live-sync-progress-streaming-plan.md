---
title: feat: Live Sync Progress Streaming — structured entity tracking + live proposals via SSE
type: feat
status: active
date: 2026-07-20
origin: docs/brainstorms/live-sync-progress.md
---

# Live Sync Progress Streaming

## Overview

Replace the raw terminal output stream in the web UI's Sync tab with **structured progress events** powered by `ingest_journal.run_ingest()`'s existing callback system. This gives reviewers real-time visibility into which entities are being processed, how many remain, and live proposal cards appearing in the New tab — all without page refresh.

The CLI (`python -m kanka_wiki_updater.sync_pipeline`) continues to work unchanged by wrapping `run_ingest()` with terminal-print callbacks (no behavior change).

---

## Problem Frame

The Sync tab spawns a subprocess, reads stdout line-by-line into a deque, and SSE-streams raw text. Reviewers see scrolling journal names but **cannot** tell which entities are being processed, how many remain, or what proposals have been generated — they just stare at scrolling terminal output.

The ingest engine (`ingest_journal.py`) already has a callback system with 7 event types (`entity_started`, `llm_result`, `proposal_queued`, `new_entity_suggestion`, `journal_completed`, `sync_started`, `sync_completed`), but the web backend never uses it — it parses stdout text instead.

**Goal:** Call `run_ingest()` in-process (in a background thread) with SSE-emitting callbacks that push typed JSON events to the browser, enabling entity-level progress cards and live proposal insertion.

---

## Requirements Trace

- R1. `ingest_journal.py` extracts core logic into a function accepting callback parameters — **already done** (`run_ingest(callbacks=...)`).
- R2. The ingest engine emits five event types: entity started, LLM response received (success/fail), proposal written to queue, new-entity suggestion emitted, and journal completed with summary counts — **already defined**.
- R3. CLI entry point continues to work unchanged by wrapping the ingest engine with terminal-print callbacks — **already done** (`main()` calls `run_ingest(limit=limit)` which uses `_default_callbacks()`).
- R4. Web backend invokes `ingest_journal.py` directly in a background thread (not subprocess) and registers callbacks that emit typed SSE events — **new work**.
- R5. `/api/sync/output` SSE endpoint emits structured JSON events (`{"type": "entity_start", ...}`, etc.) instead of raw terminal text — **new work**.
- R6. New proposals are pushed via the same SSE stream with a distinct event type so the browser can append them live to the "New" tab without page refresh — **new work**.
- R7. UI displays an entity list grouped by journal showing: entity name, source journal, status indicator (pending/processing/done/error), and error message on hover/click — **frontend only; plan covers backend data shape**.
- R8. Completed entities visually distinguished from pending ones; list scrolls/paginates as new journals are discovered — **frontend only**.
- R9. Ingest engine supports graceful cancellation: in-progress LLM calls interrupted (or allowed to finish and discarded), completed proposals remain, entity cards reflect final statuses — **new work**.

---

**Origin actors:** A1 (Reviewer)
**Origin flows:** F1 (Run sync with live entity tracking), F2 (Cancel a running sync)
**Origin acceptance examples:** AE1 (Covers R4, R5, R7), AE2 (Covers R5, R7), AE3 (Covers R9)

---

## Scope Boundaries

- **In scope:** Backend changes to `review_web.py` (subprocess → in-process thread with SSE-emitting callbacks).
- **In scope:** Entity progress state tracking shared between the ingest thread and the SSE generator.
- **In scope:** Cancellation support via a shared `threading.Event` flag checked between journal batches.
- **Not in scope:** Frontend UI changes (entity cards, live proposal insertion) — those are separate frontend tasks that consume the new data shapes defined here.
- **Not in scope:** Pagination or virtual scrolling for large entity lists (>100 entities).
- **Not in scope:** Changing `pending_changes.json` schema or proposal data format.
- **Not in scope:** Live editing of proposals during sync.
- **Not in scope:** TUI implementation (future work — the callback interface is already designed to be reusable).

---

## Context & Research

### Relevant Code and Patterns

| File | Pattern to follow |
|------|------------------|
| `kanka_wiki_updater/ingest_journal.py` | **Existing** callback system with `_default_callbacks()` — 7 event types, dict merge pattern (`cbs.update(callbacks)`) |
| `kanka_wiki_updater/review_web.py` | SSE infrastructure: `_sync_jobs` dict, `_sync_lock`, `_sync_cancel_lock`, `/api/sync/output` generator function |
| `tests/conftest.py` | Fixtures: `mock_env`, `mock_requests`, `app_with_queue`; use `tmp_path` for file I/O tests |
| `tests/test_review_web.py` | Class-based test organization; mock KankaClient via `patch()` at module path |

### Institutional Learnings

- **Subprocess + SSE pattern** in `review_web.py` is well-structured (daemon thread, bounded deque, 200ms polling, GeneratorExit handling) — replicate the threading model but replace stdout parsing with direct function callbacks.
- **Cancellation via terminate→kill cascade** exists for subprocesses; for in-process work, use a shared `threading.Event` checked between journal batches instead of OS signals.
- **state.py lacks atomic writes** — single `open(path, 'w')` without temp-file-then-rename. This is pre-existing and not changed by this plan (out of scope).
- **No tests for the callback system** in `ingest_journal.py`. New tests should cover custom callbacks receiving correct event payloads.

### External References

- Kanka API v1 docs: https://app.kanka.io/api-docs/1.0/overview (unchanged)

---

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Invocation model | In-process thread calling `run_ingest()` directly | Enables structured callbacks instead of parsing stdout text; SSE events are typed JSON, not raw terminal output. Aligns with brainstorm decision (Approach A). |
| Cancellation mechanism | Shared `threading.Event` flag checked between journal batches in `run_ingest()` | No OS signal needed for in-process work. In-flight LLM calls finish but no new ones start. Simpler than process-kill cascade; partial results preserved. |
| SSE channel architecture | Single connection with event-type discrimination (`entity_progress`, `proposal_pushed`, `status`) | Fewer connections to manage; client routes by event type. Deferred question from brainstorm resolved in favor of simplicity. |
| Entity progress state | Shared mutable dict `{entity_id: {name, journal_name, status, error}}` protected by `_sync_lock` | Simple and fast for the expected scale (dozens to low hundreds of entities). No need for a separate event bus — callbacks update the dict directly under lock. |
| Test strategy | Unit tests for callback system + integration test for web SSE endpoint with mocked KankaClient | Follow existing patterns: `tmp_path` for state files, mock KankaClient via `patch()`, class-based organization. |

---

## Open Questions

### Resolved During Planning

- **How exactly cancellation interrupts an in-flight LLM call?** — Use a shared `threading.Event` flag (`_sync_cancelled`) checked by `run_ingest()` between journal batches (before each journal's processing starts). In-flight LLM calls are allowed to finish but no new ones start. This is simpler than process-kill and preserves partial results cleanly.
- **Single vs separate SSE connections?** — Single connection with event-type discrimination (`entity_progress`, `proposal_pushed`, `status`). Client routes by type. Simpler than managing two connections; the browser can listen to one EventSource and dispatch based on `event` field.
- **Pagination for large entity lists?** — Deferred to implementation. The backend emits all entity progress events; the frontend shows all journals processed so far. Virtual scrolling/pagination is a future optimization.

### Deferred to Implementation

- Exact names of SSE event type strings (e.g., `"entity_progress"` vs `"entity_start"`). Choose for consistency with existing `event: message` naming convention.
- Whether `_sync_cancelled` should be a module-level variable or passed as a parameter to `run_ingest()`. Module-level is simpler; parameter is more testable. Decide during U2 implementation.

---

## Implementation Units

### - [ ] U1. **[Design entity progress state model and SSE event schema]**

**Goal:** Define the shared data structures for entity progress tracking and the JSON shapes of all SSE events that will replace raw terminal text.

**Requirements:** R4, R5, R7

**Dependencies:** None (foundational)

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (per-job entity progress state in `_sync_jobs[job_id]['progress']`, event schema constants, helper functions)
- Test: `tests/test_sse_event_schema.py` (new — unit tests for event shape validation and entity progress state management)

**Approach:**
1. Add per-job entity progress state scoped to `_sync_jobs[job_id]['progress']` (a dict keyed by `(journal_name, entity_name)` tuples). Each value is a dict: `{name, journal_name, status (pending|processing|done|error), error_message?, source_journal_url?}`.
2. Define event type constants as module-level strings for consistency: `EVENT_ENTITY_PROGRESS = "entity_progress"`, `EVENT_PROPOSAL_PUSHED = "proposal_pushed"`, `EVENT_STATUS_CHANGE = "status_change"`.
3. Add a helper `_emit_sse(event_type, data)` that serializes to SSE format — this mirrors the existing `f'event: message\ndata: {json.dumps(...)}\n\n'` pattern used in the current generator but with typed events.

**Patterns to follow:**
- Existing SSE serialization in `/api/sync/output` generator (`yield f'event: ...\\ndata: ...\\n\\n'`)
- `_sync_lock` usage for protecting shared mutable state (already exists)

**Test scenarios:**
- **Happy path:** Creating an entity progress entry sets status to `"pending"`; updating it to `"processing"`, `"done"`, or `"error"` each produce correct dict shapes.
- **Edge case:** Concurrent updates from ingest thread and SSE generator happen on the same dict — verify lock protects against torn reads/writes.
- **Integration:** `_emit_sse("entity_progress", {...})` produces valid SSE-formatted string with correct `event:` prefix and JSON-encoded `data:` body.

**Verification:**
- All event shape functions produce valid SSE strings parseable by a simple Python SSE parser.
- Entity progress state updates are thread-safe under concurrent access (verified by test with threading).

---

### - [ ] U2. **[Wire review_web.py to call run_ingest() in-process with SSE-emitting callbacks]**

**Goal:** Replace the subprocess-spawning `/api/sync/run` endpoint with an in-thread invocation of `ingest_journal.run_ingest()` that uses callbacks emitting typed SSE events.

**Requirements:** R4, R5

**Dependencies:** U1 (entity progress state and event schema)

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (`run_sync()`, `_sync_thread()`, imports)
- Test: `tests/test_review_web_ingest.py` (new — integration tests for in-process ingest invocation via Flask test client, with mocked KankaClient and SSE output validation)

**Approach:**
1. Replace the subprocess.Popen call in `/api/sync/run` with an import of `run_ingest` from `ingest_journal`.
2. Create a `_SSECallbackEmitter(job_id)` class that wraps the existing SSE infrastructure: it holds references to `_sync_jobs[job_id]['buffer']` and emits typed events via `_emit_sse()`.
3. Register callbacks on `run_ingest(callbacks=callbacks_dict)`:
   - `entity_started`: update entity progress state → emit `EVENT_ENTITY_PROGRESS` with status `"processing"`
   - `llm_result`: update entity progress to `"done"` or `"error"` → emit `EVENT_ENTITY_PROGRESS`
   - `proposal_queued`: emit `EVENT_PROPOSAL_PUSHED` with the full proposal dict (so frontend can append it live)
   - `new_entity_suggestion`: emit `EVENT_PROPOSAL_PUSHED` for new entity proposals
   - `journal_completed`: emit `EVENT_ENTITY_PROGRESS` for journal-level summary; update all pending entities in that journal to `"done"`
   - `sync_started`: initialize entity progress state with all discovered entities at status `"pending"` (requires a pre-computation step or lazy initialization)
   - `sync_completed`: emit `EVENT_STATUS_CHANGE` with final counts and set job status to `"completed"`

4. The background thread no longer reads stdout — instead it calls `run_ingest()` directly, which runs synchronously in the thread. Thread lifecycle: start → call `run_ingest()` → wait for completion → update job status (same pattern as current `_sync_thread`).

5. Keep the existing SSE generator (`/api/sync/output`) mostly unchanged — it already drains a buffer and yields typed events. The only change is that event types are now structured JSON instead of raw terminal text lines.

6. Update `cancel_sync()` to work with in-process jobs: remove the subprocess.terminate()/kill() cascade; replace with setting `_sync_cancelled.set()` (the threading.Event from U3). Keep status mutation under `_sync_cancel_lock` for thread safety.

**Technical design:**
```python
# In /api/sync/run (simplified sketch)
def run_sync():
    job_id = _next_job_id()
    
    # Per-job entity progress state (prevents clobbering on concurrent jobs)
    _sync_jobs[job_id]['progress'] = {}
    
    def make_callbacks(job_id):
        emitter = _SSECallbackEmitter(job_id)
        
        def on_entity_started(entity_name, journal_name):
            key = (journal_name, entity_name)
            if job_id not in _sync_jobs or _sync_jobs[job_id]['progress'] is None:
                return  # job was removed while callback was active
            with _sync_lock:
                _sync_jobs[job_id]['progress'][key] = {'name': entity_name, 'journal_name': journal_name, 'status': 'processing'}
            emitter.emit('entity_progress', {...})
        
        # ... other callbacks follow same pattern
        
        return {
            'entity_started': on_entity_started,
            'llm_result': ...,
            'proposal_queued': lambda p: emitter.emit('proposal_pushed', p),
            'new_entity_suggestion': lambda s: emitter.emit('proposal_pushed', s),
            'journal_completed': ...,
            'sync_started': ...,
            'sync_completed': ...,
        }
    
    # Store job state (same structure as before for SSE compatibility)
    _sync_jobs[job_id] = {
        'status': 'running',
        'started_at': time.time(),
        'finished_at': None,
    }
    
    thread = threading.Thread(
        target=_run_ingest_thread, 
        args=(job_id, make_callbacks(job_id)), 
        daemon=True
    )
    thread.start()
    return jsonify({'job_id': job_id, 'status': 'running'})


def _run_ingest_thread(job_id, callbacks):
    """Background thread: call run_ingest directly (no subprocess)."""
    try:
        from .ingest_journal import run_ingest as ingest_run
        # client is created inside run_ingest by default
        ingest_run(callbacks=callbacks)
        
        with _sync_cancel_lock:
            if job_id in _sync_jobs and _sync_jobs[job_id]['status'] != 'cancelled':
                _sync_jobs[job_id]['status'] = 'completed'
    except Exception as e:
        with _sync_cancel_lock:
            if job_id in _sync_jobs and _sync_jobs[job_id]['status'] != 'cancelled':
                _sync_jobs[job_id]['status'] = 'error'
```

**Patterns to follow:**
- Existing `_sync_thread()` lifecycle (daemon thread, status mutation under lock)
- Callback merge pattern from `ingest_journal.py` (`cbs.update(callbacks)`)
- SSE generator with 200ms polling and GeneratorExit handling (unchanged)
- Per-job scoping pattern: follow how `_sync_jobs[job_id]` holds job-specific state

**Deferred — LLM error callbacks:** The existing `run_ingest()` only calls callbacks for successful proposals. When `propose_update()` returns an error dict (with `_llm_error` key), no callback fires and the entity gets no progress update. This is a known gap; fixing it (`llm_result(..., ok=False)` for failed LLM calls) should be done as a follow-up improvement but is out of scope for this plan.

**Test scenarios:**
- **Happy path:** Flask POST to `/api/sync/run` starts a job; after the background thread completes, `/api/sync/status` returns `"completed"` status.
- **Integration:** SSE output from `/api/sync/output` during ingest contains typed events (`entity_progress`, `proposal_pushed`) not raw terminal text — verify event type distribution in captured output.
- **Error path:** If `run_ingest()` raises an exception, job status becomes `"error"` and the error is emitted via SSE (not swallowed).

**Verification:**
- Running sync from web UI produces structured SSE events instead of raw journal-name lines.
- All 7 callback event types produce at least one SSE event in a real sync run with mocked KankaClient returning test data.

---

### - [ ] U3. **[Add graceful cancellation support via threading.Event]**

**Goal:** Enable the user to cancel a running sync mid-execution. Cancellation stops new journal processing but preserves already-completed proposals and entity progress state.

**Requirements:** R9, F2

**Dependencies:** U2 (in-process ingest thread with shared state)

**Files:**
- Modify: `kanka_wiki_updater/ingest_journal.py` (`run_ingest()` — add cancellation check between journals)
- Modify: `kanka_wiki_updater/review_web.py` (`cancel_sync()`, `/api/sync/run`)
- Test: `tests/test_cancellation.py` (new — tests for cancellation during ingest, partial result preservation, entity progress state on cancel)

**Approach:**
1. Add a `_sync_cancelled = threading.Event()` module-level variable in `review_web.py`. Reset it at the start of each sync job; set it when `/api/sync/cancel` is called.
2. In `cancel_sync()`, set the cancellation event and update job status to `"cancelled"` (same two-tier approach as before for safety).
3. In `run_ingest()` in `ingest_journal.py`:
   - Accept an optional `_cancelled_event` parameter (for testing — defaults to None, meaning no cancellation check).
   - Before processing each journal: `if _cancelled_event and _cancelled_event.is_set(): break`. This provides a clean checkpoint between journals.
   - In-progress LLM calls are allowed to finish naturally; the flag only prevents new journal/entity processing.
4. On cancellation: emit final SSE events for all in-flight entities (keep them as `"processing"` or mark as `"error"` with reason "cancelled"), emit `EVENT_STATUS_CHANGE` with status `"cancelled"`, and return partial summary from `run_ingest()`.

**Technical design:**
```python
# In ingest_journal.py — run_ingest signature change:
def run_ingest(client=None, callbacks=None, limit=None, _cancelled_event=None):
    ...
    for i, journal in enumerate(to_process, start=1):
        # Cancellation checkpoint
        if _cancelled_event and _cancelled_event.is_set():
            print(f'\nSync cancelled after processing {i - 1}/{len(to_process)} journals.')
            break
        
        # Existing journal processing logic...
```

**Patterns to follow:**
- Existing `_sync_cancel_lock` usage for protecting status mutation
- Threading.Event pattern (standard library, well-tested)
- Partial result preservation: already-completed proposals remain in `pending_changes.json` because they were written before cancellation

**Cancellation edge case:** If the user cancels while no sync is running, `cancel_sync()` should return success with a benign message (no-op). The existing check for job existence handles this.

**Test scenarios:**
- **Happy path (early cancel):** Start a sync with 10 journals; cancel after 3 complete. Verify: job status is `"cancelled"`, exactly 3 journal's worth of proposals exist in the queue, SSE stream received `status_change` event with `"cancelled"` status.
- **Edge case (cancel during LLM call):** Cancel while an LLM call is in progress for one entity. The in-flight call finishes; that entity's proposal is preserved; no new journals are processed.
- **Error path (already finished):** Call cancel on a job that already completed. Verify no error, status remains `"completed"`.

**Verification:**
- Cancelling during sync preserves partial results — proposals for completed journals remain in `pending_changes.json`.
- Entity progress state reflects accurate final statuses after cancellation (done entities stay green; unprocessed ones stay pending).
- Calling cancel on a non-running job is a no-op and returns success.

---

### - [ ] U4. **[Add comprehensive tests for the callback system and SSE integration]**

**Goal:** Achieve meaningful test coverage for the ingest engine's callback mechanism, the web backend's in-process invocation, and the SSE event output — areas that currently have zero or minimal tests.

**Requirements:** R1 (verification), R3 (CLI unchanged), R4-R6 (web streaming)

**Dependencies:** U2 (in-process integration), U3 (cancellation)

**Files:**
- Test: `tests/test_ingest_callbacks.py` (new — unit tests for `_default_callbacks()`, callback override, event payload shapes)
- Modify: `tests/test_review_web.py` or new file `tests/test_review_web_ingest.py` (integration tests from U2 and U3 live here if preferred by project convention)

**Approach:**
1. **Callback unit tests** (`test_ingest_callbacks.py`):
   - Test that `_default_callbacks()` returns a dict with all 7 event type keys.
   - Test that each default callback is a no-op callable (doesn't raise, doesn't mutate state).
   - Test that passing custom callbacks to `run_ingest()` overrides defaults for specified keys while preserving unspecified ones as no-ops.
   - Test that callbacks receive correct argument shapes: `entity_started(name, journal)`, `llm_result(name, journal, ok, data)`, `proposal_queued(proposal_dict)`, etc.

2. **Web integration tests** (new file or append to existing):
   - POST `/api/sync/run` → verify job created with `"running"` status and a valid `job_id`.
   - GET `/api/sync/output` during/after sync → verify SSE stream contains typed events, not raw text.
   - Verify proposal count in queue increases after sync completes (mock KankaClient returns controlled data).
   - Test cancellation endpoint: POST `/api/sync/cancel?job_id=...` → verify status becomes `"cancelled"`.

3. **Test fixtures** (`conftest.py`):
   - Add a `mock_ingest_callbacks()` fixture that collects callback invocations into lists for assertion.
   - Reuse existing `app_with_queue` and `mock_env` fixtures.

**Patterns to follow:**
- Existing `conftest.py` patterns: `mock_env`, `mock_requests`, `app_with_queue`.
- Test class organization from `test_review_web.py`: `TestSyncApi`, `TestSSEOutput`, etc.
- Use `tmp_path` for queue/state file isolation between tests.

**Test scenarios:**
- **Happy path (callback invocation):** After a sync run with mocked data, verify each callback type was called the expected number of times with correct argument values.
- **Error path (LLM failure in ingest):** Mock `propose_update()` to return an error dict; verify `llm_result` is called with `ok=False` and the entity progress state reflects `"error"`.
- **Integration (SSE typed output):** Capture SSE output from a test sync run; parse event types and verify distribution matches expected counts.

**Verification:**
- All new tests pass under `pytest -v --tb=short`.
- Existing tests in `test_review_web.py` continue to pass (backward compatibility).
- Callback system coverage: each of the 7 callback types is invoked and verified by at least one test.

---

## System-Wide Impact

- **Interaction graph:** 
  - `review_web.py` → calls `ingest_journal.run_ingest()` directly (new, replaces subprocess)
  - `run_ingest()` → callbacks → `_SSECallbackEmitter` → SSE generator → browser
  - Per-job entity progress state (`_sync_jobs[job_id]['progress']`) → scoped per job, shared between that job's ingest thread and SSE generator (protected by `_sync_lock`). No global mutable state.
- **Error propagation:** Exceptions in `run_ingest()` are caught in the background thread, set job status to `"error"`, and emitted via SSE. The Flask request handler returns immediately after starting the thread (non-blocking).
- **State lifecycle risks:** 
  - Partial writes: proposals already queued before cancellation remain in `pending_changes.json` — this is correct behavior.
  - Concurrent access: per-job entity progress state (`_sync_jobs[job_id]['progress']`) protected by existing `_sync_lock`; no new locks needed. Per-job scoping prevents clobbering if two sync jobs run concurrently.
- **API surface parity:** The CLI (`python -m kanka_wiki_updater.sync_pipeline`) is unchanged — it calls `run_ingest()` with default terminal-print callbacks. No behavioral change for CLI users.
- **Integration coverage:** The SSE output format changes from raw text lines to typed JSON events — the frontend must be updated separately (out of scope). Backend consumers of `/api/sync/output` will see different event shapes after this change.
- **Unchanged invariants:** 
  - `pending_changes.json` schema unchanged — proposals use identical structure.
  - CLI entry point (`main()`) signature and behavior unchanged.
  - Existing state.py functions (`get_last_sync`, `set_last_sync`, `append_to_queue`, etc.) unchanged.

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| In-process ingest blocks Flask's GIL during LLM calls | LLM calls are I/O-bound (HTTP to LM Studio); Python's GIL is released during network I/O. Background thread keeps Flask free for other requests. |
| Cancelling mid-LLM-call leaves orphaned HTTP connection | In-flight LLM calls are allowed to finish; the cancellation flag only prevents *new* calls. No resource leak — each call completes normally. |
| SSE generator and ingest thread share per-job entity progress state under same lock (`_sync_lock`) as other API routes | Minimal risk: lock is held briefly during callback emission (dict update + JSON serialize). Flask requests to `/api/proposals/*` may see slightly higher latency if a sync is running, but these are fast operations (<1ms with lock). |
| Frontend breakage from SSE format change | Out of scope for this plan. The new event shapes are documented in U1; frontend work should be sequenced after backend changes land. |
| `run_ingest()` raises unexpected exception during sync | Caught by `_run_ingest_thread` wrapper → job status set to `"error"` → error message emitted via SSE (same behavior as current subprocess exit with non-zero code). |

---

## Documentation / Operational Notes

- No CLI behavior changes — no user-facing documentation updates needed for `sync_pipeline`.
- The web UI frontend (`templates/index.html` + JS) will need separate updates to render the new event types — this is a distinct task that consumes the data shapes defined in U1.
- Consider adding a brief migration note in the changelog noting that `/api/sync/output` SSE events are now structured JSON instead of raw text lines (breaking change for any non-browser consumers).

---

## Sources & References

- **Origin document:** [docs/brainstorms/live-sync-progress.md](docs/brainstorms/live-sync-progress.md)
- Related code: `kanka_wiki_updater/sync_pipeline.py` (thin re-export), `kanka_wiki_updater/review_web.py` (SSE infrastructure), `kanka_wiki_updater/state.py` (persistence)
- Prior plan: [docs/plans/2025-07-19-001-refactor-review-web-backend-plan.md](docs/plans/2025-07-19-001-refactor-review-web-backend-plan.md)
