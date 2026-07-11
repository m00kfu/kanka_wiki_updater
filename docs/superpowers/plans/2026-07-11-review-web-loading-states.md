# Review Web Loading States Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visual loading feedback (spinners, status messages, pulse animations) to three operations in review_web.py: regenerate proposal, approve/reject sync, and the sync tab status indicator.

**Architecture:** All changes live inside the embedded `INDEX_HTML` string in `review_web.py`. Three additions: a CSS spinner + overlay styles, two JS helper functions (`showContentLoading`, `hideContentLoading`) plus one action-bar helper (`setActionbarLoading`), and wiring calls at the appropriate points in existing async functions. No backend API changes required.

**Tech Stack:** Python 3 (Flask), vanilla JavaScript (ES5 compatible for browser compatibility), CSS3 animations, embedded HTML template string.

## Global Constraints

- Line length: 120 chars (pyproject.toml default)
- All changes in `kanka_wiki_updater/review_web.py` only — no new files
- Follow existing code style: ES5-compatible JS (`var`, function expressions, IIFEs), CSS variables from `:root`, dark theme colors matching existing palette
- No dependencies added

---

```markdown
# Review Web Loading States Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visual loading feedback (spinners, status messages, pulse animations) to three operations in review_web.py: regenerate proposal, approve/reject sync, and the sync tab status indicator.

**Architecture:** All changes live inside the embedded `INDEX_HTML` string in `review_web.py`. Three additions: a CSS spinner + overlay styles, two JS helper functions (`showContentLoading`, `hideContentLoading`) plus one action-bar helper (`setActionbarLoading`), and wiring calls at the appropriate points in existing async functions. No backend API changes required.

**Tech Stack:** Python 3 (Flask), vanilla JavaScript (ES5 compatible for browser compatibility), CSS3 animations, embedded HTML template string.

## Global Constraints

- Line length: 120 chars (pyproject.toml default)
- All changes in `kanka_wiki_updater/review_web.py` only — no new files
- Follow existing code style: ES5-compatible JS (`var`, function expressions, IIFEs), CSS variables from `:root`, dark theme colors matching existing palette
- No dependencies added

---

### Task 1: Add CSS spinner and loading overlay styles

**Files:**
- Modify: `kanka_wiki_updater/review_web.py:1067` (CSS section, before the closing `</style>`)

**Interfaces:**
- Consumes: nothing new
- Produces: CSS classes `.loading-overlay`, `.spinner`, `.btn.loading` and keyframes `@keyframes spin` + `@keyframes pulse-dot` for use by JS helpers

- [ ] **Step 1: Add CSS for loading spinner, overlay, and action-bar states**

Insert the following CSS rules into the `<style>` block in `INDEX_HTML`, right before line 1081 (`</style>`):

```css
/* Loading spinner */
.loading-overlay { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:240px; gap:16px; }
.spinner { width:28px; height:28px; border:3px solid var(--border); border-top-color:var(--cyan); border-radius:50%; animation:spin 0.7s linear infinite; flex-shrink:0; }
.loading-overlay .loading-text { font-size:14px; color:var(--text-dim); text-align:center; max-width:320px; line-height:1.5; }

/* Spinner keyframes */
@keyframes spin { 0%{transform:rotate(0deg)} 100%{transform:rotate(360deg)} }

/* Pulse animation for sync status dot */
@keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.3} }
.pulse-active { animation:pulse-dot 1s ease-in-out infinite; display:inline-block; }

/* Disabled button state during loading */
.btn.loading { pointer-events:none; opacity:0.6; cursor:not-allowed; transform:none !important; }
```

- [ ] **Step 2: Verify the CSS is syntactically valid**

Run: `python -c "from kanka_wiki_updater.review_web import create_app; app = create_app(); print('CSS loaded OK')"`

Expected output: `CSS loaded OK` (no errors — if there's a template syntax error, it will fail here)

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "style: add CSS spinner, overlay, and pulse animation for loading states"
```


### Task 2: Add JavaScript helper functions

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (JS section inside INDEX_HTML)

