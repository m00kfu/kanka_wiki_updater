"""Mock-LLM regression tests for synopsis proposal relation parsing + validation.

These tests verify that build_synopsis_proposal correctly:
1. Parses relation_changes from LLM output
2. Validates labels against a RelationTypeTracker
3. Defaults to [] when no relation_changes are present
4. Enriches proposals with _type_status and similar_types
"""

import pytest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanka_wiki_updater.sync.relation_types import RelationTypeTracker
from kanka_wiki_updater.sync.synopsis_generator import build_synopsis_proposal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity(name='Alice', kind='character', entry='', local_id=1, entity_id=1, relations=None):
    return {
        'name': name,
        'kind': kind,
        'entry': entry,
        'local_id': local_id,
        'entity_id': entity_id,
        'relations': relations or [],
    }


def _make_journal(id_=1, name='Session 1', date='', entry='Alice met Bob today.'):
    return {
        'id': id_,
        'name': name,
        'date': date,
        'entry': entry,
    }


def _mock_llm_response(relation_changes=None):
    """Return a dict that mimics the LLM's JSON response."""
    base = {
        'updated_entry': 'Alice met Bob today. She also befriended Carol.',
        'change_summary': 'Added meeting with Bob and friendship with Carol.',
        '_is_new_info': True,
        'new_paragraph_indices': [1],
        'uncertain': [],
    }
    if relation_changes is not None:
        base['relation_changes'] = relation_changes
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_valid_relation_changes():
    """LLM returns attitude_delta → validated correctly, attitude is computed."""
    tracker = RelationTypeTracker()

    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {
                'action': 'create', 'target_name': 'Bob', 'relation': 'Ally',
                'attitude_delta': 15,
            },
            {
                'action': 'update', 'target_name': 'Carol', 'relation': 'Rival',
                'attitude_delta': -10,
            },
        ])

        entity = _make_entity(
            relations=[{'target_id': 999, 'relation': 'Acquaintance', 'attitude': 20}]
        )
        journal = _make_journal()
        index = {999: {'name': 'Carol'}}

        result = build_synopsis_proposal(1, entity, journal, index, relation_tracker=tracker)

    assert result is not None
    rels = result['relation_changes']
    # 4 entries: 2 originals + 2 reciprocals (auto-generated)
    assert len(rels) == 4

    # Originals first, then reciprocals
    bob_rel = next(r for r in rels if r['target_name'] == 'Bob')
    assert bob_rel['action'] == 'create'
    assert bob_rel['attitude'] == 15
    assert isinstance(bob_rel['attitude'], int)
    assert 'owner_name' not in bob_rel  # originals have no owner_name (use proposal entity)
    assert 'attitude_delta' not in bob_rel  # transient field, popped during processing

    carol_rel = next(r for r in rels if r['target_name'] == 'Carol')
    assert carol_rel['action'] == 'update'
    assert carol_rel['attitude'] == 10

    # Reciprocals have owner_name pointing to the original target
    alice_from_bob = next(r for r in rels if r.get('owner_name') == 'Bob' and r['target_name'] == 'Alice')
    assert alice_from_bob['action'] == 'create'
    assert alice_from_bob['attitude'] == 15

    alice_from_carol = next(r for r in rels if r.get('owner_name') == 'Carol' and r['target_name'] == 'Alice')
    assert alice_from_carol['action'] == 'update'
    assert alice_from_carol['attitude'] == 10


def test_parse_no_relation_changes():
    """Missing relation_changes key → defaults to []."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'Alice met Bob today.',
            'change_summary': 'Added meeting with Bob.',
            '_is_new_info': True,
            'uncertain': [],
        }

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index)

    assert result is not None
    assert result['relation_changes'] == []


def test_parse_empty_relation_changes():
    """Empty relation_changes array → stays empty."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'Alice met Bob today.',
            'change_summary': 'Added meeting with Bob.',
            '_is_new_info': True,
            'relation_changes': [],
            'uncertain': [],
        }

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index)

    assert result is not None
    assert result['relation_changes'] == []


