#!/usr/bin/env python3
"""CLI entry point for kanka_wiki_updater.

Usage:
    python -m kanka_wiki_updater.cli revert     # Revert last applied batch
    python -m kanka_wiki_updater.cli reset      # Reset to first journal
    python -m kanka_wiki_updater.review.web     # Start web UI
    python -m kanka_wiki_updater.sync.ingest_journal  # Run sync pipeline

For the full CLI, install the package and use:
    kanka-wiki-updater <command> [args]
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog='kanka-wiki-updater',
        description='Kanka Wiki Updater - Sync Kanka journals to a wiki',
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # revert command
    rev_parser = subparsers.add_parser('revert', help='Revert the last applied batch')
    rev_parser.set_defaults(func=_cmd_revert)

    # reset command
    rst_parser = subparsers.add_parser('reset', help='Reset to first journal and re-sync everything')
    rst_parser.set_defaults(func=_cmd_reset)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


def _cmd_revert(_args):
    from kanka_wiki_updater.cli.revert import main as revert_main
    revert_main()


def _cmd_reset(_args):
    from kanka_wiki_updater.cli.reset_to_first import main as reset_main
    reset_main()


if __name__ == '__main__':
    main()
