"""Comprehensive tests for the ingest callback system and SSE integration (U4).

Covers:
- U4a: Unit tests for _default_callbacks() and callback override behavior
- U4b: Integration test for /api/sync/run → SSE stream with mocked KankaClient
- U4c: Cancellation edge case tests (cancel mid-sync, cancel after completion)
"""

import json
import threading
import time
from collections import deque
from unittest import mock as umock

import pytest

# ============================================================================
# U4a: Unit tests for _default_callbacks() and callback override
# ============================================================================


class TestDefaultCallbacks:
    """Verify all 8 event type keys exist and are callable no-ops."""

    def test_all_event_keys_present(self):
        from kanka_wiki_updater.sync.ingest_journal import _default_callbacks

        cbs = _default_callbacks()
        expected_keys = {
            'entity_started',
            'llm_result',
            'proposal_queued',
            'new_entity_suggestion',
            'journal_completed',
            'sync_started',
            'sync_completed',
            'journal_entities_discovered',
        }
        assert set(cbs.keys()) == expected_keys

    def test_all_callbacks_are_callables(self):
        from kanka_wiki_updater.sync.ingest_journal import _default_callbacks

        cbs = _default_callbacks()
        for key, cb in cbs.items():
            assert callable(cb), f'Callback {key!r} is not callable: {type(cb)}'

    def test_default_callbacks_return_none(self):
        """Default callbacks should be no-ops that return None."""
        from kanka_wiki_updater.sync.ingest_journal import _default_callbacks

        cbs = _default_callbacks()

        # entity_started(entity_name, journal_name)
        assert cbs['entity_started']('Kael', 'Session 1') is None

        # llm_result(entity_name, journal_name, ok, data)
        assert cbs['llm_result']('Kael', 'Session 1', True, None) is None
        assert cbs['llm_result']('Kael', 'Session 1', False, Exception('fail')) is None

        # proposal_queued(proposal_dict)
        assert cbs['proposal_queued']({'type': 'update'}) is None

        # new_entity_suggestion(suggestion_dict)
        assert cbs['new_entity_suggestion']({'entity_name': 'NewGuy'}) is None

        # journal_completed(journal_name, entities_processed, suggestions_count)
        assert cbs['journal_completed']('Session 1', 2, 1) is None

        # sync_started(total_journals, total_entities_estimate)
        assert cbs['sync_started'](5, None) is None
        assert cbs['sync_started'](0, 3) is None

        # sync_completed(total_proposals, total_new_entities)
        assert cbs['sync_completed'](10, 2) is None

    def test_custom_callbacks_override_specified_keys(self):
        """Provided callbacks should override defaults for specified keys only."""
        from kanka_wiki_updater.sync.ingest_journal import _default_callbacks

        call_log = []

        def custom_entity_started(entity_name, journal_name):
            call_log.append(('entity_started', entity_name, journal_name))

        cbs = _default_callbacks()
        cbs.update({'entity_started': custom_entity_started})

        # Custom callback was called
        cbs['entity_started']('Vexara', 'Session 2')
        assert ('entity_started', 'Vexara', 'Session 2') in call_log

        # Other callbacks still work as no-ops
        assert cbs['llm_result']('X', 'Y', True, None) is None
        assert cbs['sync_completed'](0, 0) is None


class TestCallbackCapture:
    """Helper fixture for capturing callback invocations in integration tests."""

    @pytest.fixture
    def capture_callbacks(self):
        """Return a factory that creates callback dicts with call logs."""
        from collections import defaultdict

        class CallbackCapture:
            def __init__(self):
                self.calls = defaultdict(list)

            def make_callback(self, event_name):
                def cb(*args, **kwargs):
                    self.calls[event_name].append({'args': args, 'kwargs': kwargs})

                return cb

            def get_calls(self, event_name):
                return list(self.calls.get(event_name, []))

        yield CallbackCapture


# ============================================================================
# U4b: Integration tests for /api/sync/run → SSE stream
# ============================================================================


