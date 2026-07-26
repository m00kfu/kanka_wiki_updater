"""
Resolve which characters/locations a piece of session-note text refers to.

Kanka's editor links other entries with bracket syntax like [entity:123]
or [character:123|Display Name]. We use that directly when present (cheap
and 100% accurate), and fall back to fuzzy name matching against the known
character/location list for plain prose mentions that weren't linked.
"""

import html
import re
from difflib import SequenceMatcher

MENTION_RE = re.compile(
    r'\[(?:entity|character|location|organisation|monster|deity|background|class|subrace|race):(\d+)'
)
MENTION_DISPLAY_RE = re.compile(
    r'(\[(?:entity|character|location|organisation|monster|deity|background|class|subrace|race):\d+)\|[^\]]*\]'
)
LINK_SPAN_RE = re.compile(
    r'\[(?:entity|character|location|organisation|monster|deity|background|class|subrace|race):\d+(?:\|[^\]]*)?\]'
)
JOURNAL_LINK_RE = re.compile(r'\[journal:\d+(?:\|[^\]]*)?\]')
TAG_RE = re.compile(r'<[^>]+>')
BLOCK_TAGS_RE = re.compile(r'<\s*(p|div)\b[^>]*>|</(?:p|div)\s*>', re.IGNORECASE)
INLINE_BREAKS_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)


def strip_html(raw):
    if not raw:
        return ''
    # Convert block-level tags (p, div) to double newlines for paragraph breaks
    text = BLOCK_TAGS_RE.sub('\n\n', raw)
    # Convert inline line breaks (<br>) to single newlines
    text = INLINE_BREAKS_RE.sub('\n', text)
    # Collapse runs of 3+ newlines into exactly 2 (one blank line separator)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Strip remaining HTML tags and unescape entities
    text = html.unescape(TAG_RE.sub(' ', text))
    return text.strip()


def strip_journal_links(raw):
    """Remove [journal:N|...] wiki links from text for comparison purposes.

    The LLM sometimes echoes back journal attribution tags that appear in the
    input prompt (from _annotate_journals). Stripping them ensures paragraph
    comparisons reflect actual content similarity, not artifact presence/absence.
    """
    if not raw:
        return ''
    text = JOURNAL_LINK_RE.sub('', raw)
    # Collapse any leading/trailing whitespace left by the removal
    return ' '.join(text.split())


def normalize_text(raw):
    """Strip HTML, collapse whitespace, and normalize Kanka mention links so
    that two versions of an entry compare as equal when they differ only in
    formatting or in the *optional* display-name part of a mention link --
    e.g. [location:9418490] vs [location:9418490|Waterdeep] render
    identically in Kanka, so the model adding/changing/removing just that
    display text isn't a real change worth a review prompt."""
    text = MENTION_DISPLAY_RE.sub(r'\1]', raw or '')
    return ' '.join(strip_html(text).split())


def linked_entity_ids(raw_entry):
    """IDs explicitly linked via [entity:N] / [character:N] / [location:N]."""
    return {int(m) for m in MENTION_RE.findall(raw_entry or '')}


def _extends_into_known_name(matched_words, known_names):
    """Check if a sequence of words starting from a fuzzy match position
    forms another known entity's full name (at least 2 words).

    Used to prevent partial matches: when first-word fuzzy matching finds
    'xanathar' in text and the next word is 'guild', check whether
    'xanathar guild' is itself a known entity name. If so, skip adding
    the partial match since the compound entity owns that region of text.

    Only checks candidates with 2+ words to avoid false positives where
    a single-word entity's own name triggers the check against itself.

    Args:
        matched_words: list of consecutive words from the text starting at
                       the fuzzy-match position (includes the first word).
        known_names: set of all known entity names (lowercased).

    Returns:
        True if matched_words forms a multi-word known entity's full name,
        False otherwise or if no match is found.
    """
    # Only check candidates with 2+ words to avoid matching single-word
    # entities against themselves (e.g., 'xanathar' in all_names_lower)
    for i in range(2, len(matched_words) + 1):
        candidate = ' '.join(matched_words[:i])
        if candidate in known_names:
            return True
    return False


def _is_likely_plural(word):
    """Return True if *word* looks like a regular English plural.

    Covers -s, -es, and -ies patterns so we can reject fuzzy matches that
    only differ by a trailing 's' (e.g. 'orders' vs 'Order').
    """
    w = word.lower()
    if len(w) <= 2:
        return False
    if w.endswith('ies') and len(w) > 3:
        return True
    if w.endswith(('ss', 'sh', 'ch', 'x', 'z')):
        return w.endswith('es')
    if w.endswith('s') and not w.endswith('us') and not w.endswith('is'):
        return True
    return False


