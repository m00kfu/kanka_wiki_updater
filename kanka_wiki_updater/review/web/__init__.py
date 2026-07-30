#!/usr/bin/env python3
"""Web-based review UI for Kanka Wiki Updater.

Launch with: python -m kanka_wiki_updater.review.web

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
    from ..core import config as pkg_config
except ImportError:
    from kanka_wiki_updater.core import config as pkg_config

# Backend modules — thin routes call these for all business logic
try:
    from .. import queue_manager
    from ..sync import sync_engine, synopsis_generator, sync_events, sync_orchestrator
    from ..sync.sync_events import (
        EVENT_ENTITY_PROGRESS,
        EVENT_PROPOSAL_PUSHED,
        EVENT_STATUS_CHANGE,
        EVENT_SYNC_COMPLETE,
        EVENT_SYNC_START,
        ENTITY_STATUSES,
    )
    _start_sync = sync_orchestrator.start_sync
except ImportError:
    from kanka_wiki_updater.review import queue_manager
    from kanka_wiki_updater.sync import sync_engine, synopsis_generator, sync_events, sync_orchestrator
    from kanka_wiki_updater.sync.sync_events import (
        EVENT_ENTITY_PROGRESS,
        EVENT_PROPOSAL_PUSHED,
        EVENT_STATUS_CHANGE,
        EVENT_SYNC_COMPLETE,
        EVENT_SYNC_START,
        ENTITY_STATUSES,
    )
    _start_sync = sync_orchestrator.start_sync

# KankaClient imported at module level so tests can mock it directly.
# (sync_engine also imports it, but mocking review_web is more convenient.)
try:
    from ..core.kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater.core.kanka_client import KankaClient

_sync_lock = threading.Lock()

# Web-specific job tracking state (SSE streaming) — NOT moved to backend modules.
# Each entry has the orchestrator's base keys plus 'buffer' for SSE frames.
_sync_jobs = {}
_sync_cancel_lock = threading.Lock()  # protects _sync_jobs mutations

# Per-job cancellation flag (set when user clicks "Cancel" during a sync).
_sync_cancelled = threading.Event()

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
    """Return the entity progress dict for *job_id*, creating it if needed.

    Thin wrapper around :func:`sync_orchestrator._get_entity_progress` that
    operates on web-specific ``_sync_jobs`` state so existing callers and
    tests continue to work without changes.
    """
    job = _sync_jobs.get(job_id)
    if job is None:
        return None
    with _sync_lock:
        if 'progress' not in job:
            job['progress'] = {}
        return job['progress']


def _set_entity_status(job_id, key, status, **extra):
    """Update (or create) an entity progress entry under the lock.

    Thin wrapper around :func:`sync_orchestrator._set_entity_status` that
    operates on web-specific ``_sync_jobs`` state so existing callers and
    tests continue to work without changes.
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
        if self.job_id is None:
            return  # emitter created before start_sync returns
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


# ── App factory ─────────────────────────────────────────────────────────────


