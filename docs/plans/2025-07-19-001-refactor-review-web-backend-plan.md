---
title: Refactor review_web.py — Extract Backend Modules
type: refactor
status: active
date: 2025-07-19
origin: docs/brainstorms/separate-review-web-backend.md
deepened: 2025-07-19
---

# Refactor review_web.py — Extract Backend Modules

## Overview

Split `review_web.py`'s ~650-line monolith into two reusable backend modules (`sync_engine.py`, `queue_manager.py`) and thin it to Flask routes only. Eliminates duplicated Kanka API logic, enables future TUI or other frontend without code duplication.

---

## Problem Frame

`review_web.py` is a ~650-line monolith mixing Flask routes, Kanka API sync logic, entity resolution, queue management, and subprocess job tracking. Neither file can be reused by another frontend (e.g., a TUI) without pulling in Flask or CLI-specific code.

**Origin:** [docs/brainstorms/separate-review-web-backend.md](../brainstorms/separate-review-web-backend.md)

---

## Requirements Trace

- **R1.** `sync_engine.py` contains all Kanka API business logic, importable standalone with mock client
- **R2.** `queue_manager.py` handles all queue I/O and in-memory manipulation (including relation CRUD), importable standalone
- **R3.** `review_web.py` becomes thin Flask routes — no business logic, no direct KankaClient calls in helper functions
- **R4.** No behavior change in JSON schema or data format; existing tests pass after import updates

---

## Scope Boundaries

### In scope
- Create `sync_engine.py` from review_web.py business logic (Step 1)
- Create `queue_manager.py` for queue I/O and relation CRUD (Step 2)
- Refactor `review_web.py` to use both modules (Step 3)

### Out of scope
- New features or LLM prompt changes
- Restructuring other modules (kanka_client, synopsis_generator, etc.)
- Adding a new frontend — just make it possible
- Moving Flask-specific state (`_sync_jobs`, `_job_counter`) to backend modules
- Modernizing `review.py` — it keeps its own sync code paths for now

---

## Context & Research

### Relevant Code and Patterns

| Pattern | Source File | How to Follow |
|---|---|---|
| Import handling for package + direct execution | `review_web.py` lines 27-38, `sync_pipeline.py` lines 19-56 | Use try/except ImportError with both relative and absolute imports; guarded by `if __name__ == '__main__' and __package__ is None` |
| JSON state I/O | `state.py` `_load()` / `_save()` | These stay in `state.py`; queue_manager calls them for file I/O but owns the higher-level manipulation logic |
| Test patterns | `tests/test_review_web.py`, `tests/conftest.py` | pytest fixtures with `tmp_path` for temp JSON files; mock KankaClient methods directly on instances |
| Relation helpers duplication | `_rel_target()` in both review_web.py and review.py (duplicated) | Extract to sync_engine.py; leave the copy in review.py since it's out of scope |
| Entity resolution cache pattern | `_entity_index_cache` module-level tuple `(index, name_map)` | Pass as explicit parameter; caller manages lifecycle |

### Institutional Learnings

- No `docs/solutions/` directory exists in this project.
- The project uses plain JSON files under `data/` for all persistent state — no database, no ORM.
- The Kanka API returns inconsistent response shapes (dict vs model objects), so code must handle both via duck typing (`getattr` + `.get()` fallbacks).

### External References

- Kanka API v1 docs: https://app.kanka.io/api-docs/1.0/overview — the canonical reference for all Kanka endpoint parameters, response shapes, and rate limits. Refer to this when updating sync_engine.py methods that call KankaClient.

---

## Key Technical Decisions

| Decision | Rationale |
|---|---|
| Two modules (sync_engine + queue_manager) instead of one | TUI can import just what it needs; clean separation between "talks to Kanka" and "manages local data" |
| Explicit cache parameters, no globals | Testable without mocking module state; clear data flow through function signatures |
| `apply_proposal(client, proposal, entity_index_cache)` as unified entry point | Single responsibility — one call handles new_entity creation, synopsis update, and all relation changes. Makes the web UI's approval endpoints trivially thin |
| queue_manager owns relation CRUD helpers too | Any frontend can manipulate proposals without knowing internal data structure (`relation_changes` list format) |
| sync_engine imports KankaClient directly | Consistent with how synopsis_generator already imports llm_client; avoids passing client through multiple layers of route handlers |

