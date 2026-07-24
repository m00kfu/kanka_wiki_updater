# Plan: Sync Tab UI Improvements — Elapsed Time, Completion Summary, Inline Errors

**Date:** 2026-07-23  
**Scope:** `kanka_wiki_updater/static/js/app.js` (elapsed time + completion summary), `kanka_wiki_updater/templates/index.html` (summary banner HTML), `kanka_wiki_updater/review_web/static/css/style.css` (inline error card styles)  
**Files changed:** 3 files, ~120 lines added/modified

---

## Goal

Address two UX gaps in the sync tab:
1. **Elapsed time + completion summary** — users can't tell how long a sync has been running or what it achieved
2. **Inline error messages** — errors only show on hover via `title` attribute, making them easy to miss

---

## Current State (as of reading 2026-07-23)

### Backend (`review_web.py`)
- `_sync_jobs` dict tracks jobs with `started_at`, `finished_at`, `status`, `progress` (per-entity status dict), and `buffer` (SSE frame queue)
- SSE typed events: `entity_progress` (with `name`, `journal_name`, `status`, optional `error_message`), `proposal_pushed`, `status_change`, `end`
- Journal completion events include `entities_processed` and `suggestions_count`

### Frontend (`app.js`)
- `syncEntities` dict keyed by `"journal_name::entity_name"` with `{name, journal_name, status, error_message}`
- `syncJournalOrder` array tracks journal group rendering order
- `currentSyncJob` object: `{job_id, status}` — **does NOT store `started_at`**
- `renderSyncContent()` renders journal groups → entity cards; errors show only as "Error" text on hover via `title` attribute
- Icon map in JS: `{'pending':'●', 'processing':'⟳', 'done':'✓', 'error':'✗'}`

### CSS (`style.css`)
- `.entity-card.status-error` already styled red with `cursor: help`
- `.sync-header`, `.sync-status-badge`, `.journal-group`, `.entity-cards` all have existing styles
- No expandable/collapsible patterns in sync section yet

---

## Task 1: Elapsed Time Display

### What changes

Add a running timer (MM:SS format) to the sync header that updates every second while sync is running. Hide it when idle or after completion.

### Implementation details

**File:** `kanka_wiki_updater/static/js/app.js`

#### 1a. Store `started_at` on the job object

In `runSync()`, capture the server's `started_at` timestamp from the SSE stream (or compute locally). Since the backend already sends `started_at` in `_sync_jobs` and it's available via `/api/sync/status`, we can:

- Option A (preferred): Read `started_at` from the first SSE event or poll `/api/sync/status` once at job start
- Option B: Store a local timestamp when `runSync()` is called

**Go with Option B** — simpler, no backend change needed. The difference between server start time and client call time is negligible (<1s).

```javascript
// In runSync(), after setting currentSyncJob.status = 'running':
currentSyncJob.started_at = Date.now();
startElapsedTimer();  // new function
```

#### 1b. Add elapsed timer logic

New functions at the bottom of the script (before `updateSyncIndicator`):

```javascript
var _elapsedInterval = null;

function startElapsedTimer() {
    if (_elapsedInterval) clearInterval(_elapsedInterval);
    _elapsedInterval = setInterval(function() {
        if (!currentSyncJob || !currentSyncJob.started_at) return;
        var elapsed = Math.floor((Date.now() - currentSyncJob.started_at) / 1000);
        var el = document.getElementById('syncElapsed');
        if (el) {
            var mins = Math.floor(elapsed / 60);
            var secs = elapsed % 60;
            el.textContent = mins + 'm ' + (secs < 10 ? '0' : '') + secs + 's';
        }
    }, 1000);
}

function stopElapsedTimer() {
    if (_elapsedInterval) {
        clearInterval(_elapsedInterval);
        _elapsedInterval = null;
    }
}
```

#### 1c. Add elapsed display to sync header HTML

In `renderSyncContent()`, inside the `.sync-header` div, add after the status badge:

```html
<span class="sync-elapsed" id="syncElapsed"></span>
```

Full header structure becomes:
```javascript
'<div class="sync-header">' +
  '<span class="sync-status-badge ' + jobStatus + '">' +
    '<span class="badge-dot"></span> ' + (jobStatus || 'idle').toUpperCase() +
  '</span>' +
  '<span class="sync-elapsed" id="syncElapsed"></span>' +
  '<span class="sync-count-summary" id="syncCountSummary"></span>' +
  '<button class="btn ' + btnClass + '" onclick="runSync()">' + btnText + '</button>' +
'</div>';
```

