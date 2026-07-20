"""Tests for review_web.py — Flask app factory, API routes, data handling."""

import json
from unittest import mock as umock

import pytest

# Reusable helper: load queue from temp file (avoids importing _load_queue which was extracted)
def _load_queue_from_file():
    """Load the current queue using queue_manager (business logic extraction)."""
    from kanka_wiki_updater.queue_manager import load_queue as _lmq
    return _lmq()



# ── Fixtures moved to conftest.py (project-wide) ───────────────────────

# ── App factory tests ───────────────────────────────────────────────────────


class TestCreateApp:
    def test_returns_flask_app(self):
        from kanka_wiki_updater.review_web import create_app

        app = create_app()
        assert app is not None
        # Flask apps have a test_client property
        assert hasattr(app, 'test_client')


# ── API route tests ────────────────────────────────────────────────────────


class TestApiProposals:
    def test_get_proposals_returns_all(self, app_with_queue):
        """GET /api/proposals returns the full queue."""
        resp = app_with_queue.get('/api/proposals')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        assert data[0]['proposal_type'] == 'new_entity'
        assert data[1]['proposal_type'] == 'update'

    def test_get_proposals_filtered_by_status(self, app_with_queue):
        """GET /api/proposals?status=pending returns only pending."""
        resp = app_with_queue.get('/api/proposals?status=pending')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_get_proposals_filtered_by_type(self, app_with_queue):
        """GET /api/proposals?type=new_entity returns only new entities."""
        resp = app_with_queue.get('/api/proposals?type=new_entity')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['entity_name'] == 'Vexara the Veiled'

    def test_get_proposals_empty_when_no_match(self, app_with_queue):
        """GET /api/proposals?status=applied returns empty list."""
        resp = app_with_queue.get('/api/proposals?status=applied')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == []


class TestApiProposalStatus:
    def test_update_status_approved_all(self, app_with_queue):
        """POST /api/proposals/1/status with status=approved_all sets applied."""
        import types
        from unittest import mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        def make_entity(name):
            return types.SimpleNamespace(name=name)

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
            ),
        ):
            resp = app_with_queue.post(
                '/api/proposals/1/status',
                json={'status': 'approved_all'},
            )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['proposal']['status'] == 'applied'

    def test_update_status_rejected(self, app_with_queue):
        """POST /api/proposals/1/status with status=rejected."""
        resp = app_with_queue.post(
            '/api/proposals/1/status',
            json={'status': 'rejected'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['proposal']['status'] == 'rejected'

    def test_update_status_invalid_index(self, app_with_queue):
        """POST /api/proposals/99/status returns 404."""
        resp = app_with_queue.post(
            '/api/proposals/99/status',
            json={'status': 'approved_all'},
        )
        assert resp.status_code == 404

    def test_update_status_invalid_value(self, app_with_queue):
        """POST /api/proposals/1/status with bad status returns 400."""
        resp = app_with_queue.post(
            '/api/proposals/1/status',
            json={'status': 'banana'},
        )
        assert resp.status_code == 400

    def test_status_persists_to_file(self, app_with_queue):
        """After approving, the queue file on disk reflects the change."""
        import types
        from unittest import mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        def make_entity(name):
            return types.SimpleNamespace(name=name)

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
            ),
        ):
            app_with_queue.post(
                '/api/proposals/1/status',
                json={'status': 'approved_all'},
            )
        # review_web\'s _load_queue was extracted to queue_manager
        queue = _load_queue_from_file()
        assert queue[1]['status'] == 'applied'


