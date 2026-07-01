# Review Web UI — Tabbed Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the review web UI sidebar into two tabs (New / Reviewed) with filtered lists and color-coded status indicators.

**Architecture:** All changes are client-side within `review_web.py`'s embedded HTML/CSS/JS. No API changes needed — filtering uses existing `status` field values (`pending`, `applied`, `rejected`).

**Tech Stack:** Python 3, Flask (embedded template), vanilla JS, CSS custom properties.

## Global Constraints

- Line length: 120 chars
- Follow existing code patterns in review_web.py
- No external dependencies added
- All changes within the single embedded INDEX_HTML string

---

### Task 1: Add tab bar HTML and CSS styles

**Files:**
- Modify: `review_web.py:144-218` (CSS section)

**Interfaces:**
- Consumes: existing CSS variables (`--cyan`, `--text-dim`, etc.)
- Produces: `.tab-bar`, `.tab-btn`, `.tab-btn.active`, `.tab-btn.inactive` styles

**Steps:**

- [ ] **Step 1: Add tab bar HTML to sidebar header**

Replace the static `<div class="sidebar-header">Proposals (N)</div>` in `renderSidebar()` with a dynamic tab bar. In the template string, add this after line 233 (`<div class="sidebar" id="sidebar"></div>`):

```html
<div class="tab-bar" id="tabBar">
  <button class="tab-btn active" data-tab="new" onclick="switchTab('new')">New</button>
  <button class="tab-btn inactive" data-tab="reviewed" onclick="switchTab('reviewed')">Reviewed</button>
</div>
```

- [ ] **Step 2: Add CSS for tab bar**

Add these styles to the `<style>` block (after line 159, before `.proposal-item`):

```css
.tab-bar { display: flex; border-bottom: 1px solid var(--border); padding: 0 8px; }
.tab-btn { background: none; border: none; color: var(--text-dim); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; padding: 10px 12px; cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s; }
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--cyan); border-bottom-color: var(--cyan); }
.tab-btn.inactive { opacity: 0.6; }
```

- [ ] **Step 3: Verify syntax**

Check that the HTML template string is still valid (no unescaped quotes or broken concatenation).

---

### Task 2: Add tab switching logic and sidebar filtering

**Files:**
- Modify: `review_web.py:252-537` (JavaScript section)

**Interfaces:**
- Consumes: existing `proposals` array, `selectedIndex`, `renderSidebar()`, `renderContent()`
- Produces: `currentTab` variable, `switchTab(tab)` function, updated `renderSidebar()` with filtering

**Steps:**

- [ ] **Step 1: Add tab state and switcher function**

Add after line 254 (`let selectedIndex = null;`) and before `getPending()`:

```javascript
let currentTab = 'new'; // default tab

function switchTab(tab) {
  if (tab === currentTab) return;
  currentTab = tab;
  selectedIndex = null; // reset selection on tab change
  renderSidebar();
  renderContent();
}
```

- [ ] **Step 2: Update renderSidebar() to filter by active tab**

Replace the existing `renderSidebar()` function (lines 269-281) with a filtered version. The key changes:
1. Filter proposals based on `currentTab` — show only pending for 'new', applied/rejected for 'reviewed'
2. Update sidebar header to show tab counts: "New (N) | Reviewed (M)"
3. Add color-coded status indicators: green ✓ for applied, red ✗ for rejected

```javascript
function renderSidebar() {
  const sidebar = document.getElementById('sidebar');
  let html = '<div class="tab-bar" id="tabBar">';
  html += '<button class="tab-btn ' + (currentTab === 'new' ? 'active' : 'inactive') + '" data-tab="new" onclick="switchTab(\'new\')">New (' + proposals.filter(p => p.status === 'pending').length + ')</button>';
  html += '<button class="tab-btn ' + (currentTab === 'reviewed' ? 'active' : 'inactive') + '" data-tab="reviewed" onclick="switchTab(\'reviewed\')">Reviewed (' + proposals.filter(p => p.status !== 'pending').length + ')</button></div>';
  
  const filtered = currentTab === 'new' 
    ? proposals.filter(p => p.status === 'pending')
    : proposals.filter(p => p.status === 'applied' || p.status === 'rejected');
  
  filtered.forEach(function(p) {
    // Find original index in proposals array for onclick handler
    const origIndex = proposals.indexOf(p);
    var isActive = origIndex === selectedIndex ? ' active' : '';
    var kind = p.proposal_type === 'new_entity' ? 'NEW' : 'UPD';
    var badgeClass = p.proposal_type === 'new_entity' ? 'badge-new' : 'badge-upd';
    
    let statusBadge = '';
    if (p.status === 'applied') {
      statusBadge = '<span style="color:var(--green)">✓</span>';
    } else if (p.status === 'rejected') {
      statusBadge = '<span style="color:var(--red)">✗</span>';
    }
    
    html += '<div class="proposal-item' + isActive + '" onclick="selectProposal(' + origIndex + ')">' +
      '<div class="name"><span class="badge ' + badgeClass + '">' + kind + '</span>' + escapeHtml(p.entity_name) + statusBadge + '</div>' +
      '<div class="meta">' + escapeHtml(p.source_journal) + '</div></div>';
  });
  
  sidebar.innerHTML = html;
}
```

