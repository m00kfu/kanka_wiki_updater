# Plan: Extract Sync Orchestration from `review_web.py`

## Goal

Create a frontend-agnostic sync orchestration module so that the eventual TUI frontend can run the same ingest pipeline with the same callback contract — without importing Flask or web-specific code.

---

## Target Architecture

```
kanka_wiki_updater/
├── ingest_journal.py          ← pipeline engine (already extracted)
├── sync_engine.py             ← apply proposals to Kanka API (already extracted)
├── queue_manager.py           ← pending_changes.json I/O (already extracted)
├── synopsis_generator.py      ← LLM proposal generation (already extracted)
│
├── sync_orchestrator.py       ← NEW: job lifecycle + pipeline start/stop/cancel
│                               shared by web + TUI frontends
│
└── review_web.py              ← Flask routes + SSE streaming (thin glue on top)
```

---

## Changes

### 1. Create `kanka_wiki_updater/sync_orchestrator.py`

**New file.** Contains all sync pipeline orchestration logic, currently embedded in the `run_sync()` route handler and job helpers in `review_web.py`.

#### Contents:

| Symbol | Description |
|---|---|
| `_job_counter: list[int]` | Monotonically increasing counter for unique job IDs |
| `_jobs: dict[str, dict]` | Per-job state (status, started_at, finished_at, progress) — **no SSE buffer** |
| `_cancel_event: threading.Event` | Shared cancellation flag, cleared before each run |
| `_lock: threading.Lock` | Protects `_jobs` mutations |
| `start_sync(callbacks, cancelled_event=None, limit=None)` | Main entry point. Spawns a background thread that calls `ingest_journal.run_ingest()` with the provided callbacks. Returns `job_id`. |
| `_sync_thread(job_id, callbacks, limit, cancelled_event)` | Target function for the background thread (extracted from `_ingest_thread` closure) |
| `cancel_sync(job_id)` | Mark a running job as cancelled; set cancellation flag |
| `get_job_status(job_id)` | Return `{status, started_at, finished_at, progress}` for a job |
| `list_jobs()` | Return summary of all jobs (used by `/api/sync/status`) |
| `_next_job_id() → str` | Generate unique ID like `"sync-1"` |
| `_set_entity_status(job_id, key, status, **extra)` | Update an entity progress entry under lock |

#### Callback contract (documented in module docstring):

```python
callbacks = {
    'entity_started':       lambda entity_name, journal_name: ...
    'llm_result':           lambda entity_name, journal_name, ok, data: ...
    'proposal_queued':      lambda proposal_dict: ...
    'new_entity_suggestion':lambda suggestion_dict: ...
    'journal_completed':    lambda journal_name, entities_processed, suggestions_count: ...
    'sync_started':         lambda total_journals, total_entities_estimate: ...
    'journal_entities_discovered': lambda journal_name, entity_names: ...
}
```

### 2. Create `kanka_wiki_updater/sync_events.py` (small shared constants module)

**New file.** Contains the callback event type constants that define the contract between any frontend and `ingest_journal.run_ingest()`.

#### Contents:

| Symbol | Description |
|---|---|
| `EVENT_ENTITY_PROGRESS = 'entity_progress'` | Entity status changed (pending/processing/done/error) |
| `EVENT_PROPOSAL_PUSHED = 'proposal_pushed'` | New or updated proposal queued |
| `EVENT_STATUS_CHANGE = 'status_change'` | Top-level sync status change |
| `EVENT_SYNC_START = 'sync_start'` | Sync began |
| `EVENT_SYNC_COMPLETE = 'sync_complete'` | Sync finished |
| `ENTITY_STATUSES = ('pending', 'processing', 'done', 'error')` | Valid entity progress statuses |

**Import path for consumers:** `from .sync_events import EVENT_ENTITY_PROGRESS, ENTITY_STATUSES`

### 3. Refactor `kanka_wiki_updater/review_web.py`