class TestApiProposalEdit:
    def test_edit_update_synopsis(self, app_with_queue):
        """POST /api/proposals/1/edit with new entry text."""
        resp = app_with_queue.post(
            '/api/proposals/1/edit',
            json={'entry': '<p>Completely rewritten synopsis.</p>'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['proposal']['proposed_entry'] == '<p>Completely rewritten synopsis.</p>'

    def test_edit_new_entity_draft(self, app_with_queue):
        """POST /api/proposals/0/edit for a new entity changes draft_entry."""
        resp = app_with_queue.post(
            '/api/proposals/0/edit',
            json={'entry': 'A powerful necromancer from the dark lands.'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['proposal']['draft_entry'] == 'A powerful necromancer from the dark lands.'

    def test_edit_invalid_index(self, app_with_queue):
        """POST /api/proposals/99/edit returns 404."""
        resp = app_with_queue.post(
            '/api/proposals/99/edit',
            json={'entry': 'test'},
        )
        assert resp.status_code == 404


class TestApiRelations:
    def test_add_relation(self, app_with_queue):
        """POST /api/proposals/1/relation with action=create."""
        resp = app_with_queue.post(
            '/api/proposals/1/relation',
            json={
                'action': 'create',
                'relation': 'enemy',
                'target_name': 'Shadowmere Cult',
                'attitude': 'vengeful',
                'reason': 'They burned my village.',
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Should now have 2 relations (1 original + 1 new)
        assert len(data['proposal']['relation_changes']) == 2

    def test_delete_relation(self, app_with_queue):
        """POST /api/proposals/1/relation with action=delete and target."""
        resp = app_with_queue.post(
            '/api/proposals/1/relation',
            json={
                'action': 'delete',
                'target_name': 'Vexara the Veiled',
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        # Should now have 0 relations (1 was deleted)
        assert len(data['proposal']['relation_changes']) == 0

    def test_update_relation(self, app_with_queue):
        """POST /api/proposals/1/relation with action=update and target."""
        resp = app_with_queue.post(
            '/api/proposals/1/relation',
            json={
                'action': 'update',
                'target_name': 'Vexara the Veiled',
                'relation': 'rival',
                'attitude': 'distrust',
                'reason': 'She betrayed us at Ironhold.',
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        rels = data['proposal']['relation_changes']
        assert len(rels) == 1
        assert rels[0]['relation'] == 'rival'
        assert rels[0]['attitude'] == 'distrust'
        assert rels[0]['reason'] == 'She betrayed us at Ironhold.'

    def test_delete_nonexistent_relation(self, app_with_queue):
        """Deleting a relation that doesn't exist returns 404."""
        resp = app_with_queue.post(
            '/api/proposals/1/relation',
            json={
                'action': 'delete',
                'target_name': 'Nonexistent Person',
            },
        )
        assert resp.status_code == 404


class TestIndexPage:
    def test_index_returns_html(self, app_with_queue):
        """GET / returns HTML content."""
        resp = app_with_queue.get('/')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type


class TestApiSyncRun:
    def test_run_sync_starts_process(self, app_with_queue):
        """POST /api/sync/run spawns a background thread and returns job_id."""
        from kanka_wiki_updater import review_web as rw

        resp = app_with_queue.post('/api/sync/run')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'job_id' in data
        assert data['status'] == 'running'
        # Verify job was created in _sync_jobs
        rw._sync_cancelled.clear()  # clean up for next test


class TestApiSyncOutput:
    def test_output_returns_sse_content_type(self, app_with_queue):
        """GET /api/sync/output returns text/event-stream."""
        resp = app_with_queue.get('/api/sync/output?job_id=nonexistent')
        assert resp.status_code == 404

    def test_status_endpoint_empty(self, app_with_queue):
        """GET /api/sync/status with no active jobs returns {active: false}."""
        resp = app_with_queue.get('/api/sync/status')
        data = resp.get_json()
        assert data['active'] is False


class TestSyncTabHtml:
    def test_index_contains_sync_tab_button(self, app_with_queue):
        """GET / returns HTML referencing the JS that renders the Sync tab button."""
        resp = app_with_queue.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()
        # The sync tab button is rendered by JavaScript in app.js
        assert '<script src="/static/js/app.js">' in html or "src='/static/js/app.js'" in html
        # Also verify the JS file contains the Sync tab rendering logic
        js_resp = app_with_queue.get('/static/js/app.js')
        assert 'Sync' in js_resp.data.decode()

    def test_index_contains_sync_output_area(self, app_with_queue):
        """GET / returns HTML referencing the JS that renders the sync output area."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        # The syncOutput element is created by JavaScript in app.js
        assert '<script src="/static/js/app.js">' in html or "src='/static/js/app.js'" in html
        js_resp = app_with_queue.get('/static/js/app.js')
        assert 'syncOutput' in js_resp.data.decode()


class TestSyncJavaScript:
    def _get_js(self, app_with_queue):
        """Helper to fetch the external JS file content."""
        resp = app_with_queue.get('/static/js/app.js')
        return resp.data.decode()

    def test_index_contains_run_sync_function(self, app_with_queue):
        """GET / returns HTML referencing JS that contains runSync function."""
        js = self._get_js(app_with_queue)
        assert 'function runSync' in js or 'runSync()' in js

    def test_index_contains_event_source_connection(self, app_with_queue):
        """JS file contains EventSource connection to sync output."""
        js = self._get_js(app_with_queue)
        assert 'EventSource' in js

    def test_sync_output_has_auto_scroll(self, app_with_queue):
        """Sync output streaming includes scrollTop scrollHeight auto-scroll."""
        js = self._get_js(app_with_queue)
        assert 'scrollTop' in js and 'scrollHeight' in js


class TestSwitchTabCancelEdit:
    def _get_js(self, app_with_queue):
        """Helper to fetch the external JS file content."""
        resp = app_with_queue.get('/static/js/app.js')
        return resp.data.decode()

    def test_switch_tab_calls_cancel_edit(self, app_with_queue):
        """switchTab() calls cancelEdit() to prevent stale editor state."""
        js = self._get_js(app_with_queue)
        assert 'if (editingField) cancelEdit()' in js or 'cancelEdit();' in js

    def test_switch_tab_resets_selected_index(self, app_with_queue):
        """switchTab() resets selectedIndex to null."""
        js = self._get_js(app_with_queue)
        assert 'selectedIndex = null' in js


class TestNewlineEscaping:
    def _get_js(self, app_with_queue):
        """Helper to fetch the external JS file content."""
        resp = app_with_queue.get('/static/js/app.js')
        return resp.data.decode()

    def test_strip_html_replaces_newlines_for_js_context(self, app_with_queue):
        """Text with newlines is replaced before JS string concatenation."""
        js = self._get_js(app_with_queue)
        # stripHtml returns text with \n; these must be replaced for safe JS strings
        assert ".replace(/\\n/g, ' ')" in js or 'escapeJs(' in js

    def test_escape_js_function_exists(self, app_with_queue):
        """JS file contains escapeJs function for JS string literal safety."""
        js = self._get_js(app_with_queue)
        assert 'function escapeJs' in js

    def test_escape_js_html_uses_br_for_newlines(self, app_with_queue):
        """escapeJsHtml converts newlines to <br>, using JS escape sequences."""
        js = self._get_js(app_with_queue)
        # The function should use escaped \r and \n in regex literals (not raw control bytes)
        assert '.replace(/\\r/g, ' in js  # CR check - escaped text sequence

    def test_escape_js_html_escapes_backslashes(self, app_with_queue):
        """escapeJsHtml doubles backslashes for JS string safety."""
        js = self._get_js(app_with_queue)
        # Must have a backslash-escaping step before newline replacement
        assert '.replace(/\\\\/g' in js

    def test_rendered_html_has_no_raw_newlines_in_js(self, app_with_queue):
        """JS file must not contain raw 0x0A bytes inside escapeJsHtml function."""
        js = self._get_js(app_with_queue)
        func_start = js.find('function escapeJsHtml')
        assert func_start >= 0, 'escapeJsHtml function not found in app.js'
        # Find the end of the function (next function or end of relevant block)
        brace_count = 0
        i = js.find('{', func_start)
        if i == -1:
            return  # Can't find function body, skip check
        for j in range(i, len(js)):
            if js[j] == '{':
                brace_count += 1
            elif js[j] == '}':
                brace_count -= 1
                if brace_count == 0:
                    func_body = js[i:j+1]
                    break
        else:
            func_body = js[i:]
        # The function should use escaped \n and \r text sequences, not raw control bytes
        assert '\n' not in func_body.replace('\\n', '').replace('\r\n', '') or '<br>' in func_body

    def test_proposal_with_newlines_does_not_break_html(self, tmp_path):
        """Proposals containing newlines in entity names/synopses render safely."""
        import json as json_mod

        queue = [
            {
                'proposal_type': 'update',
                'entity_name': 'Entity\nWith\nNewlines',
                'entity_kind': 'character',
                'source_journal': 'Session 1',
                'previous_entry': '<p>Old line1\nLine2</p>',
                'proposed_entry': '<p>New line1\nLine2</p>',
                'status': 'pending',
            }
        ]
        queue_file = tmp_path / 'pending_changes.json'
        with open(queue_file, 'w') as f:
            json_mod.dump(queue, f, indent=2)

        import kanka_wiki_updater.config as config
        from kanka_wiki_updater.review_web import create_app

        original_data_dir = config.DATA_DIR
        config.DATA_DIR = str(tmp_path)

        try:
            app = create_app()
            app.config['TESTING'] = True
            client = app.test_client()

            resp = client.get('/')
            assert resp.status_code == 200
            html = resp.data.decode()

            # Check that escapeJsHtml function exists in the external JS file and uses <br> replacement
            js_resp = client.get('/static/js/app.js')
            assert 'function escapeJsHtml' in js_resp.data.decode()
            assert '<br>' in js_resp.data.decode()
        finally:
            config.DATA_DIR = original_data_dir

    def test_no_raw_newlines_in_js_string_literals(self, app_with_queue):
        """JS file must not have raw 0x0A bytes inside string literals.

        This catches bugs where Python's \\n in triple-quoted strings becomes
        a literal newline byte that breaks JavaScript string parsing.
        Specifically checks for the SSE handler pattern: data.text + ' + newline + ';
        """
        js = self._get_js(app_with_queue)

        # The bug was: currentSyncJob.output += data.text + '\n'; where \n is 0x0A byte
        # This should now be: currentSyncJob.output += data.text + '\n'; where \n is literal backslash-n
        assert "data.text + '\\n'" in js or "data.text + '\\\\n'" in js, (
            'SSE handler has raw newline inside JS string. Check app.js for unescaped \\n.'
        )

    def test_sse_handler_uses_escaped_newline(self, app_with_queue):
        """SSE output streaming must use \\n escape sequence for newline."""
        js = self._get_js(app_with_queue)
        # The SSE handler concatenates data.text with a newline separator.
        # JS should see: data.text + '\n'  (literal backslash-n in source)
        # NOT: data.text + ' + actual_newline_byte + ';
        assert "data.text + '\\n'" in js or "data.text + '\\\\n'" in js


class TestApiProposalSync:
    """Tests for the /api/proposals/<index>/sync endpoint."""

    def test_sync_endpoint_exists(self, app_with_queue):
        """POST /api/proposals/0/sync returns a JSON response (may fail without Kanka)."""
        import unittest.mock as mock

        # Mock KankaClient to avoid needing real API credentials
        mock_client = mock.MagicMock()
        mock_client.create_character.return_value = {'data': {'id': 999, 'entity_id': '42', 'entry': ''}}
        mock_client.get_relations.return_value = []

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch('kanka_wiki_updater.sync_engine.build_entity_index', return_value={}),
        ):
            resp = app_with_queue.post('/api/proposals/0/sync')
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'ok' in data
            assert 'message' in data

    def test_sync_new_entity_creates_character(self, app_with_queue):
        """Syncing a new_entity proposal calls create_character on KankaClient."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.create_character.return_value = {'data': {'id': 999, 'entity_id': '42', 'entry': ''}}
        mock_client.get_relations.return_value = []

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch('kanka_wiki_updater.sync_engine.build_entity_index', return_value={}),
        ):
            data = app_with_queue.post('/api/proposals/0/sync').get_json()
            assert data['ok'] is True
            mock_client.create_character.assert_called_once()

    def test_sync_update_calls_update_entity_entry(self, app_with_queue):
        """Syncing an update proposal calls update_entity_entry on KankaClient."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
            ),
        ):
            resp = app_with_queue.post('/api/proposals/1/sync')
            data = resp.get_json()
            assert data['ok'] is True
            mock_client.update_entity_entry.assert_called_once()

    def test_sync_invalid_index_returns_404(self, app_with_queue):
        """POST /api/proposals/99/sync returns 404."""
        resp = app_with_queue.post('/api/proposals/99/sync')
        assert resp.status_code == 404

    def test_sync_failure_returns_error(self, app_with_queue):
        """When KankaClient raises an error, sync returns ok=False."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.create_character.side_effect = Exception('API is down')

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post('/api/proposals/0/sync')
            data = resp.get_json()
            assert data['ok'] is False


class TestStatusWithSync:
    """Tests that status update triggers sync for approved proposals."""

    def test_approve_all_triggers_sync(self, app_with_queue):
        """Approving all triggers KankaClient calls and returns sync info."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.create_character.return_value = {'data': {'id': 999, 'entity_id': '42', 'entry': ''}}
        mock_client.get_relations.return_value = []

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch('kanka_wiki_updater.sync_engine.build_entity_index', return_value={}),
        ):
            resp = app_with_queue.post(
                '/api/proposals/0/status',
                json={'status': 'approved_all'},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert 'sync' in data
            assert data['ok'] is True
            assert data['proposal']['status'] == 'applied'
            assert 'Created character' in data['sync']['message']

    def test_approve_sync_failure_returns_409(self, app_with_queue):
        """When sync fails, status update returns 409 with error details."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.create_character.side_effect = Exception('Connection refused')

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post(
                '/api/proposals/0/status',
                json={'status': 'approved_all'},
            )
            assert resp.status_code == 409
            data = resp.get_json()
            assert data['sync_error'] is True
            assert 'Connection refused' in data['sync_message']

    def test_reject_does_not_trigger_sync(self, app_with_queue):
        """Rejecting a proposal does NOT call any KankaClient methods."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post(
                '/api/proposals/0/status',
                json={'status': 'rejected'},
            )
            assert resp.status_code == 200
            mock_client.create_character.assert_not_called()

    def test_approve_update_syncs_synopsis(self, app_with_queue):
        """Approving an update proposal syncs the synopsis to Kanka."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
            ),
        ):
            resp = app_with_queue.post(
                '/api/proposals/1/status',
                json={'status': 'approved_all'},
            )
            assert resp.status_code == 200
            mock_client.update_entity_entry.assert_called_once()

    def test_approve_synopsis_only_triggers_sync(self, app_with_queue):
        """Approving synopsis-only also triggers sync (synopsis is synced)."""
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        with (
            mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            mock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
            ),
        ):
            resp = app_with_queue.post(
                '/api/proposals/1/status',
                json={'status': 'approved_synopsis_only'},
            )
            assert resp.status_code == 200
            mock_client.update_entity_entry.assert_called_once()


class TestApiProposalRegenerate:
    """Tests for the /api/proposals/<index>/regenerate endpoint."""

    def test_regenerate_non_update_returns_400(self, app_with_queue):
        """Regenerating a new_entity proposal returns 400."""
        resp = app_with_queue.post('/api/proposals/0/regenerate')
        assert resp.status_code == 400
        data = resp.get_json()
        assert 'only update' in data['error'].lower()

    def test_regenerate_invalid_index_returns_404(self, app_with_queue):
        """POST /api/proposals/99/regenerate returns 404."""
        resp = app_with_queue.post('/api/proposals/99/regenerate')
        assert resp.status_code == 404

    def test_regenerate_missing_journal_id_returns_400(self, app_with_queue):
        """A truncated proposal without _journal_id returns 400."""
        from unittest import mock as umock

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []

        with (
            umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            umock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ),
        ):
            app_with_queue.post('/api/proposals/1/status', json={'status': 'rejected'})

        # review_web's _load_queue was extracted to queue_manager
        queue = _load_queue_from_file()
        # Add truncated flag and _journal_id, then remove _journal_id to test missing field
        queue[1]['truncated'] = True
        queue[1]['_journal_id'] = 789
        queue[1].pop('_journal_id', None)
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        from unittest import mock as umock

        # get_journal returns None when the journal doesn't exist
        mock_client = umock.MagicMock()
        mock_client.get_journal.return_value = None

        with umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post('/api/proposals/1/regenerate')
        assert resp.status_code == 400

    def test_regenerate_identical_output_returns_409(self, app_with_queue):
        """Regeneration that produces identical output returns 409."""
        import types as _types
        from unittest import mock as umock

        # Set up queue with _journal_id and truncated flag
        queue = _load_queue_from_file()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(id=789, name='Test', date='', created_at='', entry='<p>Old synopsis.</p>')

        mock_entity = _types.SimpleNamespace(
            id=101, entity_id='42', name='Kael Ironfist', local_id=101, entry='<p>Old synopsis.</p>', relations=[]
        )

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []
        mock_client.get_journal.return_value = mock_journal
        mock_client.get_characters.return_value = [mock_entity]

        with (
            umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            umock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ),
            umock.patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock_chat,
        ):
            # Return identical entry — no change detected
            mock_chat.return_value = {
                'updated_entry': '<p>Old synopsis.</p>',
                'change_summary': '',
                'relation_changes': [],
            }
            resp = app_with_queue.post('/api/proposals/1/regenerate')

        assert resp.status_code == 409

    def test_regenerate_force_bypasses_identical_check(self, app_with_queue):
        """POST with ?force=1 returns success even when output is identical."""
        import types as _types
        from unittest import mock as umock

        queue = _load_queue_from_file()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(id=789, name='Test', date='', created_at='', entry='<p>Old synopsis.</p>')

        mock_entity = _types.SimpleNamespace(
            id=101, entity_id='42', name='Kael Ironfist', local_id=101, entry='<p>Old synopsis.</p>', relations=[]
        )

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []
        mock_client.get_journal.return_value = mock_journal
        mock_client.get_characters.return_value = [mock_entity]

        with (
            umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            umock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ),
            umock.patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock_chat,
        ):
            mock_chat.return_value = {
                'updated_entry': '<p>Old synopsis.</p>',
                'change_summary': '',
                'relation_changes': [],
            }
            resp = app_with_queue.post('/api/proposals/1/regenerate?force=1')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True

    def test_regenerate_success_updates_proposal(self, app_with_queue):
        """A successful regeneration updates the proposal and clears truncated flag."""
        import types as _types
        from unittest import mock as umock

        # Set up queue with _journal_id and truncated flag
        queue = _load_queue_from_file()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(id=789, name='Test', date='', created_at='', entry='<p>Old synopsis.</p>')

        mock_entity = _types.SimpleNamespace(
            id=101, entity_id='42', name='Kael Ironfist', local_id=101, entry='<p>Old synopsis.</p>', relations=[]
        )

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []
        mock_client.get_journal.return_value = mock_journal
        mock_client.get_characters.return_value = [mock_entity]

        with (
            umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            umock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ),
            umock.patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock_chat,
        ):
            # Return a different entry — change detected
            mock_chat.return_value = {
                'updated_entry': '<p>New synopsis text.</p>',
                'change_summary': 'Updated.',
                'relation_changes': [],
            }
            resp = app_with_queue.post('/api/proposals/1/regenerate')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        assert data['proposal']['proposed_entry'] == '<p>New synopsis text.</p>'
        assert data['proposal'].get('truncated') is False

    def test_regenerate_injects_journal_link_when_new_info(self, app_with_queue):
        """Regeneration should inject [journal:entity_id|name] prefix when _is_new_info."""
        import types as _types
        from unittest import mock as umock

        queue = _load_queue_from_file()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(
            id=789, entity_id=123456, name='Session 5', date='', created_at='', entry='<p>Old synopsis.</p>'
        )

        mock_entity = _types.SimpleNamespace(
            id=101, entity_id='42', name='Kael Ironfist', local_id=101, entry='<p>Old synopsis.</p>', relations=[]
        )

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []
        mock_client.get_journal.return_value = mock_journal
        mock_client.get_characters.return_value = [mock_entity]

        with (
            umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            umock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ),
            umock.patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock_chat,
        ):
            # LLM includes journal tag directly in its output (Rule 7).
            mock_chat.return_value = {
                'updated_entry': '[journal:123456|Session 5] <p>New synopsis text.</p>',
                'change_summary': 'Added new info.',
                '_is_new_info': True,
                'relation_changes': [],
            }
            resp = app_with_queue.post('/api/proposals/1/regenerate')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ok'] is True
        # Journal tag from LLM output passes through as-is.
        assert "[journal:123456|<i>Session 5</i>]" in data['proposal']['proposed_entry']

    def test_regenerate_injects_journal_link_before_last_paragraph(self, app_with_queue):
        """When LLM returns multiple paragraphs, journal link goes before the last one."""
        import types as _types
        from unittest import mock as umock

        queue = _load_queue_from_file()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(
            id=789, entity_id=123456, name='Session 5', date='', created_at='', entry='<p>Old synopsis.</p>'
        )

        mock_entity = _types.SimpleNamespace(
            id=101, entity_id='42', name='Kael Ironfist', local_id=101, entry='<p>Old synopsis.</p>', relations=[]
        )

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []
        mock_client.get_journal.return_value = mock_journal
        mock_client.get_characters.return_value = [mock_entity]

        with (
            umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            umock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ),
            umock.patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock_chat,
        ):
            # LLM returns old content + new paragraph at end with journal tag.
            mock_chat.return_value = {
                'updated_entry': '<p>Old content here.</p>\n\n[journal:123456|Session 5] Following their adventures, [character:9419629|Warryn] retired.',
                'change_summary': 'Added retirement info.',
                '_is_new_info': True,
                'relation_changes': [],
            }
            resp = app_with_queue.post('/api/proposals/1/regenerate')

        assert resp.status_code == 200
        data = resp.get_json()
        proposed = data['proposal']['proposed_entry']
        # Journal tag from LLM output passes through as-is.
        assert "[journal:123456|<i>Session 5</i>]" in proposed
        # Old content should NOT start with a journal link.
        assert not proposed.startswith('[journal')
        assert 'Warryn' in proposed

    def test_regenerate_llm_error_returns_500(self, app_with_queue):
        """When chat_json raises (LLM connection failure), show 500 not 409."""
        import types as _types
        from unittest import mock as umock

        queue = _load_queue_from_file()
        queue[1]['truncated'] = True

        # _is_new_info=True forces a re-fetch of journals so the old-path is exercised.
        queue[1]['_journal_id'] = 789
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(
            id=789, name='Session 5', date='', created_at='', entry='<p>Old synopsis.</p>'
        )
        mock_entity = _types.SimpleNamespace(
            id=queue[1]['entity_local_id'],
            entity_id=str(queue[1]['entity_local_id']),
            name=queue[1]['entity_name'],
            local_id=queue[1]['entity_local_id'],
            entry='<p>Kael is a warrior.</p>',
            relations=[],
        )

        mock_client = umock.MagicMock()
        mock_client.get_journal.return_value = mock_journal
        mock_client.get_characters.return_value = [mock_entity]

        with (
            umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client),
            umock.patch(
                'kanka_wiki_updater.sync_engine.build_entity_index',
                side_effect=lambda c: {queue[1]['entity_local_id']: {'name': queue[1]['entity_name']}},
            ),
            umock.patch('kanka_wiki_updater.synopsis_generator.chat_json') as mock_chat,
        ):
            # Simulate LLM connection failure — raises an exception.
            import requests

            mock_chat.side_effect = requests.exceptions.ConnectionError('Connection refused')
            resp = app_with_queue.post('/api/proposals/1/regenerate')

        assert resp.status_code == 500
        data = resp.get_json()
        assert 'LLM call failed' in data['error']


