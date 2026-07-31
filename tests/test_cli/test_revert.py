"""Tests for revert module — relation undo, entry restoration, entity deletion."""

from unittest.mock import MagicMock, patch

import pytest



class TestRevertRelationResult:
    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_undo_create_deletes_relation(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_relation_result

        client = MagicMock()
        client.get_relations.return_value = [{'id': 'rel-1', 'target_id': 456}]

        rr = {
            'action_taken': 'created',
            'target_name': 'Bob',
            'target_id': 456,
        }
        revert_relation_result(123, rr, client)

        client.delete_relation.assert_called_once_with(123, 'rel-1')

    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_undo_create_no_id_prints_warning(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_relation_result

        client = MagicMock()
        client.get_relations.return_value = [{'target_id': 456}]

        rr = {
            'action_taken': 'created',
            'target_name': 'Bob',
            'target_id': 456,
        }
        revert_relation_result(123, rr, client)

        client.delete_relation.assert_not_called()

    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_undo_update_restores_previous(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_relation_result

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

    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_undo_delete_recreates(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_relation_result

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

    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_undo_delete_skips_if_already_exists(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_relation_result

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
    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_restores_synopsis_and_reverses_relations(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_update_entry

        client = MagicMock()
        client.get_relations.return_value = []
        entry = {
            'entity_name': 'Alice',
            'entity_kind': 'character',
            'entity_id': 123,
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

        client.update_entity_entry.assert_called_with('characters', 123, 'Old synopsis')


class TestRevertNewEntityEntry:
    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_deletes_character(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_new_entity_entry

        client = MagicMock()
        entry = {
            'entity_name': 'Bob',
            'created_kind': 'character',
            'created_local_id': 789,
        }

        revert_new_entity_entry(entry, client)

        client.delete_character.assert_called_once_with(789)

    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_deletes_location(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_new_entity_entry

        client = MagicMock()
        entry = {
            'entity_name': 'Waterdeep',
            'created_kind': 'location',
            'created_local_id': 999,
        }

        revert_new_entity_entry(entry, client)

        client.delete_location.assert_called_once_with(999)

    @patch('kanka_wiki_updater.cli.revert.KankaClient')
    def test_no_record_prints_warning(self, MockClient):
        from kanka_wiki_updater.cli.revert import revert_new_entity_entry

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
    """Test the main() CLI flow using a real DB."""

    def test_no_batch_prints_message(self, state_db):
        from kanka_wiki_updater.core import state as state_mod

        assert state_mod.get_last_applied_batch() is None

        with patch('builtins.input', return_value='n'), \
             patch('kanka_wiki_updater.cli.revert.KankaClient') as MockClient:
                from kanka_wiki_updater.cli.revert import main

                main()

        MockClient.return_value.update_entity_entry.assert_not_called()

    def test_user_cancel_does_nothing(self, state_db):
        from kanka_wiki_updater.core import state as state_mod

        batch_entries = [
            {
                'proposal_type': 'update',
                'entity_name': 'Alice',
                'entity_kind': 'character',
                'entity_local_id': 1,
                'previous_entry': 'Old',
                'source_journal': 'S1',
            },
        ]
        state_mod.log_applied_batch(batch_entries)

        with patch('builtins.input', return_value='n'), \
             patch('kanka_wiki_updater.cli.revert.KankaClient') as MockClient:
                from kanka_wiki_updater.cli.revert import main

                main()

        MockClient.return_value.update_entity_entry.assert_not_called()

    def test_reverts_updates_before_new_entities(self, state_db):
        from kanka_wiki_updater.core import state as state_mod

        batch_entries = [
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
        ]
        state_mod.log_applied_batch(batch_entries)

        # Verify batch is stored and retrievable
        batch = state_mod.get_last_applied_batch()
        assert batch is not None
        assert len(batch['entries']) == 2

        with patch('builtins.input', return_value='y'), \
             patch('kanka_wiki_updater.cli.revert.KankaClient') as MockClient:
                client_inst = MagicMock()
                MockClient.return_value = client_inst

                from kanka_wiki_updater.cli.revert import main

                main()

        # After revert, the batch should be marked reverted (get_last_applied_batch returns None)
        assert state_mod.get_last_applied_batch() is None

    def test_revert_marks_batch_as_reverted(self, state_db):
        """After revert, get_last_applied_batch should return None for a single-batch scenario."""
        from kanka_wiki_updater.core import state as state_mod

        batch_entries = [
            {
                'proposal_type': 'update',
                'entity_name': 'Alice',
                'entity_kind': 'character',
                'entity_local_id': 1,
                'previous_entry': 'Old',
                'source_journal': 'S1',
            },
        ]
        state_mod.log_applied_batch(batch_entries)

        with patch('builtins.input', return_value='y'), \
             patch('kanka_wiki_updater.cli.revert.KankaClient') as MockClient:
                client_inst = MagicMock()
                MockClient.return_value = client_inst

                from kanka_wiki_updater.cli.revert import main

                main()

        # After revert, the batch should be marked reverted
        assert state_mod.get_last_applied_batch() is None
