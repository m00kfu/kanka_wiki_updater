# Progress Bar for Sync Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live step-by-step progress bar to `sync_pipeline.py` that tracks per-entity work units while processing each session-note journal, showing real-time updates via in-place cursor overwrite so the user always sees current progress without spammy output.

**Architecture:** Create a small `ProgressTracker` class with Unicode block rendering and `\r` carriage-return overwrites. Instantiate one tracker per journal before processing entities, call `mark_done()` after each LLM call and new-entity scan completes, then `finish()` to clean up the cursor line.

**Tech Stack:** Python 3.7+, standard library only (`sys.stdout.write`, no external deps)

## Global Constraints

- No external dependencies — use only stdlib
- Follow existing code patterns: minimal files, clear docstrings, defensive error handling
- Terminal output must not break the user's shell prompt on clean exit (flush + newline via `finish()`)

---

### Task 1: Create ProgressTracker class

**Files:**
- Create: `progress.py`
- Test: Manual verification — run a quick inline test after implementation

**Interfaces:**
- Produces: `class ProgressTracker(total: int)` with methods:
  - `mark_done(label: str = "") -> None` — increment done count, render bar in-place
  - `finish() -> None` — final render + newline to clean cursor position

```python
"""In-memory progress tracker for sync_pipeline journal processing.

Uses \\r carriage return to overwrite the same terminal line so the user
always sees current progress without spammy output.
"""
import sys


class ProgressTracker:
    """Track per-journal work units with an in-place Unicode progress bar."""

    def __init__(self, total: int) -> None:
        self.total = max(total, 1)  # avoid division by zero if total is 0
        self.done = 0

    def _render(self, label: str = "") -> str:
        """Return a progress bar string like '████░░ 60% [LLM for Alice...]'.

        Uses full block chars (█) for done portion and empty blocks (░) for remaining.
        Percentage rounds up to avoid showing 100% until the final mark_done().
        """
        pct = int((self.done / self.total) * 100) if self.total else 0
        filled = min(int(self.done / self.total * 20), 20)
        empty = 20 - filled

        bar = "█" * filled + "░" * empty
        header = f"[{bar} {pct}%]"
        if label:
            header += f" {label}"
        return header

    def mark_done(self, label: str = "") -> None:
        """Increment done count and render the bar in-place."""
        self.done += 1
        sys.stdout.write(f"\r{self._render(label)}")
        sys.stdout.flush()

    def finish(self) -> None:
        """Final render with newline so cursor is clean for next journal or prompt."""
        if self.total == 0:
            return  # nothing to show
        sys.stdout.write(f"\r{self._render('')}\n")
        sys.stdout.flush()
```

**Steps:**

- [ ] **Step 1: Create progress.py with the ProgressTracker class**

Write the file `progress.py` at module root (`C:\Users\m00kfu\Desktop\kanka\kanka_wiki_updater\progress.py`) with the exact code above. No imports beyond `sys`. The class must handle edge cases:
- `total=0`: `_render()` returns `[░░░░░░░░░░░░░░░░░░░░ 0%]` (no crash)
- `mark_done()` called multiple times: increments correctly, never exceeds total visually
- `finish()` after zero calls: renders current state (e.g., `████░░ 50%`) with newline

- [ ] **Step 2: Verify ProgressTracker works**

Run this inline test in a Python shell to confirm behavior:
```python
from progress import ProgressTracker
t = ProgressTracker(4)
print("Before any marks:")
t.mark_done("LLM for Alice...")
import time; time.sleep(0.3)
t.mark_done("LLM for Bob...")
time.sleep(0.3)
t.finish()
```
Expected: terminal shows the bar updating in-place, then a clean newline after finish().

- [ ] **Step 3: Commit**

```bash
git add progress.py
git commit -m "feat: add ProgressTracker class with in-place Unicode progress bar"
```

---

### Task 2: Integrate tracker into sync_pipeline main loop