- [ ] **Step 3: Update keyboard navigation to respect filtered list**

Replace the existing `case 'n':` and `case 'p':` in the keydown handler (lines 519-520) with filtered versions:

```javascript
case 'n': {
  const visibleIndices = getVisibleIndices();
  const currentPos = visibleIndices.indexOf(selectedIndex);
  if (currentPos !== null && currentPos < visibleIndices.length - 1) {
    selectedIndex = visibleIndices[currentPos + 1];
    renderSidebar();
    renderContent();
  }
  break;
}
case 'p': {
  const visibleIndices = getVisibleIndices();
  const currentPos = visibleIndices.indexOf(selectedIndex);
  if (currentPos !== null && currentPos > 0) {
    selectedIndex = visibleIndices[currentPos - 1];
    renderSidebar();
    renderContent();
  }
  break;
}
```

Add the helper function after `getPending()`:

```javascript
function getVisibleIndices() {
  if (currentTab === 'new') {
    return proposals.reduce((acc, p, i) => { if (p.status === 'pending') acc.push(i); return acc; }, []);
  } else {
    return proposals.reduce((acc, p, i) => { if (p.status !== 'pending') acc.push(i); return acc; }, []);
  }
}
```

- [ ] **Step 4: Update selectProposal to handle tab switching**

Modify `selectProposal()` (line 381) to preserve the current tab when selecting items from different tabs:

```javascript
function selectProposal(i) {
  selectedIndex = i;
  if (editingField) cancelEdit();
  renderSidebar();
  renderContent();
}
```

(No change needed — it already works correctly since we pass the original index.)

- [ ] **Step 5: Verify JavaScript syntax**

Check for any broken string concatenation or missing semicolons.

---

### Task 3: Update stats and initialization

**Files:**
- Modify: `review_web.py:258-267` (updateStats function)
- Modify: `review_web.py:531-534` (initialization)

**Steps:**

- [ ] **Step 1: Keep updateStats() unchanged**

The stats bar already shows global counts (`pending | approved | rejected`) — no changes needed.

- [ ] **Step 2: Verify initialization uses 'new' as default tab**

Line 534 initializes with `selectProposal(0)` if proposals exist. Since `currentTab` defaults to `'new'`, this correctly selects the first pending proposal (or first overall if none are pending). No changes needed.

- [ ] **Step 3: Run ruff check on review_web.py**

```bash
ruff check review_web.py
```

Expected: PASS (no lint errors from JS/HTML string changes)

---

### Task 4: Manual testing and verification

**Steps:**

- [ ] **Step 1: Start the server**

```bash
python -m kanka_wiki_updater.review_web
```

- [ ] **Step 2: Verify tab behavior**

1. Open `http://127.0.0.1:5555` — should default to "New" tab with pending proposals only
2. Click "Reviewed" tab — sidebar should show applied/rejected items with ✓/✗ indicators
3. Switch back to "New" — sidebar filters again, selection resets
4. Select an item in each tab — content area shows correct proposal details

- [ ] **Step 3: Verify keyboard shortcuts**

1. With New tab active, press `n`/`p` — should cycle through pending items only
2. Switch to Reviewed tab, press `n`/`p` — should cycle through reviewed items only

- [ ] **Step 4: Verify approve/reject from either tab**

1. Select a pending item in New tab → click "Reject" → item moves to Reviewed with ✗
2. Select an applied item in Reviewed tab → click "Reject" → status updates to ✗

---

## Implementation Notes

- All changes are within the single `review_web.py` file — no new files created
- The filtering logic is client-side only; the API already returns all proposals with correct status fields
- Tab state (`currentTab`) persists during the session but resets on page reload (acceptable UX)
- Color coding uses existing CSS variables: `--green` for applied, `--red` for rejected
