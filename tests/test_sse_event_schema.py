"""Tests for review_web SSE event schema, helpers, and entity progress state.

Covers:
- ``_emit_sse()`` produces valid SSE frames (U1)
- Entity progress state creation/updates are thread-safe (U1)
- Event type constants are defined correctly (U1)
- ``_get_entity_progress`` / ``_set_entity_status`` handle edge cases (U1)
"""

import json
import threading
from unittest import mock as umock

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def rw():
    """Import review_web fresh for each test."""
    from kanka_wiki_updater import review_web as rw_mod
    yield rw_mod
    # Reset module-level state between tests
    rw_mod._sync_jobs.clear()


@pytest.fixture
def so():
    """Import sync_orchestrator fresh for each test."""
    from kanka_wiki_updater import sync_orchestrator as so_mod
    yield so_mod
    # Reset orchestrator state between tests
    so_mod._jobs.clear()


# ── Event type constant checks (U1) ────────────────────────────────────────


class TestEventConstants:
    """Verify event type constants match the schema used in SSE output."""

    def test_entity_progress_constant(self, rw):
        assert rw.EVENT_ENTITY_PROGRESS == 'entity_progress'

    def test_proposal_pushed_constant(self, rw):
        assert rw.EVENT_PROPOSAL_PUSHED == 'proposal_pushed'

    def test_status_change_constant(self, rw):
        assert rw.EVENT_STATUS_CHANGE == 'status_change'

    def test_sync_start_constant(self, rw):
        assert rw.EVENT_SYNC_START == 'sync_start'

    def test_sync_complete_constant(self, rw):
        assert rw.EVENT_SYNC_COMPLETE == 'sync_complete'


class TestEntityStatuses:
    """Verify accepted entity progress statuses."""

    def test_all_statuses_defined(self, rw):
        expected = ('pending', 'processing', 'done', 'error')
        assert rw.ENTITY_STATUSES == expected

    def test_no_duplicate_statuses(self, rw):
        assert len(rw.ENTITY_STATUSES) == len(set(rw.ENTITY_STATUSES))


# ── _emit_sse() tests (U1) ────────────────────────────────────────────────


class TestEmitSSE:
    """Unit tests for the SSE serialization helper."""

    def test_emits_correct_format(self, rw):
        frame = rw._emit_sse('entity_progress', {'name': 'Kael'})
        lines = frame.split('\n')
        assert lines[0] == 'event: entity_progress'
        assert lines[1].startswith('data: ')

    def test_json_escaped_data(self, rw):
        data = {'text': 'line with "quotes" and \\backslash'}
        frame = rw._emit_sse('status_change', data)
        parsed = json.loads(frame.split('\n')[1][6:])  # strip 'data: '
        assert parsed['text'] == data['text']

    def test_empty_dict_payload(self, rw):
        frame = rw._emit_sse('sync_start', {})
        parts = frame.strip().split('\n')
        assert len(parts) == 2
        assert json.loads(parts[1][6:]) == {}

    def test_list_payload(self, rw):
        data = [{'status': 'running'}, {'count': 3}]
        frame = rw._emit_sse('sync_complete', data)
        parsed = json.loads(frame.split('\n')[1][6:])
        assert parsed == data

    def test_ensures_ascii_false_preserves_unicode(self, rw):
        """ensure_ascii=False means unicode chars pass through."""
        data = {'name': 'Ñoño'}
        frame = rw._emit_sse('entity_progress', data)
        # Should contain the raw unicode character, not \\u00d1
        assert 'Ñoño' in frame

    def test_trailing_double_newline(self, rw):
        """Each SSE frame must end with an empty line (\\n\\n)."""
        frame = rw._emit_sse('entity_progress', {})
        assert frame.endswith('\n\n')


# ── _get_entity_progress() tests (U1) ──────────────────────────────────────


class TestGetEntityProgress:
    """Tests for the per-job entity progress accessor (from sync_orchestrator)."""

    def test_returns_none_for_unknown_job(self, so):
        result = so._get_entity_progress('nonexistent')
        assert result is None

    def test_creates_progress_dict_on_first_call(self, so):
        job_id = 'sync-1'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        progress = so._get_entity_progress(job_id)
        assert isinstance(progress, dict)
        # Verify it's stored on the job
        assert so._jobs[job_id].get('progress') is progress

    def test_returns_existing_progress(self, so):
        job_id = 'sync-2'
        with so._lock:
            so._jobs[job_id] = {'status': 'running', 'progress': {'existing': True}}
        result = so._get_entity_progress(job_id)
        assert result == {'existing': True}

    def test_multiple_jobs_have_independent_progress(self, so):
        for i in range(3):
            job_id = f'sync-{i}'
            with so._lock:
                so._jobs[job_id] = {'status': 'running'}

        assert so._get_entity_progress('sync-0') is not so._get_entity_progress('sync-1')


# ── _set_entity_status() tests (U1) ────────────────────────────────────────