**Interfaces:**
- Consumes: nothing new — uses existing DOM helpers (`document.getElementById`, `escapeHtml`)
- Produces: three global JS functions usable by any event handler in the page:
  - `showContentLoading(message)` — replaces content area with loading overlay
  - `hideContentLoading()` — restores normal rendering via `renderContent()`
  - `setActionbarLoading(enabled, message)` — dims action bar buttons and shows inline spinner+message

- [ ] **Step 1: Add JS helper functions**

Insert the following three functions into the `<script>` block in `INDEX_HTML`, right after the `escapeHtmlForTextarea` function (around line 1359) and before the `// ── Actions ────────────────────────────────────────────────────────────────` comment:

```javascript
// ── Loading states ─────────────────────────────────────────────────────────

function showContentLoading(message) {
  var content = document.getElementById('content');
  if (!content) return;
  content.innerHTML = '<div class="loading-overlay">' +
    '<div class="spinner"></div>' +
    '<div class="loading-text">' + escapeHtml(message || 'Processing...') + '</div>' +
    '</div>';
}

function hideContentLoading() {
  renderContent();
}

function setActionbarLoading(enabled, message) {
  var bar = document.getElementById('actionBar');
  if (!bar) return;
  var buttons = bar.querySelectorAll('.btn:not([onclick*="runSync"])');
  if (enabled) {
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.add('loading');
    }
    // Remove any existing loading indicator first
    var oldInd = bar.querySelector('.actionbar-loading-indicator');
    if (oldInd) oldInd.remove();
    var ind = document.createElement('span');
    ind.className = 'actionbar-loading-indicator';
    ind.style.cssText = 'margin-left:auto;display:flex;align-items:center;gap:8px;font-size:13px;color:var(--cyan);font-weight:600;flex-shrink:0;';
    var dot = document.createElement('span');
    dot.className = 'spinner';
    dot.style.cssText = 'width:14px;height:14px;border-width:2px;';
    ind.appendChild(dot);
    if (message) {
      var txt = document.createTextNode(' ' + escapeHtml(message));
      ind.appendChild(txt);
    }
    bar.insertBefore(ind, bar.querySelector('.shortcuts'));
  } else {
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.remove('loading');
    }
    var existingInd = bar.querySelector('.actionbar-loading-indicator');
    if (existingInd) existingInd.remove();
  }
}
```

- [ ] **Step 2: Verify the JS is syntactically valid**

Run: `python -c "from kanka_wiki_updater.review_web import create_app; app = create_app(); print('JS loaded OK')"`

Expected output: `JS loaded OK` (no errors)

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "js: add showContentLoading, hideContentLoading, setActionbarLoading helpers"
```


### Task 3: Wire loading state to regenerate proposal

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (the `regenerateProposal()` function in JS)

**Interfaces:**
- Consumes: `showContentLoading()`, `hideContentLoading()` from Task 2, existing `apiCall()` and `escapeHtml()` helpers
- Produces: updated regenerate flow that shows a loading overlay during LLM call

- [ ] **Step 1: Update regenerateProposal() to use content-area loading**

Find the existing `regenerateProposal` function (around line 1533) and replace its body with this updated version. The key changes are:
1. Use `showContentLoading()` instead of appending text to the banner
2. Restore on success via `hideContentLoading()`
3. Show error inline + toast on failure

Replace the entire function body from `async function regenerateProposal() {` through its closing `}` with:

```javascript
async function regenerateProposal() {
  if (selectedIndex === null) return;
  var p = proposals[selectedIndex];
  if (!p || p.proposal_type !== 'update') { showToast('Only update proposals can be regenerated', 'error'); return; }

  // Show loading state in content area
  showContentLoading('Generating new proposal... This may take a minute.');

  var result = await apiCall('/api/proposals/' + selectedIndex + '/regenerate', 'POST');
  if (!result) { hideContentLoading(); return; }

  if (result.ok) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Regeneration successful — proposal updated with fresh LLM output.', 'success');
  } else {
    var msg = result.error || 'Regeneration failed';
    // Show error inline in the content area instead of spinner
    showContentLoading('Regeneration: <strong>' + escapeHtml(msg) + '</strong>');
    showToast(msg, 'error');
  }
}
```

Also remove the old banner-inline loading code from `renderContent()` — find this block inside renderContent (around line 1230-1235):

```javascript
  // Truncation warning with regenerate button
  var isTruncated = p.truncated === true || (p.change_summary && p.change_summary.indexOf('[TRUNCATED:') !== -1);
  if (isTruncated && p.proposal_type === 'update') {
    html += '<div class="warning" id="truncationWarning">' +
      '&#9888; This proposal may be truncated — the LLM hit its token limit and output was cut off. ' +
      '<button class="btn" onclick="regenerateProposal()" style="padding:4px 12px;font-size:12px;margin-left:12px;">Regenerate (higher max_tokens)</button>' +
      '</div>';
  } else if (isTruncated && p.proposal_type === 'new_entity') {
    html += '<div class="warning">&#9888; This new-entity suggestion may be truncated — the LLM output was cut off. Edit manually to fix.</div>';
  }
