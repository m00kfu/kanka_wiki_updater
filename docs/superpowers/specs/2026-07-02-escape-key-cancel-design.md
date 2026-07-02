# Escape Key to Cancel Editing in review_web

**Date:** 2026-07-02  
**File:** `kanka_wiki_updater/review_web.py` (INDEX_HTML template)

## Problem

When editing a proposal's synopsis in the web review UI, users must click the "Cancel" button in the edit banner to exit edit mode. There is no keyboard shortcut for this action.

## Solution

Add Escape key support to cancel editing with unsaved-change confirmation.

### Changes

1. **Store original text on edit start** — In `startEdit()`, save the textarea's initial content to a new variable (`editingOriginal`) so we can detect if anything changed.

2. **Dedicated Escape key listener** — Add a separate `keydown` event listener for `'Escape'`. When pressed during editing mode:
   - Compare current textarea value against `editingOriginal`
   - If content differs → show native `confirm()` dialog asking "Discard unsaved changes?"
   - If user confirms or no changes were made → call `cancelEdit()`

3. **Update shortcuts display** — Add `[esc] cancel` to the keyboard shortcut hints at the bottom of the page (lines 362 and 364-368).

### Scope

- Single file change: `review_web.py`
- Only frontend (embedded HTML/JS in INDEX_HTML string)
- No backend API changes
- No new dependencies

### Behavior

| Scenario | Action |
|---|---|
| Press Escape, no changes made | Immediately cancel edit |
| Press Escape, changes were made | Show confirm dialog; cancel only if confirmed |
| Press Escape while not in edit mode | No effect (listener checks `editingField`) |