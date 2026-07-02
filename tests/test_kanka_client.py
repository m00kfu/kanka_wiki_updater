"""Tests for KankaClient HTTP wrapper (mocked requests)."""

import time
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
            MIN_SECONDS_BETWEEN_REQUESTS=0,
        ),
    )


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
        client = KankaClient()
        client.session = MagicMock()
        resp = MagicMock(status_code=429, headers={'Retry-After': '0.01'})
        client.session.request.return_value = resp

        with pytest.raises(KankaError) as exc_info:
            client._request('GET', 'journals')
        assert 'gave up' in str(exc_info.value).lower() or 'repeated 429' in str(exc_info.value).lower()
        assert client.session.request.call_count == 5

    @patch('kanka_wiki_updater.kanka_client.KankaClient._throttle')
    def test_success_returns_json(self, mock_throttle):
        client = KankaClient()
        client.session = MagicMock()
        resp = MagicMock(text='{"data": [{"id": 1}]}', status_code=200)
        resp.json.return_value = {'data': [{'id': 1}]}
        client.session.request.return_value = resp

        result = client._request('GET', 'journals')
        assert result == {'data': [{'id': 1}]}

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

    @patch.object(KankaClient, '_request')
    def test_params_only_on_first_page(self, mock_request):
        client = KankaClient()
        mock_request.return_value = {'data': [], 'links': {}}

        client._get_all('journals', params={'lastSync': '2024-01-01'})
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
        import kanka_wiki_updater.config as config_mod

        original = config_mod.MIN_SECONDS_BETWEEN_REQUESTS
        try:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = 0.5
            client = KankaClient()
            client._last_request_time = 0.0

            start = time.monotonic()
            client._throttle()
            elapsed = time.monotonic() - start

            assert elapsed >= 0.49
        finally:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = original

    def test_throttle_no_sleep_when_enough_time_passed(self):
        import kanka_wiki_updater.config as config_mod

        original = config_mod.MIN_SECONDS_BETWEEN_REQUESTS
        try:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = 0.1
            client = KankaClient()
            client._last_request_time = time.monotonic() - 5.0

            start = time.monotonic()
            client._throttle()
            elapsed = time.monotonic() - start

            assert elapsed < 0.05
        finally:
            config_mod.MIN_SECONDS_BETWEEN_REQUESTS = original
