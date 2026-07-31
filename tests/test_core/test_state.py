"""Tests for SQLite-backed local state persistence."""

import sqlite3
import threading

import pytest

from kanka_wiki_updater.core import db, state


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    """Point state at a fresh SQLite DB in tmp_path."""
    import kanka_wiki_updater.core.config as config
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()
    db.init_db()
    yield
    db.close_all()


# ── Missing DB → defaults ──────────────────────────────────────────────────


def test_load_queue_empty(state_db):
    result = state.load_queue()
    assert isinstance(result, dict)
    assert result['proposals'] == []
    assert 'per_tab' in result['_tree_state']


def test_get_last_sync_none(state_db):
    assert state.get_last_sync() is None


def test_get_processed_journal_ids_empty(state_db):
    result = state.get_processed_journal_ids()
    assert isinstance(result, set)
    assert len(result) == 0


def test_get_last_applied_batch_none(state_db):
    assert state.get_last_applied_batch() is None


# ── save_queue / load_queue round-trip ─────────────────────────────────────


def test_save_and_load_queue_roundtrip(state_db):
    items = [
        {'proposal_type': 'update', 'entity_name': 'Alice'},
        {'proposal_type': 'new_entity', 'entity_name': 'Bob'},
    ]
    state.save_queue(items)
    result = state.load_queue()
    assert isinstance(result, dict)
    assert result['proposals'] == items


def test_save_queue_wraps_plain_list_preserving_tree_state(state_db):
    # First write a tree_state
    wrapped = {
        'proposals': [{'id': 1}],
        '_tree_state': {'per_tab': {'new': {'expanded': ['x'], 'selected_id': None}, 'reviewed': {}, 'sync': {}}},
    }
    state.save_queue(wrapped)

    # Now write a plain list — tree_state should be preserved
    state.save_queue([{'id': 2}])
    result = state.load_queue()
    assert result['proposals'] == [{'id': 2}]
    assert 'per_tab' in result['_tree_state']


def test_save_queue_accepts_wrapped_format(state_db):
    wrapped = {
        'proposals': [{'a': 1}],
        '_tree_state': {'per_tab': {'new': {'expanded': [], 'selected_id': None}, 'reviewed': {}, 'sync': {}}},
    }
    state.save_queue(wrapped)
    result = state.load_queue()
    assert result['proposals'] == [{'a': 1}]


# ── append_to_queue ────────────────────────────────────────────────────────


def test_append_to_queue_adds_items(state_db):
    state.save_queue([{'id': 1}])
    state.append_to_queue([{'id': 2}, {'id': 3}])
    result = state.load_queue()
    assert len(result['proposals']) == 3
    assert result['proposals'][0]['id'] == 1
    assert result['proposals'][2]['id'] == 3


def test_append_to_queue_empty_list(state_db):
    state.save_queue([{'id': 1}])
    state.append_to_queue([])
    result = state.load_queue()
    assert len(result['proposals']) == 1


def test_order_preserved_after_appends(state_db):
    for i in range(5):
        state.append_to_queue([{'seq': i}])
    result = state.load_queue()
    ids = [p['seq'] for p in result['proposals']]
    assert ids == [0, 1, 2, 3, 4]


# ── update_queue modifier ──────────────────────────────────────────────────


def test_update_queue_modifier_mutates_and_persists(state_db):
    state.save_queue([{'id': 1}])

    def _remove_first(data):
        data['proposals'].pop(0)

    state.update_queue(_remove_first)
    result = state.load_queue()
    assert len(result['proposals']) == 0


def test_update_queue_empty_initial(state_db):
    def _add_one(data):
        data['proposals'].append({'new': True})

    state.update_queue(_add_one)
    result = state.load_queue()
    assert result['proposals'] == [{'new': True}]


# ── set_last_sync / get_last_sync ──────────────────────────────────────────


def test_set_and_get_last_sync(state_db):
    state.set_last_sync('2024-01-15T10:30:00')
    assert state.get_last_sync() == '2024-01-15T10:30:00'


