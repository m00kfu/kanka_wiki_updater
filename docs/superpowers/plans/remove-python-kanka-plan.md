# Remove python-kanka — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vendored `python-kanka` library with direct `requests` calls in `kanka_client.py`, delete the vendor directory, and update tests — while keeping the outer `KankaClient` public API unchanged.

**Architecture:** The existing `KankaClient` wraps a single `kanka.KankaClient` instance that manages entity managers (journals, characters, locations). We replace it with one `requests.Session` managed directly by our class. A private `_request()` method handles auth, URL construction, retry on 429, and error raising — extracted from python-kanka's client implementation. All 8 methods that delegate to entity managers get converted to raw HTTP calls; the 4 relation methods already use this pattern and need no changes.

**Tech Stack:** Python 3.10+, `requests` (already a dep), pytest, ruff.

## Global Constraints

- Return types must remain dicts matching existing shapes — no consumer code should break
- Method signatures must not change
- Rate-limit retry: same behavior as python-kanka (up to 8 retries, exponential backoff 1s→15s max)
- Line length: 120 chars (per `pyproject.toml`)
- All new code must pass `ruff check`

---

### Task 1: Add `_request()` method with retry/rate-limit logic

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py:1-30` (top of class)
- Test: `tests/test_kanka_client.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `KankaClient._request(method, endpoint, **kwargs) -> dict` — private helper that makes HTTP calls with retry logic

**Step 1: Add `_request()` method to KankaClient class**

Add this private method right after `__init__`, before the first public method (`get_journals`). The method replicates python-kanka's retry behavior extracted from `vendor/python-kanka/src/kanka/client.py:306-346`:

```python
def _request(self, method: str, endpoint: str, **kwargs) -> dict:
    """Make HTTP request to Kanka API with automatic retry on rate limits."""
    import time as _time

    url = f'{self._base_url}/campaigns/{self._campaign_id}/{endpoint}'
    attempts = 0
    delay = 1.0
    max_delay = 15.0
    max_retries = 8

    while attempts <= max_retries:
        try:
            response = self._session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise KankaError(f'Request failed: {exc}') from exc

        if response.status_code == 429:
            attempts += 1
            if not self._retry_on_rate_limit or attempts > max_retries:
                raise KankaError('Rate limit exceeded after retries')
            retry_after = response.headers.get('Retry-After')
            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    from email.utils import parsedate_to_datetime as _parsed
                    try:
                        delta = _parsed(retry_after) - _parsed(response.headers.get('Date', ''))
                        delay = max(0, delta.total_seconds())
                    except Exception:
                        delay *= 2
            else:
                remaining = response.headers.get('X-RateLimit-Remaining')
                reset = response.headers.get('X-RateLimit-Reset')
                if remaining and reset:
                    try:
                        if int(remaining) == 0:
                            delay = max(0, int(reset) - _time.time())
                    except (ValueError, TypeError):
                        pass
                else:
                    delay *= 2
            delay = min(delay, max_delay)
            _time.sleep(delay)
            continue

        if response.status_code == 401:
            raise KankaError('Invalid authentication token')
        elif response.status_code == 403:
            raise KankaError('Access forbidden')
        elif response.status_code == 404:
            raise KankaError(f'Resource not found: {endpoint}')
        elif response.status_code >= 400:
            msg = response.text or f'HTTP {response.status_code}'
            raise KankaError(f'API error {response.status_code}: {msg}')

        if method == 'DELETE':
            return {}
        return response.json()

    raise KankaError('Unexpected error in request retry logic')
```

**Step 2: Update `__init__` to use requests.Session instead of kanka.KankaClient**

Replace lines 24-30:
```python
# OLD (lines 24-30):
def __init__(self):
    self._client = kanka.KankaClient(
        token=config.KANKA_TOKEN,
        campaign_id=int(config.KANKA_CAMPAIGN_ID),
        enable_rate_limit_retry=True,
    )

# NEW:
def __init__(self):
    import requests as _requests

    self._base_url = config.KANKA_BASE_URL.rstrip('/')
    self._campaign_id = int(config.KANKA_CAMPAIGN_ID)
    self._retry_on_rate_limit = True

    self._session = _requests.Session()
    self._session.headers.update({
        'Authorization': f'Bearer {config.KANKA_TOKEN}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    })
```

