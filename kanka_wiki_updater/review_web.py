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
from collections import defaultdict, deque  # noqa: F401 (defaultdict needed for Task 2 SSE streaming)
from pathlib import Path

if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template_string, request

try:
    from . import config as pkg_config
except ImportError:
    from kanka_wiki_updater import config as pkg_config

# Import KankaClient at module level so tests can mock it
try:
    from .kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater.kanka_client import KankaClient

try:
    from .sync_pipeline import build_entity_index
except ImportError:
    from kanka_wiki_updater.sync_pipeline import build_entity_index
_sync_lock = threading.Lock()


def _rel_target(rel):
    """Extract the target_id from a Relation model or dict."""
    return getattr(rel, 'target_id', None) or (rel.get('target_id') if isinstance(rel, dict) else None)


def _rel_id(rel):
    """Extract the id from a Relation model or dict."""
    return getattr(rel, 'id', None) or (rel.get('id') if isinstance(rel, dict) else None)


_sync_jobs = {}
_job_counter = [0]


def _next_job_id():
    """Generate a unique job ID."""
    _job_counter[0] += 1
    return f'sync-{_job_counter[0]}'


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)

    @app.route('/')
    def index():
        queue = _load_queue()
        return render_template_string(INDEX_HTML, PROPOSALS=queue)

    @app.route('/api/proposals')
    def get_proposals():
        queue = _load_queue()
        status_filter = request.args.get('status')
        type_filter = request.args.get('type')

        if status_filter:
            queue = [p for p in queue if p.get('status') == status_filter]
        if type_filter:
            queue = [p for p in queue if p.get('proposal_type') == type_filter]

        return jsonify(queue)

    @app.route('/api/proposals/<int:index>/status', methods=['POST'])
    def update_status(index):
        queue = _load_queue()
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
                success, message = _sync_proposal_to_kanka(index)
            queue = _load_queue()  # Reload after potential modifications
            proposal = queue[index]
            sync_result = {'ok': success, 'message': message}
            if not success:
                return jsonify(
                    {
                        'proposal': proposal,
                        'ok': False,
                        'sync_error': True,
                        'sync_message': message,
                    }
                ), 409

        mapping = {
            'approved_all': 'applied',
            'approved_synopsis_only': 'applied',
            'rejected': 'rejected',
        }
        queue[index]['status'] = mapping[status_value]
        _save_queue(queue)
        result = {'proposal': queue[index], 'ok': True}
        if sync_result:
            result['sync'] = sync_result
        return jsonify(result)

    @app.route('/api/proposals/<int:index>/edit', methods=['POST'])
    def edit_proposal(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        entry_text = data.get('entry', '')
        proposal = queue[index]

        if proposal['proposal_type'] == 'new_entity':
            proposal['draft_entry'] = entry_text
        else:
            proposal['proposed_entry'] = entry_text

        _save_queue(queue)
        return jsonify({'proposal': proposal, 'ok': True})

    @app.route('/api/proposals/<int:index>/relation', methods=['POST'])
    def update_relation(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        action = (data.get('action') or '').strip().lower()
        target_name = data.get('target_name', '')
        proposal = queue[index]
        relations = proposal.get('relation_changes', [])

        if action == 'create':
            new_rel = {
                'action': 'create',
                'relation': data.get('relation', ''),
                'target_name': target_name,
                'attitude': data.get('attitude', ''),
                'reason': data.get('reason', ''),
            }
            relations.append(new_rel)

        elif action == 'delete':
            found = next((r for r in relations if r['target_name'] == target_name), None)
            if not found:
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404
            relations.remove(found)

        elif action == 'update':
            found = next((r for r in relations if r['target_name'] == target_name), None)
            if not found:
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404
            found['relation'] = data.get('relation', found['relation'])
            found['attitude'] = data.get('attitude', found['attitude'])
            found['reason'] = data.get('reason', found['reason'])

        else:
            return jsonify({'error': f'Invalid action: {action}'}), 400

        proposal['relation_changes'] = relations
        _save_queue(queue)
        return jsonify({'proposal': proposal, 'ok': True})

    def _sync_proposal_to_kanka(idx):
        """Actually push a proposal to Kanka.io. Returns (success, details)."""
        try:
            from .mentions import add_missing_entity_tags
        except ImportError:
            from kanka_wiki_updater.mentions import add_missing_entity_tags

        queue = _load_queue()
        if idx >= len(queue):
            return False, 'Proposal not found in queue'

        proposal = queue[idx]
        client = KankaClient()
        details = []
        errors = []

        def resolve_name_to_id(client, name):
            """Resolve an entity name to its entity_id via a full index build."""
            try:
                index = build_entity_index(client)
                for eid, data in index.items():
                    if data['name'] == name:
                        return eid
            except Exception as e:
                errors.append(f'Resolution warning: {e}')
            return None

        try:
            if proposal.get('proposal_type') == 'new_entity':
                entity_type = proposal.get('suggested_type', 'character')
                kind_param = 'characters' if entity_type == 'character' else 'locations'
                result = getattr(client, f'create_{entity_type}')(
                    proposal['entity_name'], entry=proposal.get('draft_entry', '')
                )
                data = result.get('data', {}) if isinstance(result, dict) else {}
                new_entity_id = data.get('entity_id')
                proposal['created_local_id'] = data.get('id')
                proposal['created_kind'] = entity_type
                proposal['created_entity_id'] = new_entity_id
                details.append(f"Created {entity_type} '{proposal['entity_name']}'")
                if new_entity_id:
                    details.append(f' (entity_id={new_entity_id})')
                    # Make immediately available as relation target for later proposals in same batch
                    queue[idx]['_resolved'] = True
                else:
                    errors.append(f"Created entity but couldn't read entity_id from response. Raw response: {result}")

            elif proposal.get('proposal_type') == 'update':
                # Update synopsis entry
                kind_param = 'characters' if proposal['entity_kind'] == 'character' else 'locations'
                client.update_entity_entry(
                    kind_param,
                    proposal['entity_local_id'],
                    proposal['proposed_entry'],
                )
                details.append(f"Updated synopsis for '{proposal['entity_name']}'")

        except Exception as e:
            errors.append(f'Sync error: {e}')

        # Handle relation changes (both new_entity and update)
        rel_changes = proposal.get('relation_changes', [])
        if rel_changes:
            try:
                if proposal.get('proposal_type') == 'new_entity':
                    entity_id_str = str(proposal.get('created_entity_id', ''))
                    if not entity_id_str or not entity_id_str.isdigit():
                        errors.append(
                            'Cannot resolve relations for new entity: no entity_id available. '
                            'Approve the synopsis first, then retry relations.'
                        )
                    else:
                        entity_id = int(entity_id_str)
                else:
                    eid = proposal.get('entity_id')
                    if not eid:
                        eid = resolve_name_to_id(client, proposal['entity_name'])
                        if not eid:
                            errors.append(
                                f"Cannot find entity_id for '{proposal['entity_name']}'. "
                                'Ensure the entity exists in Kanka.'
                            )
                    else:
                        entity_id = int(eid)

                # Only proceed with relations if we have a valid entity_id and no fatal errors
                fatal_errors = [e for e in errors if 'Cannot resolve' in e or 'Cannot find entity_id' in e]
                if not fatal_errors and 'entity_id' in locals():
                    existing_relations = client.get_relations(entity_id)
                    for rc in rel_changes:
                        target_name = rc['target_name']
                        action = (rc.get('action') or '').strip().lower()

                        if action == 'delete':
                            target_entity_id = resolve_name_to_id(client, target_name)
                            if not target_entity_id:
                                details.append(f'Skipped deleting relation -> {target_name}: entity not found')
                                continue
                            existing = next((r for r in existing_relations if _rel_target(r) == target_entity_id), None)
                            if existing and _rel_id(existing):
                                client.delete_relation(entity_id, _rel_id(existing))
                                details.append(f'Deleted relation -> {target_name}')

                        elif action in ('create', 'update'):
                            target_entity_id = resolve_name_to_id(client, target_name)
                            if not target_entity_id:
                                details.append(f'Skipped {action} relation -> {target_name}: entity not found')
                                continue

                            existing = next((r for r in existing_relations if _rel_target(r) == target_entity_id), None)

                            if action == 'create' or not existing:
                                resp = client.create_relation(
                                    entity_id, target_entity_id, rc['relation'], rc.get('attitude')
                                )
                                details.append(f"Created relation -> {target_name}: '{rc['relation']}'")
                            elif existing and _rel_id(existing):
                                client.update_relation(
                                    entity_id,
                                    _rel_id(existing),
                                    relation=rc['relation'],
                                    attitude=rc.get('attitude'),
                                )
                                details.append(f"Updated relation -> {target_name}: '{rc['relation']}'")

            except Exception as e:
                errors.append(f'Relation sync error: {e}')

        if errors:
            return False, '; '.join(errors)
        return True, '; '.join(details)

    @app.route('/api/proposals/<int:index>/sync', methods=['POST'])
    def sync_proposal(index):
        """Sync a single proposal to Kanka.io. Used before marking as applied."""
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        with _sync_lock:
            success, message = _sync_proposal_to_kanka(index)

        result = {
            'ok': success,
            'message': message,
            'proposal': queue[index],
        }
        if success:
            # Mark as applied after successful sync
            mapping = {
                'approved_all': 'applied',
                'approved_synopsis_only': 'applied',
            }
            status_value = request.args.get('status', '')
            status_key = f'status_{status_value}'
            if hasattr(request, 'get_json') and request.get_json(silent=True):
                status_value = request.get_json(silent=True).get('status', '')
            # We just sync; the caller should still call /status to set local state
        _save_queue(queue)
        return jsonify(result)

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
        last_flush = [0]  # mutable index into buffer

        def generate():
            while True:
                # Drain any buffered lines first
                while buffer:
                    line = buffer.popleft()
                    yield f'event: message\ndata: {json.dumps({"type": "output", "text": line})}\n\n'

                if job['status'] in ('completed', 'error'):
                    yield f'event: status\ndata: {json.dumps({"status": job["status"]})}\n\n'
                    yield 'event: end\n\n'
                    break

                time.sleep(0.2)  # poll interval

        from flask import Response

        return Response(generate(), mimetype='text/event-stream')

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


def _load_queue():
    try:
        from . import config, state
    except ImportError:
        from kanka_wiki_updater import config, state

    queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
    return state._load(queue_file, [])


def _save_queue(queue):
    try:
        from . import config, state
    except ImportError:
        from kanka_wiki_updater import config, state

    queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
    state._save(queue_file, queue)


def _sync_thread(job_id, proc, buffer):
    """Background thread that reads subprocess stdout and pushes to deque."""
    try:
        for line in proc.stdout:
            buffer.append(line.rstrip('\n'))
        proc.wait()
        if proc.returncode == 0:
            _sync_jobs[job_id]['status'] = 'completed'
        else:
            _sync_jobs[job_id]['status'] = 'error'
    except Exception as e:
        buffer.append(f'Sync error: {e}')
        _sync_jobs[job_id]['status'] = 'error'


# ── HTML template (embedded single-page app) ───────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kanka Wiki Review</title>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #c9d1d9; --text-dim: #8b949e;
  --green: #3fb950; --red: #f85149; --yellow: #d29922;
  --cyan: #39d2c0; --magenta: #bc8cff; --blue: #58a6ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }
.app { display: flex; flex-direction: column; height: 100vh; }
.header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; }
.header h1 { font-size: 16px; font-weight: 600; color: var(--cyan); }
.header .stats { font-size: 13px; color: var(--text-dim); }
.main { display: flex; flex: 1; overflow: hidden; }
.sidebar { width: 280px; background: var(--surface); border-right: 1px solid var(--border); flex-shrink: 0; display: flex; flex-direction: column; }
.tab-bar { display: flex; border-bottom: 1px solid var(--border); padding: 0 8px; flex-shrink: 0; }
.sidebar-list { overflow-y: auto; flex: 1; }
.tab-btn { background: none; border: none; color: var(--text-dim); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; padding: 10px 12px; cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s; }
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--cyan); border-bottom-color: var(--cyan); }
.tab-btn.inactive { opacity: 0.6; }
.proposal-item { padding: 12px 16px; cursor: pointer; border-bottom: 1px solid var(--border); transition: background 0.15s; }
.proposal-item:hover { background: #1c2128; }
.proposal-item.active { background: #1f2a37; border-left: 3px solid var(--cyan); }
.proposal-item .name { font-size: 14px; font-weight: 500; margin-bottom: 4px; }
.proposal-item .meta { font-size: 11px; color: var(--text-dim); }
.badge { display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; text-transform: uppercase; margin-right: 6px; }
.badge-new { background: #1a2e3a; color: var(--cyan); }
.badge-upd { background: #1c2833; color: var(--blue); }
.content { flex: 1; overflow-y: auto; padding: 24px; }
.proposal-header { margin-bottom: 20px; }
.proposal-header h2 { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
.proposal-header .source { font-size: 13px; color: var(--text-dim); }
.proposal-header .summary { font-size: 14px; color: var(--text); margin-top: 8px; padding: 10px; background: var(--surface); border-radius: 6px; border-left: 3px solid var(--magenta); }
.diff-section { margin-bottom: 24px; }
.diff-section h3 { font-size: 13px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.diff-container { background: #0d1117; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.diff-line { padding: 4px 12px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 15px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.diff-add { background: #0d1f0d; color: var(--green); border-left: 3px solid var(--green); }
.diff-del { background: #2a0d0d; color: var(--red); border-left: 3px solid var(--red); }
.relations-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.relation-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; }
.relation-card .rel-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.rel-action { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; text-transform: uppercase; }
.rel-create { background: #0d2817; color: var(--green); }
.rel-update { background: #2a2000; color: var(--yellow); }
.rel-delete { background: #2a0d0d; color: var(--red); }
.relation-card .rel-target { font-size: 14px; font-weight: 500; }
.relation-card .rel-reason { font-size: 13px; color: var(--text-dim); margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border); }
.warning { background: #2a2000; border: 1px solid var(--yellow); border-radius: 6px; padding: 10px 14px; margin-bottom: 16px; font-size: 13px; color: var(--yellow); }
.warning.critical { background: #2a0d0d; border-color: var(--red); color: var(--red); }
.editor-section { margin-bottom: 24px; }
textarea.synopsis-editor { width: 100%; min-height: 200px; background: #0d1117; border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-family: inherit; font-size: 14px; line-height: 1.6; padding: 12px; resize: vertical; }
textarea.synopsis-editor:focus { outline: none; border-color: var(--cyan); }
.action-bar { position: sticky; bottom: 0; background: rgba(22, 27, 34, 0.95); backdrop-filter: blur(8px); border-top: 1px solid var(--border); padding: 16px 24px; display: flex; gap: 10px; align-items: center; }
.btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: 1px solid var(--border); transition: all 0.15s; background: var(--surface); color: var(--text); }
.btn:hover { opacity: 0.85; transform: translateY(-1px); }
.btn-primary { background: #238636; color: white; border-color: #238636; }
.btn-danger { background: #da3633; color: white; border-color: #da3633; }
.toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%) translateY(100px); padding: 12px 24px; border-radius: 6px; font-size: 14px; font-weight: 500; opacity: 0; transition: all 0.3s ease; z-index: 100; }
.toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
.toast-success { background: #238636; color: white; }
.toast-error { background: #da3633; color: white; }
.empty-state { text-align: center; padding: 60px 20px; color: var(--text-dim); }
.empty-state h3 { font-size: 18px; margin-bottom: 8px; color: var(--text); }
.sync-container { display: flex; flex-direction: column; height: calc(100vh - 205px); min-height: 120px; max-height: calc(100vh - 205px); }
.empty-state { display: flex; flex-direction: column; height: 100%; }
.sync-output { background:#0d1117;border:1px solid var(--border);border-radius:6px;padding:12px;color:var(--green);font-family:'SF Mono',monospace;font-size:13px;line-height:1.6;overflow-y:auto;white-space:pre-wrap;text-align:left;flex:1;min-height:0;width:100%; }
.progress-bar { height: 3px; background: var(--border); position: relative; }
.progress-fill { height: 100%; background: linear-gradient(90deg, var(--cyan), var(--blue)); transition: width 0.3s ease; }
.edit-mode-banner { background: #0d2837; border-bottom: 1px solid var(--cyan); padding: 8px 24px; font-size: 13px; color: var(--cyan); display: none; }
.edit-mode-banner.visible { display: flex; justify-content: space-between; align-items: center; }
.relation-editor { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.relation-editor select, .relation-editor input { background: #0d1117; border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 13px; margin-right: 8px; }
.shortcuts { position: fixed; bottom: 70px; right: 20px; font-size: 11px; color: var(--text-dim); text-align: right; line-height: 1.8; }
kbd { background: var(--surface); border: 1px solid var(--border); padding: 1px 6px; border-radius: 3px; font-family: monospace; }
.add-relation-form { margin-top: 12px; padding: 10px; background: var(--surface); border: 1px dashed var(--border); border-radius: 6px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.add-relation-form input, .add-relation-form select { background: #0d1117; border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 13px; }
.add-relation-form button { padding: 4px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; background: var(--green); color: white; border: none; }
.entity-name-editor, .type-selector { margin-bottom: 8px; }
.entity-name-editor input { background: #0d1117; border: 1px solid var(--cyan); color: var(--text); padding: 6px 10px; border-radius: 4px; font-size: 16px; width: 300px; }
.type-selector select { background: #0d1117; border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 13px; margin-left: 8px; }
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <h1>Kanka Wiki Review</h1>
    <div class="stats" id="stats"></div>
  </div>
  <div class="edit-mode-banner" id="editBanner">
    <span>Edit mode — modify the text above, then save or cancel.</span>
    <button class="btn btn-primary" onclick="saveEdit()" style="padding:4px 12px;font-size:12px;">Save</button>
    <button class="btn btn-secondary" onclick="cancelEdit()" style="padding:4px 12px;font-size:12px;">Cancel</button>
  </div>
  <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
  <div class="main">
    <div class="sidebar" id="sidebar"></div>
    <div class="content" id="content"></div>
  </div>
  <div class="action-bar" id="actionBar">
    <button class="btn btn-primary" onclick="approveAll()">Approve All</button>
    <button class="btn btn-secondary" onclick="approveSynopsisOnly()">Synopsis Only</button>
    <button class="btn btn-danger" onclick="rejectCurrent()">Reject</button>
    <div style="flex:1"></div>
    <span style="font-size:12px;color:var(--text-dim)">[n]ext [p]rev [e]dit [esc]cancel [a]pprove [s]ynopsis [r]eject [q]uit</span>
  </div>
  <div class="shortcuts">
    <kbd>n</kbd> next &nbsp; <kbd>p</kbd> prev<br>
    <kbd>e</kbd> edit &nbsp; <kbd>esc</kbd> cancel<br>
    <kbd>a</kbd> approve all &nbsp; <kbd>s</kbd> synopsis only<br>
    <kbd>r</kbd> reject &nbsp; <kbd>q</kbd> quit (close tab)
  </div>
  <div class="toast" id="toast"></div>
</div>

<script>
let proposals = {{ PROPOSALS | tojson }};
let selectedIndex = null;
let currentTab = 'new'; // default tab
let editingField = null; // 'synopsis' or 'name' for new entities
let editingOriginal = ''; // original text when entering edit mode (for escape-to-cancel)
let currentSyncJob = null; // {job_id, status, output}

function getPending() { return proposals.filter(p => p.status === 'pending'); }

function getVisibleIndices() {
  if (currentTab === 'new') {
    return proposals.reduce((acc, p, i) => { if (p.status === 'pending') acc.push(i); return acc; }, []);
  } else {
    return proposals.reduce((acc, p, i) => { if (p.status !== 'pending') acc.push(i); return acc; }, []);
  }
}
function updateStats() {
  const pending = getPending();
  const applied = proposals.filter(p => p.status === 'applied').length;
  const rejected = proposals.filter(p => p.status === 'rejected').length;
  document.getElementById('stats').textContent = pending.length + ' pending | ' + applied + ' approved | ' + rejected + ' rejected';
  const total = proposals.length;
  const done = applied + rejected;
  const pct = total ? (done / total * 100) : 0;
  document.getElementById('progressFill').style.width = pct + '%';
}

function renderSidebar() {
  const sidebar = document.getElementById('sidebar');
  let html = '<div class="tab-bar" id="tabBar">' +
    '<button class="tab-btn ' + (currentTab === "new" ? "active" : "inactive") + '" data-tab="new" onclick="switchTab(\\'new\\')">New</button>' +
    '<button class="tab-btn ' + (currentTab === "reviewed" ? "active" : "inactive") + '" data-tab="reviewed" onclick="switchTab(\\'reviewed\\')">Reviewed</button>' +
    '<button class="tab-btn ' + (currentTab === "sync" ? "active" : "inactive") + '" data-tab="sync" onclick="switchTab(\\'sync\\')">Sync</button>' +
    '</div>';
  html += '<div class="sidebar-list">';
  let pending = proposals.filter(p => p.status === 'pending');
  if (currentTab === 'new') {
    pending.sort((a, b) => {
      const aNew = a.proposal_type === 'new_entity' ? 0 : 1;
      const bNew = b.proposal_type === 'new_entity' ? 0 : 1;
      return aNew - bNew;
    });
  }
  const filtered = currentTab === 'new' ? pending : proposals.filter(p => p.status !== 'pending');
  for (let f = 0; f < filtered.length; f++) {
    const origIdx = proposals.indexOf(filtered[f]);
    var isActive = origIdx === selectedIndex ? ' active' : '';
    var kind = filtered[f].proposal_type === 'new_entity' ? 'NEW' : 'UPD';
    var badgeClass = filtered[f].proposal_type === 'new_entity' ? 'badge-new' : 'badge-upd';
    var statusBadge = '';
    if (filtered[f].status === 'applied') { statusBadge = '<span style="color:var(--green)">&#10003;</span>'; }
    else if (filtered[f].status === 'rejected') { statusBadge = '<span style="color:var(--red)">&#10007;</span>'; }
    html += '<div class="proposal-item' + isActive + '" onclick="selectProposal(' + origIdx + ')">' +
      '<div class="name"><span class="badge ' + badgeClass + '">' + kind + '</span>' + escapeJsHtml(filtered[f].entity_name) + statusBadge + '</div>' +
      '<div class="meta">' + escapeJsHtml(filtered[f].source_journal) + '</div></div>';
  }
  html += '</div>';
  sidebar.innerHTML = html;
}

function switchTab(tab) {
  if (tab === currentTab) return;
  if (editingField) cancelEdit();
  currentTab = tab;
  selectedIndex = null;
  renderSidebar();
  renderContent();
}

function renderContent() {
  var content = document.getElementById('content');
  if (selectedIndex === null || selectedIndex >= proposals.length) {
    if (currentTab !== 'sync') {
      content.innerHTML = '<div class="empty-state"><h3>No proposal selected</h3>Select one from the sidebar.</div>';
    }
    renderSyncContent(content);
    return;
  }
  var p = proposals[selectedIndex];
  var html = '';

  // Header
  if (p.proposal_type === 'new_entity') {
    html += '<div class="proposal-header"><h2>New ' + p.suggested_type + ': <span id="entityNameDisplay">' + escapeJsHtml(p.entity_name) + '</span></h2>';
    html += '<div class="source">&larr; ' + escapeJsHtml(p.source_journal) + '</div></div>';
  } else {
    var statusIcon = {pending:'&#9675;', applied:'<span style="color:var(--green)">&#10003;</span>', rejected:'<span style="color:var(--red)">&#10007;</span>'}[p.status] || '&#9675;';
    html += '<div class="proposal-header"><h2>' + statusIcon + ' ' + escapeJsHtml(p.entity_name) + ' <span style="font-weight:400;color:var(--text-dim);font-size:16px">(' + p.entity_kind + ')</span></h2>';
    html += '<div class="source">&larr; ' + escapeJsHtml(p.source_journal) + '</div>';
    if (p.change_summary) { html += '<div class="summary">' + escapeJsHtml(p.change_summary) + '</div>'; }
    html += '</div>';
  }

  // Warnings for dropped mentions
  var prevEntry = p.previous_entry || '';
  var proposedEntry = p.proposed_entry || '';
  if (prevEntry && proposedEntry) {
    var oldIds = (prevEntry.match(/\\[entity:(\\d+)\\]/g) || []).map(function(s){ return s.match(/(\\d+)/)[1]; });
    var newIds = (proposedEntry.match(/\\[entity:(\\d+)\\]/g) || []).map(function(s){ return s.match(/(\\d+)/)[1]; });
    var dropped = oldIds.filter(function(x){ return newIds.indexOf(x) === -1; });
    if (dropped.length > 0) {
      html += '<div class="warning critical">!! ' + dropped.length + ' mention link(s) missing from new version!</div>';
    }
  }

  // Synopsis / draft editing area
  html += '<div class="diff-section"><h3>' + (p.proposal_type === 'new_entity' ? 'Draft Synopsis' : 'Synopsis') + '</h3><div class="diff-container">';
  if (editingField === 'synopsis') {
    var currentText = p.proposal_type === 'new_entity' ? p.draft_entry : p.proposed_entry;
    html += '<textarea class="synopsis-editor" id="synopsisEditor">' + escapeJsHtml((stripHtml(currentText) || '').replace(/\\n/g, ' ')) + '</textarea>';
  } else {
    if (p.proposal_type === 'new_entity') {
      html += '<div class="diff-line" style="cursor:pointer" onclick="startEdit(&quot;synopsis&quot;)">' + escapeJsHtml((p.draft_entry || '(none)').replace(/\\n/g, ' ')) + '</div>';
      html += '<div style="padding:4px 12px;font-size:11px;color:var(--text-dim)">Click to edit</div>';
    } else {
      var prevLines = stripHtml(p.previous_entry).split('\\n');
      var newLines = (p.proposed_entry || '').split('\\n');
      var maxLen = Math.max(prevLines.length, newLines.length);
      for (var i = 0; i < maxLen; i++) {
        if (i >= prevLines.length) {
          html += '<div class="diff-line diff-add">' + escapeJsHtml(newLines[i]) + '</div>';
        } else if (i >= newLines.length) {
          html += '<div class="diff-line diff-del">' + escapeJsHtml(prevLines[i]) + '</div>';
        } else if (prevLines[i] !== newLines[i]) {
          html += '<div class="diff-line diff-del">' + escapeJsHtml(prevLines[i]) + '</div>';
          html += '<div class="diff-line diff-add">' + escapeJsHtml(newLines[i]) + '</div>';
        } else {
          html += '<div class="diff-line" style="padding-left:20px">' + escapeJsHtml(prevLines[i]) + '</div>';
        }
      }
    }
  }
  html += '</div></div>';

  // Relation changes (update proposals only)
  if (p.relation_changes && p.relation_changes.length > 0) {
    html += '<div class="diff-section"><h3>Relationship Changes</h3><div class="relations-list">';
    p.relation_changes.forEach(function(rc, idx) {
      var actionClass = rc.action === 'create' ? 'rel-create' : rc.action === 'update' ? 'rel-update' : 'rel-delete';
      html += '<div class="relation-card" id="rel-' + idx + '">' +
        '<div class="rel-header">' +
          '<span class="rel-action ' + actionClass + '">' + escapeJsHtml(rc.action) + '</span>' +
          '<span class="rel-target">' + escapeJsHtml(p.entity_name) + ' --' + escapeJsHtml(rc.relation) + '--> ' + escapeJsHtml(rc.target_name) + '</span>' +
          '<button class="btn" onclick="deleteRelation(' + idx + ')" style="padding:2px 8px;font-size:11px;margin-left:auto;">Delete</button>' +
        '</div>' +
        '<div style="font-size:12px;color:var(--text-dim)">Attitude: ' + escapeJsHtml(rc.attitude || 'N/A') + '</div>' +
        '<div class="rel-reason">Reason: ' + escapeJsHtml(rc.reason) + '</div></div>';
    });
    html += '</div>';

    // Add new relation form
    html += '<div class="add-relation-form">' +
      '<input type="text" id="newRelTarget" placeholder="Target name">' +
      '<select id="newRelAction"><option value="create">create</option><option value="update">update</option></select>' +
      '<input type="text" id="newRelRelation" placeholder="Relation (e.g. ally)">' +
      '<input type="text" id="newRelAttitude" placeholder="Attitude">' +
      '<button onclick="addRelation()">Add</button></div>';
    html += '</div>';
  }

  // Status indicator
  if (p.status !== 'pending') {
    var statusColor = p.status === 'applied' ? 'var(--green)' : 'var(--red)';
    html += '<div style="text-align:center;padding:16px;color:' + statusColor + ';font-weight:600;font-size:14px">Status: ' + p.status.toUpperCase() + '</div>';
  }

  content.innerHTML = html;
}

function renderSyncContent(content) {
  if (currentTab !== 'sync') return;
  var jobStatus = currentSyncJob ? currentSyncJob.status : 'idle';
  var statusColor = {'running':'var(--blue)','completed':'var(--green)','error':'var(--red)','idle':'var(--text-dim)'}[jobStatus] || 'var(--text-dim)';

  content.innerHTML = '<div class="empty-state">' +
    '<h3>Run Sync Pipeline</h3>' +
    '<div class="sync-container">' +
      '<div style="margin:16px 0;">' +
        '<button class="btn btn-primary" id="runSyncBtn" onclick="runSync()">' + (currentSyncJob ? 'Cancel' : 'Run Sync') + '</button>' +
        '<span style="margin-left:12px;color:' + statusColor + ';font-weight:600;font-size:14px">&#9679; ' + jobStatus.toUpperCase() + '</span>' +
      '</div>' +
      '<pre id="syncOutput" class="sync-output">' +
        (currentSyncJob && currentSyncJob.output ? escapeJsHtml(currentSyncJob.output) : 'No sync run in progress.') +
      '</pre>' +
    '</div>' +
  '</div>';
}

function selectProposal(i) {
  selectedIndex = i;
  if (editingField) cancelEdit();
  renderSidebar();
  renderContent();
}

function escapeHtml(text) {
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(text || ''));
  return div.innerHTML;
}

function escapeJsHtml(str) {
  // Escape HTML special chars, convert newlines to <br> for safe innerHTML insertion and JS string literal safety
  var escaped = (escapeHtml(str || '')).replace(/\\\\/g, '\\\\');
  return escaped.replace(/\\r\\n/g, '<br>').replace(/\\r/g, '<br>').replace(/\\n/g, '<br>');
}

function escapeJs(str) {
  // Escape backslash, newline, carriage return, and forward slash for safe JS string literal insertion
  return (str || '').replace(/\\\\/g, '\\\\\\\\').replace(/\\n/g, '\\\\n').replace(/\\r/g, '\\\\r').replace(/\\//g, '\\/');
}

function stripHtml(html) {
  var tmp = document.createElement('div');
  tmp.innerHTML = html || '';
  return tmp.textContent || tmp.innerText || '';
}

// ── Actions ────────────────────────────────────────────────────────────────

async function apiCall(url, method, body) {
  try {
    var res = await fetch(url, {
      method: method,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
    return null;
  }
}

function approveAll() {
  if (selectedIndex === null) return;
  var oldIndex = selectedIndex;
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_all'})
    .then(function(data) {
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync && data.sync.ok) {
        showToast('Synced to Kanka: ' + data.sync.message, 'success');
      } else if (data.sync && !data.sync.ok) {
        showToast('Kanka sync failed: ' + data.sync.message, 'error');
      } else {
        showToast('Approved all', 'success');
      }
    });
}

function approveSynopsisOnly() {
  if (selectedIndex === null) return;
  var oldIndex = selectedIndex;
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_synopsis_only'})
    .then(function(data) {
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync && data.sync.ok) {
        showToast('Synopsis synced to Kanka: ' + data.sync.message, 'success');
      } else if (data.sync && !data.sync.ok) {
        showToast('Kanka sync failed: ' + data.sync.message, 'error');
      } else {
        showToast('Synopsis approved', 'success');
      }
    });
}

function rejectCurrent() {
  if (selectedIndex === null) return;
  var oldIndex = selectedIndex;
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'rejected'})
    .then(function(data) { if (data) { proposals[selectedIndex] = data.proposal; _advance(oldIndex); showToast('Rejected', 'error'); } });
}

function _advance(fromIndex) {
  var visible = getVisibleIndices();
  for (var i = 0; i < visible.length; i++) {
    if (visible[i] > fromIndex) {
      selectedIndex = visible[i];
      renderSidebar();
      renderContent();
      return;
    }
  }
  selectedIndex = null;
  renderSidebar();
  renderContent();
}

// ── Editor ─────────────────────────────────────────────────────────────────

function startEdit(field) {
  editingField = field;
  renderContent();
  var editor = document.getElementById('synopsisEditor');
  if (editor) {
    editingOriginal = editor.value;
    editor.focus();
    editor.selectionStart = editor.value.length;
  }
}

async function saveEdit() {
  if (selectedIndex === null || !editingField) return;
  var editor = document.getElementById('synopsisEditor');
  var text = editor.value;
  var fieldKey = editingField === 'synopsis' ? (proposals[selectedIndex].proposal_type === 'new_entity' ? 'draft_entry' : 'proposed_entry') : null;

  if (fieldKey) {
    var result = await apiCall('/api/proposals/' + selectedIndex + '/edit', 'POST', {entry: text});
    if (result && result.proposal) {
      proposals[selectedIndex] = result.proposal;
      editingField = null;
      renderSidebar();
      renderContent();
      showToast('Changes saved', 'success');
    }
  }
}

function cancelEdit() {
  editingField = null;
  renderContent();
}

// ── Relation management ────────────────────────────────────────────────────

async function addRelation() {
  if (selectedIndex === null) return;
  var target = document.getElementById('newRelTarget').value.trim();
  var action = document.getElementById('newRelAction').value;
  var relation = document.getElementById('newRelRelation').value.trim();
  var attitude = document.getElementById('newRelAttitude').value.trim();

  if (!target || !relation) { showToast('Target and relation are required', 'error'); return; }

  var result = await apiCall('/api/proposals/' + selectedIndex + '/relation', 'POST', {
    action: action, target_name: target, relation: relation, attitude: attitude, reason: ''
  });
  if (result && result.proposal) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Relation added', 'success');
  }
}

async function deleteRelation(idx) {
  if (selectedIndex === null) return;
  var p = proposals[selectedIndex];
  var rel = p.relation_changes[idx];
  var result = await apiCall('/api/proposals/' + selectedIndex + '/relation', 'POST', {
    action: 'delete', target_name: rel.target_name
  });
  if (result && result.proposal) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Relation deleted', 'success');
  }
}

// ── Toast ──────────────────────────────────────────────────────────────────

function showToast(message, type) {
  var toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'toast toast-' + type + ' show';
  setTimeout(function(){ toast.classList.remove('show'); }, 7000);
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (editingField || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

  switch(e.key.toLowerCase()) {
    case 'n': {
      const visible = getVisibleIndices();
      const pos = visible.indexOf(selectedIndex);
      if (pos !== null && pos < visible.length - 1) {
        selectedIndex = visible[pos + 1];
        renderSidebar();
        renderContent();
      }
      break;
    }
    case 'p': {
      const visible = getVisibleIndices();
      const pos = visible.indexOf(selectedIndex);
      if (pos !== null && pos > 0) {
        selectedIndex = visible[pos - 1];
        renderSidebar();
        renderContent();
      }
      break;
    }
    case 'e': startEdit('synopsis'); break;
    case 'a': approveAll(); break;
    case 's': approveSynopsisOnly(); break;
    case 'r': rejectCurrent(); break;
    case 'q': window.close(); break;
  }
});

// ── Escape key to cancel editing ───────────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (e.key !== 'Escape' || !editingField) return;
  var editor = document.getElementById('synopsisEditor');
  if (!editor) return;
  var hasChanges = editor.value !== editingOriginal;
  if (hasChanges && !confirm('Discard unsaved changes?')) return;
  cancelEdit();
});

// ── Sync pipeline runner ───────────────────────────────────────────────────

async function runSync() {
  if (currentSyncJob) {
    // Cancel: close EventSource and reset
    currentSyncJob = null;
    renderContent();
    return;
  }

  var result = await apiCall('/api/sync/run', 'POST');
  if (!result || !result.job_id) return;

  currentSyncJob = { job_id: result.job_id, status: 'running', output: '' };
  renderContent();

  // Connect to SSE stream
  var source = new EventSource('/api/sync/output?job_id=' + result.job_id);

  source.addEventListener('message', function(e) {
    var data = JSON.parse(e.data);
    if (data.type === 'output') {
      currentSyncJob.output += data.text + '\\n';
      var pre = document.getElementById('syncOutput');
      if (pre) {
        pre.textContent = currentSyncJob.output;
        pre.scrollTop = pre.scrollHeight;
      }
    }
  });

  source.addEventListener('status', function(e) {
    var data = JSON.parse(e.data);
    currentSyncJob.status = data.status;
    renderContent();
  });

  source.addEventListener('end', function() {
    source.close();
    // Refresh proposals after sync completes
    loadProposals();
  });
}

function loadProposals() {
  fetch('/api/proposals')
    .then(function(r){ return r.json(); })
    .then(function(data) {
      var existing = proposals.slice();
      for (var i = 0; i < data.length; i++) {
        var found = false;
        for (var j = 0; j < existing.length; j++) {
          if (existing[j].entity_name === data[i].entity_name && existing[j].source_journal === data[i].source_journal) {
            existing[j] = data[i];
            found = true;
            break;
          }
        }
        if (!found) { existing.push(data[i]); }
      }
      proposals = existing;
      updateStats();
      renderSidebar();
      renderContent();
    })
    .catch(function() { /* silently ignore — keep current state */ });
}

// ── Init ───────────────────────────────────────────────────────────────────

updateStats();
renderSidebar();
if (proposals.length > 0) { selectProposal(0); }
else { document.getElementById('content').innerHTML = '<div class="empty-state"><h3>No pending proposals</h3>Run sync_pipeline first.</div>'; }

setInterval(updateStats, 5000);
</script>
</body>
</html>"""


def main():
    """Entry point for `python -m kanka_wiki_updater.review_web`."""
    app = create_app()
    print('Starting Kanka Wiki Review UI...')
    print('Open http://127.0.0.1:5555 in your browser')
    app.run(host='127.0.0.1', port=5555, debug=False)


if __name__ == '__main__':
    main()
