"""Tests for sync_pipeline pure functions."""

from types import SimpleNamespace as AttrMap

from kanka_wiki_updater.sync_pipeline import (
    EntityData,
    apply_relation_changes_locally,
    build_entity_index,
    find_mentioned_entities,
    journal_sort_key,
    relation_summary,
)


def _d(d):
    """Convert a dict to SimpleNamespace for attribute access."""
    return AttrMap((k, _d(v) if isinstance(v, dict) else v) for k, v in d.items())


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
            return [_d(r) for r in client_data]

        def get_locations(self):
            return []

    index = build_entity_index(MockClient())
    assert 123 in index
    assert index[123].kind == 'character'
    assert index[123].local_id == 456
    assert index[123].name == 'Alice'


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
            return [_d(r) for r in client_data]

    index = build_entity_index(MockClient())
    assert 789 in index
    assert index[789].kind == 'location'


def test_build_entity_index_empty():
    class MockClient:
        def get_characters(self):
            return []

        def get_locations(self):
            return []

    index = build_entity_index(MockClient())
    assert len(index) == 0


def test_build_entity_index_missing_entry():
    class MockClient:
        def get_characters(self):
            return [_d({'entity_id': 1, 'id': 2, 'name': 'Bob', 'relations': []})]

        def get_locations(self):
            return []

    index = build_entity_index(MockClient())
    assert index[1].entry == ''


def test_build_entity_index_missing_relations():
    class MockClient:
        def get_characters(self):
            return [_d({'entity_id': 1, 'id': 2, 'name': 'Bob', 'entry': 'Test'})]

        def get_locations(self):
            return []

    index = build_entity_index(MockClient())
    assert index[1].relations == []


# --- relation_summary tests (Task 9) ---


def test_relation_summary_empty():
    result = relation_summary([], {})
    assert result == '(none on record)'


def test_relation_summary_with_relations():
    entity_data = AttrMap(name='Bob')
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
    entity1 = AttrMap(name='Alice')
    entity2 = AttrMap(name='Bob')
    index = {1: entity1, 2: entity2}
    relations = [
        {'target_id': 1, 'relation': 'Friend', 'attitude': 80},
        {'target_id': 2, 'relation': 'Enemy', 'attitude': -60},
    ]
    result = relation_summary(relations, index)
    lines = result.split('\n')
    assert len(lines) == 2


def test_relation_summary_none_attitude():
    entity_data = AttrMap(name='Alice')
    index = {1: entity_data}
    relations = [{'target_id': 1, 'relation': 'Acquaintance', 'attitude': None}]
    result = relation_summary(relations, index)
    assert 'None' in result


# --- find_mentioned_entities tests (Task 9) ---


def test_find_mentioned_entities_linked_only():
    text = '[character:123|Alice] went to [location:456|Castle]'
    entity1 = AttrMap(kind='character', name='Alice')
    entity2 = AttrMap(kind='location', name='Castle')
    index = {123: entity1, 456: entity2}
    result = find_mentioned_entities(text, index)
    assert 123 in result
    assert 456 in result


def test_find_mentioned_entities_no_links():
    text = 'Someone went somewhere'
    entity_data = AttrMap(kind='character', name='Alice')
    index = {123: entity_data}
    result = find_mentioned_entities(text, index)
    assert len(result) == 0


def test_find_mentioned_entities_fuzzy_match():
    text = 'Alice went to the castle'
    entity_data = AttrMap(kind='character', name='Alice')
    index = {123: entity_data}
    result = find_mentioned_entities(text, index)
    assert 123 in result


def test_find_mentioned_entities_filters_unknown():
    text = '[entity:999|Unknown]'
    entity_data = AttrMap(kind='character', name='Alice')
    index = {123: entity_data}
    result = find_mentioned_entities(text, index)
    assert 999 not in result


