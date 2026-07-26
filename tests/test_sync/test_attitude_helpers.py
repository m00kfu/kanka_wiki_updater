"""Unit tests for attitude delta helpers in synopsis_generator."""

from kanka_wiki_updater.sync.synopsis_generator import (
    _get_current_attitude,
    _rel_target,
    _resolve_target_name,
    compute_new_attitude,
)


# ── _rel_target tests ────────────────────────────────────────────────


def test_rel_target_from_dict():
    assert _rel_target({'target_id': 456}) == 456
    assert _rel_target({}) is None


def test_rel_target_from_object():
    class Rel:
        target_id = 789

    assert _rel_target(Rel()) == 789


# ── _resolve_target_name tests ───────────────────────────────────────


def test_exact_match_case_insensitive():
    index = {1: {'name': 'Alice'}, 2: {'name': 'Bob'}}
    assert _resolve_target_name('Alice', index) == 1
    assert _resolve_target_name('bob', index) == 2


def test_no_match():
    index = {1: {'name': 'Alice'}, 2: {'name': 'Bob'}}
    assert _resolve_target_name('Charlie', index) is None


def test_empty_and_whitespace_names():
    index = {1: {'name': 'Alice'}}
    assert _resolve_target_name('', index) is None
    assert _resolve_target_name('   ', index) is None


def test_whitespace_in_entity_name():
    index = {1: {'name': '  Alice  '}}
    assert _resolve_target_name('Alice', index) == 1


# ── _get_current_attitude tests ──────────────────────────────────────


def test_found_existing_relation():
    relations = [
        {'target_id': 456, 'relation': 'Ally', 'attitude': 30},
        {'target_id': 789, 'relation': 'Enemy', 'attitude': -50},
    ]
    assert _get_current_attitude(relations, 456) == 30
    assert _get_current_attitude(relations, 789) == -50


def test_missing_relation():
    relations = [{'target_id': 456, 'relation': 'Ally', 'attitude': 30}]
    assert _get_current_attitude(relations, 999) is None


def test_empty_relations_list():
    assert _get_current_attitude([], 123) is None


def test_none_attitude_value():
    relations = [{'target_id': 456, 'relation': 'Acquaintance', 'attitude': None}]
    assert _get_current_attitude(relations, 456) is None


# ── compute_new_attitude tests ───────────────────────────────────────


def test_positive_delta_on_existing_score():
    assert compute_new_attitude(30, 15) == 45


def test_negative_delta_on_existing_score():
    assert compute_new_attitude(30, -15) == 15


def test_zero_delta_preserves_current_score():
    """Delta of 0 is intentional — should return current_score unchanged."""
    assert compute_new_attitude(42, 0) == 42


def test_clamp_to_upper_bound():
    assert compute_new_attitude(90, 25) == 100   # clamped from 115
    assert compute_new_attitude(85, 15) == 100   # exactly at boundary


def test_clamp_to_lower_bound():
    assert compute_new_attitude(-80, -30) == -100  # clamped from -110
    assert compute_new_attitude(-75, -25) == -100  # exactly at boundary


def test_none_current_score_uses_zero_baseline():
    """New relation: delta applied to neutral baseline of 0."""
    assert compute_new_attitude(None, 25) == 25
    assert compute_new_attitude(None, -30) == -30


def test_no_clamp_needed():
    """When sum is within range, return exact result."""
    assert compute_new_attitude(80, -30) == 50
    assert compute_new_attitude(-90, 25) == -65
