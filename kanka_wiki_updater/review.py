#!/usr/bin/env python3
"""
Review pending proposals queued by sync_pipeline.py, approve or reject each
one, and publish approved changes back to Kanka. Handles two kinds of
proposals:

  - "update": a revised synopsis and/or relationship changes for an
    existing character/location.
  - "new_entity": a proper noun mentioned in a session note that doesn't
    match any existing entity, with a suggested type and draft synopsis.

New-entity proposals are reviewed first, so if you approve one, it becomes
a valid relation target for "update" proposals reviewed right after it in
the same run (a relation change pointing at a brand-new character can't
resolve to anything until that character actually exists in Kanka).

Usage:
    ./kanka_wiki_updater/review.py
    python -m kanka_wiki_updater.review
"""

import difflib
import sys
from pathlib import Path

if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from . import colors, state
    from .kanka_client import KankaClient
    from .mentions import add_missing_entity_tags, find_unlinked_mentions, linked_entity_ids, normalize_text, strip_html
    from .sync_pipeline import build_entity_index
except ImportError:
    from kanka_wiki_updater import colors, state
    from kanka_wiki_updater.kanka_client import KankaClient
    from kanka_wiki_updater.mentions import add_missing_entity_tags, find_unlinked_mentions, linked_entity_ids, normalize_text, strip_html
    from kanka_wiki_updater.sync_pipeline import build_entity_index


def has_meaningful_change(proposal):
    """An "update" proposal is worth a human's time only if the synopsis
    actually differs (ignoring formatting-only differences) or a relation
    change is proposed. Anything else is a no-op the model generated and
    shouldn't interrupt the review flow."""
    same_text = normalize_text(proposal['previous_entry']) == normalize_text(proposal['proposed_entry'])
    return not same_text or bool(proposal.get('relation_changes'))


def print_diff(old, new):
    old_lines = strip_html(old).splitlines() or ['']
    new_lines = strip_html(new).splitlines() or ['']
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=''))
    for line in diff[2:]:  # skip the --- / +++ header lines
        if line.startswith('+'):
            print(' ', colors.green(line))
        elif line.startswith('-'):
            print(' ', colors.red(line))
        elif line.startswith('@@'):
            print(' ', colors.dim(line))
        else:
            print(' ', line)


def prompt_choice(prompt, choices='yna'):
    while True:
        answer = input(colors.cyan(f'{prompt} [{choices}] ')).strip().lower()
        if answer and answer[0] in choices:
            return answer[0]


def review_new_entity_proposal(proposal, index, name_to_id, client, save_fn=None):
    print('\n' + colors.dim('=' * 70))
    print(
        colors.bold(colors.magenta(f'NEW {proposal["suggested_type"].upper()}: {proposal["entity_name"]}'))
        + colors.dim(f'  <-  {proposal["source_journal"]}')
    )
    print(colors.dim('-' * 70))
    if proposal.get('reason'):
        print(f'Why flagged: {proposal["reason"]}')
    print('\nDraft synopsis:')
    print(' ', strip_html(proposal.get('draft_entry', '')) or '(none)')
    unlinked_warning = unlinked_mention_warning(proposal.get('draft_entry', ''), index)
    if unlinked_warning:
        print(colors.yellow(unlinked_warning))

    choice = prompt_choice(
        '\nCreate in Kanka? (y)es as suggested type / (c)reate as the other type instead / (n)o, skip',
        choices='ycn',
    )
    if choice == 'n':
        proposal['status'] = 'rejected'
        if save_fn:
            save_fn()
        print()
        return proposal

    entity_type = proposal['suggested_type']
    if choice == 'c':
        entity_type = 'location' if entity_type == 'character' else 'character'

    if entity_type == 'character':
        result = client.create_character(proposal['entity_name'], entry=proposal.get('draft_entry'))
    else:
        result = client.create_location(proposal['entity_name'], entry=proposal.get('draft_entry'))

    data = result.get('data', {}) if isinstance(result, dict) else {}
    new_entity_id = data.get('entity_id')
    # Record what was actually created, regardless of whether we could read
    # back an entity_id -- revert.py needs created_local_id/created_kind to
    # undo this later.
    proposal['created_local_id'] = data.get('id')
    proposal['created_kind'] = entity_type
    proposal['created_entity_id'] = new_entity_id
    if new_entity_id:
        # Make the new entity immediately usable as a relation target for
        # any "update" proposals reviewed later in this same run.
        index[new_entity_id] = {
            'kind': entity_type,
            'local_id': data.get('id'),
            'name': proposal['entity_name'],
            'entry': data.get('entry') or '',
            'relations': [],
        }
        name_to_id[proposal['entity_name']] = new_entity_id
        print(
            colors.green(
                f"  Created {entity_type} '{proposal['entity_name']}' (entity_id={new_entity_id}). "
                f"It's now available as a relation target for the rest of this review."
            )
        )
    else:
        print(
            colors.yellow(
                f"  ! Created the entity, but couldn't read an entity_id back from Kanka's "
                f"response, so it won't resolve as a relation target this session. Raw "
                f'response: {result}'
            )
        )

    proposal['status'] = 'applied'
    if save_fn:
        save_fn()
    print()
    return proposal