```

Replace it with:

```javascript
  // Truncation warning with regenerate button (no inline loading state)
  var isTruncated = p.truncated === true || (p.change_summary && p.change_summary.indexOf('[TRUNCATED:') !== -1);
  if (isTruncated && p.proposal_type === 'update') {
    html += '<div class="warning" id="truncationWarning">' +
      '&#9888; This proposal may be truncated — the LLM hit its token limit and output was cut off. ' +
      '<button class="btn" onclick="regenerateProposal()" style="padding:4px 12px;font-size:12px;margin-left:12px;">Regenerate (higher max_tokens)</button>' +
      '</div>';
  } else if (isTruncated && p.proposal_type === 'new_entity') {
    html += '<div class="warning">&#9888; This new-entity suggestion may be truncated — the LLM output was cut off. Edit manually to fix.</div>';
  }
```

- [ ] **Step 2: Verify regeneration flow works**

Start the server and test manually: `python -m kanka_wiki_updater.review_web` then open http://127.0.0.1:5555, select a proposal, click "Regenerate". The content area should show a spinner + message while processing, then restore or show an error on completion.

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "ui: wire loading overlay to regenerate proposal flow"
```


### Task 4: Wire loading state to approve/reject operations

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (the `approveAll`, `approveSynopsisOnly`, and `rejectCurrent` functions in JS)

**Interfaces:**
- Consumes: `setActionbarLoading()` from Task 2, existing `apiCall()`, `showToast()`, `_advance()` helpers
- Produces: updated approve/reject flows that dim action bar buttons with inline spinner during Kanka sync

- [ ] **Step 1: Update approveAll() to use action-bar loading**

Find the `approveAll` function (around line 1389) and replace it with:

```javascript
async function approveAll() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  setActionbarLoading(true, 'Syncing to Kanka...');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_all'})
    .then(function(data) {
      setActionbarLoading(false);
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync) {
        if (data.sync.warnings && data.sync.warnings.length > 0) {
          showToast('Synced with warnings: ' + data.sync.message, 'warning');
        } else if (data.sync.ok) {
          showToast('Synced to Kanka: ' + data.sync.message, 'success');
        } else {
          showToast('Kanka sync failed: ' + data.sync.message, 'error');
        }
      } else {
        showToast('Approved all', 'success');
      }
    });
}
```

- [ ] **Step 2: Update approveSynopsisOnly() to use action-bar loading**

Find the `approveSynopsisOnly` function (around line 1412) and replace it with:

```javascript
async function approveSynopsisOnly() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  setActionbarLoading(true, 'Syncing synopsis to Kanka...');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_synopsis_only'})
    .then(function(data) {
      setActionbarLoading(false);
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync) {
        if (data.sync.warnings && data.sync.warnings.length > 0) {
          showToast('Synopsis synced with warnings: ' + data.sync.message, 'warning');
        } else if (data.sync.ok) {
          showToast('Synopsis synced to Kanka: ' + data.sync.message, 'success');
        } else {
          showToast('Kanka sync failed: ' + data.sync.message, 'error');
        }
      } else {
        showToast('Synopsis approved', 'success');
      }
    });
}
```

- [ ] **Step 3: Update rejectCurrent() to use action-bar loading**

Find the `rejectCurrent` function (around line 1435) and replace it with:

