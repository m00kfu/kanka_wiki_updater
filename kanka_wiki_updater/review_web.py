#!/usr/bin/env python3
"""Web-based review UI for Kanka Wiki Updater.

Launch with: ./kanka_wiki_updater/review_web.py or python -m kanka_wiki_updater.review_web

Serves a single-page web app at http://127.0.0.1:5555 that reads and writes
data/pending_changes.json — the same file used by review.py. Both can coexist
without conflict; they just need to agree on the JSON schema.
"""

import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

_DEBUG = bool(os.environ.get('KANKA_DEBUG'))


def _debug(*args):
    if _DEBUG:
        print('[DEBUG]', *args, file=sys.stderr)


if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template, request  # noqa: E402

try:
    from . import config as pkg_config
except ImportError:
    from kanka_wiki_updater import config as pkg_config

# Backend modules — thin routes call these for all business logic
try:
    from . import queue_manager, sync_engine
    from .sync_pipeline import build_entity_index
except ImportError:
    from kanka_wiki_updater import queue_manager, sync_engine
    from kanka_wiki_updater.sync_pipeline import build_entity_index

# KankaClient imported at module level so tests can mock it directly.
# (sync_engine also imports it, but mocking review_web is more convenient.)
try:
    from .kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater.kanka_client import KankaClient

_sync_lock = threading.Lock()

# Web-specific job tracking state (SSE streaming) — NOT moved to backend modules
_sync_jobs = {}
_job_counter = [0]
_sync_cancel_lock = threading.Lock()  # protects _sync_jobs mutations

# Per-job cancellation flag (set when user clicks "Cancel" during a sync).
_sync_cancelled = threading.Event()

# ── SSE event type constants ───────────────────────────────────────────────
EVENT_ENTITY_PROGRESS = 'entity_progress'
EVENT_PROPOSAL_PUSHED = 'proposal_pushed'
EVENT_STATUS_CHANGE = 'status_change'
EVENT_SYNC_START = 'sync_start'
EVENT_SYNC_COMPLETE = 'sync_complete'

# Accepted entity progress statuses
ENTITY_STATUSES = ('pending', 'processing', 'done', 'error')

# SSE idle timeout in seconds — if no data arrives within this window, the
# generator terminates and sends ``event: end`` so clients can reconnect.
_SSE_IDLE_TIMEOUT = int(os.environ.get('SSE_IDLE_TIMEOUT', 15))


# ── SSE helpers ─────────────────────────────────────────────────────────────


def _emit_sse(event_type, data):
    """Serialize a typed event to SSE format.

    Returns ``event: <type>\\ndata: <json>\\n\\n``.
    """
    return f'event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def _get_entity_progress(job_id):
    """Return the entity progress dict for *job_id*, creating it if needed."""
    job = _sync_jobs.get(job_id)
    if job is None:
        return None
    with _sync_lock:
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
        One of ENTITY_STATUSES.
    **extra
        Additional fields (e.g. ``error_message``, ``source_journal_url``).
    """
    if status not in ENTITY_STATUSES:
        raise ValueError(f'Invalid entity status {status!r}; must be one of {ENTITY_STATUSES}')
    progress = _get_entity_progress(job_id)
    if progress is None:
        return
    with _sync_lock:
        entry = progress.get(key, {})
        entry['name'] = key[1]
        entry['journal_name'] = key[0]
        entry['status'] = status
        for k, v in extra.items():
            if v is not None:
                entry[k] = v
        progress[key] = entry


# ── SSE callback emitter ────────────────────────────────────────────────────


class _SSECallbackEmitter:
    """Wraps per-job SSE infrastructure for structured callback emission.

    Each sync job gets its own emitter instance. The emitter appends typed
    SSE frames to the job's output buffer so the SSE generator can stream
    them to connected clients.
    """

    __slots__ = ('job_id',)

    def __init__(self, job_id):
        self.job_id = job_id

    # -- helpers -----------------------------------------------------------

    def _append(self, event_type, data):
        """Append a single SSE frame to the job's buffer (under lock)."""
        with _sync_lock:
            job = _sync_jobs.get(self.job_id)
            if job is not None and 'buffer' in job:
                frame = f'event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
                job['buffer'].append(frame)

    # -- public API --------------------------------------------------------

    def emit(self, event_type, data):
        """Emit a typed SSE event to the job's buffer."""
        self._append(event_type, data)

    def entity_progress(self, status, **extra):
        """Emit an entity_progress event with *status* and optional fields."""
        payload = {'status': status}
        payload.update(extra)
        self._append(EVENT_ENTITY_PROGRESS, payload)

    def proposal_pushed(self, data):
        """Emit a proposal_pushed event (for new or updated proposals)."""
        self._append(EVENT_PROPOSAL_PUSHED, data)

    def status_change(self, status):
        """Emit a top-level status_change event."""
        self._append(EVENT_STATUS_CHANGE, {'status': status})


