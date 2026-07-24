"""Unit tests for sync_orchestrator — job lifecycle, progress tracking, and execution.

Covers:
- Job ID generation uniqueness (U1)
- start_sync creates jobs and spawns threads (U1)
- cancel_sync marks jobs as cancelled (U1)
- get_job_status returns correct info (U1)
- list_jobs returns all job summaries (U1)
- _set_entity_status updates progress under lock (U1)
- Thread safety of concurrent progress updates (U1)
"""

import threading
from unittest import mock as umock

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def so():
    """Import sync_orchestrator fresh for each test.

    Resets both job state and the counter before AND after each test
    to ensure isolation regardless of test execution order.
    """
    from kanka_wiki_updater import sync_orchestrator as so_mod
    # Reset before test
    so_mod._jobs.clear()
    so_mod._job_counter[0] = 0
    yield so_mod
    # Reset after test (cleanup)
    so_mod._jobs.clear()
    so_mod._job_counter[0] = 0


# ── Job ID generation (U1) ─────────────────────────────────────────────────


class TestJobIdGeneration:
    """Verify unique job IDs are generated."""

    def test_first_job_id(self, so):
        assert so._next_job_id() == 'sync-1'

    def test_sequential_ids(self, so):
        ids = [so._next_job_id() for _ in range(5)]
        expected = ['sync-1', 'sync-2', 'sync-3', 'sync-4', 'sync-5']
        assert ids == expected

    def test_ids_are_unique(self, so):
        ids = {so._next_job_id() for _ in range(100)}
        assert len(ids) == 100


# ── start_sync (U1) ────────────────────────────────────────────────────────


class TestStartSync:
    """Verify start_sync creates jobs and spawns threads."""

    def test_returns_job_id(self, so):
        job_id = so.start_sync(callbacks={})
        assert isinstance(job_id, str)
        assert job_id.startswith('sync-')

    def test_creates_job_entry(self, so):
        job_id = so.start_sync(callbacks={})
        with so._lock:
            job = so._jobs[job_id]
        assert job['status'] == 'running'
        assert isinstance(job['started_at'], float)
        assert job['finished_at'] is None

    def test_thread_starts_and_completes(self, so):
        """A sync that calls no callbacks should complete quickly."""
        mock_ingest = umock.patch(
            'kanka_wiki_updater.sync_orchestrator.run_ingest',
            side_effect=lambda **kw: None,
        )
        with mock_ingest:
            job_id = so.start_sync(callbacks={})
            # Keep patch alive while thread runs
            import time
            time.sleep(0.5)

        status = so.get_job_status(job_id)
        assert status is not None
        assert status['status'] == 'completed'
        assert isinstance(status['finished_at'], float)

    def test_cancelled_event_is_cleared(self, so):
        event = threading.Event()
        mock_ingest = umock.patch(
            'kanka_wiki_updater.sync_orchestrator.run_ingest',
            side_effect=lambda **kw: None,
        )
        with mock_ingest:
            job_id = so.start_sync(callbacks={}, cancelled_event=event)
            import time
            time.sleep(0.2)
        assert not event.is_set()  # orchestrator doesn't set the event

    def test_error_sets_status_to_error(self, so):
        """If run_ingest raises, status should be 'error'."""
        mock_ingest = umock.patch(
            'kanka_wiki_updater.sync_orchestrator.run_ingest',
            side_effect=RuntimeError('test failure'),
        )
        with mock_ingest:
            job_id = so.start_sync(callbacks={})
            # Keep patch alive while thread runs and raises
            import time
            time.sleep(1.0)

        status = so.get_job_status(job_id)
        assert status is not None
        assert status['status'] == 'error'


# ── cancel_sync (U1) ───────────────────────────────────────────────────────


class TestCancelSync:
    """Verify cancel_sync marks jobs as cancelled."""

    def test_cancels_running_job(self, so):
        job_id = so.start_sync(callbacks={})
        import time
        time.sleep(0.1)  # let thread start
        result = so.cancel_sync(job_id)
        assert result is True

        status = so.get_job_status(job_id)
        assert status['status'] == 'cancelled'
        assert isinstance(status['finished_at'], float)

    def test_returns_false_for_unknown_job(self, so):
        result = so.cancel_sync('nonexistent')
        assert result is False


# ── get_job_status (U1) ───────────────────────────────────────────────────


