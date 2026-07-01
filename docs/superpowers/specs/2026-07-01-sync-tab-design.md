# Implementation Plan: Sync Pipeline Tab in review_web.py

## Overview

Add a third "Sync" tab to the web-based review UI that lets users run `sync_pipeline` from the browser, with real-time output streaming via Server-Sent Events (SSE). The sync module is **not reimplemented** — it's invoked as a subprocess and its stdout/stderr streamed back.

## Files Changed

- `review_web.py` — backend endpoints + frontend tab
- No new files needed; everything fits in the existing module

---

## Part 1: Backend Endpoints (`review_web.py`)

### 1.1 Module-level state for running jobs

```python
import subprocess
import threading
from collections import deque, defaultdict

# Tracks active sync runs keyed by job_id
_sync_jobs = {}  # {job_id: {"process": Popen, "buffer": deque, "status": str, ...}}
_job_counter = [0]  # mutable counter for generating IDs
```

### 1.2 New endpoint: `POST /api/sync/run`

- Generates a unique job ID (increment `_job_counter`)
- Spawns `sync_pipeline.py` as a subprocess with unbuffered output (`python -m kanka_wiki_updater.sync_pipeline --limit <config.JOURNAL_BATCH_LIMIT>`)
- Starts a background thread that reads stdout/stderr line-by-line and pushes to the job's deque buffer
- Returns immediately with `{job_id, status: "running"}`

**Key details:**
- Use `subprocess.Popen` with `stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, universal_newlines=True` for unbuffered streaming
- Background thread runs in a loop reading lines from stdout; on EOF it sets status to `"completed"` or `"error"` based on returncode
- Store job info: `{process, buffer, status, output_lines, started_at, finished_at}`

### 1.3 New endpoint: `GET /api/sync/output?job_id=<id>` (SSE)

- Returns `Content-Type: text/event-stream`
- Yields SSE-formatted events for each new line in the job's buffer
- Uses a polling approach with short intervals (e.g., 200ms) since we can't use async await on deque without locks
- Sends a final event when status changes to `"completed"` or `"error"`
- Cleans up completed jobs after a timeout

**SSE format:**
```
event: message
data: {"type": "output", "text": "..."}

event: status
data: {"status": "completed"}

event: end
```

### 1.4 New endpoint: `GET /api/sync/status`

- Returns current sync job status for the frontend to poll
- Response format: `{jobs: [{job_id, status, output_lines_count, started_at, finished_at}]}`
- If no active jobs, returns `{active: false}`

### 1.5 New endpoint: `POST /api/sync/cancel?job_id=<id>` (optional)

- Sends `SIGTERM` to the running subprocess
- Sets status to `"cancelled"`
- Returns `{ok: true}`

---

## Part 2: Frontend Tab ("Sync")

### 2.1 Tab bar update

Add a third tab button in `renderSidebar()`:
```html
<button class="tab-btn" data-tab="sync" onclick="switchTab('sync')">Sync</button>
```

### 2.2 Sync tab content (`currentTab === 'sync'`)

When the user switches to the "Sync" tab, render:

**Header section:**
- Title: "Run Sync Pipeline"
- Status indicator (idle / running / completed / error) with color coding
- Current time / elapsed timer while running

**Controls:**
- **Run Sync** button — calls `POST /api/sync/run`, starts SSE connection to `/api/sync/output`
- **Cancel** button (disabled when idle, enabled when running) — calls `POST /api/sync/cancel`
- Optional: a text input for `--limit` parameter

**Output area:**
- `<pre>` element with monospace font, dark background (`#0d1117`), green text (`var(--green)`)
- Auto-scrolls to bottom as new lines arrive
- Lines are appended from SSE events
- Styled like a terminal: `> Running sync pipeline...`, `> Building character/location index...`, etc.

**Post-sync behavior:**
- When sync completes, show a summary line: "Queued X update(s) and Y new entity suggestion(s)"
- Auto-refresh the proposal list (re-fetch from `/api/proposals`) so the sidebar updates with new proposals
- Show toast notification when sync finishes

### 2.3 JavaScript functions to add

```javascript
let currentSyncJob = null; // {job_id, eventSource: EventSource}

function runSync() {
    // POST /api/sync/run → get job_id
    // Connect EventSource to /api/sync/output?job_id=<id>
    // Append each "message" event text to output <pre>
    // On "end" event, refresh proposal list, show toast
}

function cancelSync() {
    if (currentSyncJob) {
        fetch('/api/sync/cancel?job_id=' + currentSyncJob.job_id, {method: 'POST'});
        currentSyncJob.eventSource.close();
        currentSyncJob = null;
    }
}

function renderContent() {
    // ... existing code ...
    if (currentTab === 'sync') {
        renderSyncTab();
    }
}

function renderSyncTab() {
    // Render sync tab content based on job status
}
```

---

## Part 3: Integration Details

### 3.1 Process invocation

The subprocess should be invoked from the module's root directory so relative imports work:
```python
import os
module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
proc = subprocess.Popen(
    ['python', '-m', 'kanka_wiki_updater.sync_pipeline'],
    cwd=module_dir,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    bufsize=1,
    universal_newlines=True,
)
```

### 3.2 Thread safety

- The background thread writes to `deque` (thread-safe for append/pop from one end)
- The SSE endpoint reads from the same deque (safe since only one reader per job)
- No shared mutable state between jobs except `_sync_jobs` dict, protected by Flask's request-scoped threads

### 3.3 Cleanup

- Completed jobs are cleaned up after a configurable timeout (e.g., 5 minutes) via a periodic sweep in the SSE endpoint or a separate cleanup thread
- On server shutdown (`app.teardown_appcontext`), terminate any running subprocesses

### 3.4 Error handling

- If `sync_pipeline` crashes or exits with non-zero code, set status to `"error"` and stream an error message
- If the Flask process is killed mid-sync, the subprocess will be orphaned — acceptable for now (could add a cleanup cron later)
- SSE connection drops → frontend reconnects on tab switch

---

## Part 4: UI/UX Polish

### 4.1 Visual design

- Sync tab uses same dark theme as rest of app
- Output area styled like terminal: dark background, monospace font, green text for normal output, yellow/red for warnings/errors
- Status indicator: colored dot + text (green = idle/completed, blue = running, red = error)
- Progress bar under header shows elapsed time during sync

### 4.2 Keyboard shortcuts

- No new keyboard shortcuts needed for the Sync tab (it's a fire-and-forget operation)

### 4.3 Auto-refresh behavior

- When sync completes, automatically refresh the proposal list by calling `GET /api/proposals`
- Update sidebar with any new proposals
- Switch back to "New" tab after completion (optional UX choice — could stay on Sync tab)

---

## Implementation Order

1. **Backend endpoints** (`/api/sync/run`, `/api/sync/output`, `/api/sync/status`)
2. **Frontend tab structure** (tab button, content rendering)
3. **SSE streaming integration** (JavaScript EventSource, output area)
4. **Error handling + cleanup**
5. **UI polish** (terminal styling, status indicators, toasts)
