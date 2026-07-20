let selectedIndex = null;
let currentTab = 'new'; // default tab
let editingField = null; // 'synopsis' or 'name' for new entities
let editingOriginal = ''; // original text when entering edit mode (for escape-to-cancel)
let currentSyncJob = null; // {job_id, status, output}
let syncEventSource = null; // EventSource reference for proper cleanup

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
    '<button class="tab-btn ' + (currentTab === "new" ? "active" : "inactive") + '" data-tab="new" onclick="switchTab(\'new\')">New</button>' +
    '<button class="tab-btn ' + (currentTab === "reviewed" ? "active" : "inactive") + '" data-tab="reviewed" onclick="switchTab(\'reviewed\')">Reviewed</button>' +
    '<button class="tab-btn ' + (currentTab === "sync" ? "active" : "inactive") + '" data-tab="sync" onclick="switchTab(\'sync\')">Sync</button>' +
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
    var sourceLink = p._source_journal_url ? '<a href="' + p._source_journal_url + '" target="_blank" rel="noopener noreferrer">' : '';
    var sourceClose = p._source_journal_url ? '</a>' : '';
    html += '<div class="source">&larr; ' + sourceLink + escapeJsHtml(p.source_journal) + sourceClose + '</div></div>';
  } else {
    var statusIcon = {pending:'&#9675;', applied:'<span style="color:var(--green)">&#10003;</span>', rejected:'<span style="color:var(--red)">&#10007;</span>'}[p.status] || '&#9675;';
    html += '<div class="proposal-header"><h2>' + statusIcon + ' ' + escapeJsHtml(p.entity_name) + ' <span style="font-weight:400;color:var(--text-dim);font-size:16px">(' + p.entity_kind + ')</span></h2>';
    var sourceLink = p._source_journal_url ? '<a href="' + p._source_journal_url + '" target="_blank" rel="noopener noreferrer">' : '';
    var sourceClose = p._source_journal_url ? '</a>' : '';
    html += '<div class="source">&larr; ' + sourceLink + escapeJsHtml(p.source_journal) + sourceClose + '</div>';
    if (p.change_summary) { html += '<div class="summary">' + escapeJsHtml(p.change_summary) + '</div>'; }
    html += '</div>';

    // Regenerate link for update proposals
    html += '<div style="padding:8px 0">' +
      '<button class="btn" onclick="regenerateProposal()" style="font-size:12px;padding:4px 12px;">&#x21bb; Regenerate Proposal</button>' +
      ' <span style="font-size:11px;color:var(--text-dim)">or press [g]</span></div>';
  }

  // Warnings for dropped mentions (all proposal types)
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

  // Truncation warning with regenerate button
  var isTruncated = p.truncated === true || (p.change_summary && p.change_summary.indexOf('[TRUNCATED:') !== -1);
  if (isTruncated && p.proposal_type === 'update') {
    html += '<div class="warning" id="truncationWarning">' +
      '&#9888; This proposal may be truncated — the LLM hit its token limit and output was cut off. ' +
      '<button class="btn" onclick="regenerateProposal()" style="padding:4px 12px;font-size:12px;margin-left:12px;">Regenerate (higher max_tokens)</button>' +
      '</div>';
  } else if (isTruncated && p.proposal_type === 'new_entity') {
    html += '<div class="warning">&#9888; This new-entity suggestion may be truncated — the LLM output was cut off. Edit manually to fix.</div>';
  }

  // Info loss warning: proposed synopsis is much shorter than previous
  var prevLen = (p.previous_entry || '').length;
  var newLen = (p.proposed_entry || '').length;
  if (prevLen > 200 && newLen < 0.65 * prevLen) {
    html += '<div class="warning critical">&#9888; WARNING: The proposed synopsis (' + newLen + ' chars) is much shorter than the previous version (' + prevLen + ' chars). This likely means old content was summarized/condensed instead of preserved. Review carefully.</div>';
  }

  // Synopsis / draft editing area
  html += '<div class="diff-section"><h3>' + (p.proposal_type === 'new_entity' ? 'Draft Synopsis' : 'Synopsis') + '</h3><div class="diff-container">';
  if (editingField === 'synopsis') {
    var currentText = p.proposal_type === 'new_entity' ? p.draft_entry : p.proposed_entry;
    html += '<textarea class="synopsis-editor" id="synopsisEditor">' + escapeHtmlForTextarea(stripHtml(currentText) || '') + '</textarea>';
  } else {
    if (p.proposal_type === 'new_entity') {
      html += '<div class="diff-line" style="cursor:pointer" onclick="startEdit(\'synopsis\')">' + renderJournalLinks(p.draft_entry || '(none)') + '</div>';
      html += '<div style="padding:4px 12px;font-size:11px;color:var(--text-dim)">Click to edit</div>';
    } else {
      var prevLines = stripHtml(p.previous_entry).split('\\n');
      var newLines = (p.proposed_entry || '').split('\\n');
      var maxLen = Math.max(prevLines.length, newLines.length);
      for (var i = 0; i < maxLen; i++) {
        if (i >= prevLines.length) {
          html += '<div class="diff-line diff-add">' + renderJournalLinks(newLines[i]) + '</div>';
        } else if (i >= newLines.length) {
          html += '<div class="diff-line diff-del">' + escapeJsHtml(prevLines[i]) + '</div>';
        } else if (prevLines[i] !== newLines[i]) {
          html += '<div class="diff-line diff-del">' + escapeJsHtml(prevLines[i]) + '</div>';
          html += '<div class="diff-line diff-add">' + renderJournalLinks(newLines[i]) + '</div>';
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

  // Show/hide regenerate button for update proposals
  var regenBtn = document.getElementById('regenerateBtn');
  if (regenBtn) {
    regenBtn.style.display = (p.proposal_type === 'update') ? '' : 'none';
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
  var escaped = (escapeHtml(str || '')).replace(/\\/g, '\\').replace(/'/g, "\\'");
  return escaped.replace(/\r\n/g, '<br>').replace(/\r/g, '<br>').replace(/\n/g, '<br>')
    .replace(/\\n/g, '\\n').replace(/\\r/g, '\\r');
}


function escapeHtmlForTextarea(str) {
  return escapeHtml(str || '');
}

function escapeJs(str) {
  return (str || '').replace(/\\/g, '\\\\\\\\').replace(/'/g, "\\'").replace(/"/g, '\\\\"').replace(/\n/g, '\\\\n').replace(/\r/g, '\\\\r').replace(/\\//g, '\\/');
}

function stripHtml(html) {
  var tmp = document.createElement('div');
  tmp.innerHTML = html || '';
  return tmp.textContent || tmp.innerText || '';
}

function renderJournalLinks(text) {
  return escapeJsHtml(text);
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

async function approveAll() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  showLoading('Approving all proposals...', 'This may take a moment while changes are synced to Kanka.');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_all'})
    .then(function(data) {
      hideLoading();
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync) {
        if (data.sync.warnings && data.sync.warnings.length > 0) {
          showToast('Synced with warnings: ' + data.sync.message, 'warning');
        } else if (data.sync.ok) {
          showToast('Synced to Kanka: ' + data.sync.message, 'success');
        } else {
          showToast('Kanka sync failed: ' + data.sync.message, 'error');
        }
      } else {
        showToast('Approved all', 'success');
      }
    })
    .catch(function() { hideLoading(); });
}

async function approveSynopsisOnly() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  showLoading('Syncing synopsis to Kanka...', 'Updating the wiki entry.');
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_synopsis_only'})
    .then(function(data) {
      hideLoading();
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync) {
        if (data.sync.warnings && data.sync.warnings.length > 0) {
          showToast('Synopsis synced with warnings: ' + data.sync.message, 'warning');
        } else if (data.sync.ok) {
          showToast('Synopsis synced to Kanka: ' + data.sync.message, 'success');
        } else {
          showToast('Kanka sync failed: ' + data.sync.message, 'error');
        }
      } else {
        showToast('Synopsis approved', 'success');
      }
    })
    .catch(function() { hideLoading(); });
}

async function rejectCurrent() {
  if (selectedIndex === null) return;
  var oldIndex = selectedIndex;
  if (editingField) await saveEdit();
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'rejected'})
    .then(function(data) { hideLoading(); if (data) { proposals[selectedIndex] = data.proposal; _advance(oldIndex); showToast('Rejected', 'error'); } });
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

// ── Truncation regeneration ────────────────────────────────────────────────

async function regenerateProposal() {
  if (selectedIndex === null) return;
  var p = proposals[selectedIndex];
  if (!p || p.proposal_type !== 'update') { showToast('Only update proposals can be regenerated', 'error'); return; }

  showLoading('Regenerating proposal...', 'Contacting LLM for fresh output — this may take a moment.');

  var result = await apiCall('/api/proposals/' + selectedIndex + '/regenerate', 'POST');
  hideLoading();
  if (!result) return;

  if (result.ok) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Regeneration successful — proposal updated with fresh LLM output.', 'success');
  } else {
    var msg = result.error || 'Regeneration failed';
    showToast(msg, 'error');
  }
}

// ── Toast ──────────────────────────────────────────────────────────────────

function showToast(message, type) {
  var toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'toast toast-' + type + ' show';
  setTimeout(function(){ toast.classList.remove('show'); }, 7000);
}

// ── Loading overlay ─────────────────────────────────────────────────────────

var _loadingRefreshInterval = null;

function showLoading(message, detail) {
  var overlay = document.getElementById('loadingOverlay');
  var textEl = document.getElementById('loadingText');
  var detailEl = document.getElementById('loadingDetail');
  overlay.classList.add('visible');
  if (textEl) textEl.textContent = message || 'Processing...';
  if (detailEl) detailEl.textContent = detail || '';

  // Auto-refresh the loading status from server while sync is running
  if (_loadingRefreshInterval) clearInterval(_loadingRefreshInterval);
  _loadingRefreshInterval = setInterval(function() {
    fetch('/api/sync/status')
      .then(function(r){ return r.json(); })
      .then(function(data) {
        if (data && data.active && data.jobs && data.jobs.length > 0) {
          var job = data.jobs[0];
          var statusText = 'Status: ' + job.status.toUpperCase();
          if (job.output_lines_count > 0) {
            statusText += ' — ' + job.output_lines_count + ' lines';
          }
          if (detailEl) detailEl.textContent = statusText;
        } else {
          clearInterval(_loadingRefreshInterval);
          _loadingRefreshInterval = null;
        }
      })
      .catch(function() {});
  }, 1000);
}

function hideLoading() {
  var overlay = document.getElementById('loadingOverlay');
  overlay.classList.remove('visible');
  if (_loadingRefreshInterval) {
    clearInterval(_loadingRefreshInterval);
    _loadingRefreshInterval = null;
  }
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
    case 'e': e.preventDefault(); if (!editingField) startEdit('synopsis'); break;
    case 'a': approveAll(); break;
    case 's': approveSynopsisOnly(); break;
    case 'r': rejectCurrent(); break;
    case 'g': regenerateProposal(); break;
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
    // Cancel: close EventSource and notify server to stop subprocess
    if (syncEventSource) {
      syncEventSource.close();
      syncEventSource = null;
    }
    var jobId = currentSyncJob.job_id;
    currentSyncJob = null;
    renderContent();
    // Notify server to terminate the running process
    apiCall('/api/sync/cancel?job_id=' + encodeURIComponent(jobId), 'POST')
      .catch(function() { /* best-effort */ });
    return;
  }

  var result = await apiCall('/api/sync/run', 'POST');
  if (!result || !result.job_id) return;

  currentSyncJob = { job_id: result.job_id, status: 'running', output: '', outputLines: 0 };
  renderContent();

  // Connect to SSE stream
  syncEventSource = new EventSource('/api/sync/output?job_id=' + result.job_id);

  syncEventSource.addEventListener('message', function(e) {
    var data = JSON.parse(e.data);
    if (data.type === 'output') {
      currentSyncJob.output += data.text + '\\n';
      currentSyncJob.outputLines++;
      updateSyncIndicator();
      var pre = document.getElementById('syncOutput');
      if (pre) {
        pre.textContent = currentSyncJob.output;
        pre.scrollTop = pre.scrollHeight;
      }
    }
  });

  syncEventSource.addEventListener('status', function(e) {
    var data = JSON.parse(e.data);
    currentSyncJob.status = data.status;
    renderContent();
    updateSyncIndicator();
  });

  syncEventSource.addEventListener('end', function() {
    syncEventSource.close();
    syncEventSource = null;
    currentSyncJob = null;
    renderContent();
    updateSyncIndicator();
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

// ── Background sync indicator (header bar when pipeline runs) ───────────────

function updateSyncIndicator() {
  var indicator = document.getElementById('syncIndicator');
  if (!indicator) return;
  if (currentSyncJob && currentSyncJob.status === 'running') {
    var statusText = 'Sync: Running';
    if (currentSyncJob.outputLines > 0) {
      statusText += ' — ' + currentSyncJob.outputLines + ' lines';
    }
    document.getElementById('syncIndicatorText').textContent = statusText;
    indicator.style.display = 'flex';
  } else {
    indicator.style.display = 'none';
  }
}