#### Imports:
- Add: `from . import sync_orchestrator, sync_events`
- Remove: `_next_job_id`, `_sync_jobs`, `_job_counter`, `_sync_cancel_lock`, `_sync_cancelled`, `_emit_sse`, `_get_entity_progress`, `_set_entity_status`, `_SSECallbackEmitter`, all event constants (replace with `sync_events.*`)

#### Route changes:

| Route | Change |
|---|---|
| `/api/sync/run` | Call `sync_orchestrator.start_sync(callbacks, cancelled_event=_sync_cancelled)` instead of inline thread logic. Wrap the returned job_id in SSE-specific state (buffer). Return JSON. |
| `/api/sync/output` | Use `sync_orchestrator.get_job_status()` for progress data. Keep SSE buffer draining and idle timeout — these are web-specific. |
| `/api/sync/cancel` | Call `sync_orchestrator.cancel_sync(job_id)`. |
| `/api/sync/status` | Call `sync_orchestrator.list_jobs()`. |

#### What stays in review_web.py:

- `_sync_jobs` dict — web's augmented view of jobs (adds SSE buffer, output_lines_count)
- `_SSECallbackEmitter` — wraps orchestrator callbacks with SSE frame emission
- `_emit_sse()` — SSE serialization helper
- All `@app.route()` handlers — thin HTTP glue
- `create_app()`, `main()` — Flask app factory + server entry point

#### What goes to sync_orchestrator.py:

- Job ID generation (`_next_job_id`)
- Job lifecycle (creation, status tracking, cancellation)
- Progress state management (`_set_entity_status`, `_get_entity_progress`)
- The background thread target function (`_sync_thread`)
- `start_sync()` — the main entry point that wires callbacks and spawns the thread

### 4. Update tests

| Test file | Change |
|---|---|
| `tests/test_review_web.py` | Replace direct imports of `_next_job_id`, `_set_entity_status`, event constants with imports from new modules. Tests for `/api/sync/*` routes should mock `sync_orchestrator.start_sync` and verify the route returns correct JSON. |
| `tests/test_review.py` (if applicable) | No change — already uses callback-based testing via `run_ingest`. |

Add: `tests/test_sync_orchestrator.py` — unit tests for `start_sync()`, `cancel_sync()`, `get_job_status()`, `_set_entity_status()` with mocked `ingest_journal.run_ingest`.

---

## Implementation Order

1. **Create `sync_events.py`** — constants only, no dependencies on other new files.
2. **Create `sync_orchestrator.py`** — extract job lifecycle + thread logic from `review_web.py`.
3. **Refactor `review_web.py`** — replace inline logic with calls to the new modules.
4. **Update tests** — fix imports, add unit tests for `sync_orchestrator`.

---

## Risk & Mitigation

| Risk | Mitigation |
|---|---|
| Breaking existing import paths in other files that reference review_web internals | Audit all imports of `review_web` before refactoring. The only public symbols currently used externally are `create_app`, `main`, and the event constants — all have clear migration paths. |
| Race conditions when moving lock-protected state between modules | Keep `_sync_lock` in `sync_orchestrator.py`. `review_web.py` accesses job state through accessor functions, not direct dict access. |
| Tests that mock `review_web.KankaClient` directly | These tests target route behavior (HTTP status codes). They should continue to work since routes still import and use `KankaClient` — only the orchestration layer changed. |

---

## Success Criteria

- [ ] `sync_events.py` exists with all event constants, imported by both `review_web.py` and any future TUI code
- [ ] `sync_orchestrator.py` exists with `start_sync()`, `cancel_sync()`, `get_job_status()`, `list_jobs()` — importable standalone without Flask
- [ ] `review_web.py` no longer defines `_next_job_id`, `_set_entity_status`, `_SSECallbackEmitter`, or event constants inline
- [ ] All existing tests pass (`pytest tests/`)
- [ ] New unit tests for `sync_orchestrator` exist and pass
- [ ] No Flask imports in `sync_orchestrator.py` or `sync_events.py`
