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
import re
import sys
import time
from pathlib import Path

from pydantic import BaseModel

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


class EntityData(BaseModel):
    kind: str
    local_id: int
    name: str
    entry: str = ''
    relations: list = None

    def model_post_init(self, __context):
        if self.relations is None:
            self.relations = []


def _rel_to_dict(rel):
    """Convert a Relation model to the dict shape expected by apply_relation_changes_locally."""
    d = {
        'target_id': getattr(rel, 'target_id', None),
        'owner_id': getattr(rel, 'owner_id', None),
        'relation': getattr(rel, 'relation', ''),
    }
    if hasattr(rel, 'attitude'):
        d['attitude'] = rel.attitude
    return d


def build_entity_index(client):
    """One pass over characters + locations, keyed by entity_id (the
    cross-entity-type id used by relations and mentions)."""
    index = {}
    for kind, rows in (('character', client.get_characters()), ('location', client.get_locations())):
        for row in rows:
            entry_text = getattr(row, 'entry', '') or ''
            rels = getattr(row, 'relations', []) or []
            index[row.entity_id] = EntityData(
                kind=kind,
                local_id=row.id,
                name=row.name,
                entry=entry_text,
                relations=[_rel_to_dict(r) for r in rels],
            )
    return index


def relation_summary(relations, index):
    if not relations:
        return '(none on record)'
    lines = []
    for rel in relations:
        target_id = rel.get('target_id') if isinstance(rel, dict) else getattr(rel, 'target_id', None)
        other_id = (
            target_id
            if target_id and target_id in index
            else (rel.get('owner_id') if isinstance(rel, dict) else getattr(rel, 'owner_id', None))
        )
        other = index.get(other_id)
        name = other.name if other else f'entity #{other_id}'
        rel_name = rel.get('relation') if isinstance(rel, dict) else getattr(rel, 'relation', '')
        attitude = rel.get('attitude') if isinstance(rel, dict) else getattr(rel, 'attitude', None)
        lines.append(f'- {rel_name} -> {name} (attitude: {attitude})')
    return '\n'.join(lines)


def find_mentioned_entities(journal_entry_raw, index):
    ids = linked_entity_ids(journal_entry_raw)
    names_by_id = {eid: data.name for eid, data in index.items()}
    ids |= fuzzy_name_matches(strip_html(journal_entry_raw), names_by_id)
    return [eid for eid in ids if eid in index]


