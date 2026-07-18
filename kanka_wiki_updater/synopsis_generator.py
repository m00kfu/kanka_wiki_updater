"""Shared synopsis generation logic for sync_pipeline and review_web.

This module extracts the common LLM-driven synopsis update pipeline so that
both ``sync_pipeline`` (batch journal processing) and ``review_web``
(one-off regeneration) call a single implementation instead of duplicating
prompt construction, response parsing, paragraph normalisation, journal-link
injection, and change comparison logic.

Public API
----------
* ``propose_update(entity_id, entity, journal, index)`` — thin wrapper that
  calls ``build_synopsis_proposal()`` with the same parameters as before.
  Keeps sync_pipeline's call site unchanged.
* ``build_entity_index(client)`` — one-pass index of all entities keyed by
  entity_id (characters, locations, organizations, creatures).
* ``relation_summary(relations, index)`` — human-readable summary for prompts.

Internals shared with review_web
---------------------------------
review_web imports these directly via ``from kanka_wiki_updater.synopsis_generator import ...``:

* ``_annotate_journals(text, journal_id)`` — wraps content blocks in
  ``[journal:N]...[/journal]`` tags so the LLM can attribute facts back to
  their source session note.
"""

import difflib
import re
import sys
import unicodedata

from kanka_wiki_updater import config
from kanka_wiki_updater.llm_client import LLMError, chat_json
from kanka_wiki_updater.mentions import (
    JOURNAL_LINK_RE,
    normalize_text,
    strip_html,
    strip_journal_links,
)
from kanka_wiki_updater.prompts import (
    NEW_ENTITY_SYSTEM_PROMPT,  # noqa: F401 -- re-exported for sync_pipeline consumers
    NEW_ENTITY_USER_PROMPT_TEMPLATE,  # noqa: F401 -- re-exported for sync_pipeline consumers
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

# ---------------------------------------------------------------------------
# Journal text annotation
# ---------------------------------------------------------------------------

_JOURNAL_REF_OPEN = '[journal:'
_JOURNAL_REF_CLOSE = '/journal]'


def _build_journal_url(journal_id):
    """Build a web URL to view the source journal entry in Kanka's UI."""
    return f'https://app.kanka.io/campaigns/{config.KANKA_CAMPAIGN_ID}/journal/{journal_id}'


def _annotate_journals(text, journal_id):
    """Insert [journal:N] markers at paragraph boundaries so the LLM can
    attribute facts back to their source session note.

    Each content block in *text* gets wrapped with opening/closing tags so
    rule-4 of the prompt preserves them verbatim in the LLM output.
    """
    if not journal_id or not text:
        return text
    parts = re.split(r'(\n+)', text)
    result = []
    first_block = True
    for part in parts:
        stripped = part.strip()
        if not stripped:
            result.append(part)
            continue
        is_list_item = bool(re.match(r'^\s*[-*\u2022]|\d+\.\s', part))
        if first_block and not is_list_item:
            result.append(f'{_JOURNAL_REF_OPEN}{journal_id}]{part}')
            first_block = False
        elif not is_list_item:
            result.append(f'{_JOURNAL_REF_CLOSE}\n{_JOURNAL_REF_OPEN}{journal_id}]')
            result.append(part)
        else:
            result.append(part)
    return ''.join(result) + _JOURNAL_REF_CLOSE


# ---------------------------------------------------------------------------
# Entity index helpers (shared by sync_pipeline and review_web)
# ---------------------------------------------------------------------------


def build_entity_index(client):
    """One pass over characters + locations + organizations + creatures, keyed by
    entity_id (the cross-entity-type id used by relations and mentions).
    """
    index = {}
    for kind, get_fn in (
        ('character', client.get_characters),
        ('location', client.get_locations),
        ('organization', client.get_organizations),
        ('creature', client.get_creatures),
    ):
        try:
            rows = get_fn()
        except Exception as e:
            print(f'  [synopsis_generator] build_entity_index: {kind}: ERROR — {e}', file=sys.stderr)
            continue
        for row in rows:
            entry_text = row.get('entry', '') or ''
            rels = row.get('relations', []) or []
            name = (row.get('name') or '<UNKNOWN>').strip()
            index[row['entity_id']] = {
                'kind': kind,
                'local_id': row['id'],  # internal DB ID for API calls
                'entity_id': row['entity_id'],  # public wiki page number for [journal:N] tags
                'name': name,
                'entry': entry_text,
                'relations': list(rels),
            }
    return index


def relation_summary(relations, index):
    """Build a human-readable summary of an entity's relations for use in LLM prompts."""
    if not relations:
        return '(none on record)'
    lines = []
    for rel in relations:
        target_id = rel.get('target_id')
        other_id = target_id if target_id and target_id in index else rel.get('owner_id')
        other = index.get(other_id)
        name = other['name'] if other else f'entity #{other_id}'
        rel_name = rel.get('relation', '')
        attitude = rel.get('attitude')
        lines.append(f'- {rel_name} -> {name} (attitude: {attitude})')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Core synopsis proposal builder
# ---------------------------------------------------------------------------


def _build_prompts(entity_id, entity, journal, index):
    """Build annotated session text and formatted user prompt for the LLM.

    Returns ``(session_text, user_prompt)`` or ``(None, None)`` if the journal
    entry is empty after stripping HTML.
    """
    # Handle both dict and SimpleNamespace (from test mocks).
    if not isinstance(journal, dict):
        journal = dict(vars(journal))

    raw_text = strip_html(journal.get('entry', '') or '')
    if not raw_text.strip():
        return None, None

    # Strip old journal links before annotating — prevents corruption when the
    # old entry already has [journal:N|...] tags from previous sessions.
    clean_raw_text = JOURNAL_LINK_RE.sub('', raw_text) if raw_text else raw_text
    session_text = _annotate_journals(clean_raw_text, str(journal.get('id') or ''))

    user_prompt = USER_PROMPT_TEMPLATE.format(
        name=entity['name'],
        entity_kind=entity['kind'],
        current_entry=strip_html(entity['entry']) or '(no synopsis yet)',
        journal_name=journal.get('name') or 'Session note',
        journal_date=journal.get('date') or journal.get('created_at', '') or '',
        journal_id=str(journal.get('id') or ''),
        session_text=session_text,
    )
    return session_text, user_prompt


def _normalize_proposed(raw_proposed):
    """Collapse single newlines to spaces while preserving paragraph breaks.

    The LLM echoes ``\\n`` from ``<br>``-converted prompt text; collapse those
    to spaces while keeping double-newline boundaries intact so synopsis
    paragraphs are not lost.
    """
    if not raw_proposed:
        return ''
    _NBSP = '\x00\x01'  # no-break-paragraph placeholder
    proposed_text = raw_proposed.replace('\n\n', _NBSP).replace('\n', ' ')
    proposed_text = re.sub(r' +', ' ', proposed_text)
    proposed_text = proposed_text.replace(_NBSP, '\n\n')

    # Normalize whitespace within paragraphs but preserve \\n\\n paragraph breaks.
    parts = proposed_text.split('\n\n')
    return '\n\n'.join(' '.join(p.split()) for p in parts if p.strip())


def _inject_journal_links(entity_id, result, raw_proposed, entity_entry):
    """Inject journal attribution links into the LLM output when new information
    is detected.

    Hybrid approach: use LLM-provided ``new_paragraph_indices`` if available,
    otherwise fall back to diff-based detection against old paragraphs.

    Returns ``(proposed_text, did_inject)``.
    """
    _is_new_info = result.get('_is_new_info') is True
    proposed_paras = [p.strip() for p in raw_proposed.split('\n\n') if p.strip()]

    # Strip old journal links from the previous entry so paragraph comparison
    # isn't affected by pre-existing [journal:N|...] tags.
    _prev_stripped = JOURNAL_LINK_RE.sub('', entity_entry or '')

    if not _is_new_info:
        return raw_proposed, False

    # Extract the source journal entry ID from result (set by caller).
    # Use it to build [journal:{source_journal_id}|...] attribution links.
    src_journal_id = result.get('_src_journal_id') or str(entity_id)
    journal_id = src_journal_id
    # Extract the source journal name from result (set by caller)
    jn_clean = (result.get('_journal_name') or '').replace('|', '').replace(']', '')
    _journal_prefix = f'[journal:{journal_id}|{jn_clean}]'

    old_paras_raw = entity_entry or ''
    old_paras = [strip_html(p) for p in old_paras_raw.split('\n\n') if p.strip()]

    # When there are no old paragraphs (e.g. new entity), append at end.
    if not old_paras:
        stripped = raw_proposed.strip()
        proposed_text = f'{stripped} {_journal_prefix}' if stripped else _journal_prefix
        return proposed_text, True

    _llm_inserts: set[int] | None = None
    llm_indices = result.get('new_paragraph_indices', None)
    if isinstance(llm_indices, list) and len(llm_indices) > 0:
        valid = [i for i in llm_indices if isinstance(i, int) and 0 <= i < len(proposed_paras)]
        _llm_inserts = set(valid) if valid else None

    # Guard: even when LLM provides indices, filter out paragraphs that
    # already have journal tags. The LLM may rephrase old tagged content
    # and still flag it as "new info" — we must not replace the existing
    # tag with a new one. Also check if untagged paragraphs are just
    # rephrasings of old tagged ones — preserve the original tag.
    preserved_old_tags: dict[int, str] = {}  # index -> old journal link to preserve
    _all_indices_handled_by_old_tags = False  # True when every LLM index was already-tagged or fuzzy-matched
    if _llm_inserts:
        filtered = set()
        for i in _llm_inserts:
            para_text = proposed_paras[i] if i < len(proposed_paras) else ''
            already_tagged = bool(JOURNAL_LINK_RE.search(para_text))
            if already_tagged:
                # Already has a tag — handled, but don't inject.
                _all_indices_handled_by_old_tags = True
            elif old_paras:
                # Check if this paragraph is just a rephrasing of an old
                # tagged paragraph — preserve the old tag instead of
                # injecting a new one.
                para_stripped = strip_journal_links(strip_html(para_text))
                for _j, old in enumerate(old_paras):
                    if JOURNAL_LINK_RE.search(old):
                        old_stripped = strip_journal_links(old)
                        if difflib.SequenceMatcher(None, para_stripped, old_stripped).ratio() > 0.5:
                            preserved_old_tags[i] = JOURNAL_LINK_RE.search(old).group(0)
                            filtered.add(i)
                            break
                else:
                    # No fuzzy match found — inject fresh journal prefix.
                    filtered.add(i)
            else:
                # No old paragraphs to compare against, no existing tag — inject.
                filtered.add(i)
        _llm_inserts = filtered if filtered else None

    # Collapse consecutive LLM indices so only the first paragraph in each
    # contiguous run gets a journal tag — subsequent paragraphs are just
    # continuation of that same new-content block and should stay untagged.
    if _llm_inserts and len(_llm_inserts) > 1:
        sorted_runs = sorted(_llm_inserts)
        collapsed: set[int] = {sorted_runs[0]}
        prev_idx = sorted_runs[0]
        for idx in sorted_runs[1:]:
            if idx > prev_idx + 1:
                collapsed.add(idx)
            prev_idx = idx
        _llm_inserts = collapsed

    _diff_insert_at: int | None = None
    if _llm_inserts is None:
        # Fallback: diff-based detection when LLM didn't provide indices.
        for i, para in enumerate(proposed_paras):
            para_stripped = strip_journal_links(strip_html(para))
            if not para_stripped:
                continue
            positional_match = False
            if old_paras and i < len(old_paras):
                positional_match = (
                    difflib.SequenceMatcher(None, para_stripped, strip_journal_links(old_paras[i])).ratio() > 0.5
                )
            if not positional_match:
                fuzzy_match = any(
                    difflib.SequenceMatcher(None, para_stripped, strip_journal_links(old)).ratio() > 0.5
                    for old in old_paras
                )

                if not fuzzy_match:
                    _diff_insert_at = i
                    break

    def _inject_at(paras, idx, prefix=None):
        if prefix is None:
            prefix = _journal_prefix
        post_para = strip_journal_links(strip_html(paras[idx]))
        pre_paras = '\n\n'.join(paras[:idx])
        pre_text = f'{pre_paras}\n\n' if pre_paras else ''
        new_text = f'{pre_text}{prefix} {post_para}'
        rest_idx = idx + 1
        remaining = '\n\n'.join(paras[rest_idx:]) if rest_idx < len(paras) else ''
        return f'{new_text}\n\n{remaining}'.rstrip() if remaining else new_text

    # Insert the journal link before each paragraph that has new info.
    _did_inject = False
    if _llm_inserts is not None and len(_llm_inserts) >= 1:
        for idx in sorted(_llm_inserts, reverse=True):
            if proposed_paras and idx < len(proposed_paras):
                prefix = preserved_old_tags.get(idx)
                raw_proposed = _inject_at(proposed_paras, idx, prefix=prefix)
                # Recompute paragraphs after insertion so subsequent indices
                # (which are at higher positions due to reverse sort) stay valid.
                proposed_paras = [p.strip() for p in raw_proposed.split('\n\n') if p.strip()]
                _did_inject = True
    elif _diff_insert_at is not None:
        raw_proposed = _inject_at(proposed_paras, _diff_insert_at)
        _did_inject = True

    # Only fall back to appending journal prefix when LLM provided no indices
    # AND diff-based detection also found nothing — means all text was already-tagged.
    if (
        not _did_inject
        and proposed_paras
        and _llm_inserts is None
        and _diff_insert_at is None
        and not _all_indices_handled_by_old_tags
    ):
        stripped = raw_proposed.strip()
        raw_proposed = f'{stripped} {_journal_prefix}'

    return raw_proposed, _did_inject


def build_synopsis_proposal(entity_id, entity, journal, index, max_tokens=None):
    """Build a synopsis update proposal for an entity based on a single journal entry.

    This is the shared core used by both ``sync_pipeline.propose_update()`` and
    ``review_web.regenerate_proposal()``.

    Parameters
    ----------
    entity_id : int or str
        The Kanka entity_id (cross-entity-type id).
    entity : dict
        Must have keys ``name``, ``kind``, ``entry``, ``local_id``.
    journal : dict
        A journal entry from the Kanka API with at least ``id``, ``name``, ``entry``.
    index : dict
        The full entity index (for relation resolution).
    max_tokens : int, optional
        Override for LLM max tokens.  If omitted the default config value is used.

    Returns
    -------
    dict or None
        A proposal dict ready to be queued in ``pending_changes.json``, or
        ``None`` when no meaningful change was detected.
    """
    _session_text, user_prompt = _build_prompts(entity_id, entity, journal, index)
    if user_prompt is None:
        return None

    # Normalize to dict for downstream .get() calls (tests use SimpleNamespace).
    if not isinstance(journal, dict):
        journal = dict(vars(journal))

    try:
        result = chat_json(SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens)
    except LLMError as e:
        print(f'  ! LLM error for {entity["name"]}: {e}', file=sys.stderr)
        return {'_llm_error': str(e)}
    except Exception as e:
        # Catch broadly — a bad response from the model, network hiccup, etc.
        print(f'  ! Error calling LLM for {entity["name"]}: {e}', file=sys.stderr)
        return {'_llm_error': str(e)}

    raw_proposed = result.get('updated_entry', '') or entity['entry']
    # Strip [journal:N] attribution links from LLM output — they were injected
    # into the session text so the model could attribute facts, but they must not
    # leak into the final synopsis.  _inject_journal_links() adds them back cleanly.
    raw_proposed = JOURNAL_LINK_RE.sub('', raw_proposed)
    proposed_text = _normalize_proposed(raw_proposed)

    # Detect when the LLM output is significantly shorter than the input,
    # which often means information was lost (summarized/condensed instead
    # of preserved). Flag it so review.py can warn the human reviewer.
    _prev_stripped = JOURNAL_LINK_RE.sub('', entity.get('entry', '') or '')
    previous_text = _prev_stripped if _prev_stripped.strip() else entity.get('entry', '')

    _info_loss_threshold = 0.65  # flag if new text < 65% of old text length
    is_potentially_truncated = len(proposed_text) < _info_loss_threshold * len(previous_text)

    # Inject journal attribution links when LLM flags new information.
    result['_journal_name'] = (journal.get('name') or '').replace('|', '').replace(']', '')
    # Use the public-facing wiki page number for [journal:N] tags.
    _journal_id = journal.get('entity_id') or journal.get('id')
    result['_src_journal_id'] = str(_journal_id)
    proposed_text, _did_inject = _inject_journal_links(entity_id, result, raw_proposed, entity['entry'])

    no_text_change = normalize_text(proposed_text) == normalize_text(previous_text)
    if no_text_change:
        return None  # model decided nothing meaningfully changed

    result['truncated'] = result.get('truncated', False) or '[TRUNCATED:' in (result.get('change_summary', '') or '')
    if is_potentially_truncated and not result['truncated']:
        result['_info_loss_warning'] = True  # internal flag for review.py

    return {
        'proposal_type': 'update',
        'entity_id': entity_id,
        'entity_kind': entity['kind'],
        'entity_local_id': entity['local_id'],
        'entity_name': entity['name'],
        'source_journal': journal.get('name'),
        '_journal_id': journal['id'],
        '_source_journal_url': _build_journal_url(journal['id']),
        'previous_entry': previous_text,
        'proposed_entry': proposed_text,
        'change_summary': result.get('change_summary', ''),
        'relation_changes': [],
        'uncertain': result.get('uncertain', []),
        'truncated': result.get('truncated', False) or '[TRUNCATED:' in (result.get('change_summary', '') or ''),
        '_info_loss_warning': is_potentially_truncated and not result['truncated'],
        'status': 'pending',
    }


# ---------------------------------------------------------------------------
# Public wrapper — sync_pipeline.caller uses this exact signature
# ---------------------------------------------------------------------------


def propose_update(entity_id, entity, journal, index):
    """Thin wrapper for sync_pipeline compatibility.

    Calls ``build_synopsis_proposal()`` with the same parameters as before.
    Keeps existing call sites unchanged while sharing all logic internally.
    """
    return build_synopsis_proposal(entity_id, entity, journal, index)


def _is_known_entity(name: str, known_names: list[str]) -> bool:
    """Return True if *name* is already an existing entity (exact/substring/fuzzy).

    Used to filter false-positive new-entity suggestions from the LLM.  Short
    names (< 4 characters) skip substring and fuzzy checks to avoid matching
    common words against many candidate names.

    Uses unicodedata.NFKD decompose + ASCII ignore for accent-insensitive
    comparison so "Jose" matches "José".
    """
    if not name:
        return False

    # --- exact, case-insensitive match -----------------------------------
    lower_known = {n.lower().strip() for n in known_names}
    if name.lower().strip() in lower_known:
        return True

    SHORT_THRESHOLD = 4  # skip substring/fuzzy for very short names

    def _accent_strip(text: str) -> str:
        return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()

    suggestion_normalized = _accent_strip(name.strip())
    suggestion_lower = suggestion_normalized.lower()

    for raw_known in known_names:
        known_stripped = raw_known.strip()
        known_normalized = _accent_strip(known_stripped)
        known_lower = known_normalized.lower()

        if not known_lower:
            continue

        # --- substring checks --------------------------------------------
        if len(name) >= SHORT_THRESHOLD and known_lower.find(suggestion_lower) != -1:
            return True  # suggestion is a substring of a known name

        if len(known_stripped) >= SHORT_THRESHOLD and suggestion_lower.find(known_lower) != -1:
            return True  # known name is a substring of the suggestion

        # --- fuzzy first-word match --------------------------------------
        if len(name) < SHORT_THRESHOLD:
            continue  # skip for short names to avoid false positives

        sugg_first = suggestion_normalized.split()[0] if suggestion_normalized.split() else ''
        know_first = known_normalized.split()[0] if known_normalized.split() else ''

        if sugg_first and know_first:
            ratio = difflib.SequenceMatcher(None, sugg_first, know_first).ratio()
            if ratio > 0.65:
                return True

    return False
