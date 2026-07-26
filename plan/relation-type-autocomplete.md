# Plan: Editable Relation Types with Autocomplete

## Overview

Replace plain-text relation type display in existing cards and the "add new" form with `<input>` + `<datalist>` autocomplete widgets, fed by `GET /api/known-relation-types`. When a user types a value not in the known list, it's auto-registered via `POST /api/known-relation-types` on save.

## Files to modify

| File | Change |
|---|---|
| `kanka_wiki_updater/review/web/static/js/app.js` | Rendering, autocomplete, save logic |
| `kanka_wiki_updater/review/web/static/css/style.css` | Styles for inline editable input + badges |

No backend changes needed — all three required endpoints already exist and work correctly.

---

## Step 1: Datalist lifecycle — inject once, populate later (`app.js`)

**Where:** At the top of `app.js`, outside any function (module-level setup).

**What:**
```js
var knownRelationTypes = [];   // global cache for autocomplete
var similarTypesSet = new Set(); // union of all similar_types from enrichment

// Create the shared <datalist> once, before first render
(function initDatalist() {
  var dl = document.createElement('datalist');
  dl.id = 'relationTypeDatalist';
  document.body.appendChild(dl);
})();
```

**Why:** `renderContent()` sets `innerHTML` on the content div — if the datalist were inside it, it'd be destroyed and recreated every render. Creating it once on `document.body` avoids that. The datalist will be empty initially (no race condition — empty datalist just means no suggestions until loaded).

**Add helper:**
```js
function populateTypeDatalist(types, allSimilar) {
  var dl = document.getElementById('relationTypeDatalist');
  if (!dl) return;
  knownRelationTypes = types || [];
  similarTypesSet = new Set();
  (allSimilar || []).forEach(function(s) { if (s) similarTypesSet.add(s); });

  // Rebuild options: known types first, then similar ones not already in known
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
```

**Call site:** In `fetchProposals()` (after proposals are loaded), call:
```js
loadKnownTypes().then(function() { renderContent(); });
```
This ensures the datalist is populated before first render. If `loadKnownTypes` fails, fall back to rendering anyway (empty datalist).

---

## Step 2: Single inline input per card — no hidden editor row (`app.js`, `renderContent()`)

**Current code (lines ~178–190):**
```js
html += '<div class="relation-card" id="rel-' + rcIdx + '">' +
  '<div class="rel-header">' +
    '<span class="rel-action ' + actionClass + '">' + escapeJsHtml(rc.action) + '</span>' +
    '<span class="rel-target">' + escapeJsHtml(p.entity_name) + ' --' +
      escapeJsHtml(rc.relation) + '--> ' + escapeJsHtml(rc.target_name) + '</span>' +
    '<button ... onclick="deleteRelation(' + rcIdx + ')">Delete</button>' +
  '</div>' +
  '<div style="...">Attitude: ' + escapeJsHtml(rc.attitude || 'N/A') + '</div>' +
  '<div class="rel-reason">Reason: ' + escapeJsHtml(rc.reason) + '</div></div>';
```

**New structure — one input, always visible:**
```js
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
```

**Key points:**
- **One `<input>`** replaces the plain text `rc.relation` in `.rel-target`. It has `list="relationTypeDatalist"` for native autocomplete.
- The input is **always visible and pre-filled** — no hidden/show pattern needed.
- A **"Save" button** sits next to the target span, initially hidden (`display:none`).
- A **NEW badge** appears if `_type_status === 'new_suggested'` (already enriched by backend).
- The `data-index` attribute links back to the card for event handlers.

**Event wiring — after `content.innerHTML = html`:**
```js
// Show Save button when user types in a relation input
var relInputs = content.querySelectorAll('.rel-type-input');
for (var i = 0; i < relInputs.length; i++) {
  (function(input) {
    var card = input.closest('.relation-card');
    var saveBtn = card.querySelector('.rel-save-btn');

    input.addEventListener('input', function() {
      // Check if value differs from original or is not a known type
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
```

---

## Step 3: `saveRelationEdit(index)` — single flow for all cases (`app.js`)

**New function:**
```js
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
```

---

## Step 4: Update `addRelation()` form — add autocomplete + new-type auto-register (`app.js`)

**Current HTML (lines ~192–194):**
```html
<input type="text" id="newRelRelation" placeholder="Relation (e.g. ally)">
```

**New:**
```html
<input type="text" id="newRelRelation" list="relationTypeDatalist"
  placeholder="Relation (e.g. ally)">
```

**Update `addRelation()` function (line ~682)** — add new-type auto-registration before the relation POST:
```js
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
```

---

## Step 5: CSS updates (`style.css`)

**Add these styles (append to the existing relation section around line 300):**

```css
/* ── Inline editable relation type input ─────────────── */
.rel-target .rel-type-input {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: inherit;
  font-weight: inherit;
  min-width: 70px;
  max-width: 200px;
}

.rel-target .rel-type-input:focus {
  border-color: var(--green);
  outline: none;
  background: #0a0b0e;
}

/* Save button (hidden by default, shown on change) */
.relation-card .rel-save-btn {
  padding: 2px 10px !important;
  font-size: 11px !important;
  cursor: pointer;
  background: var(--green);
  color: #0c0d10;
  border: none;
  font-weight: 600;
  border-radius: 4px;
}

.relation-card .rel-save-btn:hover {
  opacity: 0.85;
}
```

---

## Step 6: Refresh datalist after relation changes (`app.js`)

After `addRelation()` and `deleteRelation()` succeed, refresh the known types list so the datalist reflects any newly added type:

```js
// In addRelation() success path (already shown in Step 4 above)
// — inline append to datalist for immediate feedback

// In deleteRelation() success path (~line 702), after re-render:
async function deleteRelation(idx) {
  // ... existing code ...
  if (result && result.proposal) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Relation deleted', 'success');
    // Refresh known types in case this was the last use of a type
    loadKnownTypes();
  }
}
```

---

## Revised data flow

```
User edits relation input on existing card (or in add form)
  │
  ├─ Types value matching known type → datalist suggests it, no badge
  │
  └─ Types novel value → NEW badge appears (if _type_status), Save button shows
       │
       ▼
  User clicks Save / Add
       │
       ├─ Value not in knownRelationTypes?
       │    POST /api/known-relation-types {label: "..."}
       │    → appends to datalist, refreshes knownRelationTypes[]
       │
       └─ POST /api/proposals/<index>/relation {action:'update'|'create', relation: "...", ...}
                    │
                    ▼
              proposals[selectedIndex] = result.proposal
              renderSidebar() + renderContent()
```

## Testing checklist

1. Open proposal with existing relations → each card shows inline editable input, pre-filled with current type. Datalist dropdown works.
2. Type a known type (e.g., "ally") → it appears in suggestions. No NEW badge.
3. Type a novel value (e.g., "swornenemy") → no match in datalist, Save button appears. If `_type_status === 'new_suggested'`, NEW badge shows.
4. Click Save → new type registered via POST `/api/known-relation-types`, then relation updated. Card re-renders with the new value and (if it was known) badge disappears.
5. Add-new form → same autocomplete from shared datalist. Typing a novel type auto-registers it on submit.
6. Page reload after adding types → newly created types appear in suggestions immediately.
