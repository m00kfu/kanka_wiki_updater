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
TAG_RE = re.compile(r'<[^>]+>')


def strip_html(raw):
    if not raw:
        return ''
    return html.unescape(TAG_RE.sub(' ', raw)).strip()


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


def fuzzy_name_matches(text, names_by_entity_id, threshold=0.84):
    """Catch plain-text mentions of known names that weren't @-linked.
    This is a cheap word-window fuzzy match -- good enough for proper
    nouns, not meant to be perfect. Always double-check the review queue
    rather than trusting this blindly."""
    text_lower = (text or '').lower()
    words = text_lower.split()
    found = set()
    for entity_id, name in names_by_entity_id.items():
        name_lower = name.lower()
        if name_lower in text_lower:
            found.add(entity_id)
            continue
        first_word = name_lower.split()[0] if name_lower.split() else name_lower
        if len(first_word) >= 4:
            for word in words:
                if SequenceMatcher(None, first_word, word.strip('.,!?;:\'"')).ratio() >= threshold:
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
