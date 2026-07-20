#!/usr/bin/env python3
"""Ingest engine: pull session-note journals from Kanka, identify mentioned entities,
call the LLM to propose wiki updates, and queue proposals for human review.

This module is the shared core used by both the CLI (sync_pipeline.py) and the web
backend (review_web.py). It accepts pluggable callback functions so any frontend
can receive structured progress events without parsing terminal output.

Usage — programmatic:
    from .ingest_journal import run_ingest, build_entity_index, find_mentioned_entities

    callbacks = {
        'entity_started': lambda e, j: print(f'Processing {e}...'),
        'llm_result':     lambda e, j, ok, data: ... ,
        'proposal_queued': lambda p: ...,
        'new_entity_suggestion': lambda s: ...,
        'journal_completed': lambda j, n_entities, n_suggetsions: ...,
    }
    run_ingest(callbacks=callbacks)

Usage — CLI (default callbacks produce identical terminal output):
    python -m kanka_wiki_updater.sync_pipeline [--limit N]
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

_DEBUG = bool(os.environ.get('KANKA_DEBUG'))  # type: ignore[unused-ignore,assignment]


def _debug(*args):
    if _DEBUG:
        print('[INGEST DEBUG]', *args, file=sys.stderr)


if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from . import config, state
    from .kanka_client import KankaClient
    from .mentions import (
        fuzzy_name_matches,
        linked_entity_ids,
        strip_html,
    )
    from .synopsis_generator import (
        NEW_ENTITY_SYSTEM_PROMPT,
        NEW_ENTITY_USER_PROMPT_TEMPLATE,
        _annotate_journals,  # noqa: F401 -- kept for potential external callers
        _build_journal_url,
        _is_known_entity,
        build_entity_index,
        chat_json,
        propose_update,
        relation_summary,
    )
except ImportError:
    from kanka_wiki_updater import config, state
    from kanka_wiki_updater.kanka_client import KankaClient
    from kanka_wiki_updater.mentions import (
        fuzzy_name_matches,
        linked_entity_ids,
        strip_html,
    )
    from kanka_wiki_updater.synopsis_generator import (  # noqa: F401 -- kept for potential external callers
        NEW_ENTITY_SYSTEM_PROMPT,
        NEW_ENTITY_USER_PROMPT_TEMPLATE,
        _build_journal_url,
        _is_known_entity,
        build_entity_index,
        chat_json,
        propose_update,
        relation_summary,
    )


# ---------------------------------------------------------------------------
# Default no-op callbacks — callers can override any subset
# ---------------------------------------------------------------------------

def _default_callbacks():
    """Return a dict of default (no-op) callback functions."""
    return {
        'entity_started': lambda entity_name, journal_name: None,
        'llm_result': lambda entity_name, journal_name, ok, data: None,
        'proposal_queued': lambda proposal_dict: None,
        'new_entity_suggestion': lambda suggestion_dict: None,
        'journal_completed': lambda journal_name, entities_processed, suggestions_count: None,
        'sync_started': lambda total_journals, total_entities_estimate: None,
        'sync_completed': lambda total_proposals, total_new_entities: None,
    }


# ---------------------------------------------------------------------------
# Business logic (extracted from sync_pipeline.py — unchanged behavior)
# ---------------------------------------------------------------------------

def find_mentioned_entities(journal_entry_raw, index):
    """Find entity IDs mentioned in a journal entry via wiki links and fuzzy name matching."""
    ids = linked_entity_ids(journal_entry_raw)
    names_by_id = {eid: data['name'] for eid, data in index.items()}
    ids |= fuzzy_name_matches(strip_html(journal_entry_raw), names_by_id)
    return [eid for eid in ids if eid in index]


DATE_RE = re.compile(r'(\d{1,5})-(\d{1,2})-(\d{1,2})')


def journal_sort_key(journal):
    """Chronological sort key based on the journal's in-fiction date."""
    raw = journal.get('date') or ''
    raw = raw.strip() if isinstance(raw, str) else str(raw).strip() if raw else ''
    match = DATE_RE.match(raw)
    if match:
        year, month, day = (int(g) for g in match.groups())
        return (0, year, month, day, journal.get('created_at', '') or '')

    year = journal.get('calendar_year')
    if year is not None:
        return (
            0, int(year),
            journal.get('calendar_month') or 0,
            journal.get('calendar_day') or 0,
            journal.get('created_at', '') or '',
        )

    return (1, 0, 0, 0, journal.get('created_at', '') or '')


def apply_relation_changes_locally(entity_id, relation_changes, index, name_to_id):
    """Keep the in-memory index's relations in sync with what was just proposed."""
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
    """Ask the LLM once per journal whether it mentions any character or location not already a known entity."""
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
            continue

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
        known_lower.add(name.lower())
    return proposals


# ---------------------------------------------------------------------------
# Core ingest engine with callback support
# ---------------------------------------------------------------------------

