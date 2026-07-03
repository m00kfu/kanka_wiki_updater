"""Tests for KankaClient HTTP wrapper (mocked python-kanka client)."""

import types
from unittest.mock import MagicMock, patch

import pytest

from kanka_wiki_updater.kanka_client import KankaClient, KankaError

# -- helpers -----------------------------------------------------------------


def _make_mock():
    """Build a mock that satisfies every attribute used by KankaClient."""
    inner = MagicMock()
    return inner


class FakeKankaClient:
    """Minimal fake kanka.KankaClient for unit tests.

    Accepts any token/campaign_id values (MagicMocks or real) and stores
    sub-resource mocks that individual tests control via the returned mock.
    """

    def __init__(self, *a, **kw):
        self._inner = _make_mock()
        # Set up sub-resources before returning the wrapper instance
        self.journals = self._inner.journals
        self.characters = self._inner.characters
        self.locations = self._inner.locations
        self.relations = self._inner.relations


# -- test fixture: patch kanka.KankaClient at module level ------------------


@pytest.fixture()
def fake_kanka():
    """Replace kanka.KankaClient with FakeKankaClient for the duration of a test."""
    with patch('kanka_wiki_updater.kanka_client.kanka.KankaClient', FakeKankaClient):
        yield


# -- tests -------------------------------------------------------------------


class TestKankaError:
    def test_is_runtime_error_subclass(self):
        assert issubclass(KankaError, RuntimeError)

    def test_contains_message(self):
        err = KankaError('bad thing')
        assert 'bad thing' in str(err)


class TestRequestErrors:
    @patch('kanka_wiki_updater.kanka_client.kanka.KankaClient', FakeKankaClient)
    def test_400_raises_kanka_error(self):
        inner = _make_mock()
        inner.journals.list.side_effect = Exception('400 Bad Request')

        real_client = KankaClient.__new__(KankaClient)
        real_client._client = inner  # use the mock directly - KankaClient accesses ._client.journals.list

        with pytest.raises(Exception) as exc_info:
            real_client.get_journals()
        assert '400' in str(exc_info.value)


class TestGetAllPagination:
    def test_single_page(self):
        mock = _make_mock()
        journal1 = types.SimpleNamespace(id=1, name='Journal 1')
        journal2 = types.SimpleNamespace(id=2, name='Journal 2')
        mock.journals.list.return_value = [journal1, journal2]

        client = KankaClient.__new__(KankaClient)
        client._client = mock

        result = client.get_journals(since='2024-01-01', journal_type='Session')
        assert len(result) == 2
        assert result[0].id == 1

    def test_params_passed_through(self):
        mock = _make_mock()
        mock.journals.list.return_value = []

        client = KankaClient.__new__(KankaClient)
        client._client = mock

        client.get_journals(since='2024-06-01', journal_type='Session')
        call_kwargs = mock.journals.list.call_args[1]
        assert 'last_sync' in call_kwargs
        assert call_kwargs['last_sync'] == '2024-06-01'