```javascript
async function rejectCurrent() {
  if (selectedIndex === null) return;
  var oldIndex = selectedIndex;
  if (editingField) await saveEdit();
  setActionbarLoading(true, 'Rejecting...');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'rejected'})
    .then(function(data) {
      setActionbarLoading(false);
      if (data) { proposals[selectedIndex] = data.proposal; _advance(oldIndex); showToast('Rejected', 'error'); }
    });
}
```

- [ ] **Step 4: Verify approve/reject flows work**

Start the server, select a pending proposal, click "Approve All" — the action bar buttons should dim and show a small spinner + "Syncing to Kanka..." message. Click "Reject" — same behavior with "Rejecting...".

- [ ] **Step 5: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "ui: wire action-bar loading state to approve/reject flows"
```


### Task 5: Add pulse animation to sync tab status indicator

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (the `renderSyncContent()` function in JS)

**Interfaces:**
- Consumes: nothing new — uses existing CSS classes from Task 1 and existing DOM APIs
- Produces: animated pulsing status dot when sync is running, static dot otherwise

- [ ] **Step 1: Update renderSyncContent() to pulse the status dot**

Find the `renderSyncContent` function (around line 1317) and update it. The key change is adding/removing a `.pulse-active` class on the status dot based on job status:

Replace the entire `function renderSyncContent(content)` with:

```javascript
function renderSyncContent(content) {
  if (currentTab !== 'sync') return;
  var jobStatus = currentSyncJob ? currentSyncJob.status : 'idle';
  var statusColor = {'running':'var(--blue)','completed':'var(--green)','error':'var(--red)','idle':'var(--text-dim)'}[jobStatus] || 'var(--text-dim)';
  var pulseClass = jobStatus === 'running' ? 'pulse-active' : '';

  content.innerHTML = '<div class="empty-state">' +
    '<h3>Run Sync Pipeline</h3>' +
    '<div class="sync-container">' +
      '<div style="margin:16px 0;">' +
        '<button class="btn btn-primary" id="runSyncBtn" onclick="runSync()">' + (currentSyncJob ? 'Cancel' : 'Run Sync') + '</button>' +
        '<span style="margin-left:12px;color:' + statusColor + ';font-weight:600;font-size:14px">&#9679; <span class="' + pulseClass + '">' + jobStatus.toUpperCase() + '</span></span>' +
      '</div>' +
      '<pre id="syncOutput" class="sync-output">' +
        (currentSyncJob && currentSyncJob.output ? escapeJsHtml(currentSyncJob.output) : 'No sync run in progress.') +
      '</pre>' +
    '</div>' +
  '</div>';
}
```

- [ ] **Step 2: Verify sync tab pulse animation**

Start the server, go to the Sync tab, click "Run Sync" — the status text should show a pulsing dot. When sync completes or is cancelled, the pulse stops and the dot returns to normal.

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "ui: add pulse animation to sync tab status indicator"
```


### Task 6: Run linting, formatting, and tests

**Files:**
- Verify: `ruff check` passes on the modified file

- [ ] **Step 1: Run ruff lint and format**

Run:
```bash
ruff check kanka_wiki_updater/review_web.py
ruff format kanka_wiki_updater/review_web.py
```

Fix any lint errors that `ruff check` reports. If formatting changes are needed, run `ruff format .`.

- [ ] **Step 2: Run all tests**

Run: `pytest -v`

Expected: All existing tests pass (no new failures introduced by CSS/JS changes). The review_web tests test the Flask API endpoints which haven't changed.

- [ ] **Step 3: Final commit if needed**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "style: apply ruff formatting to review_web"
```


### Task 7: Manual end-to-end verification

**Files:**
- No file changes — manual testing only

- [ ] **Step 1: Start server and verify all loading states**

Run: `python -m kanka_wiki_updater.review_web`

Then in browser at http://127.0.0.1:5555:
1. Select a pending proposal → click "Regenerate" → verify content area shows spinner + message, then restores on completion/failure
2. Click "Approve All" (or "Reject") → verify action bar buttons dim and show inline spinner with status text
3. Switch to Sync tab → click "Run Sync" → verify the RUNNING label has a pulsing dot animation
4. Verify sync output still streams correctly beneath the status indicator

- [ ] **Step 2: Commit any fixes**

If manual testing reveals issues, fix them and commit:

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "fix: address review feedback on loading states"
```

---

## Plan Self-Review Checklist

