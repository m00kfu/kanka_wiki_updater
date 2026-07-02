"""Tests for revert module — relation undo, entry restoration, entity deletion."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_state():
    with patch('kanka_wiki_updater.revert.state') as mock:
        mock.get_last_applied_batch.return_value = None
        yield mock


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
        client.get_relations.return_value = [{'target_id': 456}]

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
        client.get_relations.return_value = [{'id': 'rel-1', 'target_id': 456, 'relation': 'Friend', 'attitude': 80}]

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
        client.get_relations.return_value = [{'id': 'rel-2', 'target_id': 456, 'relation': 'Friend', 'attitude': 80}]

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

        assert client.update_entity_entry.called_with('characters', 123, 'Old synopsis')


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

            main()

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_user_cancel_does_nothing(self, MockClient):
        from kanka_wiki_updater import state as state_mod

        state_mod.get_last_applied_batch.return_value = {
            'run_id': 'abc123',
            'entries': [
                {
                    'proposal_type': 'update',
                    'entity_name': 'Alice',
                    'entity_kind': 'character',
                    'entity_local_id': 1,
                    'previous_entry': 'Old',
                    'source_journal': 'S1',
                },
            ],
        }

        with patch('builtins.input', return_value='n'):
            from kanka_wiki_updater.revert import main

            main()

        MockClient.return_value.update_entity_entry.assert_not_called()

    @patch('kanka_wiki_updater.revert.KankaClient')
    def test_reverts_updates_before_new_entities(self, MockClient):
        from kanka_wiki_updater import state as state_mod

        state_mod.get_last_applied_batch.return_value = {
            'run_id': 'abc123',
            'entries': [
                {
                    'proposal_type': 'new_entity',
                    'entity_name': 'Bob',
                    'created_kind': 'character',
                    'created_local_id': 999,
                },
                {
                    'proposal_type': 'update',
                    'entity_name': 'Alice',
                    'entity_kind': 'character',
                    'entity_local_id': 1,
                    'previous_entry': 'Old',
                    'source_journal': 'S1',
                },
            ],
        }

        with patch('builtins.input', return_value='y'):
            from kanka_wiki_updater.revert import main

            main()