def propose_update(entity_id, entity, journal, index):
    session_text = strip_html(getattr(journal, 'entry', '') or '')
    if not session_text.strip():
        return None

    user_prompt = USER_PROMPT_TEMPLATE.format(
        name=entity.name,
        entity_kind=entity.kind,
        current_entry=strip_html(entity.entry) or '(no synopsis yet)',
        current_relations=relation_summary(entity.relations, index),
        journal_name=getattr(journal, 'name', None) or 'Session note',
        journal_date=getattr(journal, 'date', None) or getattr(journal, 'created_at', '') or '',
        session_text=session_text,
    )
    try:
        result = chat_json(SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        # Catch broadly, not just LLMError -- a bad response from the model,
        # a network hiccup, or anything else here should cost us one entity,
        # not the rest of a multi-hour backfill run.
        print(f'  ! LLM error for {entity.name}: {e}', file=sys.stderr)
        return None

    no_text_change = normalize_text(result.get('updated_entry', '')) == normalize_text(entity.entry)
    if no_text_change and not result.get('relation_changes'):
        return None  # model decided nothing meaningfully changed

    return {
        'proposal_type': 'update',
        'entity_id': entity_id,
        'entity_kind': entity.kind,
        'entity_local_id': entity.local_id,
        'entity_name': entity.name,
        'source_journal': getattr(journal, 'name', None),
        'previous_entry': entity.entry,
        'proposed_entry': result.get('updated_entry', '') or entity.entry,
        'change_summary': result.get('change_summary', ''),
        'relation_changes': result.get('relation_changes', []),
        'uncertain': result.get('uncertain', []),
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
    raw = getattr(journal, 'date', None) or ''
    raw = raw.strip() if isinstance(raw, str) else str(raw).strip() if raw else ''
    match = DATE_RE.match(raw)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return (0, year, month, day, getattr(journal, 'created_at', '') or '')

    year = getattr(journal, 'calendar_year', None)
    if year is not None:
        return (
            0,
            int(year),
            getattr(journal, 'calendar_month', 0) or 0,
            getattr(journal, 'calendar_day', 0) or 0,
            getattr(journal, 'created_at', '') or '',
        )

    return (1, 0, 0, 0, getattr(journal, 'created_at', '') or '')


def apply_relation_changes_locally(entity_id, relation_changes, index, name_to_id):
    """Keep the in-memory index's relations in sync with what was just
    proposed (but not yet applied to Kanka), so the *next* journal in this
    same run sees the up-to-date picture instead of the stale Kanka copy."""
    entity_data = index[entity_id]
    relations = entity_data.relations
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
    session_text = strip_html(getattr(journal, 'entry', '') or '')
    if not session_text.strip():
        return []

    user_prompt = NEW_ENTITY_USER_PROMPT_TEMPLATE.format(
        known_names='\n'.join(f'- {n}' for n in sorted(known_names)) or '(none yet)',
        journal_name=getattr(journal, 'name', None) or 'Session note',
        journal_date=getattr(journal, 'date', None) or getattr(journal, 'created_at', '') or '',
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
            continue  # defensive re-check even though the prompt already asked for this

        suggested_type = (candidate.get('suggested_type') or 'character').strip().lower()
        if suggested_type not in ('character', 'location'):
            suggested_type = 'character'

        proposals.append(
            {
                'proposal_type': 'new_entity',
                'entity_name': name,
                'suggested_type': suggested_type,
                'draft_entry': candidate.get('draft_entry', ''),
                'reason': candidate.get('reason', ''),
                'source_journal': getattr(journal, 'name', None),
                'status': 'pending',
            }
        )
        known_lower.add(name.lower())  # don't suggest the same new name twice in one run
    return proposals


def main(limit=None):
    client = KankaClient()
    print('Building character/location index...')
    index = build_entity_index(client)
    name_to_id = {data.name: eid for eid, data in index.items()}

    last_sync = state.get_last_sync()
    print(f'Fetching journals since: {last_sync or "(beginning -- full history)"}')
    journals = client.get_journals(since=last_sync, journal_type=config.SESSION_JOURNAL_TYPE or None)
    print(f'Fetched {len(journals)} journal(s) from Kanka.')

    processed_ids = state.get_processed_journal_ids()
    to_process = [j for j in journals if j.id not in processed_ids]

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
        mentioned = find_mentioned_entities(getattr(journal, 'entry', ''), index)
        new_candidates = propose_new_entities(journal, known_names)

        # Calculate total work units for this journal
        total_units = len(mentioned) + (1 if new_candidates else 0)

        if total_units > 0:
            print(f'      {getattr(journal, "name", None)}')
            tracker = ProgressTracker(total_units)

            for entity_id in mentioned:
                entity = index[entity_id]
                proposal = propose_update(entity_id, entity, journal, index)
                tracker.mark_done(f'LLM for {entity.name}...')
                if proposal:
                    state.append_to_queue([proposal])
                    total_proposals += 1
                    entity.entry = proposal['proposed_entry']
                    apply_relation_changes_locally(entity_id, proposal['relation_changes'], index, name_to_id)

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
            print(f"  ({i}/{len(to_process)}) '{getattr(journal, 'name', None)}': no entities found")

        # Always mark journal processed regardless of tracker usage
        state.mark_journal_processed(journal.id, title=getattr(journal, 'name', None))
        elapsed = time.time() - t0
        mins, secs = divmod(int(elapsed), 60)
        print(f"      ({i}/{len(to_process)}) '{getattr(journal, 'name', None)}' processed in {mins:02d}:{secs:02d}")

    # Only advance the API's "lastSync" cursor once everything fetched this
    # run has actually been processed. If --limit left some journals
    # unprocessed, leave the cursor alone -- next run will re-fetch the same
    # set, silently skip the ones already done (via processed_journals.json),
    # and pick up where this run left off.
    if journals and len(to_process) == total_new:
        newest = max(j.updated_at for j in journals)
        state.set_last_sync(newest)

    if total_proposals or total_new_entities:
        print(
            f'\nQueued {total_proposals} synopsis/relationship update(s) and '
            f'{total_new_entities} new entity suggestion(s) this run.'
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
