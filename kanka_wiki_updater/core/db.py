"""SQLite-backed persistence for kanka_wiki_updater state.

Provides a thread-local connection pool, schema initialisation helpers, and
a context-manager that auto-commits on success / rolls back on exception.
"""

import os
import sqlite3
import threading

from . import config

# ---------------------------------------------------------------------------
# Schema (all tables for Phases 1-3 so no future DDL changes are needed)
# ---------------------------------------------------------------------------

SCHEMA = """\
CREATE TABLE IF NOT EXISTS proposals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    payload    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tree_state (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    state    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS applied_batches (
    run_id      TEXT PRIMARY KEY,
    entries     TEXT NOT NULL,
    reverted    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS processed_journals (
    journal_id   INTEGER PRIMARY KEY,
    title        TEXT
);

CREATE TABLE IF NOT EXISTS known_relation_types (
    label   TEXT PRIMARY KEY,
    count   INTEGER NOT NULL DEFAULT 0
);
"""


def db_path():
    """Return the absolute path to the SQLite database file."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    return os.path.join(config.DATA_DIR, 'kanka_wiki_updater.db')


# ---------------------------------------------------------------------------
# Thread-local connection pool
# ---------------------------------------------------------------------------

_local = threading.local()


def connect():
    """Return a cached *sqlite3.Connection* for the current thread.

    First call creates and configures the connection; subsequent calls in the
    same thread return the cached instance.  Use as a context manager::

        with db.connect() as conn:
            conn.execute("INSERT ...")
        # committed automatically; rolled back on exception
    """
    path = db_path()
    if not hasattr(_local, 'connections'):
        _local.connections = {}
    if path in _local.connections:
        return _local.connections[path]

    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=5000')
    _local.connections[path] = conn
    return conn


class _connection_manager:
    """Context manager that commits on success, rolls back on exception.

    Starts an explicit ``BEGIN IMMEDIATE`` transaction so that writers
    acquire a reserved lock immediately and serialize against each other,
    preventing lost updates in read-modify-write patterns (e.g. concurrent
    ``append_to_queue`` calls).
    """

    def __init__(self):
        self._conn = None

    # pylint: disable=invalid-name
    def __enter__(self):
        self._conn = connect()
        self._conn.execute('BEGIN IMMEDIATE')
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._conn.rollback()
        else:
            self._conn.commit()
        return False


def init_db():
    """Create all tables and seed the schema_version meta row."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            'INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)',
            ('schema_version', '1'),
        )


def reset_db():
    """Drop all six tables.  Used by Phase 3's ``reset_to_first``."""
    with connect() as conn:
        conn.execute('DROP TABLE IF EXISTS proposals')
        conn.execute('DROP TABLE IF EXISTS tree_state')
        conn.execute('DROP TABLE IF EXISTS meta')
        conn.execute('DROP TABLE IF EXISTS applied_batches')
        conn.execute('DROP TABLE IF EXISTS processed_journals')
        conn.execute('DROP TABLE IF EXISTS known_relation_types')


def close_all():
    """Close every cached connection (test hygiene)."""
    conns = getattr(_local, 'connections', None)
    if conns:
        from contextlib import suppress

        for c in conns.values():
            with suppress(sqlite3.ProgrammingError):
                c.close()
        _local.connections.clear()


# ---------------------------------------------------------------------------
# Convenience context manager (module-level function returning a CM)
# ---------------------------------------------------------------------------

def transaction():
    """Return a context manager that commits on success, rolls back on error."""
    return _connection_manager()
