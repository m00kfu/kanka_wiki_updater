"""Tests for RelationTypeTracker — store, lookup, suggest, persist."""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanka_wiki_updater.sync.relation_types import (
    DEFAULT_RELATION_TYPES,
    RelationTypeTracker,
    ensure_seeded,
    get_inverse_label,
    is_symmetric_relation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def rt_db(tmp_path, monkeypatch):
    """Point the core DB at a fresh SQLite file in tmp_path."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()
    # Drop all tables to ensure clean schema (e.g. after a previous test)
    conn = db.connect()
    conn.execute('DROP TABLE IF EXISTS known_relation_types')
    conn.execute('DROP TABLE IF EXISTS meta')
    conn.execute('DROP TABLE IF EXISTS proposals')
    conn.execute('DROP TABLE IF EXISTS tree_state')
    conn.execute('DROP TABLE IF EXISTS applied_batches')
    conn.execute('DROP TABLE IF EXISTS processed_journals')
    db.init_db()
    yield tmp_path
    db.close_all()


def _tmp_tracker(data_dir=None, **kwargs):
    """Create a tracker with a temporary data directory."""
    if data_dir is None:
        import tempfile
        data_dir = tempfile.mkdtemp()
    return RelationTypeTracker(data_dir=str(data_dir), **kwargs)


# ---------------------------------------------------------------------------
# Persistence (SQLite-backed)
# ---------------------------------------------------------------------------


def test_load_save_roundtrip(rt_db):
    """Persist + reload preserves counts."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    config.DATA_DIR = str(rt_db)
    db.close_all()
    db.init_db()

    t = RelationTypeTracker(data_dir=str(rt_db))
    t.add_type('Ally')
    t.add_type('Enemy')
    t.add_type('Ally')  # increment count
    t.save()

    db.close_all()
    t2 = RelationTypeTracker(data_dir=str(rt_db))
    t2.load()
    assert t2.known_types == {'Ally': 2, 'Enemy': 1}


def test_load_nonexistent_db_is_empty():
    """Missing DB → empty known set."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Don't init the DB — just load from a tracker pointing there
        t = RelationTypeTracker(data_dir=tmpdir)
        t.load()
        assert t.known_types == {}


def test_save_creates_directory(rt_db):
    """save() creates parent directories if they don't exist."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    config.DATA_DIR = str(rt_db)
    db.close_all()
    db.init_db()

    nested = os.path.join(str(rt_db), 'a', 'b')
    t = RelationTypeTracker(data_dir=nested)
    t.add_type('Test')
    t.save()
    assert os.path.isfile(os.path.join(nested, 'kanka_wiki_updater.db'))


def test_load_corrupt_db_is_empty():
    """Corrupt/missing DB → falls back to empty set."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # No DB file at all — load should return empty
        t = RelationTypeTracker(data_dir=tmpdir)
        t.load()
        assert t.known_types == {}


# ---------------------------------------------------------------------------
# Lookup & suggestion
# ---------------------------------------------------------------------------


def test_is_known_case_insensitive():
    """'ALLY'.is_known() == True when 'Ally' is in known set."""
    t = _tmp_tracker()
    t.add_type('Ally')
    assert t.is_known('ally')
    assert t.is_known('ALLY')
    assert t.is_known('Ally')


def test_is_known_empty_label():
    """Empty or whitespace-only labels return False."""
    t = _tmp_tracker()
    t.add_type('Ally')
    assert not t.is_known('')
    assert not t.is_known('   ')
    assert not t.is_known(None)  # type: ignore[arg-type]


def test_is_known_unknown():
    """Unknown label returns False."""
    t = _tmp_tracker()
    t.add_type('Ally')
    assert not t.is_known('Enemy')


def test_suggest_similar_exact_match_returns_empty():
    """Exact known type → no suggestions (ratio=1.0, excluded)."""
    t = _tmp_tracker()
    t.add_type('Ally')
    assert t.suggest_similar('Ally') == []


def test_suggest_similar_fuzzy():
    """'Allie' should suggest 'Ally'."""
    t = _tmp_tracker()
    t.add_type('Ally')
    suggestions = t.suggest_similar('Allie')
    assert 'Ally' in suggestions


def test_suggest_similar_empty_when_no_known():
    """No known types → empty suggestions."""
    t = _tmp_tracker()
    assert t.suggest_similar('Anything') == []


def test_suggest_similar_empty_label():
    """Empty label → no suggestions."""
    t = _tmp_tracker()
    t.add_type('Ally')
    assert t.suggest_similar('') == []


def test_get_sorted_labels_by_frequency():
    """Labels sorted by count descending, then alphabetically."""
    t = _tmp_tracker()
    for _ in range(5):
        t.add_type('Ally')
    for _ in range(3):
        t.add_type('Enemy')
    t.add_type('Friend')

    labels = t.get_sorted_labels()
    assert labels[0] == 'Ally'   # 5 occurrences
    assert labels[1] == 'Enemy'  # 3 occurrences
    assert labels[2] == 'Friend' # 1 occurrence


def test_get_sorted_labels_limit():
    """Limit parameter caps the returned list."""
    t = _tmp_tracker()
    for i in range(10):
        t.add_type(f'Type{i}')
    result = t.get_sorted_labels(limit=3)
    assert len(result) == 3


def test_get_sorted_labels_empty():
    """No known types → empty list."""
    t = _tmp_tracker()
    assert t.get_sorted_labels() == []


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def test_add_type_increments_count():
    """Adding a type increments its count."""
    t = _tmp_tracker()
    t.add_type('Ally')
    t.add_type('Ally')
    assert t.known_types['Ally'] == 2


def test_merge_new_types_updates_counts():
    """merge_new_types adds multiple labels and increments counts."""
    t = _tmp_tracker()
    t.merge_new_types(['Ally', 'Enemy', 'Ally'])
    assert t.known_types == {'Ally': 2, 'Enemy': 1}


def test_add_type_empty_label_noop():
    """Empty label is silently ignored."""
    t = _tmp_tracker()
    t.add_type('')
    t.add_type('   ')
    assert t.known_types == {}


# ---------------------------------------------------------------------------
# Backward-compatible enrichment
# ---------------------------------------------------------------------------


def test_enrich_proposals_adds_fields():
    """Loaded proposal gets _type_status + similar_types."""
    t = _tmp_tracker()
    t.add_type('Ally')

    proposals = [
        {
            'relation_changes': [
                {'action': 'create', 'target_name': 'Bob', 'relation': 'Allie'},  # typo → similar to Ally
                {'action': 'update', 'target_name': 'Carol', 'relation': 'Ally'},
            ],
        },
    ]
    t.enrich_proposals(proposals)

    rc0 = proposals[0]['relation_changes'][0]
    assert rc0['_type_status'] == 'new_suggested'
    assert 'Ally' in rc0['similar_types'], f"Expected 'Ally' in similar_types but got {rc0['similar_types']}"

    rc1 = proposals[0]['relation_changes'][1]
    assert rc1['_type_status'] == 'known'
    assert rc1['similar_types'] == []


def test_enrich_proposals_backward_compat():
    """Proposals without prior enrichment get populated."""
    t = _tmp_tracker()
    t.add_type('Ally')

    proposals = [
        {
            'relation_changes': [
                {'action': 'create', 'target_name': 'Bob', 'relation': 'Enemy'},
            ],
        },
    ]
    # Enrich once
    t.enrich_proposals(proposals)
    assert proposals[0]['relation_changes'][0]['_type_status'] == 'new_suggested'

    # Enrich again (idempotent — should not change _type_status)
    t.enrich_proposals(proposals)
    assert proposals[0]['relation_changes'][0]['_type_status'] == 'new_suggested'


def test_enrich_proposals_empty_rels():
    """Proposals with no relation_changes are left unchanged."""
    t = _tmp_tracker()
    proposals = [{'relation_changes': []}]
    t.enrich_proposals(proposals)
    assert proposals[0]['relation_changes'] == []


def test_enrich_proposals_missing_key():
    """Proposal without relation_changes key is handled gracefully."""
    t = _tmp_tracker()
    proposals = [{}]  # no 'relation_changes' key
    t.enrich_proposals(proposals)
    assert 'relation_changes' not in proposals[0]


# ---------------------------------------------------------------------------
# Persistence format (SQLite)
# ---------------------------------------------------------------------------


def test_save_format(rt_db):
    """Saved data is stored in the known_relation_types table."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    config.DATA_DIR = str(rt_db)
    db.close_all()
    db.init_db()

    t = RelationTypeTracker(data_dir=str(rt_db))
    t.add_type('Ally')
    t.save()

    conn = db.connect()
    row = conn.execute(
        'SELECT label, count FROM known_relation_types WHERE label = ?', ('Ally',)
    ).fetchone()
    assert row is not None
    assert row['label'] == 'Ally'
    assert row['count'] == 1


def test_load_preserves_last_scraped(rt_db):
    """load() restores last_scraped_at timestamp."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    config.DATA_DIR = str(rt_db)
    db.close_all()
    db.init_db()

    t = RelationTypeTracker(data_dir=str(rt_db))
    t._last_scraped_at = 12345.0
    t.save()

    db.close_all()
    t2 = RelationTypeTracker(data_dir=str(rt_db))
    t2.load()
    assert t2._last_scraped_at == 12345.0


# ---------------------------------------------------------------------------
# load_from_client (mock-based)
# ---------------------------------------------------------------------------


def test_load_from_client_populated(rt_db):
    """Entities with various relation labels → correct counts."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    config.DATA_DIR = str(rt_db)
    db.close_all()
    db.init_db()

    t = RelationTypeTracker(data_dir=str(rt_db))

    mock_client = mock.MagicMock()
    mock_client.get_characters.return_value = [
        {
            'relations': [
                {'relation': 'Ally', 'target_id': 1},
                {'relation': 'Enemy', 'target_id': 2},
            ],
        },
        {
            'relations': [
                {'relation': 'Ally', 'target_id': 3},
            ],
        },
    ]
    mock_client.get_locations.return_value = []
    mock_client.get_organizations.return_value = []
    mock_client.get_creatures.return_value = []

    t.load_from_client(mock_client)

    assert t.known_types == {'Ally': 2, 'Enemy': 1}


def test_load_from_client_empty_campaign(rt_db):
    """No relations on any entity → empty known set."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    config.DATA_DIR = str(rt_db)
    db.close_all()
    db.init_db()

    t = RelationTypeTracker(data_dir=str(rt_db))

    mock_client = mock.MagicMock()
    for fn in ('get_characters', 'get_locations', 'get_organizations', 'get_creatures'):
        getattr(mock_client, fn).return_value = []

    t.load_from_client(mock_client)

    assert t.known_types == {}


def test_load_from_client_api_error_is_tolerated(rt_db):
    """API error for one kind doesn't prevent others from being scraped."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    config.DATA_DIR = str(rt_db)
    db.close_all()
    db.init_db()

    t = RelationTypeTracker(data_dir=str(rt_db))

    mock_client = mock.MagicMock()
    mock_client.get_characters.side_effect = Exception('Network error')
    mock_client.get_locations.return_value = [
        {'relations': [{'relation': 'Sacred Site', 'target_id': 1}]},
    ]
    mock_client.get_organizations.return_value = []
    mock_client.get_creatures.return_value = []

    t.load_from_client(mock_client)

    assert t.known_types == {'Sacred Site': 1}


# ---------------------------------------------------------------------------
# Symmetric relations & inverse mapping (Phase: reciprocal generation)
# ---------------------------------------------------------------------------


def test_is_symmetric_relation():
    """Known symmetric types return True; asymmetric/unknown return False."""
    assert is_symmetric_relation('Ally') is True
    assert is_symmetric_relation('enemy') is True
    assert is_symmetric_relation('Rival') is True
    assert is_symmetric_relation('spouse') is True
    # Asymmetric types return False
    assert is_symmetric_relation('parent') is False
    assert is_symmetric_relation('child') is False
    assert is_symmetric_relation('mother') is False
    # Unknown/empty returns False
    assert is_symmetric_relation('Blood Oath') is False
    assert is_symmetric_relation('') is False


def test_get_inverse_label():
    """Inverse mapping works for known pairs; unknown falls back to self."""
    assert get_inverse_label('parent') == 'child'
    assert get_inverse_label('child') == 'parent'
    assert get_inverse_label('mother') == 'son'
    assert get_inverse_label('father') == 'son'
    # Symmetric types not in inverse map — fall through to same label default
    assert get_inverse_label('ally') == 'ally'  # not in INVERSE_RELATIONS, defaults to self
    # Unknown type falls back to same label (safe symmetric assumption)
    assert get_inverse_label('Blood Oath') == 'Blood Oath'


def test_get_inverse_label_case_insensitive():
    """'PARENT', 'Parent', 'parent' all map to 'child'."""
    assert get_inverse_label('PARENT') == 'child'
    assert get_inverse_label('Child') == 'parent'


# ---------------------------------------------------------------------------
# Default seeding (Phase: default relation types)
# ---------------------------------------------------------------------------


def test_seed_defaults_populates_empty_tracker(tmp_path):
    """seed_defaults() fills known_types when empty."""
    t = RelationTypeTracker(data_dir=str(tmp_path))
    assert t.seed_defaults() is True
    assert len(t.known_types) == len(DEFAULT_RELATION_TYPES)


def test_seed_defaults_noop_when_populated():
    """seed_defaults() returns False and does nothing if types exist."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        t = RelationTypeTracker(data_dir=tmpdir)
        t.add_type('Ally')
        assert t.seed_defaults() is False


def test_ensure_seeded_creates_persistence_file(tmp_path, monkeypatch):
    """ensure_seeded() seeds the DB table when it's empty."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()

    result = ensure_seeded(data_dir=str(tmp_path))
    assert result is True

    conn = db.connect()
    count_row = conn.execute('SELECT COUNT(*) AS c FROM known_relation_types').fetchone()
    assert count_row['c'] == len(DEFAULT_RELATION_TYPES)
    db.close_all()


def test_ensure_seeded_skips_existing_data(tmp_path, monkeypatch):
    """ensure_seeded() does nothing when the table already has rows."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()
    db.init_db()

    # Pre-seed one type directly in the DB
    with db.transaction() as conn:
        conn.execute('INSERT INTO known_relation_types (label, count) VALUES (?, ?)', ('Custom', 1))
    # close before ensure_seeded (it closes connections too)
    db.close_all()

    result = ensure_seeded(data_dir=str(tmp_path))
    assert result is False

    # Should still have exactly one row (the custom one)
    conn = db.connect()
    count_row = conn.execute('SELECT COUNT(*) AS c FROM known_relation_types').fetchone()
    assert count_row['c'] == 1
    db.close_all()


def test_ensure_seeded_idempotent(tmp_path, monkeypatch):
    """Calling ensure_seeded twice does not double-seed."""
    import kanka_wiki_updater.core.config as config
    from kanka_wiki_updater.core import db
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path))
    db.close_all()

    result1 = ensure_seeded(data_dir=str(tmp_path))
    assert result1 is True

    # Second call should be a no-op (table now has rows)
    result2 = ensure_seeded(data_dir=str(tmp_path))
    assert result2 is False


def test_attitude_guidance_text_format():
    """attitude_guidance_text() returns non-empty, multi-line string."""
    from kanka_wiki_updater.sync.default_attitudes import attitude_guidance_text
    text = attitude_guidance_text()
    assert isinstance(text, str)
    assert len(text) > 50
    # Should contain at least one baseline entry
    assert '+15' in text or '-80' in text
