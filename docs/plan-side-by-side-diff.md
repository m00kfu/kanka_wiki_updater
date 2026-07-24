# Implementation Plan: Side-by-Side Diff View

## Problem Statement

The current unified diff shows old and new lines stacked vertically. For longer synopses this forces vertical scrolling to compare what changed. A side-by-side view lets reviewers see both versions at once, aligned by context.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Diff algorithm** | LCS-based (not Myers) | Simpler to implement inline in vanilla JS; produces clean paired hunks for side-by-side alignment |
| **Layout** | Flexbox two-column, single scrollable container | Keeps both columns always visible together; no need for complex sync-scroll logic |
| **Toggle location** | Small icon button next to the Synopsis heading | Non-intrusive, discoverable via hover |
| **Default mode** | Unified (current behavior) | No regression for existing users |
| **New entities** | Toggle only shown for update proposals | New entities have no "previous" to compare against; side-by-side is meaningless. |

## Files Modified

1. **`kanka_wiki_updater/static/js/app.js`**
2. **`kanka_wiki_updater/static/css/style.css`**

---

## Changes in Detail

### 1. `app.js` — State & Toggle

Add a new state variable near the top (after line 3):

```js
let diffViewMode = 'unified'; // or 'side-by-side'
```

Add a toggle function:

```js
function toggleDiffView() {
  if (editingField) return;
  diffViewMode = diffViewMode === 'unified' ? 'side-by-side' : 'unified';
  renderContent();
}
```

### 2. `app.js` — LCS Diff Function

Add a new helper function that computes the Longest Common Subsequence of two line arrays and returns an array of operations:

```js
function computeDiff(oldLines, newLines) {
  // Returns array of {type:'keep'|'del'|'add', leftLine?, rightLine?}
  var m = oldLines.length, n = newLines.length;
  
  // Build LCS table (top-down with memoization to avoid stack overflow on large inputs)
  var dp = [];
  for (var i = 0; i <= m; i++) {
    dp[i] = new Array(n + 1).fill(0);
  }
  for (var i = m - 1; i >= 0; i--) {
    for (var j = n - 1; j >= 0; j--) {
      if (oldLines[i] === newLines[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }
  
  // Backtrack to produce operations
  var ops = [];
  var i = 0, j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && oldLines[i] === newLines[j]) {
      ops.push({ type: 'keep', line: oldLines[i] });
      i++; j++;
    } else if (i < m && (j >= n || dp[i][j] === dp[i + 1][j])) {
      ops.push({ type: 'del', line: oldLines[i] });
      i++;
    } else {
      ops.push({ type: 'add', line: newLines[j] });
      j++;
    }
  }
  return ops;
}
```

### 3. `app.js` — Unified Render (Refactored)

The current diff rendering code (lines ~148-165) should be extracted into a reusable function:

```js
function renderUnifiedDiff(prevText, newText) {
  var prevLines = stripHtml(prevText).split('\n');
  var newLines = stripHtml(newText || '').split('\n');
  var ops = computeDiff(prevLines, newLines);
  
  var html = '';
  for (var i = 0; i < ops.length; i++) {
    if (ops[i].type === 'keep') {
      html += '<div class="diff-line" style="padding-left:20px;color:var(--text-dim)">' + escapeJsHtml(ops[i].line) + '</div>';
    } else if (ops[i].type === 'del') {
      html += '<div class="diff-line diff-del">' + escapeJsHtml(ops[i].line) + '</div>';
    } else {
      html += '<div class="diff-line diff-add">' + renderJournalLinks(ops[i].line) + '</div>';
    }
  }
  return html;
}
```

Replace the current inline diff block with a call to this function.

### 4. `app.js` — Side-by-Side Render (New Function)

```js
function renderSideBySideDiff(prevText, newText) {
  var prevLines = stripHtml(prevText).split('\n');
  var newLines = stripHtml(newText || '').split('\n');
  var ops = computeDiff(prevLines, newLines);
  
  // Group operations into rows: each row has at most one old-line and one new-line
  var rows = [];
  for (var i = 0; i < ops.length; i++) {
    if (ops[i].type === 'keep') {
      rows.push({ left: ops[i].line, right: ops[i].line });
    } else if (ops[i].type === 'del' && i + 1 < ops.length && ops[i + 1].type === 'add') {
      // Paired change — show old and new on same row
      rows.push({ left: ops[i].line, right: ops[i + 1].line });
      i++; // skip the add since we paired it
    } else if (ops[i].type === 'del') {
      rows.push({ left: ops[i].line, right: null });
    } else {
      rows.push({ left: null, right: ops[i].line });
    }
  }
  
  var html = '<div class="diff-columns">';
  // Left column header + content
  html += '<div class="diff-column diff-column-left"><div class="diff-col-header">Previous</div>';
  for (var r = 0; r < rows.length; r++) {
    if (rows[r].left !== null) {
      html += '<div class="diff-line diff-del">' + escapeJsHtml(rows[r].left) + '</div>';
    } else {
      html += '<div class="diff-line diff-empty"></div>';
    }
  }
  html += '</div>';
  
  // Right column header + content
  html += '<div class="diff-column diff-column-right"><div class="diff-col-header">Proposed</div>';
  for (var r = 0; r < rows.length; r++) {
    if (rows[r].right !== null) {
      html += '<div class="diff-line diff-add">' + renderJournalLinks(rows[r].right) + '</div>';
    } else {
      html += '<div class="diff-line diff-empty"></div>';
    }
  }
  html += '</div></div>';
  
  return html;
}
```

