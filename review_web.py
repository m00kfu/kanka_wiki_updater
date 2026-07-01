"""Web-based review UI for Kanka Wiki Updater.

Launch with: python -m kanka_wiki_updater.review_web

Serves a single-page web app at http://127.0.0.1:5555 that reads and writes
data/pending_changes.json — the same file used by review.py. Both can coexist
without conflict; they just need to agree on the JSON schema.
"""

import os

from flask import Flask, jsonify, render_template_string, request


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

        mapping = {
            'approved_all': 'applied',
            'approved_synopsis_only': 'applied',
            'rejected': 'rejected',
        }
        queue[index]['status'] = mapping[status_value]
        _save_queue(queue)
        return jsonify({'proposal': queue[index], 'ok': True})

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

    return app


def _load_queue():
    from . import config, state

    queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
    return state._load(queue_file, [])


def _save_queue(queue):
    from . import config, state

    queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
    state._save(queue_file, queue)


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
.sidebar { width: 280px; background: var(--surface); border-right: 1px solid var(--border); overflow-y: auto; flex-shrink: 0; }
.sidebar-header { padding: 12px 16px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-dim); border-bottom: 1px solid var(--border); }
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
    <span style="font-size:12px;color:var(--text-dim)">[n]ext [p]rev [e]dit [a]pprove [s]ynopsis [r]eject [q]uit</span>
  </div>
  <div class="shortcuts">
    <kbd>n</kbd> next &nbsp; <kbd>p</kbd> prev<br>
    <kbd>e</kbd> edit &nbsp; <kbd>a</kbd> approve all<br>
    <kbd>s</kbd> synopsis only &nbsp; <kbd>r</kbd> reject<br>
    <kbd>q</kbd> quit (close tab)
  </div>
  <div class="toast" id="toast"></div>
</div>

<script>
let proposals = {{ PROPOSALS | tojson }};
let selectedIndex = null;
let editingField = null; // 'synopsis' or 'name' for new entities

function getPending() { return proposals.filter(p => p.status === 'pending'); }
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
  let html = '<div class="sidebar-header">Proposals (' + proposals.length + ')</div>';
  proposals.forEach(function(p, i) {
    var isActive = i === selectedIndex ? ' active' : '';
    var kind = p.proposal_type === 'new_entity' ? 'NEW' : 'UPD';
    var badgeClass = p.proposal_type === 'new_entity' ? 'badge-new' : 'badge-upd';
    var statusBadge = {pending:'', applied:' ✓', rejected:' ✗'}[p.status] || '';
    html += '<div class="proposal-item' + isActive + '" onclick="selectProposal(' + i + ')">' +
      '<div class="name"><span class="badge ' + badgeClass + '">' + kind + '</span>' + p.entity_name + statusBadge + '</div>' +
      '<div class="meta">' + p.source_journal + '</div></div>';
  });
  sidebar.innerHTML = html;
}

