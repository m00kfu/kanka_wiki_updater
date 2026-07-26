"""Tests for RelationTypeTracker — store, lookup, suggest, persist."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanka_wiki_updater.sync.relation_types import RelationTypeTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_tracker(**kwargs):
    """Create a tracker with a temporary data directory."""
    tmpdir = Path(kwargs.pop('data_dir', '/tmp/rt_test'))
    tmpdir.mkdir(exist_ok=True)
    return RelationTypeTracker(data_dir=str(tmpdir), **kwargs)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_load_save_roundtrip():
    """Persist + reload preserves counts."""
    t = _tmp_tracker()
    t.add_type('Ally')
    t.add_type('Enemy')
    t.add_type('Ally')  # increment count
    t.save()

    t2 = RelationTypeTracker(data_dir=t.data_dir)
    t2.load()
    assert t2.known_types == {'Ally': 2, 'Enemy': 1}


def test_load_nonexistent_file_is_empty():
    """Missing file → empty known set."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        t = RelationTypeTracker(data_dir=tmpdir)
        t.load()
        assert t.known_types == {}


def test_save_creates_directory():
    """save() creates parent directories if they don't exist."""
    import shutil, tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        nested = os.path.join(tmpdir, 'a', 'b')
        t = RelationTypeTracker(data_dir=nested)
        t.add_type('Test')
        t.save()
        assert os.path.isfile(os.path.join(nested, 'known_relation_types.json'))


def test_load_corrupt_json_is_empty():
    """Corrupt JSON file → falls back to empty set."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, 'known_relation_types.json')
        with open(path, 'w') as f:
            f.write('not json {{{')
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
# Persistence format
# ---------------------------------------------------------------------------


def test_save_format():
    """Saved JSON has the expected structure."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        t = RelationTypeTracker(data_dir=tmpdir)
        t.add_type('Ally')
        t.save()

        path = os.path.join(tmpdir, 'known_relation_types.json')
        with open(path) as f:
            data = json.load(f)
        assert 'known_types' in data
        assert 'last_scraped_at' in data
        assert data['known_types'] == {'Ally': 1}


def test_load_preserves_last_scraped():
    """load() restores last_scraped_at timestamp."""
    import tempfile, time
    with tempfile.TemporaryDirectory() as tmpdir:
        t = RelationTypeTracker(data_dir=tmpdir)
        t._last_scraped_at = 12345.0
        t.save()

        t2 = RelationTypeTracker(data_dir=tmpdir)
        t2.load()
        assert t2._last_scraped_at == 12345.0


# ---------------------------------------------------------------------------
# load_from_client (mock-based)
# ---------------------------------------------------------------------------


def test_load_from_client_populated():
    """Entities with various relation labels → correct counts."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        t = RelationTypeTracker(data_dir=tmpdir)

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


def test_load_from_client_empty_campaign():
    """No relations on any entity → empty known set."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        t = RelationTypeTracker(data_dir=tmpdir)

        mock_client = mock.MagicMock()
        for fn in ('get_characters', 'get_locations', 'get_organizations', 'get_creatures'):
            getattr(mock_client, fn).return_value = []

        t.load_from_client(mock_client)

        assert t.known_types == {}


def test_load_from_client_api_error_is_tolerated():
    """API error for one kind doesn't prevent others from being scraped."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        t = RelationTypeTracker(data_dir=tmpdir)

        mock_client = mock.MagicMock()
        mock_client.get_characters.side_effect = Exception('Network error')
        mock_client.get_locations.return_value = [
            {'relations': [{'relation': 'Sacred Site', 'target_id': 1}]},
        ]
        mock_client.get_organizations.return_value = []
        mock_client.get_creatures.return_value = []

        t.load_from_client(mock_client)

        assert t.known_types == {'Sacred Site': 1}
