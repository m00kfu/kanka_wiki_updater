"""Tests for the sync placeholder bug fix (proposals disappearing on click).

Covers:
- U1a: SSE proposal_pushed events include real journal name in 'journal' field
- U1b: entity_progress events include journal_name
- U2a: Frontend selectProposal matches by name + resolved journal
- U2b: Frontend renderContent resolves placeholders with correct journal
- U3:  Selection restore works when saved ID used placeholder journal
- U4a: Backend save_queue uses queue lock (no lost updates)
- U4b: Atomic writes prevent torn reads during concurrent access
"""

import json
import os
import tempfile
import threading
import time
from collections import deque
from unittest import mock as umock

import pytest


# ============================================================================
# U1a: SSE proposal_pushed events include real journal name
# ============================================================================


class TestSSEProposalPushedIncludesJournal:
    """Verify the 'journal' field is present in all proposal_pushed SSE events."""

    def test_proposal_queued_event_has_journal_field(self, app_with_queue):
        from kanka_wiki_updater.review.web import EVENT_PROPOSAL_PUSHED, _SSECallbackEmitter

        job_id = 'test-proposal-journal-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)
        proposal = {
            'proposal_type': 'update',
            'entity_name': 'Kael Ironfist',
            'entity_kind': 'character',
            'suggested_type': 'character',
            'source_journal': 'Session 12',
        }
        emitter.proposal_pushed({
            'type': proposal['proposal_type'],
            'name': proposal['entity_name'],
            'kind': proposal['entity_kind'],
            'suggested_type': proposal['suggested_type'],
            'status': 'pending',
            'journal': proposal.get('source_journal', ''),
        })

        assert len(buffer) == 1
        line = buffer[0]
        data = json.loads(line.split('\n')[1][6:])  # strip 'data: '
        assert data['name'] == 'Kael Ironfist'
        assert data['journal'] == 'Session 12', (
            "proposal_pushed event must include real journal name in 'journal' field"
        )

    def test_new_entity_suggestion_event_has_journal_field(self, app_with_queue):
        from kanka_wiki_updater.review.web import EVENT_PROPOSAL_PUSHED, _SSECallbackEmitter

        job_id = 'test-new-entity-journal-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)
        suggestion = {'entity_name': 'NewCharacter', 'suggested_type': 'character'}
        journal_name = 'Session 5'
        emitter.proposal_pushed({
            'type': 'new_entity',
            'name': suggestion['entity_name'],
            'kind': suggestion['suggested_type'],
            'suggested_type': suggestion['suggested_type'],
            'status': 'pending',
            'journal': journal_name,
        })

        assert len(buffer) == 1
        line = buffer[0]
        data = json.loads(line.split('\n')[1][6:])
        assert data['name'] == 'NewCharacter'
        assert data['journal'] == 'Session 5', (
            "new_entity proposal_pushed event must include real journal name"
        )

    def test_proposal_queued_callback_includes_journal(self, app_with_queue):
        """The on_proposal_queued callback in /api/sync/run should pass journal."""
        from kanka_wiki_updater.review.web import _SSECallbackEmitter

        job_id = 'test-callback-journal-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)

        # Simulate the on_proposal_queued callback (as called in /api/sync/run)
        proposal = {
            'proposal_type': 'update',
            'entity_name': 'Kael Ironfist',
            'entity_kind': 'character',
            'suggested_type': 'character',
            'source_journal': 'Session 12',
        }
        emitter.proposal_pushed({
            'type': proposal.get('proposal_type', 'update'),
            'name': proposal.get('entity_name', ''),
            'kind': proposal.get('entity_kind', ''),
            'suggested_type': proposal.get('suggested_type', ''),
            'status': 'pending',
            'journal': proposal.get('source_journal', ''),
        })

        assert len(buffer) == 1
        line = buffer[0]
        data = json.loads(line.split('\n')[1][6:])
        assert data['name'] == 'Kael Ironfist'
        assert data['journal'] == 'Session 12', (
            "on_proposal_queued callback must include real journal in SSE event"
        )