# ── Job lifecycle helpers ───────────────────────────────────────────────────


def _next_job_id():
    """Generate a unique job ID."""
    _job_counter[0] += 1
    return f'sync-{_job_counter[0]}'


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)

    @app.route('/')
    def index():
        queue = queue_manager.load_queue()
        return render_template('index.html', PROPOSALS=queue, KANKA_CAMPAIGN_ID=pkg_config.KANKA_CAMPAIGN_ID)

    @app.route('/api/proposals')
    def get_proposals():
        queue = queue_manager.load_queue()
        status_filter = request.args.get('status')
        type_filter = request.args.get('type')

        if status_filter:
            queue = [p for p in queue if p.get('status') == status_filter]
        if type_filter:
            queue = [p for p in queue if p.get('proposal_type') == type_filter]

        return jsonify(queue)

    @app.route('/api/proposals/<int:index>/status', methods=['POST'])
    def update_status(index):
        queue = queue_manager.load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        status_value = data.get('status')
        valid_statuses = ('approved_all', 'approved_synopsis_only', 'rejected')
        if status_value not in valid_statuses:
            return jsonify({'error': f'Invalid status. Must be one of {valid_statuses}'}), 400

        sync_result = None
        if status_value in ('approved_all', 'approved_synopsis_only'):
            with _sync_lock:
                client = KankaClient()
                sync_result = sync_engine.apply_proposal(client, queue[index], {})
            # Reload after potential modifications (created_local_id etc.)
            queue = queue_manager.load_queue()
            proposal = queue[index]
            if not sync_result['ok']:
                return jsonify(
                    {
                        'proposal': proposal,
                        'ok': False,
                        'sync_error': True,
                        'sync_message': sync_result['message'],
                    }
                ), 409

        queue_manager.update_status(queue, index, status_value)
        queue_manager.save_queue(queue)
        result = {'proposal': queue[index], 'ok': True}
        if sync_result:
            result['sync'] = sync_result
        return jsonify(result)

    @app.route('/api/proposals/<int:index>/edit', methods=['POST'])
    def edit_proposal(index):
        queue = queue_manager.load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        entry_text = data.get('entry', '')
        proposal = queue[index]

        queue_manager.edit_proposal_text(queue, index, entry_text, proposal['proposal_type'])
        queue_manager.save_queue(queue)
        return jsonify({'proposal': queue[index], 'ok': True})

    @app.route('/api/proposals/<int:index>/relation', methods=['POST'])
    def update_relation(index):
        queue = queue_manager.load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        action = (data.get('action') or '').strip().lower()
        target_name = data.get('target_name', '')

        if action == 'create':
            queue_manager.add_relation_change(
                queue,
                index,
                action,
                target_name,
                relation=data.get('relation', ''),
                attitude=data.get('attitude', ''),
                reason=data.get('reason', ''),
            )

        elif action == 'delete':
            if not queue_manager.delete_relation_change(queue, index, target_name):
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404

        elif action == 'update':
            if (
                queue_manager.update_relation_change(
                    queue,
                    index,
                    target_name,
                    relation=data.get('relation', ''),
                    attitude=data.get('attitude', ''),
                    reason=data.get('reason', ''),
                )
                is None
            ):
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404

        else:
            return jsonify({'error': f'Invalid action: {action}'}), 400

        queue_manager.save_queue(queue)
        return jsonify({'proposal': queue[index], 'ok': True})

    def _sync_proposal_to_kanka(idx):
        """Thin wrapper that delegates to sync_engine.apply_proposal().

        Kept for backward compatibility with existing callers.
        """
        queue = queue_manager.load_queue()
        if idx >= len(queue):
            return {'ok': False, 'message': 'Proposal not found in queue', 'warnings': []}

        proposal = queue[idx]
        client = KankaClient()
        # Pass empty dict to trigger fresh index build per call (cache reset)
        return sync_engine.apply_proposal(client, proposal, {})

    @app.route('/api/proposals/<int:index>/sync', methods=['POST'])
    def sync_proposal(index):
        """Sync a single proposal to Kanka.io. Used before marking as applied."""
        queue = queue_manager.load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        with _sync_lock:
            sync_result = _sync_proposal_to_kanka(index)

        result = {
            'ok': sync_result['ok'],
            'message': sync_result['message'],
            'warnings': sync_result.get('warnings', []),
            'proposal': queue[index],
        }
        # Mark as applied after successful sync; caller should also update local state via /status
        queue_manager.save_queue(queue)
        return jsonify(result)

    @app.route('/api/proposals/<int:index>/regenerate', methods=['POST'])
    def regenerate_proposal(index):
        """Re-run a truncated update proposal through the LLM with higher token limits."""
        try:
            queue = queue_manager.load_queue()
        except Exception as e:
            print(f'[REGEN] ERROR loading queue: {e}', file=sys.stderr, flush=True)
            import traceback

            traceback.print_exc(file=sys.stderr)
            return jsonify({'error': str(e)}), 500
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        proposal = queue[index]
        if _DEBUG:
            _debug(
                'regenerate #{} type={} truncated={} entity_id={} journal_id={} source_journal={}'.format(
                    index,
                    proposal.get('proposal_type'),
                    proposal.get('truncated'),
                    proposal.get('entity_id'),
                    proposal.get('_journal_id'),
                    proposal.get('source_journal'),
                )
            )

        if proposal.get('proposal_type') != 'update':
            return jsonify({'error': 'Only update proposals can be regenerated'}), 400

        # Allow regeneration of truncated proposals, or force-regenerate via ?force=1.
        # Stale proposals without _journal_id are OK as long as source_journal is present.
        _debug('about to enter main try block')
        journal_id = proposal.get('_journal_id')
        entity_id = proposal.get('entity_id')
        if not entity_id:
            return jsonify(
                {
                    'ok': False,
                    'error': (
                        'This proposal lacks the data needed to regenerate. Re-run sync_pipeline for fresh proposals.'
                    ),
                }
            ), 400

        # Need at least _journal_id for journal lookup.
        if not journal_id:
            return jsonify(
                {
                    'ok': False,
                    'error': (
                        'This proposal lacks both _journal_id and source_journal — cannot locate the original session.'
                    ),
                }
            ), 400

        # Delegate synopsis regeneration to the shared generator.
        from kanka_wiki_updater.synopsis_generator import build_synopsis_proposal

        try:
            client = KankaClient()
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Failed to initialize API client: {e}'}), 500

        # Fetch the source journal directly via the single-journal endpoint.
        try:
            src_journal = client.get_journal(journal_id)
        except Exception as api_err:
            return jsonify(
                {
                    'ok': False,
                    'error': f'Cannot fetch journal from Kanka: {api_err}',
                }
            ), 400

        if not src_journal:
            return jsonify({'ok': False, 'error': 'Source journal not found.'}), 404

        # Helper to safely get attrs from dict or SimpleNamespace.
        def _safe_get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # Fetch fresh entity data (may have changed since original sync).
        try:
            kind_param = f'{proposal["entity_kind"]}s'
            entity_raw = getattr(client, f'get_{kind_param}')()
        except Exception as api_err:
            return jsonify(
                {
                    'ok': False,
                    'error': f'Cannot contact Kanka to fetch entities: {api_err}',
                }
            ), 400

        entity_data = next(
            (e for e in entity_raw if _safe_get(e, 'id') == proposal['entity_local_id']),
            None,
        )
        if not entity_data:
            return jsonify({'ok': False, 'error': 'Entity not found.'}), 404

        # Build the entity dict expected by build_synopsis_proposal.
        entity = {
            'name': proposal['entity_name'],
            'kind': proposal['entity_kind'],
            'entry': _safe_get(entity_data, 'entry') or '',
            'local_id': proposal['entity_local_id'],  # internal DB ID for API calls
            'entity_id': _safe_get(entity_data, 'entity_id'),  # public wiki page number for [journal:N] tags
        }

        # Use 2x max_tokens for regeneration.
        import kanka_wiki_updater.config as pkg_config

        regen_max = (
            pkg_config.LLM_MAX_TOKENS * 2 if pkg_config.LLM_PROVIDER != 'gemini' else pkg_config.GEMINI_MAX_TOKENS * 2
        )

        # Build entity index for relation resolution.
        idx = build_entity_index(client)

        result_proposal = build_synopsis_proposal(int(entity_id), entity, src_journal, idx, max_tokens=regen_max)

        force_regenerate = request.args.get('force', '0').lower() in ('1', 'true')

        # LLM connection/call error — show the real message instead of masking as "no change".
        if isinstance(result_proposal, dict) and result_proposal.get('_llm_error'):
            return jsonify(
                {
                    'ok': False,
                    'error': f'LLM call failed: {result_proposal["_llm_error"]}',
                }
            ), 500

        if result_proposal is None and not force_regenerate:
            return jsonify(
                {
                    'ok': False,
                    'error': 'LLM returned no meaningful change (identical to current).',
                }
            ), 409

        # When forcing regeneration with identical output, build a minimal proposal.
        if result_proposal is None:
            result_proposal = {
                'proposed_entry': queue[index].get('proposed_entry', entity.get('entry') or ''),
                'change_summary': '(forced regeneration - no meaningful change)',
            }

        # Merge the new proposal into the existing queue entry.
        queue[index]['proposed_entry'] = result_proposal['proposed_entry']
        queue[index]['change_summary'] = result_proposal.get('change_summary', '')
        queue[index]['relation_changes'] = []
        queue[index]['uncertain'] = result_proposal.get('uncertain', [])
        queue[index]['truncated'] = result_proposal.get('truncated', False)

        queue_manager.save_queue(queue)
        return jsonify(
            {
                'ok': True,
                'proposal': queue[index],
            }
        )

    @app.route('/api/sync/run', methods=['POST'])
    def run_sync():
        """Start a sync pipeline run in-process (no subprocess).

        Spawns a background thread that calls ``ingest_journal.run_ingest()``
        directly with SSE-emitting callbacks.  The SSE generator at
        ``/api/sync/output`` streams typed events to connected clients.
        """
        job_id = _next_job_id()

        # Reset cancellation flag for this new sync run
        _sync_cancelled.clear()

        buffer = deque(maxlen=500)
        progress = {}

        with _sync_lock:
            _sync_jobs[job_id] = {
                'status': 'running',
                'started_at': time.time(),
                'finished_at': None,
                'progress': progress,
                'buffer': buffer,
            }

        # -- Callbacks -------------------------------------------------------
        emitter = _SSECallbackEmitter(job_id)

        def on_entity_started(entity_name, journal_name):
            key = (journal_name, entity_name)
            with _sync_lock:
                entry = progress.get(key, {})
                entry['status'] = 'processing'
                progress[key] = entry
            # Emit AFTER releasing the lock to avoid deadlock —
            # emitter.entity_progress() also acquires _sync_lock internally.
            emitter.entity_progress('processing', name=entity_name, journal_name=journal_name)

        def on_journal_entities_discovered(journal_name, entity_names):
            """Fire when all entities for a journal are discovered (before LLM)."
            Registers them as 'pending' so the UI shows the full list upfront."""
            for name in entity_names:
                key = (journal_name, name)
                with _sync_lock:
                    progress[key] = {
                        'name': name,
                        'journal_name': journal_name,
                        'status': 'pending',
                    }
                emitter.entity_progress('pending', name=name, journal_name=journal_name)

        def on_llm_result(entity_name, journal_name, ok, data):
            key = (journal_name, entity_name)
            status = 'done' if ok else 'error'
            error_msg = None
            if not ok:
                error_msg = str(data) if data else 'LLM call failed'
            with _sync_lock:
                entry = progress.get(key, {})
                entry['status'] = status
                if error_msg:
                    entry['error_message'] = error_msg
                progress[key] = entry
            # Emit AFTER releasing the lock to avoid deadlock —
            # emitter.entity_progress() also acquires _sync_lock internally.
            emitter.entity_progress(
                status,
                name=entity_name,
                journal_name=journal_name,
                error_message=error_msg,
            )

        def on_proposal_queued(proposal):
            emitter.proposal_pushed(
                {
                    'type': proposal.get('proposal_type', 'update'),
                    'name': proposal.get('entity_name', ''),
                    'kind': proposal.get('entity_kind', ''),
                    'status': 'pending',
                }
            )

        def on_new_entity_suggestion(suggestion):
            emitter.proposal_pushed(
                {
                    'type': suggestion.get('proposal_type', 'new_entity'),
                    'name': suggestion.get('entity_name', ''),
                    'kind': suggestion.get('suggested_type', ''),
                    'status': 'pending',
                }
            )

        def on_journal_completed(journal_name, entities_processed, suggestions_count):
            # Collect keys to update while holding the lock, then emit events
            # AFTER releasing it — emitter.entity_progress() also acquires
            # _sync_lock internally, so doing both under the same lock would
            # deadlock on a non-reentrant Lock().
            keys_to_update = []
            with _sync_lock:
                for key in list(progress.keys()):
                    if key[0] == journal_name and progress[key]['status'] in ('pending', 'processing'):
                        progress[key]['status'] = 'done'
                        keys_to_update.append(key)
            # Emit individual completion events outside the lock
            for key in keys_to_update:
                emitter.entity_progress('done', name=key[1], journal_name=journal_name)
            emitter.entity_progress(
                'journal_complete',
                journal_name=journal_name,
                entities_processed=entities_processed,
                suggestions_count=suggestions_count,
            )

        def on_sync_started(total_journals, total_entities_estimate):
            emitter.status_change('running')

        callbacks = {
            'entity_started': on_entity_started,
            'llm_result': on_llm_result,
            'proposal_queued': on_proposal_queued,
            'new_entity_suggestion': on_new_entity_suggestion,
            'journal_completed': on_journal_completed,
            'sync_started': on_sync_started,
            'journal_entities_discovered': on_journal_entities_discovered,
        }

        # -- Background thread -----------------------------------------------
        def _ingest_thread():
            try:
                from . import config as pkg_config
                from .ingest_journal import run_ingest as ingest_run

                client = KankaClient()
                ingest_run(
                    client=client,
                    callbacks=callbacks,
                    limit=pkg_config.JOURNAL_BATCH_LIMIT,
                    cancelled_event=_sync_cancelled,
                )
                with _sync_lock:
                    if job_id in _sync_jobs and _sync_jobs[job_id]['status'] != 'cancelled':
                        _sync_jobs[job_id]['status'] = 'completed'
                        _sync_jobs[job_id]['finished_at'] = time.time()
            except Exception as e:
                import traceback

                print(f'[SYNC ERROR] {e}', file=sys.stderr, flush=True)
                traceback.print_exc(file=sys.stderr, flush=True)
                with _sync_lock:
                    if job_id in _sync_jobs and _sync_jobs[job_id]['status'] != 'cancelled':
                        _sync_jobs[job_id]['status'] = 'error'
                        _sync_jobs[job_id]['finished_at'] = time.time()

        thread = threading.Thread(target=_ingest_thread, daemon=True)
        thread.start()
        return jsonify({'job_id': job_id, 'status': 'running'})

    @app.route('/api/sync/output')
    def sync_output():
        """SSE endpoint that streams structured progress events.

        The generator drains the job's buffer and polls for new data.  If no
        frames arrive within ``_SSE_IDLE_TIMEOUT`` seconds, or if the job
        reaches a terminal state (completed / error / cancelled), the stream
        terminates with an ``event: end`` frame so clients can reconnect.
        """
        job_id = request.args.get('job_id')
        if not job_id or job_id not in _sync_jobs:
            from flask import Response

            return Response('Job not found', status=404, mimetype='text/plain')

        job = _sync_jobs[job_id]
        buffer = job['buffer']

        def generate():
            idle_start = time.time()
            seen_progress_keys = set()  # track which progress keys already emitted
            try:
                while True:
                    # Drain any buffered SSE lines from _SSECallbackEmitter.
                    # Lines are already properly formatted SSE frames
                    # ('event: <type>\ndata: {...}\n\n') — yield them directly
                    # so the frontend receives typed events (entity_progress,
                    # status_change, etc.) instead of double-wrapped message events.
                    while buffer:
                        line = buffer.popleft()
                        yield line
                        idle_start = time.time()  # reset on data

                    # Also emit any entity progress entries that haven't been sent yet.
                    # This handles cases where _set_entity_status was called directly
                    # (e.g. in tests) rather than through the emitter.
                    prog = job.get('progress', {})
                    for key, entry in list(prog.items()):
                        if key not in seen_progress_keys:
                            yield f'event: {EVENT_ENTITY_PROGRESS}\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n'
                            seen_progress_keys.add(key)
                            idle_start = time.time()

                    if job['status'] in ('completed', 'error', 'cancelled'):
                        # Drain any remaining buffered events (e.g. final
                        # entity completion or journal-complete events) before
                        # closing the stream so the frontend sees them.
                        while buffer:
                            line = buffer.popleft()
                            yield line
                        yield f'event: status\ndata: {json.dumps({"status": job["status"]})}\n\n'
                        yield 'event: end\n\n'
                        return

                    # Idle timeout — prevent forever-blocking generators.
                    if time.time() - idle_start > _SSE_IDLE_TIMEOUT:
                        break

                    time.sleep(0.2)  # poll interval
            except GeneratorExit:
                # Client disconnected — mark job as cancelled so stale connections don't serve it
                with _sync_cancel_lock:
                    if job['status'] == 'running':
                        job['status'] = 'cancelled'
                        job['finished_at'] = time.time()

        from flask import Response

        return Response(generate(), mimetype='text/event-stream')

    @app.route('/api/sync/cancel', methods=['POST'])
    def cancel_sync():
        """Cancel a running sync. Sets the cancellation flag so the ingest
        engine stops processing new journals (in-flight ones complete).
        """
        job_id = request.args.get('job_id')
        if not job_id or job_id not in _sync_jobs:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404

        with _sync_cancel_lock:
            job = _sync_jobs[job_id]
            # For in-process jobs (no subprocess attribute) just set the flag.
            if job.get('process') is None:
                _sync_cancelled.set()
            job['status'] = 'cancelled'
            job['finished_at'] = time.time()

        return jsonify({'ok': True, 'job_id': job_id})

    @app.route('/api/sync/status')
    def sync_status():
        jobs = []
        for jid, job in _sync_jobs.items():
            jobs.append(
                {
                    'job_id': jid,
                    'status': job['status'],
                    'output_lines_count': len(job['buffer']),
                    'started_at': job['started_at'],
                    'finished_at': job.get('finished_at'),
                }
            )
        return jsonify({'active': bool(jobs), 'jobs': jobs})

    return app


def main():
    """Entry point for `python -m kanka_wiki_updater.review_web`."""
    app = create_app()
    print('Starting Kanka Wiki Review UI...')
    print('Open http://127.0.0.1:5555 in your browser')
    app.run(host='127.0.0.1', port=5555, debug=False)


if __name__ == '__main__':
    main()