def dropped_mention_warning(proposal, index):
    """The model is instructed never to strip existing [entity:N]-style
    mention links, but instructions aren't guarantees -- catch it
    programmatically too, since a silently-dropped wiki link is easy to
    miss in a wall of diff text."""
    dropped_ids = linked_entity_ids(proposal['previous_entry']) - linked_entity_ids(proposal['proposed_entry'])
    if not dropped_ids:
        return None
    names = [index[i]['name'] for i in dropped_ids if i in index]
    label = ', '.join(names) if names else f'entity id(s) {sorted(dropped_ids)}'
    return (
        f'  !! {len(dropped_ids)} mention link(s) present in the OLD synopsis are missing '
        f'from the new one: {label}. The model likely replaced a wiki link with plain '
        f'text while rewriting nearby prose -- check the diff closely; this is probably '
        f'NOT intentional.'
    )


def unlinked_mention_warning(text, index, exclude_entity_id=None):
    """Known entity names appearing as plain text in `text` with no
    [entity:N]-style link at all -- either a link the model should have
    added for a known character/location it named, or one it flattened to
    plain text (dropped_mention_warning catches that case too, from the
    angle of comparing old vs new; this one just looks at the final text).
    The model isn't told other entities' numeric IDs, so it can't reliably
    add a correct link itself -- this is a detect-and-flag-for-a-human
    check, not something the model can be instructed to self-correct."""
    names_by_id = {eid: data['name'] for eid, data in index.items()}
    found = find_unlinked_mentions(text, names_by_id, exclude_entity_id=exclude_entity_id)
    if not found:
        return None
    suggestions = [f'{name} (-> [{index[eid]["kind"]}:{eid}])' for eid, name in found]
    return (
        f'  ! {len(found)} known name(s) appear as plain text with no wiki link: '
        f'{", ".join(suggestions)}. Add the link manually in Kanka after approving '
        f'if you want it connected.'
    )