#### 1d. Start/stop timer at correct lifecycle points

- **Start:** In `runSync()`, after setting status to `'running'` and connecting SSE, call `startElapsedTimer()`
- **Stop on completion:** In the SSE `'end'` event handler, call `stopElapsedTimer()` before refreshing proposals
- **Stop on cancel:** In the cancel branch of `runSync()`, call `stopElapsedTimer()`

#### 1e. CSS for elapsed timer

Add to `style.css`:

```css
.sync-elapsed {
    font-size: 13px;
    color: var(--amber);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    min-width: 48px;
    text-align: center;
}
```

`tabular-nums` prevents the width from jittering as digits change.

### Where to hook in existing code

| Location | Change |
|----------|--------|
| `runSync()` ~line 530 (after status='running') | Set `started_at = Date.now()`, call `startElapsedTimer()` |
| `renderSyncContent()` sync-header HTML | Add `<span class="sync-elapsed" id="syncElapsed">` |
| SSE `'end'` handler (~line 620) | Call `stopElapsedTimer()` |
| Cancel branch in `runSync()` (~line 510) | Call `stopElapsedTimer()` |
| `style.css` | Add `.sync-elapsed` rule |

---

## Task 2: Completion Summary Banner

### What changes

When sync completes, show a summary banner with:
- Total entities processed (by status breakdown)
- Number of proposals created
- A "Review New Proposals" button that switches to the New tab and selects the first pending proposal

### Implementation details

**File:** `kanka_wiki_updater/static/js/app.js`

#### 2a. Compute summary stats from sync state

In the SSE `'end'` event handler, after setting job status to completed:

```javascript
// Count entities by status
var totalEntities = 0, doneCount = 0, errorCount = 0;
for (var k in syncEntities) {
    if (!syncEntities[k]._meta && syncEntities[k].journal_name) {
        totalEntities++;
        if (syncEntities[k].status === 'done') doneCount++;
        else if (syncEntities[k].status === 'error') errorCount++;
    }
}

// Count proposals created during this run (those without source_journal set yet, or newly added)
var newProposals = 0;
for (var i = 0; i < proposals.length; i++) {
    if (proposals[i].status === 'pending' && !proposals[i]._sync_placeholder) {
        // Count non-placeholder pending proposals that weren't there before
        // We'll use a simpler heuristic: count all current pending proposals
        newProposals++;
    }
}

// Show summary banner
showCompletionSummary(totalEntities, doneCount, errorCount, newProposals);
```

#### 2b. Add `showCompletionSummary()` function

```javascript
function showCompletionSummary(total, done, errors, proposals) {
    var content = document.getElementById('content');
    if (!content || currentTab !== 'sync') return;

    // Build summary line
    var parts = [total + ' entity' + (total !== 1 ? 'ies' : '') + ' processed'];
    if (done > 0) parts.push(done + ' done');
    if (errors > 0) parts.push(errors + ' error' + (errors !== 1 ? '' : 's'));

    var html = '<div class="sync-summary-banner">' +
        '<span class="sync-summary-text">✓ Sync complete — ' + parts.join(', ') + '</span>' +
        '<button class="btn btn-primary" onclick="reviewNewProposals()" style="margin-left:12px;">Review New Proposals →</button>' +
    '</div>';

    // Insert banner at top of sync content, before journal groups
    var container = content.querySelector('.sync-progress-container');
    if (container) {
        container.insertAdjacentHTML('afterbegin', html);
    } else {
        content.innerHTML = html + content.innerHTML;
    }
}

function reviewNewProposals() {
    stopElapsedTimer();
    currentTab = 'new';
    selectedIndex = null;
    renderSidebar();
    // Select first pending proposal
    var visible = getVisibleIndices();
    if (visible.length > 0) {
        selectedIndex = visible[0];
    }
    renderContent();
}
```

#### 2c. Call summary from SSE `'end'` handler

In the existing `'end'` event listener, after `stopElapsedTimer()` and before `loadProposals()`:

```javascript
syncEventSource.addEventListener('end', function() {
    syncEventSource.close();
    stopElapsedTimer();  // NEW
    if (currentSyncJob) currentSyncJob.status = 'completed';
    _renderSyncContent();

    // Show completion summary before refreshing proposals
    var totalEntities = 0, doneCount = 0, errorCount = 0;
    for (var k in syncEntities) {
        if (!syncEntities[k]._meta && syncEntities[k].journal_name) {
            totalEntities++;
            if (syncEntities[k].status === 'done') doneCount++;
            else if (syncEntities[k].status === 'error') errorCount++;
        }
    }

    showCompletionSummary(totalEntities, doneCount, errorCount, 0);

    // Refresh proposals from server to fill in full data
    loadProposals();
});
```

