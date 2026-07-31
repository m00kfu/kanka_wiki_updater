"""Tests for local state persistence (JSON files under DATA_DIR)."""

import json

from kanka_wiki_updater.core import state


def test_load_missing_file_returns_default():
    default = {'key': 'value'}
    result = state._load('/nonexistent/path/file.json', default)
    assert result == default


def test_save_creates_file(tmp_path):
    test_file = tmp_path / 'test.json'
    state._save(str(test_file), {'key': 'value'})
    content = json.loads(test_file.read_text(encoding='utf-8'))
    assert content == {'key': 'value'}


def test_save_uses_2_space_indent(tmp_path):
    test_file = tmp_path / 'test.json'
    state._save(str(test_file), {'a': 1})
    raw = test_file.read_text(encoding='utf-8')
    assert '  "a"' in raw


def test_get_last_sync_missing(tmp_path):
    # Use a temp file to avoid pollution from real DATA_DIR
    original_file = state.SYNC_FILE
    try:
        state.SYNC_FILE = str(tmp_path / 'sync_state.json')
        result = state.get_last_sync()
        assert result is None
    finally:
        state.SYNC_FILE = original_file


def test_set_last_sync_and_get(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    sync_file = str(data_dir / 'sync_state.json')
    original_sync_file = state.SYNC_FILE
    try:
        state.SYNC_FILE = sync_file
        state.set_last_sync('2024-01-15T10:30:00')
        result = state.get_last_sync()
        assert result == '2024-01-15T10:30:00'
    finally:
        state.SYNC_FILE = original_sync_file


def test_set_last_sync_overwrites(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    sync_file = str(data_dir / 'sync_state.json')
    original_sync_file = state.SYNC_FILE
    try:
        state.SYNC_FILE = sync_file
        state.set_last_sync('first-value')
        state.set_last_sync('second-value')
        result = state.get_last_sync()
        assert result == 'second-value'
    finally:
        state.SYNC_FILE = original_sync_file


def test_load_queue_empty(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    queue_file = str(data_dir / 'pending_changes.json')
    original_queue_file = state.QUEUE_FILE
    try:
        state.QUEUE_FILE = queue_file
        result = state.load_queue()
        # load_queue returns the wrapped dict format
        assert isinstance(result, dict)
        assert result['proposals'] == []
    finally:
        state.QUEUE_FILE = original_queue_file


def test_save_and_load_queue(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    queue_file = str(data_dir / 'pending_changes.json')
    original_queue_file = state.QUEUE_FILE
    try:
        state.QUEUE_FILE = queue_file
        items = [
            {'proposal_type': 'update', 'entity_name': 'Alice'},
            {'proposal_type': 'new_entity', 'entity_name': 'Bob'},
        ]
        state.save_queue(items)
        result = state.load_queue()
        assert isinstance(result, dict)
        assert result['proposals'] == items
    finally:
        state.QUEUE_FILE = original_queue_file


def test_append_to_queue_adds_items(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    queue_file = str(data_dir / 'pending_changes.json')
    original_queue_file = state.QUEUE_FILE
    try:
        state.QUEUE_FILE = queue_file
        initial = [{'id': 1}]
        state.save_queue(initial)
        state.append_to_queue([{'id': 2}, {'id': 3}])
        result = state.load_queue()
        assert len(result['proposals']) == 3
        assert result['proposals'][0]['id'] == 1
        assert result['proposals'][2]['id'] == 3
    finally:
        state.QUEUE_FILE = original_queue_file


def test_append_to_queue_empty_list(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    queue_file = str(data_dir / 'pending_changes.json')
    original_queue_file = state.QUEUE_FILE
    try:
        state.QUEUE_FILE = queue_file
        initial = [{'id': 1}]
        state.save_queue(initial)
        state.append_to_queue([])
        result = state.load_queue()
        assert len(result['proposals']) == 1
    finally:
        state.QUEUE_FILE = original_queue_file


def test_log_applied_batch_empty():
    state.log_applied_batch([])


def test_log_applied_batch_creates_entry(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    applied_log = str(data_dir / 'applied_log.json')
    original_applied_log = state.APPLIED_LOG
    try:
        state.APPLIED_LOG = applied_log
        batch_entries = [
            {'proposal_type': 'update', 'entity_name': 'Alice'},
            {'proposal_type': 'new_entity', 'entity_name': 'Bob'},
        ]
        state.log_applied_batch(batch_entries)
        result = state.get_last_applied_batch()
        assert result is not None
        assert result['entries'] == batch_entries
        assert 'run_id' in result
        assert result['reverted'] is False
    finally:
        state.APPLIED_LOG = original_applied_log


def test_get_last_applied_batch_none_when_empty():
    log = state._load(state.APPLIED_LOG, [])
    if not log:
        assert state.get_last_applied_batch() is None


def test_mark_batch_reverted(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    applied_log = str(data_dir / 'applied_log.json')
    original_applied_log = state.APPLIED_LOG
    try:
        state.APPLIED_LOG = applied_log
        batch_entries = [{'proposal_type': 'update', 'entity_name': 'Alice'}]
        state.log_applied_batch(batch_entries)
        run_id = state.get_last_applied_batch()['run_id']
        assert state.get_last_applied_batch() is not None
        state.mark_batch_reverted(run_id)
        assert state.get_last_applied_batch() is None
    finally:
        state.APPLIED_LOG = original_applied_log


def test_get_last_applied_batch_skips_reverted(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    applied_log = str(data_dir / 'applied_log.json')
    original_applied_log = state.APPLIED_LOG
    try:
        state.APPLIED_LOG = applied_log
        state.log_applied_batch([{'id': 1}])
        first_run_id = state.get_last_applied_batch()['run_id']
        state.log_applied_batch([{'id': 2}])
        second_run_id = state.get_last_applied_batch()['run_id']
        assert second_run_id != first_run_id
        state.mark_batch_reverted(second_run_id)
        result = state.get_last_applied_batch()
        assert result is not None
        assert result['run_id'] == first_run_id
    finally:
        state.APPLIED_LOG = original_applied_log


def test_get_processed_journal_ids_empty():
    result = state.get_processed_journal_ids()
    assert isinstance(result, set)


def test_mark_journal_processed(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    processed_file = str(data_dir / 'processed_journals.json')
    original_processed_file = state.PROCESSED_FILE
    try:
        state.PROCESSED_FILE = processed_file
        state.mark_journal_processed(12345)
        result = state.get_processed_journal_ids()
        assert 12345 in result
    finally:
        state.PROCESSED_FILE = original_processed_file


def test_mark_journal_processed_with_title(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    processed_file = str(data_dir / 'processed_journals.json')
    original_processed_file = state.PROCESSED_FILE
    try:
        state.PROCESSED_FILE = processed_file
        state.mark_journal_processed(12345, title='Session 1')
        result = state.get_processed_journal_ids()
        assert 12345 in result
    finally:
        state.PROCESSED_FILE = original_processed_file


def test_mark_journal_processed_duplicate(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    processed_file = str(data_dir / 'processed_journals.json')
    original_processed_file = state.PROCESSED_FILE
    try:
        state.PROCESSED_FILE = processed_file
        state.mark_journal_processed(12345)
        state.mark_journal_processed(12345)
        result = state.get_processed_journal_ids()
        assert len(result) == 1
    finally:
        state.PROCESSED_FILE = original_processed_file


def test_get_processed_journal_ids_handles_mixed_format(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    processed_file = str(data_dir / 'processed_journals.json')
    original_processed_file = state.PROCESSED_FILE
    try:
        state.PROCESSED_FILE = processed_file
        test_data = [
            {'id': 100, 'title': 'Old format dict'},
            200,
            {'id': 300},
        ]
        with open(processed_file, 'w', encoding='utf-8') as f:
            json.dump(test_data, f)
        result = state.get_processed_journal_ids()
        assert 100 in result
        assert 200 in result
        assert 300 in result
    finally:
        state.PROCESSED_FILE = original_processed_file


def test_processed_journals_not_wrapped_by_queue_migration(tmp_path):
    """Regression: bare-array processed_journals.json must not be wrapped
    by the queue migration logic that adds _tree_state.  Without this fix,
    get_processed_journal_ids() returned dict keys instead of journal IDs."""
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    processed_file = str(data_dir / 'processed_journals.json')
    original_processed_file = state.PROCESSED_FILE
    try:
        # Write the real-world format: a bare JSON array of journal IDs.
        with open(processed_file, 'w', encoding='utf-8') as f:
            json.dump([179078], f)

        result = state.get_processed_journal_ids()
        assert isinstance(result, set), f'Expected set, got {type(result).__name__}'
        assert 179078 in result, 'Journal ID must be present in dedup set'
        # Ensure we did NOT get the queue migration keys.
        assert 'proposals' not in result
        assert '_tree_state' not in result
    finally:
        state.PROCESSED_FILE = original_processed_file
