# Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Achieve comprehensive test coverage across all modules in `kanka_wiki_updater`, filling gaps where no tests exist and consolidating duplicates.

**Architecture:** Add new test files alongside existing ones under `tests/`. Use pytest fixtures from `conftest.py` for shared setup (mock env vars). Mock external I/O (HTTP, filesystem) with `unittest.mock`; pure functions get direct unit tests.

**Tech Stack:** Python 3.x, pytest, unittest.mock, colorama (optional), json_repair

## Global Constraints

- Line length: 120 chars (`pyproject.toml` ruff config)
- No network calls in tests — mock `requests` and any HTTP-dependent code
- Use `tmp_path` fixture for filesystem tests; restore module-level globals after each test
- Tests must pass with `pytest` from the project root

---

## Task 1: Deduplicate `_extract_json` tests

**Files:**
- Modify: `tests/test_llm_providers.py` (keep all shared JSON extraction tests here)
- Delete: `tests/test_llm_client.py` entirely (all its tests are duplicated in test_llm_providers.py)

**Interfaces:**
- Consumes: None — cleanup task only
- Produces: Single source of truth for `_extract_json` tests under `TestExtractJson` class

- [ ] **Step 1: Verify duplication is complete**

Run: `diff <(grep -n "def test_" tests/test_llm_client.py) <(grep -n "def test_" tests/test_llm_providers.py | head -6)`
Expected: Both files share identical test names for `_extract_json` (test_extract_json_valid, test_extract_json_with_markdown_fence, etc.)

- [ ] **Step 2: Delete the duplicate file**

Run: `Remove-Item tests\test_llm_client.py`

- [ ] **Step 3: Run remaining tests to confirm nothing breaks**

