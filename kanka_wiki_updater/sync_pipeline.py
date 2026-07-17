#!/usr/bin/env python3
"""
Pull new session-note journals from Kanka, ask the local LLM (via LM Studio)
to propose updated synopses and relationship changes for any character or
location they mention, and queue those proposals for human review.

Nothing is written back to Kanka here -- see review.py for that step.

Usage:
    ./kanka_wiki_updater/sync_pipeline.py [--limit N]
    python -m kanka_wiki_updater.sync_pipeline [--limit N]
"""

import argparse
import os
import re
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

_DEBUG = bool(os.environ.get('KANKA_DEBUG'))  # type: ignore[unused-ignore,assignment]


def _debug(*args):
    if _DEBUG:
        print('[DEBUG]', *args, file=sys.stderr)


_MIN_NAME_LENGTH = 4


def _normalize_name(name):
    """Strip accents/diacritics and collapse whitespace for comparison."""
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(name.split())


def _is_known_entity(suggested_name, known_names):
    """Return True if *suggested_name* looks like an existing entity.

    Checks multiple heuristics against every name in *known_names*:
      1. **Exact match** (case-insensitive) on normalized names.
      2. **Substring containment** -- the suggestion is contained within a
         known name or vice-versa (e.g. "Aerendyl" inside "Aerendyl Stonehand").
      3. **Fuzzy match on first words** -- SequenceMatcher ratio ≥ 0.84 when
         both first words are at least 4 characters long.
      4. **Fuzzy match on full names** -- catches cases where neither name is a
         substring of the other but they share significant overlap (e.g.
         "Aerendel Stoneclaw" vs "Lord Aerendyl").

    Single-character suggestions skip all checks to avoid matching every word.
    Very short suggestions (< 4 chars) skip fuzzy checks; exact and substring
    still apply."""
    suggested = (suggested_name or '').strip()
    if not suggested:
        return False

    # Normalize for comparison: strip accents, collapse whitespace.
    norm_suggested = _normalize_name(suggested).lower()
    known_normed = [_normalize_name(n).lower() for n in known_names]

    # Fast exact check -- always applies even to single-char names.
    if norm_suggested in known_normed:
        return True

    # Skip substring and fuzzy checks for very short suggestions (1-3 chars)
    # to avoid matching common words as substrings of many names.
    if len(norm_suggested) < _MIN_NAME_LENGTH:
        return False

    for lower_name in known_normed:
        if not lower_name:
            continue

        # Substring containment (bidirectional).
        if norm_suggested in lower_name or lower_name in norm_suggested:
            return True

        # Fuzzy match on the first word.
        suggested_first = norm_suggested.split()[0] if norm_suggested.split() else norm_suggested
        known_first = lower_name.split()[0] if lower_name.split() else lower_name
        if (
            len(suggested_first) >= _MIN_NAME_LENGTH
            and len(known_first) >= _MIN_NAME_LENGTH
            and SequenceMatcher(None, suggested_first, known_first).ratio() >= 0.84
        ):
            return True

        # Fuzzy match on full names -- catches near-misses where neither name
        # is a substring of the other but they share significant overlap
        # (e.g. "Aerendel Stoneclaw" vs "Lord Aerendyl").
        if SequenceMatcher(None, norm_suggested, lower_name).ratio() >= 0.84:
            return True

    return False


if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from . import config, state
    from .kanka_client import KankaClient
    from .llm_client import chat_json
    from .mentions import (
        JOURNAL_LINK_RE,
        fuzzy_name_matches,
        linked_entity_ids,
        normalize_text,
        strip_html,
        strip_journal_links,
    )
    from .progress import ProgressTracker
    from .prompts import (
        NEW_ENTITY_SYSTEM_PROMPT,
        NEW_ENTITY_USER_PROMPT_TEMPLATE,
        SYSTEM_PROMPT,
        USER_PROMPT_TEMPLATE,
    )
except ImportError:
    from kanka_wiki_updater import config, state
    from kanka_wiki_updater.kanka_client import KankaClient
    from kanka_wiki_updater.llm_client import chat_json
    from kanka_wiki_updater.mentions import (
        JOURNAL_LINK_RE,
        fuzzy_name_matches,
        linked_entity_ids,
        normalize_text,
        strip_html,
        strip_journal_links,
    )
    from kanka_wiki_updater.progress import ProgressTracker
    from kanka_wiki_updater.prompts import (
        NEW_ENTITY_SYSTEM_PROMPT,
        NEW_ENTITY_USER_PROMPT_TEMPLATE,
        SYSTEM_PROMPT,
        USER_PROMPT_TEMPLATE,
    )


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
        is_list_item = bool(re.match(r'^\s*[-*•]|\d+\.\s', part))
        if first_block and not is_list_item:
            result.append(f'{_JOURNAL_REF_OPEN}{journal_id}]{part}')
            first_block = False
        elif not is_list_item:
            result.append(f'{_JOURNAL_REF_CLOSE}\n{_JOURNAL_REF_OPEN}{journal_id}]')
            result.append(part)
        else:
            result.append(part)
    return ''.join(result) + _JOURNAL_REF_CLOSE


