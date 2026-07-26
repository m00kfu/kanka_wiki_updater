let selectedIndex = null;
let currentTab = 'new'; // default tab
let editingField = null; // 'synopsis' or 'name' for new entities
let editingOriginal = ''; // original text when entering edit mode (for escape-to-cancel)
let diffViewMode = 'unified'; // or 'side-by-side'
let currentSyncJob = null; // {job_id, status}
let syncEventSource = null; // EventSource reference for proper cleanup
let _syncEnded = false;     // guard: prevent auto-reconnect after 'end' event

// ── Relation type autocomplete state ───────────────────────
var knownRelationTypes = [];   // global cache for autocomplete
var similarTypesSet = new Set(); // union of all similar_types from enrichment

(function initDatalist() {
  var dl = document.createElement('datalist');
  dl.id = 'relationTypeDatalist';
  document.body.appendChild(dl);
})();

function populateTypeDatalist(types, allSimilar) {
  var dl = document.getElementById('relationTypeDatalist');
  if (!dl) return;
  knownRelationTypes = types || [];
  similarTypesSet = new Set();
  (allSimilar || []).forEach(function(s) { if (s) similarTypesSet.add(s); });

  var seen = {};
  dl.innerHTML = '';
  knownRelationTypes.forEach(function(t) {
    dl.appendChild(makeOption(t));
    seen[t] = true;
  });
  similarTypesSet.forEach(function(s) {
    if (!seen[s]) { dl.appendChild(makeOption(s)); seen[s] = true; }
  });
}