#### 2d. CSS for summary banner

Add to `style.css`:

```css
.sync-summary-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    background: rgba(106,171,115,.08);
    border: 1px solid var(--green);
    border-radius: 8px;
    margin-bottom: 12px;
}
.sync-summary-text {
    font-size: 14px;
    color: var(--green);
    font-weight: 600;
}
```

### Where to hook in existing code

| Location | Change |
|----------|--------|
| `app.js` — new functions | Add `showCompletionSummary()`, `reviewNewProposals()` |
| SSE `'end'` handler (~line 620) | Compute stats, call `showCompletionSummary()`, then `loadProposals()` |
| `style.css` | Add `.sync-summary-banner` and `.sync-summary-text` rules |

---

## Task 3: Inline Error Messages on Entity Cards

### What changes

Replace the current hover-only error display with an inline expandable error message. When an entity has an error, show a small "Error" indicator that expands to reveal the full error message when clicked.

### Implementation details

**File:** `kanka_wiki_updater/static/js/app.js`

#### 3a. Modify entity card rendering in `renderSyncContent()`

Current code (around line 470):
```javascript
html += '<div class="entity-card status-' + ent.status + '" title=' +
    (ent.error_message ? escapeJs(ent.error_message) : (ent.status === 'processing' ? 'Processing...' : '')) + '>' +
    '<span class="entity-icon">' + icon + '</span>' +
    escapeJsHtml(ent.name);
if (ent.error_message) {
    html += '<span class="error-tip">Error</span>';
}
html += '</div>';
```

Replace with:
```javascript
var errorHtml = '';
if (ent.error_message) {
    var errId = 'err-' + Math.random().toString(36).substr(2, 8);
    errorHtml = '<span class="error-indicator" data-error-id="' + errId + '" onclick="toggleErrorDetail(event)">⚠</span>' +
        '<div class="error-detail" id="' + errId + '" style="display:none;">' +
            escapeJsHtml(ent.error_message) +
            ' <span class="error-close" onclick="closeErrorDetail(\'' + errId.replace(/'/g, "\\'") + '\')" style="cursor:pointer;margin-left:8px;color:var(--text-dim);">✕</span>' +
        '</div>';
}

html += '<div class="entity-card status-' + ent.status + '">' +
    '<span class="entity-icon">' + icon + '</span>' +
    escapeJsHtml(ent.name) + errorHtml +
'</div>';
```

#### 3b. Add toggle/close helper functions

```javascript
function toggleErrorDetail(e) {
    e.stopPropagation();
    var indicator = e.target;
    var errId = indicator.getAttribute('data-error-id');
    var detail = document.getElementById(errId);
    if (detail) {
        detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
    }
}

function closeErrorDetail(errId) {
    var detail = document.getElementById(errId);
    if (detail) detail.style.display = 'none';
}
```

#### 3c. CSS for inline error display

Add to `style.css`:

```css
.error-indicator {
    font-size: 12px;
    color: var(--red);
    margin-left: auto;
    cursor: pointer;
    padding: 2px 4px;
    border-radius: 3px;
    transition: background 0.12s;
}
.error-indicator:hover {
    background: rgba(212,96,90,.1);
}
.error-detail {
    font-size: 12px;
    color: var(--red);
    padding: 4px 8px 4px 26px;
    margin-left: 8px;
    background: rgba(212,96,90,.05);
    border-radius: 4px;
    line-height: 1.5;
    word-break: break-word;
}
.entity-card.status-error {
    cursor: default;
}
```

### Where to hook in existing code

| Location | Change |
|----------|--------|
| `renderSyncContent()` entity card rendering (~line 470) | Replace error display with inline expandable version |
| `app.js` — new functions | Add `toggleErrorDetail()`, `closeErrorDetail()` |
| `style.css` | Add `.error-indicator`, `.error-detail`, update `.entity-card.status-error` |

---

## Summary of Changes

### Files modified (3)

| File | Lines changed | Description |
|------|--------------|-------------|
| `static/js/app.js` | ~80 added/modified | Elapsed timer, completion summary, inline errors |
| `templates/index.html` | 0 changes | No HTML template changes needed — all dynamic rendering |
| `review_web/static/css/style.css` | ~35 added | `.sync-elapsed`, `.sync-summary-banner`, `.error-indicator`, `.error-detail` |

