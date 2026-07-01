# Kanka Wiki Review Web UI — Tabbed Interface Design

## Overview

Split the sidebar into two tabs: **New** (pending proposals) and **Reviewed** (applied + rejected). The New tab is the default view. Each tab filters its own sidebar list independently.

## Changes

### 1. Tab bar in sidebar header
- Replace the static `Proposals (N)` header with a clickable two-tab bar: `New (count)` / `Reviewed (count)`
- Active tab highlighted with cyan border-bottom and brighter text; inactive tab dimmed
- Counts update live as proposals are approved/rejected

### 2. Filtering logic
- **New tab**: sidebar shows only proposals where `status === 'pending'`
- **Reviewed tab**: sidebar shows proposals where `status === 'applied' || status === 'rejected'`
- Switching tabs re-renders the sidebar and resets selection to null (or keeps it if still in that category)

### 3. Sidebar item rendering
- Each row shows: type badge (NEW/UPD), entity name, and a colored status indicator
- Status indicators only appear on reviewed items: green `✓` for applied, red `✗` for rejected
- Pending items show an empty circle `&#9675;` as before

### 4. Content area
- Unchanged — still shows the selected proposal's details regardless of which tab is active
- Selecting a reviewed item from the Reviewed tab displays its synopsis diff and relation changes normally

### 5. Stats bar (header)
- Stays global: `X pending | Y approved | Z rejected`
- No change to this element

### 6. Action buttons & keyboard shortcuts
- Unchanged — approve/reject still work on any selected proposal regardless of active tab
- Keyboard navigation (`n`/`p`) only cycles through visible (filtered) proposals in the current tab

## Implementation Notes

All changes are within `review_web.py`'s embedded HTML/CSS/JS. No backend API changes needed — filtering happens client-side based on existing `status` field values.