**Step 3: Write tests for `_request()`**

Add to `tests/test_kanka_client.py` (new class at end of file):

```python
class TestRequestRetry:
    """Test the _request method's retry and error handling."""

    def _make_client(self, status_code=200, response_body=None, headers=None):
        import json as _json
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.headers = headers or {}
        if response_body is not None:
            mock_resp.json.return_value = response_body
        else:
            mock_resp.json.return_value = {'data': []}
        return mock_resp

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_200_returns_json(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_client(200, {'data': [1, 2]})
        client._session.request.return_value = mock_resp

        result = client._request('GET', 'journals')
        assert result == {1, 2} or result['data'] == [1, 2]

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_401_raises_kanka_error(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_client(401)
        client._session.request.return_value = mock_resp

        with pytest.raises(KankaError, match='Invalid authentication'):
            client._request('GET', 'journals')

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_delete_returns_empty_dict(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_client(204, None)
        mock_resp.json.side_effect = ValueError('No JSON')
        client._session.request.return_value = mock_resp

        result = client._request('DELETE', 'characters/123')
        assert result == {}
```

**Step 4: Run tests to verify they fail** (tests reference new API, old code still uses kanka)

Run: `pytest tests/test_kanka_client.py -v`
Expected: FAIL — KankaClient.__init__ tries `import kanka` which won't work with the new structure.

**Step 5: Make minimal changes to make tests pass** (only `_request()` + `__init__`)

Actually implement steps 1-2 above in `kanka_client.py`. Don't convert any methods yet — just add `_request()` and update `__init__` so the class can be instantiated.

Run: `pytest tests/test_kanka_client.py::TestRequestRetry -v`
Expected: PASS

**Step 6: Commit**

```bash
git add kanka_wiki_updater/kanka_client.py tests/test_kanka_client.py
git commit -m "feat: replace kanka.KankaClient with raw requests session"
```

---

### Task 2: Convert `get_journals()` to raw HTTP

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py:34-40` (current `get_journals`)
- Test: update `tests/test_kanka_client.py::TestGetAllPagination` and `TestCRUDOperations.test_get_journals_passes_params`

**Interfaces:**
- Consumes: `_request()` method from Task 1
- Produces: returns list of dicts (journal objects) — same as before

**Step 1: Update existing tests to mock `_request` instead of entity managers**

In `tests/test_kanka_client.py`, update the test classes that currently mock `mock.journals.list`:

Replace the `_client_with_mock()` helper and all test methods that set up `client._client = mock` with a new helper:

```python
def _make_full_client():
    """Create a KankaClient instance ready for testing (no kanka dependency)."""
    from unittest.mock import MagicMock
    client = object.__new__(KankaClient)
    client._base_url = 'https://api.kanka.io/1.0'
    client._campaign_id = 999
    client._retry_on_rate_limit = True
    client._session = MagicMock()
    return client, client._session
```

Update `TestGetAllPagination.test_single_page`:
```python
def test_single_page(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': [
            {'id': 1, 'name': 'Journal 1'},
            {'id': 2, 'name': 'Journal 2'},
        ]}
    )

    result = client.get_journals(since='2024-01-01', journal_type='Session')
    assert len(result) == 2
    assert result[0]['id'] == 1
```

Update `TestGetAllPagination.test_params_passed_through`:
```python
def test_params_passed_through(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': []}
    )

    client.get_journals(since='2024-06-01', journal_type='Session')
    call_args = session.request.call_args
    assert call_args[0][0] == 'GET'
    assert '/journals' in call_args[0][1]
```

Update `TestCRUDOperations.test_get_journals_passes_params`:
```python
def test_get_journals_passes_params(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': []}
    )
    client.get_journals(since='2024-01-01', journal_type='Session')
    call_args = session.request.call_args
    assert 'lastSync' in call_args[0][1] or 'last_sync' in call_args[1].get('params', {})
```

**Step 2: Rewrite `get_journals()` method**

Replace lines 34-40 with:
```python
def get_journals(self, since=None, journal_type=None):
    params = {}
    if since:
        params['lastSync'] = since
    if journal_type:
        params['type'] = journal_type
    resp = self._request('GET', 'journals', params=params)
    data = resp.get('data') or []
    if isinstance(data, dict):
        data = [data]
    return data
