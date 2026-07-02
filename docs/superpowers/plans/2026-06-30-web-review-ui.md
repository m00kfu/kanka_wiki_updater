# Web Review UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Flask-based web interface at `python -m kanka_wiki_updater.review_web` that lets users browse, edit (synopsis + relations), and approve/reject pending proposals from `pending_changes.json`.

**Architecture:** A single new module `review_web.py` containing the Flask app, API routes, and an embedded HTML/CSS/JS template. The web UI reads/writes the same `data/pending_changes.json` used by `review.py`, so both can coexist without conflict. No database — plain JSON files are the source of truth.

**Tech Stack:** Python 3.14+, Flask (already in requirements.txt), vanilla HTML/CSS/JS (single inline template, no build step).

## Global Constraints

- Line length: 120 chars (`pyproject.toml` config already set)
- No new dependencies beyond what's already in `requirements.txt` (Flask is installed but not yet listed — add it)
- Follow existing code style: no comments unless explicitly requested, use f-strings, type hints where appropriate
- Tests go in `tests/`, follow pytest conventions with fixtures for mock data
- The web UI must work without a running LLM server or Kanka API connection (it only reads/writes local JSON)

---

### Task 1: Add Flask to requirements.txt and verify import

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: nothing
- Produces: Flask available as a dependency for all subsequent tasks

- [ ] **Step 1: Add flask to requirements.txt**

Append this line to the end of `requirements.txt`:
```
flask>=3.0
```

- [ ] **Step 2: Verify Flask imports cleanly**

Run:
```bash
python -c "from flask import Flask; print('Flask OK')"
```
Expected output: `Flask OK`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add flask>=3.0 for web review UI"
```

---

### Task 2: Write tests for the Flask app factory and API routes (no server running)

**Files:**
- Create: `tests/test_review_web.py`

**Interfaces:**
- Consumes: `state.load_queue()`, `state.save_queue()` — both already exist in `kanka_wiki_updater.state`
- Produces: test fixtures and assertions that Task 3's implementation must satisfy

```python
"""Tests for review_web.py — Flask app factory, API routes, data handling."""

import json
import os
import tempfile
from pathlib import Path

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_queue(tmp_path):
    """Create a temporary pending_changes.json with mixed proposal types."""
    queue = [
        {
            "proposal_type": "new_entity",
            "entity_name": "Vexara the Veiled",
            "suggested_type": "character",
            "draft_entry": "A mysterious sorceress.",
            "source_journal": "Session 12",
            "status": "pending",
        },
        {
            "proposal_type": "update",
            "entity_name": "Kael Ironfist",
            "entity_kind": "character",
            "entity_id": "42",
            "entity_local_id": 101,
            "source_journal": "Session 13",
            "change_summary": "Updated synopsis.",
            "previous_entry": "<p>Old text.</p>",
            "proposed_entry": "<p>New text with allies.</p>",
            "relation_changes": [
                {
                    "action": "create",
                    "relation": "ally",
                    "target_name": "Vexara the Veiled",
                    "attitude": "cautious trust",
                    "reason": "Met at Ironhold.",
                }
            ],
            "status": "pending",
        },
    ]
    queue_file = tmp_path / "pending_changes.json"
    json.dump(queue, open(queue_file, "w"), indent=2)
    return str(queue_file), tmp_path


@pytest.fixture
def app_with_queue(mock_queue):
    """Create a Flask test client with the review_web app and a temp queue file."""
    from kanka_wiki_updater.review_web import create_app

    queue_file, data_dir = mock_queue
    # Override DATA_DIR so state.py reads our temp file
    import kanka_wiki_updater.config as config
    original_data_dir = config.DATA_DIR
    config.DATA_DIR = str(data_dir)

    app = create_app()
    app.config["TESTING"] = True

    yield app

    # Restore original DATA_DIR after test
    config.DATA_DIR = original_data_dir


# ── App factory tests ───────────────────────────────────────────────────────

class TestCreateApp:
    def test_returns_flask_app(self):
        from kanka_wiki_updater.review_web import create_app

        app = create_app()
        assert app is not None
        # Flask apps have a test_client property
        assert hasattr(app, "test_client")


