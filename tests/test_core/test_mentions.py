"""Tests for entity resolution in session notes."""

from kanka_wiki_updater.core.mentions import (
    JOURNAL_LINK_RE,
    add_missing_entity_tags,
    auto_link_entry,
    find_unlinked_mentions,
    fuzzy_name_matches,
    linked_entity_ids,
    normalize_text,
    strip_html,
    strip_journal_links,
)


def test_strip_journal_links_bare_format():
    assert strip_journal_links('[journal:179078]The adventurers arrived') == 'The adventurers arrived'


def test_strip_journal_links_display_name_format():
    assert (
        strip_journal_links('[journal:9423110|Last time on Phandelver Z pt 1]Text here')
        == 'Text here'
    )


def test_journal_link_re_matches_bare_and_fully_formatted():
    assert JOURNAL_LINK_RE.search('[journal:179078]')
    assert JOURNAL_LINK_RE.search('[journal:9423110|Last time on Phandelver Z pt 1]')
    assert not JOURNAL_LINK_RE.search('some plain text without links')


def test_strip_html_basic():
    # <br> tags become newlines; leading/trailing whitespace is stripped
    assert strip_html('<br><br>test') == 'test'


def test_strip_html_preserves_paragraphs():
    assert strip_html('<p>First paragraph.</p><br><p>Second paragraph.</p>') == 'First paragraph.\n\nSecond paragraph.'


def test_strip_html_block_tags():
    assert '\n\n' in strip_html('<div>A block</div><p>Another block</p>')


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


def test_fuzzy_name_matches_compound_prefix_no_false_positive():
    # "Xanathar" (char) and "Xanathar Guild" (org) should not be interchangeable.
    # When text contains the full compound name, only the org is matched.
    names = {123: 'Xanathar', 456: 'Xanathar Guild'}
    result = fuzzy_name_matches('the Xanathar Guild attacked', names)
    assert 456 in result  # compound exact match
    assert 123 not in result  # char should NOT be matched via partial


def test_fuzzy_name_matches_standalone_first_word_no_compound_match():
    # When text contains just "xanathar" (no following compound word),
    # the character entity matches but not the compound.
    names = {123: 'Xanathar', 456: 'Xanathar Guild'}
    result = fuzzy_name_matches('the Xanathar arrived late', names)
    assert 123 in result  # exact word-boundary match for char


def test_fuzzy_name_matches_compound_with_possessive():
    # "Xanathar's guild" - possessive excludes the character via lookahead;
    # compound fuzzy first-word is skipped because 'guild' follows.
    names = {123: 'Xanathar', 456: 'Xanathar Guild'}
    result = fuzzy_name_matches("the Xanathar's guild attacked", names)
    assert 123 not in result  # possessive exclusion blocks exact match


def test_fuzzy_name_matches_single_word_unaffected():
    # Single-word entity names should still use full fuzzy first-word matching.
    names = {123: 'Alice', 456: 'Bob'}
    result = fuzzy_name_matches('Alic went to the party', names)
    assert 123 in result  # "Alic" fuzzy-matches "Alice"


def test_fuzzy_name_matches_multiple_compounds_no_cross_contamination():
    # Two different compound entities should not falsely match each other.
    names = {1: 'Stormwind City', 2: 'Stormwind'}
    result = fuzzy_name_matches('The Stormwind City guards patrolled.', names)
    assert 1 in result  # exact compound match
    assert 2 not in result  # partial should be skipped


def test_fuzzy_name_matches_compound_only_first_word_in_text():
    # When only the first word of a compound appears (no following words),
    # fuzzy first-word matching still applies since there's no extension.
    names = {1: 'Xanathar', 2: 'Xanathar Guild'}
    result = fuzzy_name_matches('xanathar is here', names)
    assert 1 in result  # exact word-boundary match


def test_fuzzy_name_matches_three_word_compound():
    # Three-word compound names should also get context-aware checking.
    names = {1: 'Blackwood', 2: 'Blackwood Forest', 3: 'Blackwood Forest Temple'}
    result = fuzzy_name_matches('The Blackwood Forest was dangerous.', names)
    assert 2 in result  # exact match for two-word compound
    assert 1 not in result  # partial should be skipped


def test_fuzzy_name_matches_context_check_false_negative():
    # When the following words don't form a known entity, fuzzy first-word
    # matching should still apply (no false negative from context check).
    names = {1: 'Alic', 2: 'Alice'}
    result = fuzzy_name_matches('Alic went to the party', names)
    assert 2 in result  # "Alic" fuzzy-matches "Alice" first word


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
    _, linked = auto_link_entry(text, index)
    assert linked == []


def test_auto_link_entry_skips_exclude_entity():
    text = 'Alice went to the castle'
    index = {123: {'name': 'Alice', 'kind': 'character'}}
    _, linked = auto_link_entry(text, index, exclude_entity_id=123)
    assert linked == []


def test_auto_link_entry_longest_first():
    index = {
        123: {'name': 'Renaer Neverember', 'kind': 'character'},
        456: {'name': 'Neverember', 'kind': 'location'},
    }
    text = 'Renaer Neverember went to Neverember'
    new_text, _ = auto_link_entry(text, index)
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