```

**Step 3: Run tests to verify they pass**

Run: `pytest tests/test_kanka_client.py::TestGetAllPagination -v` and `pytest tests/test_kanka_client.py::TestCRUDOperations::test_get_journals_passes_params -v`
Expected: PASS

**Step 4: Commit**

```bash
git add kanka_wiki_updater/kanka_client.py tests/test_kanka_client.py
git commit -m "feat: convert get_journals to raw HTTP request"
```

---

### Task 3: Convert `get_characters()` and `get_locations()` to raw HTTP

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py:44-48` (current methods)
- Test: add tests for both methods in `tests/test_kanka_client.py::TestCRUDOperations`

**Interfaces:**
- Consumes: `_request()` from Task 1
- Produces: returns list of dicts — same as before

**Step 1: Rewrite the two methods**

Replace lines 44-48 with:
```python
def get_characters(self):
    resp = self._request('GET', 'characters', params={'related': 1})
    data = resp.get('data') or []
    if isinstance(data, dict):
        data = [data]
    return data

def get_locations(self):
    resp = self._request('GET', 'locations', params={'related': 1})
    data = resp.get('data') or []
    if isinstance(data, dict):
        data = [data]
    return data
```

**Step 2: Add tests**

Add to `tests/test_kanka_client.py` in `TestCRUDOperations`:
```python
def test_get_characters(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': [{'id': 1, 'name': 'Alice'}]}
    )
    result = client.get_characters()
    assert len(result) == 1
    assert result[0]['name'] == 'Alice'

def test_get_locations(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': [{'id': 2, 'name': 'Waterdeep'}]}
    )
    result = client.get_locations()
    assert len(result) == 1
    assert result[0]['name'] == 'Waterdeep'
```

**Step 3: Run tests to verify they pass**

Run: `pytest tests/test_kanka_client.py::TestCRUDOperations -v`
Expected: PASS (all existing + new tests)

**Step 4: Commit**

```bash
git add kanka_wiki_updater/kanka_client.py tests/test_kanka_client.py
git commit -m "feat: convert get_characters and get_locations to raw HTTP"
```

---

### Task 4: Convert `update_entity_entry()` to raw HTTP

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py:50-57` (current method)
- Test: update existing test in `tests/test_kanka_client.py::TestCRUDOperations.test_update_entity_entry_converts_newlines`

**Interfaces:**
- Consumes: `_request()` from Task 1
- Produces: returns whatever the API responds — same as before

**Step 1: Rewrite method**

Replace lines 50-57 with:
```python
def update_entity_entry(self, kind, entity_local_id, entry_text):
    html = entry_text.replace('\n\n', '<br><br>').replace('\n', '<br>')
    self._request('PATCH', f'{kind}/{entity_local_id}', json={'entry': html})
```

**Step 2: Update test to mock `_request` instead of entity manager**

Replace `test_update_entity_entry_converts_newlines`:
```python
def test_update_entity_entry_converts_newlines(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': {}}
    )
    client.update_entity_entry('characters', 123, 'para1\n\npara2\nline3')
    call_args = session.request.call_args
    body = call_args[1]['json']
    assert body['entry'] == 'para1<br><br>para2<br>line3'
```

**Step 3: Run tests to verify they pass**

Run: `pytest tests/test_kanka_client.py::TestCRUDOperations::test_update_entity_entry_converts_newlines -v`
Expected: PASS

**Step 4: Commit**

```bash
git add kanka_wiki_updater/kanka_client.py tests/test_kanka_client.py
git commit -m "feat: convert update_entity_entry to raw HTTP"
```

---

### Task 5: Convert `create_character()` and `create_location()` to raw HTTP

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py:59-77` (current methods)
- Test: add tests in `tests/test_kanka_client.py::TestCRUDOperations`

**Interfaces:**
- Consumes: `_request()` from Task 1
- Produces: returns dict with shape `{'data': {'id': ..., 'entity_id': ..., 'name': ..., 'entry': ...}}` — same as before

**Step 1: Rewrite both methods**

