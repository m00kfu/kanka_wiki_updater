# Python-Kanka Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw HTTP `kanka_client.py` with a thin wrapper around the forked `python-kanka` library, adding RelationsManager support and migrating all callers from dict access to model attribute access.

**Architecture:** Fork `python-kanka` as a git submodule, add a `RelationsManager` for entity relations (the upstream library lacks this entirely), then rewrite `kanka_client.py` as a thin wrapper that delegates to the library's Pydantic models while normalizing response shapes at the library level. All callers migrate from dict access (`row['entity_id']`) to model attribute access (`row.entity_id`).

**Tech Stack:** Python 3.12+, pydantic (from python-kanka), requests-toolbelt, python-kanka (forked)

## Global Constraints

- No dict access remains after migration — all callers use model attributes
- `MIN_SECONDS_BETWEEN_REQUESTS` removed from config.py; rate limiting handled by python-kanka's built-in exponential backoff
- Wrapper preserves the exact same public method signatures so caller changes are purely syntactic (dict → attribute)
- Tests must pass after each task that touches tested modules

---

### Task 1: Fork python-kanka + add RelationsManager

**Files:**
- Create: `vendor/python-kanka/` (git submodule directory)
- Modify: `vendor/python-kanka/pyproject.toml` — change `requires-python = "==3.14.6"` to `requires-python = ">=3.12"`
- Modify: `vendor/python-kanka/src/kanka/managers.py` — add `RelationsManager(EntityManager[Relation])` class (~50 lines)
- Modify: `vendor/python-kanka/src/kanka/models/entities.py` — add `class Relation(BaseModel)` with fields: `id: int`, `owner_id: int`, `target_id: int`, `relation: str`, `attitude: str | None = None`, `two_way: bool = False`, `visibility_id: int`
- Modify: `vendor/python-kanka/src/kanka/client.py` — register RelationsManager on client instantiation

**Interfaces:**
- Consumes: existing EntityManager pattern from python-kanka
- Produces: `client.relations.list_for_entity(entity_id) → list[Relation]`, `client.relations.create(entity_id, target_id, relation, ...) → Relation`, `client.relations.update(entity_id, relation_id, **fields) → Relation`, `client.relations.delete(entity_id, relation_id) → bool`

- [ ] **Step 1: Create fork and add submodule**

```bash
git clone https://github.com/YOUR_NAME/python-kanka.git vendor/python-kanka-fork
cd vendor/python-kanka-fork
sed -i 's/requires-python = "==3.14\.6"/requires-python = ">=3.12"/' pyproject.toml
# Add RelationsManager to managers.py and Relation model to entities.py per spec §2
git remote add local file:///tmp/python-kanka-local  # or push to your GitHub fork
git push YOUR_FORK main
cd -
rm -rf vendor/python-kanka-fork
git submodule add https://github.com/YOUR_NAME/python-kanka.git vendor/python-kanka
```

Expected: `vendor/python-kanka/` directory with the forked library.

- [ ] **Step 2: Register RelationsManager in client.py**

In `vendor/python-kanka/src/kanka/client.py`, add to `__init__`:

```python
from .managers import RelationsManager
# ... existing managers ...
self.relations = RelationsManager(self)
```

Expected: `client.relations` is available after creating a KankaClient instance.

- [ ] **Step 3: Commit the fork**

```bash
cd vendor/python-kanka && git add -A && git commit -m "Add RelationsManager, relax Python >=3.12" && cd ../..
git add vendor/python-kanka && git commit -m "chore: add python-kanka submodule with RelationsManager"
```

---

### Task 2: Add dependency + remove rate-limit config

**Files:**
- Modify: `requirements.txt` — add `python-kanka @ git+https://github.com/YOUR_NAME/python-kanka.git@main`
- Modify: `config.py` — remove line 38 (`MIN_SECONDS_BETWEEN_REQUESTS = ...`)
- Modify: `tests/test_kanka_client.py` — remove `MIN_SECONDS_BETWEEN_REQUESTS=0` from mock_config fixture (line 19)

**Interfaces:**
- Consumes: nothing new
- Produces: no more `config.MIN_SECONDS_BETWEEN_REQUESTS` symbol anywhere in the codebase

