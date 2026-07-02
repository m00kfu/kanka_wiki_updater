"""Demo: Web-based review UI for Kanka Wiki Updater (Flask).

Run from the project root:
    python kanka_wiki_updater/demo_web.py

Opens http://127.0.0.1:5555 in your browser. Uses mock data — no API calls.
"""

from flask import Flask, jsonify, request, render_template_string
import json

app = Flask(__name__)

# ── Mock data (same as demo_tui.py) ────────────────────────────────────────

MOCK_NEW_ENTITIES = [
    {
        "proposal_type": "new_entity",
        "entity_name": "Vexara the Veiled",
        "suggested_type": "character",
        "draft_entry": "A mysterious sorceress who operates out of the Shadowmere district. She trades in forbidden knowledge and has been seen conversing with members of the Crimson Circle.",
        "source_journal": "Session 12 - The Gathering Storm",
    },
    {
        "proposal_type": "new_entity",
        "entity_name": "Ironhold Keep",
        "suggested_type": "location",
        "draft_entry": "An ancient fortress on the northern border, once held by the Frostborn dynasty. Now abandoned after the Great Schism.",
        "source_journal": "Session 12 - The Gathering Storm",
    },
]

MOCK_UPDATES = [
    {
        "proposal_type": "update",
        "entity_name": "Kael Ironfist",
        "entity_kind": "character",
        "entity_id": "42",
        "entity_local_id": 101,
        "source_journal": "Session 13 - Ashes of the North",
        "change_summary": "Updated synopsis to reflect his journey through Ironhold Keep and alliance with Vexara.",
        "previous_entry": "<p>Kael is a dwarf warrior from the Mountain Clan. He wields a massive warhammer called Stonebreaker. Known for his stubbornness and loyalty, he has been allies with Thorin for years.</p>",
        "proposed_entry": "<p>Kael is a dwarf warrior from the Mountain Clan who now serves as a scout for the Northern Alliance. He wields a massive warhammer called Stonebreaker and has recently formed an uneasy alliance with the sorceress Vexara after discovering ancient dwarven ruins at Ironhold Keep.</p>\n\n<p>His journey through the northern wastes has hardened him, but he remains loyal to his companions. The encounter at Ironhold revealed fragments of a prophecy that may explain the Great Schism.</p>",
        "relation_changes": [
            {
                "action": "create",
                "relation": "ally",
                "target_name": "Vexara the Veiled",
                "attitude": "cautious trust",
                "reason": "They formed an alliance after discovering Ironhold together.",
            },
        ],
    },
    {
        "proposal_type": "update",
        "entity_name": "Thorin Stormborn",
        "entity_kind": "character",
        "entity_id": "17",
        "entity_local_id": 88,
        "source_journal": "Session 13 - Ashes of the North",
        "change_summary": "Added new enemy relation and updated synopsis with his confrontation at the Frostpeak Pass.",
        "previous_entry": "<p>Thorin is an elven ranger who serves as Kael's closest friend. He has exceptional archery skills and knowledge of ancient lore. His family was destroyed during the Dragon Wars.</p>",
        "proposed_entry": "<p>Thorin is an elven ranger who serves as Kael's closest friend and tactical advisor. He has exceptional archery skills and deep knowledge of ancient dwarven architecture — which proved invaluable at Ironhold Keep.</p>\n\n<p>After the Frostpeak incident, Thorin has sworn vengeance against the Shadowmere cultists. His calm demeanor is beginning to crack under the weight of recent losses.</p>",
        "relation_changes": [
            {
                "action": "create",
                "relation": "enemy",
                "target_name": "Shadowmere Cult",
                "attitude": "vengeful",
                "reason": "Cultists ambushed Thorin at Frostpeak Pass, wounding him severely.",
            },
        ],
    },
]

# In-memory state (would be data/pending_changes.json in production)
PROPOSALS = []
for p in MOCK_NEW_ENTITIES:
    p["status"] = "pending"
    PROPOSALS.append(p)
for p in MOCK_UPDATES:
    p["status"] = "pending"
    PROPOSALS.append(p)


