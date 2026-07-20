#!/usr/bin/env python3
"""Thin re-export module for backward compatibility.

All business logic has been extracted to ingest_journal.py. This module
re-exports the public API so existing imports from sync_pipeline continue
to work, and provides the CLI entry point that delegates to the new engine.

Usage:
    ./kanka_wiki_updater/sync_pipeline.py [--limit N]
    python -m kanka_wiki_updater.sync_pipeline [--limit N]
"""

import argparse
import sys
from pathlib import Path

if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Re-export all public symbols from ingest_journal for backward compatibility
from .ingest_journal import (  # noqa: F401,E402
    apply_relation_changes_locally,
    build_entity_index,
    find_mentioned_entities,
    journal_sort_key,
    main,
    propose_new_entities,
    propose_update,
    run_ingest,
)

# Re-export additional symbols that tests import directly from sync_pipeline
from .ingest_journal import _is_known_entity  # noqa: F401,E402
from .ingest_journal import relation_summary  # noqa: F401,E402
from .ingest_journal import chat_json  # noqa: F401,E402

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Sync new Kanka session journals into proposed wiki updates.')
    parser.add_argument(
        '--limit',
        type=int,
        default=__import__('kanka_wiki_updater.config').config.JOURNAL_BATCH_LIMIT,
        help='Max number of new journals to process this run, oldest-first. '
        'Defaults to JOURNAL_BATCH_LIMIT in .env, or unlimited if unset.',
    )
    args = parser.parse_args()
    main(limit=args.limit)