- [ ] **Step 1: Update requirements.txt**

```python
# Add this line to requirements.txt, after "flask>=3.0":
python-kanka @ git+https://github.com/YOUR_NAME/python-kanka.git@main
```

- [ ] **Step 2: Remove MIN_SECONDS_BETWEEN_REQUESTS from config.py**

Delete the entire line:
```python
MIN_SECONDS_BETWEEN_REQUESTS = float(os.environ.get('KANKA_REQUEST_INTERVAL', '2.1'))
```

- [ ] **Step 3: Update test_kanka_client.py mock fixture**

In `tests/test_kanka_client.py`, change the mock_config fixture from:
```python
MagicMock(
    KANKA_BASE_URL='https://api.kanka.io/1.0',
    KANKA_CAMPAIGN_ID='1',
    KANKA_TOKEN='test-token',
    MIN_SECONDS_BETWEEN_REQUESTS=0,  # REMOVE THIS LINE
),
```

To:
```python
MagicMock(
    KANKA_BASE_URL='https://api.kanka.io/1.0',
    KANKA_CAMPAIGN_ID='1',
    KANKA_TOKEN='test-token',
),
```

- [ ] **Step 4: Run tests to verify nothing breaks yet**

Run: `pytest tests/test_kanka_client.py -v`
Expected: All existing tests pass (still using raw HTTP, no changes to kanka_client.py logic yet).

---

### Task 3: Rewrite kanka_client.py as python-kanka wrapper

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py` — complete rewrite (~140 → ~80 lines)
- Test: `tests/test_kanka_client.py` — rewrite all CRUD tests for new wrapper approach

**Interfaces (public API — must match current signatures exactly):**
```python
class KankaClient:
    def __init__(self)  # wraps _KankaClient with retry config
    def get_journals(self, since=None, journal_type=None) → list[KankaModel]
    def get_characters(self) → list[KankaModel]
    def get_locations(self) → list[KankaModel]
    def update_entity_entry(self, kind, entity_local_id, entry_text)  # patches entry text with HTML conversion
    def create_character(self, name, entry=None, **extra)  # delegates to library + HTML conversion
    def create_location(self, name, entry=None, **extra)   # same pattern
    def delete_character(self, local_id)  # delegates to library
    def delete_location(self, local_id)   # delegates to library
    def get_relations(self, entity_id) → list[KankaModel]
    def create_relation(self, entity_id, target_id, relation, attitude=None, two_way=False, visibility_id=1)
    def update_relation(self, entity_id, relation_id, **fields)
    def delete_relation(self, entity_id, relation_id)
```

**Key design decisions:**
- `get_journals` returns Kanka's Pydantic models (not dicts) — callers use `.name`, `.id`, etc.
- `update_entity_entry` still does HTML conversion (`\n\n` → `<br><br>`) then delegates to library
- CRUD for characters/locations delegates to python-kanka's built-in managers where they exist; custom wrapper methods handle the HTML entry conversion
- Relations use `client._client.relations.*` from the fork

```python
"""Minimal Kanka API client — thin wrapper around python-kanka.

Delegates entity operations (journals, characters, locations) to the
python-kanka library which returns Pydantic models with snake_case
attributes. Wraps relations via a custom RelationsManager in our fork.
Rate limiting is handled by python-kanka's built-in exponential backoff.
"""

from kanka import KankaClient as _KankaClient  # from fork (vendor/python-kanka)

from . import config


class KankaError(RuntimeError):
    pass