function makeOption(value) {
  var opt = document.createElement('option');
  opt.value = value.replace(/"/g, '&quot;').replace(/</g, '&lt;');
  return opt;
}

async function loadKnownTypes() {
  try {
    var result = await apiCall('/api/known-relation-types', 'GET');
    if (result) {
      populateTypeDatalist(result.types || [], result.similar_types || []);
    }
  } catch(e) {
    /* empty datalist is fine — no race condition */
  }
}

// ── Sync progress state (live entity tracking) ─────────────────────────────
let syncEntities = {};   // key: "journal_name::entity_name" → {name, journal_name, status, error_message}
let syncJournalOrder = []; // ordered array of journal names for rendering group order

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
    // New entities first, then updates — preserve insertion order within each group
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
    if (filtered[f].status === 'applied') { statusBadge = '<span style="color:var(--green);font-weight:700;">&#10003;</span>'; }
    else if (filtered[f].status === 'rejected') { statusBadge = '<span style="color:var(--red);font-weight:700;">&#10007;</span>'; }
    var placeholderClass = (filtered[f]._sync_placeholder) ? ' sync-placeholder' : '';

    html += '<div class="proposal-item' + isActive + placeholderClass + '" onclick="selectProposal(' + origIdx + ')">' +
      '<div class="name"><span class="badge ' + badgeClass + '">' + kind + '</span>' + escapeJsHtml(filtered[f].entity_name) + statusBadge + '</div>' +
      '<div class="meta">' + escapeJsHtml(filtered[f].source_journal || '') + '</div></div>';
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

  // ── Header ────────────────────────────────────────────────
  if (p.proposal_type === 'new_entity') {
    html += '<div class="proposal-header"><h2>New <span style="font-weight:400;color:var(--text-dim);font-size:16px">' + escapeJsHtml(p.suggested_type) + '</span>: <span id="entityNameDisplay">' + escapeJsHtml(p.entity_name) + '</span></h2>';
    var sourceLink = p._source_journal_url ? '<a href="' + p._source_journal_url + '" target="_blank" rel="noopener noreferrer">' : '';
    var sourceClose = p._source_journal_url ? '</a>' : '';
    html += '<div class="source">&#8592; ' + sourceLink + escapeJsHtml(p.source_journal) + sourceClose + '</div></div>';
  } else {
    var statusIcon = {'pending':'&#9675;', 'applied':'<span style="color:var(--green);font-weight:700;">&#10003;</span>', 'rejected':'<span style="color:var(--red);font-weight:700;">&#10007;</span>'}[p.status] || '&#9675;';
    html += '<div class="proposal-header"><h2>' + statusIcon + ' ' + escapeJsHtml(p.entity_name) + ' <span style="font-weight:400;color:var(--text-dim);font-size:16px">(' + p.entity_kind + ')</span></h2>';
    var sourceLink = p._source_journal_url ? '<a href="' + p._source_journal_url + '" target="_blank" rel="noopener noreferrer">' : '';
    var sourceClose = p._source_journal_url ? '</a>' : '';
    html += '<div class="source">&#8592; ' + sourceLink + escapeJsHtml(p.source_journal) + sourceClose + '</div>';
    if (p.change_summary) { html += '<div class="summary">' + escapeJsHtml(p.change_summary) + '</div>'; }
    html += '</div>';

    // Regenerate link for update proposals
    html += '<div style="padding:8px 0">' +
      '<button class="btn" onclick="regenerateProposal()" style="font-size:12px;padding:4px 12px;">&#8635; Regenerate Proposal</button></div>';
  }

  // ── Warnings ──────────────────────────────────────────────
  var prevEntry = p.previous_entry || '';
  var proposedEntry = p.proposed_entry || '';
  if (prevEntry && proposedEntry) {
    var oldIds = (prevEntry.match(/\[entity:(\d+)\]/g) || []).map(function(s){ return s.match(/(\d+)/)[1]; });
    var newIds = (proposedEntry.match(/\[entity:(\d+)\]/g) || []).map(function(s){ return s.match(/(\d+)/)[1]; });
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

  // ── Synopsis / draft editing area ─────────────────────────
  var synopsisHeading = p.proposal_type === 'new_entity' ? 'Draft Synopsis' : 'Synopsis';
  var toggleBtn = '';
  if (p.proposal_type !== 'new_entity') {
    var isSideBySide = diffViewMode === 'side-by-side';
    toggleBtn = '<button class="diff-view-toggle" onclick="toggleDiffView()" title="Switch to side-by-side view">' +
      (isSideBySide ? '&#9775; Unified' : '&#9642; Side-by-Side') + '</button>';
  }
  html += '<div class="diff-section"><h3>' + synopsisHeading + ' ' + toggleBtn + '</h3><div class="diff-container">';
  if (editingField === 'synopsis') {
    var currentText = p.proposal_type === 'new_entity' ? p.draft_entry : p.proposed_entry;
    html += '<textarea class="synopsis-editor" id="synopsisEditor">' + escapeHtmlForTextarea(stripHtml(currentText || '')) + '</textarea>';
  } else {
    if (p.proposal_type === 'new_entity') {
      html += '<div class="diff-line" style="cursor:pointer;padding:16px;min-height:60px;" onclick="startEdit(\'synopsis\')">' + renderJournalLinks(p.draft_entry || '(none)') + '</div>';
      html += '<div style="padding:4px 14px;font-size:11px;color:var(--text-dim)">Click to edit</div>';
    } else if (diffViewMode === 'side-by-side') {
      html += renderSideBySideDiff(p.previous_entry, p.proposed_entry);
    } else {
      html += renderUnifiedDiff(p.previous_entry, p.proposed_entry);
    }
  }
  html += '</div></div>';

  // ── Relation changes (update proposals only) ──────────────
  if (p.relation_changes && p.relation_changes.length > 0) {
    html += '<div class="diff-section"><h3>Relationship Changes</h3><div class="relations-list">';
    for (var rcIdx = 0; rcIdx < p.relation_changes.length; rcIdx++) {
      var rc = p.relation_changes[rcIdx];
      var actionClass = rc.action === 'create' ? 'rel-create' : rc.action === 'update' ? 'rel-update' : 'rel-delete';
      var relAttrEsc = (rc.relation || '').replace(/"/g, '&quot;').replace(/</g, '&lt;');
      var badgeHtml = '';
      if (rc._type_status === 'new_suggested') {
        badgeHtml = '<span class="badge-new-type" style="margin-left:4px;">NEW</span>';
      }

      html += '<div class="relation-card" id="rel-' + rcIdx + '">' +
        '<div class="rel-header">' +
          '<span class="rel-action ' + actionClass + '">' + escapeJsHtml(rc.action) + '</span>' +
          '<span class="rel-target">' +
            escapeJsHtml(p.entity_name) + ' --' +
            '<input type="text" class="rel-type-input" data-index="' + rcIdx + '" value="' + relAttrEsc + '" list="relationTypeDatalist">' +
            badgeHtml +
            '--> ' + escapeJsHtml(rc.target_name) +
          '</span>' +
          '<button class="btn rel-save-btn" data-index="' + rcIdx + '" style="display:none;padding:2px 8px;font-size:11px;">Save</button>' +
          '<button class="btn" onclick="deleteRelation(' + rcIdx + ')" style="padding:2px 8px;font-size:11px;margin-left:auto;">Delete</button>' +
        '</div>' +
        '<div style="font-size:12px;color:var(--text-dim)">Attitude: ' + escapeJsHtml(rc.attitude || 'N/A') + '</div>' +
        '<div class="rel-reason">Reason: ' + escapeJsHtml(rc.reason) + '</div></div>';
    }
    html += '</div>';

    // Add new relation form
    html += '<div class="add-relation-form">' +
      '<input type="text" id="newRelTarget" placeholder="Target name">' +
      '<select id="newRelAction"><option value="create">create</option><option value="update">update</option></select>' +
      '<input type="text" id="newRelRelation" list="relationTypeDatalist" placeholder="Relation (e.g. ally)">' +
      '<input type="text" id="newRelAttitude" placeholder="Attitude">' +
      '<button onclick="addRelation()">Add</button></div>';
    html += '</div>';
  }

  // ── Status indicator ──────────────────────────────────────
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

  // ── Wire up relation type inputs (show Save on change) ───
  var relInputs = content.querySelectorAll('.rel-type-input');
  for (var i = 0; i < relInputs.length; i++) {
    (function(input) {
      var card = input.closest('.relation-card');
      var saveBtn = card.querySelector('.rel-save-btn');

      input.addEventListener('input', function() {
        var origValue = input.defaultValue;
        var currentVal = input.value.trim();
        var isNewType = currentVal && !knownRelationTypes.includes(currentVal);
        var hasChanges = currentVal !== origValue || isNewType;

        saveBtn.style.display = hasChanges ? '' : 'none';
      });

      saveBtn.addEventListener('click', function() {
        saveRelationEdit(parseInt(input.dataset.index, 10));
      });
    })(relInputs[i]);
  }
}

function renderSyncContent(content) {
  if (currentTab !== 'sync') return;
  var jobStatus = currentSyncJob ? currentSyncJob.status : 'idle';

  // Build sync header with status badge and controls
  var btnText, btnClass;
  if (jobStatus === 'running') {
    btnText = 'Cancel Sync';
    btnClass = 'btn-danger';
  } else if (!currentSyncJob || jobStatus === 'completed' || jobStatus === 'error') {
    btnText = 'Run Sync';
    btnClass = 'btn-primary';
  } else if (jobStatus === 'cancelled') {
    btnText = 'Re-run Sync';
    btnClass = 'btn-primary';
  } else {
    btnText = 'Restart Sync';
    btnClass = 'btn-primary';
  }

  var html = '<div class="sync-progress-container">' +
    '<div class="sync-header">' +
      '<span class="sync-status-badge ' + jobStatus + '">' +
        '<span class="badge-dot"></span> ' + (jobStatus || 'idle').toUpperCase() +
      '</span>' +
      '<span class="sync-elapsed" id="syncElapsed"></span>' +
      '<span class="sync-count-summary" id="syncCountSummary"></span>' +
      '<button class="btn ' + btnClass + '" onclick="runSync()">' + btnText + '</button>' +
    '</div>';

  if (jobStatus === 'idle') {
    html += '<div style="padding:40px 20px;text-align:center;color:var(--text-dim);font-size:13px;">' +
      '<p>Run the sync pipeline to scan journal entries and extract entity updates.</p>' +
      '<p style="margin-top:8px;font-size:12px;">Entities will appear here in real-time as they are processed.</p></div>';
    content.innerHTML = html;
    return;
  }

  // Render journal groups with entity cards
  var order = syncJournalOrder || [];

  // If no explicit order yet, fall back to insertion order from syncEntities
  if (order.length === 0) {
    for (var k in syncEntities) {
      if (k.indexOf('::') !== -1 && !syncEntities[k]._meta) {
        var jn = syncEntities[k].journal_name;
        if (jn && order.indexOf(jn) === -1) order.push(jn);
      }
    }
  }

  for (var g = 0; g < order.length; g++) {
    var journalName = order[g];
    // Collect entities for this journal
    var entities = [];
    for (var k2 in syncEntities) {
      if (!syncEntities[k2]._meta && syncEntities[k2].journal_name === journalName) {
        entities.push(syncEntities[k2]);
      }
    }

    var doneCount = 0;
    for (var eIdx = 0; eIdx < entities.length; eIdx++) {
      if (entities[eIdx].status === 'done') doneCount++;
    }

    html += '<div class="journal-group">' +
      '<div class="journal-group-header">&#128203; ' + escapeJsHtml(journalName) +
        '<span class="group-progress">(' + entities.length + ' entity' + (entities.length !== 1 ? 'ies' : '') + ', ' + doneCount + '/' + entities.length + ' done)</span>' +
      '</div>' +
      '<div class="entity-cards">';

    for (var e = 0; e < entities.length; e++) {
      var ent = entities[e];
      var iconMap = {'pending':'&#9675;', 'processing':'&#8635;', 'done':'&#10003;', 'skipped':'&#8617;', 'error':'&#10007;'};
      var icon = iconMap[ent.status] || '&#9675;';

      var errorHtml = '';
      if (ent.error_message) {
        var errId = 'err-' + Math.random().toString(36).substr(2, 8);
        errorHtml = '<span class="error-indicator" data-error-id="' + errId + '" onclick="toggleErrorDetail(event)">&#9888;</span>' +
            '<div class="error-detail" id="' + errId + '" style="display:none;">' +
              escapeJsHtml(ent.error_message) +
              ' <span class="error-close" onclick="closeErrorDetail(\'' + errId.replace(/'/g, "\\'") + '\')" style="cursor:pointer;margin-left:8px;color:var(--text-dim);">&#10007;</span>' +
            '</div>';
      }

      html += '<div class="entity-card status-' + ent.status + '">' +
          '<span class="entity-icon">' + icon + '</span>' +
          escapeJsHtml(ent.name) + errorHtml +
        '</div>';
    }

    html += '</div></div>'; // close entity-cards, journal-group
  }

  // If there are no entities yet but sync is running, show a loading hint
  if (order.length === 0) {
    html += '<div class="sync-empty-hint">Waiting for first entities...</div>';
  }

  html += '</div>'; // close sync-progress-container
  content.innerHTML = html;

  // Update count summary
  var totalCount = 0, totalDone = 0, totalSkipped = 0;
  for (var k3 in syncEntities) {
    if (!syncEntities[k3]._meta && syncEntities[k3].journal_name) {
      totalCount++;
      if (syncEntities[k3].status === 'done') totalDone++;
      else if (syncEntities[k3].status === 'skipped') totalSkipped++;
    }
  }
  var summaryEl = document.getElementById('syncCountSummary');
  if (summaryEl) {
    var parts = [totalCount + ' entities'];
    if (totalDone > 0) parts.push(totalDone + ' done');
    if (totalSkipped > 0) parts.push(totalSkipped + ' skipped');
    summaryEl.textContent = parts.join(', ');
  }
}

async function selectProposal(i) {
  // Resolve sync-placeholder proposals by fetching full data from server
  if (proposals[i] && proposals[i]._sync_placeholder) {
    showLoading('Resolving proposal...', 'Fetching full data for: ' + proposals[i].entity_name);

    var result = await apiCall('/api/proposals', 'GET');
    hideLoading();
    if (!result) return;

    // Find the resolved proposal in the server response
    for (var j = 0; j < result.length; j++) {
      if (result[j].entity_name === proposals[i].entity_name &&
          !result[j]._sync_placeholder) {
        proposals[i] = result[j];
        selectedIndex = i;
        renderSidebar();
        if (editingField) cancelEdit();
        renderContent();
        showToast('Proposal loaded', 'success');
        return;
      }
    }

    // If not found yet, the sync may still be running — just show it as placeholder
    hideLoading();
  }

  selectedIndex = i;
  if (editingField) cancelEdit();
  renderSidebar();
  renderContent();
}

// ── Text utilities ────────────────────────────────────────

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
  return (str || '').replace(/\\/g, '\\\\\\\\').replace(/'/g, "\\'").replace(/"/g, '\\\\"')
    .replace(/\n/g, '\\\\n').replace(/\r/g, '\\\\r').replace(/\//g, '\\/');
}

function stripHtml(html) {
  var tmp = document.createElement('div');
  tmp.innerHTML = html || '';
  return tmp.textContent || tmp.innerText || '';
}

function renderJournalLinks(text) {
  return escapeJsHtml(text);
}

// ── Diff helpers (LCS-based line diff) ───────────────────

function normalizeDiffLines(text) {
  // Strip HTML and trim each line to avoid false diffs from whitespace
  // differences introduced by <p>, </p>, or other block-level tags.
  var stripped = stripHtml(text || '');
  return stripped.split('\n').map(function(l){ return l.trim(); });
}

function computeDiff(oldLines, newLines) {
  // Returns array of {type:'keep'|'del'|'add', line: string}
  var m = oldLines.length, n = newLines.length;
  if (m === 0 && n === 0) return [];

  // Build LCS table bottom-up
  var dp = [];
  for (var i = 0; i <= m; i++) {
    dp[i] = new Array(n + 1).fill(0);
  }
  for (var i = m - 1; i >= 0; i--) {
    for (var j = n - 1; j >= 0; j--) {
      if (oldLines[i] === newLines[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  // Backtrack to produce operations
  var ops = [];
  var i = 0, j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && oldLines[i] === newLines[j]) {
      ops.push({ type: 'keep', line: oldLines[i] });
      i++; j++;
    } else if (i < m && (j >= n || dp[i][j] === dp[i + 1][j])) {
      ops.push({ type: 'del', line: oldLines[i] });
      i++;
    } else {
      ops.push({ type: 'add', line: newLines[j] });
      j++;
    }
  }
  return ops;
}

function renderUnifiedDiff(prevText, newText) {
  var prevLines = normalizeDiffLines(prevText);
  var newLines = normalizeDiffLines(newText);
  var ops = computeDiff(prevLines, newLines);

  // Check if there are actual changes
  var hasChanges = false;
  for (var i = 0; i < ops.length; i++) {
    if (ops[i].type !== 'keep') { hasChanges = true; break; }
  }
  if (!hasChanges) {
    return '<div class="empty-diff">No changes</div>';
  }

  var html = '';
  for (var i = 0; i < ops.length; i++) {
    if (ops[i].type === 'keep') {
      html += '<div class="diff-line" style="padding-left:20px;color:var(--text-dim)">' + escapeJsHtml(ops[i].line) + '</div>';
    } else if (ops[i].type === 'del') {
      html += '<div class="diff-line diff-del">' + escapeJsHtml(ops[i].line) + '</div>';
    } else {
      html += '<div class="diff-line diff-add">' + renderJournalLinks(ops[i].line) + '</div>';
    }
  }
  return html;
}

function renderSideBySideDiff(prevText, newText) {
  var prevLines = normalizeDiffLines(prevText);
  var newLines = normalizeDiffLines(newText);
  var ops = computeDiff(prevLines, newLines);

  // Check if there are actual changes
  var hasChanges = false;
  for (var i = 0; i < ops.length; i++) {
    if (ops[i].type !== 'keep') { hasChanges = true; break; }
  }
  if (!hasChanges) {
    return '<div class="empty-diff">No changes</div>';
  }

  // Group operations into rows: each row has at most one old-line and one new-line
  var rows = [];
  for (var i = 0; i < ops.length; i++) {
    if (ops[i].type === 'keep') {
      rows.push({ left: ops[i].line, right: ops[i].line });
    } else if (ops[i].type === 'del' && i + 1 < ops.length && ops[i + 1].type === 'add') {
      // Paired change — show old and new on same row
      rows.push({ left: ops[i].line, right: ops[i + 1].line });
      i++;
    } else if (ops[i].type === 'del') {
      rows.push({ left: ops[i].line, right: null });
    } else {
      rows.push({ left: null, right: ops[i].line });
    }
  }

  var html = '<div class="diff-columns">';
  // Left column header + content
  html += '<div class="diff-column diff-column-left"><div class="diff-col-header">Previous</div>';
  for (var r = 0; r < rows.length; r++) {
    if (rows[r].left !== null) {
      var leftClass = (rows[r].right === null || rows[r].left !== rows[r].right) ? 'diff-del' : 'diff-keep';
      html += '<div class="diff-line ' + leftClass + '">' + escapeJsHtml(rows[r].left) + '</div>';
    } else {
      html += '<div class="diff-line diff-empty"></div>';
    }
  }
  html += '</div>';

  // Right column header + content
  html += '<div class="diff-column diff-column-right"><div class="diff-col-header">Proposed</div>';
  for (var r = 0; r < rows.length; r++) {
    if (rows[r].right !== null) {
      var rightClass = (rows[r].left === null || rows[r].left !== rows[r].right) ? 'diff-add' : 'diff-keep';
      html += '<div class="diff-line ' + rightClass + '">' + renderJournalLinks(rows[r].right) + '</div>';
    } else {
      html += '<div class="diff-line diff-empty"></div>';
    }
  }
  html += '</div></div>';

  return html;
}

function toggleDiffView() {
  if (editingField) return;
  diffViewMode = diffViewMode === 'unified' ? 'side-by-side' : 'unified';
  renderContent();
}

// ── API calls ─────────────────────────────────────────────

async function apiCall(url, method, body) {
  try {
    var opts = {
      method: method,
      headers: {'Content-Type': 'application/json'}
    };
    if (body !== undefined && body !== null) {
      opts.body = JSON.stringify(body);
    }
    var res = await fetch(url, opts);
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
    return null;
  }
}

// ── Actions ───────────────────────────────────────────────

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
  // Try forward first, then backward (forward works for full list,
  // backward is needed when items are removed from a filtered tab)
  for (var i = 0; i < visible.length; i++) {
    if (visible[i] > fromIndex) {
      selectedIndex = visible[i];
      renderSidebar();
      renderContent();
      return;
    }
  }
  for (var j = visible.length - 1; j >= 0; j--) {
    selectedIndex = visible[j];
    renderSidebar();
    renderContent();
    return;
  }
  selectedIndex = null;
  renderSidebar();
  renderContent();
}

// ── Editor ────────────────────────────────────────────────

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

// ── Relation management ───────────────────────────────────

async function saveRelationEdit(idx) {
  if (selectedIndex === null) return;
  var p = proposals[selectedIndex];
  var rc = p.relation_changes[idx];

  // Read the edited value from the inline input
  var input = document.querySelector('.rel-type-input[data-index="' + idx + '"]');
  var newRelation = (input ? input.value : '').trim();

  if (!newRelation) {
    showToast('Relation type is required', 'error');
    return;
  }

  // If this is a new type, register it first
  if (!knownRelationTypes.includes(newRelation)) {
    var createResult = await apiCall('/api/known-relation-types', 'POST', {label: newRelation});
    if (!createResult || !createResult.ok) {
      showToast('Failed to create relation type "' + newRelation + '"', 'error');
      return;
    }
    // Refresh known types so the datalist updates immediately
    await loadKnownTypes();
  }

  // Send update to backend (action='update' overwrites relation field)
  var result = await apiCall('/api/proposals/' + selectedIndex + '/relation', 'POST', {
    action: 'update',
    target_name: rc.target_name,
    relation: newRelation,
    attitude: rc.attitude || '',
    reason: rc.reason || ''
  });

  if (result && result.proposal) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Relation updated', 'success');
  }
}

async function addRelation() {
  if (selectedIndex === null) return;
  var target = document.getElementById('newRelTarget').value.trim();
  var action = document.getElementById('newRelAction').value;
  var relation = document.getElementById('newRelRelation').value.trim();
  var attitude = document.getElementById('newRelAttitude').value.trim();

  if (!target || !relation) { showToast('Target and relation are required', 'error'); return; }

  // Auto-register new types (same logic as saveRelationEdit)
  if (!knownRelationTypes.includes(relation)) {
    var createResult = await apiCall('/api/known-relation-types', 'POST', {label: relation});
    if (createResult && createResult.ok) {
      knownRelationTypes.push(relation);
      // Rebuild datalist option without full reload
      var dl = document.getElementById('relationTypeDatalist');
      if (dl) {
        var opt = document.createElement('option');
        opt.value = relation.replace(/"/g, '&quot;').replace(/</g, '&lt;');
        dl.appendChild(opt);
      }
    }
  }

  var result = await apiCall('/api/proposals/' + selectedIndex + '/relation', 'POST', {
    action: action, target_name: target, relation: relation, attitude: attitude, reason: ''
  });
  if (result && result.proposal) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Relation added', 'success');
    // Clear form
    document.getElementById('newRelTarget').value = '';
    document.getElementById('newRelRelation').value = '';
    document.getElementById('newRelAttitude').value = '';
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
    // Refresh known types in case this was the last use of a type
    loadKnownTypes();
  }
}

// ── Truncation regeneration ───────────────────────────────

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

// ── Toast notifications ───────────────────────────────────

function showToast(message, type) {
  var toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'toast toast-' + type + ' show';
  setTimeout(function(){ toast.classList.remove('show'); }, 7000);
}

// ── Loading overlay ───────────────────────────────────────

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

// ── Keyboard shortcuts ────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (editingField || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

  switch(e.key.toLowerCase()) {
    case 'n': {
      var visible = getVisibleIndices();
      var pos = visible.indexOf(selectedIndex);
      if (pos !== null && pos < visible.length - 1) {
        selectedIndex = visible[pos + 1];
        renderSidebar();
        renderContent();
      }
      break;
    }
    case 'p': {
      var visible2 = getVisibleIndices();
      var pos2 = visible2.indexOf(selectedIndex);
      if (pos2 !== null && pos2 > 0) {
        selectedIndex = visible2[pos2 - 1];
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

// ── Shortcuts dropdown toggle ─────────────────────────────

function toggleShortcuts() {
  var dd = document.getElementById('shortcutsDropdown');
  dd.classList.toggle('visible');
}

function hideShortcuts() {
  var dd = document.getElementById('shortcutsDropdown');
  if (dd) dd.classList.remove('visible');
}

// Close shortcuts dropdown when clicking outside
document.addEventListener('click', function(e) {
  var dd = document.getElementById('shortcutsDropdown');
  var btn = document.getElementById('shortcutToggle');
  if (dd && dd.classList.contains('visible') && !dd.contains(e.target) && !btn.contains(e.target)) {
    hideShortcuts();
  }
});

// ── Escape key to cancel editing ──────────────────────────

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') hideShortcuts();
  if (e.key !== 'Escape' || !editingField) return;
  var editor = document.getElementById('synopsisEditor');
  if (!editor) return;
  var hasChanges = editor.value !== editingOriginal;
  if (hasChanges && !confirm('Discard unsaved changes?')) return;
  cancelEdit();
});

// ── Completion summary banner ─────────────────────────────

function showCompletionSummary(total, done, skipped, errors, proposals) {
  var content = document.getElementById('content');
  if (!content || currentTab !== 'sync') return;

  var parts = [total + ' entity' + (total !== 1 ? 'ies' : '') + ' processed'];
  if (done > 0) parts.push(done + ' done');
  if (skipped > 0) parts.push(skipped + ' skipped');
  if (errors > 0) parts.push(errors + ' error' + (errors !== 1 ? '' : 's'));

  var html = '<div class="sync-summary-banner">' +
      '<span class="sync-summary-text">&#10003; Sync complete \u2014 ' + parts.join(', ') + '</span>' +
      '<button class="btn btn-primary" onclick="reviewNewProposals()" style="margin-left:12px;">Review New Proposals \u2192</button>' +
    '</div>';

  var container = content.querySelector('.sync-progress-container');
  if (container) {
    container.insertAdjacentHTML('afterbegin', html);
  } else {
    content.innerHTML = html + content.innerHTML;
  }
}

function reviewNewProposals() {
  stopElapsedTimer();
  currentTab = 'new';
  selectedIndex = null;
  renderSidebar();
  var visible = getVisibleIndices();
  if (visible.length > 0) {
    selectedIndex = visible[0];
  }
  renderContent();
}

// ── Elapsed time timer ────────────────────────────────────

var _elapsedInterval = null;

function startElapsedTimer() {
  if (_elapsedInterval) clearInterval(_elapsedInterval);
  _elapsedInterval = setInterval(function() {
    if (!currentSyncJob || !currentSyncJob.started_at) return;
    var elapsed = Math.floor((Date.now() - currentSyncJob.started_at) / 1000);
    var el = document.getElementById('syncElapsed');
    if (el) {
      var mins = Math.floor(elapsed / 60);
      var secs = elapsed % 60;
      el.textContent = mins + 'm ' + (secs < 10 ? '0' : '') + secs + 's';
    }
  }, 1000);
}

function stopElapsedTimer() {
  if (_elapsedInterval) {
    clearInterval(_elapsedInterval);
    _elapsedInterval = null;
  }
}

// ── Inline error expand/collapse ──────────────────────────

function toggleErrorDetail(e) {
  e.stopPropagation();
  var indicator = e.target;
  var errId = indicator.getAttribute('data-error-id');
  var detail = document.getElementById(errId);
  if (detail) {
    detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
  }
}

function closeErrorDetail(errId) {
  var detail = document.getElementById(errId);
  if (detail) detail.style.display = 'none';
}

// ── Sync pipeline runner (typed SSE dispatch) ─────────────

function _addJournalGroup(journalName) {
  if (!syncEntities['__journal_order__']) { syncEntities['__journal_order__'] = []; }
  var order = syncEntities['__journal_order__'];
  if (order.indexOf(journalName) === -1) {
    order.push(journalName);
    syncJournalOrder = order.slice(); // snapshot
  }
}

function _renderSyncContent() {
  renderSyncContent(document.getElementById('content'));
}

async function runSync() {
  if (currentSyncJob && currentSyncJob.status === 'running') {
    // Cancel: close EventSource and notify server to stop ingest thread
    if (syncEventSource) {
      syncEventSource.close();
      syncEventSource = null;
    }
    var jobId = currentSyncJob.job_id;
    apiCall('/api/sync/cancel?job_id=' + encodeURIComponent(jobId), 'POST')
      .catch(function() { /* best-effort */ });
    stopElapsedTimer();
    // Optimistically mark as cancelled in UI
    currentSyncJob.status = 'cancelled';
    _renderSyncContent();
    return;
  }

  // Clear previous sync state
  syncEntities = {};
  syncJournalOrder = [];

  var result = await apiCall('/api/sync/run', 'POST');
  if (!result || !result.job_id) return;

  currentSyncJob = { job_id: result.job_id, status: 'running', started_at: Date.now() };
  startElapsedTimer();
  renderContent();

  // Connect to SSE stream with typed event listeners
  syncEventSource = new EventSource('/api/sync/output?job_id=' + result.job_id);

  // Connection error / unexpected close — poll server for actual status.
  // This catches cases where the SSE stream closed without sending an 'end'
  // event (e.g. idle timeout, network drop), preventing the UI from staying
  // stuck in "running" forever.
  syncEventSource.onerror = function() {
    if (!syncEventSource || _syncEnded) return;  // already cleaned up by 'end' handler
    if (!currentSyncJob || currentSyncJob.status !== 'running') return;
    fetch('/api/sync/status')
      .then(function(r){ return r.json(); })
      .then(function(data) {
        if (data && data.jobs && data.jobs.length > 0) {
          var job = data.jobs[0];
          currentSyncJob.status = job.status;
          stopElapsedTimer();
          _renderSyncContent();
        }
      })
      .catch(function() { /* best-effort */ });
  };

  // Entity progress events — entity cards update in real-time
  syncEventSource.addEventListener('entity_progress', function(e) {
    var data;
    try { data = JSON.parse(e.data); } catch (_) { return; }
    if (!data.name || !data.journal_name) return;

    // Track journal group order (first-seen)
    _addJournalGroup(data.journal_name);

    // Update entity state
    var key = data.journal_name + '::' + data.name;
    syncEntities[key] = {
      name: data.name,
      journal_name: data.journal_name,
      status: data.status || 'processing',
      error_message: data.error_message || null,
    };

    // Also update matching placeholder in proposals sidebar with real journal name
    for (var pi = 0; pi < proposals.length; pi++) {
      if (proposals[pi]._sync_placeholder &&
          proposals[pi].entity_name === data.name &&
          proposals[pi].source_journal === 'Syncing...') {
        proposals[pi].source_journal = data.journal_name;
        break;
      }
    }

    _renderSyncContent();
  });

  // Live proposal insertion — proposals appear in "New" tab as they're queued
  syncEventSource.addEventListener('proposal_pushed', function(e) {
    var data;
    try { data = JSON.parse(e.data); } catch (_) { return; }
    if (!data.name || !data.type) return;

    // Look up real journal name from syncEntities (set by entity_progress events)
    var realJournal = '';
    for (var sk in syncEntities) {
      if (syncEntities[sk]._meta) continue;
      if (syncEntities[sk].name === data.name && syncEntities[sk].journal_name) {
        realJournal = syncEntities[sk].journal_name;
        break;
      }
    }

    // Create a minimal placeholder proposal for sidebar display
    var placeholder = {
      entity_name: data.name,
      source_journal: realJournal || 'Syncing...',
      proposal_type: data.type === 'new_entity' ? 'new_entity' : 'update',
      entity_kind: data.kind || '',
      status: 'pending',
      _sync_placeholder: true, // marks this as a live-insert placeholder
    };

    // Avoid duplicate entries for the same entity
    var found = false;
    for (var i2 = 0; i2 < proposals.length; i2++) {
      if (proposals[i2].entity_name === data.name) {
        if (proposals[i2]._sync_placeholder) {
          // Update existing placeholder with fresh data
          Object.assign(proposals[i2], placeholder);
        }
        found = true;
        break;
      }
    }
    if (!found) {
      proposals.push(placeholder);
    }

    updateStats();
    renderSidebar();
  });

  // Status change events — sync header badge updates
  syncEventSource.addEventListener('status_change', function(e) {
    var data;
    try { data = JSON.parse(e.data); } catch (_) { return; }
    if (data.status) {
      currentSyncJob.status = data.status;
      _renderSyncContent();
    }
  });

  // Stream end — finalize sync job and refresh full proposal data
  syncEventSource.addEventListener('end', function() {
    _syncEnded = true;        // MUST be first: blocks onerror reconnection race
    if (syncEventSource) {     // double-check in case close() already nullified it
      syncEventSource.close();
      syncEventSource = null;
    }
    stopElapsedTimer();
    _renderSyncContent();

    // Show completion summary before refreshing proposals
    var totalEntities = 0, doneCount = 0, skippedCount = 0, errorCount = 0;
    for (var k in syncEntities) {
      if (!syncEntities[k]._meta && syncEntities[k].journal_name) {
        totalEntities++;
        if (syncEntities[k].status === 'done') doneCount++;
        else if (syncEntities[k].status === 'skipped') skippedCount++;
        else if (syncEntities[k].status === 'error') errorCount++;
      }
    }

    showCompletionSummary(totalEntities, doneCount, skippedCount, errorCount, 0);

    // Refresh proposals from server to fill in full data
    loadProposals();
  });
}

// ── Proposal loading ────────────────────────────────────

function loadProposals() {
  fetch('/api/proposals')
    .then(function(r){ return r.json(); })
    .then(function(data) {
      var serverMap = {};
      for (var si = 0; si < data.length; si++) {
        var key = data[si].entity_name.toLowerCase();
        if (!serverMap[key]) { serverMap[key] = []; }
        serverMap[key].push(data[si]);
      }

      // Replace placeholders with real server data, keep non-placeholders as-is
      for (var pi = 0; pi < proposals.length; pi++) {
        if (proposals[pi]._sync_placeholder) {
          var pKey = proposals[pi].entity_name.toLowerCase();
          var pJournal = proposals[pi].source_journal;
          if (serverMap[pKey]) {
            // Find the server proposal that matches both name and journal
            var match = null;
            for (var sm = 0; sm < serverMap[pKey].length; sm++) {
              if (serverMap[pKey][sm].source_journal === pJournal) {
                match = serverMap[pKey][sm];
                break;
              }
            }
            proposals[pi] = match || serverMap[pKey][0];  // fallback to first
          }
        }
      }

      // Add any server proposals not already represented in the list
      for (var si2 = 0; si2 < data.length; si2++) {
        var sName = data[si2].entity_name;
        var sJournal = data[si2].source_journal;
        var exists = false;
        for (var ei = 0; ei < proposals.length; ei++) {
          if (proposals[ei].entity_name === sName &&
              proposals[ei].source_journal === sJournal) {
            exists = true;
            break;
          }
        }
        if (!exists) { proposals.push(data[si2]); }
      }

      // Reset selection — the array may have shifted (new items inserted at front),
      // so any stale selectedIndex would highlight the wrong proposal.
      selectedIndex = null;

      updateStats();
      renderSidebar();
      renderContent();
    })
    .catch(function() { /* silently ignore — keep current state */ });
}

// ── Init ──────────────────────────────────────────────────

async function initApp() {
  await Promise.all([
    loadKnownTypes().catch(function(){ /* empty datalist is fine */ }),
    new Promise(function(resolve) { loadProposals(); resolve(); })
  ]);
}

initApp();

setInterval(updateStats, 5000);

// ── Background sync indicator (header bar when pipeline runs) ──

function updateSyncIndicator() {
  var indicator = document.getElementById('syncIndicator');
  if (!indicator) return;
  if (currentSyncJob && currentSyncJob.status === 'running') {
    var statusText = 'Sync: Running';
    // Count entities in sync state for the header bar
    var entityCount = 0;
    for (var k4 in syncEntities) {
      if (!syncEntities[k4]._meta && syncEntities[k4].journal_name) entityCount++;
    }
    if (entityCount > 0) {
      statusText += ' — ' + entityCount + ' entities';
    }
    document.getElementById('syncIndicatorText').textContent = statusText;
    indicator.style.display = 'flex';
  } else {
    indicator.style.display = 'none';
  }
}