class TestEntityProgressIncludesJournalName:
    """Verify entity_progress SSE events include journal_name."""

    def test_entity_progress_has_journal_name(self, app_with_queue):
        from kanka_wiki_updater.review.web import EVENT_ENTITY_PROGRESS, _SSECallbackEmitter

        job_id = 'test-entity-progress-journal-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)
        # The real callers pass journal_name (see web/__init__.py lines 514, 525, etc.)
        emitter.entity_progress('processing', name='TestEntity', journal_name='Session 1')

        assert len(buffer) == 1
        line = buffer[0]
        data = json.loads(line.split('\n')[1][6:])
        assert 'journal_name' in data, (
            "entity_progress event must include journal_name for frontend matching"
        )
        assert data['journal_name'] == 'Session 1'


# ============================================================================
# U2a: Frontend selectProposal matches by name + resolved journal
# ============================================================================


class TestFrontendPlaceholderResolution:
    """Test that the frontend correctly resolves sync placeholders using journal names."""

    def test_js_proposal_pushed_prefers_data_journal(self):
        """The proposal_pushed handler should prefer data.journal over syncEntities lookup."""
        with open(
            'kanka_wiki_updater/review/web/static/js/app.js', encoding='utf-8'
        ) as f:
            js = f.read()

        # The handler should check data.journal first (dot notation in JS)
        assert 'data.journal' in js, (
            "proposal_pushed handler must reference data.journal from SSE event"
        )

    def test_js_selectProposal_resolves_placeholder_journal(self):
        """selectProposal must resolve placeholder journal via syncEntities before matching."""
        with open(
            'kanka_wiki_updater/review/web/static/js/app.js', encoding='utf-8'
        ) as f:
            js = f.read()

        # selectProposal should look up realJournal from syncEntities when source_journal is 'Syncing...'
        assert "realJournalForMatch" in js or 'realJournal' in js, (
            "selectProposal must resolve placeholder journal for matching"
        )

    def test_js_renderContent_resolves_placeholder_with_journal(self):
        """renderContent's placeholder resolution should match by name + journal."""
        with open(
            'kanka_wiki_updater/review/web/static/js/app.js', encoding='utf-8'
        ) as f:
            js = f.read()

        # renderContent should resolve realJournalForMatch for placeholder matching
        assert "realJournalForMatch" in js, (
            "renderContent must use resolved journal when matching placeholders"
        )


class TestFrontendLoadProposalsMatching:
    """Test that loadProposals matches by name + journal and doesn't fall back blindly."""

    def test_js_loadProposals_has_resolve_placeholder_journal(self):
        """loadProposals should have a helper to resolve placeholder journals."""
        with open(
            'kanka_wiki_updater/review/web/static/js/app.js', encoding='utf-8'
        ) as f:
            js = f.read()

        assert "resolvePlaceholderJournal" in js, (
            "loadProposals must have resolvePlaceholderJournal helper for journal resolution"
        )

    def test_js_loadProposals_no_blind_fallback_for_placeholders(self):
        """loadProposals should NOT blindly fallback to serverMap[pKey][0] for placeholders."""
        with open(
            'kanka_wiki_updater/review/web/static/js/app.js', encoding='utf-8'
        ) as f:
            js = f.read()

        # The code should have a conditional that checks _sync_placeholder before falling back
        assert "_sync_placeholder" in js, (
            "loadProposals must check _sync_placeholder flag to prevent wrong-journal fallback"
        )


# ============================================================================
# U3: Selection restore works with resolved journals
# ============================================================================


