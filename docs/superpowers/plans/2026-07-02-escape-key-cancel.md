# Escape Key Cancel Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to press Escape to cancel synopsis editing in the review_web UI, with confirmation if changes were made.

**Architecture:** Add a dedicated `keydown` listener for `'Escape'` that checks whether any textarea content changed since edit mode started; if so, prompt with native `confirm()` before calling existing `cancelEdit()`. Store original text when entering edit mode via new `editingOriginal` variable.

**Tech Stack:** Vanilla JavaScript (embedded in Flask template string), no dependencies.

## Global Constraints

- Single file change: `kanka_wiki_updater/review_web.py`
- Only frontend changes — no backend API modifications
- Follow existing code style: `var/let` declarations, camelCase functions, JSDoc-style comments where present
- Line length: 120 chars (project convention per `pyproject.toml`)

---

### Task 1: Add Escape key listener to cancel editing

**Files:**
- Modify: `kanka_wiki_updater/review_web.py:362` (shortcuts hint line)
- Modify: `kanka_wiki_updater/review_web.py:364-368` (shortcuts panel HTML)
- Modify: `kanka_wiki_updater/review_web.py:640-668` (add `editingOriginal`, add Escape listener after existing shortcuts handler)

**Interfaces:**
- Consumes: existing `cancelEdit()` function (line 665), existing `startEdit()` function (line 640)
- Produces: new `editingOriginal` variable, new Escape keydown event listener

**Steps:**

- [ ] **Step 1: Update shortcuts display to show `[esc] cancel` hint**

In the HTML template string `INDEX_HTML`, update two places:

1. Line ~362 — the action bar shortcut text (after `[q]uit`):
```html
<span style="font-size:12px;color:var(--text-dim)">[n]ext [p]rev [e]dit [esc]cancel [a]pprove [s]ynopsis [r]eject [q]uit</span>
```

2. Lines ~364-368 — the shortcuts panel (add a new row):
```html
<div class="shortcuts">
  <kbd>n</kbd> next &nbsp; <kbd>p</kbd> prev<br>
  <kbd>e</kbd> edit &nbsp; <kbd>esc</kbd> cancel<br>
  <kbd>a</kbd> approve all &nbsp; <kbd>s</kbd> synopsis only<br>
  <kbd>r</kbd> reject &nbsp; <kbd>q</kbd> quit (close tab)
</div>
```

- [ ] **Step 2: Store original text when entering edit mode**

In the `startEdit(field)` function (around line 640), add a line to capture the current textarea content before focusing. Find this existing code:

```javascript
function startEdit(field) {
  editingField = field;
  renderContent();
  var editor = document.getElementById('synopsisEditor');
  if (editor) { editor.focus(); editor.selectionStart = editor.value.length; }
}
```

Replace with:

```javascript
function startEdit(field) {
  editingField = field;
  renderContent();
  var editor = document.getElementById('synopsisEditor');
  if (editor) {
    editingOriginal = editor.value;
    editor.focus();
    editor.selectionStart = editor.value.length;
  }
}
```

- [ ] **Step 3: Add dedicated Escape key listener**

After the existing `document.addEventListener('keydown', ...)` block that ends at line 748 (after the closing `});`), add a new event listener for Escape. Insert this code right after line 748:

```javascript
// ── Escape key to cancel editing ───────────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (e.key !== 'Escape' || !editingField) return;
  var editor = document.getElementById('synopsisEditor');
  if (!editor) return;
  var hasChanges = editor.value !== editingOriginal;
  if (hasChanges && !confirm('Discard unsaved changes?')) return;
  cancelEdit();
});
```

This listener:
- Only fires for `'Escape'` key and when `editingField` is truthy
- Gets the textarea element to compare content
- Uses native `confirm()` dialog if content differs — cancels (returns early) if user clicks "Cancel"
- Calls existing `cancelEdit()` on confirm or no changes

- [ ] **Step 4: Manual verification**

Run the app and verify manually:
```bash
python -m kanka_wiki_updater.review_web
```

Then in browser at http://127.0.0.1:5555:
1. Select a pending proposal from sidebar
2. Press `e` to enter edit mode — notice the textarea appears with Save/Cancel banner
3. Make some changes to the text, then press Escape → confirm dialog should appear
4. Click "Cancel" in the dialog → editing should remain active (text unchanged)
5. Press Escape again → click "OK" in dialog → editing should be cancelled, diff view restored
6. Press `e` to enter edit mode, DON'T make changes, press Escape → editing should cancel immediately (no confirm dialog)

- [ ] **Step 5: Run linting**

```bash
ruff check kanka_wiki_updater/review_web.py
```

Expected: no new errors. If any style warnings appear from the JavaScript inside the template string, they can be ignored (ruff checks Python only).