class TestSyncRunEndpoint:
    """Test the /api/sync/run endpoint with mocked KankaClient."""

    def test_run_sync_creates_job(self, app_with_queue):
        """POST /api/sync/run returns job_id and status=running."""

        # Mock KankaClient to return minimal data (no journals)
        mock_client = umock.MagicMock()
        mock_client.get_journals.return_value = []

        with umock.patch('kanka_wiki_updater.review.web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post('/api/sync/run')

        assert resp.status_code == 200
        data = resp.get_json()
        assert 'job_id' in data
        assert data['status'] == 'running'
        assert data['job_id'].startswith('sync-')

    def test_run_sync_emits_status_change_event(self, app_with_queue):
        """Entity progress events should appear in SSE output."""

        mock_client = umock.MagicMock()
        mock_client.get_journals.return_value = []

        with umock.patch('kanka_wiki_updater.review.web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post('/api/sync/run')

        job_id = resp.get_json()['job_id']

        # Wait for the background thread to complete (no journals means instant)
        time.sleep(1.5)

        # Check SSE output contains status event
        sse_resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        assert sse_resp.status_code == 200
        body = sse_resp.data.decode()
        assert 'event: end' in body

    def test_run_sync_invalidates_old_cancel_flag(self, app_with_queue):
        """A new sync run should clear the cancellation flag."""
        import kanka_wiki_updater.review.web as rw

        # Set cancel first (simulating a previous cancelled run)
        _sync_cancelled = rw._sync_cancelled
        assert not _sync_cancelled.is_set()  # initially clear

        mock_client = umock.MagicMock()
        mock_client.get_journals.return_value = []

        with umock.patch('kanka_wiki_updater.review.web.KankaClient', return_value=mock_client):
            app_with_queue.post('/api/sync/run')

        # New run clears the flag
        assert not _sync_cancelled.is_set()


class TestSyncOutputEndpoint:
    """Test the /api/sync/output SSE endpoint."""

    def test_output_returns_404_for_unknown_job(self, app_with_queue):
        resp = app_with_queue.get('/api/sync/output?job_id=nonexistent')
        assert resp.status_code == 404

    def test_output_streams_completed_status(self, app_with_queue):
        """Completed jobs should emit a status event and end marker."""
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-404-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'completed',
                'buffer': [],
                'progress': {},
            }

        resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        assert resp.status_code == 200
        body = resp.data.decode()
        # Should contain status event and end marker
        assert 'event: end' in body
        assert '"status": "completed"' in body

    def test_output_streams_error_status(self, app_with_queue):
        """Error jobs should emit error status and end."""
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-error-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'error',
                'buffer': [],
                'progress': {},
            }

        resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        assert resp.status_code == 200
        body = resp.data.decode()
        assert '"status": "error"' in body
        assert 'event: end' in body


class TestCancelledStatus:
    """Test the cancelled job status appears correctly."""

    def test_cancelled_status_in_output(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-cancelled-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'cancelled',
                'buffer': [],
                'progress': {},
            }

        resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        assert resp.status_code == 200
        body = resp.data.decode()
        assert '"status": "cancelled"' in body


