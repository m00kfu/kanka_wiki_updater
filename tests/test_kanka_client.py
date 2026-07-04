"""Tests for KankaClient HTTP wrapper (raw requests.Session)."""

from unittest.mock import MagicMock, patch

import pytest

from kanka_wiki_updater.kanka_client import KankaClient, KankaError

# -- helpers -----------------------------------------------------------------


def _make_mock_session():
    """Build a mock requests.Session for unit tests."""
    return MagicMock()


# -- tests -------------------------------------------------------------------


class TestKankaError:
    def test_is_runtime_error_subclass(self):
        assert issubclass(KankaError, RuntimeError)

    def test_contains_message(self):
        err = KankaError('bad thing')
        assert 'bad thing' in str(err)


class TestRequestErrors:
    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_400_raises_kanka_error(self, mock_session_cls):
        session = _make_mock_session()
        resp = MagicMock()
        resp.status_code = 400
        resp.text = 'Bad Request'
        session.request.return_value = resp

        client = KankaClient.__new__(KankaClient)
        client._session = session
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 1
        client._retry_on_rate_limit = True

        with pytest.raises(KankaError, match='400'):
            client._request('GET', 'journals')


class TestGetAllPagination:
    def test_single_page(self):
        session = _make_mock_session()
        resp_data = [{'id': 1, 'name': 'Journal 1'}, {'id': 2, 'name': 'Journal 2'}]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'data': resp_data}
        session.request.return_value = mock_resp

        client = KankaClient.__new__(KankaClient)
        client._session = session
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 1
        client._retry_on_rate_limit = True

        result = client.get_journals(since='2024-01-01', journal_type='Session')
        assert len(result) == 2
        assert result[0]['id'] == 1