def review_proposal(proposal, index, name_to_id, client, update_num=None, total_updates=None, save_fn=None):
    if update_num is not None and total_updates is not None:
        banner = f'[ Update {update_num}/{total_updates} ]'
        right_pad = 70 - len('===') - len(banner)
        print(colors.dim('=' * 3 + banner + '=' * max(0, right_pad)))
    else:
        print('\n' + colors.dim('=' * 70))
    print(
        colors.bold(colors.cyan(f'{proposal["entity_name"]} ({proposal["entity_kind"]})'))
        + colors.dim(f'  <-  {proposal["source_journal"]}')
    )
    print(colors.dim('-' * 70))
    print(f'Summary: {proposal["change_summary"]}')
    warning = dropped_mention_warning(proposal, index)
    if warning:
        print(colors.bold(colors.red(warning)))
    unlinked_warning = unlinked_mention_warning(
        proposal['proposed_entry'], index, exclude_entity_id=proposal['entity_id']
    )
    if unlinked_warning:
        print(colors.yellow(unlinked_warning))
    if proposal.get('uncertain'):
        print(colors.yellow('Flagged as uncertain:'))
        for note in proposal['uncertain']:
            print(colors.yellow(f'  ! {note}'))
    # Auto-link any known entity names that appear as plain text without wiki tags
    # before showing the diff, so the user sees the final text with tags included.
    proposed = proposal['proposed_entry']
    linked = []
    if proposal.get('entity_id'):
        proposed, linked = add_missing_entity_tags(proposed, index, exclude_entity_id=proposal['entity_id'])
        if linked:
            proposal['proposed_entry'] = proposed
            suggestion = ', '.join(f'{name} (->{kind}:{eid})' for eid, kind, name in linked)
            print(colors.green(f'  + Auto-linked: {suggestion}'))

    print('\nSynopsis diff:')
    print_diff(proposal['previous_entry'], proposal['proposed_entry'])

    if proposal['relation_changes']:
        print(colors.bold('\nProposed relationship changes:'))
        for rc in proposal['relation_changes']:
            print(
                colors.magenta(
                    f'  [{rc["action"]}] {rc["relation"]} -> {rc["target_name"]} '
                    f'(attitude={rc.get("attitude")}) -- {rc.get("reason", "")}'
                )
            )

    choice = prompt_choice('\nApply this change? (y)es / (n)o / (a)pprove synopsis only, skip relations')
    if choice == 'n':
        proposal['status'] = 'rejected'
        if save_fn:
            save_fn()
        print()
        return proposal

    client.update_entity_entry(
        'characters' if proposal['entity_kind'] == 'character' else 'locations',
        proposal['entity_local_id'],
        proposal['proposed_entry'],
    )

    relation_results = []
    if choice == 'y':
        entity_id = proposal['entity_id']
        existing_relations = client.get_relations(entity_id)
        for rc in proposal['relation_changes']:
            try:
                target_id = name_to_id.get(rc['target_name'])
                if not target_id:
                    print(
                        colors.yellow(
                            f"  ! Skipping relation '{rc['relation']}' -> '{rc['target_name']}': "
                            f'no entity with that exact name is known. If this is a new '
                            f"character/location, its 'new entity' suggestion needs to be "
                            f'approved (it should appear earlier in this same review run) -- '
                            f'or check for a name mismatch (nickname, title, typo) between '
                            f"what the model wrote and the entity's actual name in Kanka."
                        )
                    )
                    continue

                existing = next((r for r in existing_relations if r.get('target_id') == target_id), None)
                action = (rc.get('action') or '').strip().lower()

                if action == 'delete':
                    if existing and existing.get('id'):
                        client.delete_relation(entity_id, existing['id'])
                        print(colors.green(f'  - Deleted relation -> {rc["target_name"]}'))
                        relation_results.append(
                            {
                                'action_taken': 'deleted',
                                'target_id': target_id,
                                'target_name': rc['target_name'],
                                'previous_relation': existing,
                            }
                        )
                    elif existing:
                        print(
                            colors.yellow(
                                f"  ! Found the relation to delete, but the API didn't return an "
                                f"'id' for it, so it can't be deleted via the API. Raw: {existing}"
                            )
                        )
                    else:
                        print(
                            colors.dim(
                                f"No existing relation to '{rc['target_name']}' found to delete -- nothing to do."
                            )
                        )

                elif action == 'update' and existing and existing.get('id'):
                    client.update_relation(
                        entity_id, existing['id'], relation=rc['relation'], attitude=rc.get('attitude')
                    )
                    print(colors.green(f"  - Updated relation -> {rc['target_name']}: '{rc['relation']}'"))
                    relation_results.append(
                        {
                            'action_taken': 'updated',
                            'target_id': target_id,
                            'target_name': rc['target_name'],
                            'previous_relation': existing,
                        }
                    )

                else:
                    resp = client.create_relation(entity_id, target_id, rc['relation'], rc.get('attitude'))
                    # The request not raising means Kanka accepted it -- record
                    # it as done regardless of how the response body parses.
                    # Different responses have wrapped the created object as
                    # either {"data": {...}} or {"data": [{...}]}; handle both
                    # rather than assuming one shape and crashing on the other.
                    body = resp.get('data') if isinstance(resp, dict) else None
                    if isinstance(body, list):
                        body = body[0] if body else {}
                    if not isinstance(body, dict):
                        body = {}
                    if body.get('target_id') == target_id:
                        print(
                            colors.green(
                                f"Created relation -> {rc['target_name']}: '{rc['relation']}' (confirmed by Kanka)"
                            )
                        )
                    else:
                        print(
                            colors.green(
                                f"  - Created relation -> {rc['target_name']}: '{rc['relation']}' "
                                f"(request succeeded; response shape was unexpected so this wasn't "
                                f'independently confirmed, but no error was raised)'
                            )
                        )
                    relation_results.append(
                        {
                            'action_taken': 'created',
                            'target_id': target_id,
                            'target_name': rc['target_name'],
                        }
                    )
            except Exception as e:
                # One bad relation change should cost us that one relation,
                # not the rest of this proposal's relations or the whole
                # review session.
                print(
                    colors.red(
                        f"  ! Unexpected error applying relation '{rc.get('relation')}' -> "
                        f"'{rc.get('target_name')}': {e}. Skipping just this one; continuing."
                    )
                )

    proposal['relation_results'] = relation_results
    proposal['status'] = 'applied'
    if save_fn:
        save_fn()
    print()
    return proposal


