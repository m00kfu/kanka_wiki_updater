"""Local state: last-sync timestamp and the pending-review queue.

Everything lives in plain JSON files under DATA_DIR so it's easy to inspect,
back up, or hand-edit if something looks wrong.
"""
import json
import os
from datetime import datetime, timezone
from . import config

os.makedirs(config.DATA_DIR, exist_ok=True)

SYNC_FILE = os.path.join(config.DATA_DIR, "sync_state.json")
QUEUE_FILE = os.path.join(config.DATA_DIR, "pending_changes.json")
APPLIED_LOG = os.path.join(config.DATA_DIR, "applied_log.json")
PROCESSED_FILE = os.path.join(config.DATA_DIR, "processed_journals.json")


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_last_sync():
    return _load(SYNC_FILE, {}).get("lastSync")


def set_last_sync(value):
    _save(SYNC_FILE, {"lastSync": value})


def load_queue():
    return _load(QUEUE_FILE, [])


def save_queue(queue):
    _save(QUEUE_FILE, queue)


def append_to_queue(items):
    queue = load_queue()
    queue.extend(items)
    save_queue(queue)


def log_applied_batch(entries):
    """Record one full batch of changes applied by a single `review` run,
    tagged with a run_id, so revert.py can undo exactly 'the most recent
    review run' instead of guessing at boundaries in a flat list."""
    if not entries:
        return
    log = _load(APPLIED_LOG, [])
    log.append({
        "run_id": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
        "reverted": False,
    })
    _save(APPLIED_LOG, log)


def get_last_applied_batch():
    """The most recent not-yet-reverted batch, or None if there isn't one --
    either nothing has been applied yet, the most recent run was already
    reverted, or it predates this batch-tracking format (applied with an
    older version of review.py) and so isn't recorded in enough detail to
    revert automatically."""
    log = _load(APPLIED_LOG, [])
    for item in reversed(log):
        if isinstance(item, dict) and "entries" in item and "run_id" in item:
            if not item.get("reverted"):
                return item
            continue  # already reverted -- keep looking further back
        return None  # hit older, less-detailed log data; nothing further back helps
    return None


def mark_batch_reverted(run_id):
    log = _load(APPLIED_LOG, [])
    for item in log:
        if isinstance(item, dict) and item.get("run_id") == run_id:
            item["reverted"] = True
    _save(APPLIED_LOG, log)


def get_processed_journal_ids():
    """Journal IDs already turned into proposals, regardless of whether
    those proposals were later approved or rejected. Used so an interrupted
    or re-run sync doesn't redo (and re-queue) the same journal twice."""
    return set(_load(PROCESSED_FILE, []))


def mark_journal_processed(journal_id):
    ids = _load(PROCESSED_FILE, [])
    if journal_id not in ids:
        ids.append(journal_id)
        _save(PROCESSED_FILE, ids)
