# Plan: Extract Regenerate Logic to `synopsis_generator.py`

## Goal

Move ~80 lines of business logic from `review_web.py`'s `/regenerate` route into a new `regenerate_proposal()` function in `synopsis_generator.py`, making it callable by both the web UI and a future TUI. Also eliminate the thin `_sync_proposal_to_kanka()` shim.

---

## 1. New function: `synopsis_generator.regenerate_proposal(client, proposal, force=False)`

**Location:** `kanka_wiki_updater/synopsis_generator.py` (at module level, after `build_synopsis_proposal`)

**Signature:**
```python
def regenerate_proposal(client, proposal, force=False):
    """Re-run a truncated update proposal through the LLM with higher token limits.
    
    Fetches fresh data from Kanka (source journal + current entity state),
    then calls build_synopsis_proposal() with 2× max_tokens.
    
    Parameters
    ----------
    client : KankaClient
        Authenticated API client.
    proposal : dict
        A pending-change entry (must be 'update' type, must have _journal_id
        and entity_local_id).
    force : bool
        If True, return the result even when no meaningful change was detected.
    
    Returns
    -------
    dict
        Success:  {'ok': True, 'proposed_entry': str, 'change_summary': str,
                   'uncertain': list, 'truncated': bool}
        Failure:  {'ok': False, 'error': str}
    """
```

**What it does (in order):**

1. **Validate proposal type** — must be `'update'`; else return `{'ok': False, 'error': 'Only update proposals can be regenerated'}`
2. **Validate required fields** — `_journal_id` and `entity_local_id` must exist; else return 400-style error
3. **Fetch source journal** via `client.get_journal(journal_id)` — handle API errors → `{ok: False, error: ...}`
4. **Handle missing journal** — if `get_journal` returns `None`, return error
5. **Build full entity index** via `build_entity_index(client)` (not just the single entity — relations may have changed)
6. **Find current entity data** by matching `entity_local_id` against `index.values()` using `_to_dict()` for dict/SimpleNamespace compatibility
7. **Handle missing entity** — return error if not found in index
8. **Build entity dict** with keys: `name`, `kind`, `entry`, `local_id`, `entity_id`
9. **Compute 2× token limit** using the same config logic as current route (`LLM_MAX_TOKENS * 2` or `GEMINI_MAX_TOKENS * 2`)
10. **Call `build_synopsis_proposal(entity_id, entity, src_journal, idx, max_tokens=regen_max)`**
11. **Handle LLM error** — if result is dict with `_llm_error`, return `{ok: False, error: 'LLM call failed: ...'}`
12. **Handle identical output** — if `None` and not forced, return 409-style error; if forced, build minimal proposal from existing queue entry data
13. **Return success dict** with `proposed_entry`, `change_summary`, `uncertain`, `truncated`

---

## 2. Changes to `review_web.py`

### Remove: the entire regenerate route body (~80 lines)

Replace it with a thin handler that delegates to the new function:

```python
@app.route('/api/proposals/<int:index>/regenerate', methods=['POST'])
def regenerate_proposal_route(index):
    queue = queue_manager.load_queue()
    if index >= len(queue):
        return jsonify({'error': 'Proposal not found'}), 404
    
    proposal = queue[index]
    if proposal.get('proposal_type') != 'update':
        return jsonify({'error': 'Only update proposals can be regenerated'}), 400
    
    force = request.args.get('force', '0').lower() in ('1', 'true')
    
    try:
        client = KankaClient()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Failed to initialize API client: {e}'}), 500
    
    result = synopsis_generator.regenerate_proposal(client, proposal, force=force)
    
    if not result['ok']:
        status_code = 409 if 'no meaningful change' in result['error'].lower() else (400 if 'lacks' in result['error'].lower() or 'Cannot fetch' in result['error'] else 500)
        return jsonify({'ok': False, 'error': result['error']}), status_code
    
    # Merge into queue entry
    queue[index]['proposed_entry'] = result['proposed_entry']
    queue[index]['change_summary'] = result.get('change_summary', '')
    queue[index]['relation_changes'] = []
    queue[index]['uncertain'] = result.get('uncertain', [])
    queue[index]['truncated'] = False
    
    queue_manager.save_queue(queue)
    return jsonify({'ok': True, 'proposal': queue[index]})
```

### Remove: `_sync_proposal_to_kanka()` shim

Both callers (`update_status` and `sync_proposal`) already have access to `sync_engine.apply_proposal`. Inline the call directly.

**In `update_status`:**
```python
if status_value in ('approved_all', 'approved_synopsis_only'):
    with _sync_lock:
        client = KankaClient()
        sync_result = sync_engine.apply_proposal(client, queue[index], {})
    queue = queue_manager.load_queue()  # reload after potential mutations
    proposal = queue[index]
```

**In `sync_proposal`:**
```python
with _sync_lock:
    client = KankaClient()
    sync_result = sync_engine.apply_proposal(client, queue[index], {})
```

### Remove: inline `_safe_get` helper

It's replaced by `_to_dict()` already in `synopsis_generator.py`. The new `regenerate_proposal()` function uses that.

---

## 3. Test strategy

**Existing tests in `test_review_web.py` — all stay and pass unchanged:**
- All `TestApiProposalRegenerate` tests mock `KankaClient` at module level (`mock.patch('kanka_wiki_updater.review_web.KankaClient')`)
- The new function is called from within the route, so the mocks intercept it correctly
- Route-level behavior (404 for bad index, 400 for non-update, queue merge, save) stays tested at the integration level

**New tests to add in `test_review_web.py` (or a new `tests/test_synopsis_generator.py`):**
- Direct unit tests for `regenerate_proposal()` function:
  - Success case with mocked client returning journal + entity data
  - Returns correct dict structure (`ok`, `proposed_entry`, etc.)
  - Calls `build_synopsis_proposal` with correct parameters
  - LLM error propagation → `{ok: False, error: 'LLM call failed: ...'}`
  - Identical output without force → `{ok: False, error: 'no meaningful change'}`
  - Force bypasses identical check
  - Missing `_journal_id` → error
  - Journal not found (`get_journal` returns `None`) → error
  - Entity not in index → error

---

## 4. Implementation order

1. **Add `regenerate_proposal()` to `synopsis_generator.py`** — the core business logic, fully testable standalone
2. **Update `review_web.py` regenerate route** — replace ~80 lines with thin handler delegating to new function
3. **Remove `_sync_proposal_to_kanka()`** — inline both callers to use `sync_engine.apply_proposal` directly
4. **Run existing tests** — verify all `test_review_web.py` tests still pass (they mock at the right level)
5. **Add unit tests for new function** — direct calls with mocked client, no Flask app needed

---

## 5. What this enables for TUI

```python
# In a future TUI module:
from kanka_wiki_updater.kanka_client import KankaClient
from kanka_wiki_updater.synopsis_generator import regenerate_proposal

client = KankaClient()
result = regenerate_proposal(client, proposal_dict, force=False)
if result['ok']:
    # present regenerated synopsis to user in TUI
else:
    # show error message
```

No Flask dependency needed. Same code path as the web UI.
