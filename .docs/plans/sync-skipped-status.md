# Plan: Add "skipped" status for LLM-evaluated entities with no proposal

## Problem

During a sync run, entities are marked as `done` when the LLM processes them — regardless of whether a proposal was actually queued. When the LLM decides an entity doesn't need a wiki update (no meaningful change), it's still shown as "done" with a checkmark, which is misleading. The user sees "done" and expects a corresponding entry in the Proposals tab that isn't there.

## Solution

Add a new `skipped` status to distinguish:
- **`done`** → LLM processed entity AND queued a proposal (actual success)
- **`skipped`** → LLM processed entity but decided no meaningful change needed (intentional skip, not an error)

---

## Files to modify

### 1. `kanka_wiki_updater/sync_events.py`

Add `'skipped'` to the allowed statuses tuple:

```python
ENTITY_STATUSES = ('pending', 'processing', 'done', 'error', 'skipped')
```

No other changes needed here — this is a single-line addition.

---

### 2. `kanka_wiki_updater/ingest_journal.py`

**Change**: In the `llm_result` callback data, distinguish between "LLM error" and "no proposal queued".

Currently in `run_ingest()`, when no meaningful change is detected:
```python
else:
    # No meaningful change (None or non-error dict) — still done,
    # just no proposal queued. Don't show as an error to the user.
    entity_ok[eid] = True

# Emit completion status IMMEDIATELY after this entity's LLM call completes
ok = entity_ok.get(eid, False)
error_msg = 'LLM call failed' if not ok else None
cbs['llm_result'](entity['name'], journal.get('name', ''), ok, error_msg)
```

The `else` branch sets `entity_ok[eid] = True`, which means the entity is treated as successful. But we need to signal that no proposal was queued so the downstream callback can set status to `'skipped'`.

**Approach**: Pass a dict with `_no_proposal: True` as the data argument instead of just `None`:

```python
else:
    # No meaningful change — LLM decided skip. Mark ok=True (not an error)
    # but pass _no_proposal flag so downstream knows no proposal was queued.
    entity_ok[eid] = True
    llm_data = {'_no_proposal': True}

# Emit completion status IMMEDIATELY after this entity's LLM call completes
ok = entity_ok.get(eid, False)
error_msg = 'LLM call failed' if not ok else None
cbs['llm_result'](entity['name'], journal.get('name', ''), ok, llm_data if not ok else error_msg)
```

Wait — this changes the callback signature semantics. Let me reconsider.

**Better approach**: Keep `ok` as-is (True for both success and skip), but pass a dict payload that carries the `_no_proposal` flag:

In the LLM-error branch, we already pass `{'_llm_error': str(e)}` — so the callback already receives dicts in error cases. We just need to also pass a dict when skipping:

```python
else:
    # No meaningful change — LLM decided skip (not an error).
    entity_ok[eid] = True
    llm_data = {'_no_proposal': True}

# ... later ...
if isinstance(proposal, dict) and 'proposal_type' in proposal:
    state.append_to_queue([proposal])
    total_proposals += 1
    cbs['proposal_queued'](proposal)
    journal_entity_count += 1
    entity_ok[eid] = True
    llm_data = None  # normal success path
elif isinstance(proposal, dict) and '_llm_error' in proposal:
    entity_ok[eid] = False
    llm_data = proposal  # contains _llm_error key
else:
    entity_ok[eid] = True
    llm_data = {'_no_proposal': True}

ok = entity_ok.get(eid, False)
error_msg = 'LLM call failed' if not ok else None
cbs['llm_result'](entity['name'], journal.get('name', ''), ok, llm_data)
```

This way:
- `ok=True, data=None` → normal success (proposal queued) → status=`done`
- `ok=False, data={'_llm_error': ...}` → LLM error → status=`error`  
- `ok=True, data={'_no_proposal': True}` → no proposal needed → status=`skipped`

---

### 3. `kanka_wiki_updater/review_web.py`

**Change**: In `on_llm_result`, map the new signal to `'skipped'`:

```python
def on_llm_result(entity_name, journal_name, ok, data):
    key = (journal_name, entity_name)
    
    if isinstance(data, dict) and data.get('_no_proposal'):
        status = 'skipped'
        error_msg = None
    elif not ok:
        status = 'error'
        error_msg = str(data) if data else 'LLM call failed'
    else:
        status = 'done'
        error_msg = None
    
    with _sync_lock:
        entry = progress.get(key, {})
        entry['status'] = status
        if error_msg:
            entry['error_message'] = error_msg
        progress[key] = entry
    emitter.entity_progress(
        status,
        name=entity_name,
        journal_name=journal_name,
        error_message=error_msg,
    )
```

**Also**: In `on_journal_completed`, don't override `'skipped'` back to `'done'`:

```python
def on_journal_completed(journal_name, entities_processed, suggestions_count):
    keys_to_update = []
    with _sync_lock:
        for key in list(progress.keys()):
            if key[0] == journal_name and progress[key]['status'] in ('pending', 'processing'):
                # Only update pending/processing — don't override done/skipped/error
                progress[key]['status'] = 'done'
                keys_to_update.append(key)
    # ... rest unchanged (already only updates pending/processing)
```

This is already correct — the existing code only transitions `pending` and `processing`, so a `'skipped'` entity set by `on_llm_result` won't be overridden.

---

### 4. `kanka_wiki_updater/static/js/app.js`

**Change A**: Add icon for `'skipped'` in the sync entities list (line ~286):

```javascript
var iconMap = {'pending':'&#9675;', 'processing':'&#8635;', 'done':'&#10003;', 'error':'&#10007;', 'skipped':'&#8500;'};
```

`&#8500;` is ⟐ (circle with dot) — or we could use `↩` (U+21A9, left arrow hook) for "returned/skipped". I'd recommend **`↩`** as it visually suggests "sent back" or "not taken in."

**Change B**: Add CSS class for skipped entity cards:

```javascript
// In the entity card rendering, status-'skipped' already gets applied via:
'<div class="entity-card status-' + ent.status + '">' + ...
```

No JS changes needed here — the template already uses `ent.status` dynamically.

**Change C**: Update completion summary to count skipped entities separately (line ~845):

```javascript
function showCompletionSummary(total, done, skipped, errors, proposals) {
    var parts = [total + ' entity' + (total !== 1 ? 'ies' : '') + ' processed'];
    if (done > 0) parts.push(done + ' done');
    if (skipped > 0) parts.push(skipped + ' skipped');
    if (errors > 0) parts.push(errors + ' error' + (errors !== 1 ? '' : 's'));
    // ... rest unchanged
}
```

And update the caller that computes done/skipped/errors counts:

```javascript
// Around line 320, in the sync completion handler:
var totalDone = 0, totalSkipped = 0, totalErrors = 0;
for (var k3 in syncEntities) {
    if (syncEntities[k3].status === 'done') totalDone++;
    else if (syncEntities[k3].status === 'skipped') totalSkipped++;
    else if (syncEntities[k3].status === 'error') totalErrors++;
}
```

---

### 5. `kanka_wiki_updater/static/css/style.css`

Add styling for the skipped status (after line ~610):

```css
.entity-card.status-skipped { color: var(--text-dim); opacity: 0.6; }
```

This gives skipped entities a muted, faded appearance — visually distinct from `done` (green checkmark) and `error` (red warning), communicating "processed but not acted upon."

---

## Summary of changes

| File | Change type | Lines affected |
|------|-------------|----------------|
| `sync_events.py` | Add `'skipped'` to tuple | ~1 line |
| `ingest_journal.py` | Pass `_no_proposal` flag in llm_result callback | ~5 lines (variable rename + else branch) |
| `review_web.py` | Handle `_no_proposal` → `'skipped'` mapping | ~8 lines |
| `static/js/app.js` | Add skipped icon, update counts & summary | ~10 lines |
| `static/css/style.css` | Add `.status-skipped` styling | ~2 lines |

**Total**: ~26 lines changed across 5 files. No new files needed.

---

## Testing checklist

- [ ] Run a sync where some entities produce proposals and others don't
- [ ] Verify "done" entities have checkmarks (✓) in the Sync tab
- [ ] Verify "skipped" entities show the skip icon (↩) with muted styling
- [ ] Verify "error" entities still show warning icon (⚠) with red text
- [ ] Verify skipped entities do NOT appear in the Proposals tab
- [ ] Verify completion summary shows "X done, Y skipped, Z errors"
- [ ] Verify no regressions on existing statuses (pending, processing, error)
