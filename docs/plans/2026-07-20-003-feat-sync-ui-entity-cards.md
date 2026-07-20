---
title: feat: Live Sync UI — entity cards grouped by journal + live proposal insertion
type: feat
status: active
date: 2026-07-20
origin: docs/brainstorms/live-sync-progress-ui.md
parent_plan: docs/plans/2026-07-20-002-feat-live-sync-progress-streaming-plan.md
---

# Live Sync UI — Entity Cards + Live Proposal Insertion

## Overview

Build the frontend UI for the live sync progress streaming feature. Replace the raw terminal-output Sync tab with a structured entity progress dashboard that shows real-time status cards grouped by journal, and auto-inserts new proposals into the "New" tab sidebar as they appear during sync — all via vanilla JS + SSE events from the existing backend.

## Requirements Trace (from parent plan)

| Requirement | Status |
|---|---|
| R7: UI displays entity list grouped by journal with name, source journal, status indicator, error message on hover | In scope |
| R8: Completed entities visually distinguished from pending; list scrolls as new journals discovered | In scope |
| R6 (proposal_pushed): Browser appends proposals live to "New" tab without page refresh | In scope |

## Scope Boundaries

- **In scope:** Frontend UI only — HTML template, JS event handling, CSS styles. Zero Python changes (backend U1–U4 are complete).
- **Not in scope:** Backend SSE event shape changes — consume existing shapes as-is.
- **Not in scope:** Proposal editing/preview during sync — sidebar shows placeholders that resolve on click.
- **Not in scope:** Pagination/virtual scrolling for >200 entities (future optimization).

## Technical Design

### State Management (vanilla JS)

```js
let syncEntities = {};      // key: "journal_name::entity_name" → {name, journal_name, status, error_message}
let syncJournalOrder = [];  // ordered array of journal names for rendering
let currentSyncJob = null;   // existing: {job_id, status, output} — repurposed for job metadata
```

### SSE Event Dispatch (in `runSync()`)

Replace the single `message` listener with typed listeners:

| Event Type | Handler Action |
|---|---|
| `entity_progress` | Update/add entity in `syncEntities`, re-render journal group |
| `proposal_pushed` | Create placeholder proposal, push to `proposals[]`, refresh sidebar |
| `status_change` | Update `currentSyncJob.status`, update sync header badge |
| `end` | Close EventSource, finalize job state, refresh full proposals list |

### Sync Tab UI Structure

```html
<div id="syncProgressContainer">
  <!-- Header: status + summary -->
  <div class="sync-header">
    <span class="sync-status-badge running">● RUNNING</span>
    <span class="sync-count-summary">0 entities · 0 journals</span>
    <button class="btn btn-danger" onclick="cancelCurrentSync()">Cancel</button>
  </div>

  <!-- Journal groups (populated by JS) -->
  <div id="syncJournalGroups"></div>
</div>
```

Each journal group:
```html
<div class="journal-group">
  <div class="journal-group-header">
    📖 Journal Name
    <span class="group-progress">(3/5 done)</span>
  </div>
  <div class="entity-cards">
    <div class="entity-card status-processing" title="Processing...">⟳ Zara</div>
    <div class="entity-card status-done" title="Complete">✓ Gladio</div>
    <div class="entity-card status-error" title="LLM timeout: connection refused">✗ Peacely</div>
  </div>
</div>
```

### Live Proposal Insertion Flow

1. `proposal_pushed` SSE event arrives with `{type, name, kind, status}`
2. Create placeholder: `{entity_name: name, source_journal: 'Syncing...', proposal_type: type, entity_kind: kind, status: 'pending', _sync_placeholder: true}`
3. Push into `proposals[]` array (new entities sorted to top)
4. Call `updateStats(); renderSidebar()` — sidebar updates instantly
5. User clicks placeholder → `selectProposal()` detects `_sync_placeholder`, fetches full proposal from `/api/proposals`, merges data, re-renders

### Polling Fallback

Keep the existing 1s polling of `/api/sync/status` as a fallback when SSE drops. This also powers:
- The header sync indicator (`#syncIndicator`)
- Auto-refresh of proposals after sync completes (via loading overlay)

## CSS Additions (in `style.css`)