def test_validate_known_type_not_flagged():
    """Known label gets _type_status: 'known'."""
    tracker = RelationTypeTracker()
    tracker.add_type('Ally')
    tracker.add_type('Enemy')

    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {'action': 'create', 'target_name': 'Bob', 'relation': 'Ally'},
            {'action': 'update', 'target_name': 'Carol', 'relation': 'enemy'},  # lowercase
        ])

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index, relation_tracker=tracker)

    assert result is not None
    for rc in result['relation_changes']:
        assert rc['_type_status'] == 'known', f"Expected 'known' but got {rc['_type_status']} for '{rc.get('relation')}'"
        assert rc['similar_types'] == []


def test_validate_new_type_flagged_with_similar():
    """Unknown label gets _type_status: 'new_suggested' + similar_types."""
    tracker = RelationTypeTracker()
    tracker.add_type('Ally')

    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {'action': 'create', 'target_name': 'Bob', 'relation': 'Allie'},  # typo of Ally
        ])

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index, relation_tracker=tracker)

    assert result is not None
    rc = result['relation_changes'][0]
    assert rc['_type_status'] == 'new_suggested'
    assert 'Ally' in rc['similar_types'], f"Expected 'Ally' in similar_types but got {rc['similar_types']}"


def test_validate_unknown_when_no_tracker():
    """No tracker → _type_status is 'unknown', no suggestions."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {'action': 'create', 'target_name': 'Bob', 'relation': 'Whatever'},
        ])

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index)

    assert result is not None
    rc = result['relation_changes'][0]
    assert rc['_type_status'] == 'unknown'
    assert rc['similar_types'] == []


def test_prompt_includes_known_types_list():
    """When tracker is provided, known types appear in the user prompt."""
    tracker = RelationTypeTracker()
    tracker.add_type('Ally')
    tracker.add_type('Enemy')

    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'Alice met Bob.',
            'change_summary': 'Added meeting.',
            '_is_new_info': True,
            'uncertain': [],
        }

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        build_synopsis_proposal(1, entity, journal, index, relation_tracker=tracker)

    # Verify the LLM was called with a prompt containing known types
    call_args = mock_chat.call_args
    user_prompt = call_args[0][1]  # second positional arg is user_prompt
    assert 'Ally' in user_prompt
    assert 'Enemy' in user_prompt


def test_prompt_without_tracker_has_placeholder():
    """No tracker → prompt has '(not yet tracked)' placeholder."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'Alice met Bob.',
            'change_summary': 'Added meeting.',
            '_is_new_info': True,
            'uncertain': [],
        }

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        build_synopsis_proposal(1, entity, journal, index)  # no tracker

    call_args = mock_chat.call_args
    user_prompt = call_args[0][1]
    assert 'not yet tracked' in user_prompt


def test_relation_changes_preserves_extra_fields():
    """attitude_delta and reason fields from LLM are handled correctly."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {
                'action': 'create',
                'target_name': 'Bob',
                'relation': 'Ally',
                'attitude_delta': 15,
                'reason': 'They helped Alice in battle.',
            },
        ])

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index)

    rc = result['relation_changes'][0]
    assert rc['action'] == 'create'
    assert rc['target_name'] == 'Bob'
    assert isinstance(rc['attitude'], int) and -100 <= rc['attitude'] <= 100
    assert rc['reason'] == 'They helped Alice in battle.'
    assert 'attitude_delta' not in rc  # transient field, removed during processing


def test_empty_relation_label_treated_as_known():
    """A relation change with empty/missing label → _type_status='known'."""
    tracker = RelationTypeTracker()

    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {'action': 'create', 'target_name': 'Bob'},  # no relation field
        ])

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index, relation_tracker=tracker)

    rc = result['relation_changes'][0]
    assert rc['_type_status'] == 'known'


def test_no_text_change_returns_none():
    """When LLM output matches existing entry exactly → None."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'Alice met Bob today.',  # same as journal entry (stripped)
            'change_summary': '',
            '_is_new_info': False,
            'uncertain': [],
        }

        entity = _make_entity(entry='Alice met Bob today.')
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index)

    assert result is None