def main():
    queue = state.load_queue()
    pending = [p for p in queue if p.get('status') == 'pending']
    if not pending:
        print('No pending changes to review.')
        return

    new_entity_pending = [p for p in pending if p.get('proposal_type') == 'new_entity']
    update_pending = [p for p in pending if p.get('proposal_type') != 'new_entity']

    client = KankaClient()
    print('Loading current entity index for relation resolution...')
    index = build_entity_index(client)
    name_to_id = {data['name']: eid for eid, data in index.items()}

    def save_fn():
        state.save_queue(queue)

    # New entities first: approving one here makes it available as a
    # relation target for the "update" proposals reviewed right after.
    if new_entity_pending:
        print(colors.bold(f'\n{len(new_entity_pending)} new entity suggestion(s) to review first.'))
        for i, proposal in enumerate(new_entity_pending, start=1):
            try:
                review_new_entity_proposal(proposal, index, name_to_id, client, save_fn=save_fn)
                print(colors.dim(f'  [{i}/{len(new_entity_pending)}]'))
            except Exception as e:
                # Defense in depth on top of the per-relation/per-call
                # try/excepts already inside these functions: if something
                # still slips through, don't let it abort the rest of the
                # session and silently drop everything decided so far.
                print(colors.red(f"\n  ! Unexpected error reviewing '{proposal.get('entity_name')}': {e}"))
                print(colors.red('    Leaving its status as-is and continuing with the rest.'))

    reviewable = []
    skipped = 0
    for proposal in update_pending:
        if has_meaningful_change(proposal):
            reviewable.append(proposal)
        else:
            proposal['status'] = 'no_change'
            state.save_queue(queue)
            skipped += 1

    if skipped:
        print(
            colors.dim(
                f'\nAuto-skipped {skipped} proposal(s) with no real change '
                f'(synopsis identical, no relationship changes).'
            )
        )

    total_updates = len(reviewable)
    for i, proposal in enumerate(reviewable, start=1):
        try:
            review_proposal(
                proposal, index, name_to_id, client, update_num=i, total_updates=total_updates, save_fn=save_fn
            )
        except Exception as e:
            print(colors.red(f"\n  ! Unexpected error reviewing '{proposal.get('entity_name')}': {e}"))
            print(
                colors.red(
                    '    Leaving its status as-is and continuing with the rest. If '
                    'Kanka was already changed for this one before the error, check '
                    "it manually -- it won't be safe to just re-approve next run."
                )
            )

    state.save_queue(queue)

    # Scope this to what was actually decided *in this run* -- filtering the
    # whole queue by status would re-include every change ever applied in
    # past runs too, since their status stays "applied" forever.
    this_run = new_entity_pending + reviewable
    applied_this_run = [p for p in this_run if p['status'] == 'applied']
    rejected_this_run = sum(1 for p in this_run if p['status'] == 'rejected')
    state.log_applied_batch(applied_this_run)

    print(
        '\n' + colors.bold('Done.') + f' {colors.green(str(len(applied_this_run)) + " applied")}, '
        f'{colors.red(str(rejected_this_run) + " rejected")}, '
        f'{colors.dim(str(skipped) + " auto-skipped (no real change)")}.'
    )


if __name__ == '__main__':
    main()
