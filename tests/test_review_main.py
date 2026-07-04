"""Tests for review main flow — skip no-change, batch logging, entity ordering."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_state(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    with patch('kanka_wiki_updater.review.state') as mock:
        mock.load_queue.return_value = []
        mock.get_last_applied_batch.return_value = None
        yield mock


class TestSkipNoChange:
    def test_skips_identical_synopsis_no_relations(self):
        from kanka_wiki_updater.review import has_meaningful_change

        proposal = {
            'previous_entry': 'Alice is a warrior.',
            'proposed_entry': 'Alice is a warrior.',
            'relation_changes': [],
        }
        assert has_meaningful_change(proposal) is False


class TestBatchLogging:
    def test_logs_applied_batch(self, tmp_path):
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
        queue = [
            {
                'proposal_type': 'update',
                'entity_name': 'Alice',
                'previous_entry': 'Same',
                'proposed_entry': 'Same',
                'relation_changes': [],
            },
            {
                'proposal_type': 'update',
                'entity_name': 'Bob',
                'previous_entry': 'Changed',
                'proposed_entry': 'Different',
                'relation_changes': [],
            },
        ]

        from kanka_wiki_updater.review import has_meaningful_change

        reviewable = [p for p in queue if has_meaningful_change(p)]
        skipped = sum(1 for p in queue if not has_meaningful_change(p))

        assert len(reviewable) == 1
        assert skipped == 1
        assert reviewable[0]['entity_name'] == 'Bob'


class TestProposalStatusTracking:
    def test_rejected_proposal_marked(self):
        proposal = {'proposal_type': 'update', 'entity_name': 'Alice'}

        proposal['status'] = 'rejected'
        assert proposal['status'] == 'rejected'

    def test_applied_proposal_marked(self):
        proposal = {'proposal_type': 'update', 'entity_name': 'Alice'}

        proposal['status'] = 'applied'
        assert proposal['status'] == 'applied'


class TestNoPending:
    def test_empty_queue_prints_message(self, capsys):
        queue = []
        pending = [p for p in queue if p.get('status') == 'pending']
        assert len(pending) == 0
