#!/usr/bin/env python3
"""
Revert the most recent `review` run's applied changes.

Undoes things in the reverse order they were applied: relation changes and
synopsis edits from "update" proposals first, then "new_entity" creations
last (so a relation pointing at a newly-created entity gets removed before
the entity itself does).

Limitations:
  - Only the single most recent *unreverted* batch can be undone. Run this
    again to step back further only if that batch hasn't already been
    reverted -- there's no multi-step undo history beyond that.
  - A batch applied before this revert tool existed isn't recorded with
    enough detail (prior relation state, created entity IDs) to undo
    automatically.
  - The journal(s) behind a reverted batch stay marked as "processed", so
    re-running sync_pipeline won't regenerate these proposals on its own.
    If you want them reconsidered, remove the relevant journal ID(s) from
    data/processed_journals.json first.

Usage:
    ./kanka_wiki_updater/revert.py
    python -m kanka_wiki_updater.revert
"""

import sys
from pathlib import Path

if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from . import colors, state
    from .kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater import colors, state
    from kanka_wiki_updater.kanka_client import KankaClient


def _rel_target(rel):
    """Get target_id from a relation that may be a model or dict."""
    return getattr(rel, 'target_id', None) or (rel.get('target_id') if isinstance(rel, dict) else None)


def _rel_id(rel):
    """Get id from a relation that may be a model or dict."""
    return getattr(rel, 'id', None) or (rel.get('id') if isinstance(rel, dict) else None)


def revert_relation_result(entity_id, rr, client):
    """Best-effort undo of one relation change. Always re-fetches current
    relations and matches by target_id rather than trusting a cached
    relation id, since that id may not have been available at apply time."""
    current = client.get_relations(entity_id)
    existing = next((r for r in current if _rel_target(r) == rr['target_id']), None)
    action_taken = rr['action_taken']

    if action_taken == 'created':
        if existing and _rel_id(existing):
            client.delete_relation(entity_id, _rel_id(existing))
            print(colors.green(f'  - Removed relation -> {rr["target_name"]} (undoing a create)'))
        else:
            print(
                colors.yellow(
                    f"  ! Couldn't find the relation -> {rr['target_name']} to remove -- it "
                    f"may already be gone, or the API isn't returning an id for it."
                )
            )
    elif action_taken == 'updated':
        prev = rr.get('previous_relation') or {}
        if existing and _rel_id(existing):
            client.update_relation(
                entity_id, _rel_id(existing), relation=prev.get('relation'), attitude=prev.get('attitude')
            )
            print(colors.green(f'  - Restored relation -> {rr["target_name"]} to its previous label/attitude'))
        else:
            print(colors.yellow(f"  ! Couldn't find the relation -> {rr['target_name']} to restore."))
    elif action_taken == 'deleted':
        prev = rr.get('previous_relation') or {}
        if existing:
            print(colors.dim(f'  (Relation -> {rr["target_name"]} already exists -- not re-creating it.)'))
        else:
            client.create_relation(entity_id, rr['target_id'], prev.get('relation', 'Related to'), prev.get('attitude'))
            print(colors.green(f'  - Re-created relation -> {rr["target_name"]} (undoing a delete)'))


def revert_update_entry(entry, client):
    print(
        colors.bold(colors.cyan(f'{entry["entity_name"]} ({entry["entity_kind"]})'))
        + colors.dim(f'  <-  {entry["source_journal"]}')
    )

    # Reverse of application order: relations were applied after the
    # synopsis, so undo them first.
    for rr in reversed(entry.get('relation_results', [])):
        revert_relation_result(entry['entity_id'], rr, client)

    client.update_entity_entry(
        'characters' if entry['entity_kind'] == 'character' else 'locations',
        entry['entity_local_id'],
        entry['previous_entry'],
    )
    print(colors.green('  - Synopsis restored to its pre-review version.'))


def revert_new_entity_entry(entry, client):
    kind = entry.get('created_kind')
    local_id = entry.get('created_local_id')
    print(colors.bold(colors.magenta(f'NEW {(kind or "?").upper()}: {entry["entity_name"]}')))

    if not local_id or not kind:
        print(
            colors.yellow(
                "  ! No record of this entity's Kanka ID -- can't delete it automatically. "
                "Remove it manually in Kanka if you don't want to keep it."
            )
        )
        return

    if kind == 'character':
        client.delete_character(local_id)
    else:
        client.delete_location(local_id)
    print(colors.green(f'  - Deleted the {kind} created during the last review.'))


def main():
    batch = state.get_last_applied_batch()
    if not batch:
        print(
            "Nothing to revert. Either nothing's been applied yet, the most recent "
            "run was already reverted, or it predates this revert tool and wasn't "
            'recorded in enough detail to undo automatically.'
        )
        return

    entries = batch['entries']
    new_entity_entries = [e for e in entries if e.get('proposal_type') == 'new_entity']
    update_entries = [e for e in entries if e.get('proposal_type') != 'new_entity']

    print(colors.bold(f'Last review run ({batch["run_id"]}) applied {len(entries)} change(s):'))
    for e in update_entries:
        rel_note = f', {len(e["relation_results"])} relation change(s)' if e.get('relation_results') else ''
        print(f'  - update: {e["entity_name"]} ({e["entity_kind"]}){rel_note}')
    for e in new_entity_entries:
        print(f'  - new {e.get("created_kind") or e.get("suggested_type")}: {e["entity_name"]}')

    confirm = input(colors.cyan('\nRevert all of this? [y/n] ')).strip().lower()
    if confirm != 'y':
        print('Cancelled -- nothing was reverted.')
        return

    client = KankaClient()
    # Reverse the original application order overall too: updates (and their
    # relation changes) get undone before the new entities they might
    # reference, since new entities were always applied first.
    for entry in reversed(entries):
        print('\n' + colors.dim('-' * 70))
        try:
            if entry.get('proposal_type') == 'new_entity':
                revert_new_entity_entry(entry, client)
            else:
                revert_update_entry(entry, client)
        except Exception as e:
            print(colors.red(f'  ! Failed to revert this entry: {e}'))

    state.mark_batch_reverted(batch['run_id'])
    print(colors.bold('\nDone reverting the most recent review run.'))
    print(
        colors.dim(
            'Note: the underlying journal(s) are still marked as processed, so '
            "re-running sync_pipeline won't regenerate these proposals on its own."
        )
    )


if __name__ == '__main__':
    main()
