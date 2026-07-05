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