def fuzzy_name_matches(text, names_by_entity_id, threshold=0.84):
    """Catch plain-text mentions of known names that weren't @-linked.

    Uses word-boundary matching for exact matches and fuzzy first-word
    comparison as a fallback for misspelled or abbreviated references.
    For compound entity names (e.g. 'Xanathar Guild'), first-word fuzzy
    matching is context-aware: if the matched region extends into another
    known entity's full name, the partial match is skipped to prevent
    treating 'Xanathar' and 'Xanathar Guild' as interchangeable.

    This is a cheap word-window fuzzy match -- good enough for proper
    nouns, not meant to be perfect. Always double-check the review queue
    rather than trusting this blindly."""
    text_lower = (text or '').lower()
    words = text_lower.split()

    # Pre-compute exact-match character spans so fuzzy matches don't overlap.
    # When "Cragmaw Castle" is an exact match in the text, we must not also
    # fuzzy-match "castle" into "Castle Ward".
    _exact_spans = []  # list of (start_char, end_char, entity_id)
    for eid_check, name_check in names_by_entity_id.items():
        pat = r'\b' + re.escape(name_check.lower()) + r'\b(?!\s*\')'
        for m in re.finditer(pat, text_lower):
            _exact_spans.append((m.start(), m.end(), eid_check))

    def _find_word_char_pos(text_str, wrds, idx):
        """Find the character start position of *idx*-th word in *text_str*."""
        pos = 0
        for i in range(idx + 1):
            if i == idx:
                return pos
            # skip past this word and any trailing whitespace
            while pos < len(text_str) and text_str[pos] != ' ':
                pos += 1
            while pos < len(text_str) and text_str[pos] == ' ':
                pos += 1
        return pos

    def _span_covers_word(word_start_char):
        """Return (True, owning_eid) if any exact-match span covers the given char offset."""
        for s, e, eid in _exact_spans:
            if s <= word_start_char < e:
                return True, eid
        return False, None

    found = set()

    # Build lowercase lookup of all known names for context checks.
    all_names_lower = {name.lower() for name in names_by_entity_id.values()}

    for entity_id, name in names_by_entity_id.items():
        name_lower = name.lower()
        word_parts = name_lower.split()

        # Use word-boundary matching so "Xanathar" does not match inside
        # "Xanathar's Guild".  Negative lookahead blocks possessive forms
        # (e.g. "Xanathar's") since they usually refer to a compound noun
        # like the guild, not the character itself.
        exact_pattern = r'\b' + re.escape(name_lower) + r'\b(?!\s*\')'
        if re.search(exact_pattern, text_lower):
            # For single-word names, check whether all occurrences are inside
            # known compound entity names (e.g. "Xanathar" inside
            # "Xanathar Guild"). If so, skip to avoid treating them as
            # interchangeable mentions.
            if len(word_parts) == 1:
                all_inside_compounds = True
                for m in re.finditer(exact_pattern, text_lower):
                    match_text = m.group()
                    # Get everything after this match and extract the following words
                    remainder = text_lower[m.end() :]
                    remaining_words = remainder.split()
                    following_words = [w.strip('.,!?;:\'"') for w in remaining_words[: len(word_parts) + 2]]

                    if not _extends_into_known_name([match_text, *following_words], all_names_lower):
                        all_inside_compounds = False
                        break

                if all_inside_compounds:
                    continue  # every occurrence is part of a compound entity

            found.add(entity_id)
            continue

        first_word = word_parts[0] if word_parts else name_lower
        if len(first_word) < 4:
            continue

        # For compound names, check whether the fuzzy-matched region
        # extends into another known entity's full name. If so, skip
        # this partial match to avoid treating 'Xanathar' and
        # 'Xanathar Guild' as interchangeable entities.
        is_compound = len(word_parts) > 1

        for word_idx, word in enumerate(words):
            clean_word = word.strip('.,!?;:\'"')
            # For compound names, strip possessive suffixes so "Xanathar's"
            # fuzzy-matches the first-word of "Xanathar Guild", then let the
            # context check decide whether to accept or skip based on following
            # words (e.g. "guild" → compound owns this region).
            # For single-word names, block fuzzy matching when the text word
            # has a possessive suffix -- this preserves the original intent of
            # the exact-match possessive exclusion.
            if is_compound:
                clean_word = re.sub(r"'s$", '', clean_word)
            elif "'s" in clean_word or "'" in clean_word:
                continue  # possessive form -- skip to respect original exclusion

            # Skip fuzzy matching when the text word is a likely English plural
            # but the entity name's first word is singular (e.g. "orders" vs
            # "Order").  This prevents common nouns from triggering false
            # positives on unrelated entity names.
            if _is_likely_plural(clean_word) and not _is_likely_plural(first_word):
                continue

            # Require the text word to be at least 4 characters for fuzzy
            # matching. Three-character words produce noisy ratios that lead
            # to false positives (e.g. "the" → "The").  Longer common nouns
            # are still caught by the plural check above.
            if len(clean_word) < 4:
                continue

            if SequenceMatcher(None, first_word, clean_word).ratio() >= threshold:
                # For compound names, require at least one additional word from
                # the entity name to appear nearby. This prevents "black" alone
                # from fuzzy-matching into "Black Viper" when only that single
                # adjective appears in the text.
                if is_compound:
                    remaining_words = set(w.lower() for w in word_parts[1:])
                    search_window = min(len(words) - word_idx - 1, len(word_parts) + 2)
                    nearby = [w.strip('.,!?;:\'"').lower() for w in words[word_idx + 1 : word_idx + search_window]]
                    if not any(rw in remaining_words for rw in nearby):
                        continue  # no additional name word found nearby

                # Check context: are subsequent words part of another entity?
                if is_compound:
                    following_words = [w.strip('.,!?;:\'"') for w in words[word_idx + 1 : word_idx + len(word_parts)]]
                    if _extends_into_known_name(
                        [clean_word, *following_words],
                        all_names_lower,
                    ):
                        continue  # another compound owns this region

                # Skip if an exact match for a different entity already covers
                # this position in the text (e.g. "cragmaw castle" should not
                # also fuzzy-match into "Castle Ward").
                word_char_start = _find_word_char_pos(text_lower, words, word_idx)
                covered, covering_eid = _span_covers_word(word_char_start)
                if covered and covering_eid is not None:
                    # Only allow overlap when our name is very similar to the
                    # owning entity's name (e.g. "Alic" vs "Alice").  This
                    # prevents "Castle Ward" from matching where
                    # "Cragmaw Castle" already has an exact match.
                    covering_name = names_by_entity_id[covering_eid].lower()
                    full_ratio = SequenceMatcher(None, name_lower, covering_name).ratio()
                    if full_ratio < 0.7:
                        continue  # this region is owned by a different entity
                    # full_ratio >= 0.7 means our name is very similar to the
                    # owning entity's — allow overlap (e.g. "Alic"/"Alice")

                found.add(entity_id)
                break

    return found


