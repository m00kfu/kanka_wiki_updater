# Progress Bar for Sync Pipeline

## Overview

Add a live step-by-step progress bar to `sync_pipeline.py` that tracks per-entity work units while processing each session-note journal, showing real-time updates via in-place cursor overwrite so the user always sees current progress without spammy output.

## Design Decisions

### ProgressTracker class
- **Location**: New file `progress.py` in the module (~30 lines)
- **API**: 
  - `__init__(total)` — total work units for this journal
  - `mark_done(label="")` — increment done count, render bar in-place with optional label
  - `finish()` — final render with newline to clean up cursor position
- **Rendering**: Unicode block characters (`████░░`) + percentage (e.g., `████░░ 60%`)

### Integration points in sync_pipeline.py
1. Before processing each journal, calculate total units: `len(mentioned_entities) + 1` (+1 for new-entity scan)
2. After each LLM call completes (`propose_update` returns), call `mark_done(f"LLM for {entity['name']}...")`
3. After new-entity scanning completes, call `mark_done()` once
4. Call `finish()` after all units are done

### Edge cases handled
- **LLM errors**: Still count as progress (bar advances regardless of success/failure) — the original code already catches exceptions per entity and continues to the next one
- **Zero entities mentioned**: No bar needed for that journal; tracker simply never renders until `finish()` is called with 0/total
- **Interrupted run (Ctrl+C)**: Terminal may leave a partial `\r` line, but harmless — next print clears it

## Implementation Approach
1. Create `progress.py` with the `ProgressTracker` class
2. Import and use it in `sync_pipeline.py` main loop
3. No changes to any other files needed
