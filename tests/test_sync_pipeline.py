"""Tests for sync_pipeline pure functions."""

from kanka_wiki_updater.sync_pipeline import (
    _is_known_entity,
    apply_relation_changes_locally,
    build_entity_index,
    find_mentioned_entities,
    journal_sort_key,
    relation_summary,
)

# --- build_entity_index tests (Task 9) ---


def test_build_entity_index_characters():
    client_data = [
        {
            'entity_id': 123,
            'id': 456,
            'name': 'Alice',
            'entry': 'A brave warrior.',
            'relations': [],
        }
    ]

    class MockClient:
        def get_characters(self):
            return client_data

        def get_locations(self):
            return []

        def get_organizations(self):
            return []

        def get_creatures(self):
            return []

    index = build_entity_index(MockClient())
    assert 123 in index
    assert index[123]['kind'] == 'character'
    assert index[123]['local_id'] == 456
    assert index[123]['name'] == 'Alice'


def test_build_entity_index_locations():
    client_data = [
        {
            'entity_id': 789,
            'id': 101,
            'name': 'Waterdeep',
            'entry': 'A coastal city.',
            'relations': [],
        }
    ]

    class MockClient:
        def get_characters(self):
            return []

        def get_locations(self):
            return client_data

        def get_organizations(self):
            return []

        def get_creatures(self):
            return []

    index = build_entity_index(MockClient())
    assert 789 in index
    assert index[789]['kind'] == 'location'


def test_build_entity_index_empty():
    class MockClient:
        def get_characters(self):
            return []

        def get_locations(self):
            return []

        def get_organizations(self):
            return []

        def get_creatures(self):
            return []

    index = build_entity_index(MockClient())
    assert len(index) == 0


def test_build_entity_index_missing_entry():
    class MockClient:
        def get_characters(self):
            return [{'entity_id': 1, 'id': 2, 'name': 'Bob', 'relations': []}]

        def get_locations(self):
            return []

        def get_organizations(self):
            return []

        def get_creatures(self):
            return []

    index = build_entity_index(MockClient())
    assert index[1]['entry'] == ''


def test_build_entity_index_missing_relations():
    class MockClient:
        def get_characters(self):
            return [{'entity_id': 1, 'id': 2, 'name': 'Bob', 'entry': 'Test'}]

        def get_locations(self):
            return []

        def get_organizations(self):
            return []

        def get_creatures(self):
            return []

    index = build_entity_index(MockClient())
    assert index[1]['relations'] == []


# --- relation_summary tests (Task 9) ---


def test_relation_summary_empty():
    result = relation_summary([], {})
    assert result == '(none on record)'


def test_relation_summary_with_relations():
    entity_data = {'name': 'Bob'}
    index = {456: entity_data}
    relations = [
        {'target_id': 456, 'relation': 'Sworn enemy', 'attitude': -80},
    ]
    result = relation_summary(relations, index)
    assert 'Sworn enemy' in result
    assert 'Bob' in result
    assert '-80' in result


def test_relation_summary_missing_target():
    index = {}
    relations = [
        {'target_id': 999, 'relation': 'Friend', 'attitude': 50},
    ]
    result = relation_summary(relations, index)
    assert 'entity #None' in result


def test_relation_summary_multiple():
    entity1 = {'name': 'Alice'}
    entity2 = {'name': 'Bob'}
    index = {1: entity1, 2: entity2}
    relations = [
        {'target_id': 1, 'relation': 'Friend', 'attitude': 80},
        {'target_id': 2, 'relation': 'Enemy', 'attitude': -60},
    ]
    result = relation_summary(relations, index)
    lines = result.split('\n')
    assert len(lines) == 2


def test_relation_summary_none_attitude():
    entity_data = {'name': 'Alice'}
    index = {1: entity_data}
    relations = [{'target_id': 1, 'relation': 'Acquaintance', 'attitude': None}]
    result = relation_summary(relations, index)
    assert 'None' in result


# --- find_mentioned_entities tests (Task 9) ---


def test_find_mentioned_entities_linked_only():
    text = '[character:123|Alice] went to [location:456|Castle]'
    entity1 = {'kind': 'character', 'name': 'Alice'}
    entity2 = {'kind': 'location', 'name': 'Castle'}
    index = {123: entity1, 456: entity2}
    result = find_mentioned_entities(text, index)
    assert 123 in result
    assert 456 in result


