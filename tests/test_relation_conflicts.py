import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanka_wiki_updater.relation_conflicts import detect_cross_proposal_conflicts, resolve_creates_to_updates


def _make_proposal(entity_name='Alice', relation_changes=None, entity_id=1):
    return {
        'proposal_type': 'update',
        'entity_id': entity_id,
        'entity_kind': 'character',
        'entity_local_id': 1,
        'entity_name': entity_name,
        'source_journal': 'Session 1',
        'previous_entry': '',
        'proposed_entry': '',
        'change_summary': '',
        'relation_changes': relation_changes or [],
        'uncertain': [],
        'status': 'pending',
    }


def _make_rel(action, target_name, relation='Ally'):
    return {
        'action': action,
        'target_name': target_name,
        'relation': relation,
        'attitude': None,
        'reason': '',
    }


def test_no_conflict_when_relation_does_not_exist():
    proposal = _make_proposal(relation_changes=[_make_rel('create', 'Bob')])
    proposals = [proposal]
    entity_index = {
        1: {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'entry': '', 'relations': []},
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(resolved) == 1
    assert resolved[0]['relation_changes'][0]['action'] == 'create'
    assert len(conflicts) == 0


def test_create_converted_to_update_when_relation_exists():
    proposal = _make_proposal(relation_changes=[_make_rel('create', 'Bob')])
    proposals = [proposal]
    entity_index = {
        1: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'entry': '',
            'relations': [{'target_id': 99, 'relation': 'Ally'}],
        },
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
    }
    _resolved, _conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert _resolved[0]['relation_changes'][0]['action'] == 'update'


def test_no_flag_when_labels_match():
    proposal = _make_proposal(relation_changes=[_make_rel('create', 'Bob', relation='Ally')])
    proposals = [proposal]
    entity_index = {
        1: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'entry': '',
            'relations': [{'target_id': 99, 'relation': 'Ally'}],
        },
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
    }
    resolved, _conflicts = resolve_creates_to_updates(proposals, entity_index)

    rc = resolved[0]['relation_changes'][0]
    assert rc.get('conflict') is None


def test_label_mismatch_flagged_and_updated():
    proposal = _make_proposal(relation_changes=[_make_rel('create', 'Bob', relation='Rival')])
    proposals = [proposal]
    entity_index = {
        1: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'entry': '',
            'relations': [{'target_id': 99, 'relation': 'Ally'}],
        },
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    rc = resolved[0]['relation_changes'][0]
    assert rc['action'] == 'update'
    conflict = rc.get('conflict')
    assert conflict is not None
    assert conflict['existing_type'] == 'Ally'
    assert conflict['proposed_type'] == 'Rival'
    assert conflict['conflict_kind'] == 'label_mismatch'
    assert len(conflicts) == 1


def test_update_action_unchanged_when_no_conflict():
    proposal = _make_proposal(relation_changes=[_make_rel('update', 'Bob', relation='Friend')])
    proposals = [proposal]
    entity_index = {
        1: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'entry': '',
            'relations': [{'target_id': 99, 'relation': 'Ally'}],
        },
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(resolved) == 1
    assert resolved[0]['relation_changes'][0]['action'] == 'update'
    assert len(conflicts) == 0


def test_delete_action_unchanged():
    proposal = _make_proposal(relation_changes=[_make_rel('delete', 'Bob')])
    proposals = [proposal]
    entity_index = {
        1: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'entry': '',
            'relations': [{'target_id': 99, 'relation': 'Ally'}],
        },
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
    }
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(resolved) == 1
    assert resolved[0]['relation_changes'][0]['action'] == 'delete'
    assert len(conflicts) == 0


def test_multiple_proposals_returns_all_conflicts():
    rel_changes_1 = [_make_rel('create', 'Bob', relation='Rival')]
    p1 = _make_proposal(entity_name='Alice', entity_id=1, relation_changes=rel_changes_1)
    rel_changes_2 = [_make_rel('create', 'Dave', relation='Enemy')]
    p2 = _make_proposal(entity_name='Carol', entity_id=2, relation_changes=rel_changes_2)
    proposals = [p1, p2]
    entity_index = {
        1: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'entry': '',
            'relations': [{'target_id': 99, 'relation': 'Ally'}],
        },
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
        2: {
            'kind': 'character',
            'local_id': 2,
            'name': 'Carol',
            'entry': '',
            'relations': [{'target_id': 100, 'relation': 'Friend'}],
        },
        100: {'kind': 'character', 'local_id': 100, 'name': 'Dave', 'entry': '', 'relations': []},
    }
    _resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)

    assert len(conflicts) == 2
    assert conflicts[0]['proposal_idx'] == 0
    assert conflicts[1]['proposal_idx'] == 1


def test_empty_proposals_returns_empty():
    resolved, conflicts = resolve_creates_to_updates([], {})
    assert resolved == []
    assert conflicts == []


def test_cross_proposal_different_pairs_no_conflict():
    """Different owner→target pairs are fine."""
    p1 = _make_proposal(entity_name='Alice', relation_changes=[_make_rel('create', 'Bob')])
    p2 = _make_proposal(entity_name='Carol', relation_changes=[_make_rel('create', 'Dave')])
    proposals = [p1, p2]
    entity_index = {
        1: {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'entry': '', 'relations': []},
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
        2: {'kind': 'character', 'local_id': 2, 'name': 'Carol', 'entry': '', 'relations': []},
        100: {'kind': 'character', 'local_id': 100, 'name': 'Dave', 'entry': '', 'relations': []},
    }
    resolved, _ = resolve_creates_to_updates(proposals, entity_index)

    conflicts = detect_cross_proposal_conflicts(resolved)
    assert len(conflicts) == 0


def test_same_owner_different_targets_no_conflict():
    """Same owner creating relations to different targets is fine."""
    p1 = _make_proposal(
        entity_name='Alice',
        relation_changes=[
            _make_rel('create', 'Bob'),
            _make_rel('create', 'Carol'),
        ],
    )
    proposals = [p1]
    entity_index = {
        1: {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'entry': '', 'relations': []},
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
        2: {'kind': 'character', 'local_id': 2, 'name': 'Carol', 'entry': '', 'relations': []},
    }
    resolved, _ = resolve_creates_to_updates(proposals, entity_index)

    conflicts = detect_cross_proposal_conflicts(resolved)
    assert len(conflicts) == 0


def test_same_pair_in_different_proposals_is_conflict():
    """Two proposals for the same owner→target pair → cross_proposal conflict."""
    p1 = _make_proposal(entity_name='Alice', relation_changes=[_make_rel('create', 'Bob')])
    p2 = _make_proposal(entity_name='Alice', relation_changes=[_make_rel('create', 'Bob')])
    proposals = [p1, p2]
    entity_index = {
        1: {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'entry': '', 'relations': []},
        99: {'kind': 'character', 'local_id': 99, 'name': 'Bob', 'entry': '', 'relations': []},
    }
    resolved, _ = resolve_creates_to_updates(proposals, entity_index)

    conflicts = detect_cross_proposal_conflicts(resolved)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c['conflict_kind'] == 'cross_proposal'
    assert c['entity_name'] == 'Alice'
    assert c['target_name'] == 'Bob'


def test_empty_proposals_returns_no_conflicts():
    """Empty queue → no cross-proposal conflicts."""
    conflicts = detect_cross_proposal_conflicts([])
    assert conflicts == []
