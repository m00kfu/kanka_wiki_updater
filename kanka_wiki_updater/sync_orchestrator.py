"""Sync pipeline orchestrator: job lifecycle, progress tracking, and execution.

This module owns all business logic for running the ingest pipeline in a
background thread with structured progress callbacks.  It is importable
standalone — no Flask or web dependencies.

Both the web frontend (review_web.py) and the eventual TUI frontend use this
module to start, monitor, and cancel sync runs.

Usage
-----
    from .sync_orchestrator import start_sync, cancel_sync, get_job_status

    job_id = start_sync(callbacks=callbacks)
    status = get_job_status(job_id)  # {'status': 'running', ...}
    cancel_sync(job_id)              # signal cancellation (best-effort)
"""

import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Dynamic imports for package + direct-execution compatibility
# ---------------------------------------------------------------------------

if __name__ == '__main__' and __package__ is None:
    _sys_path_0 = str(Path(__file__).resolve().parent.parent)
    if _sys_path_0 not in sys.path:
        sys.path.insert(0, _sys_path_0)

try:
    from . import config as pkg_config
    from .ingest_journal import run_ingest
except ImportError:
    from kanka_wiki_updater import config as pkg_config  # type: ignore[no-redef]
    from kanka_wiki_updater.ingest_journal import run_ingest  # type: ignore[import-not-found,assignment]

try:
    from .kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater.kanka_client import KankaClient  # type: ignore[import-not-found,assignment]

# ---------------------------------------------------------------------------
# Job state (protected by _lock)
# ---------------------------------------------------------------------------

_job_counter = [0]
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _next_job_id():
    """Generate a unique job ID like ``'sync-1'``."""
    _job_counter[0] += 1
    return f'sync-{_job_counter[0]}'


# ── Progress helpers (thread-safe) ──────────────────────────────────────────


def _get_entity_progress(job_id):
    """Return the entity progress dict for *job_id*, creating it if needed."""
    job = _jobs.get(job_id)
    if job is None:
        return None
    with _lock:
        if 'progress' not in job:
            job['progress'] = {}
        return job['progress']


def _set_entity_status(job_id, key, status, **extra):
    """Update (or create) an entity progress entry under the lock.

    Parameters
    ----------
    job_id : str
    key : tuple[str, str]
        ``(journal_name, entity_name)`` — the dict key.
    status : str
        One of ENTITY_STATUSES from sync_events.
    **extra
        Additional fields (e.g. ``error_message``, ``source_journal_url``).
    """
    # Import here to avoid circular imports at module load time
    from .sync_events import ENTITY_STATUSES

    if status not in ENTITY_STATUSES:
        raise ValueError(f'Invalid entity status {status!r}; must be one of {ENTITY_STATUSES}')
    progress = _get_entity_progress(job_id)
    if progress is None:
        return
    with _lock:
        entry = progress.get(key, {})
        entry['name'] = key[1]
        entry['journal_name'] = key[0]
        entry['status'] = status
        for k, v in extra.items():
            if v is not None:
                entry[k] = v
        progress[key] = entry


# ── Public API ──────────────────────────────────────────────────────────────


def start_sync(callbacks, cancelled_event=None, limit=None):
    """Run the ingest pipeline in a background thread.

    Parameters
    ----------
    callbacks : dict[str, callable]
        Callback map as documented in ``ingest_journal.run_ingest``.
    cancelled_event : threading.Event, optional
        If provided, this event is cleared before starting and checked
        by the ingest engine during execution.  The caller owns the event.
    limit : int, optional
        Override for ``JOURNAL_BATCH_LIMIT`` from config.

    Returns
    -------
    str
        A job ID that can be used with :func:`get_job_status` and
        :func:`cancel_sync`.
    """
    global _jobs  # module-level dict assignment not needed, but explicit

    job_id = _next_job_id()
    progress = {}

    if cancelled_event is not None:
        cancelled_event.clear()

    with _lock:
        _jobs[job_id] = {
            'status': 'running',
            'started_at': time.time(),
            'finished_at': None,
            'progress': progress,
        }

    thread = threading.Thread(
        target=_sync_thread,
        args=(job_id, callbacks, limit, cancelled_event),
        daemon=True,
    )
    thread.start()
    return job_id


def _sync_thread(job_id, callbacks, limit, cancelled_event):
    """Target function for the background sync thread."""
    try:
        client = KankaClient()
        run_ingest(
            client=client,
            callbacks=callbacks,
            limit=limit if limit is not None else pkg_config.JOURNAL_BATCH_LIMIT,
            cancelled_event=cancelled_event,
        )
        with _lock:
            job = _jobs.get(job_id)
            if job and job['status'] != 'cancelled':
                job['status'] = 'completed'
                job['finished_at'] = time.time()
    except Exception as e:
        import traceback

        print(f'[SYNC ERROR] {e}', file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        with _lock:
            job = _jobs.get(job_id)
            if job and job['status'] != 'cancelled':
                job['status'] = 'error'
                job['finished_at'] = time.time()


def cancel_sync(job_id):
    """Mark a running job as cancelled.

    This sets the status to ``'cancelled'`` immediately.  If a
    ``cancelled_event`` was passed to :func:`start_sync`, that event is
    *not* set here — the caller must handle cancellation signaling
    separately (e.g. via the web route that owns the event).

    Parameters
    ----------
    job_id : str
        The ID returned by :func:`start_sync`.

    Returns
    -------
    bool
        ``True`` if the job was found and marked cancelled, ``False`` otherwise.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return False
        job['status'] = 'cancelled'
        job['finished_at'] = time.time()
        return True


def get_job_status(job_id):
    """Return status info for a single job.

    Returns
    -------
    dict or None
        ``{'job_id', 'status', 'started_at', 'finished_at', 'progress'}``
        or ``None`` if the job does not exist.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {
            'job_id': job_id,
            'status': job['status'],
            'started_at': job['started_at'],
            'finished_at': job.get('finished_at'),
            'progress': dict(job.get('progress', {})),
        }


def list_jobs():
    """Return a summary of all jobs.

    Returns
    -------
    list[dict]
        Each entry has ``job_id``, ``status``, ``started_at``,
        ``finished_at`` (no progress data).
    """
    with _lock:
        return [
            {
                'job_id': jid,
                'status': job['status'],
                'started_at': job['started_at'],
                'finished_at': job.get('finished_at'),
            }
            for jid, job in _jobs.items()
        ]