def test_find_mentioned_entities_no_links():
    text = 'Someone went somewhere'
    entity_data = {'kind': 'character', 'name': 'Alice'}
    index = {123: entity_data}
    result = find_mentioned_entities(text, index)
    assert len(result) == 0


def test_find_mentioned_entities_fuzzy_match():
    text = 'Alice went to the castle'
    entity_data = {'kind': 'character', 'name': 'Alice'}
    index = {123: entity_data}
    result = find_mentioned_entities(text, index)
    assert 123 in result


def test_find_mentioned_entities_filters_unknown():
    text = '[entity:999|Unknown]'
    entity_data = {'kind': 'character', 'name': 'Alice'}
    index = {123: entity_data}
    result = find_mentioned_entities(text, index)
    assert 999 not in result


def test_find_mentioned_entities_empty_text():
    entity_data = {'kind': 'character', 'name': 'Alice'}
    result = find_mentioned_entities('', {123: entity_data})
    assert len(result) == 0


# --- journal_sort_key tests (Task 10) ---


def test_journal_sort_key_gregorian_date():
    j = {'date': '2024-06-15', 'created_at': '2024-06-15T10:00:00'}
    result = journal_sort_key(j)
    assert result[0] == 0
    assert result[1] == 2024


def test_journal_sort_key_custom_calendar():
    j = {'calendar_year': 5, 'calendar_month': 3, 'calendar_day': 12}
    result = journal_sort_key(j)
    assert result[0] == 0
    assert result[1] == 5


def test_journal_sort_key_fallback_to_created_at():
    j = {'created_at': '2024-06-15T10:00:00'}
    result = journal_sort_key(j)
    assert result[0] == 1


def test_journal_sort_key_date_over_created():
    j_dated = {'date': '2024-06-15', 'created_at': '2024-07-01T10:00:00'}
    j_undated = {'created_at': '2024-06-10T10:00:00'}

    key_dated = journal_sort_key(j_dated)
    key_undated = journal_sort_key(j_undated)

    assert key_dated < key_undated


def test_journal_sort_key_empty_date():
    j = {'date': '', 'created_at': '2024-06-15T10:00:00'}
    result = journal_sort_key(j)
    assert result[0] == 1


def test_journal_sort_key_no_date_or_created():
    j = {}
    result = journal_sort_key(j)
    assert result[0] == 1
    assert result[4] == ''


# --- apply_relation_changes_locally tests (Task 10) ---


def test_apply_relation_changes_locally_create():
    index = {
        123: {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'relations': []},
        456: {'name': 'Bob'},
    }
    name_to_id = {'Bob': 456}

    apply_relation_changes_locally(
        123,
        [{'action': 'create', 'target_name': 'Bob', 'relation': 'Friend'}],
        index,
        name_to_id,
    )

    assert len(index[123]['relations']) == 1
    assert index[123]['relations'][0]['target_id'] == 456


def test_apply_relation_changes_locally_update():
    index = {
        123: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'relations': [{'target_id': 456, 'relation': 'Acquaintance', 'attitude': None}],
        },
        456: {'name': 'Bob'},
    }
    name_to_id = {'Bob': 456}

    apply_relation_changes_locally(
        123,
        [{'action': 'update', 'target_name': 'Bob', 'relation': 'Friend', 'attitude': 80}],
        index,
        name_to_id,
    )

    rel = index[123]['relations'][0]
    assert rel['relation'] == 'Friend'
    assert rel['attitude'] == 80


def test_apply_relation_changes_locally_delete():
    index = {
        123: {
            'kind': 'character',
            'local_id': 1,
            'name': 'Alice',
            'relations': [{'target_id': 456, 'relation': 'Friend'}],
        },
        456: {'name': 'Bob'},
    }
    name_to_id = {'Bob': 456}

    apply_relation_changes_locally(
        123,
        [{'action': 'delete', 'target_name': 'Bob'}],
        index,
        name_to_id,
    )

    assert len(index[123]['relations']) == 0


def test_apply_relation_changes_locally_unknown_target():
    index = {123: {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'relations': []}}
    name_to_id = {}

    apply_relation_changes_locally(
        123,
        [{'action': 'create', 'target_name': 'Unknown', 'relation': 'Friend'}],
        index,
        name_to_id,
    )

    assert len(index[123]['relations']) == 0


def test_apply_relation_changes_locally_empty_changes():
    index = {123: {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'relations': []}}
    apply_relation_changes_locally(123, [], index, {})
    assert len(index[123]['relations']) == 0