Replace lines 59-77 with:
```python
def create_character(self, name, entry=None, **extra):
    data = {'name': name}
    if entry:
        html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
        data['entry'] = html
    data.update(extra)
    resp = self._request('POST', 'characters', json=data)
    d = resp.get('data') or {}
    return {'data': {k: d.get(k) for k in ['id', 'entity_id', 'name', 'entry']}}

def create_location(self, name, entry=None, **extra):
    data = {'name': name}
    if entry:
        html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
        data['entry'] = html
    data.update(extra)
    resp = self._request('POST', 'locations', json=data)
    d = resp.get('data') or {}
    return {'data': {k: d.get(k) for k in ['id', 'entity_id', 'name', 'entry']}}
```

**Step 2: Update/create tests**

Replace the existing create_character and create_location tests with `_make_full_client` style mocks:

```python
def test_create_character_with_entry(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': {'id': 1, 'entity_id': 42, 'name': 'Alice', 'entry': 'A warrior.'}}
    )
    result = client.create_character('Alice', entry='A brave warrior.')
    call_args = session.request.call_args
    body = call_args[1]['json']
    assert body['name'] == 'Alice'
    assert body['entry'] == 'A brave warrior.'
    assert result['data']['name'] == 'Alice'

def test_create_character_without_entry(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': {'id': 2, 'entity_id': 43, 'name': 'Bob', 'entry': None}}
    )
    client.create_character('Bob')
    call_args = session.request.call_args
    body = call_args[1]['json']
    assert 'entry' not in body

def test_create_location_with_entry(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': {'id': 3, 'entity_id': 44, 'name': 'Waterdeep', 'entry': 'A city.'}}
    )
    client.create_location('Waterdeep', entry='A coastal city.')
    call_args = session.request.call_args
    body = call_args[1]['json']
    assert body['name'] == 'Waterdeep'
```

**Step 3: Run tests to verify they pass**

Run: `pytest tests/test_kanka_client.py::TestCRUDOperations -v`
Expected: PASS (all tests including create_character and create_location)

**Step 4: Commit**

```bash
git add kanka_wiki_updater/kanka_client.py tests/test_kanka_client.py
git commit -m "feat: convert create_character and create_location to raw HTTP"
```

---

### Task 6: Convert `delete_character()` and `delete_location()` to raw HTTP + finalize relation methods

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py:79-131` (delete methods; relations already use `_request`)
- Test: update delete tests in `tests/test_kanka_client.py::TestCRUDOperations`

**Interfaces:**
- Consumes: `_request()` from Task 1
- Produces: returns True — same as before

**Step 1: Rewrite delete methods**

Replace lines 79-83 with:
```python
def delete_character(self, local_id):
    self._request('DELETE', f'characters/{local_id}')
    return True

def delete_location(self, local_id):
    self._request('DELETE', f'locations/{local_id}')
    return True