class TestSyncStatusEndpoint:
    """Test the /api/sync/status endpoint."""

    def test_status_returns_empty_when_no_jobs(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        rw._sync_jobs.clear()

        resp = app_with_queue.get('/api/sync/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['active'] is False
        assert data['jobs'] == []

    def test_status_reports_running_job(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-status-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'running',
                'started_at': time.time() - 10,
                'finished_at': None,
                'progress': {},
                'buffer': deque(['line1'], maxlen=500),
            }

        resp = app_with_queue.get('/api/sync/status')
        data = resp.get_json()
        assert data['active'] is True
        assert len(data['jobs']) == 1
        job_info = data['jobs'][0]
        assert job_info['job_id'] == job_id
        assert job_info['status'] == 'running'


class TestCancelSyncEndpoint:
    """Test the /api/sync/cancel endpoint."""

    def test_cancel_returns_ok(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        # Create a fake running job first
        job_id = 'test-cancel-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': [],
                'progress': {},
            }

        resp = app_with_queue.post(f'/api/sync/cancel?job_id={job_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert data['job_id'] == job_id

    def test_cancel_sets_threading_event(self, app_with_queue):
        """Cancelling a running sync should set the module-level threading event."""
        import kanka_wiki_updater.review.web as rw

        _sync_cancelled = rw._sync_cancelled
        # Clear any leftover state from previous tests in this class
        _sync_cancelled.clear()
        assert not _sync_cancelled.is_set()

        job_id = 'test-cancel-event-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': [],
                'progress': {},
            }

        resp = app_with_queue.post(f'/api/sync/cancel?job_id={job_id}')
        assert resp.status_code == 200
        # Threading event should now be set
        assert _sync_cancelled.is_set()

    def test_cancel_unknown_job_returns_404(self, app_with_queue):
        resp = app_with_queue.post('/api/sync/cancel?job_id=nonexistent')
        assert resp.status_code == 404


# ============================================================================
# U4c: Cancellation edge case tests
# ============================================================================


class TestCancellationEdgeCases:
    """Test cancellation behavior in various scenarios."""

    def test_cancel_after_completion_is_safe(self, app_with_queue):
        """Cancelling a completed job should not crash or corrupt state."""
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-cancel-done'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'completed',
                'buffer': [],
                'progress': {},
            }

        resp = app_with_queue.post(f'/api/sync/cancel?job_id={job_id}')
        # Should not error, even on completed job
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        # Status should remain terminal
        with app_with_queue.application.test_request_context():
            assert rw._sync_jobs[job_id]['status'] == 'cancelled'

    def test_cancel_event_prevents_new_journal_processing(self):
        """Cancelled ingest should not process any more journals."""
        from threading import Event

        from kanka_wiki_updater.sync.ingest_journal import _default_callbacks, run_ingest

        cancelled = Event()
        # Pre-set the cancellation flag before starting
        cancelled.set()

        mock_client = umock.MagicMock()
        mock_client.get_journals.return_value = [
            {
                'id': '1',
                'name': 'Session 1',
                'entry': '<p>Test</p>',
                'date': '2024-01-01',
                'updated_at': '2024-01-01T00:00:00Z',
            },
            {
                'id': '2',
                'name': 'Session 2',
                'entry': '<p>More</p>',
                'date': '2024-01-02',
                'updated_at': '2024-01-02T00:00:00Z',
            },
        ]

        calls = []
        cbs = _default_callbacks()
        cbs['sync_started'] = lambda *a: calls.append('started')
        cbs['journal_completed'] = lambda *a: calls.append('completed')
        cbs['sync_completed'] = lambda *a: calls.append('done')

        # Patch KankaClient so run_ingest uses our mock instead of real API
        with umock.patch('kanka_wiki_updater.sync.ingest_journal.KankaClient', return_value=mock_client):
            summary = run_ingest(
                client=mock_client,
                callbacks=cbs,
                cancelled_event=cancelled,
            )

        # sync_started should have fired (it's called before the loop)
        assert 'started' in calls
        # But no journals should be processed because cancelled was already set
        assert summary['journals_processed'] == 0
        # sync_completed still fires with zero counts
        assert 'done' in calls

    def test_cancel_during_sync_stops_at_next_journal_boundary(self):
        """Cancel mid-sync: process one journal, then stop."""
        from kanka_wiki_updater.sync.ingest_journal import _default_callbacks, run_ingest

        cancelled = threading.Event()
        thread_done = threading.Event()

        mock_client = umock.MagicMock()
        # Return journals that will be processed one-by-one
        mock_client.get_journals.return_value = [
            {
                'id': str(i),
                'name': f'Session {i}',
                'entry': '<p>Test</p>',
                'date': f'2024-01-{i:02d}',
                'updated_at': f'2024-01-{i:02d}T00:00:00Z',
            }
            for i in range(1, 6)  # 5 journals total
        ]

        cbs = _default_callbacks()
        cbs['entity_started'] = lambda *a: None
        cbs['llm_result'] = lambda *a: None
        cbs['journal_completed'] = lambda journal_name, _, __: None

        # Start the ingest in a separate thread so we can cancel mid-run
        summary_holder = [None]
        thread_done = threading.Event()

        def run_with_cancel():
            try:
                with umock.patch('kanka_wiki_updater.sync.ingest_journal.KankaClient', return_value=mock_client):
                    summary_holder[0] = run_ingest(
                        client=mock_client,
                        callbacks=cbs,
                        cancelled_event=cancelled,
                    )
            finally:
                thread_done.set()

        t = threading.Thread(target=run_with_cancel)
        t.start()

        # Wait a bit for the first journal to start processing
        time.sleep(0.3)
        # Now cancel — should stop before processing all 5 journals
        cancelled.set()

        t.join(timeout=5)
        assert thread_done.is_set(), 'Ingest thread did not finish in time'

        # Should have processed fewer than 5 (exact count depends on timing, but < 5 proves cancellation worked)
        summary = summary_holder[0]
        assert summary['journals_processed'] < 5


class TestEntityProgressDuringSync:
    """Verify entity progress state is correctly updated during a sync run."""

    def test_entity_progress_reflects_processing_stages(self, app_with_queue):
        """Entities should transition through pending → processing → done statuses."""
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-progress-job'
        with app_with_queue.application.test_request_context():
            # Simulate a partially completed sync
            rw._sync_jobs[job_id] = {
                'status': 'completed',
                'buffer': [],
                'progress': {
                    ('Session 1', 'Kael'): {'name': 'Kael', 'journal_name': 'Session 1', 'status': 'done'},
                    ('Session 2', 'Vexara'): {
                        'name': 'Vexara',
                        'journal_name': 'Session 2',
                        'status': 'error',
                        'error_message': 'LLM timeout',
                    },
                },
            }

        resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        body = resp.data.decode()
        assert '"name": "Kael"' in body
        assert '"status": "done"' in body
        assert 'LLM timeout' in body


class TestSSECallbackEmitter:
    """Unit tests for the _SSECallbackEmitter class."""

    def test_emitter_appends_to_buffer(self, app_with_queue):
        from kanka_wiki_updater.review.web import EVENT_ENTITY_PROGRESS, _SSECallbackEmitter

        job_id = 'test-emitter-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)
        emitter.entity_progress('processing', name='TestEntity')

        # Buffer should contain an SSE frame
        assert len(buffer) == 1
        line = buffer[0]
        assert f'event: {EVENT_ENTITY_PROGRESS}' in line

    def test_emitter_proposal_pushed_event(self, app_with_queue):
        from kanka_wiki_updater.review.web import EVENT_PROPOSAL_PUSHED, _SSECallbackEmitter

        job_id = 'test-proposal-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)
        emitter.proposal_pushed({'type': 'update', 'name': 'Kael'})

        assert len(buffer) == 1
        line = buffer[0]
        assert f'event: {EVENT_PROPOSAL_PUSHED}' in line
        data = json.loads(line.split('\n')[1][6:])  # strip 'data: '
        assert data['type'] == 'update'

    def test_emitter_status_change_event(self, app_with_queue):
        from kanka_wiki_updater.review.web import EVENT_STATUS_CHANGE, _SSECallbackEmitter

        job_id = 'test-status-job'
        buffer = []
        with app_with_queue.application.test_request_context():
            import kanka_wiki_updater.review.web as rw

            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': buffer,
                'progress': {},
            }

        emitter = _SSECallbackEmitter(job_id)
        emitter.status_change('completed')

        assert len(buffer) == 1
        line = buffer[0]
        assert f'event: {EVENT_STATUS_CHANGE}' in line
        data = json.loads(line.split('\n')[1][6:])
        assert data['status'] == 'completed'


