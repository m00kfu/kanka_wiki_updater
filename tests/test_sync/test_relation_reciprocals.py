"""Tests for reciprocal relation generation (synopsis_generator._generate_reciprocals).

These tests verify that the post-processing step correctly generates reciprocal
entries with inverse labels and owner_name references.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanka_wiki_updater.sync.synopsis_generator import _generate_reciprocals


# ---------------------------------------------------------------------------
# Symmetric relations (same label both ways)
# ---------------------------------------------------------------------------


def test_symmetric_relation_generates_same_label():
    """Ally -> Ally reciprocal (same label both ways)."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Ally'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 2
    # Original preserved
    assert result[0]['action'] == 'create'
    assert result[0]['target_name'] == 'Bob'
    assert result[0]['relation'] == 'Ally'
    assert 'owner_name' not in result[0]
    # Reciprocal has same label and owner_name pointing to original target
    assert result[1]['action'] == 'create'
    assert result[1]['target_name'] == 'Alice'
    assert result[1]['relation'] == 'Ally'
    assert result[1]['owner_name'] == 'Bob'


def test_symmetric_update_preserved():
    """Update actions are preserved on reciprocals."""
    rcs = [{'action': 'update', 'target_name': 'Carol', 'relation': 'Enemy'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 2
    assert result[1]['action'] == 'update'
    assert result[1]['owner_name'] == 'Carol'


# ---------------------------------------------------------------------------
# Asymmetric relations (inverse labels)
# ---------------------------------------------------------------------------


def test_parent_child_inverse():
    """Parent -> Child reciprocal (different labels)."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Parent'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 2
    assert result[0]['relation'] == 'Parent'
    assert result[1]['relation'].lower() == 'child'
    assert result[1]['target_name'] == 'Alice'
    assert result[1]['owner_name'] == 'Bob'


def test_child_parent_inverse():
    """Child -> Parent reciprocal."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'child'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 2
    assert result[1]['relation'] == 'parent'


def test_teacher_student_inverse():
    """Teacher -> Student reciprocal."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'teacher'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 2
    assert result[1]['relation'] == 'student'


def test_guardian_ward_inverse():
    """Guardian -> Ward reciprocal."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'guardian'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 2
    assert result[1]['relation'] == 'ward'


# ---------------------------------------------------------------------------
# Unknown relations (fallback to symmetric)
# ---------------------------------------------------------------------------


def test_unknown_relation_defaults_to_symmetric():
    """Uncommon relation type falls back to same label (safe default)."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Blood Oath'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 2
    # Unknown types assume symmetric (same label both ways) — safe default.
    assert result[1]['relation'] == 'Blood Oath'


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_delete_not_doubled():
    """Delete actions are passed through without generating a reciprocal."""
    rcs = [{'action': 'delete', 'target_name': 'Bob', 'relation': 'Enemy'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 1
    assert result[0]['action'] == 'delete'


def test_attitude_inherited_by_reciprocal():
    """Reciprocal inherits the computed absolute attitude from original."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Ally',
            'attitude': 45, 'attitude_delta': 10}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert result[1]['attitude'] == 45


def test_multiple_relations_each_get_reciprocal():
    """Multiple relation changes each produce their own reciprocal."""
    rcs = [
        {'action': 'create', 'target_name': 'Bob', 'relation': 'Ally'},
        {'action': 'update', 'target_name': 'Carol', 'relation': 'Enemy', 'attitude': -60},
    ]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 4
    # Order: [original0, reciprocal0, original1, reciprocal1]
    assert result[0]['target_name'] == 'Bob'       # original rc0
    assert 'owner_name' not in result[0]            # no owner on originals
    assert result[1]['target_name'] == 'Alice'      # reciprocal of Bob
    assert result[1]['owner_name'] == 'Bob'
    assert result[2]['target_name'] == 'Carol'      # original rc1
    assert result[3]['target_name'] == 'Alice'      # reciprocal of Carol
    assert result[3]['owner_name'] == 'Carol'


def test_empty_input():
    rcs = []
    result = _generate_reciprocals(rcs, owner_name='Alice')
    assert result == []


def test_case_insensitive_inverse_lookup():
    """'parent', 'Parent', 'PARENT' all map to 'child'."""
    for label in ('parent', 'Parent', 'PARENT'):
        rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': label}]
        result = _generate_reciprocals(rcs, owner_name='Alice')
        assert result[1]['relation'].lower() == 'child'


def test_empty_relation_label_no_reciprocal():
    """Entries with empty relation labels are passed through without reciprocal."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': ''}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 1


def test_empty_target_no_reciprocal():
    """Entries with empty target names are passed through without reciprocal."""
    rcs = [{'action': 'create', 'target_name': '', 'relation': 'Ally'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(result) == 1


def test_reason_preserved_on_reciprocal():
    """Reciprocal inherits reason prefixed with 'Reciprocal of:'."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Ally',
            'reason': 'Fought together at the battle.'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert result[1]['reason'] == 'Reciprocal of: Fought together at the battle.'


def test_reason_empty_on_reciprocal():
    """When original has no reason, reciprocal gets empty prefix."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Ally'}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert result[1]['reason'] == 'Reciprocal of: '


def test_attitude_delta_preserved_on_reciprocal():
    """attitude_delta is passed through to reciprocal (transient, removed downstream)."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Ally',
            'attitude_delta': 15}]
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert result[1]['attitude_delta'] == 15


def test_original_not_modified():
    """_generate_reciprocals does not mutate the input list."""
    rcs = [{'action': 'create', 'target_name': 'Bob', 'relation': 'Ally'}]
    original_len = len(rcs)
    result = _generate_reciprocals(rcs, owner_name='Alice')

    assert len(rcs) == original_len  # input not mutated
    assert result is not rcs  # returns new list
