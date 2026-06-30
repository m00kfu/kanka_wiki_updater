"""Tests for entity resolution in session notes."""

from kanka_wiki_updater.mentions import (
    add_missing_entity_tags,
    auto_link_entry,
    find_unlinked_mentions,
    fuzzy_name_matches,
    linked_entity_ids,
    normalize_text,
    strip_html,
)


def test_strip_html_basic():
    assert strip_html('<br><br>test') == 'test'


def test_strip_html_empty():
    assert strip_html('') == ''


def test_strip_html_none():
    assert strip_html(None) == ''


def test_strip_html_unescapes_entities():
    # html.unescape converts &amp; to &, so the result should contain '&'
    assert '&' in strip_html('&amp;')


def test_normalize_text_same():
    a = '[character:123|John] went to [location:456|Castle]'
    b = '[character:123|John] went to [location:456|Castle]'
    assert normalize_text(a) == normalize_text(b)


def test_normalize_text_different():
    a = 'hello world'
    b = 'goodbye world'
    assert normalize_text(a) != normalize_text(b)


def test_linked_entity_ids_single():
    text = '[character:123|John]'
    result = linked_entity_ids(text)
    assert 123 in result


def test_linked_entity_ids_multiple():
    text = '[character:123|John] visited [location:456|Castle]'
    ids = linked_entity_ids(text)
    assert 123 in ids
    assert 456 in ids


def test_linked_entity_ids_no_links():
    text = 'John went to the castle'
    assert linked_entity_ids(text) == set()


# --- fuzzy_name_matches tests (Task 1) ---


def test_fuzzy_name_matches_exact():
    result = fuzzy_name_matches('Alice went to the castle', {123: 'Alice'})
    assert 123 in result


def test_fuzzy_name_matches_first_word():
    # "Alic" gives ratio ~0.89 against "Alice", above default threshold (0.84)
    result = fuzzy_name_matches('Alic went to the castle', {123: 'Alice'})
    assert 123 in result


def test_fuzzy_name_matches_no_match():
    result = fuzzy_name_matches('Bob went to the tavern', {123: 'Alice'})
    assert 123 not in result


def test_fuzzy_name_matches_multiple_entities():
    names = {123: 'Alice', 456: 'Bob'}
    result = fuzzy_name_matches('Alice and Bob went together', names)
    assert 123 in result
    assert 456 in result


def test_fuzzy_name_matches_empty_input():
    result = fuzzy_name_matches('', {123: 'Alice'})
    assert 123 not in result


def test_fuzzy_name_matches_none_input():
    result = fuzzy_name_matches(None, {123: 'Alice'})
    assert result == set()


# --- find_unlinked_mentions tests (Task 2) ---


def test_find_unlinked_mentions_basic():
    index = {123: 'Alice', 456: 'Bob'}
    result = find_unlinked_mentions('Alice went to the castle', index)
    assert (123, 'Alice') in result


def test_find_unlinked_mentions_skips_linked():
    text = '[character:123|Alice] went to the castle'
    index = {123: 'Alice', 456: 'Bob'}
    result = find_unlinked_mentions(text, index)
    assert len(result) == 0


def test_find_unlinked_mentions_skips_short_names():
    # "A" (1 char) is below min_name_length=4; "Bobby" (5 chars) passes
    index = {123: 'A', 456: 'Bobby'}
    result = find_unlinked_mentions('A and Bobby went together', index)
    assert (123, 'A') not in result
    assert (456, 'Bobby') in result


def test_find_unlinked_mentions_exclude_entity():
    text = 'Alice and Bobby went together'
    index = {123: 'Alice', 456: 'Bobby'}
    result = find_unlinked_mentions(text, index, exclude_entity_id=123)
    assert (123, 'Alice') not in result
    assert (456, 'Bobby') in result


def test_find_unlinked_mentions_no_false_positives():
    index = {123: 'Alice'}
    result = find_unlinked_mentions('The quick brown fox', index)
    assert len(result) == 0


def test_find_unlinked_mentions_empty_input():
    result = find_unlinked_mentions('', {123: 'Alice'})
    assert result == []


def test_find_unlinked_mentions_none_input():
    result = find_unlinked_mentions(None, {123: 'Alice'})
    assert result == []


# --- auto_link_entry & add_missing_entity_tags tests (Task 3) ---


def test_auto_link_entry_basic():
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    text = 'Alice went to the castle'
    new_text, linked = auto_link_entry(text, index)
    assert '[character:123|Alice]' in new_text
    assert (123, 'Alice') in linked


def test_auto_link_entry_skips_already_linked():
    text = '[character:123|Alice] went to the castle'
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    new_text, linked = auto_link_entry(text, index)
    assert linked == []


def test_auto_link_entry_skips_exclude_entity():
    text = 'Alice went to the castle'
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    new_text, linked = auto_link_entry(text, index, exclude_entity_id=123)
    assert linked == []


def test_auto_link_entry_longest_first():
    index = {
        123: {'name': 'Renaer Neverember', 'kind': 'character'},
        456: {'name': 'Neverember', 'kind': 'location'},
    }
    text = 'Renaer Neverember went to Neverember'
    new_text, linked = auto_link_entry(text, index)
    assert '[character:123|Renaer Neverember]' in new_text


def test_add_missing_entity_tags_basic():
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    text = 'Alice went to the castle'
    new_text, details = add_missing_entity_tags(text, index)
    assert '[character:123|Alice]' in new_text
    assert (123, 'character', 'Alice') in details


def test_add_missing_entity_tags_empty():
    text = '[character:123|Alice] went to the castle'
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    new_text, details = add_missing_entity_tags(text, index)
    assert new_text == text
    assert details == []


def test_auto_link_entry_empty_input():
    result_text, linked = auto_link_entry('', {})
    assert result_text == ''
    assert linked == []


def test_add_missing_entity_tags_none_input():
    result_text, details = add_missing_entity_tags(None, {})
    assert result_text == ''
    assert details == []