1. **Spec coverage:** Each requirement from the design doc is covered: CSS spinner/overlay (Task 1), JS helpers (Task 2), regenerate wiring (Task 3), approve/reject wiring (Task 4), sync tab pulse (Task 5).
2. **Placeholder scan:** No "TBD", "TODO", or vague references — every step has exact code and commands.
3. **Type consistency:** All JS functions use ES5 syntax consistent with the existing file; CSS variable names match `:root` definitions in the same file.
4. **No new dependencies:** Only existing Python/JS/CSS in review_web.py is modified.

```python
# Review Web Loading States Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add visual loading feedback (spinners, status messages, pulse animations) to three operations in review_web.py: regenerate proposal, approve/reject sync, and the sync tab status indicator.

**Architecture:** All changes live inside the embedded `INDEX_HTML` string in `review_web.py`. Three additions: a CSS spinner + overlay styles, two JS helper functions (`showContentLoading`, `hideContentLoading`) plus one action-bar helper (`setActionbarLoading`), and wiring calls at the appropriate points in existing async functions. No backend API changes required.

**Tech Stack:** Python 3 (Flask), vanilla JavaScript (ES5 compatible for browser compatibility), CSS3 animations, embedded HTML template string.

## Global Constraints

- Line length: 120 chars (pyproject.toml default)
- All changes in `kanka_wiki_updater/review_web.py` only — no new files
- Follow existing code style: ES5-compatible JS (`var`, function expressions, IIFEs), CSS variables from `:root`, dark theme colors matching existing palette
- No dependencies added

---

### Task 1: Add CSS spinner and loading overlay styles

**Files:**
- Modify: `kanka_wiki_updater/review_web.py:1067` (CSS section, before the closing `</style>`)

**Interfaces:**
- Consumes: nothing new
- Produces: CSS classes `.loading-overlay`, `.spinner`, `.btn.loading` and keyframes `@keyframes spin` + `@keyframes pulse-dot` for use by JS helpers

- [ ] **Step 1: Add CSS for loading spinner, overlay, and action-bar states**

Insert the following CSS rules into the `<style>` block in `INDEX_HTML`, right before line 1081 (`</style>`):

```css
/* Loading spinner */
.loading-overlay { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:240px; gap:16px; }
.spinner { width:28px; height:28px; border:3px solid var(--border); border-top-color:var(--cyan); border-radius:50%; animation:spin 0.7s linear infinite; flex-shrink:0; }
.loading-overlay .loading-text { font-size:14px; color:var(--text-dim); text-align:center; max-width:320px; line-height:1.5; }

/* Spinner keyframes */
@keyframes spin { 0%{transform:rotate(0deg)} 100%{transform:rotate(360deg)} }

/* Pulse animation for sync status dot */
@keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.3} }
.pulse-active { animation:pulse-dot 1s ease-in-out infinite; display:inline-block; }

/* Disabled button state during loading */
.btn.loading { pointer-events:none; opacity:0.6; cursor:not-allowed; transform:none !important; }
```

- [ ] **Step 2: Verify the CSS is syntactically valid**

Run: `python -c "from kanka_wiki_updater.review_web import create_app; app = create_app(); print('CSS loaded OK')"`

Expected output: `CSS loaded OK` (no errors — if there's a template syntax error, it will fail here)

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "style: add CSS spinner, overlay, and pulse animation for loading states"
```


### Task 2: Add JavaScript helper functions

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (JS section inside INDEX_HTML)

**Interfaces:**
- Consumes: nothing new — uses existing DOM helpers (`document.getElementById`, `escapeHtml`)
- Produces: three global JS functions usable by any event handler in the page:
  - `showContentLoading(message)` — replaces content area with loading overlay
  - `hideContentLoading()` — restores normal rendering via `renderContent()`
  - `setActionbarLoading(enabled, message)` — dims action bar buttons and shows inline spinner+message

- [ ] **Step 1: Add JS helper functions**

Insert the following three functions into the `<script>` block in `INDEX_HTML`, right after the `escapeHtmlForTextarea` function (around line 1359) and before the `// ── Actions ────────────────────────────────────────────────────────────────` comment:

```javascript
// ── Loading states ─────────────────────────────────────────────────────────

function showContentLoading(message) {
  var content = document.getElementById('content');
  if (!content) return;
  content.innerHTML = '<div class="loading-overlay">' +
    '<div class="spinner"></div>' +
    '<div class="loading-text">' + escapeHtml(message || 'Processing...') + '</div>' +
    '</div>';
}

function hideContentLoading() {
  renderContent();
}

function setActionbarLoading(enabled, message) {
  var bar = document.getElementById('actionBar');
  if (!bar) return;
  var buttons = bar.querySelectorAll('.btn:not([onclick*="runSync"])');
  if (enabled) {
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.add('loading');
    }
    // Remove any existing loading indicator first
    var oldInd = bar.querySelector('.actionbar-loading-indicator');
    if (oldInd) oldInd.remove();
    var ind = document.createElement('span');
    ind.className = 'actionbar-loading-indicator';
    ind.style.cssText = 'margin-left:auto;display:flex;align-items:center;gap:8px;font-size:13px;color:var(--cyan);font-weight:600;flex-shrink:0;';
    var dot = document.createElement('span');
    dot.className = 'spinner';
    dot.style.cssText = 'width:14px;height:14px;border-width:2px;';
    ind.appendChild(dot);
    if (message) {
      var txt = document.createTextNode(' ' + escapeHtml(message));
      ind.appendChild(txt);
    }
    bar.insertBefore(ind, bar.querySelector('.shortcuts'));
  } else {
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.remove('loading');
    }
    var existingInd = bar.querySelector('.actionbar-loading-indicator');
    if (existingInd) existingInd.remove();
  }
}
```

- [ ] **Step 2: Verify the JS is syntactically valid**

Run: `python -c "from kanka_wiki_updater.review_web import create_app; app = create_app(); print('JS loaded OK')"`

Expected output: `JS loaded OK` (no errors)

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "js: add showContentLoading, hideContentLoading, setActionbarLoading helpers"
```


### Task 3: Wire loading state to regenerate proposal

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (the `regenerateProposal()` function in JS)

**Interfaces:**
- Consumes: `showContentLoading()`, `hideContentLoading()` from Task 2, existing `apiCall()` and `escapeHtml()` helpers
- Produces: updated regenerate flow that shows a loading overlay during LLM call

- [ ] **Step 1: Update regenerateProposal() to use content-area loading**

Find the existing `regenerateProposal` function (around line 1533) and replace its body with this updated version. The key changes are:
1. Use `showContentLoading()` instead of appending text to the banner
2. Restore on success via `hideContentLoading()`
3. Show error inline + toast on failure

Replace the entire function body from `async function regenerateProposal() {` through its closing `}` with:

```javascript
async function regenerateProposal() {
  if (selectedIndex === null) return;
  var p = proposals[selectedIndex];
  if (!p || p.proposal_type !== 'update') { showToast('Only update proposals can be regenerated', 'error'); return; }

  // Show loading state in content area
  showContentLoading('Generating new proposal... This may take a minute.');

  var result = await apiCall('/api/proposals/' + selectedIndex + '/regenerate', 'POST');
  if (!result) { hideContentLoading(); return; }

  if (result.ok) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Regeneration successful — proposal updated with fresh LLM output.', 'success');
  } else {
    var msg = result.error || 'Regeneration failed';
    // Show error inline in the content area instead of spinner
    showContentLoading('Regeneration: <strong>' + escapeHtml(msg) + '</strong>');
    showToast(msg, 'error');
  }
}
```

Also remove the old banner-inline loading code from `renderContent()` — find this block inside renderContent (around line 1230-1235):

```javascript
  // Truncation warning with regenerate button
  var isTruncated = p.truncated === true || (p.change_summary && p.change_summary.indexOf('[TRUNCATED:') !== -1);
  if (isTruncated && p.proposal_type === 'update') {
    html += '<div class="warning" id="truncationWarning">' +
      '&#9888; This proposal may be truncated — the LLM hit its token limit and output was cut off. ' +
      '<button class="btn" onclick="regenerateProposal()" style="padding:4px 12px;font-size:12px;margin-left:12px;">Regenerate (higher max_tokens)</button>' +
      '</div>';
  } else if (isTruncated && p.proposal_type === 'new_entity') {
    html += '<div class="warning">&#9888; This new-entity suggestion may be truncated — the LLM output was cut off. Edit manually to fix.</div>';
  }