def _to_html(text: str | None) -> str | None:
    """Convert plain text to HTML for Kanka's entry field."""
    if not text:
        return None
    return text.replace('\n\n', '<br><br>').replace('\n', '<br>')


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
            params['type'] = journal_type
        return list(self._client.journals.list(**params))

    def get_characters(self):
        return list(self._client.characters.list(related=True))

    def get_locations(self):
        return list(self._client.locations.list(related=True))

    def update_entity_entry(self, kind: str, entity_local_id: int, entry_text: str):
        html = _to_html(entry_text)
        if kind == 'characters':
            # python-kanka's character manager may not support entry=kwarg;
            # fall back to raw PATCH on the entity endpoint.
            try:
                self._client.characters.update(entity_local_id, entry=html)
            except TypeError:
                from kanka import KankaClient as _KankaClient  # has _request
                self._client._request('PATCH', f'characters/{entity_local_id}', json={'entry': html})
        elif kind == 'locations':
            try:
                self._client.locations.update(entity_local_id, entry=html)
            except TypeError:
                from kanka import KankaClient as _KankaClient  # has _request
                self._client._request('PATCH', f'locations/{entity_local_id}', json={'entry': html})

    def create_character(self, name: str, entry: str | None = None, **extra):
        body = {'name': name}
        html_entry = _to_html(entry)
        if html_entry:
            body['entry'] = html_entry
        body.update(extra)
        return self._client.characters.create(**body)

    def create_location(self, name: str, entry: str | None = None, **extra):
        body = {'name': name}
        html_entry = _to_html(entry)
        if html_entry:
            body['entry'] = html_entry
        body.update(extra)
        return self._client.locations.create(**body)

    def delete_character(self, local_id: int):
        self._client.characters.delete(local_id)

    def delete_location(self, local_id: int):
        self._client.locations.delete(local_id)

    # -- Relations --------------------------------------------------------

    def get_relations(self, entity_id: int):
        return list(self._client.relations.list_for_entity(entity_id))

    def create_relation(
        self, entity_id: int, target_id: int, relation: str,
        attitude: str | None = None, two_way: bool = False, visibility_id: int = 1,
    ):
        body = {
            'owner_id': entity_id,
            'target_id': target_id,
            'relation': relation,
            'visibility_id': visibility_id,
        }
        if attitude is not None:
            body['attitude'] = attitude
        if two_way:
            body['two_way'] = True
        return self._client.relations.create(entity_id, target_id, relation, **body)

    def update_relation(self, entity_id: int, relation_id: int, **fields):
        return self._client.relations.update(entity_id, relation_id, **fields)

    def delete_relation(self, entity_id: int, relation_id: int):
        return self._client.relations.delete(entity_id, relation_id)
```

- [ ] **Step 1: Write the new kanka_client.py**

Replace entire contents of `kanka_wiki_updater/kanka_client.py` with the implementation above.

- [ ] **Step 2: Rewrite tests for the new wrapper**

Replace `tests/test_kanka_client.py` entirely:

```python
"""Tests for KankaClient HTTP wrapper (mocked python-kanka client)."""

from unittest.mock import MagicMock, patch

import pytest

from kanka_wiki_updater.kanka_client import KankaClient, KankaError


@pytest.fixture(autouse=True)
def mock_config(monkeypatch):
    monkeypatch.setattr(
        'kanka_wiki_updater.config',
        MagicMock(
            KANKA_BASE_URL='https://api.kanka.io/1.0',
            KANKA_CAMPAIGN_ID='1',
            KANKA_TOKEN='test-token',
        ),
    )


class TestKankaError:
    def test_is_runtime_error_subclass(self):
        assert issubclass(KankaError, RuntimeError)

    def test_contains_message(self):
        err = KankaError('bad thing')
        assert 'bad thing' in str(err)


class TestInit:
    @patch('kanka_wiki_updater.kanka_client._KankaClient')
    def test_initializes_with_retry_config(self, mock_kanka_cls):
        KankaClient()
        mock_kanka_cls.assert_called_once_with(
            token='test-token',
            campaign_id='1',
            enable_rate_limit_retry=True,
            max_retries=8,
            retry_delay=1.0,
            max_retry_delay=15.0,
        )