def find_unlinked_mentions(text, names_by_entity_id, exclude_entity_id=None, min_name_length=4):
    """Find known entity names that appear as literal plain text in `text`
    with NO existing [entity:N]-style link anywhere for that entity --
    candidates for a link that's missing entirely, either because the model
    never added one for a known entity it named, or because it flattened
    the only existing link to plain text while rewriting nearby prose.

    An entity already linked ANYWHERE in the text is not flagged even if
    its name also appears elsewhere as plain text -- Kanka convention (and
    auto_link_entry below) is to link only the first mention of a name, so
    later plain-text repeats of an already-linked name are expected, not a
    problem.

    Deliberately exact (word-boundary) matching rather than the fuzzy pass
    used for raw session notes -- false positives here would flag harmless
    prose as a problem on every single review, which gets ignored fast.
    Short names are skipped (min_name_length) for the same reason."""
    text = text or ''
    already_linked = linked_entity_ids(text)
    masked = LINK_SPAN_RE.sub(lambda m: ' ' * len(m.group(0)), text)
    found = []
    for entity_id, name in names_by_entity_id.items():
        if entity_id == exclude_entity_id or len(name) < min_name_length:
            continue
        if entity_id in already_linked:
            continue
        if re.search(r'\b' + re.escape(name) + r'\b', masked):
            found.append((entity_id, name))
    return found


def auto_link_entry(text, index, exclude_entity_id=None, min_name_length=4):
    """Insert a [character:N|Name] / [location:N|Name] link at the first
    unlinked plain-text occurrence of each known entity name in `text`,
    for any entity that doesn't already have a link somewhere in the text.
    Existing link spans (and ones inserted earlier in this same call) are
    never matched into.

    `index` is the same shape sync_pipeline.py builds:
    {entity_id: {"name": ..., "kind": "character"|"location", ...}}.

    Returns (new_text, linked) where linked is a list of (entity_id, name)
    for each name actually auto-linked."""
    text = text or ''
    already_linked = linked_entity_ids(text)

    candidates = [
        (eid, data['name'], data['kind'])
        for eid, data in index.items()
        if eid != exclude_entity_id and eid not in already_linked and len(data['name']) >= min_name_length
    ]
    # Longest names first, so e.g. "Renaer Neverember" is linked as a whole
    # before a shorter candidate name that happens to be a substring of it
    # could match inside what's about to become that link's display text.
    candidates.sort(key=lambda t: -len(t[1]))

    linked = []
    for entity_id, name, kind in candidates:
        link_spans = [(m.start(), m.end()) for m in LINK_SPAN_RE.finditer(text)]
        for m in re.finditer(r'\b' + re.escape(name) + r'\b', text):
            if any(start <= m.start() < end for start, end in link_spans):
                continue  # inside an existing (or just-inserted) link -- skip
            token = f'[{kind}:{entity_id}|{name}]'
            text = text[: m.start()] + token + text[m.end() :]
            linked.append((entity_id, name))
            break  # only the first valid occurrence of this name
    return text, linked


def add_missing_entity_tags(text, index, exclude_entity_id=None, min_name_length=4):
    """Add [entity:N|Name] tags for any known entity names that appear as
    plain text in `text` without an existing wiki link.

    Skips the entity identified by `exclude_entity_id` so the entity being
    updated is never re-linked into its own synopsis.

    Returns (modified_text, linked) where `linked` is a list of
    ``(entity_id, kind, name)`` for each entity that was linked.
    """
    text, linked = auto_link_entry(text, index, exclude_entity_id=exclude_entity_id, min_name_length=min_name_length)
    details = [(eid, index[eid]['kind'], name) for eid, name in linked]
    return text, details