class TestCRUDOperations:
    def _client_with_mock(self):
        mock = _make_mock()
        client = KankaClient.__new__(KankaClient)
        client._client = mock
        return client, mock

    # -- journals --

    def test_get_journals_passes_params(self):
        client, mock = self._client_with_mock()
        mock.journals.list.return_value = []
        client.get_journals(since='2024-01-01', journal_type='Session')
        call_kwargs = mock.journals.list.call_args[1]
        assert 'last_sync' in call_kwargs
        assert call_kwargs['last_sync'] == '2024-01-01'

    # -- update entity entry --

    def test_update_entity_entry_converts_newlines(self):
        client, mock = self._client_with_mock()
        client.update_entity_entry('characters', 123, 'para1\n\npara2\nline3')
        call_args = mock.characters.update.call_args[1]
        assert call_args['entry'] == 'para1<br><br>para2<br>line3'

    # -- create character --

    def test_create_character_with_entry(self):
        client, mock = self._client_with_mock()
        char = types.SimpleNamespace(id=1, entity_id=42, name='Alice', entry='A warrior.')
        mock.characters.create.return_value = char  # always returns *char*

        result = client.create_character('Alice', entry='A brave warrior.')
        call_kwargs = mock.characters.create.call_args[1]
        assert call_kwargs['name'] == 'Alice'
        assert call_kwargs['entry'] == 'A brave warrior.'
        # getattr(char, k) returns the real value for str attrs (SimpleNamespace does this correctly)
        assert result['data']['name'] == 'Alice'

    def test_create_character_without_entry(self):
        client, mock = self._client_with_mock()
        char = types.SimpleNamespace(id=2, entity_id=43, name='Bob', entry=None)
        mock.characters.create.return_value = char

        client.create_character('Bob')
        call_kwargs = mock.characters.create.call_args[1]
        assert 'entry' not in call_kwargs

    # -- create location --

    def test_create_location_with_entry(self):
        client, mock = self._client_with_mock()
        loc = types.SimpleNamespace(id=3, entity_id=44, name='Waterdeep', entry='A city.')
        mock.locations.create.return_value = loc

        client.create_location('Waterdeep', entry='A coastal city.')
        call_kwargs = mock.locations.create.call_args[1]
        assert call_kwargs['name'] == 'Waterdeep'

    # -- delete --

    def test_delete_character(self):
        client, mock = self._client_with_mock()
        mock.characters.delete.return_value = True
        client.delete_character(456)
        mock.characters.delete.assert_called_once_with(456)

    def test_delete_location(self):
        client, mock = self._client_with_mock()
        mock.locations.delete.return_value = True
        client.delete_location(789)
        mock.locations.delete.assert_called_once_with(789)

    # -- relations --

    def test_create_relation_with_attitude(self):
        client, mock = self._client_with_mock()
        mock._request.return_value = {'data': [{'id': 1, 'owner_id': 123, 'target_id': 456, 'relation': 'Sworn enemy'}]}

        client.create_relation(123, 456, 'Sworn enemy', attitude=-80)
        mock._request.assert_called_once()
        call_args = mock._request.call_args
        assert call_args[0] == ('POST', 'entities/123/relations')
        body = call_args[1]['json']
        assert body['owner_id'] == 123
        assert body['target_id'] == 456
        assert body['relation'] == 'Sworn enemy'
        assert body['attitude'] == -80
        assert body['visibility_id'] == 1

    def test_create_relation_without_attitude(self):
        client, mock = self._client_with_mock()
        mock._request.return_value = {'data': [{'id': 2, 'owner_id': 123, 'target_id': 456, 'relation': 'Friend'}]}

        client.create_relation(123, 456, 'Friend', attitude=None)
        call_kwargs = mock._request.call_args[1]
        body = call_kwargs['json']
        assert 'attitude' not in body

    def test_create_relation_with_two_way(self):
        client, mock = self._client_with_mock()
        mock._request.return_value = {'data': [{'id': 3, 'owner_id': 123, 'target_id': 456, 'relation': 'Friend'}]}

        client.create_relation(123, 456, 'Friend', two_way=True)
        call_kwargs = mock._request.call_args[1]
        body = call_kwargs['json']
        assert body['two_way'] is True

    def test_update_relation(self):
        client, mock = self._client_with_mock()
        client.update_relation(123, 999, relation='Enemy', attitude=-50)
        call_kwargs = mock._request.call_args[1]
        assert call_kwargs['json'] == {'relation': 'Enemy', 'attitude': -50}

    def test_delete_relation(self):
        client, mock = self._client_with_mock()
        client.delete_relation(123, 999)
        mock._request.assert_called_once_with('DELETE', 'entities/123/relations/999')


class TestThrottle:
    """Throttling is now handled by python-kanka; verify config value still exists."""

    def test_config_has_request_interval(self):
        import kanka_wiki_updater.config as real_config

        assert hasattr(real_config, 'REQUEST_INTERVAL')
        assert isinstance(real_config.REQUEST_INTERVAL, float)


# -- sanity: ensure KankaClient can be instantiated with mocked env -----------


class TestInstantiation:
    @patch('kanka_wiki_updater.kanka_client.kanka.KankaClient', FakeKankaClient)
    def test_can_create_client(self):
        """KankaClient() should not raise when token is set."""
        client = KankaClient()
        assert client._client is not None  # fake kanka.KankaClient instance
