#!/usr/bin/env python3
"""Web-based review UI for Kanka Wiki Updater.

Launch with: ./kanka_wiki_updater/review_web.py or python -m kanka_wiki_updater.review_web

Serves a single-page web app at http://127.0.0.1:5555 that reads and writes
data/pending_changes.json — the same file used by review.py. Both can coexist
without conflict; they just need to agree on the JSON schema.
"""

import json
import os
import subprocess
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
                queue, index, action, target_name,
                relation=data.get('relation', ''),
                attitude=data.get('attitude', ''),
                reason=data.get('reason', ''),
            )

        elif action == 'delete':
            if not queue_manager.delete_relation_change(queue, index, target_name):
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404

        elif action == 'update':
            if queue_manager.update_relation_change(
                    queue, index, target_name,
                    relation=data.get('relation', ''),
                    attitude=data.get('attitude', ''),
                    reason=data.get('reason', ''),
            ) is None:
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
        job_id = _next_job_id()
        module_dir = str(Path(pkg_config.DATA_DIR).parent)
        proc = subprocess.Popen(
            [sys.executable, '-m', 'kanka_wiki_updater.sync_pipeline'],
            cwd=module_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        buffer = deque(maxlen=500)
        _sync_jobs[job_id] = {
            'process': proc,
            'buffer': buffer,
            'status': 'running',
            'started_at': time.time(),
            'finished_at': None,
        }
        thread = threading.Thread(target=_sync_thread, args=(job_id, proc, buffer), daemon=True)
        thread.start()
        return jsonify({'job_id': job_id, 'status': 'running'})

    @app.route('/api/sync/output')
    def sync_output():
        job_id = request.args.get('job_id')
        if not job_id or job_id not in _sync_jobs:
            from flask import Response

            return Response('Job not found', status=404, mimetype='text/plain')

        job = _sync_jobs[job_id]
        buffer = job['buffer']

        def generate():
            try:
                while True:
                    # Drain any buffered lines first
                    while buffer:
                        line = buffer.popleft()
                        yield f'event: message\ndata: {json.dumps({"type": "output", "text": line})}\n\n'

                    if job['status'] in ('completed', 'error', 'cancelled'):
                        yield f'event: status\ndata: {json.dumps({"status": job["status"]})}\n\n'
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
        job_id = request.args.get('job_id')
        if not job_id or job_id not in _sync_jobs:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404

        with _sync_cancel_lock:
            job = _sync_jobs[job_id]
            if job['process'] and job['process'].poll() is None:
                try:
                    job['process'].terminate()
                    job['process'].wait(timeout=3)
                except Exception:
                    try:
                        job['process'].kill()
                        job['process'].wait(timeout=5)
                    except Exception:
                        pass
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


def _sync_thread(job_id, proc, buffer):
    """Background thread that reads subprocess stdout and pushes to deque."""
    try:
        for line in proc.stdout:
            with _sync_cancel_lock:
                if job_id in _sync_jobs and _sync_jobs[job_id]['status'] == 'cancelled':
                    break
            buffer.append(line.rstrip('\n'))
        proc.wait()
        with _sync_cancel_lock:
            if job_id not in _sync_jobs or _sync_jobs[job_id]['status'] == 'cancelled':
                return
            if proc.returncode == 0:
                _sync_jobs[job_id]['status'] = 'completed'
            else:
                _sync_jobs[job_id]['status'] = 'error'
    except Exception as e:
        with _sync_cancel_lock:
            if job_id in _sync_jobs and _sync_jobs[job_id]['status'] != 'cancelled':
                buffer.append(f'Sync error: {e}')
                _sync_jobs[job_id]['status'] = 'error'





def main():
    """Entry point for `python -m kanka_wiki_updater.review_web`."""
    app = create_app()
    print('Starting Kanka Wiki Review UI...')
    print('Open http://127.0.0.1:5555 in your browser')
    app.run(host='127.0.0.1', port=5555, debug=False)


if __name__ == '__main__':
    main()