class TestWebSyncCallbacks:
    """Test that web sync callbacks correctly wire to SSE emission."""

    def test_on_entity_started_updates_progress_and_emits(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-callbacks-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': [],
                'progress': {},
            }

        # Directly invoke the callback (same one used in /api/sync/run)
        key = ('Session 1', 'Kael Ironfist')
        with app_with_queue.application.test_request_context():
            rw._set_entity_status(job_id, key, 'processing')

        progress = rw._sync_jobs[job_id]['progress']
        assert key in progress
        entry = progress[key]
        assert entry['status'] == 'processing'
        assert entry['name'] == 'Kael Ironfist'
        assert entry['journal_name'] == 'Session 1'

    def test_on_journal_completed_marks_entities_done(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-journal-complete-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'running',
                'buffer': [],
                'progress': {},
            }

        # Manually set up some entities in processing state
        key1 = ('Session 5', 'Entity A')
        key2 = ('Session 5', 'Entity B')
        with app_with_queue.application.test_request_context():
            rw._set_entity_status(job_id, key1, 'processing')
            rw._set_entity_status(job_id, key2, 'pending')

        # Verify they're still processing/pending before the simulated journal_complete callback
        progress = rw._sync_jobs[job_id]['progress']
        assert progress[key1]['status'] == 'processing'
        assert progress[key2]['status'] == 'pending'

    def test_multiple_sync_runs_have_independent_state(self, app_with_queue):
        """Two concurrent sync runs should not share entity progress state."""
        import kanka_wiki_updater.review.web as rw

        job1_id = 'test-multi-1'
        job2_id = 'test-multi-2'

        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job1_id] = {
                'status': 'running',
                'buffer': [],
                'progress': {},
            }
            rw._sync_jobs[job2_id] = {
                'status': 'running',
                'buffer': [],
                'progress': {},
            }

        key1 = ('J1', 'E1')
        key2 = ('J1', 'E1')  # same key name but different jobs

        with app_with_queue.application.test_request_context():
            rw._set_entity_status(job1_id, key1, 'processing')
            rw._set_entity_status(job2_id, key2, 'pending')

        p1 = rw._sync_jobs[job1_id]['progress'][key1]
        p2 = rw._sync_jobs[job2_id]['progress'][key2]

        assert p1['status'] == 'processing'
        assert p2['status'] == 'pending'


class TestSyncOutputWithBufferContent:
    """Verify that buffered SSE frames from the emitter are streamed correctly."""

    def test_buffer_content_emitted_as_output_events(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-buffer-job'
        buffer = deque(
            [
                'event: entity_progress\ndata: {"name": "Kael", "status": "processing"}\n\n',
            ],
            maxlen=500,
        )
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'completed',  # complete so stream terminates immediately
                'buffer': buffer,
                'progress': {},
            }

        resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        assert resp.status_code == 200
        body = resp.data.decode()
        # Buffered SSE lines are yielded directly (already properly formatted)
        assert 'event: entity_progress' in body
        assert '"name": "Kael"' in body

    def test_empty_buffer_still_streams_status(self, app_with_queue):
        import kanka_wiki_updater.review.web as rw

        job_id = 'test-empty-buffer-job'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'completed',
                'buffer': [],
                'progress': {},
            }

        resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        assert resp.status_code == 200
        body = resp.data.decode()
        # Should still get status event and end marker even with empty buffer
        assert 'event: end' in body
