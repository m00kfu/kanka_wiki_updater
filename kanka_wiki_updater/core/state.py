"""Local state: last-sync timestamp and the pending-review queue.

Everything lives in plain JSON files under DATA_DIR so it's easy to inspect,
back up, or hand-edit if something looks wrong.
"""

import json
import os
import threading
from datetime import datetime, timezone

from . import config

os.makedirs(config.DATA_DIR, exist_ok=True)

SYNC_FILE = os.path.join(config.DATA_DIR, 'sync_state.json')
QUEUE_FILE = os.path.join(config.DATA_DIR, 'pending_changes.json')
APPLIED_LOG = os.path.join(config.DATA_DIR, 'applied_log.json')
PROCESSED_FILE = os.path.join(config.DATA_DIR, 'processed_journals.json')

# Lock protecting atomic load-modify-save on pending_changes.json.
# Prevents race conditions between the sync thread and Flask web UI endpoints
# (e.g. /api/tree-state) that both read and write this file concurrently.
_queue_lock = threading.Lock()


def _load_plain(path, default):
    """Load JSON from *path* without any wrapping or migration logic.

    Returns the raw parsed value (list, dict, scalar) or *default* if the
    file is missing, empty, or corrupted.  Used for files that store bare
    lists or simple dicts (``sync_state.json``, ``applied_log.json``,
    ``processed_journals.json``).
    """
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f'[WARN] Failed to load {path}: {exc}. Starting fresh.')
        return default
    return data


def _load(path, default):
    """Load JSON from *path*, applying backward-compat wrapping for the old
    plain-array format.  Returns the new wrapped shape::

        { 'proposals': [...], '_tree_state': { 'per_tab': {...} } }

    If the file is missing, empty, or corrupted a fresh default is returned."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f'[WARN] Failed to load {path}: {exc}. Starting fresh.')
        return default

    # Backward compat: old format is a plain list → auto-wrap it.
    if isinstance(data, list):
        print(f'[MIGRATE] pending_changes.json was an array — auto-wrapping.')
        data = {'proposals': data}

    # Ensure _tree_state exists and has per-tab keys for all three tabs.
    ts = data.get('_tree_state')
    if not isinstance(ts, dict):
        print(f'[MIGRATE] pending_changes.json missing/invalid _tree_state — adding defaults.')
        ts = {'per_tab': {}}
        data['_tree_state'] = ts

    # Support legacy flat 'expanded' / 'selected_id' keys inside _tree_state.
    if 'per_tab' not in ts or not isinstance(ts.get('per_tab'), dict):
        print(f'[MIGRATE] _tree_state missing/invalid per_tab — migrating from old format.')
        legacy_expanded = ts.pop('expanded', [])
        legacy_selected_id = ts.pop('selected_id', None)
        ts['per_tab'] = {
            'new': {'expanded': legacy_expanded, 'selected_id': legacy_selected_id},
            'reviewed': {'expanded': [], 'selected_id': None},
            'sync': {'expanded': [], 'selected_id': None},
        }
    else:
        for tab in ('new', 'reviewed', 'sync'):
            if tab not in ts['per_tab'] or not isinstance(ts['per_tab'][tab], dict):
                print(f'[MIGRATE] _tree_state.per_tab.{tab} missing — adding default.')
                ts['per_tab'][tab] = {'expanded': [], 'selected_id': None}
            else:
                if 'selected_id' not in ts['per_tab'][tab]:
                    print(f'[MIGRATE] _tree_state.per_tab.{tab} missing selected_id — adding default.')
                    ts['per_tab'][tab]['selected_id'] = None

    return data


def _save(path, data):
    """Write *data* as JSON to *path*."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_last_sync():
    """Return the last-sync timestamp string, or None."""
    data = _load_plain(SYNC_FILE, {})
    if isinstance(data, dict):
        return data.get('lastSync')
    return None


def set_last_sync(value):
    _save(SYNC_FILE, {'lastSync': value})