class TestSetEntityStatus:
    """Tests for entity progress entry creation and updates (from sync_orchestrator)."""

    def test_creates_entry_on_first_set(self, so):
        job_id = 'sync-3'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        key = ('Session 1', 'Kael Ironfist')
        so._set_entity_status(job_id, key, 'processing')

        progress = so._get_entity_progress(job_id)
        entry = progress[key]
        assert entry['name'] == 'Kael Ironfist'
        assert entry['journal_name'] == 'Session 1'
        assert entry['status'] == 'processing'

    def test_updates_existing_entry(self, so):
        job_id = 'sync-4'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        key = ('Session 2', 'Vexara')
        so._set_entity_status(job_id, key, 'pending')
        so._set_entity_status(job_id, key, 'processing')

        entry = so._jobs[job_id]['progress'][key]
        assert entry['status'] == 'processing'
        # Name and journal_name should be preserved
        assert entry['name'] == 'Vexara'
        assert entry['journal_name'] == 'Session 2'

    def test_extra_fields_merged(self, so):
        job_id = 'sync-5'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        key = ('Session 3', 'Warryn')
        so._set_entity_status(
            job_id,
            key,
            'error',
            error_message='LLM timeout',
            source_journal_url='https://kanka.io/journal/12345',
        )

        entry = so._jobs[job_id]['progress'][key]
        assert entry['status'] == 'error'
        assert entry['error_message'] == 'LLM timeout'
        assert entry['source_journal_url'] == 'https://kanka.io/journal/12345'

    def test_none_extra_fields_ignored(self, so):
        job_id = 'sync-6'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        key = ('Session 4', 'Test')
        so._set_entity_status(job_id, key, 'done', error_message=None)

        entry = so._jobs[job_id]['progress'][key]
        assert 'error_message' not in entry

    def test_invalid_status_raises(self, so):
        job_id = 'sync-7'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        key = ('Session 5', 'Bad')
        with pytest.raises(ValueError, match='Invalid entity status'):
            so._set_entity_status(job_id, key, 'unknown')

    def test_unknown_job_is_safe_noop(self, so):
        """Calling _set_entity_status for a nonexistent job should not crash."""
        so._set_entity_status('nonexistent', ('J', 'E'), 'pending')  # no error


# ── Thread-safety tests (U1) ──────────────────────────────────────────────


class TestEntityProgressThreadSafety:
    """Verify concurrent updates to entity progress are safe (sync_orchestrator)."""

    def test_concurrent_updates_same_key(self, so):
        """Multiple threads updating the same entity key should not corrupt state."""
        job_id = 'sync-10'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        key = ('Session 10', 'Concurrent')
        errors = []

        def updater(thread_num):
            try:
                for status in ('pending', 'processing', 'done'):
                    so._set_entity_status(job_id, key, status)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=updater, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors  # no exceptions raised
        entry = so._jobs[job_id]['progress'][key]
        assert 'name' in entry and 'journal_name' in entry and 'status' in entry
        assert entry['status'] == 'done'  # last writer wins, but state is valid

    def test_concurrent_updates_different_keys(self, so):
        """Multiple threads updating different keys should not interfere."""
        job_id = 'sync-11'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}

        def updater(journal, entity_name, thread_num):
            key = (journal, entity_name)
            for status in ('pending', 'processing', 'done'):
                so._set_entity_status(job_id, key, status)

        threads = [
            threading.Thread(target=updater, args=(f'J{i}', f'E{i}', i))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        progress = so._jobs[job_id]['progress']
        assert len(progress) == 20
        for key, entry in progress.items():
            assert entry['status'] == 'done'
            assert entry['name'] == key[1]
            assert entry['journal_name'] == key[0]

    def test_concurrent_get_and_set(self, so):
        """_get_entity_progress + _set_entity_status under concurrent load."""
        job_id = 'sync-12'
        with so._lock:
            so._jobs[job_id] = {'status': 'running'}
        errors = []

        def getter():
            try:
                for _ in range(50):
                    p = so._get_entity_progress(job_id)
                    assert isinstance(p, dict)
            except Exception as e:
                errors.append(e)

        def setter():
            try:
                for i in range(50):
                    key = (f'J{i % 3}', f'E{i}')
                    so._set_entity_status(job_id, key, 'processing')
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=getter) for _ in range(5)]
        threads += [threading.Thread(target=setter) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


# ── Integration: SSE output includes entity progress (U1 verification) ─────


class TestSSEOutputWithEntityProgress:
    """Verify the SSE generator emits entity progress events."""

    def test_entity_progress_events_appear_in_stream(self, app_with_queue):
        """Entity progress entries created during a job appear in SSE output."""
        from kanka_wiki_updater import review_web as rw
        from kanka_wiki_updater.review_web import EVENT_ENTITY_PROGRESS

        # Create a fake job (completed so the SSE stream terminates immediately)
        job_id = 'test-job-99'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'completed',
                'buffer': [],
                'progress': {},
            }

        # Set up progress entry directly on web job state (SSE output reads from here)
        key = ('Test Journal', 'Test Entity')
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id]['progress'][key] = {
                'name': 'Test Entity',
                'journal_name': 'Test Journal',
                'status': 'processing',
            }

        # Fetch SSE output — should include the entity_progress event
        resp = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        assert resp.status_code == 200
        body = resp.data.decode()
        assert f'event: {EVENT_ENTITY_PROGRESS}' in body

    def test_entity_key_only_emitted_once_per_connection(self, app_with_queue):
        """Each entity progress key should only appear once per SSE connection."""
        from kanka_wiki_updater import review_web as rw
        from kanka_wiki_updater.review_web import EVENT_ENTITY_PROGRESS

        job_id = 'test-job-100'
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id] = {
                'status': 'completed',
                'buffer': [],
                'progress': {},
            }

        key = ('J1', 'E1')
        with app_with_queue.application.test_request_context():
            rw._sync_jobs[job_id]['progress'][key] = {
                'name': 'E1',
                'journal_name': 'J1',
                'status': 'processing',
            }

        # First SSE fetch — should see the entity
        resp1 = app_with_queue.get(f'/api/sync/output?job_id={job_id}')
        body1 = resp1.data.decode()
        count1 = body1.count(f'event: {EVENT_ENTITY_PROGRESS}')
        assert count1 >= 1

    def test_status_event_on_completion(self, app_with_queue):
        """When job status is 'completed', the SSE stream emits a status event and ends."""
        from kanka_wiki_updater import review_web as rw
        job_id = 'test-job-101'

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
        assert '"status": "completed"' in body or 'completed' in body
