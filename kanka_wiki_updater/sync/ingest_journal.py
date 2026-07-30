#!/usr/bin/env python3
"""Ingest engine: pull session-note journals from Kanka, identify mentioned entities,
call the LLM to propose wiki updates, and queue proposals for human review.

This module is the shared core used by both the CLI and the web
backend (review.web). It accepts pluggable callback functions so any frontend
can receive structured progress events without parsing terminal output.

Usage — programmatic:
    from kanka_wiki_updater.sync.ingest_journal import run_ingest, build_entity_index, find_mentioned_entities

    callbacks = {
        'entity_started': lambda e, j: print(f'Processing {e}...'),
        'llm_result':     lambda e, j, ok, data: ... ,
        'proposal_queued': lambda p: ...,
        'new_entity_suggestion': lambda s, jn: ...,
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
    from .core import config, state
    from .core.kanka_client import KankaClient
    from .core.mentions import (
        MENTION_DISPLAY_RE,
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
    from ..sync.relation_types import RelationTypeTracker  # noqa: E402
except ImportError:
    from kanka_wiki_updater.core import config, state
    from kanka_wiki_updater.core.kanka_client import KankaClient
    from kanka_wiki_updater.core.mentions import (
        MENTION_DISPLAY_RE,
        fuzzy_name_matches,
        linked_entity_ids,
        strip_html,
    )
    from kanka_wiki_updater.sync.synopsis_generator import (  # noqa: F401 -- kept for potential external callers
        NEW_ENTITY_SYSTEM_PROMPT,
        NEW_ENTITY_USER_PROMPT_TEMPLATE,
        _build_journal_url,
        _is_known_entity,
        build_entity_index,
        chat_json,
        propose_update,
        relation_summary,
    )
    from kanka_wiki_updater.sync.relation_types import RelationTypeTracker  # noqa: E402


# ---------------------------------------------------------------------------
# Default no-op callbacks — callers can override any subset
# ---------------------------------------------------------------------------

def _default_callbacks():
    """Return a dict of default (no-op) callback functions."""
    return {
        'entity_started': lambda entity_name, journal_name: None,
        'llm_result': lambda entity_name, journal_name, ok, data: None,
        'proposal_queued': lambda proposal_dict: None,
        'new_entity_suggestion': lambda suggestion_dict, journal_name: None,
        'journal_completed': lambda journal_name, entities_processed, suggestions_count: None,
        'sync_started': lambda total_journals, total_entities_estimate: None,
        'sync_completed': lambda total_proposals, total_new_entities: None,
        'journal_entities_discovered': lambda journal_name, entity_names: None,
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
    """Ask the LLM once per journal whether it mentions any character or location not already a known entity.

    Existing wiki links like ``[character:123456|R]`` are masked to
    ``[character:123456]`` before scanning so the LLM does not mistake the
    display-name portion ("R") for a new character name.
    """
    session_text = strip_html(journal.get('entry', '') or '')
    if not session_text.strip():
        return []
    # Mask existing entity links so their display names aren't picked up
    # as new-entity suggestions (e.g. [character:123456|R] → [character:123456]).
    session_text = MENTION_DISPLAY_RE.sub(r'\1]', session_text)

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
                'source_journal': journal.get('name'),
                '_journal_id': journal_id,
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
    journals = client.get_journals(since=last_sync, journal_types=config.SESSION_JOURNAL_TYPES or None)
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

    # Build a relation type tracker so the LLM knows about existing types.
    relation_tracker = RelationTypeTracker()
    relation_tracker.load()
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

        cancelled_during_journal = False

        t0 = time.time()
        mentioned = find_mentioned_entities(journal.get('entry', ''), index)
        new_candidates = propose_new_entities(journal, known_names)
        total_units = len(mentioned) + (1 if new_candidates else 0)

        journal_entity_count = 0

        # Track which entities were started so we can emit completion callbacks
        # even if one crashes mid-processing.
        started_eids: list[int] = []

        # --- Discover entities for this journal (fast, no LLM) ---------------
        # New-entity names first so they appear before existing updates
        # both in the sync progress display and in the proposal queue.
        entity_names_for_journal = []
        for candidate in new_candidates:
            entity_names_for_journal.append(candidate['entity_name'])
        for eid in mentioned:
            entity_names_for_journal.append(index[eid]['name'])

        if total_units > 0:
            print(f'      {journal.get("name")}')

            # Emit full list of entities upfront so the UI can display them all.
            cbs['journal_entities_discovered'](
                journal.get('name', ''), entity_names_for_journal,
            )

            # Track per-entity success/failure for individual status emission
            entity_ok = {}

            # New-entity scanning (first, so they appear before updates in the queue)
            if new_candidates and not cancelled_during_journal:
                print(
                    f'      + {len(new_candidates)} new entity suggestion(s): '
                    + ', '.join(f'{c["entity_name"]} ({c["suggested_type"]})' for c in new_candidates)
                )
                journal_name = journal.get('name', '')
                for candidate in new_candidates:
                    state.append_to_queue([candidate])
                    known_names.add(candidate['entity_name'])
                    total_new_entities += 1
                    cbs['new_entity_suggestion'](candidate, journal_name)

            for eid in mentioned:
                try:
                    entity = index[eid]
                except KeyError:
                    print(f'      ! Entity {eid} not found in index — skipping', file=sys.stderr)
                    continue

                cbs['entity_started'](entity['name'], journal.get('name', ''))
                started_eids.append(eid)

                try:
                    proposal = propose_update(eid, entity, journal, index, relation_tracker=relation_tracker)
                except Exception as e:
                    print(f'      ! Error processing {entity["name"]}: {e}', file=sys.stderr)
                    proposal = {'_llm_error': str(e)}

                llm_result_data = None  # payload for llm_result callback

                if isinstance(proposal, dict) and 'proposal_type' in proposal:
                    state.append_to_queue([proposal])
                    total_proposals += 1
                    cbs['proposal_queued'](proposal)
                    journal_entity_count += 1
                    entity_ok[eid] = True
                elif isinstance(proposal, dict) and '_llm_error' in proposal:
                    # LLM call failed — still queue a minimal proposal so the user
                    # can retry it (regenerate) from the review UI instead of needing
                    # to re-run the entire sync pipeline.
                    error_proposal = {
                        'proposal_type': 'update',
                        'entity_id': eid,
                        'entity_kind': entity['kind'],
                        'entity_local_id': entity['local_id'],
                        'entity_name': entity['name'],
                        'source_journal': journal.get('name'),
                        '_journal_id': journal['id'],
                        '_source_journal_url': _build_journal_url(journal['id']),
                        'previous_entry': entity['entry'] or '',
                        'proposed_entry': '',
                        'change_summary': '',
                        'relation_changes': [],
                        'uncertain': [],
                        'truncated': False,
                        '_llm_error': proposal['_llm_error'],
                        'status': 'pending',
                    }
                    state.append_to_queue([error_proposal])
                    total_proposals += 1
                    cbs['proposal_queued'](error_proposal)
                    journal_entity_count += 1
                    entity_ok[eid] = False
                    llm_result_data = proposal  # carries _llm_error key
                else:
                    # No meaningful change — LLM decided skip (not an error).
                    # Pass _no_proposal flag so downstream can mark as 'skipped'.
                    entity_ok[eid] = True
                    llm_result_data = {'_no_proposal': True}

                # Emit completion status IMMEDIATELY after this entity's LLM call
                # completes, so the UI updates in real-time instead of waiting for
                # ALL entities to finish before showing any results.
                ok = entity_ok.get(eid, False)
                error_msg = 'LLM call failed' if not ok else None
                cbs['llm_result'](entity['name'], journal.get('name', ''), ok, llm_result_data)

                # Check for cancellation after each entity's LLM call completes.
                # The in-flight LLM call cannot be interrupted, but subsequent
                # entities won't start processing once cancelled.
                if cancelled_event is not None and cancelled_event.is_set():
                    print(f'\nSync cancelled by user after processing {journal_entity_count} entity(ies) for '
                          f"'{journal.get('name', '')}'.")
                    cancelled_during_journal = True
                    break

            total_entities_processed += journal_entity_count + (len(new_candidates) if new_candidates else 0)

        # Note: llm_result is now called inside the entity loop above,
        # immediately after each entity's LLM call completes. This ensures
        # real-time status updates in the UI instead of waiting for all
        # entities to finish before showing any results.

        # New-entity candidates always succeed (no LLM synopsis step).
        if new_candidates and not cancelled_during_journal:
            for candidate in new_candidates:
                cbs['llm_result'](candidate['entity_name'], journal.get('name', ''), True, candidate)

        # Mark journal as completed.
        total_journals_processed += 1
        cbs['journal_completed'](journal.get('name', f'Journal {i}'), journal_entity_count, len(new_candidates))
        if not cancelled_during_journal:
            state.mark_journal_processed(journal['id'], title=journal.get('name'))
        if not cancelled_during_journal:
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