def test_find_mentioned_entities_empty_text():
    entity_data = AttrMap(kind='character', name='Alice')
    result = find_mentioned_entities('', {123: entity_data})
    assert len(result) == 0


# --- journal_sort_key tests (Task 10) ---


def test_journal_sort_key_gregorian_date():
    j = AttrMap(date='2024-06-15', created_at='2024-06-15T10:00:00')
    result = journal_sort_key(j)
    assert result[0] == 0
    assert result[1] == 2024


def test_journal_sort_key_custom_calendar():
    j = AttrMap(calendar_year=5, calendar_month=3, calendar_day=12)
    result = journal_sort_key(j)
    assert result[0] == 0
    assert result[1] == 5


def test_journal_sort_key_fallback_to_created_at():
    j = AttrMap(created_at='2024-06-15T10:00:00')
    result = journal_sort_key(j)
    assert result[0] == 1


def test_journal_sort_key_date_over_created():
    j_dated = AttrMap(date='2024-06-15', created_at='2024-07-01T10:00:00')
    j_undated = AttrMap(created_at='2024-06-10T10:00:00')

    key_dated = journal_sort_key(j_dated)
    key_undated = journal_sort_key(j_undated)

    assert key_dated < key_undated


def test_journal_sort_key_empty_date():
    j = AttrMap(date='', created_at='2024-06-15T10:00:00')
    result = journal_sort_key(j)
    assert result[0] == 1


def test_journal_sort_key_no_date_or_created():
    j = AttrMap()
    result = journal_sort_key(j)
    assert result[0] == 1
    assert result[4] == ''


# --- apply_relation_changes_locally tests (Task 10) ---


def test_apply_relation_changes_locally_create():
    index = {
        123: EntityData(kind='character', local_id=1, name='Alice', relations=[]),
        456: AttrMap(name='Bob'),
    }
    name_to_id = {'Bob': 456}

    apply_relation_changes_locally(
        123,
        [{'action': 'create', 'target_name': 'Bob', 'relation': 'Friend'}],
        index,
        name_to_id,
    )

    assert len(index[123].relations) == 1
    assert index[123].relations[0]['target_id'] == 456


def test_apply_relation_changes_locally_update():
    index = {
        123: EntityData(
            kind='character',
            local_id=1,
            name='Alice',
            relations=[{'target_id': 456, 'relation': 'Acquaintance', 'attitude': None}],
        ),
        456: AttrMap(name='Bob'),
    }
    name_to_id = {'Bob': 456}

    apply_relation_changes_locally(
        123,
        [{'action': 'update', 'target_name': 'Bob', 'relation': 'Friend', 'attitude': 80}],
        index,
        name_to_id,
    )

    rel = index[123].relations[0]
    assert rel['relation'] == 'Friend'
    assert rel['attitude'] == 80


def test_apply_relation_changes_locally_delete():
    index = {
        123: EntityData(
            kind='character',
            local_id=1,
            name='Alice',
            relations=[{'target_id': 456, 'relation': 'Friend'}],
        ),
        456: AttrMap(name='Bob'),
    }
    name_to_id = {'Bob': 456}

    apply_relation_changes_locally(
        123,
        [{'action': 'delete', 'target_name': 'Bob'}],
        index,
        name_to_id,
    )

    assert len(index[123].relations) == 0


def test_apply_relation_changes_locally_unknown_target():
    index = {123: EntityData(kind='character', local_id=1, name='Alice', relations=[])}
    name_to_id = {}

    apply_relation_changes_locally(
        123,
        [{'action': 'create', 'target_name': 'Unknown', 'relation': 'Friend'}],
        index,
        name_to_id,
    )

    assert len(index[123].relations) == 0


def test_apply_relation_changes_locally_empty_changes():
    index = {123: EntityData(kind='character', local_id=1, name='Alice', relations=[])}
    apply_relation_changes_locally(123, [], index, {})
    assert len(index[123].relations) == 0