def test_llm_error_returns_error_dict():
    """LLM error → proposal with _llm_error field."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        from kanka_wiki_updater.llm.client import LLMError
        mock_chat.side_effect = LLMError('Connection refused')

        entity = _make_entity()
        journal = _make_journal()
        index = {}

        result = build_synopsis_proposal(1, entity, journal, index)

    assert result is not None
    assert result['_llm_error'] == 'Connection refused'


def test_zero_delta_is_intentional_not_ignored():
    """Delta of 0 means the LLM considered it and decided no change — attitude should be set."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {
                'action': 'update',
                'target_name': 'Carol',
                'relation': 'Rival',
                'attitude_delta': 0,  # intentional no-change
            },
        ])

        entity = _make_entity(
            relations=[{'target_id': 999, 'relation': 'Rival', 'attitude': -30}]
        )
        journal = _make_journal()
        index = {999: {'name': 'Carol'}}

        result = build_synopsis_proposal(1, entity, journal, index)

    rc = result['relation_changes'][0]
    assert rc['attitude'] == -30  # delta 0 applied: -30 + 0 = -30


def test_unresolved_target_name_still_applies_delta():
    """When target_name doesn't resolve (typo/hallucination), delta is still applied from baseline 0."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {
                'action': 'create',
                'target_name': 'Bobb',  # typo — doesn't match any entity in index
                'relation': 'Ally',
                'attitude_delta': 15,
            },
        ])

        entity = _make_entity()
        journal = _make_journal()
        index = {}  # no "Bobb" or "Bob" in index

        result = build_synopsis_proposal(1, entity, journal, index)

    rc = result['relation_changes'][0]
    assert rc['attitude'] == 15  # applied from baseline 0; reviewer can fix typo in UI


def test_prompt_includes_dynamic_attitude_guidance():
    """RELATION_SYSTEM_PROMPT uses attitude_guidance_text() output."""
    from kanka_wiki_updater.core.prompts import RELATION_SYSTEM_PROMPT
    from kanka_wiki_updater.sync.default_attitudes import DEFAULT_ATTITUDES, attitude_guidance_text

    guidance = attitude_guidance_text()
    # The prompt should contain the dynamic guidance text
    assert guidance in RELATION_SYSTEM_PROMPT
    # And at least one baseline entry (keys converted: underscore → space)
    for key in DEFAULT_ATTITUDES:
        label = key.replace('_', ' ')
        if label not in RELATION_SYSTEM_PROMPT:
            pytest.fail(f"Expected attitude baseline '{label}' in RELATION_SYSTEM_PROMPT")


def test_attitude_clamped_at_upper_bound():
    """When current_score + delta > 100, clamp to 100."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {
                'action': 'update',
                'target_name': 'Carol',
                'relation': 'Ally',
                'attitude_delta': 25,
            },
        ])

        entity = _make_entity(
            relations=[{'target_id': 999, 'relation': 'Ally', 'attitude': 90}]
        )
        journal = _make_journal()
        index = {999: {'name': 'Carol'}}

        result = build_synopsis_proposal(1, entity, journal, index)

    rc = result['relation_changes'][0]
    assert rc['attitude'] == 100  # clamped from 115


def test_attitude_clamped_at_lower_bound():
    """When current_score + delta < -100, clamp to -100."""
    with mock.patch('kanka_wiki_updater.sync.synopsis_generator.chat_json') as mock_chat:
        mock_chat.return_value = _mock_llm_response([
            {
                'action': 'update',
                'target_name': 'Carol',
                'relation': 'Enemy',
                'attitude_delta': -30,
            },
        ])

        entity = _make_entity(
            relations=[{'target_id': 999, 'relation': 'Enemy', 'attitude': -85}]
        )
        journal = _make_journal()
        index = {999: {'name': 'Carol'}}

        result = build_synopsis_proposal(1, entity, journal, index)

    rc = result['relation_changes'][0]
    assert rc['attitude'] == -100  # clamped from -115