```css
/* Sync progress container */
.sync-progress-container { display: flex; flex-direction: column; gap: 12px; }
.sync-header { display: flex; align-items: center; gap: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }

/* Status badges */
.sync-status-badge { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.sync-status-badge.running { color: var(--blue); animation: pulse 2s infinite; }
.sync-status-badge.completed { color: var(--green); }
.sync-status-badge.error { color: var(--red); }
.sync-status-badge.cancelled { color: var(--yellow); }

@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }

/* Journal groups */
.journal-group {} /* container for a journal's entities */
.journal-group-header { font-size: 13px; font-weight: 600; color: var(--text); padding: 8px 0; display: flex; align-items: center; gap: 6px; }
.group-progress { font-size: 12px; color: var(--text-dim); font-weight: 400; }

/* Entity cards */
.entity-cards { display: flex; flex-direction: column; gap: 4px; margin-bottom: 8px; padding-left: 8px; border-left: 2px solid var(--border); }
.entity-card { font-size: 13px; padding: 6px 10px; border-radius: 4px; display: flex; align-items: center; gap: 6px; transition: background 0.15s; cursor: default; }
.entity-card:hover { background: rgba(255,255,255,0.03); }
.entity-card.status-pending { color: var(--text-dim); }
.entity-card.status-processing { color: var(--blue); }
.entity-card.status-done { color: var(--green); }
.entity-card.status-error { color: var(--red); cursor: help; }
.entity-card .entity-icon { font-size: 14px; flex-shrink: 0; width: 18px; text-align: center; }

/* Live proposal placeholder in sidebar */
.proposal-item.sync-placeholder { border-left-color: var(--cyan); opacity: 0.7; }
```

## Implementation Units

### - [ ] U1. Add CSS styles for entity cards, journal groups, status badges

**Files:** `kanka_wiki_updater/static/css/style.css`

Add all new CSS classes listed above. Keep within existing design language (dark theme, same spacing scale).

### - [ ] U2. Rewrite `runSync()` with typed SSE event dispatch + entity state tracking

**Files:** `kanka_wiki_updater/static/js/app.js`

- Add `syncEntities`, `syncJournalOrder` state vars at module level
- Replace single `message` listener with per-event-type listeners in `runSync()`
- `entity_progress`: update/add entry in `syncEntities`, re-render sync tab
- `proposal_pushed`: create placeholder, push to proposals array, refresh sidebar
- `status_change`: update job status
- `end`: close EventSource, finalize

### - [ ] U3. Rewrite `renderSyncContent()` — entity progress cards grouped by journal

**Files:** `kanka_wiki_updater/static/js/app.js`

Replace the raw `<pre id="syncOutput">` with structured HTML:
- Sync header with status badge + summary count
- Journal groups (ordered by first-seen order)
- Entity cards per group with status icons and hover tooltips for errors

### - [ ] U4. Live proposal insertion + placeholder resolution

**Files:** `kanka_wiki_updater/static/js/app.js`

- In `proposal_pushed` handler: create minimal proposal object, push to array
- Update sidebar rendering to show sync-placeholder styling
- On click of placeholder in `selectProposal()`: fetch full data from `/api/proposals`, merge, re-render
- Keep existing `loadProposals()` working for non-sync scenarios

### - [ ] U5. Polish — cancel flow, edge cases, visual feedback

**Files:** `kanka_wiki_updater/static/js/app.js` + `templates/index.html`

- Cancel button in sync header (or reuse the main Run/Cancel toggle)
- Visual distinction for cancelled entities (unprocessed stay pending or get marked cancelled)
- Ensure existing proposal review flow is completely unchanged
- Handle SSE connection drops gracefully via polling fallback

## Testing Approach

Manual browser testing only — this is a UI feature:
1. Start web UI → open Sync tab → click "Run Sync"
2. Verify entity cards appear and update in real-time (grouped by journal)
3. Verify status icons change: processing → done/error
4. Verify proposals auto-appear in "New" tab sidebar during sync
5. Click a proposal placeholder — should resolve to full data after sync completes
6. Test cancel mid-sync — verify clean stop, partial results preserved
7. Run existing test suite — no regressions expected (zero Python changes)

## Files Modified

| File | Changes |
|---|---|
| `kanka_wiki_updater/static/css/style.css` | +120 lines: entity card styles, journal groups, status badges, animations |
| `kanka_wiki_updater/static/js/app.js` | ~150 line rewrite of runSync() + renderSyncContent(), +80 lines new handlers |
| `kanka_wiki_updater/templates/index.html` | Minor: add sync header button if needed (optional) |

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| SSE connection drops mid-sync | Polling fallback at 1s interval already exists; also keep `loadProposals()` polling for state refresh |
| Large entity lists (>200) cause DOM slowdown | Full re-render is fine for <200 entities. Future optimization: only diff/re-render changed entries |
| Placeholder proposals lack full data → confusing UX on click | Show "Loading..." skeleton in content area while fetching; add toast notification when resolved |
| Existing proposal review flow breaks | Keep all existing `selectProposal()`, `renderSidebar()` logic intact — only add new branches for `_sync_placeholder` |
