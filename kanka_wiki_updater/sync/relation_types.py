"""Track known relation types per campaign.

This module provides :class:`RelationTypeTracker` — a lightweight, persistent
store for relation labels scraped from Kanka or approved by humans during review.
Persistence is via the shared SQLite database (``kanka_wiki_updater.db``).

Usage
-----
    tracker = RelationTypeTracker(data_dir='data')
    tracker.load()                       # load from DB (or empty)
    tracker.add_type('Blood Oath')       # after human approval
    tracker.save()                       # persist to DB
"""

from __future__ import annotations

import difflib
import os
import sys

# ---------------------------------------------------------------------------
# Default relation types — seeded on first run when no persistence exists
# ---------------------------------------------------------------------------

DEFAULT_RELATION_TYPES: frozenset[str] = frozenset({
    # Symmetric / bidirectional
    'ally', 'allies', 'enemy', 'enemies', 'rival', 'rivals',
    'nemesis', 'nemesises', 'friend', 'friends', 'foe', 'foes',
    'opponent', 'opponents', 'comrade', 'comrades',
    'sibling', 'siblings', 'spouse', 'spouses',
    'partner', 'partners', 'coworker', 'coworkers',
    'teammate', 'teammates',

    # Asymmetric / inverse pairs (both directions seeded)
    'parent', 'child', 'mother', 'father', 'son', 'daughter',
    'brother', 'sister', 'grandparent', 'grandchild',
    'uncle', 'aunt', 'nephew', 'niece',
    'master', 'servant', 'employer', 'employee',
    'teacher', 'student', 'mentor', 'mentee',
    'guardian', 'ward',

    # Additional common types
    'lover', 'fiancee', 'crush', 'ex', 'betrothed',
    'betrayer', 'traitor', 'assassin', 'kidnapper', 'slayer',
    'apprentice', 'patron', 'client', 'healer', 'bodyguard',
    'spy', 'informant', 'member of', 'leader of', 'founder',
    'lieutenant', 'subordinate',
    'blood oath', 'familiar', 'cursed by', 'blessed by',
    'owes favor', 'indebted to', 'rescued by',
})

# ---------------------------------------------------------------------------
# Symmetric relations & inverse mapping (used by reciprocal generation)
# ---------------------------------------------------------------------------

# Relations that are naturally bidirectional with the same label.
SYMMETRIC_RELATIONS = frozenset({
    'ally', 'allies', 'enemy', 'enemies', 'rival', 'rivals',
    'nemesis', 'nemesises', 'friend', 'friends', 'foe', 'foes',
    'opponent', 'opponents', 'comrade', 'comrades',
    'sibling', 'siblings', 'spouse', 'spouses',
    'partner', 'partners', 'coworker', 'coworkers',
    'teammate', 'teammates',
})

# Inverse mapping for asymmetric relation pairs.
# Key = label from owner→target; value = inverse label for target→owner.
INVERSE_RELATIONS: dict[str, str] = {
    # Family
    'parent': 'child',
    'child': 'parent',
    'mother': 'son',       # simplified — doesn't distinguish son/daughter
    'father': 'son',
    'son': 'father',
    'daughter': 'father',  # or could be 'parent' for gender-neutral
    'brother': 'sibling',
    'sister': 'sibling',
    'grandparent': 'grandchild',
    'grandchild': 'grandparent',
    'uncle': 'nephew/niece',
    'aunt': 'nephew/niece',
    'nephew': 'uncle/aunt',
    'niece': 'uncle/aunt',
    # Power/authority
    'master': 'servant',
    'servant': 'master',
    'employer': 'employee',
    'employee': 'employer',
    'teacher': 'student',
    'student': 'teacher',
    'mentor': 'mentee',
    'mentee': 'mentor',
    'guardian': 'ward',
    'ward': 'guardian',
    'captain': 'crew member',
    # Other
    'debtor': 'creditor',
    'creditor': 'debtor',
}


def is_symmetric_relation(label: str) -> bool:
    """Return True if *label* is a known symmetric relation type."""
    return label.strip().lower() in SYMMETRIC_RELATIONS


def get_inverse_label(label: str) -> str:
    """Return the inverse label for an asymmetric relation.

    Falls back to the same label (symmetric assumption) when no mapping exists.
    This is a safe default — most relations are symmetric or near-symmetric.

    Lookup is case-insensitive. When falling back to the original label,
    its casing is preserved; mapped values are returned as stored (lowercase).
    """
    stripped = label.strip()
    inverse = INVERSE_RELATIONS.get(stripped.lower(), stripped)
    return inverse