class TestCRUDOperations:
    def _make_session(self, return_value=None):
        session = _make_mock_session()
        resp = MagicMock()
        resp.status_code = 200
        if return_value is not None:
            resp.json.return_value = return_value
        else:
            resp.json.return_value = {'data': []}
        session.request.return_value = resp
        return session

    def _make_client(self, return_value=None):
        session = self._make_session(return_value)
        client = KankaClient.__new__(KankaClient)
        client._session = session
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 1
        client._retry_on_rate_limit = True
        return client, session

    # -- journals --

    def test_get_journals_passes_params(self):
        client, session = self._make_client()
        resp_data = {'data': [{'id': 1, 'name': 'Test'}]}
        session.request.return_value.json.return_value = resp_data
        client.get_journals(since='2024-01-01', journal_type='Session')
        call_kwargs = session.request.call_args[1]
        assert 'params' in call_kwargs
        assert call_kwargs['params']['last_sync'] == '2024-01-01'
        assert call_kwargs['params']['type'] == 'Session'

    # -- update entity entry --

    def test_update_entity_entry_converts_newlines(self):
        client, session = self._make_client()
        client.update_entity_entry('characters', 123, 'para1\n\npara2\nline3')
        call_kwargs = session.request.call_args[1]
        assert call_kwargs['json']['entry'] == 'para1<br><br>para2<br>line3'

    # -- create character --

    def test_create_character_with_entry(self):
        client, session = self._make_client()
        resp_data = {'data': {'id': 1, 'entity_id': 42, 'name': 'Alice', 'entry': 'A warrior.'}}
        session.request.return_value.json.return_value = resp_data

        result = client.create_character('Alice', entry='A brave warrior.')
        call_kwargs = session.request.call_args[1]
        assert call_kwargs['json']['name'] == 'Alice'
        assert call_kwargs['json']['entry'] == 'A brave warrior.'
        assert result['data']['name'] == 'Alice'

    def test_create_character_without_entry(self):
        client, session = self._make_client()
        resp_data = {'data': {'id': 2, 'entity_id': 43, 'name': 'Bob', 'entry': None}}
        session.request.return_value.json.return_value = resp_data

        client.create_character('Bob')
        call_kwargs = session.request.call_args[1]
        assert 'entry' not in call_kwargs['json']

    # -- create location --

    def test_create_location_with_entry(self):
        client, session = self._make_client()
        resp_data = {'data': {'id': 3, 'entity_id': 44, 'name': 'Waterdeep', 'entry': 'A city.'}}
        session.request.return_value.json.return_value = resp_data

        client.create_location('Waterdeep', entry='A coastal city.')
        call_kwargs = session.request.call_args[1]
        assert call_kwargs['json']['name'] == 'Waterdeep'

    # -- delete --

    def test_delete_character(self):
        client, session = self._make_client()
        result = client.delete_character(456)
        call_args = session.request.call_args
        assert call_args[0][0] == 'DELETE'
        assert '/characters/456' in call_args[0][1]
        assert result is True

    def test_delete_location(self):
        client, session = self._make_client()
        result = client.delete_location(789)
        call_args = session.request.call_args
        assert call_args[0][0] == 'DELETE'
        assert '/locations/789' in call_args[0][1]
        assert result is True

    # -- relations --

    def test_create_relation_with_attitude(self):
        client, session = self._make_client()
        resp_data = {'data': [{'id': 1, 'owner_id': 123, 'target_id': 456, 'relation': 'Sworn enemy'}]}
        session.request.return_value.json.return_value = resp_data

        client.create_relation(123, 456, 'Sworn enemy', attitude=-80)
        call_args = session.request.call_args
        assert call_args[0][0] == 'POST'
        assert '/entities/123/relations' in call_args[0][1]
        body = call_args[1]['json']
        assert body['owner_id'] == 123
        assert body['target_id'] == 456
        assert body['relation'] == 'Sworn enemy'
        assert body['attitude'] == -80
        assert body['visibility_id'] == 1

    def test_create_relation_without_attitude(self):
        client, session = self._make_client()
        resp_data = {'data': [{'id': 2, 'owner_id': 123, 'target_id': 456, 'relation': 'Friend'}]}
        session.request.return_value.json.return_value = resp_data

        client.create_relation(123, 456, 'Friend', attitude=None)
        call_kwargs = session.request.call_args[1]
        body = call_kwargs['json']
        assert 'attitude' not in body

    def test_create_relation_with_two_way(self):
        client, session = self._make_client()
        resp_data = {'data': [{'id': 3, 'owner_id': 123, 'target_id': 456, 'relation': 'Friend'}]}
        session.request.return_value.json.return_value = resp_data

        client.create_relation(123, 456, 'Friend', two_way=True)
        call_kwargs = session.request.call_args[1]
        body = call_kwargs['json']
        assert body['two_way'] is True

    def test_update_relation(self):
        client, session = self._make_client()
        client.update_relation(123, 999, relation='Enemy', attitude=-50)
        call_args = session.request.call_args
        assert call_args[0][0] == 'PATCH'
        assert '/entities/123/relations/999' in call_args[0][1]
        call_kwargs = session.request.call_args[1]
        assert call_kwargs['json'] == {'relation': 'Enemy', 'attitude': -50}

    def test_delete_relation(self):
        client, session = self._make_client()
        client.delete_relation(123, 999)
        call_args = session.request.call_args
        assert call_args[0][0] == 'DELETE'
        assert '/entities/123/relations/999' in call_args[0][1]


class TestThrottle:
    """Throttling is now handled by python-kanka; verify config value still exists."""

    def test_config_has_request_interval(self):
        import kanka_wiki_updater.config as real_config

        assert hasattr(real_config, 'REQUEST_INTERVAL')
        assert isinstance(real_config.REQUEST_INTERVAL, float)


# -- sanity: ensure KankaClient can be instantiated with mocked env -----------


class TestInstantiation:
    def test_can_create_client(self):
        """KankaClient() should not raise when token is set."""
        client = KankaClient()
        assert client._session is not None  # requests.Session instance

    def test_session_has_correct_headers(self):

        client = KankaClient()
        headers = dict(client._session.headers)
        assert 'Authorization' in headers or any('authorization' in k.lower() for k in headers)
        assert 'Accept' in headers or any('accept' in k.lower() for k in headers)