def create_app():
    """Create and configure the Flask application."""
    _here = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__,
                template_folder='templates',
                static_folder=os.path.join(_here, 'static'))

    @app.route('/')
    def index():
        data = queue_manager.load_queue()
        return render_template('index.html', PROPOSALS=data['proposals'], KANKA_CAMPAIGN_ID=pkg_config.KANKA_CAMPAIGN_ID)

    @app.route('/api/proposals')
    def get_proposals():
        data = queue_manager.load_queue()
        queue = data['proposals']
        status_filter = request.args.get('status')
        type_filter = request.args.get('type')

        if status_filter:
            queue = [p for p in queue if p.get('status') == status_filter]
        if type_filter:
            queue = [p for p in queue if p.get('proposal_type') == type_filter]

        return jsonify(queue)

    @app.route('/api/proposals/<int:index>/status', methods=['POST'])
    def update_status(index):
        data = queue_manager.load_queue()
        queue = data['proposals']
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        body = request.get_json()
        status_value = body.get('status')
        valid_statuses = ('approved_all', 'approved_synopsis_only', 'rejected')
        if status_value not in valid_statuses:
            return jsonify({'error': f'Invalid status. Must be one of {valid_statuses}'}), 400

        sync_result = None
        if status_value in ('approved_all', 'approved_synopsis_only'):
            with _sync_lock:
                client = KankaClient()
                sync_result = sync_engine.apply_proposal(client, queue[index], {})
            # Reload after potential modifications (created_local_id etc.)
            data = queue_manager.load_queue()
            queue = data['proposals']
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
        queue_manager.save_queue(data)
        result = {'proposal': queue[index], 'ok': True}
        if sync_result:
            result['sync'] = sync_result
        return jsonify(result)

    @app.route('/api/proposals/<int:index>/edit', methods=['POST'])
    def edit_proposal(index):
        data = queue_manager.load_queue()
        queue = data['proposals']
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        body = request.get_json()
        entry_text = body.get('entry', '')
        proposal = queue[index]

        queue_manager.edit_proposal_text(queue, index, entry_text, proposal['proposal_type'])
        queue_manager.save_queue(data)
        return jsonify({'proposal': queue[index], 'ok': True})

    @app.route('/api/proposals/<int:index>/relation', methods=['POST'])
    def update_relation(index):
        data = queue_manager.load_queue()
        queue = data['proposals']
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        body = request.get_json()
        action = (body.get('action') or '').strip().lower()
        target_name = body.get('target_name', '')

        if action == 'create':
            queue_manager.add_relation_change(
                queue,
                index,
                action,
                target_name,
                relation=body.get('relation', ''),
                attitude=body.get('attitude', ''),
                reason=body.get('reason', ''),
                owner_name=body.get('owner_name', ''),
            )

        elif action == 'delete':
            if not queue_manager.delete_relation_change(queue, index, target_name):
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404

        elif action == 'update':
            update_fields = {
                'relation': body.get('relation', ''),
                'attitude': body.get('attitude', ''),
                'reason': body.get('reason', ''),
            }
            new_target_name = body.get('target_name')
            if new_target_name is not None and new_target_name != target_name:
                update_fields['target_name'] = new_target_name
            new_entity_id = body.get('target_entity_id')
            if new_entity_id is not None:
                update_fields['target_entity_id'] = new_entity_id

            if queue_manager.update_relation_change(queue, index, target_name, **update_fields) is None:
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404

        else:
            return jsonify({'error': f'Invalid action: {action}'}), 400

        queue_manager.save_queue(data)
        return jsonify({'proposal': queue[index], 'ok': True})

    @app.route('/api/proposals/<int:index>/sync', methods=['POST'])
    def sync_proposal(index):
        """Sync a single proposal to Kanka.io. Used before marking as applied."""
        data = queue_manager.load_queue()
        queue = data['proposals']
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        with _sync_lock:
            client = KankaClient()
            sync_result = sync_engine.apply_proposal(client, queue[index], {})

        result = {
            'ok': sync_result['ok'],
            'message': sync_result['message'],
            'warnings': sync_result.get('warnings', []),
            'proposal': queue[index],
        }
        # Mark as applied after successful sync; caller should also update local state via /status
        queue_manager.save_queue(data)
        return jsonify(result)

    # ── Known relation types API ────────────────────────────────

    # ── Entity autocomplete API ───────────────────────────────

    @app.route('/api/entities')
    def get_entities():
        """Return all known entities (characters, locations, organisations, creatures) for datalist autocomplete."""
        client = KankaClient()
        types_map = {
            'characters': client.get_characters,
            'locations': client.get_locations,
            'organisations': client.get_organizations,
            'creatures': client.get_creatures,
        }
        entities = []
        for etype, fetch_fn in types_map.items():
            try:
                items = fetch_fn()
                for item in (items or []):
                    entities.append({'id': item.get('id'), 'name': item.get('name', '')})
            except Exception as exc:
                # Skip this type on failure — don't block others
                print(f'[ENTITIES] Skipping {etype}: {exc}', file=sys.stderr, flush=True)
        return jsonify({'entities': entities})

    @app.route('/api/known-relation-types')
    def get_known_types():
        """Return known relation types sorted by frequency."""
        tracker = queue_manager.get_tracker()
        return jsonify({
            'types': tracker.get_sorted_labels(),
            'counts': dict(tracker.known_types),
        })

    @app.route('/api/known-relation-types', methods=['POST'])
    def add_known_type():
        """Approve a new relation type."""
        data = request.get_json()
        label = (data.get('label') or '').strip()
        if not label:
            return jsonify({'error': 'No label provided'}), 400
        queue_manager.add_known_relation_type(label)
        # Reload the tracker so subsequent calls see the new type
        tracker = queue_manager.get_tracker()
        return jsonify({'ok': True, 'type': label})

    @app.route('/api/proposals/<int:index>/regenerate', methods=['POST'])
    def regenerate_proposal_route(index):
        """Re-run a truncated update or new-entity proposal through the LLM with higher token limits."""
        try:
            data = queue_manager.load_queue()
        except Exception as e:
            print(f'[REGEN] ERROR loading queue: {e}', file=sys.stderr, flush=True)
            import traceback

            traceback.print_exc(file=sys.stderr)
            return jsonify({'error': str(e)}), 500
        queue = data['proposals']
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        proposal = queue[index]
        ptype = proposal.get('proposal_type', 'update')
        if _DEBUG:
            _debug(
                'regenerate #{} type={} truncated={} entity_id={} journal_id={} source_journal={}'.format(
                    index,
                    ptype,
                    proposal.get('truncated'),
                    proposal.get('entity_id'),
                    proposal.get('_journal_id'),
                    proposal.get('source_journal'),
                )
            )

        force = request.args.get('force', '0').lower() in ('1', 'true')

        try:
            client = KankaClient()
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Failed to initialize API client: {e}'}), 500

        tracker = queue_manager.get_tracker()
        result = synopsis_generator.regenerate_proposal(
            client, proposal, force=force, relation_tracker=tracker,
        )

        if not result['ok']:
            # Map error types to HTTP status codes (mirrors original route)
            err = result['error'].lower()
            if 'no meaningful change' in err or 'llm no longer suggests' in err:
                code = 409
            elif ('lacks' in err or 'cannot fetch' in err or 'cannot contact' in err):
                code = 400
            elif 'not found' in err:
                code = 404
            else:
                code = 500
            return jsonify({'ok': False, 'error': result['error']}), code

        # Merge the new proposal into the existing queue entry.
        if ptype == 'new_entity':
            queue[index]['draft_entry'] = result['proposed_entry']
            queue[index]['reason'] = result.get('change_summary', '')
            queue[index]['uncertain'] = result.get('uncertain', [])
            queue[index]['truncated'] = result.get('truncated', False)
        else:
            queue[index]['proposed_entry'] = result['proposed_entry']
            queue[index]['change_summary'] = result.get('change_summary', '')
            queue[index]['relation_changes'] = result.get('relation_changes', [])
            queue[index]['uncertain'] = result.get('uncertain', [])
            queue[index]['truncated'] = False

        queue_manager.save_queue(data)
        return jsonify({'ok': True, 'proposal': queue[index]})

    # ── Tree state API ───────────────────────────────────────

    @app.route('/api/tree-state')
    def get_tree_state():
        """Return the full _tree_state object including per-tab expand arrays and selected IDs."""
        data = queue_manager.load_queue()
        ts = data.get('_tree_state', {})
        return jsonify({'per_tab': ts.get('per_tab', {})})

    @app.route('/api/tree-state', methods=['POST'])
    def update_tree_state():
        """Accept partial updates — only the fields present in the request body are merged."""
        data = queue_manager.load_queue()
        if '_tree_state' not in data:
            data['_tree_state'] = {'per_tab': {}}
        body = request.get_json() or {}
        # Merge per-tab state (only the tab key present)
        if 'per_tab' in body and isinstance(body['per_tab'], dict):
            ts = data['_tree_state'].setdefault('per_tab', {})
            for tab, val in body['per_tab'].items():
                if tab in ('new', 'reviewed', 'sync'):
                    if not isinstance(ts.get(tab), dict):
                        ts[tab] = {}
                    if 'expanded' in val:
                        ts[tab]['expanded'] = val['expanded']
                    if 'selected_id' in val:
                        ts[tab]['selected_id'] = val['selected_id']
        queue_manager.save_queue(data)
        return jsonify({'ok': True})

    @app.route('/api/sync/run', methods=['POST'])
    def run_sync():
        """Start a sync pipeline run in-process (no subprocess).

        Delegates to :func:`sync_orchestrator.start_sync` for job lifecycle
        management, then wraps the returned job_id with web-specific SSE
        infrastructure (output buffer).  The SSE generator at
        ``/api/sync/output`` streams typed events to connected clients.
        """
        # Reset cancellation flag for this new sync run
        _sync_cancelled.clear()

        progress = {}
        emitter = _SSECallbackEmitter(None)  # job_id set below after start_sync

        def on_entity_started(entity_name, journal_name):
            key = (journal_name, entity_name)
            with _sync_lock:
                entry = progress.get(key, {})
                entry['status'] = 'processing'
                entry['_processed'] = True
                progress[key] = entry
            emitter.entity_progress('processing', name=entity_name, journal_name=journal_name)

        def on_journal_entities_discovered(journal_name, entity_names):
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

            if isinstance(data, dict) and data.get('_no_proposal'):
                status = 'skipped'
                error_msg = None
            elif not ok:
                status = 'error'
                error_msg = (data.get('_llm_error') or str(data)) if data else 'LLM call failed'
            else:
                status = 'done'
                error_msg = None

            with _sync_lock:
                entry = progress.get(key, {})
                entry['status'] = status
                if error_msg:
                    entry['error_message'] = error_msg
                progress[key] = entry
            emitter.entity_progress(
                status,
                name=entity_name,
                journal_name=journal_name,
                error_message=error_msg,
            )

        def on_proposal_queued(proposal):
            emitter.proposal_pushed({
                'type': proposal.get('proposal_type', 'update'),
                'name': proposal.get('entity_name', ''),
                'kind': proposal.get('entity_kind', ''),
                'status': 'pending',
            })

        def on_new_entity_suggestion(suggestion):
            emitter.proposal_pushed({
                'type': suggestion.get('proposal_type', 'new_entity'),
                'name': suggestion.get('entity_name', ''),
                'kind': suggestion.get('suggested_type', ''),
                'status': 'pending',
            })

        def on_journal_completed(journal_name, entities_processed, suggestions_count):
            keys_to_done = []
            keys_to_skip = []
            with _sync_lock:
                for key in list(progress.keys()):
                    if key[0] == journal_name and progress[key]['status'] in ('pending', 'processing'):
                        if progress[key].get('_processed'):
                            # Entity went through the full pipeline — mark done.
                            progress[key]['status'] = 'done'
                            keys_to_done.append(key)
                        else:
                            # Discovered but never started processing
                            # (e.g. new-entity candidate that was skipped).
                            progress[key]['status'] = 'skipped'
                            keys_to_skip.append(key)
            for key in keys_to_done:
                emitter.entity_progress('done', name=key[1], journal_name=journal_name)
            for key in keys_to_skip:
                emitter.entity_progress('skipped', name=key[1], journal_name=journal_name)
            emitter.entity_progress(
                'journal_complete',
                journal_name=journal_name,
                entities_processed=entities_processed,
                suggestions_count=suggestions_count,
            )

        def on_sync_started(total_journals, total_entities_estimate):
            emitter.status_change('running')

        def on_sync_completed(total_proposals, total_new_entities):
            """Update _sync_jobs status when the orchestrator thread finishes."""
            with _sync_lock:
                job = _sync_jobs.get(job_id)
                if job and job['status'] == 'running':
                    job['status'] = 'completed'
                    job['finished_at'] = time.time()

        callbacks = {
            'entity_started': on_entity_started,
            'llm_result': on_llm_result,
            'proposal_queued': on_proposal_queued,
            'new_entity_suggestion': on_new_entity_suggestion,
            'journal_completed': on_journal_completed,
            'sync_started': on_sync_started,
            'journal_entities_discovered': on_journal_entities_discovered,
            'sync_completed': on_sync_completed,
        }

        # Start the orchestrator — it creates its own job state under _lock.
        job_id = _start_sync(
            callbacks=callbacks,
            cancelled_event=_sync_cancelled,
        )

        # Update emitter with real job_id and wrap with web-specific SSE buffer.
        emitter.job_id = job_id
        buffer = deque(maxlen=500)
        with _sync_lock:
            if job_id in _sync_jobs:
                _sync_jobs[job_id]['buffer'] = buffer
            else:
                _sync_jobs[job_id] = {
                    'status': 'running',
                    'started_at': time.time(),
                    'finished_at': None,
                    'progress': progress,
                    'buffer': buffer,
                }
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

        # If the job is already in a terminal state, do NOT start an SSE stream.
        # Returning a non-SSE response causes EventSource to fail the connection
        # and set readyState to CLOSED — permanently halting future reconnect
        # attempts that would otherwise create an infinite GET loop.
        if job['status'] in ('completed', 'error', 'cancelled'):
            from flask import Response

            return Response(
                'Sync job already ' + job['status'],
                status=404,
                mimetype='text/plain',
            )

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
                        yield f'event: status_change\ndata: {json.dumps({"status": job["status"]})}\n\n'
                        yield 'event: end\n\n'
                        return
                    if time.time() - idle_start > _SSE_IDLE_TIMEOUT:
                        yield 'event: end\n\n'
                        return

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