---

## Open Questions

### Resolved During Planning

- **Q: What about `_sync_jobs`, `_job_counter`, `_sync_cancel_lock` — web-specific job tracking for SSE streaming?**
  A: These stay in `review_web.py`. They're web-specific (SSE job management), not business logic.

- **Q: The current code has a cache reset inconsistency — `/status` endpoint resets the entity index cache on every call, but `/sync` does not. What should happen after refactoring?**
  A: Make both consistent by always resetting the cache at the start of `apply_proposal()`. This is a minor behavior change that aligns with the intent (fresh resolution per operation) and eliminates a subtle source of stale data bugs.

### Deferred to Implementation

- Exact import path adjustments in existing test files — they currently mock internal review_web functions (`_sync_proposal_to_kanka`, `_load_queue`) which will no longer exist; tests must be updated to mock the new module-level functions or call routes through the Flask test client instead
- The precise structure of `test_review_web.py` — whether to restructure into unit tests for sync_engine/queue_manager (in their own files) vs. integration tests that exercise the full route stack

---

## Output Structure

```
kanka_wiki_updater/
├── sync_engine.py          ← NEW: Kanka API business logic (extracted from review_web.py)
├── queue_manager.py        ← NEW: Queue I/O and in-memory manipulation (extracted from review_web.py)
├── review_web.py           ← MODIFY: Flask routes only (~200 lines, down from ~650)
└── tests/
    ├── test_sync_engine.py ← NEW
    └── test_queue_manager.py ← NEW
```

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Module Dependencies

```
review_web.py  →  sync_engine + queue_manager (thin Flask routes)
sync_pipeline  →  synopsis_generator (unchanged, already extracted)
kanka_client   →  (leaf module, unchanged)

sync_engine.py → kanka_client + config (imports directly)
queue_manager.py → state (calls _load/_save), config (for DATA_DIR path)
```

### Data Flow for apply_proposal()

```
apply_proposal(client, proposal, entity_index_cache)
  │
  ├─ reset cache if empty (ensure fresh resolution)
  │
  ├─ if new_entity: client.create_character/location(...)
  │   └─ parse response → set created_local_id, created_kind, created_entity_id
  │
  ├─ if update:     client.update_entity_entry(kind_param, local_id, proposed_entry)
  │
  └─ for each relation_change in proposal.relation_changes:
      ├─ resolve_entity(client, target_name, entity_index_cache) → target_entity_id
      ├─ fetch existing relations via client.get_relations(entity_id)
      ├─ handle reverse-direction conflict (409 prevention)
      ├─ create / update / delete as appropriate
      └─ collect details/warnings/errors list
```

### Function Signatures (Directional)

```python
# sync_engine.py
def apply_proposal(client, proposal, entity_index_cache):
    """Apply a single proposal to Kanka. Returns {'ok': bool, 'message': str, 'warnings': list}."""

def resolve_entity(client, name, entity_index_cache):
    """Resolve an entity name to its entity_id. Returns entity_id int or None."""

# queue_manager.py
def load_queue():
    """Load pending_changes.json → list[dict]."""

def save_queue(queue):
    """Write queue to pending_changes.json."""

def edit_proposal_text(queue, index, text, proposal_type):
    """Update draft_entry (new_entity) or proposed_entry (update)."""

def update_status(queue, index, status_value):
    """Set approved_all / approved_synopsis_only / rejected on a proposal."""

def add_relation_change(queue, index, action, target_name, relation, attitude='', reason=''):
    """Add a new relation change to the queue entry's relation_changes list."""

def delete_relation_change(queue, index, target_name):
    """Remove a relation change by target name from the queue entry."""

def update_relation_change(queue, index, target_name, **fields):
    """Update fields (relation, attitude, reason) on an existing relation change."""
```

---

## Implementation Units

### - [ ] U1. Create sync_engine.py — Kanka API Business Logic

**Goal:** Extract all business logic from `review_web.py` that interacts with the Kanka API into a standalone module.

**Requirements:** R1, R3 (sync_engine part)