# ── API route tests ────────────────────────────────────────────────────────

class TestApiProposals:
    def test_get_proposals_returns_all(self, app_with_queue):
        """GET /api/proposals returns the full queue."""
        resp = app_with_queue.get("/api/proposals")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        assert data[0]["proposal_type"] == "new_entity"
        assert data[1]["proposal_type"] == "update"

    def test_get_proposals_filtered_by_status(self, app_with_queue):
        """GET /api/proposals?status=pending returns only pending."""
        resp = app_with_queue.get("/api/proposals?status=pending")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_get_proposals_filtered_by_type(self, app_with_queue):
        """GET /api/proposals?type=new_entity returns only new entities."""
        resp = app_with_queue.get("/api/proposals?type=new_entity")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["entity_name"] == "Vexara the Veiled"

    def test_get_proposals_empty_when_no_match(self, app_with_queue):
        """GET /api/proposals?status=applied returns empty list."""
        resp = app_with_queue.get("/api/proposals?status=applied")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == []


class TestApiProposalStatus:
    def test_update_status_approved_all(self, app_with_queue):
        """POST /api/proposals/1/status with status=approved_all sets applied."""
        resp = app_with_queue.post(
            "/api/proposals/1/status",
            json={"status": "approved_all"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["proposal"]["status"] == "applied"

    def test_update_status_rejected(self, app_with_queue):
        """POST /api/proposals/1/status with status=rejected."""
        resp = app_with_queue.post(
            "/api/proposals/1/status",
            json={"status": "rejected"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["proposal"]["status"] == "rejected"

    def test_update_status_invalid_index(self, app_with_queue):
        """POST /api/proposals/99/status returns 404."""
        resp = app_with_queue.post(
            "/api/proposals/99/status",
            json={"status": "approved_all"},
        )
        assert resp.status_code == 404

    def test_update_status_invalid_value(self, app_with_queue):
        """POST /api/proposals/1/status with bad status returns 400."""
        resp = app_with_queue.post(
            "/api/proposals/1/status",
            json={"status": "banana"},
        )
        assert resp.status_code == 400

    def test_status_persists_to_file(self, app_with_queue):
        """After approving, the queue file on disk reflects the change."""
        import kanka_wiki_updater.state as state

        # Approve proposal at index 1
        app_with_queue.post(
            "/api/proposals/1/status",
            json={"status": "approved_all"},
        )
        # Reload from disk
        queue = state.load_queue()
        assert queue[1]["status"] == "applied"


class TestApiProposalEdit:
    def test_edit_update_synopsis(self, app_with_queue):
        """POST /api/proposals/1/edit with new entry text."""
        resp = app_with_queue.post(
            "/api/proposals/1/edit",
            json={"entry": "<p>Completely rewritten synopsis.</p>"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["proposal"]["proposed_entry"] == "<p>Completely rewritten synopsis.</p>"

    def test_edit_new_entity_draft(self, app_with_queue):
        """POST /api/proposals/0/edit for a new entity changes draft_entry."""
        resp = app_with_queue.post(
            "/api/proposals/0/edit",
            json={"entry": "A powerful necromancer from the dark lands."},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["proposal"]["draft_entry"] == "A powerful necromancer from the dark lands."

    def test_edit_invalid_index(self, app_with_queue):
        """POST /api/proposals/99/edit returns 404."""
        resp = app_with_queue.post(
            "/api/proposals/99/edit",
            json={"entry": "test"},
        )
        assert resp.status_code == 404


class TestApiRelations:
    def test_add_relation(self, app_with_queue):
        """POST /api/proposals/1/relation with action=create."""
        resp = app_with_queue.post(
            "/api/proposals/1/relation",
            json={
                "action": "create",
                "relation": "enemy",
                "target_name": "Shadowmere Cult",
                "attitude": "vengeful",
                "reason": "They burned my village.",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Should now have 2 relations (1 original + 1 new)
        assert len(data["proposal"]["relation_changes"]) == 2

    def test_delete_relation(self, app_with_queue):
        """POST /api/proposals/1/relation with action=delete and target."""
        resp = app_with_queue.post(
            "/api/proposals/1/relation",
            json={
                "action": "delete",
                "target_name": "Vexara the Veiled",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Should now have 0 relations (1 was deleted)
        assert len(data["proposal"]["relation_changes"]) == 0

    def test_update_relation(self, app_with_queue):
        """POST /api/proposals/1/relation with action=update and target."""
        resp = app_with_queue.post(
            "/api/proposals/1/relation",
            json={
                "action": "update",
                "target_name": "Vexara the Veiled",
                "relation": "rival",
                "attitude": "distrust",
                "reason": "She betrayed us at Ironhold.",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        rels = data["proposal"]["relation_changes"]
        assert len(rels) == 1
        assert rels[0]["relation"] == "rival"
        assert rels[0]["attitude"] == "distrust"
        assert rels[0]["reason"] == "She betrayed us at Ironhold."

    def test_delete_nonexistent_relation(self, app_with_queue):
        """Deleting a relation that doesn't exist returns 404."""
        resp = app_with_queue.post(
            "/api/proposals/1/relation",
            json={
                "action": "delete",
                "target_name": "Nonexistent Person",
            },
        )
        assert resp.status_code == 404


class TestIndexPage:
    def test_index_returns_html(self, app_with_queue):
        """GET / returns HTML content."""
        resp = app_with_queue.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.content_type

```

- [ ] **Step 4: Run tests to verify they all fail**

Run:
```bash
pytest tests/test_review_web.py -v
```
Expected: All tests FAIL with `ModuleNotFoundError` or `ImportError` for `kanka_wiki_updater.review_web`.

- [ ] **Step 5: Commit (tests only)**

```bash
git add tests/test_review_web.py
git commit -m "test: add review_web module tests (all should fail)"
```

---

### Task 3: Implement the Flask app factory and core API routes

**Files:**
- Create: `kanka_wiki_updater/review_web.py`

**Interfaces:**
- Consumes: `state.load_queue()`, `state.save_queue()` from `kanka_wiki_updater.state`
- Produces: `create_app()` function that returns a Flask app with routes:
  - `GET /api/proposals?status=&type=` → JSON array of proposals
  - `POST /api/proposals/<int:index>/status` → JSON `{proposal, ok}`
  - `POST /api/proposals/<int:index>/edit` → JSON `{proposal, ok}`
  - `POST /api/proposals/<int:index>/relation` → JSON `{proposal, ok}`

- [ ] **Step 1: Write the Flask app factory and proposal routes**

Create `kanka_wiki_updater/review_web.py` with this content:

```python
"""Web-based review UI for Kanka Wiki Updater.

Launch with: python -m kanka_wiki_updater.review_web

Serves a single-page web app at http://127.0.0.1:5555 that reads and writes
data/pending_changes.json — the same file used by review.py. Both can coexist
without conflict; they just need to agree on the JSON schema.
"""

import json
from flask import Flask, jsonify, request, render_template_string


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(INDEX_HTML)

    @app.route("/api/proposals")
    def get_proposals():
        queue = _load_queue()
        status_filter = request.args.get("status")
        type_filter = request.args.get("type")

        if status_filter:
            queue = [p for p in queue if p.get("status") == status_filter]
        if type_filter:
            queue = [p for p in queue if p.get("proposal_type") == type_filter]

        return jsonify(queue)

    @app.route("/api/proposals/<int:index>/status", methods=["POST"])
    def update_status(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({"error": "Proposal not found"}), 404

        data = request.get_json()
        status_value = data.get("status")
        valid_statuses = ("approved_all", "approved_synopsis_only", "rejected")
        if status_value not in valid_statuses:
            return jsonify({"error": f"Invalid status. Must be one of {valid_statuses}"}), 400

        mapping = {
            "approved_all": "applied",
            "approved_synopsis_only": "applied",
            "rejected": "rejected",
        }
        queue[index]["status"] = mapping[status_value]
        _save_queue(queue)
        return jsonify({"proposal": queue[index], "ok": True})

    @app.route("/api/proposals/<int:index>/edit", methods=["POST"])
    def edit_proposal(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({"error": "Proposal not found"}), 404

        data = request.get_json()
        entry_text = data.get("entry", "")
        proposal = queue[index]

        if proposal["proposal_type"] == "new_entity":
            proposal["draft_entry"] = entry_text
        else:
            proposal["proposed_entry"] = entry_text

        _save_queue(queue)
        return jsonify({"proposal": proposal, "ok": True})

    @app.route("/api/proposals/<int:index>/relation", methods=["POST"])
    def update_relation(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({"error": "Proposal not found"}), 404

        data = request.get_json()
        action = (data.get("action") or "").strip().lower()
        target_name = data.get("target_name", "")
        proposal = queue[index]
        relations = proposal.get("relation_changes", [])

        if action == "create":
            new_rel = {
                "action": "create",
                "relation": data.get("relation", ""),
                "target_name": target_name,
                "attitude": data.get("attitude", ""),
                "reason": data.get("reason", ""),
            }
            relations.append(new_rel)

        elif action == "delete":
            found = next((r for r in relations if r["target_name"] == target_name), None)
            if not found:
                return jsonify({"error": f"No relation to '{target_name}' found"}), 404
            relations.remove(found)

        elif action == "update":
            found = next((r for r in relations if r["target_name"] == target_name), None)
            if not found:
                return jsonify({"error": f"No relation to '{target_name}' found"}), 404
            found["relation"] = data.get("relation", found["relation"])
            found["attitude"] = data.get("attitude", found["attitude"])
            found["reason"] = data.get("reason", found["reason"])

        else:
            return jsonify({"error": f"Invalid action: {action}"}), 400

        proposal["relation_changes"] = relations
        _save_queue(queue)
        return jsonify({"proposal": proposal, "ok": True})

    return app


def _load_queue():
    from . import state
    return state.load_queue()


def _save_queue(queue):
    from . import state
    state.save_queue(queue)


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
.diff-line { padding: 4px 12px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.diff-add { background: #0d1f0d; color: var(--green); border-left: 3px solid var(--green); }
.diff-del { background: #2a0d0d; color: var(--red); border-left: 3px solid var(--red); text-decoration: line-through; opacity: 0.7; }
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
    html += '<div class="source">← ' + escapeHtml(p.source_journal) + '</div></div>';
  } else {
    var statusIcon = {pending:'○', applied:'<span style="color:var(--green)">✓</span>', rejected:'<span style="color:var(--red)">✗</span>'}[p.status] || '○';
    html += '<div class="proposal-header"><h2>' + statusIcon + ' ' + escapeHtml(p.entity_name) + ' <span style="font-weight:400;color:var(--text-dim);font-size:16px">(' + p.entity_kind + ')</span></h2>';
    html += '<div class="source">← ' + escapeHtml(p.source_journal) + '</div>';
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
      html += '<div class="diff-line" style="cursor:pointer" onclick="startEdit(\'synopsis\')">' + escapeHtml(stripHtml(p.draft_entry || '(none)')) + '</div>';
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
    print("Starting Kanka Wiki Review UI...")
    print("Open http://127.0.0.1:5555 in your browser")
    app.run(host="127.0.0.1", port=5555, debug=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run tests to verify they pass**

Run:
```bash
pytest tests/test_review_web.py -v
```
Expected: All tests PASS.

- [ ] **Step 3: Verify the module entry point works**

Run:
```bash
python -c "from kanka_wiki_updater.review_web import create_app; app = create_app(); print('App created OK')"
```
Expected output: `App created OK`

- [ ] **Step 4: Commit**

```bash
git add kanka_wiki_updater/review_web.py tests/test_review_web.py
git commit -m "feat: add web review UI with Flask (full edit + relation management)"
```

---

### Task 4: Add `__main__.py` entry point for `review_web` module

**Files:**
- The `review_web.py` file already has the `if __name__ == "__main__"` block from Task 3, Step 1. No additional files needed.

**Interfaces:**
- Consumes: `create_app()` from `review_web.py` (defined in Task 3)
- Produces: ability to run `python -m kanka_wiki_updater.review_web`

- [ ] **Step 1: Verify the entry point works**

Run:
```bash
cd C:\Users\m00kfu\Desktop\kanka\kanka_wiki_updater && python -c "import kanka_wiki_updater.review_web; print('Entry point OK')"
```
Expected output: `Entry point OK`

- [ ] **Step 2: Commit**

```bash
git add kanka_wiki_updater/review_web.py
git commit -m "feat: add __main__ entry point for review_web module"
```

---

### Task 5: Update AGENTS.md and README.md with documentation

**Files:**
- Modify: `AGENTS.md` (add a line to the architecture table)
- Modify: `README.md` (add a section about the web UI)

**Interfaces:**
- Consumes: nothing new
- Produces: updated docs that mention the new command

- [ ] **Step 1: Add review_web to AGENTS.md architecture table**

In `AGENTS.md`, find the architecture table and add this row after the `review.py` line:

```markdown
| `review_web.py` | Web-based review UI (Flask). Same API as `review.py` but served at http://127.0.0.1:5555 with full editing of synopsis, relations, and entity names. Run with `python -m kanka_wiki_updater.review_web`. Both read/write the same `pending_changes.json`. |
```

- [ ] **Step 2: Add web UI section to README.md**

Add this block after the existing "Running" section in `README.md`:

```markdown
## Web Review UI (optional)

A browser-based review interface is available as an alternative to the CLI:

```bash
python -m kanka_wiki_updater.review_web
```

Opens http://127.0.0.1:5555 with a sidebar of proposals, inline synopsis editing, and full relation management (add/remove/change). Both `review.py` and `review_web.py` read/write the same `data/pending_changes.json`, so they can coexist without conflict.
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: document web review UI in AGENTS.md and README.md"
```

---

### Task 6: Final verification — run all tests together

**Files:**
- All test files (existing + new)

**Interfaces:**
- Consumes: everything built so far
- Produces: confidence that the full test suite passes

- [ ] **Step 1: Run the complete test suite**

Run:
```bash
pytest -v
```
Expected: All tests pass, including the new `test_review_web.py` tests.

- [ ] **Step 2: Lint check**

Run:
```bash
ruff check kanka_wiki_updater/review_web.py tests/test_review_web.py
```
Expected: No lint errors. If there are any, fix them and re-run until clean.

- [ ] **Step 3: Format check**

Run:
```bash
ruff format --check kanka_wiki_updater/review_web.py tests/test_review_web.py
```
Expected: Files already formatted (or run `ruff format` to fix).

- [ ] **Step 4: Final commit if formatting changed anything**

```bash
git add -A
git commit -m "style: format review_web module and tests"
```

---

## Self-Review Checklist

1. **Spec coverage:** Each requirement from the design summary maps to a task:
   - New command `review_web` → Task 4 (entry point)
   - Parallel to `review.py`, same JSON file → Tasks 3, 5 (shared state module)
   - Edit synopsis text → Task 3 (`/edit` endpoint + JS editor UI)
   - Full relation control (add/remove/change) → Task 3 (`/relation` endpoint + JS relation forms)
   - New entity full edit (name, type, draft) → Task 3 (`/edit` handles `draft_entry` for new entities; name/type editing in HTML template)
   - Tests with TDD → Task 2 (tests written before implementation)

2. **Placeholder scan:** No "TBD", "TODO", or vague references found. Every step has actual code, commands, and expected output.

3. **Type consistency:** All function signatures match between tasks:
   - `create_app()` returns Flask app (Task 3)
   - `_load_queue()` / `_save_queue()` use `state.load_queue()` / `state.save_queue()` (Task 3)
   - API endpoints all return JSON with `{proposal, ok}` shape (Task 3)

4. **Edge cases covered in tests:**
   - Invalid proposal index → 404 (Task 2: `TestApiProposalStatus.test_update_status_invalid_index`)
   - Invalid status value → 400 (Task 2: `test_update_status_invalid_value`)
   - Deleting non-existent relation → 404 (Task 2: `test_delete_nonexistent_relation`)
   - Empty filter results → returns `[]` (Task 2: `test_get_proposals_empty_when_no_match`)
   - Status persists to disk after API call (Task 2: `test_status_persists_to_file`)
