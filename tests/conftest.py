import json
import os
import types
import unittest.mock as mock

import pytest


# ── SQLite state fixture (Phase 1) ────────────────────────────────────────


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    """Point state at a fresh SQLite DB in tmp_path."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()
    db.init_db()
    yield
    db.close_all()


# ── Environment fixture ────────────────────────────────────────────────────


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
def seed_queue(state_db):
    """Seed the SQLite queue with a list of proposals."""
    def _seed(proposals):
        from kanka_wiki_updater.core import state
        state.save_queue({'proposals': proposals,
                          '_tree_state': {'per_tab': {}}})
    return _seed


@pytest.fixture
def mock_queue(seed_queue):
    """Seed a temporary queue with mixed proposal types."""
    proposals = [
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
    seed_queue(proposals)


@pytest.fixture
def app_with_queue(mock_queue):
    """Create a Flask test client with the review_web app and a seeded queue."""
    from kanka_wiki_updater.review.web import create_app
    import kanka_wiki_updater.review.web as rw

    # Reset module-level sync job state between tests
    rw._sync_jobs.clear()
    from kanka_wiki_updater.sync import sync_orchestrator as so
    so._jobs.clear()

    app = create_app()
    app.config['TESTING'] = True

    client = app.test_client()

    yield client