def _data_dir():
    """Return the data directory path."""
    try:
        from .core import config as pkg_config
        return pkg_config.DATA_DIR
    except ImportError:
        import kanka_wiki_updater.core.config as pkg_config  # type: ignore[import-not-found, no-redef]
        return pkg_config.DATA_DIR


class RelationTypeTracker:
    """Tracks known relation types per campaign."""

    def __init__(self, data_dir: str | None = None):
        self.known_types: dict[str, int] = {}  # label -> count
        self.data_dir = data_dir or _data_dir()
        self._last_scraped_at: float | None = None

    # ------------------------------------------------------------------
    # Persistence (SQLite)
    # ------------------------------------------------------------------

    def _import_db(self):
        """Lazy-import the core db module."""
        try:
            from ..core import db as core_db  # type: ignore[import-not-found]
        except ImportError:
            import kanka_wiki_updater.core.db as core_db  # type: ignore[import-not-found, no-redef]
        return core_db

    def _db_path(self) -> str:
        """Return the path to the SQLite DB for this tracker's data_dir."""
        return os.path.join(self.data_dir, 'kanka_wiki_updater.db')

    def _with_data_dir(self):
        """Temporarily set config.DATA_DIR to self.data_dir and init/close helpers.

        Returns a (core_db, saved_data_dir) tuple so the caller can restore it.
        """
        core_db = self._import_db()
        try:
            from ..core import config as pkg_config
        except ImportError:
            import kanka_wiki_updater.core.config as pkg_config  # type: ignore[import-not-found, no-redef]
        saved_data_dir = pkg_config.DATA_DIR
        pkg_config.DATA_DIR = self.data_dir
        return core_db, pkg_config, saved_data_dir

    def _restore_data_dir(self, config_module, saved_data_dir):
        """Restore the original DATA_DIR after a DB operation."""
        try:
            from ..core import db as core_db  # type: ignore[import-not-found]
        except ImportError:
            import kanka_wiki_updater.core.db as core_db  # type: ignore[import-not-found, no-redef]
        config_module.DATA_DIR = saved_data_dir
        core_db.close_all()

    def load(self) -> None:
        """Load from SQLite. Falls back to empty set on errors."""
        core_db, config_mod, saved_dd = self._with_data_dir()
        try:
            # Ensure tables exist (creates DB if missing)
            core_db.init_db()
            conn = core_db.connect()
            for row in conn.execute(
                'SELECT label, count FROM known_relation_types'
            ):
                self.known_types[row['label']] = row['count']
            meta_row = conn.execute(
                'SELECT value FROM meta WHERE key = ?', ('last_scraped_at',)
            ).fetchone()
            if meta_row:
                self._last_scraped_at = float(meta_row['value'])
        except Exception:  # sqlite3.DatabaseError, OSError, etc.
            pass
        finally:
            self._restore_data_dir(config_mod, saved_dd)

    def save(self) -> None:
        """Persist known_types + metadata to SQLite."""
        core_db, config_mod, saved_dd = self._with_data_dir()
        try:
            # Ensure tables exist (creates DB if missing)
            core_db.init_db()
            with core_db.transaction() as conn:
                # Delete-and-insert is simplest for this small table
                conn.execute('DELETE FROM known_relation_types')
                for label, count in self.known_types.items():
                    conn.execute(
                        'INSERT INTO known_relation_types (label, count) VALUES (?, ?)',
                        (label, count),
                    )
                if self._last_scraped_at is not None:
                    conn.execute(
                        'INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
                        ('last_scraped_at', str(self._last_scraped_at)),
                    )
        finally:
            self._restore_data_dir(config_mod, saved_dd)

    # ------------------------------------------------------------------
    # Lookup & suggestion
    # ------------------------------------------------------------------

    def is_known(self, label: str) -> bool:
        """Case-insensitive check if *label* matches any known type."""
        if not label:
            return False
        lower = label.strip().lower()
        return any(k.lower() == lower for k in self.known_types)

    def suggest_similar(self, label: str, n: int = 3) -> list[str]:
        """Fuzzy match against known types. Returns top-N by SequenceMatcher ratio > 0.6."""
        if not label or not self.known_types:
            return []
        needle = label.strip().lower()
        scored: list[tuple[float, str]] = []
        for k in self.known_types:
            ratio = difflib.SequenceMatcher(None, needle, k.lower()).ratio()
            if ratio > 0.6 and k.lower() != needle:
                scored.append((ratio, k))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [s[1] for s in scored[:n]]

    def get_sorted_labels(self, limit=None) -> list[str]:
        """Return known types sorted by usage frequency (most popular first).

        Parameters
        ----------
        limit : int or None
            Maximum number of labels to return.  ``None`` (default) returns all.
        """
        if not self.known_types:
            return []
        result = sorted(self.known_types, key=lambda k: (-self.known_types[k], k))
        if limit is not None:
            result = result[:limit]
        return result

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_type(self, label: str) -> None:
        """Register a new type (after human approval). Increments count."""
        if not label or not label.strip():
            return
        key = label.strip()
        self.known_types[key] = self.known_types.get(key, 0) + 1

    def merge_new_types(self, labels: list[str]) -> None:
        """Merge a batch of new relation type labels. Increments counts."""
        for label in labels:
            key = label.strip()
            if key:
                self.known_types[key] = self.known_types.get(key, 0) + 1

    def seed_defaults(self) -> bool:
        """Seed known_types with DEFAULT_RELATION_TYPES if currently empty.

        Returns True if seeding occurred, False if types were already present.
        Does NOT call save() — caller decides when to persist.
        """
        if self.known_types:
            return False  # already has data
        for label in DEFAULT_RELATION_TYPES:
            self.add_type(label)
        return True

    # ------------------------------------------------------------------
    # Backward-compatible enrichment
    # ------------------------------------------------------------------

    def enrich_proposals(self, proposals: list[dict]) -> list[dict]:
        """Enrich each proposal's relation_changes with _type_status + similar_types.

        Proposals that already have these fields are left unchanged (idempotent).
        This is called on load_queue() so the review UI always sees enriched data.
        """
        for proposal in proposals:
            rels = proposal.get('relation_changes') or []
            if not rels:
                continue
            new_rels = []
            for rc in rels:
                # Skip already-enriched entries (idempotent)
                if '_type_status' in rc and 'similar_types' in rc:
                    new_rels.append(rc)
                    continue

                enriched = dict(rc)
                label = (rc.get('relation') or '').strip()
                if not label or self.is_known(label):
                    enriched['_type_status'] = 'known'
                    enriched['similar_types'] = []
                else:
                    enriched['_type_status'] = 'new_suggested'
                    enriched['similar_types'] = self.suggest_similar(label)
                new_rels.append(enriched)
            proposal['relation_changes'] = new_rels

        return proposals

    # ------------------------------------------------------------------
    # Full Kanka scrape (Phase 3 — called later, not on by default)
    # ------------------------------------------------------------------

    def load_from_client(self, client) -> None:
        """Scrape ALL relations from every entity in the campaign.

        Fetches all entities (characters, locations, organizations, creatures),
        collects every unique ``relation`` label, and stores counts for ranking.
        """
        self.known_types = {}
        for kind, get_fn in (
            ('character', client.get_characters),
            ('location', client.get_locations),
            ('organization', client.get_organizations),
            ('creature', client.get_creatures),
        ):
            try:
                rows = get_fn()
            except Exception as e:
                print(f'  [relation_types] load_from_client: {kind}: ERROR — {e}', file=sys.stderr)
                continue
            for row in (rows or []):
                rels = row.get('relations') or []
                for rel in rels:
                    label = (rel.get('relation') or '').strip()
                    if label:
                        self.known_types[label] = self.known_types.get(label, 0) + 1

        import time as _time
        self._last_scraped_at = _time.time()
        self.save()


def ensure_seeded(data_dir: str | None = None) -> bool:
    """Seed default relation types only when the DB table is empty.

    If the table has rows, do nothing — respect that this campaign may have
    custom types.  A fresh install (table doesn't exist or has zero rows)
    gets seeded.

    Returns True if seeding was performed, False otherwise.
    Safe to call multiple times — idempotent.
    """
    tracker = RelationTypeTracker(data_dir=data_dir)
    core_db, config_mod, saved_dd = tracker._with_data_dir()
    try:
        # init_db creates the table; after that it always exists
        core_db.init_db()
        conn = core_db.connect()
        count_row = conn.execute('SELECT COUNT(*) AS c FROM known_relation_types').fetchone()
        if count_row['c'] == 0:
            tracker.seed_defaults()
            tracker.save()
            return True
        return False
    finally:
        tracker._restore_data_dir(config_mod, saved_dd)