function renderContent() {
  var content = document.getElementById('content');
  if (selectedIndex === null || selectedIndex >= proposals.length) {
    content.innerHTML = '<div class="empty-state"><h3>No proposal selected</h3>Select one from the sidebar.</div>';
    return;
  }
  var p = proposals[selectedIndex];
  var html = '';

  // Header
  if (p.proposal_type === 'new_entity') {
    html += '<div class="proposal-header"><h2>New ' + p.suggested_type + ': <span id="entityNameDisplay">' + escapeHtml(p.entity_name) + '</span></h2>';
    html += '<div class="source">&larr; ' + escapeHtml(p.source_journal) + '</div></div>';
  } else {
    var statusIcon = {pending:'&#9675;', applied:'<span style="color:var(--green)">&#10003;</span>', rejected:'<span style="color:var(--red)">&#10007;</span>'}[p.status] || '&#9675;';
    html += '<div class="proposal-header"><h2>' + statusIcon + ' ' + escapeHtml(p.entity_name) + ' <span style="font-weight:400;color:var(--text-dim);font-size:16px">(' + p.entity_kind + ')</span></h2>';
    html += '<div class="source">&larr; ' + escapeHtml(p.source_journal) + '</div>';
    if (p.change_summary) { html += '<div class="summary">' + escapeHtml(p.change_summary) + '</div>'; }
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
    html += '<textarea class="synopsis-editor" id="synopsisEditor">' + escapeHtml(stripHtml(currentText)) + '</textarea>';
  } else {
    if (p.proposal_type === 'new_entity') {
      html += '<div class="diff-line" style="cursor:pointer" onclick="startEdit(&quot;synopsis&quot;)">' + escapeHtml(stripHtml(p.draft_entry || '(none)')) + '</div>';
      html += '<div style="padding:4px 12px;font-size:11px;color:var(--text-dim)">Click to edit</div>';
    } else {
      var prevLines = stripHtml(p.previous_entry).split('\\n');
      var newLines = (p.proposed_entry || '').split('\\n');
      var maxLen = Math.max(prevLines.length, newLines.length);
      for (var i = 0; i < maxLen; i++) {
        if (i >= prevLines.length) {
          html += '<div class="diff-line diff-add">' + escapeHtml(newLines[i]) + '</div>';
        } else if (i >= newLines.length) {
          html += '<div class="diff-line diff-del">' + escapeHtml(prevLines[i]) + '</div>';
        } else if (prevLines[i] !== newLines[i]) {
          html += '<div class="diff-line diff-del">' + escapeHtml(prevLines[i]) + '</div>';
          html += '<div class="diff-line diff-add">' + escapeHtml(newLines[i]) + '</div>';
        } else {
          html += '<div class="diff-line" style="padding-left:20px">' + escapeHtml(prevLines[i]) + '</div>';
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
          '<span class="rel-action ' + actionClass + '">' + escapeHtml(rc.action) + '</span>' +
          '<span class="rel-target">' + escapeHtml(p.entity_name) + ' --' + escapeHtml(rc.relation) + '--> ' + escapeHtml(rc.target_name) + '</span>' +
          '<button class="btn" onclick="deleteRelation(' + idx + ')" style="padding:2px 8px;font-size:11px;margin-left:auto;">Delete</button>' +
        '</div>' +
        '<div style="font-size:12px;color:var(--text-dim)">Attitude: ' + escapeHtml(rc.attitude || 'N/A') + '</div>' +
        '<div class="rel-reason">Reason: ' + escapeHtml(rc.reason) + '</div></div>';
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
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_all'})
    .then(function(data) { if (data) { proposals[selectedIndex] = data.proposal; renderSidebar(); renderContent(); showToast('Approved all', 'success'); } });
}

function approveSynopsisOnly() {
  if (selectedIndex === null) return;
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_synopsis_only'})
    .then(function(data) { if (data) { proposals[selectedIndex] = data.proposal; renderSidebar(); renderContent(); showToast('Synopsis approved', 'success'); } });
}

function rejectCurrent() {
  if (selectedIndex === null) return;
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'rejected'})
    .then(function(data) { if (data) { proposals[selectedIndex] = data.proposal; renderSidebar(); renderContent(); showToast('Rejected', 'error'); } });
}

// ── Editor ─────────────────────────────────────────────────────────────────

function startEdit(field) {
  editingField = field;
  renderContent();
  var editor = document.getElementById('synopsisEditor');
  if (editor) { editor.focus(); editor.selectionStart = editor.value.length; }
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
  setTimeout(function(){ toast.classList.remove('show'); }, 2000);
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (editingField || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

  switch(e.key.toLowerCase()) {
    case 'n': selectProposal(Math.min(selectedIndex + 1, proposals.length - 1)); break;
    case 'p': selectProposal(Math.max(selectedIndex - 1, 0)); break;
    case 'e': startEdit('synopsis'); break;
    case 'a': approveAll(); break;
    case 's': approveSynopsisOnly(); break;
    case 'r': rejectCurrent(); break;
    case 'q': window.close(); break;
  }
});

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
