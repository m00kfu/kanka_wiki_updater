# Review Web Loading States Design

**Date:** 2026-07-11  
**File:** `kanka_wiki_updater/review_web.py` (embedded HTML/JS single-page app)

## Problem

Three operations in the review web UI have no or minimal visual feedback while running:
1. **Regenerate proposal** — LLM call takes 5–60 seconds, currently shows only "Generating..." appended to a warning banner
2. **Approve / Reject sync-to-Kanka** — HTTP calls to Kanka take several seconds each, users see no indication anything is happening
3. **Sync tab status** — the RUNNING indicator is static text with no animation

## Solution: Content-Area Loading Overlay (Approach B)

### 1. CSS Additions (~40 lines)

- `.loading-overlay` — centered flex container for content-area loading state
- `@keyframes spin` — CSS-only rotating arc spinner, 28px diameter, cyan color
- `.btn.loading` — disables button with reduced opacity + pointer-events-none during operation
- Pulse animation on sync status dot: `@keyframes pulse-dot` oscillating opacity

### 2. JavaScript Helper Functions (~30 lines)

**`showContentLoading(message)`**  
Replaces the content area (`#content`) inner HTML with a loading overlay centered vertically and horizontally within the existing content container. Contains a spinning div + message text.

**`hideContentLoading()`**  
Restores normal content rendering by calling `renderContent()`.

**`setActionbarLoading(enabled, message)`**  
When enabled: dims all buttons in `#actionBar`, appends an inline spinner + status message to the right side of the action bar. When disabled: restores button states and removes the indicator.

### 3. Regenerate Proposal Flow

```
User clicks "Regenerate" 
  → showContentLoading("Generating new proposal... This may take a minute.")
  → apiCall('/api/proposals/INDEX/regenerate', 'POST')
    → success: hideContentLoading() → update proposals array → renderSidebar() + renderContent()
    → failure: replace spinner with error message div → showToast(error, 'error')
```

### 4. Approve / Reject Flow

```
User clicks "Approve All" 
  → setActionbarLoading(true, "Syncing to Kanka...")
  → apiCall('/api/proposals/INDEX/status', 'POST', {status: 'approved_all'})
    → success: setActionbarLoading(false) → update state → showToast('Approved', 'success')
    → failure: setActionbarLoading(false, "Sync failed — see error below") → showToast(error, 'error')
```

Same pattern for `approveSynopsisOnly()` and `rejectCurrent()`.

### 5. Sync Tab Enhancement

- The status dot (`&#9679;`) in the sync tab gets a `.pulse` CSS class when status is "running"
- Pulse animation: oscillates opacity between 1.0 and 0.3 over 1s, infinite
- No other changes needed — sync already streams output via SSE

## Files Modified

Only `kanka_wiki_updater/review_web.py` — all changes are within the embedded `INDEX_HTML` string (CSS + JS). No backend API changes required.
