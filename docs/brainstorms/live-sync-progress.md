# Live Sync Progress Streaming

**Date:** 2025-07-20  
**Status:** Draft — ready for planning  

---

## Problem Frame

The "Sync" tab in `review_web.py` runs `sync_pipeline.py` as a subprocess and streams terminal output (journal names, timing) via SSE. This gives the user nothing more useful than "something is happening." They cannot see which entities are being processed, how many remain, or what proposals have been generated — they just stare at scrolling text.

**Goal:** Replace the raw terminal stream with structured progress events that power two UI improvements:
1. An entity-level progress list showing each entity's status (pending → processing → done/error) as it flows through LLM calls.
2. Live proposal cards appearing in the "New" tab without page refresh, via SSE pushing new proposals to the browser.

---

## Actors

- **A1. Reviewer:** Opens the Sync/Review web UI and runs a sync; watches progress in real-time and reviews proposals as they appear.

---

## Key Flows

- F1. **Run sync with live entity tracking**
  - **Trigger:** User clicks "Start Sync" on the Sync tab (enhanced Review page).
  - **Actors:** A1
  - **Steps:**
    1. UI shows an entity list grouped by journal, all marked "pending."
    2. As each entity's LLM call starts, its status changes to "processing" with a spinner.
    3. When the LLM responds successfully, the entity card turns green ("done") and a new proposal appears live in the New tab.
    4. If the LLM fails, the entity card shows a red error badge with the message on hover/click.
    5. After all entities for a journal are done, that journal section collapses or is marked complete.
    6. When sync finishes, the UI returns to normal (all proposals visible in New tab).
  - **Outcome:** User sees real-time entity progress and live proposals without refreshing.
  - **Covered by:** R1–R8

- F2. **Cancel a running sync**
  - **Trigger:** User clicks "Cancel" on the Sync tab while sync is running.
  - **Actors:** A1
  - **Steps:**
    1. UI sends cancellation request to `/api/sync/cancel`.
    2. Backend terminates the ingest process.
    3. All in-progress entity cards show "cancelled" status; completed ones remain green.
  - **Outcome:** Sync stops gracefully; partial results are preserved in `pending_changes.json`.
  - **Covered by:** R9

---

## Requirements

**[Ingest engine — callback-based]**
- R1. `ingest_journal.py` extracts the core journal-processing logic from `sync_pipeline.py` into a function that accepts callback parameters for progress events.
- R2. The ingest engine emits five event types: entity started, LLM response received (success/fail), proposal written to queue, new-entity suggestion emitted, and journal completed with summary counts.
- R3. The existing CLI entry point (`python -m kanka_wiki_updater.sync_pipeline`) continues to work unchanged by wrapping the ingest engine with terminal-print callbacks.

**[Web SSE streaming]**
- R4. The web backend invokes `ingest_journal.py` directly in a background thread (not as a subprocess) and registers callbacks that emit typed SSE events for each progress event.
- R5. The `/api/sync/output` SSE endpoint emits structured JSON events (`{"type": "entity_start", ...}`, etc.) instead of raw terminal text, enabling the frontend to render entity cards rather than scrolling log lines.
- R6. New proposals are pushed via a separate SSE channel (or same stream with distinct event type) so the browser can append them live to the "New" tab without page refresh.

**[Entity progress list]**
- R7. The UI displays an entity list grouped by journal showing: entity name, source journal, status indicator (pending/processing/done/error), and for errors — a visible error message on hover/click.
- R8. Completed entities are visually distinguished from pending ones; the list scrolls or paginates as new journals are discovered during processing.

**[Cancellation]**
- R9. The ingest engine supports graceful cancellation: when cancelled, in-progress LLM calls are interrupted (or allowed to finish and discarded), already-completed proposals remain in the queue, and entity cards reflect final statuses.

---

## Acceptance Examples

- AE1. **Covers R4, R5, R7.** Given a sync run with 3 journals mentioning 8 unique entities total, when each LLM call completes successfully, the frontend receives an SSE event that changes that entity's card from "processing" to green "done" and appends a new proposal card in the New tab.
- AE2. **Covers R5, R7.** When an LLM call fails (e.g., timeout), the frontend receives an error event; the corresponding entity card turns red with a hoverable tooltip showing the error message ("LLM timeout after 30s").
- AE3. **Covers R9.** When the user cancels during a running sync, no new LLM calls are started, already-finished proposals remain in `pending_changes.json`, and all entity cards show their final status (green for done, red for error, grey for pending/cancelled).

---

## Success Criteria

- A reviewer running sync from the web UI can see exactly which entities have been processed, which are in progress, and how many remain — without reading terminal output.
- New proposals appear in the "New" tab within ~2 seconds of LLM response, with no page refresh required.
- `python -m kanka_wiki_updater.sync_pipeline` continues to work identically for CLI users (no behavior change).
- The ingest engine is importable standalone and testable with mock callbacks — no Flask or subprocess dependencies.

---

## Scope Boundaries

- **Not in scope:** A separate dedicated "Sync Progress" tab; the entity list lives within the existing Review page's Sync section, and proposals appear in the existing New tab.
- **Not in scope:** TUI implementation (future work — this brainstorm only defines the callback interface that a TUI would consume).
- **Not in scope:** Changing `pending_changes.json` schema or proposal data format; new proposals use the same structure as current sync output.
- **Not in scope:** Live editing of proposals during sync; edits still happen after sync completes via the existing review flow.

---

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Ingest module name | `ingest_journal.py` | Clear action verb, distinct from `sync_engine.py` (which handles write-back to Kanka), no "sync" prefix needed since it describes the operation |
| Architecture pattern | Callback engine (Approach A) | Clean separation; CLI wraps with print callbacks, web registers SSE-emitting callbacks. Future TUI plugs in widget-update callbacks. No event bus overhead for 2-3 consumers. |
| Entity list scope | Current journal only (not all journals) | Simpler UI state management; user sees what's happening now, not a static pre-computed queue of every entity across all pending journals |
| Error display | Inline error badge per entity | User can see which entities failed without losing the overall progress picture; hover/click reveals details rather than cluttering the default view |
| Invocation model | In-process thread (not subprocess) | Enables structured callbacks instead of parsing stdout text; SSE events are typed JSON, not raw terminal output |

---

## Dependencies / Assumptions

- `sync_engine.py` and `queue_manager.py` already exist and are importable standalone — the ingest engine will use them for apply/sync operations.
- The existing SSE infrastructure in `review_web.py` (`_sync_jobs`, `_next_job_id()`, `/api/sync/output`) provides a working pattern to build on, not replace from scratch.
- `pending_changes.json` writes are atomic and safe for concurrent access by the ingest thread and any active review operations (assumed via existing file-locking in `state.py`).

---

## Outstanding Questions

### Resolve Before Planning

- **None.** All product decisions have been made.

### Deferred to Planning

- How exactly cancellation interrupts an in-flight LLM call — should we kill the process, or use a shared flag checked between calls?
- Whether entity-level progress events and proposal-pushed events should share one SSE connection (with event type discriminator) or use separate connections.
- Pagination vs virtual scrolling for large entity lists (>100 entities from many journals).

---

## Next Steps

-> /ce-plan for structured implementation planning
