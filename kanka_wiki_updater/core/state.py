"""Local state backed by a single SQLite database.

All persistence now lives in ``{DATA_DIR}/kanka_wiki_updater.db`` with six
tables: ``proposals``, ``tree_state``, ``meta``, ``applied_batches``,
``processed_journals``, and ``known_relation_types``.  Public function names
and signatures are identical to the previous JSON-based implementation so that
``ingest_journal.py``, ``cli/revert.py``, and the CLI never need changes.
"""

import json
import sqlite3
import time

from . import db

# ---------------------------------------------------------------------------
# Default shapes
# ---------------------------------------------------------------------------

_DEFAULT_TREE_STATE = {
    'per_tab': {
        'new': {'expanded': [], 'selected_id': None},
        'reviewed': {'expanded': [], 'selected_id': None},
        'sync': {'expanded': [], 'selected_id': None},
    },
}


def _default_queue():
    return {'proposals': [], '_tree_state': dict(_DEFAULT_TREE_STATE)}


# ---------------------------------------------------------------------------
# last-sync helpers (meta table)
# ---------------------------------------------------------------------------

def get_last_sync():
    """Return the last-sync timestamp string, or None."""
    with db.connect() as conn:
        row = conn.execute(
            'SELECT value FROM meta WHERE key = ?', ('lastSync',)
        ).fetchone()
        return row['value'] if row else None


def set_last_sync(value):
    """Persist a last-sync timestamp."""
    with db.transaction() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
            ('lastSync', str(value)),
        )


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------

def load_queue():
    """Return the full wrapped object::

        { 'proposals': [...], '_tree_state': {...} }
    """
    with db.connect() as conn:
        rows = conn.execute(
            'SELECT payload FROM proposals ORDER BY id'
        ).fetchall()
        proposals = [json.loads(r['payload']) for r in rows]

        row = conn.execute(
            'SELECT state FROM tree_state WHERE id = 1'
        ).fetchone()
        ts = json.loads(row['state']) if row and row['state'] else dict(_DEFAULT_TREE_STATE)

    return {'proposals': proposals, '_tree_state': ts}


def _save_proposals(conn, proposals):
    """Delete all existing proposal rows and re-insert *proposals*."""
    conn.execute('DELETE FROM proposals')
    for p in proposals:
        conn.execute(
            'INSERT INTO proposals (payload) VALUES (?)',
            (json.dumps(p, ensure_ascii=False),),
        )


def _save_tree_state(conn, tree_state):
    """Upsert the single tree_state row."""
    conn.execute('DELETE FROM tree_state WHERE id = 1')
    conn.execute(
        'INSERT INTO tree_state (id, state) VALUES (?, ?)',
        (1, json.dumps(tree_state, ensure_ascii=False)),
    )


_MAX_RETRIES = 5


def update_queue(modifier):
    """Atomically load the queue, call *modifier* on it, and save back.

    *modifier* is a callable that receives the data dict and mutates it in-place.
    The entire load-modify-save cycle runs inside one transaction so concurrent
    callers never overwrite each other's changes.  Retries on SQLite busy/locked
    errors to handle concurrent writers without lost updates.
    """
    for _attempt in range(_MAX_RETRIES):
        try:
            with db.transaction() as conn:
                # Load current state
                rows = conn.execute(
                    'SELECT payload FROM proposals ORDER BY id'
                ).fetchall()
                data = {
                    'proposals': [json.loads(r['payload']) for r in rows],
                    '_tree_state': _DEFAULT_TREE_STATE,
                }
                ts_row = conn.execute(
                    'SELECT state FROM tree_state WHERE id = 1'
                ).fetchone()
                if ts_row and ts_row['state']:
                    data['_tree_state'] = json.loads(ts_row['state'])

                # Mutate in-place
                modifier(data)

                # Persist
                _save_proposals(conn, data['proposals'])
                _save_tree_state(conn, data['_tree_state'])
            return  # success — break out of retry loop
        except sqlite3.OperationalError:
            if _attempt < _MAX_RETRIES - 1:
                time.sleep(0.05 * (2 ** _attempt))  # exponential back-off
            else:
                raise  # give up after max retries


def save_queue(data, path=None):
    """Persist *data* to the database.

    Accepts either the new wrapped format { proposals: [...], _tree_state: {...} }
    or a plain list for backward compatibility (legacy callers).
    When called with a plain list, existing _tree_state is preserved.

    NOTE: *path* is accepted for backward compatibility but ignored — all data
    goes to the SQLite database.
    """
    with db.transaction() as conn:
        # Load current tree_state if we have one
        ts_row = conn.execute(
            'SELECT state FROM tree_state WHERE id = 1'
        ).fetchone()
        current_ts = _DEFAULT_TREE_STATE
        if ts_row and ts_row['state']:
            current_ts = json.loads(ts_row['state'])

        final_data = {'proposals': data, '_tree_state': current_ts} if isinstance(data, list) else data

        _save_proposals(conn, final_data.get('proposals', []))
        _save_tree_state(conn, final_data.get('_tree_state', current_ts))


def append_to_queue(items):
    """Atomically add *items* to the queue."""
    def _modifier(data):
        data['proposals'].extend(items)

    update_queue(_modifier)


# ---------------------------------------------------------------------------
# Applied-batch log (applied_batches table)
# ---------------------------------------------------------------------------

def log_applied_batch(entries):
    """Record one full batch of changes applied by a single review run."""
    if not entries:
        return
    import datetime as _dt
    with db.transaction() as conn:
        run_id = _dt.datetime.now(_dt.timezone.utc).isoformat()
        conn.execute(
            'INSERT INTO applied_batches (run_id, entries) VALUES (?, ?)',
            (run_id, json.dumps(entries, ensure_ascii=False)),
        )


def get_last_applied_batch():
    """The most recent not-yet-reverted batch, or None."""
    with db.connect() as conn:
        rows = conn.execute(
            'SELECT run_id, entries, reverted FROM applied_batches '
            'ORDER BY created_at DESC, run_id DESC'
        ).fetchall()
        for row in rows:
            if not row['reverted']:
                return {
                    'run_id': row['run_id'],
                    'entries': json.loads(row['entries']),
                    'reverted': bool(row['reverted']),
                }
    return None


def mark_batch_reverted(run_id):
    """Mark a previously-applied batch as reverted."""
    with db.transaction() as conn:
        conn.execute(
            'UPDATE applied_batches SET reverted = 1 WHERE run_id = ?',
            (run_id,),
        )


# ---------------------------------------------------------------------------
# Processed journals (processed_journals table)
# ---------------------------------------------------------------------------

def get_processed_journal_ids():
    """Journal IDs already turned into proposals."""
    with db.connect() as conn:
        rows = conn.execute('SELECT journal_id FROM processed_journals').fetchall()
        return {int(r['journal_id']) for r in rows}


def mark_journal_processed(journal_id, title=None):
    """Mark a journal ID as processed (idempotent)."""
    with db.transaction() as conn:
        conn.execute(
            'INSERT OR IGNORE INTO processed_journals (journal_id, title) VALUES (?, ?)',
            (int(journal_id), title),
        )