def load_queue():
    """Return the full wrapped object: { proposals: [...], _tree_state: {...} }."""
    default = {'proposals': [], '_tree_state': {'per_tab': {}}}
    return _load(QUEUE_FILE, default)


def update_queue(modifier):
    """Atomically load the queue, call *modifier* on it, and save back.

    *modifier* is a callable that receives the data dict and mutates it in-place.
    The entire load-modify-save cycle is protected by a threading lock so that
    concurrent callers (sync thread + Flask web UI) never overwrite each other's
    changes to ``pending_changes.json``.

    Usage::

        def add_proposal(data):
            data['proposals'].append(new_proposal)

        state.update_queue(add_proposal)
    """
    with _queue_lock:
        data = load_queue()
        modifier(data)
        _save(QUEUE_FILE, data)


def save_queue(data):
    """Persist *data* to *pending_changes.json*.

    Accepts either the new wrapped format { proposals: [...], _tree_state: {...} }
    or a plain list for backward compatibility (legacy callers).
    When called with a plain list, existing _tree_state is preserved so that
    every write path keeps the file in the wrapped shape.

    NOTE: this function acquires the queue lock around its internal load+save
    to prevent lost updates from concurrent writers.
    """
    def _modifier(data):
        if isinstance(data, list):
            # Reconstruct with current tree state
            pass  # handled below in caller context

    with _queue_lock:
        current = load_queue()
        if isinstance(data, list):
            final_data = {'proposals': data, '_tree_state': current.get('_tree_state', {})}
        else:
            final_data = data
        _save(QUEUE_FILE, final_data)


def append_to_queue(items):
    """Atomically add *items* to the queue."""
    def _modifier(data):
        data['proposals'].extend(items)

    update_queue(_modifier)


def log_applied_batch(entries):
    """Record one full batch of changes applied by a single `review` run,
    tagged with a run_id, so revert.py can undo exactly 'the most recent
    review run' instead of guessing at boundaries in a flat list."""
    if not entries:
        return
    log = _load_plain(APPLIED_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append(
        {
            'run_id': datetime.now(timezone.utc).isoformat(),
            'entries': entries,
            'reverted': False,
        }
    )
    _save(APPLIED_LOG, log)


def get_last_applied_batch():
    """The most recent not-yet-reverted batch, or None if there isn't one --
    either nothing has been applied yet, the most recent run was already
    reverted, or it predates this batch-tracking format (applied with an
    older version of review.py) and so isn't recorded in enough detail to
    revert automatically."""
    log = _load_plain(APPLIED_LOG, [])
    if not isinstance(log, list):
        return None
    for item in reversed(log):
        if isinstance(item, dict) and 'entries' in item and 'run_id' in item:
            if not item.get('reverted'):
                return item
            continue  # already reverted -- keep looking further back
        return None  # hit older, less-detailed log data; nothing further back helps
    return None


def mark_batch_reverted(run_id):
    log = _load_plain(APPLIED_LOG, [])
    if not isinstance(log, list):
        return
    for item in log:
        if isinstance(item, dict) and item.get('run_id') == run_id:
            item['reverted'] = True
    _save(APPLIED_LOG, log)


def get_processed_journal_ids():
    """Journal IDs already turned into proposals, regardless of whether
    those proposals were later approved or rejected. Used so an interrupted
    or re-run sync doesn't redo (and re-queue) the same journal twice."""
    entries = _load_plain(PROCESSED_FILE, [])
    if not isinstance(entries, list):
        return set()
    return {e['id'] if isinstance(e, dict) else e for e in entries}


def mark_journal_processed(journal_id, title=None):
    entries = _load_plain(PROCESSED_FILE, [])
    if not isinstance(entries, list):
        entries = []
    existing_ids = {e['id'] if isinstance(e, dict) else e for e in entries}
    if journal_id not in existing_ids:
        entry = {'id': journal_id}
        if title:
            entry['title'] = title
        entries.append(entry)
        _save(PROCESSED_FILE, entries)
