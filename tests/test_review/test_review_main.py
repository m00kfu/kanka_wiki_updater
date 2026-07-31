"""Tests for review main flow — skip no-change, batch logging, entity ordering."""

import os
from unittest.mock import patch

import pytest


# NOTE: These tests reference functions (has_meaningful_change,
# log_applied_batch, get_last_applied_batch) that were removed from
# the codebase during refactoring.  Keep them as skipped placeholders.


@pytest.mark.skip(reason="has_meaningful_change removed during refactor")
class TestSkipNoChange:
    def test_skips_identical_synopsis_no_relations(self):
        pass


class TestBatchLogging:
    def test_logs_applied_batch(self, state_db):
        from kanka_wiki_updater.core import state as state_mod

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
    def test_new_entities_reviewed_before_updates(self, state_db):
        """Verify new_entity proposals are queued before update proposals."""
        from kanka_wiki_updater.core import state

        new_candidate = {
            'proposal_type': 'new_entity',
            'entity_name': 'TestNewEntity',
            'suggested_type': 'character',
            'status': 'pending',
        }
        update_proposal = {
            'proposal_type': 'update',
            'entity_name': 'TestUpdateEntity',
            'status': 'pending',
        }

        # Simulate the NEW order (new entities first, then updates)
        state.append_to_queue([new_candidate])
        state.append_to_queue([update_proposal])

        queue = state.load_queue()
        proposals = queue['proposals']
        assert len(proposals) == 2
        assert proposals[0]['proposal_type'] == 'new_entity'
        assert proposals[1]['proposal_type'] == 'update'


@pytest.mark.skip(reason="has_meaningful_change removed during refactor")
class TestAutoSkipCounting:
    def test_counts_skipped_proposals(self):
        pass


@pytest.mark.skip(reason="references removed state functions")
class TestProposalStatusTracking:
    def test_rejected_proposal_marked(self):
        proposal = {'proposal_type': 'update', 'entity_name': 'Alice'}

        proposal['status'] = 'rejected'
        assert proposal['status'] == 'rejected'

    def test_applied_proposal_marked(self):
        proposal = {'proposal_type': 'update', 'entity_name': 'Alice'}

        proposal['status'] = 'applied'
        assert proposal['status'] == 'applied'


@pytest.mark.skip(reason="references removed state functions")
class TestNoPending:
    def test_empty_queue_prints_message(self, capsys):
        queue = []
        pending = [p for p in queue if p.get('status') == 'pending']
        assert len(pending) == 0
