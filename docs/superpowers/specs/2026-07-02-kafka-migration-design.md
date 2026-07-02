# Design: Migrate to python-kanka + Add Relations Support

**Date:** 2026-07-02  
**Status:** Approved  

## Problem

The current `kanka_client.py` (140 lines) wraps the Kanka API directly using `requests`. It works for characters, locations, journals, and relations — but:
- Relations handling is fragile due to response shape inconsistencies (`{"data": {...}}` vs `{"data": [{"..."}]}`)
- Rate limiting uses a fixed interval instead of adaptive backoff
- Expanding into other entity types (events, quests, etc.) would require writing raw HTTP code each time

## Solution

Use [python-kanka](https://github.com/ervwalter/python-kanka) as the API client library. Fork it to:
1. Relax the Python version constraint (`==3.14.6` → `>=3.12`)
2. Add a `RelationsManager` (the library lacks relations support entirely)

Replace all dict-based access across callers with model attribute access via Pydantic models from python-kanka.

## Scope

| File | Change | Lines affected |
|---|---|---|
| **python-kanka/ (submodule)** | Add RelationsManager + fix Python pin | ~100 new lines |
| `kanka_wiki_updater/kanka_client.py` | Replace raw HTTP with python-kanka wrapper | ~140 → ~80 |
| `kanka_wiki_updater/sync_pipeline.py` | Dict access → model attributes | ~70 changes |
| `kanka_wiki_updater/review.py` | Dict access → model attributes | ~30 changes |
| `kanka_wiki_updater/revert.py` | Dict access → model attributes | ~15 changes |
| `requirements.txt` | Add python-kanka via git | +1 line |
| `config.py` | Remove `MIN_SECONDS_BETWEEN_REQUESTS` | -2 lines removed |

## Architecture

### 1. Fork Setup (git submodule)

```bash
git submodule add https://github.com/YOUR_NAME/python-kanka vendor/python-kanka
cd vendor/python-kanka
# Fix pyproject.toml: requires-python = ">=3.12"
# Add RelationsManager to src/kanka/managers.py
# Commit locally, push to fork
```

Add to `requirements.txt`:
```
python-kanka @ git+https://github.com/YOUR_NAME/python-kanka.git@main
```

### 2. RelationsManager (in fork)

Mirrors the existing `EntityManager` pattern but for relations:

```python
# src/kanka/models/entities.py — new model
class Relation(BaseModel):
    id: int
    owner_id: int
    target_id: int
    relation: str
    attitude: str | None = None
    two_way: bool = False
    visibility_id: int

# src/kanka/managers.py — new manager
class RelationsManager(EntityManager[Relation]):
    def list_for_entity(self, entity_id):
        response = self.client._request("GET", f"entities/{entity_id}/relations")
        # Normalize shape inconsistency
        data = response["data"]
        if isinstance(data, list):
            return [Relation(**item) for item in data]
        return [Relation(**data)]

    def create(self, entity_id: int, target_id: int, relation: str,
               attitude: str | None = None, two_way: bool = False,
               visibility_id: int = 1):
        body = {"owner_id": entity_id, "target_id": target_id,
                "relation": relation, "visibility_id": visibility_id}
        if attitude is not None:
            body["attitude"] = attitude
        if two_way:
            body["two_way"] = True
        response = self.client._request("POST", f"entities/{entity_id}/relations", json=body)
        data = response["data"]
        if isinstance(data, list):
            data = data[0] if data else {}
        return Relation(**data)

    def update(self, entity_id: int, relation_id: int, **fields):
        response = self.client._request("PATCH", f"entities/{entity_id}/relations/{relation_id}", json=fields)
        data = response["data"]
        if isinstance(data, list):
            data = data[0] if data else {}
        return Relation(**data)

    def delete(self, entity_id: int, relation_id: int):
        self.client._request("DELETE", f"entities/{entity_id}/relations/{relation_id}")
        return True
```

### 3. kanka_client.py — New Implementation

**Before (raw HTTP):**
```python
def get_characters(self):
    return self._get_all('characters', params={'related': 1})

def create_character(self, name, entry=None, **extra):
    body = {'name': name}
    if entry: ...
    return self._request('POST', 'characters', json=body)
```

**After (python-kanka wrapper):**
```python
from kanka import KankaClient as _KankaClient  # from fork

class KankaError(RuntimeError):
    pass

class KankaClient:
    def __init__(self):
        self._client = _KankaClient(
            token=config.KANKA_TOKEN,
            campaign_id=config.KANKA_CAMPAIGN_ID,
            enable_rate_limit_retry=True,
            max_retries=8,
            retry_delay=1.0,
            max_retry_delay=15.0,
        )

    def get_journals(self, since=None, journal_type=None):
        params = {}
        if since:
            params['lastSync'] = since
        if journal_type:
            # Map custom param to library filter
            pass
        journals = self._client.journals.list(**params)
        return [j.model_dump() for j in journals]  # normalize to dicts for now

    def get_characters(self):
        chars = self._client.characters.list(related=True)
        return [c.model_dump() for c in chars]

    def get_locations(self):
        locs = self._client.locations.list(related=True)
        return [l.model_dump() for l in locs]

    # ... CRUD methods delegate to library ...
```

**Key decision:** The wrapper normalizes response shapes (the `{"data": {...}}` vs `{"data": [{"..."}]}` inconsistency) at the library level, so callers see consistent data.

### 4. Caller Migration

All dict accesses become model attribute access:

| Before | After |
|---|---|
| `row['entity_id']` | `row.entity_id` |
| `rel['target_id']` | `rel.target_id` |
| `journal.get('name')` | `journal.name` |
| `data.get('id')` | `data.id` (or `.model_dump()['id']`) |

**sync_pipeline.py changes:**
```python
# build_entity_index:
index[row['entity_id']] = { ... }  →  index[row.entity_id] = EntityData(...)

# relation_summary:
other_id = rel['target_id']  →  other_id = rel.target_id
```

### 5. Testing Strategy

- **Fork tests:** RelationsManager unit tests in python-kanka's test suite (pytest)
- **Wrapper tests:** `test_kanka_client.py` — mock `_KankaClient` methods, verify wrapper behavior
- **Integration tests:** Existing integration tests pass with new model shapes
- **No mocking needed** for pure functions (mentions, state, progress)

### 6. Dependencies & Config Changes

**requirements.txt additions:**
```
python-kanka @ git+https://github.com/YOUR_NAME/python-kanka.git@main
pydantic>=2.0           # from python-kanka dependency
requests-toolbelt       # from python-kanka dependency
```

**config.py removals:**
```python
# Remove: MIN_SECONDS_BETWEEN_REQUESTS (handled by python-kanka internally)
MIN_SECONDS_BETWEEN_REQUESTS = float(os.environ.get('KANKA_REQUEST_INTERVAL', '2.1'))
```

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Pydantic model field names differ from Kanka API field names | python-kanka uses `model_config` with `populate_by_name=True` to handle both `snake_case` and `camelCase` fields |
| Relations response shape inconsistency not fully covered by library | Handled explicitly in RelationsManager (see §2) |
| python-kanka's exponential backoff behaves differently than fixed interval | Test with a slow local server; adjust retry_delay/max_retry_delay if needed |
| Fork maintenance burden | Minimal — only 3 files touched, ~100 lines. Future library updates can be cherry-picked or rebased. |

## Success Criteria

- [ ] All existing tests pass with new client
- [ ] Relations operations (list/create/update/delete) work via python-kanka's API
- [ ] Rate limiting uses exponential backoff (verified by observing 429 handling in logs)
- [ ] No dict access remains — all callers use model attributes
- [ ] `python -m kanka_wiki_updater.sync_pipeline` completes a full run successfully
- [ ] `python -m kanka_wiki_updater.review` applies changes and relations correctly