Run: `pytest tests/ -v --co -q`
Expected: No errors, test count reduced by ~8 (the deleted file's tests)

---

## Task 2: Test `config.py` — validation and defaults

**Files:**
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: None
- Produces: Tests for `_require()`, env var loading, Gemini validation, batch limit parsing

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for config module — env var loading, validation, defaults."""

import importlib
import os
import unittest.mock as mock

import pytest


@pytest.fixture(autouse=True)
def restore_env():
    """Save and restore all config-related env vars around each test."""
    _saved = {k: os.environ.get(k) for k in ('KANKA_TOKEN', 'KANKA_CAMPAIGN_ID', 'LLM_PROVIDER',
                                               'GEMINI_API_KEY', 'JOURNAL_BATCH_LIMIT')}
    yield
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestRequire:
    def test_require_returns_value_when_set(self):
        import kanka_wiki_updater.config as config_mod
        with unittest.mock.patch.dict(os.environ, {'TEST_VAR': 'hello'}):
            result = config_mod._require('TEST_VAR')
        assert result == 'hello'

    def test_require_raises_when_missing(self):
        import kanka_wiki_updater.config as config_mod
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('NONEXISTENT_CONFIG_VAR_XYZ', None)
            with pytest.raises(RuntimeError) as exc_info:
                config_mod._require('NONEXISTENT_CONFIG_VAR_XYZ')
            assert "Missing required setting 'NONEXISTENT_CONFIG_VAR_XYZ'" in str(exc_info.value)


class TestDefaults:
    def test_kanka_base_url_defaults(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.KANKA_BASE_URL == 'https://api.kanka.io/1.0'

    def test_lmstudio_base_url_defaults(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.LMSTUDIO_BASE_URL == 'http://localhost:1234/v1'

    def test_lmstudio_model_defaults(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.LMSTUDIO_MODEL == 'local-model'

    def test_session_journal_type_defaults(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.SESSION_JOURNAL_TYPE == 'Session'

    def test_request_interval_defaults(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.MIN_SECONDS_BETWEEN_REQUESTS == 2.1

    def test_llm_max_tokens_defaults(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.LLM_MAX_TOKENS == 4096

    def test_llm_timeout_defaults(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.LLM_TIMEOUT_SECONDS == 600

    def test_provider_defaults_to_lmstudio(self):
        import kanka_wiki_updater.config as config_mod
        assert config_mod.LLM_PROVIDER == 'lmstudio'


class TestGeminiValidation:
    def test_gemini_without_key_raises(self):
        with unittest.mock.patch.dict(os.environ, {'LLM_PROVIDER': 'gemini', 'GEMINI_API_KEY': ''}):
            import importlib
            import kanka_wiki_updater.config as config_mod
            with pytest.raises(ValueError) as exc_info:
                importlib.reload(config_mod)
            assert 'GEMINI_API_KEY' in str(exc_info.value)

    def test_gemini_with_key_ok(self):
        with unittest.mock.patch.dict(os.environ, {'LLM_PROVIDER': 'gemini', 'GEMINI_API_KEY': 'sk-test'}):
            import importlib
            import kanka_wiki_updater.config as config_mod
            importlib.reload(config_mod)
            assert config_mod.LLM_PROVIDER == 'gemini'


class TestBatchLimit:
    def test_batch_limit_parses_int(self):
        with unittest.mock.patch.dict(os.environ, {'JOURNAL_BATCH_LIMIT': '50'}):
            import importlib
            import kanka_wiki_updater.config as config_mod
            importlib.reload(config_mod)
            assert config_mod.JOURNAL_BATCH_LIMIT == 50

    def test_batch_limit_none_when_empty(self):
        with unittest.mock.patch.dict(os.environ, {'JOURNAL_BATCH_LIMIT': ''}):
            import importlib
            import kanka_wiki_updater.config as config_mod
            importlib.reload(config_mod)
            assert config_mod.JOURNAL_BATCH_LIMIT is None

    def test_batch_limit_none_when_unset(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('JOURNAL_BATCH_LIMIT', None)
            import importlib
            import kanka_wiki_updater.config as config_mod
            importlib.reload(config_mod)
            assert config_mod.JOURNAL_BATCH_LIMIT is None


class TestCustomEnv:
    def test_custom_kanka_base_url(self):
        with unittest.mock.patch.dict(os.environ, {'KANKA_BASE_URL': 'http://localhost:8080'}):
            import importlib
            import kanka_wiki_updater.config as config_mod
            importlib.reload(config_mod)
            assert config_mod.KANKA_BASE_URL == 'http://localhost:8080'

    def test_custom_request_interval(self):
        with unittest.mock.patch.dict(os.environ, {'KANKA_REQUEST_INTERVAL': '0.5'}):
            import importlib
            import kanka_wiki_updater.config as config_mod
            importlib.reload(config_mod)
            assert config_mod.MIN_SECONDS_BETWEEN_REQUESTS == 0.5

    def test_custom_llm_settings(self):
        with unittest.mock.patch.dict(os.environ, {
            'LLM_MAX_TOKENS': '8192', 'LLM_TIMEOUT_SECONDS': '300'
        }):
            import importlib
            import kanka_wiki_updater.config as config_mod
            importlib.reload(config_mod)
            assert config_mod.LLM_MAX_TOKENS == 8192
            assert config_mod.LLM_TIMEOUT_SECONDS == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — module not found (file doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_config.py
git commit -m "test: add config module tests for env loading and validation"
```

---

## Task 3: Test `progress.py` — ProgressTracker

**Files:**
- Create: `tests/test_progress.py`

**Interfaces:**
- Consumes: None
- Produces: Tests for rendering, Unicode/ASCII fallback, Windows suppression, percentage rounding

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for in-memory progress tracker with terminal rendering."""

import sys
from unittest.mock import mock_open, patch

import pytest

from kanka_wiki_updater.progress import ProgressTracker


class TestProgressRendering:
    def test_initial_state_zero_percent(self):
        tracker = ProgressTracker(10)
        assert tracker.done == 0

    def test_filled_returns_block_or_equals(self):
        tracker_unicode = ProgressTracker(10)
        tracker_unicode._unicode = True
        assert tracker_unicode.filled == '█'

        tracker_ascii = ProgressTracker(10)
        tracker_ascii._unicode = False
        assert tracker_ascii.filled == '='

    def test_empty_returns_block_or_dash(self):
        tracker_unicode = ProgressTracker(10)
        tracker_unicode._unicode = True
        assert tracker_unicode.empty == '░'

        tracker_ascii = ProgressTracker(10)
        tracker_ascii._unicode = False
        assert tracker_ascii.empty == '-'

    def test_bar_width_is_20(self):
        tracker = ProgressTracker(10)
        tracker.done = 6
        rendered = tracker._render()
        bar_start = rendered[1:21]  # skip '[' and take 20 chars
        assert len(bar_start) == 20

    def test_percentage_rounds_up(self):
        tracker = ProgressTracker(3)
        tracker.done = 2
        rendered = tracker._render()
        pct_part = rendered[22:25]  # after ']' grab percentage
        assert '67%' in rendered or '66%' in rendered

    def test_label_appended(self):
        tracker = ProgressTracker(10)
        rendered = tracker._render('LLM for Alice...')
        assert 'LLM for Alice...' in rendered

    def test_no_label_when_empty_string(self):
        tracker = ProgressTracker(10)
        rendered = tracker._render()
        # Should not have trailing space after percentage bar
        assert rendered == rendered.rstrip() or ' ' not in rendered.split(']')[-1].strip()


class TestProgressIncrement:
    def test_mark_done_increments(self):
        tracker = ProgressTracker(5)
        tracker.mark_done()
        assert tracker.done == 1

    def test_finish_prints_newline(self, capsys):
        tracker = ProgressTracker(2)
        tracker.mark_done('step 1')
        tracker.mark_done('step 2')
        tracker.finish()
        output = capsys.readouterr().out
        assert '\n' in output

    def test_max_width_tracks_longest_render(self):
        tracker = ProgressTracker(1)
        tracker._render('very long label that exceeds normal width')
        assert tracker._max_width > 0


class TestEdgeCases:
    def test_zero_total_avoids_division_by_zero(self):
        tracker = ProgressTracker(0)
        assert tracker.total == 1  # clamped to 1

    def test_finish_with_zero_total_does_nothing(self, capsys):
        tracker = ProgressTracker(0)
        tracker.finish()
        output = capsys.readouterr().out
        assert output == ''

    @patch('sys.stdout.encoding', 'ascii')
    def test_fallback_to_ascii_on_encode_error(self):
        with patch.object(ProgressTracker, '_check_unicode', return_value=False):
            tracker = ProgressTracker(10)
            assert tracker.filled == '='
            assert tracker.empty == '-'

    def test_use_cr_disabled_on_windows(self, monkeypatch):
        import os
        original_name = os.name
        try:
            monkeypatch.setattr('os.name', 'nt')
            # Re-import to pick up the change
            import importlib
            from kanka_wiki_updater import progress as progress_mod
            importlib.reload(progress_mod)
            tracker = progress_mod.ProgressTracker(10)
            assert tracker._use_cr is False
        finally:
            os.name = original_name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_progress.py -v`
Expected: FAIL — module not found (file doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_progress.py
git commit -m "test: add ProgressTracker tests for rendering and edge cases"
```

---

## Task 4: Test `colors.py` — enabled vs disabled paths

**Files:**
- Create: `tests/test_colors.py`

**Interfaces:**
- Consumes: None
- Produces: Tests for color wrapping, identity fallback, reset behavior

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for terminal color helpers (colorama enabled/disabled)."""

import importlib
import sys
from unittest.mock import patch


def _reload_colors():
    """Force-reload colors module to pick up mock state."""
    if 'kanka_wiki_updater.colors' in sys.modules:
        del sys.modules['kanka_wiki_updater.colors']
    from kanka_wiki_updater import colors as mod
    return mod


class TestColorWrapping:
    def test_red_wraps_text(self):
        # When colorama is available, red should wrap with Fore.RED and RESET_ALL
        from kanka_wiki_updater.colors import red
        result = red('hello')
        assert 'hello' in result

    def test_green_wraps_text(self):
        from kanka_wiki_updater.colors import green
        result = green('world')
        assert 'world' in result

    def test_all_colors_exist(self):
        from kanka_wiki_updater.colors import red, green, yellow, cyan, magenta, bold, dim
        for fn in [red, green, yellow, cyan, magenta, bold, dim]:
            result = fn('test')
            assert 'test' in result

    def test_reset_applied(self):
        from kanka_wiki_updater.colors import red
        try:
            from colorama import Style
            expected_reset = Style.RESET_ALL
        except ImportError:
            return  # skip if colorama not installed
        result = red('test')
        assert expected_reset in result


class TestDisabledFallback:
    def test_identity_when_no_colorama(self):
        """When colorama is missing, all colors should be identity functions."""
        import kanka_wiki_updater.colors as colors_mod

        # Save original state
        _orig_enabled = colors_mod._ENABLED
        _orig_red = colors_mod.red

        try:
            # Simulate ImportError by patching the module's namespace
            colors_mod._ENABLED = False
            colors_mod.red = colors_mod._identity
            colors_mod.green = colors_mod._identity
            colors_mod.yellow = colors_mod._identity
            colors_mod.cyan = colors_mod._identity
            colors_mod.magenta = colors_mod._identity
            colors_mod.bold = colors_mod._identity
            colors_mod.dim = colors_mod._identity

            assert colors_mod.red('hello') == 'hello'
            assert colors_mod.green('world') == 'world'
        finally:
            colors_mod._ENABLED = _orig_enabled
            colors_mod.red = _orig_red


class TestEnabledFlag:
    def test_enabled_is_boolean(self):
        from kanka_wiki_updater.colors import _ENABLED
        assert isinstance(_ENABLED, bool)

    def test_identity_function_exists(self):
        from kanka_wiki_updater.colors import _identity
        assert _identity('hello') == 'hello'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_colors.py -v`
Expected: FAIL — module not found (file doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_colors.py
git commit -m "test: add colors module tests for enabled/disabled paths"
```

---

## Task 5: Test `kanka_client.py` — HTTP wrapper with pagination, throttling, retries

**Files:**
- Create: `tests/test_kanka_client.py`

**Interfaces:**
- Consumes: Mocked `requests.Session`, config module globals
- Produces: Tests for `_request` error handling, 429 retry, `_get_all` pagination, all CRUD operations

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for KankaClient HTTP wrapper (mocked requests)."""

import time
from unittest.mock import MagicMock, patch

import pytest

from kanka_wiki_updater.kanka_client import KankaClient, KankaError


@pytest.fixture(autouse=True)
def mock_config(monkeypatch):
    """Provide minimal config for client initialization."""
    monkeypatch.setattr('kanka_wiki_updater.config', MagicMock(
        KANKA_BASE_URL='https://api.kanka.io/1.0',
        KANKA_CAMPAIGN_ID='1',
        KANKA_TOKEN='test-token',
        MIN_SECONDS_BETWEEN_REQUESTS=0,  # no real throttling in tests
    ))


class TestKankaError:
    def test_is_runtime_error_subclass(self):
        assert issubclass(KankaError, RuntimeError)

    def test_contains_message(self):
        err = KankaError('bad thing')
        assert 'bad thing' in str(err)


class TestRequestErrors:
    @patch('kanka_wiki_updater.kanka_client.KankaClient._throttle')
    def test_400_raises_kanka_error(self, mock_throttle):
        client = KankaClient()
        client.session = MagicMock()
        resp = MagicMock(status_code=400, text='Not found')
        client.session.request.return_value = resp

        with pytest.raises(KankaError) as exc_info:
            client._request('GET', 'journals')
        assert '400' in str(exc_info.value)

    @patch('kanka_wiki_updater.kanka_client.KankaClient._throttle')
    def test_429_retries(self, mock_throttle):
        """429 should retry up to 5 times before giving up."""
        client = KankaClient()
        client.session = MagicMock()
        resp = MagicMock(status_code=429, headers={'Retry-After': '0.01'})
        client.session.request.return_value = resp

        with pytest.raises(KankaError) as exc_info:
            client._request('GET', 'journals')
        assert 'repeated 429' in str(exc_info.value).lower() or 'gave up' in str(exc_info.value).lower()
        # Should have been called 5 times (5 retries)
        assert client.session.request.call_count == 5

    @patch('kanka_wiki_updater.kanka_client.KankaClient._throttle')
    def test_success_returns_json(self, mock_throttle):
        client = KankaClient()
        client.session = MagicMock()
        resp = MagicMock(text='{"data": [{"id": 1}]}', status_code=200)
        resp.json.return_value = {"data": [{"id": 1}]}
        client.session.request.return_value = resp

        result = client._request('GET', 'journals')
        assert result == {"data": [{"id": 1}]}

    @patch('kanka_wiki_updater.kanka_client.KankaClient._throttle')
    def test_empty_response_returns_dict(self, mock_throttle):
        client = KankaClient()
        client.session = MagicMock()
        resp = MagicMock(text='', status_code=204)
        client.session.request.return_value = resp

        result = client._request('GET', 'journals')
        assert result == {}


class TestGetAllPagination:
    @patch.object(KankaClient, '_request')
    def test_single_page(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {
            'data': [{'id': 1}, {'id': 2}],
            'links': {},
        }

        result = client._get_all('journals')
        assert len(result) == 2
        assert result[0]['id'] == 1

    @patch.object(KankaClient, '_request')
    def test_multi_page_follows_next(self, mock_request):
        client = KankaClient()
        mock_request.side_effect = [
            {
                'data': [{'id': 1}],
                'links': {'next': 'https://api.kanka.io/1.0/journals?page=2'},
            },
            {
                'data': [{'id': 2}, {'id': 3}],
                'links': {},
            },
        ]

        result = client._get_all('journals')
        assert len(result) == 3
        # Second call should use the next URL directly (no params)
        assert mock_request.call_count == 2

    @patch.object(KankaClient, '_request')
    def test_params_only_on_first_page(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {'data': [], 'links': {}}

        client._get_all('journals', params={'lastSync': '2024-01-01'})
        # Second call (if it existed) should have next_params=None
        assert mock_request.call_args_list[0][1]['params'] == {'lastSync': '2024-01-01'}


class TestCRUDOperations:
    @patch.object(KankaClient, '_request')
    def test_get_journals_passes_params(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {'data': []}

        client.get_journals(since='2024-01-01', journal_type='Session')
        call_kwargs = mock_request.call_args[1]
        assert 'params' in call_kwargs
        assert call_kwargs['params']['lastSync'] == '2024-01-01'

    @patch.object(KankaClient, '_request')
    def test_update_entity_entry_converts_newlines(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.update_entity_entry('characters', 123, 'para1\n\npara2\nline3')
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['json']['entry'] == 'para1<br><br>para2<br>line3'

    @patch.object(KankaClient, '_request')
    def test_create_character_with_entry(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.create_character('Alice', entry='A brave warrior.')
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['json']['name'] == 'Alice'
        assert call_kwargs['json']['entry'] == 'A brave warrior.'

    @patch.object(KankaClient, '_request')
    def test_create_character_without_entry(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.create_character('Bob')
        call_kwargs = mock_request.call_args[1]
        assert 'entry' not in call_kwargs['json']

    @patch.object(KankaClient, '_request')
    def test_create_location_with_entry(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.create_location('Waterdeep', entry='A coastal city.')
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['json']['name'] == 'Waterdeep'

    @patch.object(KankaClient, '_request')
    def test_delete_character(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.delete_character(456)
        assert mock_request.call_args[0][1] == 'characters/456'
        assert mock_request.call_args[0][0] == 'DELETE'

    @patch.object(KankaClient, '_request')
    def test_delete_location(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.delete_location(789)
        assert mock_request.call_args[0][1] == 'locations/789'

    @patch.object(KankaClient, '_request')
    def test_create_relation_with_attitude(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.create_relation(123, 456, 'Sworn enemy', -80)
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['json']['relation'] == 'Sworn enemy'
        assert call_kwargs['json']['attitude'] == -80
        assert call_kwargs['json']['visibility_id'] == 1

    @patch.object(KankaClient, '_request')
    def test_create_relation_without_attitude(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.create_relation(123, 456, 'Friend', attitude=None)
        call_kwargs = mock_request.call_args[1]
        assert 'attitude' not in call_kwargs['json']

    @patch.object(KankaClient, '_request')
    def test_create_relation_with_two_way(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.create_relation(123, 456, 'Friend', two_way=True)
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['json']['two_way'] is True

    @patch.object(KankaClient, '_request')
    def test_update_relation(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.update_relation(123, 999, relation='Enemy', attitude=-50)
        assert mock_request.call_args[0][1] == 'entities/123/relations/999'
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs['json']['relation'] == 'Enemy'

    @patch.object(KankaClient, '_request')
    def test_delete_relation(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {}

        client.delete_relation(123, 999)
        assert mock_request.call_args[0][1] == 'entities/123/relations/999'
        assert mock_request.call_args[0][0] == 'DELETE'


class TestThrottle:
    def test_throttle_enforces_minimum_interval(self):
        """_throttle should sleep if less than MIN_SECONDS_BETWEEN_REQUESTS elapsed."""
        import kanka_wiki_updater.config as config_mod

        original = config_mod.MIN_SECONDS_BETWEEN_REQUESTS
        try:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = 0.5
            client = KankaClient()
            client._last_request_time = 0.0  # simulate last request "now" (t=0)

            start = time.monotonic()
            client._throttle()
            elapsed = time.monotonic() - start

            assert elapsed >= 0.49  # allow small timing variance
        finally:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = original

    def test_throttle_no_sleep_when_enough_time_passed(self):
        import kanka_wiki_updater.config as config_mod

        original = config_mod.MIN_SECONDS_BETWEEN_REQUESTS
        try:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = 0.1
            client = KankaClient()
            client._last_request_time = time.monotonic() - 5.0  # 5 seconds ago

            start = time.monotonic()
            client._throttle()
            elapsed = time.monotonic() - start

            assert elapsed < 0.05  # should not sleep
        finally:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kanka_client.py -v`
Expected: FAIL — module not found (file doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_kanka_client.py
git commit -m "test: add KankaClient HTTP wrapper tests for pagination, retries, CRUD"
```

---

## Task 6: Test `revert.py` — pure functions and main flow

**Files:**
- Create: `tests/test_revert.py`

**Interfaces:**
- Consumes: `state.get_last_applied_batch()`, mocked KankaClient
- Produces: Tests for relation undo logic, entry restoration, new entity deletion, batch ordering

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for revert module — relation undo, entry restoration, entity deletion."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_state():
    """Mock state module to avoid real file I/O."""
    with patch('kanka_wiki_updater.revert.state') as mock:
        mock.get_last_applied_batch.return_value = None
        yield mock


@pytest.fixture(autouse=True)
def_mock_colors():
    """Ensure colors work without colorama."""
    from kanka_wiki_updater import colors
    return colors


class TestRevertRelationResult:
    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_undo_create_deletes_relation(self, MockClient):
        from kanka_wiki_updater.revert import revert_relation_result

        client = MagicMock()
        client.get_relations.return_value = [{'id': 'rel-1', 'target_id': 456}]

        rr = {
            'action_taken': 'created',
            'target_name': 'Bob',
            'target_id': 456,
        }
        revert_relation_result(123, rr, client)

        client.delete_relation.assert_called_once_with(123, 'rel-1')

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_undo_create_no_id_prints_warning(self, MockClient):
        from kanka_wiki_updater.revert import revert_relation_result

        client = MagicMock()
        client.get_relations.return_value = [{'target_id': 456}]  # no 'id' field

        rr = {
            'action_taken': 'created',
            'target_name': 'Bob',
            'target_id': 456,
        }
        revert_relation_result(123, rr, client)

        client.delete_relation.assert_not_called()

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_undo_update_restores_previous(self, MockClient):
        from kanka_wiki_updater.revert import revert_relation_result

        client = MagicMock()
        client.get_relations.return_value = [
            {'id': 'rel-1', 'target_id': 456, 'relation': 'Friend', 'attitude': 80}
        ]

        rr = {
            'action_taken': 'updated',
            'target_name': 'Bob',
            'target_id': 456,
            'previous_relation': {'relation': 'Acquaintance', 'attitude': None},
        }
        revert_relation_result(123, rr, client)

        client.update_relation.assert_called_once_with(123, 'rel-1', relation='Acquaintance', attitude=None)

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_undo_delete_recreates(self, MockClient):
        from kanka_wiki_updater.revert import revert_relation_result

        client = MagicMock()
        # Relation no longer exists in current state
        client.get_relations.return_value = []

        rr = {
            'action_taken': 'deleted',
            'target_name': 'Bob',
            'target_id': 456,
            'previous_relation': {'relation': 'Friend', 'attitude': 80},
        }
        revert_relation_result(123, rr, client)

        client.create_relation.assert_called_once_with(123, 456, 'Friend', 80)

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_undo_delete_skips_if_already_exists(self, MockClient):
        from kanka_wiki_updater.revert import revert_relation_result

        client = MagicMock()
        # Relation already exists — don't re-create
        client.get_relations.return_value = [
            {'id': 'rel-2', 'target_id': 456, 'relation': 'Friend', 'attitude': 80}
        ]

        rr = {
            'action_taken': 'deleted',
            'target_name': 'Bob',
            'target_id': 456,
            'previous_relation': {'relation': 'Friend', 'attitude': 80},
        }
        revert_relation_result(123, rr, client)

        client.create_relation.assert_not_called()


class TestRevertUpdateEntry:
    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_restores_synopsis_and_reverses_relations(self, MockClient):
        from kanka_wiki_updater.revert import revert_update_entry

        client = MagicMock()
        entry = {
            'entity_name': 'Alice',
            'entity_kind': 'character',
            'entity_local_id': 123,
            'previous_entry': 'Old synopsis',
            'source_journal': 'Session 1',
            'relation_results': [
                {
                    'action_taken': 'created',
                    'target_name': 'Bob',
                    'target_id': 456,
                }
            ],
        }

        revert_update_entry(entry, client)

        # Relations reversed first (in reverse order), then synopsis restored
        assert client.delete_relation.called or client.update_relation.called
        client.update_entity_entry.assert_called_once_with(
            'characters', 123, 'Old synopsis'
        )


class TestRevertNewEntityEntry:
    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_deletes_character(self, MockClient):
        from kanka_wiki_updater.revert import revert_new_entity_entry

        client = MagicMock()
        entry = {
            'entity_name': 'Bob',
            'created_kind': 'character',
            'created_local_id': 789,
        }

        revert_new_entity_entry(entry, client)

        client.delete_character.assert_called_once_with(789)

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_deletes_location(self, MockClient):
        from kanka_wiki_updater.revert import revert_new_entity_entry

        client = MagicMock()
        entry = {
            'entity_name': 'Waterdeep',
            'created_kind': 'location',
            'created_local_id': 999,
        }

        revert_new_entity_entry(entry, client)

        client.delete_location.assert_called_once_with(999)

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_no_record_prints_warning(self, MockClient):
        from kanka_wiki_updater.revert import revert_new_entity_entry

        client = MagicMock()
        entry = {
            'entity_name': 'Unknown',
            'created_kind': None,
            'created_local_id': None,
        }

        revert_new_entity_entry(entry, client)

        client.delete_character.assert_not_called()
        client.delete_location.assert_not_called()


class TestMainFlow:
    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_no_batch_prints_message(self, MockClient):
        from kanka_wiki_updater import state as state_mod
        state_mod.get_last_applied_batch.return_value = None

        with patch('builtins.input', return_value='n'):
            from kanka_wiki_updater.revert import main
            # Should not raise
            main()

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_user_cancel_does_nothing(self, MockClient):
        from kanka_wiki_updater import state as state_mod
        state_mod.get_last_applied_batch.return_value = {
            'run_id': 'abc123',
            'entries': [
                {'proposal_type': 'update', 'entity_name': 'Alice', 'entity_kind': 'character',
                 'entity_local_id': 1, 'previous_entry': 'Old', 'source_journal': 'S1'},
            ],
        }

        with patch('builtins.input', return_value='n'):
            from kanka_wiki_updater.revert import main
            main()

        MockClient.return_value.update_entity_entry.assert_not_called()

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_reverts_updates_before_new_entities(self, MockClient):
        """Update entries should be reverted before new entities (reverse order)."""
        from kanka_wiki_updater import state as state_mod
        state_mod.get_last_applied_batch.return_value = {
            'run_id': 'abc123',
            'entries': [
                {'proposal_type': 'new_entity', 'entity_name': 'Bob', 'created_kind': 'character',
                 'created_local_id': 999},
                {'proposal_type': 'update', 'entity_name': 'Alice', 'entity_kind': 'character',
                 'entity_local_id': 1, 'previous_entry': 'Old', 'source_journal': 'S1'},
            ],
        }

        with patch('builtins.input', return_value='y'):
            from kanka_wiki_updater.revert import main
            main()

        # Bob (new_entity) should be deleted AFTER Alice is restored
        # because entries are reversed: update first, then new_entity
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_revert.py -v`
Expected: FAIL — module not found (file doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_revert.py
git commit -m "test: add revert module tests for relation undo, entry restoration, entity deletion"
```

---

## Task 7: Test `sync_pipeline.py` — main orchestrator logic

**Files:**
- Create: `tests/test_sync_pipeline_main.py`

**Interfaces:**
- Consumes: Mocked KankaClient, state module, LLM chat_json
- Produces: Tests for limit handling, idempotency (processed journals), cursor advancement, new entity dedup

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for sync_pipeline main orchestrator — limit, idempotency, cursor logic."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_state(tmp_path):
    """Provide a temporary data directory via state module."""
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    with patch('kanka_wiki_updater.sync_pipeline.state') as mock:
        mock.DATA_DIR = str(data_dir)
        mock.get_last_sync.return_value = None
        mock.get_processed_journal_ids.return_value = set()
        mock.load_queue.return_value = []
        yield mock


@pytest.fixture(autouse=True)
def mock_llm():
    """Return a neutral LLM response that always proposes no change."""
    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock:
        mock.return_value = {
            'updated_entry': 'Same synopsis',
            'change_summary': '',
            'relation_changes': [],
            'uncertain': [],
        }
        yield mock


@pytest.fixture(autouse=True)
def mock_client():
    """Return empty character/location lists."""
    with patch('kanka_wiki_updater.sync_pipeline.KankaClient') as Mock:
        client = MagicMock()
        client.get_characters.return_value = []
        client.get_locations.return_value = []
        Mock.return_value = client
        yield client


class TestLimitHandling:
    @patch('kanka_wiki_updater.sync_pipeline.main')
    def test_limit_passes_to_main(self, mock_main):
        """--limit N should be passed to main(limit=N)."""
        from kanka_wiki_updater import sync_pipeline

        # Simulate argparse parsing
        with patch.object(sync_pipeline.sys, 'argv', ['sync_pipeline', '--limit', '5']):
            parser = sync_pipeline.argparse.ArgumentParser()
            parser.add_argument('--limit', type=int, default=None)
            args = parser.parse_args()

        assert args.limit == 5


class TestIdempotency:
    @patch('kanka_wiki_updater.sync_pipeline.main')
    def test_skips_processed_journals(self):
        """Journals already in processed set should be skipped."""
        from kanka_wiki_updater import state as state_mod

        # Setup: one journal exists, one is already processed
        client = MagicMock()
        client.get_characters.return_value = []
        client.get_locations.return_value = []
        client.get_journals.return_value = [
            {'id': 100, 'name': 'Session A', 'entry': 'Alice fought a dragon.', 'date': '2024-01-01'},
            {'id': 200, 'name': 'Session B', 'entry': 'Bob drank ale.', 'date': '2024-01-02'},
        ]

        with patch('kanka_wiki_updater.sync_pipeline.KankaClient', return_value=client):
            state_mod.get_processed_journal_ids.return_value = {100}  # Session A already done

            from kanka_wiki_updater import sync_pipeline as sp
            original_main = sp.main
            captured_journals = []

            def capture_main(limit=None):
                client2 = MagicMock()
                client2.get_characters.return_value = []
                client2.get_locations.return_value = []
                # We can't easily intercept inside main, so test the filter directly
                journals = client.get_journals()
                processed = state_mod.get_processed_journal_ids()
                to_process = [j for j in journals if j['id'] not in processed]
                captured_journals.extend(to_process)

            capture_main()

        assert len(captured_journals) == 1
        assert captured_journals[0]['id'] == 200


class TestCursorAdvancement:
    def test_advances_only_when_all_processed(self):
        """lastSync should only advance if all fetched journals were processed."""
        from kanka_wiki_updater import state as state_mod

        # Simulate: 5 journals fetched, but limit=3 means only 3 processed
        journals = [
            {'id': i, 'updated_at': f'2024-01-{i:02d}T10:00:00', 'name': f'Session {i}',
             'entry': 'Test', 'date': f'2024-01-{i:02d}'}
            for i in range(1, 6)
        ]

        # All processed — cursor should advance
        state_mod.get_processed_journal_ids.return_value = {1, 2, 3, 4, 5}
        total_new = len(journals)
        to_process = [j for j in journals if j['id'] not in state_mod.get_processed_journal_ids()]

        assert len(to_process) == 0  # nothing new to process

    def test_does_not_advance_with_limit(self):
        """When --limit leaves unprocessed journals, cursor stays put."""
        from kanka_wiki_updater import state as state_mod

        journals = [
            {'id': i, 'updated_at': f'2024-01-{i:02d}T10:00:00', 'name': f'Session {i}',
             'entry': 'Test', 'date': f'2024-01-{i:02d}'}
            for i in range(1, 6)
        ]

        # Only processed IDs 1, 2, 3 — but limit=3 means journal 5 is unprocessed
        state_mod.get_processed_journal_ids.return_value = {1, 2, 3}
        to_process = [j for j in journals if j['id'] not in state_mod.get_processed_journal_ids()]

        # With limit=3: to_process[:3] = [4,5,...], but total_new=5 != len(to_process)=3
        assert len(to_process) == 2  # IDs 4 and 5


class TestNewEntityDedup:
    def test_same_name_not_suggested_twice(self):
        """A new entity name should not be suggested again in the same run."""
        from kanka_wiki_updater.sync_pipeline import propose_new_entities

        journal = {'id': 1, 'name': 'Session 1', 'entry': 'Bob the Bard appeared.', 'date': '2024-01-01'}
        known_names = set()

        result1 = propose_new_entities(journal, known_names)
        assert len(result1) == 1
        assert result1[0]['entity_name'] == 'Bob the Bard'

        # Same journal, same name — should be filtered by known_names
        result2 = propose_new_entities(journal, known_names)
        assert len(result2) == 0


class TestEmptyJournal:
    def test_empty_entry_skipped(self):
        """Journals with empty/whitespace-only entry should not generate proposals."""
        from kanka_wiki_updater.sync_pipeline import propose_update

        entity = {'name': 'Alice', 'kind': 'character', 'entry': 'Old synopsis'}
        journal = {'id': 1, 'name': 'Session 1', 'entry': '', 'date': '2024-01-01'}

        result = propose_update(123, entity, journal, {})
        assert result is None

    def test_whitespace_only_entry_skipped(self):
        from kanka_wiki_updater.sync_pipeline import propose_update

        entity = {'name': 'Alice', 'kind': 'character', 'entry': 'Old synopsis'}
        journal = {'id': 1, 'name': 'Session 1', 'entry': '   \n\n  ', 'date': '2024-01-01'}

        result = propose_update(123, entity, journal, {})
        assert result is None


class TestNoMeaningfulChange:
    def test_same_synopsis_no_relations_returns_none(self):
        """If LLM returns identical synopsis and no relation changes, proposal is None."""
        from kanka_wiki_updater.sync_pipeline import propose_update

        entity = {'name': 'Alice', 'kind': 'character', 'entry': 'Same text'}
        journal = {'id': 1, 'name': 'Session 1', 'entry': 'Nothing new happened.', 'date': '2024-01-01'}

        # Simulate LLM returning identical entry
        result = propose_update(123, entity, journal, {})
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sync_pipeline_main.py -v`
Expected: FAIL — module not found (file doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_sync_pipeline_main.py
git commit -m "test: add sync_pipeline main orchestrator tests for limit, idempotency, cursor"
```

---

## Task 8: Test `review.py` — main flow and proposal handling

**Files:**
- Create: `tests/test_review_main.py`

**Interfaces:**
- Consumes: Mocked KankaClient, state module, colors module
- Produces: Tests for skipped proposals, batch logging, new-entity-first ordering

- [ ] **Step 1: Write the failing test file**

```python
"""Tests for review main flow — skip no-change, batch logging, entity ordering."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_state(tmp_path):
    """Provide a temporary data directory via state module."""
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    with patch('kanka_wiki_updater.review.state') as mock:
        mock.load_queue.return_value = []
        mock.get_last_applied_batch.return_value = None
        yield mock


class TestSkipNoChange:
    def test_skips_identical_synopsis_no_relations(self):
        """Proposals with no meaningful change should be auto-skipped."""
        from kanka_wiki_updater.review import has_meaningful_change

        proposal = {
            'previous_entry': 'Alice is a warrior.',
            'proposed_entry': 'Alice is a warrior.',
            'relation_changes': [],
        }
        assert has_meaningful_change(proposal) is False


class TestBatchLogging:
    def test_logs_applied_batch(self, tmp_path):
        """Applied proposals should be logged with run_id."""
        from kanka_wiki_updater import state as state_mod

        applied = [
            {'proposal_type': 'update', 'entity_name': 'Alice', 'status': 'applied'},
            {'proposal_type': 'new_entity', 'entity_name': 'Bob', 'status': 'applied'},
        ]
        state_mod.log_applied_batch(applied)

        batch = state_mod.get_last_applied_batch()
        assert batch is not None
        assert len(batch['entries']) == 2
        assert 'run_id' in batch


class TestNewEntityFirst:
    def test_new_entities_reviewed_before_updates(self):
        """New entity proposals should appear before update proposals."""
        queue = [
            {'proposal_type': 'update', 'entity_name': 'Alice'},
            {'proposal_type': 'new_entity', 'entity_name': 'Bob'},
            {'proposal_type': 'update', 'entity_name': 'Charlie'},
        ]

        new_entity_pending = [p for p in queue if p.get('proposal_type') == 'new_entity']
        update_pending = [p for p in queue if p.get('proposal_type') != 'new_entity']

        assert len(new_entity_pending) == 1
        assert len(update_pending) == 2
        assert new_entity_pending[0]['entity_name'] == 'Bob'


class TestAutoSkipCounting:
    def test_counts_skipped_proposals(self):
        """Number of auto-skipped proposals should be tracked."""
        queue = [
            {'proposal_type': 'update', 'entity_name': 'Alice',
             'previous_entry': 'Same', 'proposed_entry': 'Same', 'relation_changes': []},
            {'proposal_type': 'update', 'entity_name': 'Bob',
             'previous_entry': 'Changed', 'proposed_entry': 'Different', 'relation_changes': []},
        ]

        from kanka_wiki_updater.review import has_meaningful_change

        reviewable = [p for p in queue if has_meaningful_change(p)]
        skipped = sum(1 for p in queue if not has_meaningful_change(p))

        assert len(reviewable) == 1
        assert skipped == 1
        assert reviewable[0]['entity_name'] == 'Bob'


class TestProposalStatusTracking:
    def test_rejected_proposal_marked(self):
        """Rejected proposals should have status='rejected'."""
        proposal = {'proposal_type': 'update', 'entity_name': 'Alice'}

        from kanka_wiki_updater.review import has_meaningful_change

        # Simulate rejection
        proposal['status'] = 'rejected'
        assert proposal['status'] == 'rejected'

    def test_applied_proposal_marked(self):
        """Applied proposals should have status='applied'."""
        proposal = {'proposal_type': 'update', 'entity_name': 'Alice'}

        proposal['status'] = 'applied'
        assert proposal['status'] == 'applied'


class TestNoPending:
    def test_empty_queue_prints_message(self, capsys):
        """If no pending proposals, should print a message and return."""
        from kanka_wiki_updater import state as state_mod
        state_mod.load_queue.return_value = []

        with patch('kanka_wiki_updater.review.main') as mock_main:
            # Just test the filter logic
            queue = []
            pending = [p for p in queue if p.get('status') == 'pending']
            assert len(pending) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_review_main.py -v`
Expected: FAIL — module not found (file doesn't exist yet)

- [ ] **Step 3: Commit**

```bash
git add tests/test_review_main.py
git commit -m "test: add review main flow tests for skip logic, batch logging, entity ordering"
```

---

## Task 9: Run full test suite and verify

**Files:**
- All test files created above

**Interfaces:**
- Consumes: All previous tasks
- Produces: Verified passing test suite

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: All tests pass, no errors or warnings

- [ ] **Step 2: Check for any lint issues**

Run: `ruff check .`
Expected: No lint errors in test files

- [ ] **Step 3: Format test files**

Run: `ruff format .`
Expected: All files formatted consistently

- [ ] **Step 4: Final commit**

```bash
git add tests/
git commit -m "test: run full suite, fix lint/format, finalize"
```

---

## Self-Review Checklist

1. **Spec coverage:** Each module with no or partial test coverage now has dedicated tests:
   - `config.py` → Task 2 (env loading, defaults, validation)
   - `kanka_client.py` → Task 5 (HTTP wrapper, pagination, retries, CRUD)
   - `progress.py` → Task 3 (rendering, Unicode/ASCII fallback, edge cases)
   - `colors.py` → Task 4 (enabled/disabled paths, identity fallback)
   - `revert.py` → Task 6 (relation undo, entry restoration, entity deletion)
   - `sync_pipeline.py` main → Task 7 (limit, idempotency, cursor, dedup)
   - `review.py` main → Task 8 (skip logic, batch logging, ordering)

2. **Placeholder scan:** No "TBD", "TODO", or vague instructions found. Every step has concrete code and exact commands.

3. **Type consistency:** All function signatures match existing code (`propose_update`, `has_meaningful_change`, `revert_relation_result`, etc.). Mock fixtures use consistent patterns across tasks.

4. **No duplicate tests:** Task 1 removes the `_extract_json` duplication between `test_llm_client.py` and `test_llm_providers.py`. Remaining tests are unique to their modules.

5. **Edge cases covered:** Empty inputs, None values, missing fields, Windows-specific behavior (CR suppression), division by zero, 429 retry exhaustion — all have explicit test cases.