**Dependencies:** None

**Files:**
- **Create:** `kanka_wiki_updater/sync_engine.py`

**Approach:**
1. Extract `_sync_proposal_to_kanka()` → rename to `apply_proposal(client, proposal, entity_index_cache)`
   - Move the inner function `resolve_name_to_id()` → rename to top-level `resolve_entity(client, name, cache)`
   - Remove module-level caches (`_entity_index_cache`, `_name_to_id_override`) — pass them as explicit parameters
   - Add a small cache warmup at the start of apply_proposal (if cache is empty dict, populate it with `(index=build_entity_index(client), name_map={...})` to match current behavior)
   - The entity type mapping dicts (`kind_param_map`, `kind_map`) become local variables inside the function

2. Extract relation helpers → `_rel_target()`, `_rel_owner()`, `_rel_id()` as module-level private functions in sync_engine.py

3. Keep the KankaClient import at module level (same pattern as synopsis_generator imports llm_client)

4. Use the existing try/except ImportError pattern for cross-package compatibility:
   ```python
   if __name__ == '__main__' and __package__ is None:
       sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
   ```

**Patterns to follow:**
- `synopsis_generator.py` — already a shared module that imports kanka_client directly; good model for sync_engine
- `_rel_target()` / `_rel_owner()` / `_rel_id()` — currently duplicated in both review_web.py and review.py with identical implementations; extract once here