```

Replace it with:

```javascript
  // Truncation warning with regenerate button (no inline loading state)
  var isTruncated = p.truncated === true || (p.change_summary && p.change_summary.indexOf('[TRUNCATED:') !== -1);
  if (isTruncated && p.proposal_type === 'update') {
    html += '<div class="warning" id="truncationWarning">' +
      '&#9888; This proposal may be truncated — the LLM hit its token limit and output was cut off. ' +
      '<button class="btn" onclick="regenerateProposal()" style="padding:4px 12px;font-size:12px;margin-left:12px;">Regenerate (higher max_tokens)</button>' +
      '</div>';
  } else if (isTruncated && p.proposal_type === 'new_entity') {
    html += '<div class="warning">&#9888; This new-entity suggestion may be truncated — the LLM output was cut off. Edit manually to fix.</div>';
  }
```

- [ ] **Step 2: Verify regeneration flow works**

Start the server and test manually: `python -m kanka_wiki_updater.review_web` then open http://127.0.0.1:5555, select a proposal, click "Regenerate". The content area should show a spinner + message while processing, then restore or show an error on completion.

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "ui: wire loading overlay to regenerate proposal flow"
```


### Task 4: Wire loading state to approve/reject operations

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (the `approveAll`, `approveSynopsisOnly`, and `rejectCurrent` functions in JS)

**Interfaces:**
- Consumes: `setActionbarLoading()` from Task 2, existing `apiCall()`, `showToast()`, `_advance()` helpers
- Produces: updated approve/reject flows that dim action bar buttons with inline spinner during Kanka sync

- [ ] **Step 1: Update approveAll() to use action-bar loading**

Find the `approveAll` function (around line 1389) and replace it with:

```javascript
async function approveAll() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  setActionbarLoading(true, 'Syncing to Kanka...');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_all'})
    .then(function(data) {
      setActionbarLoading(false);
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync) {
        if (data.sync.warnings && data.sync.warnings.length > 0) {
          showToast('Synced with warnings: ' + data.sync.message, 'warning');
        } else if (data.sync.ok) {
          showToast('Synced to Kanka: ' + data.sync.message, 'success');
        } else {
          showToast('Kanka sync failed: ' + data.sync.message, 'error');
        }
      } else {
        showToast('Approved all', 'success');
      }
    });
}
```

- [ ] **Step 2: Update approveSynopsisOnly() to use action-bar loading**

Find the `approveSynopsisOnly` function (around line 1412) and replace it with:

```javascript
async function approveSynopsisOnly() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  setActionbarLoading(true, 'Syncing synopsis to Kanka...');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_synopsis_only'})
    .then(function(data) {
      setActionbarLoading(false);
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync) {
        if (data.sync.warnings && data.sync.warnings.length > 0) {
          showToast('Synopsis synced with warnings: ' + data.sync.message, 'warning');
        } else if (data.sync.ok) {
          showToast('Synopsis synced to Kanka: ' + data.sync.message, 'success');
        } else {
          showToast('Kanka sync failed: ' + data.sync.message, 'error');
        }
      } else {
        showToast('Synopsis approved', 'success');
      }
    });
}
```

- [ ] **Step 3: Update rejectCurrent() to use action-bar loading**

Find the `rejectCurrent` function (around line 1435) and replace it with:

```javascript
async function rejectCurrent() {
  if (selectedIndex === null) return;
  var oldIndex = selectedIndex;
  if (editingField) await saveEdit();
  setActionbarLoading(true, 'Rejecting...');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'rejected'})
    .then(function(data) {
      setActionbarLoading(false);
      if (data) { proposals[selectedIndex] = data.proposal; _advance(oldIndex); showToast('Rejected', 'error'); }
    });
}
```

- [ ] **Step 4: Verify approve/reject flows work**

Start the server, select a pending proposal, click "Approve All" — the action bar buttons should dim and show a small spinner + "Syncing to Kanka..." message. Click "Reject" — same behavior with "Rejecting...".

