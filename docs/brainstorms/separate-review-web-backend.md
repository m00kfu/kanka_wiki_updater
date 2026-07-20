# Separate review_web Backend from Frontend

**Date:** 2025-07-19  
**Status:** Approved — ready for planning  
**Scope:** Standard refactor

---

## Problem

`review_web.py` is a ~650-line monolith that mixes Flask routes (frontend), Kanka API sync logic, entity resolution, and queue management. This makes it impossible to reuse the business logic with other frontends (e.g., a TUI). `review.py` has its own outdated copy of similar sync code — both files diverge from each other.

**Goal:** Extract all business logic into reusable backend modules so `review_web.py` becomes thin Flask routes, and `review.py` gets modernized to use the same shared code.

---

## Scope Boundaries

### In scope
- Create two new backend modules (see below)
- Refactor `review_web.py` to use them (Flask routes stay)
- Modernize `review.py` to use the sync engine instead of its own code paths
- Preserve all existing functionality, behavior, and data compatibility (`pending_changes.json` schema unchanged)

### Out of scope
- New features or LLM prompt changes
- Database migration or schema changes
- Restructuring other modules (kanka_client, synopsis_generator, etc.)
- Adding a new frontend — just make it possible

---

## Proposed Architecture

### `sync_engine.py` — "talks to Kanka"
All business logic that interacts with the Kanka API:
- `apply_proposal(client, proposal, entity_index_cache)` — unified entry point for syncing any proposal (new_entity or update) back to Kanka. Handles entity creation/update, synopsis updates, and all relation changes. Replaces `_sync_proposal_to_kanka()`.
- `resolve_entity(client, name, entity_index_cache)` — resolves an entity name to its entity_id using wiki link parsing, exact match, substring match, and difflib fuzzy matching. Replaces `resolve_name_to_id()`.
- Relation helpers: `_rel_target(rel)`, `_rel_owner(rel)`, `_rel_id(rel)` (currently duplicated in review_web.py and review.py).

### `queue_manager.py` — "manages pending_changes.json"
All queue I/O and in-memory data manipulation:
- `load_queue()` / `save_queue(queue)` — read/write the JSON file. Replaces `_load_queue()`, `_save_queue()`.
- Proposal editing: `edit_proposal_text(queue, index, text, proposal_type)` — updates either `draft_entry` (new_entity) or `proposed_entry` (update).
- Status management: `update_status(queue, index, status_value)` — sets approved_all, approved_synopsis_only, rejected.
- Relation CRUD on queue entries: `add_relation_change()`, `delete_relation_change()`, `update_relation_change()` — used by the web UI's relation edit routes.

### `review_web.py` (new) — "Flask routes only"
All route handlers become thin one-liners that call the two modules above. No business logic, no API calls, no entity resolution. Just: parse request → call module → return JSON.

---

## Implementation Plan (3 Sequential Steps)

### Step 1: Create `sync_engine.py` from review_web.py
- Extract `_sync_proposal_to_kanka()`, `resolve_name_to_id()`, and relation helpers into new functions with explicit parameters
- Rename to `apply_proposal()` and `resolve_entity()`
- Remove module-level caches (`_entity_index_cache`, `_name_to_id_override`) — pass them as arguments instead
- Do NOT touch review_web.py or review.py yet

### Step 2: Refactor review_web.py to use sync_engine + queue_manager
- Replace all business logic in route handlers with calls to the new modules
- Verify the web UI still works (run `python -m kanka_wiki_updater.review_web` and test each endpoint)

### Step 3: Modernize review.py
- Replace both code paths (`review_new_entity_proposal()` direct API calls + `review_proposal()` direct API calls) with single call to `apply_proposal(client, proposal, cache)` after user approval
- Keep the interactive CLI flow intact — only the Kanka API calls change
- Verify `python -m kanka_wiki_updater.review` still works

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Backend split | Two modules: sync_engine + queue_manager | TUI can import just what it needs; clean separation of concerns |
| Cache handling | Explicit parameters, no globals | Testable without mocking module state; clear data flow |
| Naming | `apply_proposal()` / `resolve_entity()` | Clear intent; follows verb-object convention |
| review.py approach | Single call to apply_proposal() replacing both old paths | Eliminates duplicated sync code; uses the more robust web version's features (fuzzy matching, reverse-direction conflict detection) |
| queue_manager scope | Owns relation CRUD helpers too | Any frontend can manipulate proposals without knowing internal data structure |

---

## Success Criteria

1. `review_web.py` contains only Flask route handlers and HTML template — no business logic, no KankaClient calls directly
2. `sync_engine.py` is importable standalone with a mock client for unit testing
3. `queue_manager.py` is importable standalone for queue operations
4. Both `python -m kanka_wiki_updater.review_web` and `python -m kanka_wiki_updater.review` work identically to before
5. No behavior change in the JSON schema or data format
6. Existing tests pass (or are updated to match new module structure)

---

## Risks & Mitigations

- **Risk:** Breaking existing `pending_changes.json` entries during refactor  
  **Mitigation:** Sequential steps; each step tested independently before proceeding. The JSON file is never rewritten with a different schema — only status fields change.

- **Risk:** review.py's simpler sync code lacks features that review_web has (fuzzy matching, reverse-direction conflict detection)  
  **Mitigation:** Step 3 replaces both old paths entirely — the modernized review.py will gain all of review_web's robustness for free.

- **Risk:** Module-level state (`_sync_jobs`, `_job_counter`) in review_web.py is specific to web SSE streaming and shouldn't move to backend modules  
  **Mitigation:** These stay in `review_web.py` — they're web-specific job tracking, not business logic.
