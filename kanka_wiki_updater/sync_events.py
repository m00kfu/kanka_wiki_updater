"""Shared callback event type constants for the sync pipeline.

These define the contract between any frontend (web, TUI, CLI) and
``ingest_journal.run_ingest()``.  Importable standalone — no Flask or web
dependencies.

Usage
-----
    from .sync_events import EVENT_ENTITY_PROGRESS, ENTITY_STATUSES

    callbacks = {
        'entity_started':       lambda e, j: ...,
        'llm_result':           lambda e, j, ok, data: ...,
        'proposal_queued':      lambda p: ...,
        'new_entity_suggestion':lambda s: ...,
        'journal_completed':    lambda j, n_ent, n_sug: ...,
        'sync_started':         lambda total_j, est_e: ...,
        'journal_entities_discovered': lambda j, names: ...,
    }
"""

# ── SSE event type constants ───────────────────────────────────────────────
EVENT_ENTITY_PROGRESS = 'entity_progress'
EVENT_PROPOSAL_PUSHED = 'proposal_pushed'
EVENT_STATUS_CHANGE   = 'status_change'
EVENT_SYNC_START      = 'sync_start'
EVENT_SYNC_COMPLETE   = 'sync_complete'

# Accepted entity progress statuses
ENTITY_STATUSES = ('pending', 'processing', 'done', 'skipped', 'error')