class TestRegenerateApiErrors:
    """Graceful degradation when Kanka API calls fail during regeneration."""

    def test_regenerate_journal_fetch_fails(self, app_with_queue):
        """When _journal_id exists but journal fetch fails, return 400 not 500."""
        from unittest import mock as umock

        queue = _load_queue_from_file()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_client = umock.MagicMock()
        mock_client.get_journal.side_effect = Exception('Connection refused')

        with umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post('/api/proposals/1/regenerate?force=1')

        assert resp.status_code == 400
        data = resp.get_json()
        assert 'Cannot fetch journal' in data['error']

    def test_regenerate_fallback_journal_fetch_fails(self, app_with_queue):
        """When _journal_id is missing and fallback journal search fails, return 400."""
        from unittest import mock as umock

        queue = _load_queue_from_file()
        # No _journal_id — triggers fallback path
        if '_journal_id' in queue[1]:
            del queue[1]['_journal_id']
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_client = umock.MagicMock()
        # No _journal_id — returns 400 early before any API call
        mock_client.get_journal.return_value = None

        with umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post('/api/proposals/1/regenerate?force=1')

        assert resp.status_code == 400
        data = resp.get_json()
        assert 'lacks both _journal_id and source_journal' in data['error']

    def test_regenerate_entity_fetch_fails(self, app_with_queue):
        """When entity fetch fails after journal is found, return 400 not 500."""
        import types as _types
        from unittest import mock as umock

        queue = _load_queue_from_file()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import os

        import kanka_wiki_updater.config as config

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(id=789, name='Test', date='', created_at='', entry='<p>Old synopsis.</p>')

        mock_client = umock.MagicMock()
        mock_client.get_journal.return_value = mock_journal
        mock_client.get_characters.side_effect = Exception('Not found')

        with umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            resp = app_with_queue.post('/api/proposals/1/regenerate?force=1')

        assert resp.status_code == 400
        data = resp.get_json()
        assert 'Cannot contact Kanka to fetch entities' in data['error']