class TestGetJobStatus:
    """Verify get_job_status returns correct info."""

    def test_returns_none_for_unknown_job(self, so):
        assert so.get_job_status('unknown') is None

    def test_returns_full_info(self, so):
        job_id = so.start_sync(callbacks={})
        status = so.get_job_status(job_id)
        assert status['job_id'] == job_id
        assert status['status'] == 'running'
        assert isinstance(status['started_at'], float)
        assert status['finished_at'] is None

    def test_progress_included(self, so):
        """Progress dict should be included in the status."""
        job_id = so.start_sync(callbacks={})
        key = ('J1', 'E1')
        so._set_entity_status(job_id, key, 'processing')

        import time
        time.sleep(0.2)  # let thread finish

        status = so.get_job_status(job_id)
        assert key in status['progress']
        assert status['progress'][key]['status'] == 'processing'


# ── list_jobs (U1) ─────────────────────────────────────────────────────────


class TestListJobs:
    """Verify list_jobs returns summaries of all jobs."""

    def test_empty_list(self, so):
        jobs = so.list_jobs()
        assert jobs == []

    def test_returns_all_jobs(self, so):
        so.start_sync(callbacks={})
        so.start_sync(callbacks={})
        import time
        time.sleep(0.3)  # let threads finish

        jobs = so.list_jobs()
        assert len(jobs) == 2
        for job in jobs:
            assert 'job_id' in job
            assert 'status' in job
            assert 'started_at' in job
            assert 'finished_at' in job


# ── _set_entity_status (U1) ───────────────────────────────────────────────


class TestSetEntityStatus:
    """Verify entity progress entries are created and updated correctly."""

    def test_creates_entry(self, so):
        job_id = so.start_sync(callbacks={})
        key = ('Session 1', 'Kael')
        so._set_entity_status(job_id, key, 'processing')

        import time
        time.sleep(0.2)
        status = so.get_job_status(job_id)
        entry = status['progress'][key]
        assert entry['name'] == 'Kael'
        assert entry['journal_name'] == 'Session 1'
        assert entry['status'] == 'processing'

    def test_updates_entry(self, so):
        job_id = so.start_sync(callbacks={})
        key = ('J1', 'E1')
        so._set_entity_status(job_id, key, 'pending')
        so._set_entity_status(job_id, key, 'processing')

        import time
        time.sleep(0.2)
        status = so.get_job_status(job_id)
        assert status['progress'][key]['status'] == 'processing'

    def test_extra_fields_merged(self, so):
        job_id = so.start_sync(callbacks={})
        key = ('J1', 'E1')
        so._set_entity_status(
            job_id, key, 'error',
            error_message='timeout',
            source_journal_url='https://example.com/123',
        )

        import time
        time.sleep(0.2)
        status = so.get_job_status(job_id)
        entry = status['progress'][key]
        assert entry['error_message'] == 'timeout'
        assert entry['source_journal_url'] == 'https://example.com/123'

    def test_none_extra_fields_ignored(self, so):
        job_id = so.start_sync(callbacks={})
        key = ('J1', 'E1')
        so._set_entity_status(job_id, key, 'done', error_message=None)

        import time
        time.sleep(0.2)
        status = so.get_job_status(job_id)
        assert 'error_message' not in status['progress'][key]

    def test_invalid_status_raises(self, so):
        job_id = so.start_sync(callbacks={})
        with pytest.raises(ValueError, match='Invalid entity status'):
            so._set_entity_status(job_id, ('J', 'E'), 'bogus')

    def test_unknown_job_is_safe_noop(self, so):
        so._set_entity_status('nonexistent', ('J', 'E'), 'pending')  # no error


# ── Thread safety (U1) ────────────────────────────────────────────────────


class TestThreadSafety:
    """Verify concurrent progress updates are safe."""

    def test_concurrent_updates_same_key(self, so):
        job_id = so.start_sync(callbacks={})
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

        assert not errors
        status = so.get_job_status(job_id)
        entry = status['progress'][key]
        assert 'name' in entry and 'journal_name' in entry and 'status' in entry
        assert entry['status'] == 'done'

    def test_concurrent_updates_different_keys(self, so):
        job_id = so.start_sync(callbacks={})

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

        status = so.get_job_status(job_id)
        assert len(status['progress']) == 20
        for key, entry in status['progress'].items():
            assert entry['status'] == 'done'
            assert entry['name'] == key[1]
            assert entry['journal_name'] == key[0]

    def test_concurrent_get_and_set(self, so):
        job_id = so.start_sync(callbacks={})
        errors = []

        def getter():
            try:
                for _ in range(50):
                    p = so.get_job_status(job_id)
                    assert isinstance(p, dict) and 'progress' in p
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