# --- _is_known_entity tests (Task 15: filter false-positive new entities) ---


def test_is_known_entity_exact_match():
    assert _is_known_entity('Alice', ['alice', 'Bob', 'Castle']) is True
    assert _is_known_entity('ALICE', ['alice', 'Bob', 'Castle']) is True


def test_is_known_entity_substring_in_known_name():
    # Suggestion "Aerendyl" matches known entity "Aerendyl Stonehand"
    assert _is_known_entity('Aerendyl', ['Aerendyl Stonehand', 'Bob']) is True


def test_is_known_entity_known_name_is_substring_of_suggestion():
    # Suggestion "Aerendyl Stonehand the Great" contains known name "Aerendyl Stonehand"
    assert _is_known_entity('Aerendyl Stonehand the Great', ['Aerendyl Stonehand']) is True


def test_is_known_entity_fuzzy_first_word_match():
    # First-word fuzzy match: "Aerendel" vs "Aerendyl" are very close
    assert _is_known_entity('Aerendel', ['Aerendyl', 'Bob the Bard']) is True


def test_is_known_entity_no_match():
    assert _is_known_entity('Zephyr Stormwind', ['Alice', 'Waterdeep', 'Bob']) is False


def test_is_known_entity_empty_name():
    assert _is_known_entity('', ['Alice']) is False


def test_is_known_entity_short_suggestion_skipped_for_substring_and_fuzzy():
    # Short suggestions skip fuzzy matching to avoid false positives, but
    # exact match still works. Substring checks are also skipped for very
    # short names (< 4 chars) since they'd be substrings of many names.
    assert _is_known_entity('A', ['Alice', 'Bob']) is False  # single char -- too short even for substring
    assert _is_known_entity('Of', ['Officer', 'Waterdeep']) is False  # too short for fuzzy


def test_is_known_entity_case_insensitive():
    assert _is_known_entity('WATERDEEP', ['waterdeep']) is True
    assert _is_known_entity('waterdeep', ['Waterdeep']) is True


def test_is_known_entity_full_name_fuzzy_match():
    # Full-name fuzzy matching catches near-misses where neither name is a
    # substring of the other but they share significant overlap.
    # "Aerendel Stoneclaw" vs "Lord Aerendyl" differ in second word, ratio ~0.45
    assert _is_known_entity('Aerendel Stoneclaw', ['Lord Aerendyl']) is False
    # But "Aerendel Stormwind" vs "Aerendyl Stormwind" share most chars (ratio ~0.89)
    assert _is_known_entity('Aerendel Stormwind', ['Aerendyl Stormwind']) is True


def test_is_known_entity_unicode_accent_normalization():
    # Accented names should match their unaccented equivalents.
    assert _is_known_entity('Jose', ['Jos\u00e9']) is True
    assert _is_known_entity('Jos\u00e9', ['Jose']) is True


def test_is_known_entity_trailing_space_in_known_name():
    # Trailing/leading whitespace in known names should not prevent matching.
    assert _is_known_entity('Aerendyl', [' Aerendyl ']) is True
    assert _is_known_entity('  Aerendyl  ', ['Aerendyl']) is True


def test_is_known_entity_partial_name_is_substring():
    # A suggestion that is a substring of an existing entity name should match.
    assert _is_known_entity('Stonehand', ['Aerendyl Stonehand']) is True
    assert _is_known_entity('Aerendel', ['Lord Aerendyl']) is False  # not a substring, fuzzy < threshold


# --- propose_update truncation flag tests (Task 16: truncated output detection) ---


def test_propose_update_truncated_flag():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session note',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'entry': 'Old synopsis.', 'relations': []}

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis.',
            'change_summary': '',
            'relation_changes': [],
            'truncated': True,  # LLM hit token limit
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result['truncated'] is True


def test_propose_update_not_truncated():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session note',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'entry': 'Old synopsis.', 'relations': []}

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis.',
            'change_summary': '',
            'relation_changes': [],
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert 'truncated' not in result or result['truncated'] is False


def test_propose_new_entity_truncation_heuristic():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_new_entities

    journal = {
        'id': 789,
        'name': 'Session note',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'new_entities': [
                {'name': 'Zara', 'suggested_type': 'character', 'draft_entry': 'A mysterious figure,', 'reason': ''},
                {'name': 'Bob', 'suggested_type': 'location', 'draft_entry': 'A nice place.', 'reason': ''},
            ]
        }

        results = propose_new_entities(journal, set())

    # First entry ends with comma → truncated heuristic triggers
    assert results[0]['truncated'] is True
    # Second entry ends with period → not truncated
    assert 'truncated' not in results[1] or results[1].get('truncated') is False


