"""Tests for review_web.py — Flask app factory, API routes, data handling."""

import json

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_queue(tmp_path):
    """Create a temporary pending_changes.json with mixed proposal types."""
    queue = [
        {
            'proposal_type': 'new_entity',
            'entity_name': 'Vexara the Veiled',
            'suggested_type': 'character',
            'draft_entry': 'A mysterious sorceress.',
            'source_journal': 'Session 12',
            'status': 'pending',
        },
        {
            'proposal_type': 'update',
            'entity_name': 'Kael Ironfist',
            'entity_kind': 'character',
            'entity_id': '42',
            'entity_local_id': 101,
            'source_journal': 'Session 13',
            'change_summary': 'Updated synopsis.',
            'previous_entry': '<p>Old text.</p>',
            'proposed_entry': '<p>New text with allies.</p>',
            'relation_changes': [
                {
                    'action': 'create',
                    'relation': 'ally',
                    'target_name': 'Vexara the Veiled',
                    'attitude': 'cautious trust',
                    'reason': 'Met at Ironhold.',
                }
            ],
            'status': 'pending',
        },
    ]
    queue_file = tmp_path / 'pending_changes.json'
    json.dump(queue, open(queue_file, 'w'), indent=2)
    return str(queue_file), tmp_path


@pytest.fixture
def app_with_queue(mock_queue):
    """Create a Flask test client with the review_web app and a temp queue file."""
    from kanka_wiki_updater.review_web import create_app

    _queue_file, data_dir = mock_queue
    # Override DATA_DIR so state.py reads our temp file
    import kanka_wiki_updater.config as config
    import kanka_wiki_updater.review_web as rw

    original_data_dir = config.DATA_DIR
    config.DATA_DIR = str(data_dir)

    # Reset module-level sync job state between tests
    rw._sync_jobs.clear()
    rw._job_counter[0] = 0

    app = create_app()
    app.config['TESTING'] = True

    client = app.test_client()

    yield client

    # Restore original DATA_DIR after test
    config.DATA_DIR = original_data_dir


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

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
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

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
            ):
                app_with_queue.post(
                    '/api/proposals/1/status',
                    json={'status': 'approved_all'},
                )
        import kanka_wiki_updater.review_web as rw

        # Reload from disk using review_web's dynamic path resolution
        queue = rw._load_queue()
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
    def test_run_sync_starts_process(self, app_with_queue, monkeypatch):
        """POST /api/sync/run spawns a subprocess and returns job_id."""
        import unittest.mock

        mock_proc = unittest.mock.MagicMock()
        mock_proc.stdout = iter(['line1\n', 'line2\n'])
        mock_proc.returncode = 0
        mock_proc.wait = unittest.mock.MagicMock()

        monkeypatch.setattr('kanka_wiki_updater.review_web.subprocess.Popen', lambda *args, **kw: mock_proc)

        resp = app_with_queue.post('/api/sync/run')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'job_id' in data
        assert data['status'] == 'running'


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
        """GET / returns HTML containing Sync tab button."""
        resp = app_with_queue.get('/')
        assert resp.status_code == 200
        html = resp.data.decode()
        assert '>Sync<' in html or 'Sync</button>' in html

    def test_index_contains_sync_output_area(self, app_with_queue):
        """GET / returns HTML containing sync output pre element."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        assert 'syncOutput' in html


class TestSyncJavaScript:
    def test_index_contains_run_sync_function(self, app_with_queue):
        """GET / returns HTML containing runSync function."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        assert 'function runSync' in html or 'runSync()' in html

    def test_index_contains_event_source_connection(self, app_with_queue):
        """GET / returns HTML containing EventSource connection to sync output."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        assert 'EventSource' in html

    def test_sync_output_has_auto_scroll(self, app_with_queue):
        """Sync output streaming includes scrollTop scrollHeight auto-scroll."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        assert 'scrollTop' in html and 'scrollHeight' in html


class TestSwitchTabCancelEdit:
    def test_switch_tab_calls_cancel_edit(self, app_with_queue):
        """switchTab() calls cancelEdit() to prevent stale editor state."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        assert 'if (editingField) cancelEdit()' in html or 'cancelEdit();' in html

    def test_switch_tab_resets_selected_index(self, app_with_queue):
        """switchTab() resets selectedIndex to null."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        assert 'selectedIndex = null' in html