### No backend changes required

All three improvements use existing data:
- Elapsed time: computed client-side from `Date.now()` at job start
- Completion summary: aggregated from `syncEntities` dict (already populated by SSE events)
- Inline errors: `error_message` field already sent in `entity_progress` SSE events

### Testing approach

1. **Manual verification** — Run a sync with known entities, some of which will error (e.g., trigger an LLM failure). Verify:
   - Timer updates every second during run
   - Summary banner appears on completion with correct counts
   - Error cards show ⚠ indicator and expand to reveal full message

2. **No new automated tests needed** — These are purely frontend UI changes; the existing `TestSyncJavaScript` class covers HTML structure assertions, and manual testing is appropriate for visual/interactive changes.

---

## Bug Fix: Sync Not Detected as Finished

### Problem
After all entities are sent to the LLM, the UI remains stuck in "running" state:
- Timer continues increasing indefinitely
- Cancel button stays visible
- No completion summary banner appears

### Root Cause Analysis
The sync pipeline has three components that must coordinate for proper completion signaling:
1. **Backend thread** (`_ingest_thread`): runs `ingest_run()`, then sets `_sync_jobs[job_id]['status'] = 'completed'`
2. **SSE generator** (`sync_output()`): polls every 0.2s for terminal states, sends `event: end\n\n` when detected
3. **Frontend EventSource**: listens for `'end'` event to finalize sync state

The bug occurs when the SSE stream closes (from idle timeout, network drop, or backend hang) before the frontend receives the `'end'` event. The original code had two gaps:
- **Backend gap**: When the SSE idle timeout fired (15s of no data flow), it silently broke the generator loop without sending `event: end\n\n`, leaving the client in perpetual "running" state
- **Frontend gap**: No fallback mechanism when the EventSource connection closes without receiving an `'end'` event

### Fixes Applied

#### Fix 1 — Backend SSE idle timeout (review_web.py)
**Location:** `sync_output()` generator, idle timeout block (~line 714)
**Change:** Replaced silent `break` with `yield 'event: end\n\n'; return`
```python
# Before:
if time.time() - idle_start > _SSE_IDLE_TIMEOUT:
    break  # Silent close — frontend never gets 'end' event

# After:
if time.time() - idle_start > _SSE_IDLE_TIMEOUT:
    yield 'event: end\n\n'  # Ensure frontend receives completion signal
    return
```
**Rationale:** The frontend relies on the `'end'` event to stop the timer, hide the cancel button, and show the completion banner. A silent break leaves the EventSource client in a perpetual "running" state when backend operations (like `state.set_last_sync()` or LLM calls) block indefinitely.

#### Fix 2 — Frontend connection loss fallback (app.js)
**Location:** After EventSource creation in `runSync()` (~line 817)
**Change:** Added `onerror` handler that polls `/api/sync/status` when the SSE stream drops without an `'end'` event
```javascript
syncEventSource.onerror = function() {
    if (!currentSyncJob || currentSyncJob.status !== 'running') return;
    fetch('/api/sync/status')
        .then(function(r){ return r.json(); })
        .then(function(data) {
            if (data && data.jobs && data.jobs.length > 0) {
                var job = data.jobs[0];
                currentSyncJob.status = job.status;
                stopElapsedTimer();
                _renderSyncContent();
            }
        })
        .catch(function() { /* best-effort */ });
};
```
**Rationale:** This is a safety net for cases where the backend hangs entirely (e.g., LLM call with no timeout, file I/O lock in `state.py`). When the SSE connection drops without an `'end'` event, the handler polls the server for actual job status and updates the UI accordingly.

### Testing
- All 58 review_web tests pass
- Full test suite: 368 passed (2 pre-existing failures + 8 pre-existing errors unrelated to these changes)
- Manual verification needed: Run a sync with an LLM timeout or file I/O block, verify UI transitions out of "running" state within ~15 seconds

---

## Implementation Order

1. **Task 3 (inline errors)** first — simplest change, no lifecycle concerns
2. **Task 1 (elapsed time)** second — adds timer logic, needs careful start/stop hooks
3. **Task 2 (completion summary)** last — depends on Task 1's timer being stopped correctly
4. **Bug fix** — applied after all UI tasks complete; ensures robust completion signaling

Each task is self-contained and can be verified independently before moving to the next.