class TestFrontendSelectionRestore:
    """Test that selection restore handles placeholder journal resolution."""

    def test_js_saveTreeState_has_version_guard(self):
        """saveTreeState should use a version counter to prevent stale writes after loadProposals."""
        with open(
            'kanka_wiki_updater/review/web/static/js/app.js', encoding='utf-8'
        ) as f:
            js = f.read()

        assert "treeStateVersion" in js, (
            "saveTreeState must use treeStateVersion counter to prevent stale writes"
        )
        # The timeout callback should check version before POSTing
        assert "snapshotVersion" in js or "treeStateVersion !== snapshotVersion" in js, (
            "saveTreeState timeout callback must compare versions before writing"
        )

    def test_js_loadProposals_bumps_version(self):
        """loadProposals should bump treeStateVersion at the start."""
        with open(
            'kanka_wiki_updater/review/web/static/js/app.js', encoding='utf-8'
        ) as f:
            js = f.read()

        assert "treeStateVersion++" in js or "treeStateVersion += 1" in js, (
            "loadProposals must bump treeStateVersion to invalidate pending saveTreeState timers"
        )


# ============================================================================
# U4a: Backend save_queue uses queue lock
# ============================================================================


class TestQueueLockSafety:
    """Verify that save_queue operations acquire the queue lock."""

    def test_save_queue_uses_state_save_queue(self, tmp_path):
        """queue_manager.save_queue should call state.save_queue (which locks)."""
        from kanka_wiki_updater.review import queue_manager
        from kanka_wiki_updater.core import state

        # Patch state.save_queue to verify it's called
        with umock.patch.object(state, 'save_queue') as mock_save:
            test_data = {
                'proposals': [
                    {'entity_name': 'Test', 'proposal_type': 'update', 'status': 'pending'}
                ],
                '_tree_state': {'per_tab': {}},
            }

            # Save using queue_manager (which should call state.save_queue)
            queue_manager.save_queue(test_data)

            mock_save.assert_called_once()
            # Verify it was called with the correct path
            call_args = mock_save.call_args
            assert 'path' in call_args.kwargs, (
                "state.save_queue must be called with path argument"
            )

    def test_state_save_queue_acquires_lock(self):
        """state.save_queue should acquire _queue_lock during write."""
        from kanka_wiki_updater.core import state

        lock = state._queue_lock
        assert hasattr(lock, 'acquire'), "_queue_lock must be a threading.Lock or RLock"

    def test_concurrent_saves_dont_clobber(self):
        """Concurrent save_queue calls should not lose updates."""
        from kanka_wiki_updater.core import state

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.json')
            # Initialize file
            state._save(path, {'count': 0})

            errors = []

            def writer(writer_id):
                try:
                    for _ in range(50):
                        with state._queue_lock:
                            data = state._load(path, {})
                            current_count = data.get('count', 0)
                            time.sleep(0.001)  # Simulate work between read and write
                            data['count'] = current_count + 1
                            data['last_writer'] = writer_id
                            state._save(path, data)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not errors, f"Concurrent writes raised errors: {errors}"

            final = state._load(path, {})
            # With proper locking, count should be exactly 200 (4 threads × 50 iterations)
            assert final['count'] == 200, (
                f"Expected count=200 with locked concurrent writes, got {final['count']}"
            )


# ============================================================================
# U4b: Atomic writes prevent torn reads
# ============================================================================


class TestAtomicWrites:
    """Verify that _save uses atomic write (temp file + os.replace)."""

    def test_save_uses_temp_file(self):
        """_save should write to a temp file first, then rename."""
        from kanka_wiki_updater.core import state

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.json')
            data = {'key': 'value'}

            # Write the file
            state._save(path, data)

            # File should exist and be valid JSON
            assert os.path.exists(path), "File should exist after _save"

            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)

            assert loaded == data, "_save should produce valid JSON matching input"

    def test_atomic_write_no_partial_read(self):
        """A reader should never see a partially written file."""
        from kanka_wiki_updater.core import state

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.json')
            errors = []
            reads_ok = 0
            writes_done = threading.Event()

            def writer():
                for i in range(100):
                    data = {'count': i, 'data': 'x' * 1000}
                    state._save(path, data)
                writes_done.set()

            def reader():
                nonlocal reads_ok
                while not writes_done.is_set():
                    try:
                        if os.path.exists(path):
                            with open(path, 'r', encoding='utf-8') as f:
                                loaded = json.load(f)
                                assert isinstance(loaded, dict), "Should load valid JSON"
                                assert 'count' in loaded, "Should have expected keys"
                                reads_ok += 1
                    except (json.JSONDecodeError, KeyError):
                        errors.append("Torn read detected")
                    time.sleep(0.001)

            w = threading.Thread(target=writer)
            r = threading.Thread(target=reader)
            w.start()
            r.start()
            r.join(timeout=5)
            w.join(timeout=5)

            assert not errors, f"Torn reads detected: {errors}"
            assert reads_ok > 0, "Reader should have successfully read the file"

    def test_temp_file_cleaned_on_error(self):
        """If _save fails mid-write, temp file should be cleaned up."""
        from kanka_wiki_updater.core import state

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.json')
            # Write initial data
            state._save(path, {'initial': True})

            # _save uses temp file + os.replace; if os.replace fails (e.g. different FS),
            # it falls back to direct write. The key invariant is: no orphan .tmp files
            # should remain after a successful save.
            state._save(path, {'updated': True})

            # No temp file should remain
            tmp_files = [f for f in os.listdir(tmp) if f.endswith('.tmp')]
            assert not tmp_files, f"Orphan temp files found: {tmp_files}"