```

**Step 2: Update delete tests to use `_make_full_client` style mocks**

Replace `test_delete_character`:
```python
def test_delete_character(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(status_code=204)
    result = client.delete_character(456)
    call_args = session.request.call_args
    assert call_args[0][0] == 'DELETE'
    assert '/characters/456' in call_args[0][1]
    assert result is True
```

Replace `test_delete_location` similarly:
```python
def test_delete_location(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(status_code=204)
    result = client.delete_location(789)
    call_args = session.request.call_args
    assert call_args[0][1].endswith('/locations/789')
    assert result is True
```

**Step 3: Update relation tests to use `_make_full_client` style mocks**

The relation tests already mock `mock._request()` — update them to use the new structure. Replace all relation test methods so they call `_make_full_client()` and verify via `session.request.call_args`:

```python
def test_create_relation_with_attitude(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': [{'id': 1, 'owner_id': 123, 'target_id': 456, 'relation': 'Sworn enemy'}]}
    )
    client.create_relation(123, 456, 'Sworn enemy', attitude=-80)
    call_args = session.request.call_args
    assert call_args[0][0] == 'POST'
    body = call_args[1]['json']
    assert body['owner_id'] == 123
    assert body['attitude'] == -80

def test_create_relation_without_attitude(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': [{'id': 2}]}
    )
    client.create_relation(123, 456, 'Friend', attitude=None)
    body = session.request.call_args[1]['json']
    assert 'attitude' not in body

def test_create_relation_with_two_way(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': [{'id': 3}]}
    )
    client.create_relation(123, 456, 'Friend', two_way=True)
    body = session.request.call_args[1]['json']
    assert body['two_way'] is True

def test_update_relation(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(status_code=200, json=lambda: {'data': {}})
    client.update_relation(123, 999, relation='Enemy', attitude=-50)
    call_args = session.request.call_args
    assert call_args[0][0] == 'PATCH'
    body = call_args[1]['json']
    assert body == {'relation': 'Enemy', 'attitude': -50}

def test_delete_relation(self):
    client, session = _make_full_client()
    session.request.return_value = MagicMock(status_code=204)
    client.delete_relation(123, 999)
    call_args = session.request.call_args
    assert call_args[0][0] == 'DELETE'
    assert '/entities/123/relations/999' in call_args[0][1]
```

**Step 4: Run ALL kanka_client tests to verify they pass**

Run: `pytest tests/test_kanka_client.py -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add kanka_wiki_updater/kanka_client.py tests/test_kanka_client.py
git commit -m "feat: convert delete methods, finalize relation methods to raw HTTP"
```

---

### Task 7: Remove python-kanka import and vendor directory + update requirements.txt

**Files:**
- Modify: `kanka_wiki_updater/kanka_client.py` (remove `import kanka`)
- Delete: `vendor/python-kanka/` (entire directory)
- Modify: `requirements.txt` (line 10)
- Modify: `tests/test_kanka_client.py` (remove FakeKankaClient, fake_kanka fixture)

**Step 1: Remove `import kanka` from kanka_client.py line 6**

Delete the line `import kanka`. The file should now only use `requests` directly.

**Step 2: Delete vendor directory**

Run: `rm -rf vendor/python-kanka/`
(If vendor dir is empty after, decide whether to delete it too — keep if other things may go there.)

**Step 3: Update requirements.txt**

Remove line 10 (`-e vendor/python-kanka`). Add explicit `requests>=2.31` on its own line (it's already listed as line 1 but was previously a transitive dep).

New `requirements.txt`:
```
requests>=2.31
python-dotenv>=1.0
json_repair>=0.30
colorama>=0.4
ruff>=0.4
pytest>=8.0
pytest-cov>=5.0
pytest-mock>=3.14
flask>=3.0
```

**Step 4: Remove FakeKankaClient and fake_kanka fixture from tests**

Delete the `FakeKankaClient` class (lines 19-32) and the `fake_kanka` fixture (lines 38-42). Also remove any remaining `@patch('kanka_wiki_updater.kanka_client.kanka.KankaClient', FakeKankaClient)` decorators from test methods — they're no longer needed since we no longer import kanka.

**Step 5: Run all tests to verify nothing is broken**

Run: `pytest tests/test_kanka_client.py -v`
Expected: PASS (all updated tests)

Also run the full suite:
Run: `pytest -v`
Expected: PASS (all test files)

**Step 6: Lint with ruff**

Run: `ruff check .`
Fix any issues reported.

Run: `ruff format .`
Auto-format if needed.

**Step 7: Commit everything**

```bash
git add kanka_wiki_updater/kanka_client.py requirements.txt tests/test_kanka_client.py
git rm -rf vendor/python-kanka/
git commit -m "refactor: remove python-kanka library, use raw requests throughout"
```

---

### Task 8: Final verification — run full test suite and lint

**Files:** None to create. All work is done in previous tasks.

**Step 1: Install dependencies fresh**

Run: `pip install -r requirements.txt`

**Step 2: Run full test suite with coverage**

Run: `pytest --cov=kanka_wiki_updater -v`
Expected: PASS, all tests green.

**Step 3: Verify no python-kankan references remain**

Run: `grep -rn "import kanka" kanka_wiki_updater/ vendor/ || echo "No kanka imports found"`
Expected: No output (no matches).

Run: `ls vendor/python-kanka/ 2>/dev/null && echo "ERROR: vendor dir still exists" || echo "vendor dir removed OK"`
Expected: "vendor dir removed OK"

**Step 4: Verify ruff passes cleanly**

Run: `ruff check .`
Expected: No issues.

**Step 5: Final commit if any lint fixes were needed**

```bash
git add -A
git commit -m "style: fix ruff-reported issues after removing python-kanka" || echo "No changes to commit"
```