def test_propose_update_stores_journal_id():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session note',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {'kind': 'character', 'local_id': 1, 'name': 'Alice', 'entry': 'Old synopsis.', 'relations': []}

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis.',
            'change_summary': '',
            'relation_changes': [],
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result['_journal_id'] == 789


# --- _is_new_info and journal link injection tests (Task 3) ---


def test_propose_update_injects_journal_link_when_new_info():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session 1: The Beginning',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Added new info',
            '_is_new_info': True,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    # Single paragraph — appended at end after whitespace collapse.
    assert '[journal:9431667|Session 1: The Beginning]' in result['proposed_entry']


def test_propose_update_injects_journal_link_before_last_paragraph():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session 1: The Beginning',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': '<p>Old content.</p>',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM returns old content + new paragraph at end (per prompt instructions).
        mock_chat.return_value = {
            'updated_entry': '<p>Old content about Alice.</p>\n\nFollowing her adventures, Alice retired.',
            'change_summary': 'Added retirement info',
            '_is_new_info': True,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    proposed = result['proposed_entry']
    # After whitespace collapse, old paragraph comes first, then [journal:N|name], then new content.
    assert '[journal:9431667|Session 1: The Beginning]' in proposed
    # Old content should NOT start with a journal link.
    assert not proposed.startswith('[journal')
    # New content (retirement) should be present after the journal link.
    assert 'retired' in proposed


def test_propose_update_injects_journal_link_at_new_info_start():
    """When new info is in paragraph 2 of 3 (not the last), journal link goes there."""
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session 2: The Middle',
        'date': '2024-01-08',
        'entry': 'Alice found a magic sword.',
        'created_at': '2024-01-09T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': '<p>Alice was a brave adventurer.</p>\n\nShe explored the dark cave.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM keeps old para 1, adds new info in para 2, then has para 3 (old).
        mock_chat.return_value = {
            'updated_entry': '<p>Alice was a brave adventurer.</p>\n\nShe found the magic sword and wielded it bravely.\n\nShe explored the dark cave.',
            'change_summary': 'Added sword acquisition',
            '_is_new_info': True,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    proposed = result['proposed_entry']
    # Journal link should be at the start of paragraph 2 (where new info starts),
    # NOT at paragraph 3 (the old content).
    assert '[journal:9431667|Session 2: The Middle]' in proposed
    # Paragraph 1 should not have a journal link.
    first_para = proposed.split('\n\n')[0]
    assert 'brave adventurer' in first_para.lower() and '[journal:' not in first_para
    # Journal paragraph should contain the new sword info, not the cave info.
    # Split by \n\n: [old_para_1, [journal] She found..., She explored...]
    paras = proposed.split('\n\n')
    journal_para_idx = next(i for i, p in enumerate(paras) if '[journal:' in p)
    assert 'sword' in paras[journal_para_idx].lower()


def test_propose_update_multi_new_paragraph_indices_injects_at_each():
    """When LLM returns multiple new_paragraph_indices, each marked para gets its own prefix."""
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session: Multi-Index',
        'date': '2024-01-01',
        'entry': 'Alice fought a dragon.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': '<p>Alice was a brave adventurer.</p>\n\nShe explored the dark cave.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM returns 3 paragraphs, indices [0, 2] — both have new info.
        mock_chat.return_value = {
            'updated_entry': '<p>Alice slew the dragon and was hailed a hero.</p>\n\nShe explored the dark cave.\n\nShe went on more adventures.',
            'change_summary': 'Added dragon slaying',
            '_is_new_info': True,
            'new_paragraph_indices': [0, 2],
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    proposed = result['proposed_entry']
    # Each marked paragraph gets its own prefix — two total.
    count = proposed.count('[journal:')
    assert count == 2, f'Expected 2 [journal: links but found {count} in:\n{proposed}'
    paras = proposed.split('\n\n')
    # Paragraph 0 (dragon slaying) and paragraph 2 (more adventures) have prefixes.
    assert '[journal:9431667|Session: Multi-Index]' in paras[0]
    assert 'dragon' in paras[0].lower()
    assert '[journal:9431667|Session: Multi-Index]' in paras[2]
    assert 'adventures' in paras[2].lower()


def test_propose_update_consecutive_indices_only_first_tagged():
    """When LLM flags consecutive paragraphs as new info, only the first gets a journal tag."""
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session: Warehouse',
        'date': '2024-01-01',
        'entry': 'New events at the warehouse.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'organisation',
        'local_id': 5,
        'name': 'The Guild',
        'entry': 'Old synopsis text.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM returns 4 paragraphs; indices [0,1,2] are consecutive (new info), index 3 is separate.
        mock_chat.return_value = {
            'updated_entry': (
                '<p>The Guild is a criminal organization.</p>\n\n'
                'They control the underworld.\n\n'
                'Their leader is feared by all.\n\n'
                'New raids have expanded their territory.'
            ),
            'change_summary': 'Updated Guild info',
            '_is_new_info': True,
            'new_paragraph_indices': [0, 1, 2, 3],
        }

        result = propose_update(5, entity, journal, {5: {'name': 'The Guild'}})

    assert result is not None
    proposed = result['proposed_entry']
    paras = proposed.split('\n\n')
    # All four indices [0,1,2,3] are consecutive — only the first (index 0) gets tagged.
    count = proposed.count('[journal:')
    assert count == 1, f'Expected 1 [journal: link but found {count} in:\n{proposed}'
    # First paragraph of the consecutive block gets the tag.
    assert '[journal:9431667|Session: Warehouse]' in paras[0]
    # Paragraphs 1, 2, and 3 are untagged continuation of new content.
    assert '[journal:' not in paras[1], f'Para 1 should be untagged: {paras[1]}'
    assert '[journal:' not in paras[2], f'Para 2 should be untagged: {paras[2]}'
    assert '[journal:' not in paras[3], f'Para 3 should be untagged: {paras[3]}'


def test_propose_update_gap_in_indices_creates_two_tags():
    """When LLM flags [0, 1, 3], indices 0/1 collapse to one tag and 3 gets its own."""
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session: Warehouse',
        'date': '2024-01-01',
        'entry': 'New events at the warehouse.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'organisation',
        'local_id': 5,
        'name': 'The Guild',
        'entry': 'Old synopsis text.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # Indices [0,1] are consecutive (collapse to one tag), index 3 is separate.
        mock_chat.return_value = {
            'updated_entry': (
                '<p>The Guild is a criminal organization.</p>\n\n'
                'They control the underworld.\n\n'
                'Their leader is feared by all.\n\n'
                'New raids have expanded their territory.'
            ),
            'change_summary': 'Updated Guild info',
            '_is_new_info': True,
            'new_paragraph_indices': [0, 1, 3],
        }

        result = propose_update(5, entity, journal, {5: {'name': 'The Guild'}})

    assert result is not None
    proposed = result['proposed_entry']
    paras = proposed.split('\n\n')
    # Indices [0,1] collapse to one tag (at 0), index 3 gets its own — two total.
    count = proposed.count('[journal:')
    assert count == 2, f'Expected 2 [journal: links but found {count} in:\n{proposed}'
    assert '[journal:' in paras[0]
    assert '[journal:' not in paras[1], f'Para 1 should be untagged: {paras[1]}'
    assert '[journal:' not in paras[2], f'Para 2 should be untagged: {paras[2]}'
    assert '[journal:' in paras[3]


def test_propose_update_no_journal_link_when_not_new_info():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session 1',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Rephrased',
            '_is_new_info': False,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    assert '[journal:' not in result['proposed_entry']


def test_propose_update_no_journal_link_when_missing_field():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'name': 'Session 1',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM output without _is_new_info field (backward compat)
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Added info',
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    assert '[journal:' not in result['proposed_entry']


def test_propose_update_sanitize_journal_name_special_chars():
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session | Special ] Name',
        'date': '2024-01-01',
        'entry': 'Alice saved the day.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': 'Old synopsis.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        mock_chat.return_value = {
            'updated_entry': 'New synopsis about Alice.',
            'change_summary': 'Added info',
            '_is_new_info': True,
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    # Pipe and bracket should be stripped from the link text; appended at end for single-paragraph output.
    assert '[journal:9431667|Session  Special  Name]' in result['proposed_entry']


def test_propose_update_strips_existing_journal_tags_before_inject():
    """When LLM already includes [journal:N|...] tags in its output, they are stripped
    before the code injects a fresh one. This prevents double-tagging when the LLM
    echoes back journal links from the prompt."""
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session: Test',
        'date': '2024-01-01',
        'entry': 'Alice fought a dragon.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        'entry': '<p>Alice was a brave adventurer.</p>',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM echoes back journal tag in its output (common when prompt contains annotations)
        mock_chat.return_value = {
            'updated_entry': '[journal:12345|Old Session] Alice slew the dragon and was hailed a hero.\n\nShe explored the dark cave.',
            'change_summary': 'Added dragon slaying',
            '_is_new_info': True,
            'new_paragraph_indices': [0],
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    proposed = result['proposed_entry']
    # Should have exactly ONE journal link — the fresh one injected by code.
    count = proposed.count('[journal:')
    assert count == 1, f'Expected 1 [journal: link but found {count} in:\n{proposed}'
    paras = proposed.split('\n\n')
    # The first paragraph should have the new journal prefix and the content without double tags.
    assert '[journal:9431667|Session: Test]' in paras[0]
    assert 'slayed' not in paras[0]  # original text, not modified
    assert 'dragon' in paras[0].lower()


def test_propose_update_preserves_existing_journal_tag_on_rephrased_content():
    """When LLM echoes back old tagged content with an existing journal tag,
    the existing tag should NOT be replaced with a new one from the current session."""
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session: New Adventure',
        'date': '2024-01-01',
        'entry': 'Alice explored the sewers and found a hidden treasure.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        # Old synopsis with an existing journal tag from a previous sync
        'entry': '[journal:9431667|Old Session] Alice explored the underground tunnels beneath the city.\n\nShe is brave and courageous.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM echoes back old tagged content, keeping the existing journal tag.
        # The LLM flags index 0 as new info but doesn't strip the existing tag.
        mock_chat.return_value = {
            'updated_entry': '[journal:9431667|Old Session] Alice explored the underground tunnels beneath the city.\n\nShe is brave and courageous.',
            'change_summary': 'Added info',
            '_is_new_info': True,
            'new_paragraph_indices': [0],
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    proposed = result['proposed_entry']
    paras = proposed.split('\n\n')
    # Both paragraphs should be preserved exactly as the LLM echoed them back.
    # Paragraph 0 has an old journal tag — it stays unchanged (not replaced).
    assert '[journal:9431667|Old Session]' in paras[0]
    count = proposed.count('[journal:')
    assert count == 1, f'Expected exactly 1 [journal: link but found {count} in:\n{proposed}'


def test_propose_update_preserves_old_journal_tag_on_fuzzy_matched_rephrase():
    """When LLM rephrases old tagged content WITHOUT a journal tag and flags it as new info,
    fuzzy matching against old tagged paragraphs should preserve the original tag."""
    from unittest.mock import patch

    from kanka_wiki_updater.sync_pipeline import propose_update

    journal = {
        'id': 789,
        'entity_id': 9431667,
        'name': 'Session: New Adventure',
        'date': '2024-01-01',
        'entry': 'Alice found a hidden treasure in the sewers.',
        'created_at': '2024-01-02T10:00:00',
    }
    entity = {
        'kind': 'character',
        'local_id': 1,
        'name': 'Alice',
        # Old synopsis with an existing journal tag — second paragraph
        'entry': 'Alice is a brave adventurer.\n\n[journal:9431667|Previous Session] Alice explored the underground tunnels beneath the city and found treasure.',
        'relations': [],
    }

    with patch('kanka_wiki_updater.sync_pipeline.chat_json') as mock_chat:
        # LLM rephrases old tagged content (para 2) WITHOUT preserving the journal tag,
        # but keeps the text nearly identical. Flags it as new info at index 1.
        mock_chat.return_value = {
            'updated_entry': 'Alice is a brave adventurer.\n\nShe explored the underground tunnels beneath the city and found treasure.',
            'change_summary': 'Added info',
            '_is_new_info': True,
            # LLM says index 1 has new info — but it's semantically identical to old tagged para.
            'new_paragraph_indices': [1],
        }

        result = propose_update(1, entity, journal, {1: {'name': 'Alice'}})

    assert result is not None
    proposed = result['proposed_entry']
    # The second paragraph should preserve the OLD tag (9431667|Previous Session),
    # NOT inject a new one with current session's info.
    paras = proposed.split('\n\n')
    assert '[journal:9431667|Previous Session]' in proposed, f'Expected preserved old tag in:\n{proposed}'
    count = proposed.count('[journal:')
    # Should have exactly 1 journal link (only paragraph 1 had one)
    assert count == 1, f'Expected 1 [journal: link but found {count} in:\n{proposed}'
