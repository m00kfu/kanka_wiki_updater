"""Shared synopsis generation logic for the sync engine and review web UI.

This module extracts the common LLM-driven synopsis update pipeline so that
the sync engine (batch journal processing) and the review web UI
(one-off regeneration) call a single implementation instead of duplicating
prompt construction, response parsing, paragraph normalisation, and change
comparison logic.

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
The review web UI imports these directly via ``from kanka_wiki_updater.sync.synopsis_generator import ...``:

* ``_annotate_journals(text, journal_id)`` — wraps content blocks in
  ``[journal:N]...[/journal]`` tags so the LLM can attribute facts back to
  their source session note.
"""

import difflib
import re
import sys
import unicodedata

from kanka_wiki_updater.core import config
from kanka_wiki_updater.llm.client import LLMError, chat_json
from kanka_wiki_updater.core.mentions import (
    JOURNAL_LINK_RE,
    normalize_text,
    strip_html,
    strip_journal_links,
)
from kanka_wiki_updater.core.prompts import (
    NEW_ENTITY_SYSTEM_PROMPT,  # noqa: F401 -- re-exported for sync_pipeline consumers
    NEW_ENTITY_USER_PROMPT_TEMPLATE,  # noqa: F401 -- re-exported for sync_pipeline consumers
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
)

# ---------------------------------------------------------------------------
# Journal text annotation
# ---------------------------------------------------------------------------

_JOURNAL_REF_OPEN = '[journal:'
_JOURNAL_REF_CLOSE_RE = re.compile(r'\[/journal\]\s*$')


def _build_journal_url(journal_id):
    """Build a web URL to view the source journal entry in Kanka's UI."""
    return f'https://app.kanka.io/campaigns/{config.KANKA_CAMPAIGN_ID}/journal/{journal_id}'


def _annotate_journals(text, journal_id, display_name=None):
    """Insert [journal:N|Name] markers at paragraph boundaries so the LLM can
    attribute facts back to their source session note.

    Each content block in *text* gets wrapped with opening/closing tags so
    rule-4 of the prompt preserves them verbatim in the LLM output.

    Parameters
    ----------
    text : str
        The journal entry text (HTML already stripped, old journal links removed).
    journal_id : str or int
        The Kanka entity_id for the [journal:N] tag.
    display_name : str, optional
        Human-readable session name.  When present tags are written as
        ``[journal:N|Name]`` so the LLM sees and echoes back the full format,
        which means existing synopses keep their citation tags across regenerations.
    """
    if not journal_id or not text:
        return text
    # Wrap the name in HTML <i> tags so it renders as *italic* in Kanka.
    name_part = f'|<i>{display_name}</i>' if display_name else ''
    tag_open = f'{_JOURNAL_REF_OPEN}{journal_id}{name_part}]'
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
            result.append(f'{tag_open}{part}')
            first_block = False
        elif not is_list_item:
            # Close the previous block, open a new one — this gives each
            # paragraph its own [journal:N|Name]…[/journal] wrapper so the
            # LLM can visually distinguish which paragraphs carry new info.
            result.append(f'[/journal]\n{tag_open}')
            result.append(part)
        else:
            result.append(part)
    return ''.join(result) + '[/journal]'


# ---------------------------------------------------------------------------
# Entity index helpers (shared by sync_pipeline and review_web)
# ---------------------------------------------------------------------------


