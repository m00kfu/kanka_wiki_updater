"""Tests for review module pure functions."""

import pytest

from kanka_wiki_updater.review import (
    dropped_mention_warning,
    has_meaningful_change,
    unlinked_mention_warning,
)


# --- has_meaningful_change tests ---


def test_has_meaningful_change_synopsis_differs():
    proposal = {
        'previous_entry': 'Alice is a warrior.',
        'proposed_entry': 'Alice is a mage.',
    }
    assert has_meaningful_change(proposal) is True


def test_has_meaningful_change_same_text():
    proposal = {
        'previous_entry': 'Alice is a warrior.',
        'proposed_entry': 'Alice is a warrior.',
    }
    assert has_meaningful_change(proposal) is False


def test_has_meaningful_change_same_text_different_format():
    proposal = {
        'previous_entry': '[character:123|Alice]',
        'proposed_entry': '[character:123|Alice]',
    }
    assert has_meaningful_change(proposal) is False


def test_has_meaningful_change_empty_relation_changes():
    proposal = {
        'previous_entry': 'Same text',
        'proposed_entry': 'Same text',
        'relation_changes': [],
    }
    assert has_meaningful_change(proposal) is False


def test_has_meaningful_change_with_relation_changes():
    proposal = {
        'previous_entry': 'Same text',
        'proposed_entry': 'Same text',
        'relation_changes': [{'action': 'create', 'target_name': 'Bob'}],
    }
    assert has_meaningful_change(proposal) is True


def test_has_meaningful_change_empty_proposal_raises():
    proposal = {'other': 'data'}
    with pytest.raises(KeyError):
        has_meaningful_change(proposal)


# --- dropped_mention_warning tests ---


def test_dropped_mention_warning_no_drop():
    proposal = {
        'previous_entry': '[character:123|Alice] went to [location:456|Castle]',
        'proposed_entry': '[character:123|Alice] went to [location:456|Castle].',
    }
    index = {}
    result = dropped_mention_warning(proposal, index)
    assert result is None


def test_dropped_mention_warning_detects_drop():
    proposal = {
        'previous_entry': '[character:123|Alice] went to [location:456|Castle]',
        'proposed_entry': 'Alice went to the castle',
    }
    index = {}
    result = dropped_mention_warning(proposal, index)
    assert result is not None
    assert 'mention link' in result.lower()


def test_dropped_mention_warning_with_entity_names():
    proposal = {
        'previous_entry': '[character:123|Alice] went to [location:456|Castle]',
        'proposed_entry': 'Alice went to the castle',
    }
    index = {
        123: {'name': 'Alice'},
        456: {'name': 'Castle'},
    }
    result = dropped_mention_warning(proposal, index)
    assert 'Alice' in result or 'Castle' in result


def test_dropped_mention_warning_new_link_added():
    proposal = {
        'previous_entry': 'Alice went to the castle',
        'proposed_entry': '[character:123|Alice] went to [location:456|Castle]',
    }
    index = {}
    result = dropped_mention_warning(proposal, index)
    assert result is None


def test_dropped_mention_warning_empty_proposal_raises():
    proposal = {'other': 'data'}
    with pytest.raises(KeyError):
        dropped_mention_warning(proposal, {})


# --- unlinked_mention_warning tests ---


def test_unlinked_mention_warning_no_issue():
    text = '[character:123|Alice] went to the castle'
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    result = unlinked_mention_warning(text, index)
    assert result is None


def test_unlinked_mention_warning_detects_unlinked():
    text = 'Alice went to the castle'
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    result = unlinked_mention_warning(text, index)
    assert result is not None


def test_unlinked_mention_warning_skips_excluded():
    text = 'Alice went to the castle'
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    result = unlinked_mention_warning(text, index, exclude_entity_id=123)
    assert result is None


def test_unlinked_mention_warning_empty_text():
    result = unlinked_mention_warning('', {123: {'name': 'Alice', 'kind': 'character'}})
    assert result is None


def test_unlinked_mention_warning_none_text():
    result = unlinked_mention_warning(None, {})
    assert result is None


def test_unlinked_mention_warning_short_names_skipped():
    text = 'A went to the castle'
    index = {123: {'name': 'A', 'kind': 'character'}}
    result = unlinked_mention_warning(text, index)
    assert result is None