def build_entity_index(client):
    """One pass over characters + locations + organizations + creatures, keyed by
    entity_id (the cross-entity-type id used by relations and mentions)."""
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
            _debug(f'  build_entity_index: {kind}: ERROR — {e}')
            continue
        _debug(f'  build_entity_index: {kind}: {len(rows)} items')
        for row in rows:
            entry_text = row.get('entry', '') or ''
            rels = row.get('relations', []) or []
            name = (row.get('name') or '<UNKNOWN>').strip()
            _debug(f'    [{kind}] eid={row["entity_id"]} local_id={row["id"]} name={name!r}')
            index[row['entity_id']] = {
                'kind': kind,
                'local_id': row['id'],
                'name': name,
                'entry': entry_text,
                'relations': list(rels),
            }
    return index


def relation_summary(relations, index):
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


def find_mentioned_entities(journal_entry_raw, index):
    ids = linked_entity_ids(journal_entry_raw)
    names_by_id = {eid: data['name'] for eid, data in index.items()}
    ids |= fuzzy_name_matches(strip_html(journal_entry_raw), names_by_id)
    return [eid for eid in ids if eid in index]


def propose_update(entity_id, entity, journal, index):
    raw_text = strip_html(journal.get('entry', '') or '')
    if not raw_text.strip():
        return None

    # Strip old journal links before annotating — prevents corruption when the
    # old entry already has [journal:N|...] tags from previous sessions.
    # Use a simple sub that preserves whitespace (strip_journal_links collapses it all).
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
    try:
        result = chat_json(SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        # Catch broadly, not just LLMError -- a bad response from the model,
        # a network hiccup, or anything else here should cost us one entity,
        # not the rest of a multi-hour backfill run.
        print(f'  ! LLM error for {entity["name"]}: {e}', file=sys.stderr)
        return None

    raw_proposed = result.get('updated_entry', '') or entity['entry']
    # Preserve paragraph breaks (\n\n) while collapsing single newlines to
    # spaces so the LLM's intended paragraph structure is kept intact.
    _NBSP = '\x00\x01'  # no-break-paragraph placeholder
    proposed_text = raw_proposed.replace('\n\n', _NBSP).replace('\n', ' ')
    proposed_text = re.sub(r' +', ' ', proposed_text)
    proposed_text = proposed_text.replace(_NBSP, '\n\n')

    # Strip old journal links from the previous entry so paragraph comparison
    # isn't affected by pre-existing [journal:N|...] tags.  Use a simple
    # sub that preserves whitespace (strip_journal_links collapses it all).
    _prev_stripped = JOURNAL_LINK_RE.sub('', entity['entry'] or '')
    previous_text = _prev_stripped if _prev_stripped.strip() else entity['entry']

    # Inject journal attribution link when LLM flags new information.
    # Hybrid approach: use LLM-provided new_paragraph_indices if available,
    # otherwise fall back to diff-based detection against old paragraphs.
    _is_new_info = result.get('_is_new_info') is True
    if _is_new_info and entity_id:
        journal_id = journal['entity_id']
        journal_name = (journal.get('name') or '').replace('|', '').replace(']', '')
        _journal_prefix = f'[journal:{journal_id}|{journal_name}]'

        proposed_paras = [p.strip() for p in proposed_text.split('\n\n') if p.strip()]
        # Use the original entry so old paragraphs retain journal links for preservation.
        # The fuzzy comparison strips links from both sides (line ~319), so this is safe.
        old_paras_raw = entity.get('entry', '') or ''
        old_paras = [strip_html(p) for p in old_paras_raw.split('\n\n') if p.strip()]

        # Build set of valid journal IDs from the original entry (for distinguishing
        # stale hallucinated tags from real old tags that should be preserved).
        _JOURNAL_ID_RE = re.compile(r'\[journal:(\d+)')
        _old_tag_ids: set[str] = set()
        for _op in old_paras:
            _m = _JOURNAL_ID_RE.search(_op)
            if _m:
                _old_tag_ids.add(_m.group(1))

        # Determine insertion position(s): LLM indices first, then diff-based fallback.
        llm_indices = result.get('new_paragraph_indices', None)
        _llm_inserts: set[int] | None = None

        if isinstance(llm_indices, list) and len(llm_indices) > 0:
            # Use LLM-provided indices (validate bounds).
            valid = [i for i in llm_indices if isinstance(i, int) and 0 <= i < len(proposed_paras)]
            _llm_inserts = set(valid) if valid else None

        # Guard: even when LLM provides indices, filter out paragraphs that
        # already have journal tags. The LLM may rephrase old tagged content
        # and still flag it as "new info" — we must not replace the existing
        # tag with a new one. Also check if untagged paragraphs are just
        # rephrasings of old tagged ones — preserve the original tag.
        preserved_old_tags: dict[int, str] = {}  # index -> old journal link to preserve
        if _llm_inserts:
            filtered = set()
            for i in _llm_inserts:
                para_text = proposed_paras[i] if i < len(proposed_paras) else ''
                _tag_match = JOURNAL_LINK_RE.search(para_text)
                if _tag_match:
                    # Check whether this tag ID is a valid old tag (rephrased content).
                    _id_match = _JOURNAL_ID_RE.search(para_text)
                    _tag_id = _id_match.group(1) if _id_match else ''
                    if _tag_id in _old_tag_ids:
                        # Re-phrased old tagged paragraph — preserve the existing tag.
                        continue
                    # Hallucinated/echoed stale tag from LLM — let it be replaced.
                    filtered.add(i)
                elif old_paras:
                    # Check if this paragraph is just a rephrasing of an old
                    # tagged paragraph — preserve the old tag instead of
                    # injecting a new one.
                    para_stripped = strip_journal_links(strip_html(para_text))
                    for _j, old in enumerate(old_paras):
                        if JOURNAL_LINK_RE.search(old):
                            old_stripped = strip_journal_links(old)
                            if SequenceMatcher(None, para_stripped, old_stripped).ratio() > 0.5:
                                # Found a match with an old tagged paragraph — keep the old tag.
                                preserved_old_tags[i] = JOURNAL_LINK_RE.search(old).group(0)
                                filtered.add(i)
                                break
                    else:
                        # No fuzzy match found — inject fresh journal prefix.
                        filtered.add(i)
            _llm_inserts = filtered  # keep empty set (all were already-tagged) vs None (LLM gave none)

        _diff_insert_at: int | None = None
        if _llm_inserts is None:
            # Fallback: diff-based detection when LLM didn't provide indices.
            # Compare positionally first (proposed[i] vs old[i]), then against any old paragraph.
            for i, para in enumerate(proposed_paras):
                para_stripped = strip_journal_links(strip_html(para))
                if not para_stripped:
                    continue
                positional_match = False
                if old_paras and i < len(old_paras):
                    old_para_text = strip_journal_links(old_paras[i])
                    positional_match = SequenceMatcher(None, para_stripped, old_para_text).ratio() > 0.5
                if not positional_match:
                    fuzzy_match = any(
                        SequenceMatcher(None, para_stripped, strip_journal_links(old)).ratio() > 0.5
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

        # Insert the journal link before each paragraph that has new info.
        # Process indices in reverse order to avoid shifting earlier positions.
        _insert_indices: list[int] = []
        if _llm_inserts is not None and len(_llm_inserts) >= 1:
            _insert_indices = sorted(_llm_inserts, reverse=True)
        elif _diff_insert_at is not None:
            _insert_indices = [_diff_insert_at]

        _did_inject = False
        for idx in _insert_indices:
            if proposed_paras and idx < len(proposed_paras):
                prefix = preserved_old_tags.get(idx)
                proposed_text = _inject_at(proposed_paras, idx, prefix=prefix)
                # Recompute paragraphs after insertion so subsequent indices
                # (which are at higher positions due to reverse sort) stay valid.
                proposed_paras = [p.strip() for p in proposed_text.split('\n\n') if p.strip()]
                _did_inject = True

        # Only fall back to appending journal prefix when LLM provided no indices
        # AND diff-based detection also found nothing — means all text was already-tagged.
        # When LLM DID provide indices but they were all filtered out (already-tagged),
        # leave the text unchanged instead of injecting a new tag.
        if not _did_inject and proposed_paras and _llm_inserts is None and _diff_insert_at is None:
            stripped = proposed_text.strip()
            proposed_text = f'{stripped} {_journal_prefix}'

    # Detect when the LLM output is significantly shorter than the input,
    # which often means information was lost (summarized/condensed instead
    # of preserved). Flag it so review.py can warn the human reviewer.
    _info_loss_threshold = 0.65  # flag if new text < 65% of old text length
    is_potentially_truncated = len(proposed_text) < _info_loss_threshold * len(previous_text)

    _proposed_journal_links = JOURNAL_LINK_RE.findall(proposed_text)
    _previous_journal_links = JOURNAL_LINK_RE.findall(previous_text or '')
    same_text = normalize_text(proposed_text) == normalize_text(previous_text)
    same_tags = set(_proposed_journal_links) == set(_previous_journal_links)
    no_text_change = same_text and same_tags
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


DATE_RE = re.compile(r'(\d{1,5})-(\d{1,2})-(\d{1,2})')


def journal_sort_key(journal):
    """Chronological sort key based on the journal's in-fiction date.

    Tries, in order:
      1. The free-text `date` field, if it looks like YYYY-MM-DD (what
         Kanka's date picker produces for the default Gregorian calendar).
      2. The structured calendar_year / calendar_month / calendar_day
         fields, for custom calendars where `date` isn't a plain ISO string.
      3. created_at, for journals with no date set at all -- these sort
         after every dated journal rather than disrupting the timeline.
    """
    raw = journal.get('date') or ''
    raw = raw.strip() if isinstance(raw, str) else str(raw).strip() if raw else ''
    match = DATE_RE.match(raw)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return (0, year, month, day, journal.get('created_at', '') or '')

    year = journal.get('calendar_year')
    if year is not None:
        return (
            0,
            int(year),
            journal.get('calendar_month') or 0,
            journal.get('calendar_day') or 0,
            journal.get('created_at', '') or '',
        )

    return (1, 0, 0, 0, journal.get('created_at', '') or '')


def apply_relation_changes_locally(entity_id, relation_changes, index, name_to_id):
    """Keep the in-memory index's relations in sync with what was just
    proposed (but not yet applied to Kanka), so the *next* journal in this
    same run sees the up-to-date picture instead of the stale Kanka copy."""
    entity_data = index[entity_id]
    relations = entity_data['relations']
    for rc in relation_changes:
        target_id = name_to_id.get(rc['target_name'])
        if not target_id:
            continue
        existing = next((r for r in relations if r.get('target_id') == target_id), None)
        action = (rc.get('action') or '').strip().lower()
        if action == 'delete':
            if existing:
                relations.remove(existing)
        else:  # create or update
            if existing:
                existing['relation'] = rc['relation']
                existing['attitude'] = rc.get('attitude')
            else:
                relations.append(
                    {
                        'owner_id': entity_id,
                        'target_id': target_id,
                        'relation': rc['relation'],
                        'attitude': rc.get('attitude'),
                    }
                )


def propose_new_entities(journal, known_names):
    """Ask the LLM once per journal whether it mentions any character or
    location that isn't already a known entity. `known_names` is a set of
    names (any case) to exclude -- callers should add a name to it as soon
    as it's been suggested, so the same backlog run doesn't propose the
    same new entity once per journal that mentions them."""
    session_text = strip_html(journal.get('entry', '') or '')
    if not session_text.strip():
        return []

    user_prompt = NEW_ENTITY_USER_PROMPT_TEMPLATE.format(
        known_names='\n'.join(f'- {n}' for n in sorted(known_names)) or '(none yet)',
        journal_name=journal.get('name') or 'Session note',
        journal_date=journal.get('date') or journal.get('created_at', '') or '',
        session_text=session_text,
    )
    try:
        result = chat_json(NEW_ENTITY_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        print(f'  ! LLM error scanning for new entities: {e}', file=sys.stderr)
        return []

    known_lower = {n.lower() for n in known_names}
    proposals = []
    for candidate in result.get('new_entities', []) or []:
        name = (candidate.get('name') or '').strip()
        if not name or name.lower() in known_lower:
            continue  # exact match -- skip even though the prompt asked for this

        if _is_known_entity(name, known_names):
            print(f'      ! Skipping "{name}" -- matches existing entity (substring/fuzzy)')
            continue

        suggested_type = (candidate.get('suggested_type') or 'character').strip().lower()
        if suggested_type not in ('character', 'location'):
            suggested_type = 'character'

        draft = (candidate.get('draft_entry') or '').strip()
        is_truncated = False
        if draft:
            last = draft.rstrip()[-1:] if len(draft) > 1 else ''
            if last in (',', ':', ';', '(', '['):
                is_truncated = True

        journal_id = getattr(journal, 'id', None) or (journal.get('id') if isinstance(journal, dict) else None)
        proposals.append(
            {
                'proposal_type': 'new_entity',
                'entity_name': name,
                'suggested_type': suggested_type,
                'draft_entry': candidate.get('draft_entry', ''),
                'reason': candidate.get('reason', ''),
                'source_journal': getattr(journal, 'name', None),
                '_source_journal_url': _build_journal_url(journal_id) if journal_id else None,
                'truncated': is_truncated,
                'status': 'pending',
            }
        )
        known_lower.add(name.lower())  # don't suggest the same new name twice in one run
    return proposals


def main(limit=None):
    client = KankaClient()
    print('Building character/location index...')
    index = build_entity_index(client)
    name_to_id = {data['name']: eid for eid, data in index.items()}

    last_sync = state.get_last_sync()
    print(f'Fetching journals since: {last_sync or "(beginning -- full history)"}')
    journals = client.get_journals(since=last_sync, journal_type=config.SESSION_JOURNAL_TYPE or None)
    print(f'Fetched {len(journals)} journal(s) from Kanka.')

    processed_ids = state.get_processed_journal_ids()
    to_process = [j for j in journals if j['id'] not in processed_ids]

    # Oldest first by the journal's in-fiction date, so a character's
    # synopsis builds up in story order rather than whatever order the API
    # happens to return things in (or the order sessions were logged, which
    # isn't always the same as the order they happened in-game).
    to_process.sort(key=journal_sort_key)

    total_new = len(to_process)
    if limit is not None and limit < total_new:
        to_process = to_process[:limit]
        print(
            f'{total_new} journal(s) are new; processing the oldest {len(to_process)} '
            f'this run ({total_new - len(to_process)} will remain queued for a future run).'
        )
    else:
        print(f'{total_new} journal(s) are new; processing oldest-first.')

    known_names = set(name_to_id.keys())
    total_proposals = 0
    total_new_entities = 0

    for i, journal in enumerate(to_process, start=1):
        t0 = time.time()
        mentioned = find_mentioned_entities(journal.get('entry', ''), index)
        new_candidates = propose_new_entities(journal, known_names)

        # Calculate total work units for this journal
        total_units = len(mentioned) + (1 if new_candidates else 0)

        if total_units > 0:
            print(f'      {journal.get("name")}')
            tracker = ProgressTracker(total_units)

            for entity_id in mentioned:
                entity = index[entity_id]
                proposal = propose_update(entity_id, entity, journal, index)
                tracker.mark_done(f'LLM for {entity["name"]}...')
                if proposal:
                    state.append_to_queue([proposal])
                    total_proposals += 1

            # New-entity scanning
            if new_candidates:
                print(
                    f'      + {len(new_candidates)} new entity suggestion(s): '
                    + ', '.join(f'{c["entity_name"]} ({c["suggested_type"]})' for c in new_candidates)
                )
                for candidate in new_candidates:
                    state.append_to_queue([candidate])
                    known_names.add(candidate['entity_name'])
                total_new_entities += len(new_candidates)

            tracker.mark_done('New-entity scan')
            tracker.finish()
        else:
            print(f"  ({i}/{len(to_process)}) '{journal.get('name')}': no entities found")

        # Always mark journal processed regardless of tracker usage
        state.mark_journal_processed(journal['id'], title=journal.get('name'))
        elapsed = time.time() - t0
        mins, secs = divmod(int(elapsed), 60)
        print(f"      ({i}/{len(to_process)}) '{journal.get('name')}' processed in {mins:02d}:{secs:02d}")

    # Only advance the API's "lastSync" cursor once everything fetched this
    # run has actually been processed. If --limit left some journals
    # unprocessed, leave the cursor alone -- next run will re-fetch the same
    # set, silently skip the ones already done (via processed_journals.json),
    # and pick up where this run left off.
    if journals and len(to_process) == total_new:
        newest = max(j['updated_at'] for j in journals)
        state.set_last_sync(newest)

    if total_proposals or total_new_entities:
        print(
            f'\nQueued {total_proposals} synopsis update(s) and {total_new_entities} new entity suggestion(s) this run.'
        )
        print('Run `python -m kanka_wiki_updater.review` to review and publish them.')
    else:
        print('\nNothing to queue this run.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync new Kanka session journals into proposed wiki updates.')
    parser.add_argument(
        '--limit',
        type=int,
        default=config.JOURNAL_BATCH_LIMIT,
        help='Max number of new journals to process this run, oldest-first. '
        'Defaults to JOURNAL_BATCH_LIMIT in .env, or unlimited if unset.',
    )
    args = parser.parse_args()
    main(limit=args.limit)
