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
    from .mentions import fuzzy_name_matches, linked_entity_ids, normalize_text, strip_html
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
    from kanka_wiki_updater.mentions import fuzzy_name_matches, linked_entity_ids, normalize_text, strip_html
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

    session_text = _annotate_journals(raw_text, str(journal.get('id') or ''))

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

    # Inject journal attribution link when LLM flags new information.
    _is_new_info = result.get('_is_new_info') is True
    if _is_new_info:
        journal_id = journal['id']
        journal_name = (journal.get('name') or '').replace('|', '').replace(']', '')
        proposed_text = f'[journal:{journal_id}|{journal_name}] {proposed_text}'

    previous_text = entity['entry']

    # Detect when the LLM output is significantly shorter than the input,
    # which often means information was lost (summarized/condensed instead
    # of preserved). Flag it so review.py can warn the human reviewer.
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