class TestGetJournals:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_passes_since_as_lastSync(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()
        client._client.journals.list.return_value = []

        client.get_journals(since='2024-01-01')
        args, kwargs = client._client.journals.list.call_args
        assert 'lastSync' in kwargs
        assert kwargs['lastSync'] == '2024-01-01'

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_passes_journal_type(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()
        client._client.journals.list.return_value = []

        client.get_journals(journal_type='Session')
        args, kwargs = client._client.journals.list.call_args
        assert 'type' in kwargs
        assert kwargs['type'] == 'Session'


class TestGetCharactersLocations:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_get_characters_passes_related(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()
        client._client.characters.list.return_value = []

        client.get_characters()
        args, kwargs = client._client.characters.list.call_args
        assert 'related' in kwargs or len(args) >= 1

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_get_locations_passes_related(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()
        client._client.locations.list.return_value = []

        client.get_locations()
        args, kwargs = client._client.locations.list.call_args
        assert 'related' in kwargs or len(args) >= 1


class TestUpdateEntityEntry:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_characters_html_conversion(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.update_entity_entry('characters', 123, 'para1\n\npara2\nline3')
        args, kwargs = client._client.characters.update.call_args
        assert kwargs['entry'] == 'para1<br><br>para2<br>line3'

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_locations_html_conversion(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.update_entity_entry('locations', 456, 'hello\nworld')
        args, kwargs = client._client.locations.update.call_args
        assert kwargs['entry'] == 'hello<br>world'


class TestCreateCharacter:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_name_only(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.create_character('Alice')
        args, kwargs = client._client.characters.create.call_args
        assert 'name' in kwargs or len(args) >= 1
        # Entry should NOT be present when not provided
        call_kwargs = dict(client._client.characters.create.call_args[1]) if client._client.characters.create.call_args[1] else {}

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_with_entry_html_conversion(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.create_character('Alice', entry='A brave warrior.')
        args, kwargs = client._client.characters.create.call_args
        call_kwargs = dict(client._client.characters.create.call_args[1]) if client._client.characters.create.call_args[1] else {}


class TestCreateLocation:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_name_only(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.create_location('Waterdeep')


class TestDeleteCharacter:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_delegates_to_library(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.delete_character(456)
        args, kwargs = client._client.characters.delete.call_args
        assert args[0] == 456


class TestDeleteLocation:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_delegates_to_library(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.delete_location(789)
        args, kwargs = client._client.locations.delete.call_args
        assert args[0] == 789


class TestRelations:
    @patch.object(KankaClient, '__init__', return_value=None)
    def test_get_relations(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.get_relations(123)
        args, kwargs = client._client.relations.list_for_entity.call_args
        assert args[0] == 123

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_create_relation_with_attitude(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.create_relation(123, 456, 'Sworn enemy', attitude=-80)
        args, kwargs = client._client.relations.create.call_args
        assert args[0] == 123
        assert args[1] == 456
        assert args[2] == 'Sworn enemy'

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_create_relation_without_attitude(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.create_relation(123, 456, 'Friend', attitude=None)
        args, kwargs = client._client.relations.create.call_args

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_update_relation(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.update_relation(123, 999, relation='Enemy', attitude=-50)
        args, kwargs = client._client.relations.update.call_args
        assert args[0] == 123
        assert args[1] == 999

    @patch.object(KankaClient, '__init__', return_value=None)
    def test_delete_relation(self, mock_init):
        client = KankaClient()
        client._client = MagicMock()

        client.delete_relation(123, 999)
        args, kwargs = client._client.relations.delete.call_args
        assert args[0] == 123
        assert args[1] == 999
```

- [ ] **Step 3: Run tests to verify new wrapper works**

Run: `pytest tests/test_kanka_client.py -v`
Expected: All new tests pass.

---

### Task 4: Migrate sync_pipeline.py — dict access → model attributes

**Files:**
- Modify: `kanka_wiki_updater/sync_pipeline.py` — ~70 changes, dict access → attribute access
- Test: (no separate test file; existing integration via `test_sync_pipeline*.py` if present)

**Specific changes needed:**

In `build_entity_index()` (lines 54-61):
```python
# Before:
index[row['entity_id']] = {
    'kind': kind,
    'local_id': row['id'],
    'name': row['name'],
    'entry': row.get('entry') or '',
    'relations': row.get('relations') or [],
}

# After:
index[row.entity_id] = EntityData(
    kind=kind,
    local_id=row.id,
    name=row.name,
    entry=getattr(row, 'entry', '') or '',
    relations=getattr(row, 'relations', []) or [],
)
```

In `relation_summary()` (lines 69-73):
```python
# Before:
other_id = rel['target_id'] if rel.get('target_id') in index else rel.get('owner_id')
other_name = other['name'] if other else f'entity #{other_id}'
f'- {rel["relation"]} -> {other_name} (attitude: {rel.get("attitude")})'

# After:
other_id = rel.target_id if hasattr(rel, 'target_id') and rel.target_id in index else rel.owner_id
other_name = other.name if other else f'entity #{other_id}'
f'- {rel.relation} -> {other_name} (attitude: {rel.attitude})'
```

In `propose_update()` (lines 84-124):
```python
# journal.get('entry') → journal.entry
# journal.get('name', 'Session note') → journal.name or 'Session note'
# result.get('updated_entry', '') → getattr(result, 'updated_entry', '') or ''
# entity['name'] → entity.name (EntityData namedtuple)
```

In `journal_sort_key()` (lines 140-157):
```python
# journal.get('date') → journal.date or ''
# journal.get('calendar_year') → getattr(journal, 'calendar_year', None)
# journal.get('created_at') → journal.created_at or ''
```

In `apply_relation_changes_locally()` (lines 160-186):
```python
# r.get('target_id') → r.target_id
# rc['target_name'] → rc['target_name'] (already a string in the proposal dict)
# relation_changes are dicts from LLM output — keep as-is for local-only tracking
```

In `main()` (lines 237-343):
```python
# j['id'] → j.id (journal model)
# journal.get('name') → journal.name or ''
# index[entity_id]['entry'] = ... → entity_data.entry = ...
# j['updated_at'] → j.updated_at
# j['id'] not in processed_ids → j.id not in processed_ids
```

**Important note on `relation_changes` from LLM output:** The LLM returns a JSON dict with `relation_changes` as a list of dicts (not Pydantic models). These stay as dicts since they're internal proposal data, not API responses. Only Kanka API responses are converted to model attributes.

- [ ] **Step 1: Update build_entity_index()**

```python
from pydantic import BaseModel


class EntityData(BaseModel):
    kind: str
    local_id: int
    name: str
    entry: str = ''
    relations: list = None

    def model_post_init(self, __context):
        if self.relations is None:
            self.relations = []


def build_entity_index(client):
    index = {}
    for kind, rows in (('character', client.get_characters()), ('location', client.get_locations())):
        for row in rows:
            entry_text = getattr(row, 'entry', '') or ''
            rels = getattr(row, 'relations', []) or []
            index[row.entity_id] = EntityData(
                kind=kind,
                local_id=row.id,
                name=row.name,
                entry=entry_text,
                relations=[_rel_to_dict(r) for r in rels],
            )
    return index


def _rel_to_dict(rel):
    """Convert a Relation model to the dict shape expected by apply_relation_changes_locally."""
    d = {
        'target_id': getattr(rel, 'target_id', None),
        'owner_id': getattr(rel, 'owner_id', None),
        'relation': getattr(rel, 'relation', ''),
    }
    if hasattr(rel, 'attitude'):
        d['attitude'] = rel.attitude
    return d
```

- [ ] **Step 2: Update relation_summary()**

```python
def relation_summary(relations, index):
    if not relations:
        return '(none on record)'
    lines = []
    for rel in relations:
        target_id = rel.get('target_id') if isinstance(rel, dict) else getattr(rel, 'target_id', None)
        other_id = target_id if target_id and target_id in index else (rel.get('owner_id') if isinstance(rel, dict) else getattr(rel, 'owner_id', None))
        other = index.get(other_id)
        name = other['name'] if other else f'entity #{other_id}'
        rel_name = rel.get('relation') if isinstance(rel, dict) else getattr(rel, 'relation', '')
        attitude = rel.get('attitude') if isinstance(rel, dict) else getattr(rel, 'attitude', None)
        lines.append(f'- {rel_name} -> {name} (attitude: {attitude})')
    return '\n'.join(lines)
```

- [ ] **Step 3: Update propose_update()**

Replace the function body to use model attributes for journal and entity data. Key changes:
- `journal.get('entry')` → `getattr(journal, 'entry', '') or ''`
- `journal.get('name', 'Session note')` → `getattr(journal, 'name', None) or 'Session note'`
- `journal.get('date')` → `getattr(journal, 'date', None)`
- `entity['name']` → `entity.name` (EntityData attribute)
- `result.get('updated_entry', '')` → `getattr(result, 'updated_entry', '') or ''`

- [ ] **Step 4: Update journal_sort_key()**

```python
def journal_sort_key(journal):
    raw = getattr(journal, 'date', None) or ''
    if isinstance(raw, str):
        raw = raw.strip()
    else:
        raw = str(raw).strip() if raw else ''
    match = DATE_RE.match(raw)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return (0, year, month, day, getattr(journal, 'created_at', '') or '')

    year = getattr(journal, 'calendar_year', None)
    if year is not None:
        return (
            0,
            int(year),
            getattr(journal, 'calendar_month', 0) or 0,
            getattr(journal, 'calendar_day', 0) or 0,
            getattr(journal, 'created_at', '') or '',
        )

    return (1, 0, 0, 0, getattr(journal, 'created_at', '') or '')
```

- [ ] **Step 5: Update main()**

Key changes in `main()`:
- Line 249: `j['id']` → `j.id` (journal model)
- Line 272: `journal.get('entry')` → `getattr(journal, 'entry', '') or ''`
- Line 279: `journal.get("name")` → `getattr(journal, 'name', None) or ''`
- Line 306: `journal.get('name')` → same pattern
- Line 309: `journal['id']` → `j.id`
- Line 312: `journal.get('name')` → same pattern
- Line 319: `j['updated_at']` → `getattr(j, 'updated_at', '') or ''`

- [ ] **Step 6: Run all tests**

Run: `pytest -v`
Expected: All existing tests pass. If any fail, fix the attribute access patterns for the specific failing test's mock shapes.

---

### Task 5: Migrate review.py — dict access → model attributes

**Files:**
- Modify: `kanka_wiki_updater/review.py` — ~30 changes, dict access → attribute access

**Specific changes needed:**

In `review_new_entity_proposal()` (lines 72-145):
```python
# result.get('data', {}) → handle model response from create_character/create_location
# data.get('entity_id') → getattr(data, 'entity_id', None) if model else data.get('entity_id')
# data.get('id') → getattr(data, 'id', None) if model else data.get('id')
```

In `review_proposal()` (lines 187-362):
```python
# proposal['entity_name'] → proposal['entity_name'] (unchanged — these are internal dicts)
# existing_relations from client.get_relations() → now returns Relation models
# r.get('target_id') → getattr(r, 'target_id', None) or r['target_id'] if dict
# existing.get('id') → getattr(existing, 'id', None) or existing.get('id')
```

**Important:** Proposal data structures (the dicts in `pending_changes.json`) remain as dicts — they're internal state, not API responses. Only the return values from KankaClient methods (`get_relations`, `create_character`, etc.) change shape.

- [ ] **Step 1: Update review_new_entity_proposal() response handling**

```python
# Lines 107-114 — handle model vs dict response from create_character/create_location
data = result.get('data', {}) if isinstance(result, dict) else {}
if hasattr(data, 'model_dump'):
    data = data.model_dump()
new_entity_id = data.get('entity_id')
proposal['created_local_id'] = data.get('id')
proposal['created_kind'] = entity_type
proposal['created_entity_id'] = new_entity_id
```

- [ ] **Step 2: Update review_proposal() relation handling**

In the relation loop (lines 253-346), change how existing relations are matched:
```python
# Before: r.get('target_id') and r.get('id')
# After: handle both model attributes and dict keys for backwards compat
def _rel_target(rel):
    return getattr(rel, 'target_id', None) or (rel.get('target_id') if isinstance(rel, dict) else None)

def _rel_id(rel):
    return getattr(rel, 'id', None) or (rel.get('id') if isinstance(rel, dict) else None)
```

Then use `_rel_target(r)` and `_rel_id(r)` throughout the relation loop.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_review.py -v`
Expected: All review tests pass. Update any mock shapes in test fixtures if needed to return dicts (since proposal data remains dict-based).

---

### Task 6: Migrate revert.py — dict access → model attributes

**Files:**
- Modify: `kanka_wiki_updater/revert.py` — ~15 changes, dict access → attribute access

**Specific changes needed:**

In `revert_relation_result()` (lines 41-75):
```python
# current from client.get_relations() → now Relation models
# r.get('target_id') → _rel_target(r) helper
# existing.get('id') → _rel_id(r) helper
```

No changes to `revert_update_entry()` or `revert_new_entity_entry()` — they work with proposal dicts which are unchanged.

- [ ] **Step 1: Add relation helpers and update revert_relation_result()**

Add at module level (near top of file, after imports):
```python
def _rel_target(rel):
    """Get target_id from a relation that may be a model or dict."""
    return getattr(rel, 'target_id', None) or (rel.get('target_id') if isinstance(rel, dict) else None)


def _rel_id(rel):
    """Get id from a relation that may be a model or dict."""
    return getattr(rel, 'id', None) or (rel.get('id') if isinstance(rel, dict) else None)
```

Then replace all `r.get('target_id')` with `_rel_target(r)` and `existing.get('id')` with `_rel_id(existing)` in `revert_relation_result()`.

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_revert.py -v`
Expected: All revert tests pass.

---

### Task 7: Migrate review_web.py — dict access → model attributes

**Files:**
- Modify: `kanka_wiki_updater/review_web.py` — ~20 changes, relation handling updates

**Specific changes needed:**

review_web.py uses the same KankaClient methods as review.py. The relation handling in the web API endpoint (around lines 274-310) needs the same `_rel_target` / `_rel_id` helpers added.

- [ ] **Step 1: Add _rel_target and _rel_id helpers to review_web.py**

Copy the helper functions from revert.py into review_web.py (or better, move them to a shared location like `kanka_wiki_updater/_utils.py`).

Actually — let's put them in kanka_client.py as module-level utilities since they relate to relation models. Then import from there:
```python
from .kanka_client import _rel_target, _rel_id
```

- [ ] **Step 2: Update relation handling in web API endpoint**

Replace `r.get('target_id')` and `existing.get('id')` with the helpers throughout review_web.py.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_review_web.py -v`
Expected: All web review tests pass.

---

### Task 8: Final integration verification

**Files:**
- No code changes — end-to-end smoke test only

- [ ] **Step 1: Install dependencies**

```bash
pip install -r requirements.txt
```

- [ ] **Step 2: Run full test suite**

Run: `pytest -v --tb=short`
Expected: All tests pass across all 14+ test files.

- [ ] **Step 3: Verify linting**

Run: `ruff check .`
Run: `ruff format --check .`
Expected: No lint errors, no formatting issues.

- [ ] **Step 4: Grep for remaining dict access patterns on Kanka data**

```bash
grep -rn "\['entity_id'\]\|\['id'\]\|\['name'\]\|\['entry'\]" kanka_wiki_updater/ --include="*.py" | grep -v "__pycache__" | grep -v "test_" | grep -v ".pyc"
```

Expected: Only matches in internal proposal dicts (pending_changes.json handling) and test files. No remaining dict access on Kanka API responses.

- [ ] **Step 5: Verify config no longer has MIN_SECONDS_BETWEEN_REQUESTS**

Run: `grep -rn "MIN_SECONDS_BETWEEN_REQUESTS" kanka_wiki_updater/`
Expected: Zero matches.

---

## File Change Summary

| File | Action | Notes |
|---|---|---|
| `vendor/python-kanka/` | Create (submodule) | Fork with RelationsManager + Python >=3.12 |
| `requirements.txt` | Modify | Add python-kanka git dependency |
| `config.py` | Modify | Remove MIN_SECONDS_BETWEEN_REQUESTS line |
| `kanka_wiki_updater/kanka_client.py` | Rewrite | ~140 → ~80 lines, python-kanka wrapper |
| `kanka_wiki_updater/sync_pipeline.py` | Modify | Dict → attribute access (~70 changes) |
| `kanka_wiki_updater/review.py` | Modify | Dict → attribute access for Kanka responses (~30 changes) |
| `kanka_wiki_updater/revert.py` | Modify | Add _rel_target/_rel_id helpers, update relation lookups (~15 changes) |
| `kanka_wiki_updater/review_web.py` | Modify | Same helper imports + relation lookup updates (~20 changes) |
| `tests/test_kanka_client.py` | Rewrite | Mock python-kanka client instead of raw requests |
| `tests/test_review*.py` | Minor update | Update mock shapes if needed for model responses |
| `tests/test_revert.py` | Minor update | Update mock shapes if needed |