def test_set_last_sync_overwrites(state_db):
    state.set_last_sync('first-value')
    state.set_last_sync('second-value')
    assert state.get_last_sync() == 'second-value'


# ── mark_journal_processed idempotency ─────────────────────────────────────


def test_mark_journal_processed_twice_one_row(state_db):
    state.mark_journal_processed(12345)
    state.mark_journal_processed(12345)
    result = state.get_processed_journal_ids()
    assert len(result) == 1
    assert 12345 in result


def test_mark_journal_processed_with_title(state_db):
    state.mark_journal_processed(12345, title='Session 1')
    result = state.get_processed_journal_ids()
    assert 12345 in result


# ── log_applied_batch / get_last_applied_batch / mark_batch_reverted ───────


def test_log_applied_batch_empty(state_db):
    state.log_applied_batch([])
    assert state.get_last_applied_batch() is None


def test_log_and_get_applied_batch(state_db):
    entries = [{'proposal_type': 'update', 'entity_name': 'Alice'}]
    state.log_applied_batch(entries)
    result = state.get_last_applied_batch()
    assert result is not None
    assert result['entries'] == entries
    assert 'run_id' in result
    assert result['reverted'] is False


def test_get_last_applied_batch_skips_reverted(state_db):
    state.log_applied_batch([{'id': 1}])
    first_run_id = state.get_last_applied_batch()['run_id']
    state.log_applied_batch([{'id': 2}])
    second_run_id = state.get_last_applied_batch()['run_id']
    assert second_run_id != first_run_id

    # Mark the newer one reverted — should see the older batch
    state.mark_batch_reverted(second_run_id)
    result = state.get_last_applied_batch()
    assert result is not None
    assert result['run_id'] == first_run_id


def test_no_unreverted_batch_returns_none(state_db):
    state.log_applied_batch([{'id': 1}])
    run_id = state.get_last_applied_batch()['run_id']
    state.mark_batch_reverted(run_id)
    assert state.get_last_applied_batch() is None


def test_mark_batch_reverted_multiple_batches_keeps_older(state_db):
    state.log_applied_batch([{'id': 1}])
    first_run_id = state.get_last_applied_batch()['run_id']
    state.log_applied_batch([{'id': 2}])
    second_run_id = state.get_last_applied_batch()['run_id']

    # Revert only the older batch — should still see the newer one
    state.mark_batch_reverted(first_run_id)
    result = state.get_last_applied_batch()
    assert result is not None
    assert result['run_id'] == second_run_id


# ── Concurrent writers: no lost proposals ──────────────────────────────────


def test_concurrent_writers_no_lost_proposals(state_db):
    """Two threads calling append_to_queue repeatedly — no lost items."""
    num_threads = 4
    per_thread = 50

    def _writer(thread_id):
        for i in range(per_thread):
            state.append_to_queue([{'thread': thread_id, 'seq': i}])

    threads = [threading.Thread(target=_writer, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = state.load_queue()
    assert len(result['proposals']) == num_threads * per_thread


# ── Corrupt DB → raises sqlite3.DatabaseError ──────────────────────────────


def test_corrupt_db_raises_database_error(tmp_path, monkeypatch):
    """Write garbage bytes to the db file; reopening should raise."""
    import kanka_wiki_updater.core.config as config

    db_file = str(tmp_path / 'kanka_wiki_updater.db')
    # Write garbage — not valid SQLite
    with open(db_file, 'wb') as f:
        f.write(b'garbage data that is not sqlite\x00\x01\x02')

    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()

    # Any read should raise sqlite3.DatabaseError (not silently return defaults)
    with pytest.raises((sqlite3.DatabaseError, sqlite3.OperationalError)):
        state.load_queue()


def test_corrupt_db_raises_on_get_last_sync(tmp_path, monkeypatch):
    import kanka_wiki_updater.core.config as config

    db_file = str(tmp_path / 'kanka_wiki_updater.db')
    with open(db_file, 'wb') as f:
        f.write(b'not a database\x00')

    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()

    with pytest.raises((sqlite3.DatabaseError, sqlite3.OperationalError)):
        state.get_last_sync()