- [ ] **Step 5: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "ui: wire action-bar loading state to approve/reject flows"
```


### Task 5: Add pulse animation to sync tab status indicator

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` (the `renderSyncContent()` function in JS)

**Interfaces:**
- Consumes: nothing new — uses existing CSS classes from Task 1 and existing DOM APIs
- Produces: animated pulsing status dot when sync is running, static dot otherwise

- [ ] **Step 1: Update renderSyncContent() to pulse the status dot**

Find the `renderSyncContent` function (around line 1317) and update it. The key change is adding/removing a `.pulse-active` class on the status dot based on job status:

Replace the entire `function renderSyncContent(content)` with:

```javascript
function renderSyncContent(content) {
  if (currentTab !== 'sync') return;
  var jobStatus = currentSyncJob ? currentSyncJob.status : 'idle';
  var statusColor = {'running':'var(--blue)','completed':'var(--green)','error':'var(--red)','idle':'var(--text-dim)'}[jobStatus] || 'var(--text-dim)';
  var pulseClass = jobStatus === 'running' ? 'pulse-active' : '';

  content.innerHTML = '<div class="empty-state">' +
    '<h3>Run Sync Pipeline</h3>' +
    '<div class="sync-container">' +
      '<div style="margin:16px 0;">' +
        '<button class="btn btn-primary" id="runSyncBtn" onclick="runSync()">' + (currentSyncJob ? 'Cancel' : 'Run Sync') + '</button>' +
        '<span style="margin-left:12px;color:' + statusColor + ';font-weight:600;font-size:14px">&#9679; <span class="' + pulseClass + '">' + jobStatus.toUpperCase() + '</span></span>' +
      '</div>' +
      '<pre id="syncOutput" class="sync-output">' +
        (currentSyncJob && currentSyncJob.output ? escapeJsHtml(currentSyncJob.output) : 'No sync run in progress.') +
      '</pre>' +
    '</div>' +
  '</div>';
}
```

- [ ] **Step 2: Verify sync tab pulse animation**

Start the server, go to the Sync tab, click "Run Sync" — the status text should show a pulsing dot. When sync completes or is cancelled, the pulse stops and the dot returns to normal.

- [ ] **Step 3: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "ui: add pulse animation to sync tab status indicator"
```


### Task 6: Run linting, formatting, and tests

**Files:**
- Verify: `ruff check` passes on the modified file

- [ ] **Step 1: Run ruff lint and format**

Run:
```bash
ruff check kanka_wiki_updater/review_web.py
ruff format kanka_wiki_updater/review_web.py
```

Fix any lint errors that `ruff check` reports. If formatting changes are needed, run `ruff format .`.

- [ ] **Step 2: Run all tests**

Run: `pytest -v`

Expected: All existing tests pass (no new failures introduced by CSS/JS changes). The review_web tests test the Flask API endpoints which haven't changed.

- [ ] **Step 3: Final commit if needed**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "style: apply ruff formatting to review_web"
```


### Task 7: Manual end-to-end verification

**Files:**
- No file changes — manual testing only

- [ ] **Step 1: Start server and verify all loading states**

Run: `python -m kanka_wiki_updater.review_web`

Then in browser at http://127.0.0.1:5555:
1. Select a pending proposal → click "Regenerate" → verify content area shows spinner + message, then restores on completion/failure
2. Click "Approve All" (or "Reject") → verify action bar buttons dim and show inline spinner with status text
3. Switch to Sync tab → click "Run Sync" → verify the RUNNING label has a pulsing dot animation
4. Verify sync output still streams correctly beneath the status indicator

- [ ] **Step 2: Commit any fixes**

If manual testing reveals issues, fix them and commit:

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "fix: address review feedback on loading states"
```


## Plan Self-Review Checklist

1. **Spec coverage:** Each requirement from the design doc is covered: CSS spinner/overlay (Task 1), JS helpers (Task 2), regenerate wiring (Task 3), approve/reject wiring (Task 4), sync tab pulse (Task 5).
2. **Placeholder scan:** No "TBD", "TODO", or vague references — every step has exact code and commands.
3. **Type consistency:** All JS functions use ES5 syntax consistent with the existing file; CSS variable names match `:root` definitions in the same file.
4. **No new dependencies:** Only existing Python/JS/CSS in review_web.py is modified.

---

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?