class TestRequestRetry:
    """Test the _request method's retry and error handling."""

    def _make_mock_response(self, status_code=200, response_body=None, headers=None):
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
        mock_resp = self._make_mock_response(200, {'data': [1, 2]})
        client._session.request.return_value = mock_resp

        result = client._request('GET', 'journals')
        assert result == {'data': [1, 2]}

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_401_raises_kanka_error(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_mock_response(401)
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
        mock_resp = self._make_mock_response(204, None)
        mock_resp.json.side_effect = ValueError('No JSON')
        client._session.request.return_value = mock_resp

        result = client._request('DELETE', 'characters/123')
        assert result == {}

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_403_raises_kanka_error(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_mock_response(403)
        client._session.request.return_value = mock_resp

        with pytest.raises(KankaError, match='Access forbidden'):
            client._request('GET', 'journals')

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_404_raises_kanka_error(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_mock_response(404)
        client._session.request.return_value = mock_resp

        with pytest.raises(KankaError, match='Resource not found'):
            client._request('GET', 'journals')

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_500_raises_kanka_error(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_mock_response(500, {'error': 'Internal error'})
        client._session.request.return_value = mock_resp

        with pytest.raises(KankaError, match='API error 500'):
            client._request('GET', 'journals')

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_rate_limit_retries_then_succeeds(self, mock_session_cls):

        with patch('kanka_wiki_updater.kanka_client._time') as mock_time:
            client = KankaClient.__new__(KankaClient)
            client._base_url = 'https://api.kanka.io/1.0'
            client._campaign_id = 999
            client._retry_on_rate_limit = True
            client._session = MagicMock()

            # First call returns 429, second succeeds
            rate_limited_resp = self._make_mock_response(429)
            success_resp = self._make_mock_response(200, {'data': [1]})
            client._session.request.side_effect = [rate_limited_resp, success_resp]

            result = client._request('GET', 'journals')
            assert result == {'data': [1]}
            assert client._session.request.call_count == 2
            mock_time.sleep.assert_called()

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_rate_limit_respects_retry_after_header(self, mock_session_cls):

        with patch('kanka_wiki_updater.kanka_client._time') as mock_time:
            client = KankaClient.__new__(KankaClient)
            client._base_url = 'https://api.kanka.io/1.0'
            client._campaign_id = 999
            client._retry_on_rate_limit = True
            client._session = MagicMock()

            rate_limited_resp = self._make_mock_response(429)
            rate_limited_resp.headers['Retry-After'] = '0.1'
            success_resp = self._make_mock_response(200, {'data': []})
            client._session.request.side_effect = [rate_limited_resp, success_resp]

            result = client._request('GET', 'journals')
            assert result == {'data': []}
            mock_time.sleep.assert_called_with(0.1)

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_rate_limit_exhausted_raises(self, mock_session_cls):

        with patch('kanka_wiki_updater.kanka_client._time') as mock_time:
            client = KankaClient.__new__(KankaClient)
            client._base_url = 'https://api.kanka.io/1.0'
            client._campaign_id = 999
            client._retry_on_rate_limit = True
            client._session = MagicMock()

            rate_limited_resp = self._make_mock_response(429)
            # Return 429 for all attempts (max_retries=8, so we need 9 total calls)
            client._session.request.side_effect = [rate_limited_resp] * 10

            with pytest.raises(KankaError, match='Rate limit exceeded'):
                client._request('GET', 'journals')
            assert mock_time.sleep.call_count >= 2  # At least a few retries happened

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_429_disabled_does_not_retry(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = False
        client._session = MagicMock()

        rate_limited_resp = self._make_mock_response(429)
        client._session.request.return_value = rate_limited_resp

        with pytest.raises(KankaError, match='Rate limit exceeded'):
            client._request('GET', 'journals')

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_request_exception_raises_kanka_error(self, mock_session_cls):
        import requests as _requests

        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 999
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        client._session.request.side_effect = _requests.RequestException('connection refused')

        with pytest.raises(KankaError, match='Request failed'):
            client._request('GET', 'journals')

    def test_init_creates_session_with_correct_headers(self):

        client = KankaClient()
        assert hasattr(client, '_session')
        assert hasattr(client, '_base_url')
        assert hasattr(client, '_campaign_id')
        assert hasattr(client, '_retry_on_rate_limit')
        # Verify session has correct headers set
        hdrs_lower = {k.lower(): k for k in client._session.headers}
        assert 'authorization' in hdrs_lower or any('authorization' in k for k in client._session.headers)
        assert 'accept' in hdrs_lower or any('accept' in k for k in client._session.headers)

    @patch('kanka_wiki_updater.kanka_client.requests.Session')
    def test_url_construction_includes_campaign_id(self, mock_session_cls):
        client = KankaClient.__new__(KankaClient)
        client._base_url = 'https://api.kanka.io/1.0'
        client._campaign_id = 42
        client._retry_on_rate_limit = True
        client._session = MagicMock()
        mock_resp = self._make_mock_response(200, {'data': []})
        client._session.request.return_value = mock_resp

        client._request('GET', 'journals')
        called_url = client._session.request.call_args[0][1]
        assert '/campaigns/42/' in called_url