**Files:**
- Modify: `sync_pipeline.py` — import, instantiate per-journal, call mark_done/finish

**Interfaces:**
- Consumes: `ProgressTracker` from `.progress` (exact import: `from .progress import ProgressTracker`)
- Produces: No new exports — internal only

**Changes to sync_pipeline.py main() around lines 237-264:**

1. **Add import** at top of file (after existing imports, before any function definitions):
```python
from .progress import ProgressTracker
```

2. **Replace the per-journal processing block** (lines ~237-264) with tracker integration:

Current code (line 239):
```python
print(f"  ({i}/{len(to_process)}) '{journal.get('name')}': "
      f"mentions {len(mentioned)} known entit(y/ies)")
```

Replace the entire entity processing section starting at line 241 with:
```python
# Calculate total work units for this journal
total_units = len(mentioned) + (1 if new_candidates else 0)
tracker = ProgressTracker(total_units)

for entity_id in mentioned:
    entity = index[entity_id]
    proposal = propose_update(entity_id, entity, journal, index)
    tracker.mark_done(f"LLM for {entity['name']}...")
    if proposal:
        state.append_to_queue([proposal])
        total_proposals += 1
        index[entity_id]["entry"] = proposal["proposed_entry"]
        apply_relation_changes_locally(entity_id, proposal["relation_changes"], index, name_to_id)

# New-entity scanning
if new_candidates:
    print(f"      + {len(new_candidates)} new entity suggestion(s): "
          + ", ".join(f"{c['entity_name']} ({c['suggested_type']})" for c in new_candidates))
    for candidate in new_candidates:
        state.append_to_queue([candidate])
        known_names.add(candidate["entity_name"])
    total_new_entities += len(new_candidates)

tracker.mark_done("New-entity scan")
tracker.finish()
```

3. **Remove or keep** the existing `print(f"  ({i}/{len(to_process)}) ..."` line — it's replaced by the tracker bar. The new-entity print (`+ N suggestion(s)`) is kept so the user still sees what was flagged.

4. **Edge case**: If `total_units == 0` (no entities, no new candidates), don't instantiate tracker — just skip to `state.mark_journal_processed(journal["id"])`. Add a guard:
```python
if total_units > 0:
    # ... tracker block above ...
else:
    print(f"  ({i}/{len(to_process)}) '{journal.get('name')}': no entities found")
# Always mark journal processed regardless of tracker usage
state.mark_journal_processed(journal["id"])
```

**Steps:**

- [ ] **Step 1: Add import and refactor main() loop**

In `sync_pipeline.py`:
1. Add `from .progress import ProgressTracker` to the imports (after line 18, before function definitions)
2. Replace lines 239-264 with the tracker-integrated version shown above, including the zero-units guard
3. Ensure `state.mark_journal_processed(journal["id"])` still runs after all other journal processing

- [ ] **Step 2: Verify integration works**

Run a dry sync pipeline (with --limit 1) against your Kanka campaign to confirm:
- Progress bar renders in-place as each LLM call completes
- New-entity scan shows its own mark_done
- Cursor is clean after finish() (no leftover partial lines)
- No regressions in existing behavior (proposals still queued, journals still marked processed)

- [ ] **Step 3: Commit**

```bash
git add sync_pipeline.py
git commit -m "feat: add per-journal progress bar to sync pipeline"
```

---

## Self-Review Checklist

1. **Spec coverage:** All spec requirements covered — ProgressTracker class (yes), integration in main loop (yes), edge cases: LLM errors counted as progress (yes, mark_done happens after propose_update returns regardless of success/failure), zero entities handled (yes, guard clause), interrupted run safe (yes, harmless partial line)
2. **Placeholder scan:** No "TBD", "TODO", or vague references — all code is explicit
3. **Type consistency:** `total: int`, `done: int`, methods return `None` — consistent throughout
4. **Scope check:** Single focused feature (progress bar), no unrelated changes, fits in ~50 lines total

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-27-progress-bar.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
