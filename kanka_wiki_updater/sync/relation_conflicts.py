"""Detect and resolve conflicts in proposed relation changes."""

from __future__ import annotations

import sys
from pathlib import Path

# Lazy-import RelationTypeTracker to avoid circular imports.
# The tracker is only needed when detect_unknown_types() or the enhanced
# apply_resolutions() is called with a tracker argument.
def _get_tracker():
    """Return a lazily-loaded RelationTypeTracker instance."""
    from .relation_types import RelationTypeTracker  # noqa: F811
    return RelationTypeTracker()


def _find_target_entity_id(entity_index, target_name):
    """Find entity_id for a given name, or None."""
    for eid, edata in entity_index.items():
        if edata['name'] == target_name:
            return eid
    return None


def resolve_creates_to_updates(proposals, entity_index):
    """Convert 'create' actions to 'update' when a prior relation exists.

    For each proposal, walks its relation_changes. If an action is "create"
    and the owner->target pair already has a relation in entity_index, converts
    the action to "update". When the existing label differs from the proposed
    one, attaches a conflict dict under ``rc["conflict"]``.

    Returns (resolved_proposals, conflicts) where each conflict is a dict with
    keys: proposal_idx, entity_name, target_name, existing_type, proposed_type,
    conflict_kind (always "label_mismatch" here).
    """
    resolved = []
    conflicts = []

    for idx, proposal in enumerate(proposals):
        owner_id = proposal.get('entity_id')
        if not owner_id:
            resolved.append(proposal)
            continue

        owner_data = entity_index.get(owner_id)
        owner_rels = (owner_data or {}).get('relations', [])

        new_rcs = []
        for rc in list(proposal.get('relation_changes', [])):
            action = (rc.get('action') or '').strip().lower()
            if action == 'create':
                target_name = rc['target_name']
                target_id = _find_target_entity_id(entity_index, target_name)

                existing_rel = None
                for rel in owner_rels:
                    if rel.get('target_id') == target_id:
                        existing_rel = rel
                        break

                if existing_rel is not None:
                    rel_label = existing_rel.get('relation', '')
                    new_rc = dict(rc)
                    new_rc['action'] = 'update'
                    if rc['relation'] != rel_label:
                        conflict = {
                            'proposal_idx': idx,
                            'entity_name': proposal['entity_name'],
                            'target_name': target_name,
                            'existing_type': rel_label,
                            'proposed_type': rc['relation'],
                            'conflict_kind': 'label_mismatch',
                        }
                        new_rc['conflict'] = conflict
                        conflicts.append(conflict)
                    else:
                        new_rc['conflict'] = None
                    new_rcs.append(new_rc)
                else:
                    new_rcs.append(dict(rc))
            else:
                new_rcs.append(dict(rc))

        resolved.append({**proposal, 'relation_changes': new_rcs})

    return resolved, conflicts


def detect_cross_proposal_conflicts(proposals):
    """Detect competing proposals for the same owner→target entity pair.

    Scans remaining 'create' actions across all proposals for duplicate
    (entity_name, target_name) pairs. The second occurrence of a pair is
    flagged as a cross_proposal conflict.

    Returns list of conflict dicts with keys: proposal_idx, entity_name,
    target_name, existing_type (None), proposed_type, conflict_kind="cross_proposal".
    """
    seen_pairs = {}  # (entity_name, target_name) -> first proposal_idx
    conflicts = []

    for idx, proposal in enumerate(proposals):
        for rc in proposal.get('relation_changes', []):
            action = (rc.get('action') or '').strip().lower()
            if action != 'create':
                continue

            entity_name = proposal['entity_name']
            target_name = rc['target_name']
            pair_key = (entity_name, target_name)

            if pair_key in seen_pairs:
                conflicts.append(
                    {
                        'proposal_idx': idx,
                        'entity_name': entity_name,
                        'target_name': target_name,
                        'existing_type': None,
                        'proposed_type': rc['relation'],
                        'conflict_kind': 'cross_proposal',
                    }
                )
            else:
                seen_pairs[pair_key] = idx

    return conflicts


def detect_unknown_types(proposals, relation_tracker=None):
    """Flag create/update actions using labels not in the known type set.

    Parameters
    ----------
    proposals : list[dict]
        The proposal queue (may already be resolved by resolve_creates_to_updates).
    relation_tracker : RelationTypeTracker | str | None, optional
        A tracker instance, a data_dir path string, or None. When None, no
        unknown-type detection is performed.

    Returns
    -------
    list[dict]
        Conflict dicts with keys: proposal_idx, entity_name, target_name,
        proposed_type, similar_types (list[str]), conflict_kind="unknown_relation_type".
    """
    if relation_tracker is None:
        return []

    # Accept either a tracker instance or a data_dir path string
    if isinstance(relation_tracker, str):
        from .relation_types import RelationTypeTracker  # noqa: F811
        relation_tracker = RelationTypeTracker(data_dir=relation_tracker)
        relation_tracker.load()

    conflicts = []
    for idx, proposal in enumerate(proposals):
        for rc in proposal.get('relation_changes', []):
            action = (rc.get('action') or '').strip().lower()
            if action not in ('create', 'update'):
                continue

            label = (rc.get('relation') or '').strip()
            if not label or relation_tracker.is_known(label):
                continue

            conflicts.append(
                {
                    'proposal_idx': idx,
                    'entity_name': proposal['entity_name'],
                    'target_name': rc['target_name'],
                    'existing_type': None,
                    'proposed_type': label,
                    'similar_types': relation_tracker.suggest_similar(label),
                    'conflict_kind': 'unknown_relation_type',
                }
            )
    return conflicts


def apply_resolutions(proposals, entity_index, relation_tracker=None):
    """Convenience wrapper: resolve creates-to-updates then detect all conflict types.

    Parameters
    ----------
    proposals : list[dict]
        The proposal queue.
    entity_index : dict
        The full entity index (for label_mismatch detection).
    relation_tracker : RelationTypeTracker | str | None, optional
        If provided, also detects unknown_relation_type conflicts.

    Returns
    -------
    tuple[list[dict], list[dict]]
        (resolved_proposals, all_conflicts) where all_conflicts contains
        label_mismatch, cross_proposal, and optionally unknown_relation_type dicts.
    """
    resolved, conflicts = resolve_creates_to_updates(proposals, entity_index)
    cross_conflicts = detect_cross_proposal_conflicts(resolved)
    unknown_conflicts = detect_unknown_types(resolved, relation_tracker) if relation_tracker else []
    return resolved, conflicts + cross_conflicts + unknown_conflicts