def run_ingest(client=None, callbacks=None, limit=None, cancelled_event=None):
    """Run the journal ingestion pipeline.

    This is the shared core used by both CLI and web backends. It fetches new
    session-note journals from Kanka, identifies mentioned entities, calls the LLM
    per entity to generate wiki update proposals, and queues those proposals for
    human review.

    Parameters
    ----------
    client : KankaClient | None
        Pre-built API client. If None, a fresh one is created (CLI default).
    callbacks : dict[str, callable] | None
        Callback functions keyed by event name. See _default_callbacks() for the
        expected signature of each callback. Missing keys fall back to no-ops.
    limit : int | None
        Max number of new journals to process this run (oldest-first).
    cancelled_event : threading.Event | None
        If provided, the pipeline checks ``cancelled_event.is_set()`` before
        processing each journal and stops when it becomes set.  The web UI
        passes its module-level cancellation event so users can abort a sync
        from the browser.

    Returns
    -------
    dict
        Summary: {'total_proposals': int, 'total_new_entities': int,
                  'journals_processed': int, 'entities_processed': int}
    """
    if client is None:
        client = KankaClient()

    cbs = _default_callbacks()
    if callbacks:
        cbs.update(callbacks)

    # --- Phase 1: Build entity index ----------------------------------------
    print('Building character/location index...')
    index = build_entity_index(client)
    name_to_id = {data['name']: eid for eid, data in index.items()}

    last_sync = state.get_last_sync()
    print(f'Fetching journals since: {last_sync or "(beginning -- full history)"}')
    journals = client.get_journals(since=last_sync, journal_type=config.SESSION_JOURNAL_TYPE or None)
    print(f'Fetched {len(journals)} journal(s) from Kanka.')

    processed_ids = state.get_processed_journal_ids()
    to_process = [j for j in journals if j['id'] not in processed_ids]
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

    # --- Phase 2: Process journals -------------------------------------------
    known_names = set(name_to_id.keys())
    total_proposals = 0
    total_new_entities = 0
    total_journals_processed = 0
    total_entities_processed = 0

    cbs['sync_started'](len(to_process), None)

    for i, journal in enumerate(to_process, start=1):
        # Check for cancellation request before processing each journal.
        if cancelled_event is not None and cancelled_event.is_set():
            print(f'\nSync cancelled by user after processing {total_journals_processed} journal(s).')
            break

        t0 = time.time()
        mentioned = find_mentioned_entities(journal.get('entry', ''), index)
        new_candidates = propose_new_entities(journal, known_names)
        total_units = len(mentioned) + (1 if new_candidates else 0)

        journal_entity_count = 0

        if total_units > 0:
            print(f'      {journal.get("name")}')

            # Process each mentioned entity
            for eid in mentioned:
                entity = index[eid]
                cbs['entity_started'](entity['name'], journal.get('name', ''))

                proposal = propose_update(eid, entity, journal, index)
                if isinstance(proposal, dict) and 'proposal_type' in proposal:
                    state.append_to_queue([proposal])
                    total_proposals += 1
                    cbs['proposal_queued'](proposal)
                    journal_entity_count += 1

            # New-entity scanning
            if new_candidates:
                print(
                    f'      + {len(new_candidates)} new entity suggestion(s): '
                    + ', '.join(f'{c["entity_name"]} ({c["suggested_type"]})' for c in new_candidates)
                )
                for candidate in new_candidates:
                    state.append_to_queue([candidate])
                    known_names.add(candidate['entity_name'])
                    total_new_entities += 1
                    cbs['new_entity_suggestion'](candidate)

            total_entities_processed += journal_entity_count + (len(new_candidates) if new_candidates else 0)

        # Mark entity as done / error for each mentioned entity
        if mentioned:
            for eid in mentioned:
                entity = index[eid]
                cbs['llm_result'](entity['name'], journal.get('name', ''), True, None)

            if new_candidates:
                for candidate in new_candidates:
                    cbs['llm_result'](candidate['entity_name'], journal.get('name', ''), True, candidate)

        # Mark journal as completed
        total_journals_processed += 1
        cbs['journal_completed'](journal.get('name', f'Journal {i}'), journal_entity_count, len(new_candidates))

        state.mark_journal_processed(journal['id'], title=journal.get('name'))
        elapsed = time.time() - t0
        mins, secs = divmod(int(elapsed), 60)
        print(f"      ({i}/{len(to_process)}) '{journal.get('name')}' processed in {mins:02d}:{secs:02d}")

    # --- Phase 3: Finalize --------------------------------------------------
    if journals and len(to_process) == total_new:
        newest = max(j['updated_at'] for j in journals)
        state.set_last_sync(newest)

    cbs['sync_completed'](total_proposals, total_new_entities)

    summary = {
        'total_proposals': total_proposals,
        'total_new_entities': total_new_entities,
        'journals_processed': total_journals_processed,
        'entities_processed': total_entities_processed,
    }

    if total_proposals or total_new_entities:
        print(
            f'\nQueued {total_proposals} synopsis update(s) and {total_new_entities} new entity suggestion(s) this run.'
        )
        print('Run `python -m kanka_wiki_updater.review` to review and publish them.')
    else:
        print('\nNothing to queue this run.')

    return summary


# ---------------------------------------------------------------------------
# CLI entry point (unchanged behavior — wraps run_ingest with terminal callbacks)
# ---------------------------------------------------------------------------

def main(limit=None):
    """CLI entry point for `python -m kanka_wiki_updater.sync_pipeline [--limit N]`."""
    run_ingest(limit=limit)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Ingest new Kanka session journals into proposed wiki updates.')
    parser.add_argument(
        '--limit',
        type=int,
        default=config.JOURNAL_BATCH_LIMIT,
        help='Max number of new journals to process this run, oldest-first. '
        'Defaults to JOURNAL_BATCH_LIMIT in .env, or unlimited if unset.',
    )
    args = parser.parse_args()
    main(limit=args.limit)