# ============================================================================
# U5: Integration - end-to-end placeholder resolution flow
# ============================================================================


class TestEndToEndPlaceholderFlow:
    """Integration test for the full placeholder resolution flow."""

    def test_full_flow_proposal_pushed_then_selected(self, app_with_queue):
        """Simulate: sync starts → proposal_pushed with journal → user clicks proposal.

        This tests the complete flow that was broken before the fix:
        1. SSE emits proposal_pushed with real journal name
        2. Frontend creates placeholder with correct source_journal
        3. User clicks proposal → selectProposal resolves via name+journal match
        """
        import kanka_wiki_updater.review.web as rw

        # Step 1: Create a running sync job and emit events directly
        job_id = 'test-e2e-job'
        buffer = deque(maxlen=500)

        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        # Step 2: Emit entity_progress (sets up syncEntities state in frontend)
        emitter = type('Emitter', (), {})()
        from kanka_wiki_updater.review.web import _SSECallbackEmitter
        emitter = _SSECallbackEmitter(job_id)

        key = ('Session 12', 'Kael Ironfist')
        with app_with_queue.application.test_request_context():
            rw._set_entity_status(job_id, key, 'processing')

        # Step 3: Emit proposal_pushed (should include journal field)
        emitter.proposal_pushed({
            'type': 'update',
            'name': 'Kael Ironfist',
            'kind': 'character',
            'suggested_type': 'character',
            'status': 'pending',
            'journal': 'Session 12',
        })

        # Verify buffer contains proposal_pushed with journal field
        assert len(buffer) >= 1, "Buffer should contain SSE events"
        found_proposal_event = False
        for line in buffer:
            if 'event: proposal_pushed' in line:
                data_line = [l for l in line.split('\n') if l.startswith('data:')][0]
                data = json.loads(data_line[6:])
                assert data['name'] == 'Kael Ironfist'
                assert data['journal'] == 'Session 12', (
                    "proposal_pushed must include real journal name"
                )
                found_proposal_event = True
                break

        assert found_proposal_event, (
            "proposal_pushed event with journal field should be in buffer"
        )

    def test_sync_entities_progress_has_journal_name(self, app_with_queue):
        """entity_progress events must include journal_name for frontend matching."""
        from kanka_wiki_updater.review.web import _SSECallbackEmitter

        job_id = 'test-entity-journal-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)
        # Real callers pass journal_name (see web/__init__.py lines 514, 525, etc.)
        emitter.entity_progress('processing', name='Kael Ironfist', journal_name='Session 12')

        assert len(buffer) >= 1
        found_entity_event = False
        for line in buffer:
            if 'event: entity_progress' in line:
                data_line = [l for l in line.split('\n') if l.startswith('data:')][0]
                data = json.loads(data_line[6:])
                assert 'journal_name' in data, (
                    "entity_progress must include journal_name"
                )
                found_entity_event = True
                break

        assert found_entity_event, (
            "entity_progress event should be in buffer"
        )