**Test scenarios:**
- **Happy path: apply_proposal creates new entity** — mock `client.create_character()` returns valid response with `entity_id`; verify function sets `created_entity_id` on proposal dict and returns `{'ok': True, 'message': "Created character 'X' (entity_id=Y)"}`
- **Happy path: apply_proposal updates synopsis** — mock `client.update_entity_entry()` succeeds; verify message includes updated synopsis confirmation
- **Happy path: apply_proposal creates relation** — mock `client.create_relation()` returns response with matching `target_id`; verify "Created relation" in message
- **Happy path: apply_proposal deletes relation** — mock `get_relations` returns existing relation with id; mock `delete_relation()` succeeds; verify "Deleted relation" in message
- **Happy path: apply_proposal updates relation** — mock `update_relation()` succeeds; verify "Updated relation" in message
- **Edge case: resolve_entity matches via wiki link** — name contains `[entity:12345|Name]`; should parse the entity_id and validate against cache
- **Edge case: resolve_entity exact match wins over fuzzy** — given a partial name that has both an exact substring candidate and a difflib match, verify exact substring is returned first
- **Edge case: resolve_entity returns None for unknown name** — name not in index, no substring or fuzzy matches; return None
- **Error path: apply_proposal fails to create entity** — mock `client.create_character()` raises exception; verify function catches it and returns `{'ok': False, 'message': "Sync error: <exception>"}`
- **Error path: apply_proposal relation resolution fails** — target name not found by resolve_entity; verify warning is collected but synopsis still applies (one bad relation doesn't abort the whole proposal)
- **Error path: reverse-direction conflict detection** — mock `get_relations` returns a relation in the opposite direction with same type; verify no create call is made and message explains the conflict

**Verification:**
- Module imports cleanly: `from kanka_wiki_updater.sync_engine import apply_proposal, resolve_entity`
- All functions have explicit parameters (no module-level state)
- Existing test suite still passes: `pytest tests/test_review_web.py -q` should pass (review_web.py hasn't been changed yet — this step only creates the new file)

---

### - [ ] U2. Create queue_manager.py — Queue I/O and Relation CRUD

**Goal:** Extract all queue read/write operations and in-memory data manipulation into a standalone module.

**Requirements:** R2

**Dependencies:** None (standalone, imports state._load/_save for file I/O)

**Files:**
- **Create:** `kanka_wiki_updater/queue_manager.py`

**Approach:**
1. Move `_load_queue()` → rename to `load_queue()`, call `state._load(os.path.join(config.DATA_DIR, 'pending_changes.json'), [])`
2. Move `_save_queue(queue)` → rename to `save_queue(queue)`, call `state._save(...)` using the same path construction
3. Create `edit_proposal_text(queue, index, text, proposal_type)`:
   - If `proposal_type == 'new_entity'`: set `queue[index]['draft_entry'] = text`
   - Else: set `queue[index]['proposed_entry'] = text`
4. Create `update_status(queue, index, status_value)`:
   - Map: `'approved_all'/'approved_synopsis_only' → 'applied'`, `'rejected' → 'rejected'`
5. Create relation CRUD functions that manipulate the `relation_changes` list in-place:
   - `add_relation_change()` — append new dict to `queue[index]['relation_changes']`
   - `delete_relation_change()` — find by target_name, remove from list
   - `update_relation_change()` — find by target_name, update specified fields

**Patterns to follow:**
- `state.py` `_load()` / `_save()` — queue_manager calls these; state.py is the persistence layer, queue_manager is the business logic layer
- The try/except ImportError pattern for config/state imports (same as review_web.py lines 275-281)
- Use `os.path.join(config.DATA_DIR, 'pending_changes.json')` — NOT string concatenation with `/`

**Test scenarios:**
- **Happy path: load_queue with valid file** — write JSON to temp file; verify returned list matches
- **Happy path: save_queue and reload** — create queue, save, reload from fresh process context; verify data integrity (JSON serialization round-trip preserves all fields including relation_changes)
- **Happy path: edit_proposal_text for new_entity** — set `draft_entry`; verify it's stored correctly
- **Happy path: edit_proposal_text for update** — set `proposed_entry`; verify correct field updated
- **Happy path: update_status approved_all → applied** — verify status maps to 'applied'
- **Happy path: add_relation_change** — append relation dict with action/relation/target_name/attitude/reason; verify list length increases by 1
- **Happy path: delete_relation_change** — remove existing target_name from relation_changes; verify it's gone
- **Edge case: edit_proposal_text on out-of-range index** — should raise IndexError (same behavior as direct dict access in current code)
- **Error path: load_queue with missing file** — default to `[]` (same as state._load behavior)

**Verification:**
- Module imports cleanly: `from kanka_wiki_updater.queue_manager import load_queue, save_queue, edit_proposal_text, update_status, add_relation_change, delete_relation_change, update_relation_change`
- All functions are pure or side-effect-only (save/load); no hidden state

---

### - [ ] U3. Refactor review_web.py — Thin Flask Routes Only

**Goal:** Replace all business logic in `review_web.py` with calls to sync_engine and queue_manager. The file should contain only Flask route handlers, HTML template, and web-specific job tracking (`_sync_jobs`, `_job_counter`).

**Requirements:** R3

**Dependencies:** U1, U2

**Files:**
- **Modify:** `kanka_wiki_updater/review_web.py` (target: ~200 lines of route handlers, down from ~650)

**Approach:**
1. Add imports at top of file:
   ```python
   from . import sync_engine
   from . import queue_manager
   ```

2. Remove all business logic functions and module-level state: `_sync_proposal_to_kanka()`, `resolve_name_to_id()`, relation helpers, entity type maps, cache globals (`_entity_index_cache`, `_name_to_id_override`). Keep web-specific state: `_sync_jobs`, `_job_counter`, `_next_job_id()`, `_sync_cancel_lock`, `_sync_thread()` — these are SSE job tracking, not business logic.

3. Update each route handler to use the new modules:

| Route | Before (business logic inline) | After (thin call) |
|---|---|---|
| `/` (index) | `_load_queue()` → pass to template | `queue_manager.load_queue()` |
| `/api/proposals` | `_load_queue()` + filters | `queue_manager.load_queue()` + filter in-route |
| `/api/proposals/<idx>/status` | inline `_sync_proposal_to_kanka(idx)` wrapped in `_sync_lock` | `with _sync_lock: sync_engine.apply_proposal(client, queue[idx], {})` then update status via queue_manager |
| `/api/proposals/<idx>/edit` | direct dict mutation on queue[index] | `queue_manager.edit_proposal_text(queue, idx, text, ptype)` + `save_queue()` |
| `/api/proposals/<idx>/relation` | inline relation CRUD on queue[index]['relation_changes'] | `queue_manager.add/delete/update_relation_change(...)` + `save_queue()` |
| `/api/proposals/<idx>/sync` | wrapper around `_sync_proposal_to_kanka(idx)` wrapped in `_sync_lock` | same pattern, now calling sync_engine.apply_proposal() with _sync_lock |
| `/api/proposals/<idx>/regenerate` | imports synopsis_generator directly (keep this) + loads queue manually | `queue_manager.load_queue()` + existing regen logic stays (it calls synopsis_generator which is already shared) |

4. The `index.html` template stays embedded at the bottom of review_web.py (it's frontend markup)
5. **Important:** Keep `with _sync_lock:` around all `apply_proposal()` calls in route handlers — this prevents concurrent Kanka API calls from corrupting state or hitting rate limits.

**Patterns to follow:**
- Keep the existing Flask route structure; only change what's inside each handler body
- The `/api/proposals/<idx>/regenerate` endpoint already imports `synopsis_generator.build_synopsis_proposal()` — keep this pattern (it's a shared module call, not business logic)

**Test scenarios:**
- **Integration: full approval flow via API** — POST to `/status` with `approved_all`; verify sync_engine.apply_proposal is called, queue status updated to 'applied', and response includes sync result
- **Integration: relation edit via API** — POST to `/relation` with action='create'; verify queue_manager.add_relation_change is called, then GET the proposal back and confirm relation_changes list contains the new entry
- **Edge case: route handling out-of-range index** — still returns 404 (behavior unchanged)
- **Edge case: route handling invalid status value** — still returns 400 with validation error (behavior unchanged)

**Verification:**
- `python -m kanka_wiki_updater.review_web` starts and serves `/` successfully
- All existing test cases in `tests/test_review_web.py` pass (import paths updated to reference new modules; see U3 approach step 1 for the import updates needed)
- No business logic remains in review_web.py — grep for `KankaClient()` should only appear in route handlers as instantiation, not in any helper functions

---

## System-Wide Impact

| Surface | Change | Parity Check |
|---|---|---|
| `pending_changes.json` schema | **No change** — same fields, same structure | Verified: only status values and relation_change dicts are touched |
| Web entry point (`python -m kanka_wiki_updater.review_web`) | Same JSON file, same API responses to browser frontend | All existing test cases must pass; SSE streaming job tracking unchanged |
| `review.py` (CLI) | **No change** — keeps its own sync code paths (out of scope for this refactor) | No impact from this refactor |
| `sync_pipeline.py` | **No change** — already uses synopsis_generator independently | No import changes needed |
| `revert.py` | **No change** — reads applied_log entries created by review.py's `state.log_applied_batch()` | Revert relies on entry structure in log, not on how sync was performed |

### Unchanged Invariants
- The JSON file path (`data/pending_changes.json`) and schema are stable
- `state.load_queue()` / `state.save_queue()` still exist in state.py; queue_manager wraps them with business logic
- `review.py` is unchanged — it continues to use its own sync code paths (out of scope)

---

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Breaking existing pending_changes.json entries during refactor | Low | High | Sequential steps; each step tested independently. The JSON file is never rewritten with a different schema — only status values change |
| Test import path breakage in review_web tests | Medium | Low | Tests currently mock internal functions (`_sync_proposal_to_kanza`, `_load_queue`) that will no longer exist; update to mock new module-level functions or use Flask test client integration approach |
| Cache reset behavior change (consolidating into apply_proposal) | N/A (intentional) | Low-Med | Both endpoints now always start with a fresh cache — aligns with the intent of getting current entity data per operation. Document as intentional behavior fix |

---

## Documentation / Operational Notes

- No user-facing documentation changes needed — behavior is preserved
- The `.env` configuration (`KANKA_TOKEN`, `KANKA_CAMPAIGN_ID`) is unchanged
- Running instructions remain the same: `python -m kanka_wiki_updater.review_web` and `python -m kanka_wiki_updater.review`

---

## Sources & References

- **Origin document:** [docs/brainstorms/separate-review-web-backend.md](../brainstorms/separate-review-web-backend.md)
- **Related code:** `kanka_wiki_updater/review_web.py`, `kanka_wiki_updater/kanka_client.py`, `kanka_wiki_updater/sync_pipeline.py`
- **Tests:** `tests/test_review_web.py`, `tests/conftest.py`
