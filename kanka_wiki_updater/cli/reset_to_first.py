#!/usr/bin/env python3
"""Reset all entities on Kanka back to their first recorded previous_entry.

Reads data/pending_changes.json, deduplicates by entity name (first occurrence
wins), and PATCHes each unique entity's synopsis back to its earliest recorded
previous_entry. This is a nuclear undo — it overwrites current Kanka state with
the oldest version of each entity found in the pending queue.

New-entity suggestions without previous_entry or entity_id are skipped silently.

Usage:
    python -m kanka_wiki_updater.cli.reset_to_first [--dry-run]
"""

import json
import sys
from pathlib import Path

if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from ..core import colors, config
    from ..core.kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater.core import colors, config
    from kanka_wiki_updater.core.kanka_client import KankaClient

_QUEUE_FILE = str(Path(config.DATA_DIR) / 'pending_changes.json')


def _kind_map():
    return {
        'character': 'characters',
        'location': 'locations',
        'organization': 'organisations',
        'creature': 'creatures',
    }


def main(dry_run: bool = False, auto_confirm: bool = False):
    if not Path(_QUEUE_FILE).exists():
        print(colors.red('No pending changes file found. Run sync first.'))
        return
    with open(_QUEUE_FILE, encoding='utf-8') as f:
        queue = json.load(f)

    # Deduplicate by entity name — first occurrence wins (earliest in file order).
    seen = {}
    for entry in queue:
        name = entry.get('entity_name', '')
        if not name or name in seen:
            continue
        prev = entry.get('previous_entry')
        eid = entry.get('entity_id')
        local_id = entry.get('entity_local_id')
        kind = entry.get('entity_kind')

        # Skip new_entity suggestions — no previous_entry, no entity_id to reset.
        if not prev or not eid or not local_id or not kind:
            continue

        seen[name] = {
            'entity_name': name,
            'entity_id': eid,
            'entity_local_id': local_id,
            'entity_kind': kind,
            'previous_entry': prev,
            'source_journal': entry.get('source_journal', '?'),
        }

    if not seen:
        print(colors.red('No entities to reset. The queue is empty or has no valid entries.'))
        return

    # Group by Kanka API endpoint kind for cleaner output.
    by_kind = {}
    for _name, entry in sorted(seen.items()):
        by_kind.setdefault(entry['entity_kind'], []).append(entry)

    print(colors.bold(f'Reset preview — {len(seen)} unique entity(s):'))
    for kind in sorted(by_kind.keys()):
        entries = by_kind[kind]
        print(f'\n  {kind.upper()} ({len(entries)})')
        for e in entries:
            pe_preview = (e['previous_entry'] or '')[:100].replace('<br>', ' ').replace('\n', ' ')
            suffix = '...' if len(e.get('previous_entry') or '') > 100 else ''
            print(
                f'    - {colors.cyan(e["entity_name"])} '
                f'(id={e["entity_local_id"]}, entity_id={e["entity_id"]})\n'
                f'      previous_entry: {colors.dim(pe_preview + suffix)}'
            )

    if dry_run:
        # In dry-run mode, just show what would happen and exit.
        return

    if not auto_confirm:
        confirm = input(colors.cyan('\nReset all of these on Kanka? [y/n] ')).strip().lower()
        if confirm != 'y':
            print('Cancelled — nothing was changed.')
            return

    client = KankaClient()
    kind_map = _kind_map()
    reset_count = 0
    skip_count = 0
    fail_count = 0

    for name, entry in sorted(seen.items()):
        api_kind = kind_map.get(entry['entity_kind'])
        if not api_kind:
            print(colors.yellow(f"  ! Skipping {name}: unknown kind '{entry['entity_kind']}'"))
            skip_count += 1
            continue

        try:
            client.update_entity_entry(api_kind, entry['entity_local_id'], entry['previous_entry'])
            reset_count += 1
            if dry_run:
                print(colors.green(f'  [DRY-RUN] Would reset {name} to previous state.'))
            else:
                print(colors.green(f'  - Reset {colors.cyan(name)} ({entry["entity_kind"]})'))
        except Exception as e:
            fail_count += 1
            print(colors.red(f'  ! Failed to reset {colors.cyan(name)}: {e}'))

    print(
        f'\nDone. '
        f'{colors.green(str(reset_count))} reset, '
        f'{colors.yellow(str(skip_count))} skipped, '
        f'{colors.red(str(fail_count))} failed.'
    )

    if not dry_run and not auto_confirm:
        confirm = input(colors.cyan('\nDelete stored data files? [y/n] ')).strip().lower()
        if confirm == 'y':
            _delete_state_files()


def _delete_state_files():
    """Remove all four state files from DATA_DIR. Silently skip any that don't exist."""
    import os as _os

    deleted = []
    for filename in ('sync_state.json', 'pending_changes.json',
                     'applied_log.json', 'processed_journals.json'):
        path = config.DATA_DIR / filename
        if _os.path.exists(path):
            _os.remove(path)
            deleted.append(filename)
    if deleted:
        print(colors.green(f'  Deleted {len(deleted)} state file(s): {", ".join(deleted)}'))
    else:
        print(colors.yellow('  No data files found to delete.'))


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    main(dry_run=dry_run)