# ── HTML template (single-page app with inline JS/CSS) ─────────────────────

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kanka Wiki Review</title>
<style>
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --text-dim: #8b949e;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d29922;
    --cyan: #39d2c0;
    --magenta: #bc8cff;
    --blue: #58a6ff;
    --btn-primary: #238636;
    --btn-danger: #da3633;
    --btn-secondary: #30363d;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    height: 100vh;
    overflow: hidden;
  }

  /* Layout */
  .app { display: flex; flex-direction: column; height: 100vh; }

  .header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 { font-size: 16px; font-weight: 600; color: var(--cyan); }
  .header .stats { font-size: 13px; color: var(--text-dim); }

  .main { display: flex; flex: 1; overflow: hidden; }

  /* Sidebar */
  .sidebar {
    width: 280px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    flex-shrink: 0;
  }
  .sidebar-header {
    padding: 12px 16px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
  }
  .proposal-item {
    padding: 12px 16px;
    cursor: pointer;
    border-bottom: 1px solid var(--border);
    transition: background 0.15s;
  }
  .proposal-item:hover { background: #1c2128; }
  .proposal-item.active { background: #1f2a37; border-left: 3px solid var(--cyan); }
  .proposal-item .name { font-size: 14px; font-weight: 500; margin-bottom: 4px; }
  .proposal-item .meta { font-size: 11px; color: var(--text-dim); }
  .badge {
    display: inline-block;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    margin-right: 6px;
  }
  .badge-new { background: #1a2e3a; color: var(--cyan); }
  .badge-upd { background: #1c2833; color: var(--blue); }
  .badge-applied { background: #0d1f0d; color: var(--green); }
  .badge-rejected { background: #2a0d0d; color: var(--red); }

  /* Content area */
  .content { flex: 1; overflow-y: auto; padding: 24px; }

  .proposal-header { margin-bottom: 20px; }
  .proposal-header h2 { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
  .proposal-header .source { font-size: 13px; color: var(--text-dim); }
  .proposal-header .summary { font-size: 14px; color: var(--text); margin-top: 8px; padding: 10px; background: var(--surface); border-radius: 6px; border-left: 3px solid var(--magenta); }

  /* Diff */
  .diff-section { margin-bottom: 24px; }
  .diff-section h3 { font-size: 13px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .diff-container { background: #0d1117; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .diff-line { padding: 4px 12px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
  .diff-add { background: #0d1f0d; color: var(--green); border-left: 3px solid var(--green); }
  .diff-del { background: #2a0d0d; color: var(--red); border-left: 3px solid var(--red); text-decoration: line-through; opacity: 0.7; }
  .diff-empty { color: var(--text-dim); font-style: italic; padding: 16px; text-align: center; }

  /* Relations */
  .relations-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
  .relation-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; }
  .relation-card .rel-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .rel-action { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; text-transform: uppercase; }
  .rel-create { background: #0d2817; color: var(--green); }
  .rel-update { background: #2a2000; color: var(--yellow); }
  .rel-delete { background: #2a0d0d; color: var(--red); }
  .relation-card .rel-target { font-size: 14px; font-weight: 500; }
  .relation-card .rel-reason { font-size: 13px; color: var(--text-dim); margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border); }

  /* Warnings */
  .warning { background: #2a2000; border: 1px solid var(--yellow); border-radius: 6px; padding: 10px 14px; margin-bottom: 16px; font-size: 13px; color: var(--yellow); }
  .warning.critical { background: #2a0d0d; border-color: var(--red); color: var(--red); }

  /* Editor */
  .editor-section { margin-bottom: 24px; }
  .editor-toolbar { display: flex; gap: 8px; margin-bottom: 8px; }
  textarea.synopsis-editor {
    width: 100%;
    min-height: 200px;
    background: #0d1117;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-family: inherit;
    font-size: 14px;
    line-height: 1.6;
    padding: 12px;
    resize: vertical;
  }
  textarea.synopsis-editor:focus { outline: none; border-color: var(--cyan); }

  /* Action bar */
  .action-bar {
    position: sticky;
    bottom: 0;
    background: rgba(22, 27, 34, 0.95);
    backdrop-filter: blur(8px);
    border-top: 1px solid var(--border);
    padding: 16px 24px;
    display: flex;
    gap: 10px;
    align-items: center;
  }
  .btn {
    padding: 8px 16px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid var(--border);
    transition: all 0.15s;
  }
  .btn:hover { opacity: 0.85; transform: translateY(-1px); }
  .btn-primary { background: var(--btn-primary); color: white; border-color: #238636; }
  .btn-secondary { background: var(--btn-secondary); color: var(--text); }
  .btn-danger { background: var(--btn-danger); color: white; border-color: #da3633; }
  .btn-edit { background: transparent; color: var(--cyan); border-color: var(--cyan); }

  /* Toast */
  .toast {
    position: fixed;
    bottom: 80px;
    left: 50%;
    transform: translateX(-50%) translateY(100px);
    padding: 12px 24px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 500;
    opacity: 0;
    transition: all 0.3s ease;
    z-index: 100;
  }
  .toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
  .toast-success { background: #238636; color: white; }
  .toast-error { background: #da3633; color: white; }

  /* Empty state */
  .empty-state { text-align: center; padding: 60px 20px; color: var(--text-dim); }
  .empty-state h3 { font-size: 18px; margin-bottom: 8px; color: var(--text); }

  /* Progress bar */
  .progress-bar { height: 3px; background: var(--border); position: relative; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, var(--cyan), var(--blue)); transition: width 0.3s ease; }

  /* Edit mode indicator */
  .edit-mode-banner {
    background: #0d2837;
    border-bottom: 1px solid var(--cyan);
    padding: 8px 24px;
    font-size: 13px;
    color: var(--cyan);
    display: none;
  }
  .edit-mode-banner.visible { display: flex; justify-content: space-between; align-items: center; }

  /* Relation editor */
  .relation-editor { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
  .relation-editor select, .relation-editor input {
    background: #0d1117;
    border: 1px solid var(--border);
    color: var(--text);
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 13px;
    margin-right: 8px;
  }

  /* Keyboard shortcuts hint */
  .shortcuts { position: fixed; bottom: 70px; right: 20px; font-size: 11px; color: var(--text-dim); text-align: right; line-height: 1.8; }
  kbd { background: var(--surface); border: 1px solid var(--border); padding: 1px 6px; border-radius: 3px; font-family: monospace; }
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <h1>Kanka Wiki Review</h1>
    <div class="stats" id="stats"></div>
  </div>

  <div class="edit-mode-banner" id="editBanner">
    <span>Edit mode — modify the synopsis above, then save or cancel.</span>
    <button class="btn btn-primary" onclick="saveEdit()" style="padding:4px 12px;font-size:12px;">Save Changes</button>
    <button class="btn btn-secondary" onclick="cancelEdit()" style="padding:4px 12px;font-size:12px;">Cancel</button>
  </div>

  <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>

  <div class="main">
    <div class="sidebar" id="sidebar"></div>
    <div class="content" id="content"></div>
  </div>

  <div class="action-bar" id="actionBar">
    <button class="btn btn-primary" onclick="approveAll()">✓ Approve All</button>
    <button class="btn btn-secondary" onclick="approveSynopsisOnly()">📝 Synopsis Only</button>
    <button class="btn btn-danger" onclick="rejectCurrent()">✗ Reject</button>
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
let editing = false;

function getPending() { return proposals.filter(p => p.status === 'pending'); }
function updateStats() {
  const pending = getPending();
  const applied = proposals.filter(p => p.status === 'applied').length;
  const rejected = proposals.filter(p => p.status === 'rejected').length;
  document.getElementById('stats').textContent = `${pending.length} pending · ${applied} approved · ${rejected} rejected`;
  const total = proposals.length;
  const done = applied + rejected;
  const pct = total ? (done / total * 100) : 0;
  document.getElementById('progressFill').style.width = pct + '%';
}

function renderSidebar() {
  const sidebar = document.getElementById('sidebar');
  let html = '<div class="sidebar-header">Proposals (' + proposals.length + ')</div>';
  proposals.forEach((p, i) => {
    const isActive = i === selectedIndex ? ' active' : '';
    const kind = p.proposal_type === 'new_entity' ? 'NEW' : 'UPD';
    const badgeClass = p.proposal_type === 'new_entity' ? 'badge-new' : 'badge-upd';
    const statusBadge = {pending:'', applied:' ✓', rejected:' ✗'}[p.status] || '';
    html += `<div class="proposal-item${isActive}" onclick="selectProposal(${i})">
      <div class="name"><span class="badge ${badgeClass}">${kind}</span>${p.entity_name}${statusBadge}</div>
      <div class="meta">${p.source_journal}</div>
    </div>`;
  });
  sidebar.innerHTML = html;
}

function renderContent() {
  const content = document.getElementById('content');
  if (selectedIndex === null || selectedIndex >= proposals.length) {
    content.innerHTML = '<div class="empty-state"><h3>No proposal selected</h3>Select one from the sidebar to review.</div>';
    return;
  }

  const p = proposals[selectedIndex];
  let html = '';

  // Header
  html += `<div class="proposal-header">`;
  if (p.proposal_type === 'new_entity') {
    html += `<h2>New ${p.suggested_type}: ${p.entity_name}</h2>`;
  } else {
    const statusIcon = {pending:'○', applied:'<span style="color:var(--green)">✓</span>', rejected:'<span style="color:var(--red)">✗</span>'}[p.status] || '○';
    html += `<h2>${statusIcon} ${p.entity_name} <span style="font-weight:400;color:var(--text-dim);font-size:16px">(${p.entity_kind})</span></h2>`;
  }
  html += `<div class="source">← ${p.source_journal}</div>`;
  if (p.change_summary) {
    html += `<div class="summary">${p.change_summary}</div>`;
  }
  html += `</div>`;

  // Warnings
  const oldIds = new Set((p.previous_entry || '').match(/\\[entity:(\\d+)\\]/g)?.map(s => s.match(/(\\d+)/)[1]) || []);
  const newIds = new Set((p.proposed_entry || '').match(/\\[entity:(\\d+)\\]/g)?.map(s => s.match(/(\\d+)/)[1]) || []);
  const dropped = oldIds.difference ? [...oldIds].filter(x => !newIds.has(x)) : [];
  if (dropped.length) {
    html += `<div class="warning critical">!! ${dropped.length} mention link(s) missing from new version!</div>`;
  }

  // Diff or draft
  html += '<div class="diff-section"><h3>Synopsis</h3><div class="diff-container">';
  if (p.proposal_type === 'new_entity') {
    const clean = p.draft_entry.replace(/<[^>]+>/g, '');
    html += `<div class="diff-line">${escapeHtml(clean)}</div>`;
  } else {
    // Simple diff: split into lines and show changes
    const prevLines = stripHtml(p.previous_entry).split('\\n');
    const newLines = p.proposed_entry.split('\\n');
    // Use a simple line-by-line comparison
    const maxLen = Math.max(prevLines.length, newLines.length);
    for (let i = 0; i < maxLen; i++) {
      if (i >= prevLines.length) {
        html += `<div class="diff-line diff-add">${escapeHtml(newLines[i])}</div>`;
      } else if (i >= newLines.length) {
        html += `<div class="diff-line diff-del">${escapeHtml(prevLines[i])}</div>`;
      } else if (prevLines[i] !== newLines[i]) {
        html += `<div class="diff-line diff-del">${escapeHtml(prevLines[i])}</div>`;
        html += `<div class="diff-line diff-add">${escapeHtml(newLines[i])}</div>`;
      } else {
        html += `<div class="diff-line" style="padding-left:20px">${escapeHtml(prevLines[i])}</div>`;
      }
    }
  }
  html += '</div></div>';

  // Relation changes
  if (p.relation_changes && p.relation_changes.length) {
    html += '<div class="diff-section"><h3>Relationship Changes</h3><div class="relations-list">';
    p.relation_changes.forEach(rc => {
      const actionClass = rc.action === 'create' ? 'rel-create' : rc.action === 'update' ? 'rel-update' : 'rel-delete';
      html += `<div class="relation-card">
        <div class="rel-header">
          <span class="rel-action ${actionClass}">${rc.action}</span>
          <span class="rel-target">${p.entity_name} --${rc.relation}--> ${rc.target_name}</span>
        </div>
        <div style="font-size:12px;color:var(--text-dim)">Attitude: ${rc.attitude || 'N/A'}</div>
        <div class="rel-reason">Reason: ${escapeHtml(rc.reason)}</div>
      </div>`;
    });
    html += '</div></div>';
  }

  // Status indicator
  if (p.status !== 'pending') {
    const statusColor = p.status === 'applied' ? 'var(--green)' : 'var(--red)';
    html += `<div style="text-align:center;padding:16px;color:${statusColor};font-weight:600;font-size:14px">Status: ${p.status.toUpperCase()}</div>`;
  }

  content.innerHTML = html;
}

function selectProposal(i) {
  selectedIndex = i;
  if (editing) cancelEdit();
  renderSidebar();
  renderContent();
}

function escapeHtml(text) {
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function stripHtml(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = html || '';
  return tmp.textContent || tmp.innerText || '';
}

// ── Actions ────────────────────────────────────────────────────────────────

async function updateProposalStatus(index, status) {
  try {
    const res = await fetch(`/api/proposals/${index}/status`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status})
    });
    if (!res.ok) throw new Error('Failed to update');
  } catch (e) {
    showToast('Error saving change', 'error');
  }
}

function approveAll() {
  if (selectedIndex === null) return;
  proposals[selectedIndex].status = 'applied';
  updateProposalStatus(selectedIndex, 'approved_all');
  renderSidebar();
  renderContent();
  showToast('✓ Approved all', 'success');
}

function approveSynopsisOnly() {
  if (selectedIndex === null) return;
  proposals[selectedIndex].status = 'applied';
  updateProposalStatus(selectedIndex, 'approved_synopsis_only');
  renderSidebar();
  renderContent();
  showToast('📝 Synopsis approved', 'success');
}

function rejectCurrent() {
  if (selectedIndex === null) return;
  proposals[selectedIndex].status = 'rejected';
  updateProposalStatus(selectedIndex, 'rejected');
  renderSidebar();
  renderContent();
  showToast('✗ Rejected', 'error');
}

// ── Editor ─────────────────────────────────────────────────────────────────

function startEdit() {
  if (selectedIndex === null || proposals[selectedIndex].proposal_type !== 'update') return;
  editing = true;
  const p = proposals[selectedIndex];
  const content = document.getElementById('content');
  
  // Insert editor before the action bar area
  let html = '<div class="editor-section"><h3 style="font-size:13px;color:var(--cyan);margin-bottom:8px;">Edit Synopsis</h3>';
  html += `<textarea class="synopsis-editor" id="synopsisEditor">${escapeHtml(stripHtml(p.proposed_entry))}</textarea>`;
  html += '</div>';
  
  // Insert at the top of content
  const existing = content.innerHTML;
  content.innerHTML = html + '<hr style="border:none;border-top:1px solid var(--border);margin:20px 0;">' + existing;
  
  document.getElementById('editBanner').classList.add('visible');
  document.getElementById('synopsisEditor').focus();
}

function saveEdit() {
  if (selectedIndex === null) return;
  const editor = document.getElementById('synopsisEditor');
  proposals[selectedIndex].proposed_entry = '<p>' + editor.value.trim().replace(/\\n/g, '</p><p>') + '</p>';
  editing = false;
  cancelEdit(); // hides banner and re-renders
  renderContent();
  showToast('Changes saved', 'success');
}

function cancelEdit() {
  editing = false;
  document.getElementById('editBanner').classList.remove('visible');
  renderContent();
}

// ── Toast ──────────────────────────────────────────────────────────────────

function showToast(message, type) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'toast toast-' + type + ' show';
  setTimeout(() => toast.classList.remove('show'), 2000);
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (editing || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
  
  switch(e.key.toLowerCase()) {
    case 'n': selectProposal(Math.min(selectedIndex + 1, proposals.length - 1)); break;
    case 'p': selectProposal(Math.max(selectedIndex - 1, 0)); break;
    case 'e': startEdit(); break;
    case 'a': approveAll(); break;
    case 's': approveSynopsisOnly(); break;
    case 'r': rejectCurrent(); break;
    case 'q': window.close(); break;
  }
});

// ── Init ───────────────────────────────────────────────────────────────────

updateStats();
renderSidebar();
if (proposals.length > 0) {
  selectProposal(0);
} else {
  document.getElementById('content').innerHTML = '<div class="empty-state"><h3>No pending proposals</h3>Run sync_pipeline first to generate proposals.</div>';
}

// Refresh stats periodically
setInterval(updateStats, 5000);
</script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, PROPOSALS=PROPOSALS)


@app.route('/api/proposals/<int:index>/status', methods=['POST'])
def update_status(index):
    data = request.get_json()
    status = data.get('status')
    if 0 <= index < len(PROPOSALS) and status in ('approved_all', 'approved_synopsis_only', 'rejected'):
        PROPOSALS[index]['status'] = {
            'approved_all': 'applied',
            'approved_synopsis_only': 'applied',
            'rejected': 'rejected',
        }[status]
        return jsonify({'ok': True, 'proposal': PROPOSALS[index]})
    return jsonify({'error': 'Invalid'}), 400


@app.route('/api/proposals/<int:index>/edit', methods=['POST'])
def edit_proposal(index):
    data = request.get_json()
    if 0 <= index < len(PROPOSALS):
        PROPOSALS[index]['proposed_entry'] = data.get('entry', '')
        return jsonify({'ok': True, 'proposal': PROPOSALS[index]})
    return jsonify({'error': 'Invalid'}), 400


if __name__ == '__main__':
    print("Starting Kanka Wiki Review UI...")
    print("Open http://127.0.0.1:5555 in your browser")
    app.run(host='127.0.0.1', port=5555, debug=False)
