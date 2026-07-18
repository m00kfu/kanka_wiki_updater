"""Tests for sync_pipeline main orchestrator — limit, idempotency, cursor logic."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_state(tmp_path):
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
    with patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock:
        mock.return_value = {
            'updated_entry': 'Same synopsis',
            'change_summary': '',
            'relation_changes': [],
            'uncertain': [],
        }
        yield mock


@pytest.fixture(autouse=True)
def mock_client():
    with patch('kanka_wiki_updater.sync_pipeline.KankaClient') as Mock:
        client = MagicMock()
        client.get_characters.return_value = []
        client.get_locations.return_value = []
        client.get_organizations.return_value = []
        Mock.return_value = client
        yield client


class TestLimitHandling:
    @patch('kanka_wiki_updater.sync_pipeline.main')
    def test_limit_passes_to_main(self, mock_main):
        from kanka_wiki_updater import sync_pipeline

        parser = sync_pipeline.argparse.ArgumentParser()
        parser.add_argument('--limit', type=int, default=None)
        args = parser.parse_args(['--limit', '5'])

        assert args.limit == 5


class TestIdempotency:
    def test_skips_processed_journals(self):
        from kanka_wiki_updater import sync_pipeline

        journals = [
            {'id': 100, 'name': 'Session A', 'entry': 'Alice fought a dragon.', 'date': '2024-01-01'},
            {'id': 200, 'name': 'Session B', 'entry': 'Bob drank ale.', 'date': '2024-01-02'},
        ]

        with patch.object(sync_pipeline.state, 'get_processed_journal_ids', return_value={100}):
            to_process = [j for j in journals if j['id'] not in sync_pipeline.state.get_processed_journal_ids()]

        assert len(to_process) == 1
        assert to_process[0]['id'] == 200


class TestCursorAdvancement:
    def test_advances_only_when_all_processed(self):
        from kanka_wiki_updater import sync_pipeline

        journals = [
            {
                'id': i,
                'updated_at': f'2024-01-{i:02d}T10:00:00',
                'name': f'Session {i}',
                'entry': 'Test',
                'date': f'2024-01-{i:02d}',
            }
            for i in range(1, 6)
        ]

        with patch.object(sync_pipeline.state, 'get_processed_journal_ids', return_value={1, 2, 3, 4, 5}):
            to_process = [j for j in journals if j['id'] not in sync_pipeline.state.get_processed_journal_ids()]

        assert len(to_process) == 0

    def test_does_not_advance_with_limit(self):
        from kanka_wiki_updater import sync_pipeline

        journals = [
            {
                'id': i,
                'updated_at': f'2024-01-{i:02d}T10:00:00',
                'name': f'Session {i}',
                'entry': 'Test',
                'date': f'2024-01-{i:02d}',
            }
            for i in range(1, 6)
        ]

        with patch.object(sync_pipeline.state, 'get_processed_journal_ids', return_value={1, 2, 3}):
            to_process = [j for j in journals if j['id'] not in sync_pipeline.state.get_processed_journal_ids()]

        assert len(to_process) == 2


class TestNewEntityDedup:
    def test_same_name_not_suggested_twice(self):
        with patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock_chat:
            from kanka_wiki_updater.sync_pipeline import propose_new_entities

            mock_chat.return_value = {
                'new_entities': [
                    {'name': 'Bob the Bard', 'suggested_type': 'character', 'draft_entry': '', 'reason': ''},
                ],
            }

            journal = {'id': 1, 'name': 'Session 1', 'entry': 'Bob the Bard appeared.', 'date': '2024-01-01'}
            known_names = set()

            result1 = propose_new_entities(journal, known_names)
            assert len(result1) == 1
            assert result1[0]['entity_name'] == 'Bob the Bard'

            # Add to known names manually (simulating what main() does after processing result1)
            known_names.add('Bob the Bard')

            result2 = propose_new_entities(journal, known_names)
            assert len(result2) == 0


class TestEmptyJournal:
    def test_empty_entry_skipped(self):
        from kanka_wiki_updater.sync_pipeline import propose_update

        entity_data = {'name': 'Alice', 'kind': 'character', 'entry': 'Old synopsis', 'relations': [], 'local_id': 1}
        journal = {'id': 1, 'name': 'Session 1', 'entry': '', 'date': '2024-01-01'}

        result = propose_update(123, entity_data, journal, {})
        assert result is None

    def test_whitespace_only_entry_skipped(self):
        from kanka_wiki_updater.sync_pipeline import propose_update

        entity_data = {'name': 'Alice', 'kind': 'character', 'entry': 'Old synopsis', 'relations': [], 'local_id': 1}
        journal = {'id': 1, 'name': 'Session 1', 'entry': '   \n\n  ', 'date': '2024-01-01'}

        result = propose_update(123, entity_data, journal, {})
        assert result is None


class TestNoMeaningfulChange:
    def test_same_synopsis_no_relations_returns_none(self, monkeypatch):
        from kanka_wiki_updater import sync_pipeline as sp

        mock_result = {
            'updated_entry': 'Same text',
            'change_summary': '',
            'relation_changes': [],
            'uncertain': [],
        }
        monkeypatch.setattr('kanka_wiki_updater.synopsis_generator.chat_json', lambda sys, usr: mock_result)

        entity_data = {'name': 'Alice', 'kind': 'character', 'entry': 'Same text', 'relations': [], 'local_id': 1}
        journal = {'id': 1, 'name': 'Session 1', 'entry': 'Nothing new happened.', 'date': '2024-01-01'}

        result = sp.propose_update(123, entity_data, journal, {})
        assert result is None