### 5. `app.js` — Integrate Toggle into Synopsis Heading

In the existing code where the Synopsis heading is rendered (~line 146), change:

```js
html += '<div class="diff-section"><h3>' + (p.proposal_type === 'new_entity' ? 'Draft Synopsis' : 'Synopsis') + '</h3><div class="diff-container">';
```

To include a toggle button for update proposals only:

```js
var heading = p.proposal_type === 'new_entity' ? 'Draft Synopsis' : 'Synopsis';
var toggleBtn = '';
if (p.proposal_type !== 'new_entity') {
  var isSideBySide = diffViewMode === 'side-by-side';
  toggleBtn = '<button class="diff-view-toggle" onclick="toggleDiffView()" title="Switch to side-by-side view">' +
    (isSideBySide ? '&#9775; Unified' : '&#9642; Side-by-Side') + '</button>';
}
html += '<div class="diff-section"><h3>' + heading + ' ' + toggleBtn + '</h3><div class="diff-container">';
```

Then inside the `else` block (non-editing, non-new-entity), replace the inline diff loop with:

```js
if (p.proposal_type === 'new_entity') {
  // ... existing new entity rendering ...
} else if (diffViewMode === 'side-by-side') {
  html += renderSideBySideDiff(p.previous_entry, p.proposed_entry);
} else {
  html += renderUnifiedDiff(p.previous_entry, p.proposed_entry);
}
```

### 6. `style.css` — Side-by-Side Styles

Add these new styles:

```css
/* ── Diff view toggle button ─────────────────────────── */
.diff-section h3 {
  display: flex;
  align-items: center;
  gap: 8px;
}
.diff-view-toggle {
  background: none;
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s;
  margin-left: auto;
}
.diff-view-toggle:hover {
  color: var(--text);
  border-color: var(--amber);
  background: var(--amber-dim);
}

/* ── Side-by-side diff columns ──────────────────────── */
.diff-columns {
  display: flex;
  gap: 0;
  min-height: 60px;
}
.diff-column {
  flex: 1;
  overflow-y: auto;
  max-height: 70vh;
}
.diff-column-left {
  border-right: 2px solid var(--border);
}
.diff-col-header {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: var(--text-muted);
  padding: 6px 14px;
  position: sticky;
  top: 0;
  background: #0a0b0e;
  border-bottom: 1px solid var(--border);
  z-index: 2;
}
.diff-line.diff-empty {
  visibility: hidden; /* keeps row alignment */
  min-height: calc(1em * 1.7);
}

/* ── Empty diff state ───────────────────────────────── */
.diff-container .empty-diff {
  padding: 20px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}
```

### 7. `style.css` — Update Existing Diff Styles

The existing `.diff-line`, `.diff-add`, `.diff-del` styles remain unchanged for unified mode. The side-by-side columns use the same classes so no duplication needed.

---

## Edge Cases to Handle

| Case | Behavior |
|---|---|
| Both old and new are empty | Show "No changes" message in both columns |
| Only additions (new entity treated as update) | Left column shows "(none)", right shows all lines green |
| Only deletions | Left shows all red, right is empty |
| Very long synopses (>50 lines) | Columns have `max-height: 70vh` with internal scroll; headers are sticky |
| User enters edit mode while in side-by-side | Edit textarea replaces the diff (existing behavior preserved) |
| Switching tabs while editing | Existing `cancelEdit()` call in `switchTab()` handles cleanup |

## Testing Approach

1. Open an update proposal → verify unified view looks identical to current behavior
2. Click "Side-by-Side" toggle → verify two columns appear with correct alignment
3. Toggle back to Unified → verify it returns to original state
4. Test with: all additions, all deletions, mixed changes, empty fields
5. Verify the edit mode textarea still works in both modes
6. Check that new entity proposals don't show the toggle button

## What This Does NOT Do (Future Work)

- **Inline word-level highlighting** within changed lines (#1 from earlier suggestions)
- **Auto-scroll sync** between columns when one is scrolled
- **Character-level diff** for very small changes (#4)
- **Change stats bar** above the diff (#5)

These can be layered on top of this foundation later.