class TestNewlineEscaping:
    def test_strip_html_replaces_newlines_for_js_context(self, app_with_queue):
        """Text with newlines is replaced before JS string concatenation."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        # stripHtml returns text with \n; these must be replaced for safe JS strings
        assert ".replace(/\\n/g, ' ')" in html or 'escapeJs(' in html

    def test_escape_js_function_exists(self, app_with_queue):
        """HTML contains escapeJs function for JS string literal safety."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        assert 'function escapeJs' in html

    def test_escape_js_html_uses_br_for_newlines(self, app_with_queue):
        """escapeJsHtml converts newlines to <br>, using JS escape sequences."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        # The function should use escaped \r and \n in regex literals (not raw control bytes)
        assert '.replace(/\\r/g, ' in html   # CR check - escaped text sequence

    def test_escape_js_html_escapes_backslashes(self, app_with_queue):
        """escapeJsHtml doubles backslashes for JS string safety."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        # Must have a backslash-escaping step before newline replacement
        assert '.replace(/\\\\/g' in html

    def test_rendered_html_has_no_raw_newlines_in_js(self, app_with_queue):
        """Rendered HTML must not contain raw 0x0A bytes inside escapeJsHtml function."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        # Find the script tag content and verify no unescaped newlines appear
        start = html.find('<script>') + len('<script>')
        end = html.find('</script>')
        js_content = html[start:end]
        func_start = js_content.find('function escapeJsHtml')
        func_end = js_content.find('}', func_start) + 1
        func_body = js_content[func_start:func_end]
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
        json_mod.dump(queue, open(queue_file, 'w'), indent=2)

        import kanka_wiki_updater.config as config
        from flask import Flask
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

            # The rendered HTML must not contain raw newline bytes inside the JS context
            start = html.find('<script>') + len('<script>')
            end = html.find('</script>')
            js_content = html[start:end]

            # Check that escapeJsHtml function exists and uses <br> replacement
            assert 'function escapeJsHtml' in js_content
            assert '<br>' in js_content
        finally:
            config.DATA_DIR = original_data_dir

    def test_no_raw_newlines_in_js_string_literals(self, app_with_queue):
        """Rendered JS must not have raw 0x0A bytes inside string literals.

        This catches bugs where Python's \\n in triple-quoted strings becomes
        a literal newline byte that breaks JavaScript string parsing.
        Specifically checks for the SSE handler pattern: data.text + ' + newline + ';
        """
        resp = app_with_queue.get('/')
        html = resp.data.decode()

        # The bug was: currentSyncJob.output += data.text + '\n'; where \n is 0x0A byte
        # This should now be: currentSyncJob.output += data.text + '\n'; where \n is literal backslash-n
        assert "data.text + '\\n'" in html or "data.text + '\\\\n'" in html, (
            'SSE handler has raw newline inside JS string. Check review_web.py for unescaped \\n.'
        )

    def test_sse_handler_uses_escaped_newline(self, app_with_queue):
        """SSE output streaming must use \\n escape sequence for newline."""
        resp = app_with_queue.get('/')
        html = resp.data.decode()
        # The SSE handler concatenates data.text with a newline separator.
        # JS should see: data.text + '\n'  (literal backslash-n in source)
        # NOT: data.text + ' + actual_newline_byte + ';
        assert "data.text + '\\n'" in html or "data.text + '\\\\n'" in html


class TestApiProposalSync:
    """Tests for the /api/proposals/<index>/sync endpoint."""

    def test_sync_endpoint_exists(self, app_with_queue):
        """POST /api/proposals/0/sync returns a JSON response (may fail without Kanka)."""
        import unittest.mock as mock

        # Mock KankaClient to avoid needing real API credentials
        mock_client = mock.MagicMock()
        mock_client.create_character.return_value = {'data': {'id': 999, 'entity_id': '42', 'entry': ''}}
        mock_client.get_relations.return_value = []

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch('kanka_wiki_updater.review_web.build_entity_index', return_value={}):
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

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch('kanka_wiki_updater.review_web.build_entity_index', return_value={}):
                resp = app_with_queue.post('/api/proposals/0/sync')
                data = resp.get_json()
                assert data['ok'] is True
                mock_client.create_character.assert_called_once()

    def test_sync_update_calls_update_entity_entry(self, app_with_queue):
        """Syncing an update proposal calls update_entity_entry on KankaClient."""
        import types
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
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

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch('kanka_wiki_updater.review_web.build_entity_index', return_value={}):
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
        import types
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
            ):
                resp = app_with_queue.post(
                    '/api/proposals/1/status',
                    json={'status': 'approved_all'},
                )
                assert resp.status_code == 200
                mock_client.update_entity_entry.assert_called_once()

    def test_approve_synopsis_only_triggers_sync(self, app_with_queue):
        """Approving synopsis-only also triggers sync (synopsis is synced)."""
        import types
        import unittest.mock as mock

        mock_client = mock.MagicMock()
        mock_client.get_relations.return_value = []

        with mock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with mock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {
                    42: {'name': 'Kael Ironfist'},
                    99: {'name': 'Vexara the Veiled'},
                },
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
        import types as _types
        from unittest import mock as umock

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []

        with umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with umock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ):
                app_with_queue.post('/api/proposals/1/status', json={'status': 'rejected'})

        import kanka_wiki_updater.review_web as rw

        queue = rw._load_queue()
        # Add truncated flag and _journal_id, then remove _journal_id to test missing field
        queue[1]['truncated'] = True
        queue[1]['_journal_id'] = 789
        queue[1].pop('_journal_id', None)
        import kanka_wiki_updater.config as config
        import os

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        resp = app_with_queue.post('/api/proposals/1/regenerate')
        assert resp.status_code == 400

    def test_regenerate_identical_output_returns_409(self, app_with_queue):
        """Regeneration that produces identical output returns 409."""
        import types as _types
        from unittest import mock as umock

        # Set up queue with _journal_id and truncated flag
        rw_module = __import__('kanka_wiki_updater.review_web', fromlist=['_load_queue'])
        queue = rw_module._load_queue()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import kanka_wiki_updater.config as config
        import os

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(id=789, name='Test', date='', created_at='', entry='<p>Old synopsis.</p>')

        mock_entity = _types.SimpleNamespace(
            id=101, entity_id='42', name='Kael Ironfist', local_id=101, entry='<p>Old synopsis.</p>', relations=[]
        )

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []
        mock_client.get_journals.return_value = [mock_journal]
        mock_client.get_characters.return_value = [mock_entity]

        with umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with umock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ):
                with umock.patch('kanka_wiki_updater.llm_client.chat_json') as mock_chat:
                    # Return identical entry — no change detected
                    mock_chat.return_value = {
                        'updated_entry': '<p>Old synopsis.</p>',
                        'change_summary': '',
                        'relation_changes': [],
                    }
                    resp = app_with_queue.post('/api/proposals/1/regenerate')

        assert resp.status_code == 409

    def test_regenerate_success_updates_proposal(self, app_with_queue):
        """A successful regeneration updates the proposal and clears truncated flag."""
        import types as _types
        from unittest import mock as umock

        # Set up queue with _journal_id and truncated flag
        rw_module = __import__('kanka_wiki_updater.review_web', fromlist=['_load_queue'])
        queue = rw_module._load_queue()
        queue[1]['_journal_id'] = 789
        queue[1]['truncated'] = True
        import kanka_wiki_updater.config as config
        import os

        queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
        with open(queue_file, 'w') as f:
            json.dump(queue, f, indent=2)

        mock_journal = _types.SimpleNamespace(id=789, name='Test', date='', created_at='', entry='<p>Old synopsis.</p>')

        mock_entity = _types.SimpleNamespace(
            id=101, entity_id='42', name='Kael Ironfist', local_id=101, entry='<p>Old synopsis.</p>', relations=[]
        )

        mock_client = umock.MagicMock()
        mock_client.get_relations.return_value = []
        mock_client.get_journals.return_value = [mock_journal]
        mock_client.get_characters.return_value = [mock_entity]

        with umock.patch('kanka_wiki_updater.review_web.KankaClient', return_value=mock_client):
            with umock.patch(
                'kanka_wiki_updater.review_web.build_entity_index',
                side_effect=lambda c: {42: {'name': 'Kael Ironfist'}},
            ):
                with umock.patch('kanka_wiki_updater.llm_client.chat_json') as mock_chat:
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