def _to_dict(obj):
    """Normalize a dict or SimpleNamespace-like object to a plain dict."""
    if isinstance(obj, dict):
        return obj
    try:
        return dict(vars(obj))
    except TypeError:
        # Fallback for objects that don't support vars() — return as-is.
        return obj  # type: ignore[return-value]


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
            d = _to_dict(row)
            entry_text = d.get('entry', '') or ''
            rels = d.get('relations', []) or []
            name = (d.get('name') or '<UNKNOWN>').strip()
            index[d['entity_id']] = {
                'kind': kind,
                'local_id': d['id'],  # internal DB ID for API calls
                'entity_id': d['entity_id'],  # public wiki page number for [journal:N] tags
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


def _build_prompts(entity_id, entity, journal, index, display_name_map=None):
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
    # Use the public-facing wiki page number (entity_id) for [journal:N] tags
    # so citations point to the source journal's shareable URL, not the internal DB ID.
    _journal_ref = str(journal.get('entity_id') or journal.get('id') or '')
    raw_name = journal.get('name') or ''
    # Kanka can return empty strings, whitespace-only values, or (rarely)
    # large chunks of the entry body as "name".  Fall back to a short session
    # reference when the name looks suspicious.
    if not raw_name.strip() or len(raw_name) > 120:
        display_name = f'Session {_journal_ref}'
    else:
        display_name = raw_name
    session_text = _annotate_journals(clean_raw_text, _journal_ref, display_name=display_name)

    # Build a map so post-processing can re-inject <i> tags if the LLM
    # strips them from its response.
    if display_name_map is not None:
        display_name_map[_journal_ref] = display_name

    user_prompt = USER_PROMPT_TEMPLATE.format(
        name=entity['name'],
        entity_kind=entity['kind'],
        current_entry=strip_html(entity['entry']) or '(no synopsis yet)',
        journal_name=journal.get('name') or 'Session note',
        journal_date=journal.get('date') or journal.get('created_at', '') or '',
        session_text=session_text,
    )
    return session_text, user_prompt


# Matches [journal:N|Name] at the start of a paragraph.  Name may contain
# nested Kanka references like [location:...|...] so we can't use [^]]*.
# Instead, find the opening marker then scan for the balanced closing ].
def _parse_journal_tag_open(s):
    """If *s* starts with ``[journal:N|Name]``, return ``(id, full_match)``,
    else ``(None, None)``.  Handles nested brackets inside *Name*.
    """
    if not s.startswith('[journal:'):
        return None, None
    # skip past '[journal:'
    rest = s[len('[journal:'):]  # e.g. '123|Some text [loc:...|...] more'
    # find the pipe separating ID from name
    idx = rest.find('|')
    if idx < 0:
        # bare [journal:N] with no name — still valid
        journal_id = rest.rstrip(']')
        if not journal_id.isdigit():
            return None, None
        full_match = '[journal:' + journal_id + ']'
        return journal_id, full_match
    journal_id = rest[:idx]
    if not journal_id.isdigit():
        return None, None
    after_pipe = rest[idx + 1:]  # e.g. 'Name [loc:...|...] more'
    # find the matching closing ] by counting nested brackets
    depth = 0
    for i, ch in enumerate(after_pipe):
        if ch == '[':
            depth += 1
        elif ch == ']':
            if depth == 0:
                full_match = '[journal:' + rest[:idx] + '|' + after_pipe[:i] + ']'
                return journal_id, full_match
            else:
                depth -= 1
    return None, None


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


def _inject_journal_italics(text, display_name_map):
    """Ensure [journal:N|Name] tags use <i> markup around the display name.

    The prompt wraps names in ``<i>Name</i>``, but the LLM may strip those
    HTML tags when echoing back. This walks through paragraphs and injects
    ``<i>``/``</i>`` into any journal tag whose ID is in *display_name_map*
    and which currently lacks them.

    Existing synopses with [journal:N|OldName] from previous sessions that
    don't have <i> tags are left alone (their IDs won't be in the map).
    """
    if not text or not display_name_map:
        return text

    paragraphs = text.split('\n\n')
    result = []
    for para in paragraphs:
        stripped = para.lstrip()
        prefix_spaces = para[: len(para) - len(stripped)]  # leading whitespace
        if not stripped.startswith('[journal:'):
            result.append(para)
            continue
        # Use the same balanced-bracket logic as _parse_journal_tag_open
        rest = stripped[len('[journal:'):]
        pipe_idx = rest.find('|')
        if pipe_idx < 0:
            # bare [journal:N] — no name to italicize
            result.append(para)
            continue
        journal_id = rest[:pipe_idx]
        after_pipe = rest[pipe_idx + 1:]
        # Find the matching closing ] by counting nested brackets
        depth = 0
        close_idx = -1
        for i, ch in enumerate(after_pipe):
            if ch == '[':
                depth += 1
            elif ch == ']':
                if depth == 0:
                    close_idx = i
                    break
                else:
                    depth -= 1
        if close_idx < 0:
            result.append(para)
            continue
        name = after_pipe[:close_idx].strip()
        full_tag_open = '[journal:' + rest[:pipe_idx + len(after_pipe[:close_idx]) + 1]
        # Check if already has <i> tags around the name
        expected_with_i = f'<i>{name}</i>'
        if expected_with_i in stripped:
            result.append(para)  # already formatted
            continue
        # Look up canonical display name for this journal ID
        jid_str = journal_id.strip()
        if jid_str not in display_name_map or display_name_map[jid_str] != name:
            result.append(para)  # different session — leave as-is
            continue
        # Inject <i> tags: rebuild tag as [journal:N|<i>Name</i>]
        # The closing ] is at position close_idx within after_pipe,
        # which corresponds to position (len('[journal:') + pipe_idx
        # + 1 + close_idx) in stripped.
        bracket_end = len('[journal:') + pipe_idx + 1 + close_idx
        new_para = (
            prefix_spaces
            + '[journal:'
            + rest[:pipe_idx]
            + '|<i>'
            + name
            + '</i>]'
            + stripped[bracket_end + 1:]
        )
        result.append(new_para)

    return '\n\n'.join(result)


_JOURNAL_TAG_CLOSE_RE = re.compile(
    r'(\[/journal\]|</i>\])'  # closing bracket (bare or </i>]</i>)
    r'\s*'
    r'(?=[^\s])',              # only when followed by non-whitespace
    re.DOTALL,
)


def _ensure_trailing_space_after_journal_tags(text):
    """Ensure exactly one space after every journal citation tag.

    Normalises ``[journal:N|<i>Name</i>]New content`` →
    ``[journal:N|<i>Name</i>] New content`` — inserting a single space when
    needed, collapsing any existing whitespace to exactly one.

    Only touches journal citation tags; other link types like
    ``[location:42|Waterdeep]`` are left untouched.
    """
    if not text:
        return text
    return _JOURNAL_TAG_CLOSE_RE.sub(r'\1 ', text)


def _deduplicate_journal_tags(text):
    """Collapse duplicate [journal:N|...] tags in the LLM output.

    The prompt annotates *every* paragraph with [journal:N|Name]…[/journal],
    so the LLM often echoes back multiple identical tags across paragraphs.
    This walks through contiguous blocks of tagged paragraphs and keeps only
    the first tag per block.  Old untagged content between blocks resets the
    context, so a later re-appearance of the same session ID (e.g., new info
    mixed into old content) is preserved.

    Existing synopses may already contain [journal:N|OldName] from previous
    sessions; those are preserved because they appear in their own block.
    """
    if not text:
        return text

    paragraphs = text.split('\n\n')
    # Maps a block's starting index to the journal ID of its first tag.
    block_journal_id: int | None = None
    result = []

    for para in paragraphs:
        stripped = para.lstrip()
        journal_id, full_tag = _parse_journal_tag_open(stripped)
        if journal_id is not None:
            # journal_id and full_tag already set by _parse_journal_tag_open
            rest = stripped[len(full_tag):]
            prefix = para[: len(para) - len(stripped)]  # leading whitespace
            if block_journal_id == journal_id:
                # Duplicate tag within the same contiguous tagged block.
                clean = _JOURNAL_REF_CLOSE_RE.sub('', rest).rstrip()
                result.append(f'{prefix}{clean}')
            else:
                block_journal_id = journal_id
                result.append(para)
        else:
            block_journal_id = None  # untagged / closing paragraph resets the block
            result.append(para)

    return '\n\n'.join(result)





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
    display_name_map: dict[str, str] = {}
    _session_text, user_prompt = _build_prompts(
        entity_id, entity, journal, index, display_name_map=display_name_map,
    )
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
    # The LLM handles all [journal:N|...] tag insertion and preservation via
    # its system-prompt rules (prompts.py Rule 7). Pass through as-is.
    proposed_text = _normalize_proposed(raw_proposed)
    proposed_text = _deduplicate_journal_tags(proposed_text)
    # Insurance: re-inject <i> tags if the LLM stripped them from its response.
    proposed_text = _inject_journal_italics(proposed_text, display_name_map)
    # Ensure exactly one space after every journal citation tag (catches both
    # bare ] from deduplication and </i>] from italic injection).
    proposed_text = _ensure_trailing_space_after_journal_tags(proposed_text)

    # Detect when the LLM output is significantly shorter than the input,
    # which often means information was lost (summarized/condensed instead
    # of preserved). Flag it so review.py can warn the human reviewer.
    _prev_stripped = JOURNAL_LINK_RE.sub('', entity.get('entry', '') or '')
    previous_text = _prev_stripped if _prev_stripped.strip() else entity.get('entry', '')

    _info_loss_threshold = 0.65  # flag if new text < 65% of old text length
    is_potentially_truncated = len(proposed_text) < _info_loss_threshold * len(previous_text)

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


# ---------------------------------------------------------------------------
# Regeneration (used by review_web and future TUI)
# ---------------------------------------------------------------------------


def regenerate_proposal(client, proposal, force=False):
    """Re-run a truncated update proposal through the LLM with higher token limits.

    Fetches fresh data from Kanka (source journal + current entity state),
    then calls ``build_synopsis_proposal()`` with 2× max_tokens.

    Parameters
    ----------
    client : KankaClient
        Authenticated API client.
    proposal : dict
        A pending-change entry (must be 'update' type, must have _journal_id
        and entity_local_id).
    force : bool
        If True, return the result even when no meaningful change was detected.

    Returns
    -------
    dict
        Success:  {'ok': True, 'proposed_entry': str, 'change_summary': str,
                   'uncertain': list, 'truncated': bool}
        Failure:  {'ok': False, 'error': str}
    """
    # --- Validate proposal type -------------------------------------------
    if proposal.get('proposal_type') != 'update':
        return {'ok': False, 'error': 'Only update proposals can be regenerated'}

    entity_id = proposal.get('entity_id')
    journal_id = proposal.get('_journal_id')
    local_id = proposal.get('entity_local_id')

    if not entity_id:
        return {
            'ok': False,
            'error': (
                'This proposal lacks the data needed to regenerate. '
                'Re-run sync_pipeline for fresh proposals.'
            ),
        }

    if not journal_id:
        return {
            'ok': False,
            'error': (
                'This proposal lacks both _journal_id and source_journal — '
                'cannot locate the original session.'
            ),
        }

    # --- Fetch source journal ---------------------------------------------
    try:
        src_journal = client.get_journal(journal_id)
    except Exception as api_err:
        return {'ok': False, 'error': f'Cannot fetch journal from Kanka: {api_err}'}

    if not src_journal:
        return {'ok': False, 'error': 'Source journal not found.'}

    # --- Fetch fresh entity data (may have changed since original sync) ----
    try:
        kind_param = f'{proposal["entity_kind"]}s'
        entity_raw = getattr(client, f'get_{kind_param}')()
    except Exception as api_err:
        return {'ok': False, 'error': f'Cannot contact Kanka to fetch entities: {api_err}'}

    # Find current entity by local_id (internal DB ID)
    entity_data = next(
        (_to_dict(e) for e in entity_raw if _to_dict(e).get('id') == local_id),
        None,
    )
    if not entity_data:
        return {'ok': False, 'error': 'Entity not found.'}

    # --- Build entity dict expected by build_synopsis_proposal -------------
    entity = {
        'name': proposal['entity_name'],
        'kind': proposal['entity_kind'],
        'entry': entity_data.get('entry') or '',
        'local_id': local_id,
        'entity_id': entity_data.get('entity_id'),
    }

    # --- Compute 2× max_tokens for regeneration ---------------------------
    regen_max = (
        config.LLM_MAX_TOKENS * 2
        if config.LLM_PROVIDER != 'gemini'
        else config.GEMINI_MAX_TOKENS * 2
    )

    # --- Build entity index for relation resolution ------------------------
    idx = build_entity_index(client)

    # --- Call the synopsis generator --------------------------------------
    result_proposal = build_synopsis_proposal(
        int(entity_id), entity, src_journal, idx, max_tokens=regen_max,
    )

    # LLM connection/call error — surface the real message
    if isinstance(result_proposal, dict) and result_proposal.get('_llm_error'):
        return {'ok': False, 'error': f'LLM call failed: {result_proposal["_llm_error"]}'}

    # No meaningful change — either error or force through
    if result_proposal is None:
        if not force:
            return {
                'ok': False,
                'error': 'LLM returned no meaningful change (identical to current).',
            }
        # Forced regeneration with identical output: build minimal proposal
        return {
            'ok': True,
            'proposed_entry': proposal.get('proposed_entry', entity_data.get('entry') or ''),
            'change_summary': '(forced regeneration - no meaningful change)',
            'uncertain': [],
            'truncated': False,
        }

    # --- Return success dict -----------------------------------------------
    return {
        'ok': True,
        'proposed_entry': result_proposal['proposed_entry'],
        'change_summary': result_proposal.get('change_summary', ''),
        'uncertain': result_proposal.get('uncertain', []),
        'truncated': False,  # regeneration resets truncated flag
    }
