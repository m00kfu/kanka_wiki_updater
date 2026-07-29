import json
import os
import types
import unittest.mock as mock

import pytest


@pytest.fixture(autouse=True, scope='session')
def mock_env():
    """Set minimal env vars for modules that require them."""
    os.environ.setdefault('KANKA_TOKEN', 'test-token')
    os.environ.setdefault('KANKA_CAMPAIGN_ID', '1')
    # Force relation generation on in tests so existing behaviour is preserved.
    # (the local .env file may have GENERATE_RELATIONS=0 for dev convenience).
    # Config module reads env at import time, so we reload it here after
    # setting the variable to ensure tests always run with relations enabled.
    os.environ['GENERATE_RELATIONS'] = 'true'
    import kanka_wiki_updater.core.config as config
    _raw = os.environ.get('GENERATE_RELATIONS', '').strip()
    if _raw:
        config.GENERATE_RELATIONS = _raw.lower() not in ('false', '0', '')
    else:
        config.GENERATE_RELATIONS = True
    yield


@pytest.fixture
def mock_requests(monkeypatch):
    """Provide a mocked requests module."""
    monkeypatch.setattr('kanka_wiki_updater.llm.providers.requests', mock)


# ── Shared queue fixtures (moved from test_review_web.py) ───────────────────


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
    with open(queue_file, 'w') as f:
        json.dump(queue, f, indent=2)
    return str(queue_file), tmp_path


@pytest.fixture
def app_with_queue(mock_queue):
    """Create a Flask test client with the review_web app and a temp queue file."""
    from kanka_wiki_updater.review.web import create_app

    _queue_file, data_dir = mock_queue
    # Override DATA_DIR so state.py reads our temp file
    import kanka_wiki_updater.core.config as config
    import kanka_wiki_updater.review.web as rw

    original_data_dir = config.DATA_DIR
    config.DATA_DIR = str(data_dir)

    # Reset module-level sync job state between tests
    rw._sync_jobs.clear()
    from kanka_wiki_updater.sync import sync_orchestrator as so
    so._jobs.clear()

    app = create_app()
    app.config['TESTING'] = True

    client = app.test_client()

    yield client

    # Restore original DATA_DIR after test
    config.DATA_DIR = original_data_dir
