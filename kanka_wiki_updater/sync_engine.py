"""Shared sync engine: apply proposals to Kanka.io.

This module contains all business logic for pushing proposals (new entity
creation, synopsis updates, relation changes) back to the Kanka API. It is
importable standalone with a mock client for unit testing — no Flask or CLI
dependencies.

Usage
-----
    from .sync_engine import apply_proposal, resolve_entity

Both functions take explicit ``entity_index_cache`` parameters (a tuple of
 ``(index_dict, name_map)`` or an empty dict to trigger a fresh build). No
module-level globals are used.
"""

import difflib
import re as _re
import sys
from pathlib import Path

_DEBUG = bool(__import__('os').environ.get('KANKA_DEBUG'))


def _debug(*args):
    if _DEBUG:
        print('[SYNC-ENGINE DEBUG]', *args, file=sys.stderr)


if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# These imports are intentionally loaded for side-effects so that tests can
# mock them at module level (same pattern as review_web.py).  The except-
# branch provides a fallback when the module is imported directly rather
# than via ``python -m``.
try:
    from . import config as pkg_config
except ImportError:
    from kanka_wiki_updater import config as pkg_config

try:
    from .kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater.kanka_client import KankaClient

try:
    from .sync_pipeline import build_entity_index
except ImportError:
    from kanka_wiki_updater.sync_pipeline import build_entity_index


# ---------------------------------------------------------------------------
# Relation helpers (extracted — used by both review_web.py and review.py)
# ---------------------------------------------------------------------------


def _rel_target(rel):
    """Extract the target_id from a Relation model or dict."""
    return getattr(rel, 'target_id', None) or (rel.get('target_id') if isinstance(rel, dict) else None)


def _rel_owner(rel):
    """Extract the owner_id from a Relation model or dict."""
    return getattr(rel, 'owner_id', None) or (rel.get('owner_id') if isinstance(rel, dict) else None)


def _rel_id(rel):
    """Extract the id from a Relation model or dict."""
    return getattr(rel, 'id', None) or (rel.get('id') if isinstance(rel, dict) else None)


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------

_WIKI_LINK_RE = _re.compile(
    r'\[(?:entity|character|location|organisation|monster|deity|background|class|subrace|race):(\d+)',
)


def resolve_entity(client, name, entity_index_cache):
    """Resolve an entity name to its entity_id.

    Resolution strategy (in order):
      1. Check *entity_index_override* if present for exact match on newly-
         created entities in the same batch.
      2. Parse Kanka wiki links like ``[organisation:9419438|Zhentarim]`` to
         extract a numeric entity_id and validate it against the index.
      3. Exact substring match (case-insensitive).
      4. Fuzzy match via difflib.get_close_matches.

    Parameters
    ----------
    client : KankaClient
        API client (used only if cache is empty to build a fresh index).
    name : str
        The entity name or wiki link to resolve.
    entity_index_cache : dict | tuple
        A ``(index_dict, name_map)`` tuple from a previous call, or an empty
        dict ``{}`` to trigger a fresh index build.

    Returns
    -------
    int | None
        The Kanka entity_id, or None if resolution failed.
    """
    _debug(f'@@@ resolve_entity CALLED: {name!r} cache_has={bool(entity_index_cache)} @@@')

    # --- 1. Check override map (newly-created entities in same batch) ------
    needle = name.strip().lower()
    override_map = (entity_index_cache.get('_override', {}) if isinstance(entity_index_cache, dict) else {})
    for n, eid in override_map.items():
        if n == needle:
            _debug(f'  override match: {name!r} -> {eid}')
            return eid

    # --- 2. Parse wiki link to extract entity_id ----------------------------
    wiki_match = _WIKI_LINK_RE.search(name)
    if wiki_match:
        candidate_eid = int(wiki_match.group(1))
        _debug(f"    parsed wiki link entity_id={candidate_eid} from '{name}'")
        index, name_map = _get_or_build_index(client, entity_index_cache)
        if candidate_eid in index:
            _debug(f"    wiki link eid found in index -> '{index[candidate_eid]['name']}'")
            return candidate_eid

    # --- 3. Exact / substring / fuzzy match via cached/built index ----------
    index, name_map = _get_or_build_index(client, entity_index_cache)
    _debug(f"    resolving '{name}' (needle={needle!r}), index has {len(index)} entities")

    # Exact match via pre-built map — always wins over fuzzy/substring
    if needle in name_map:
        eid = name_map[needle]
        _debug(f"    exact match found: '{index[eid]['name']}' -> eid={eid}")
        return eid

    # Substring fallback (case-insensitive)
    candidates = [data['name'] for data in index.values() if needle in data['name'].lower()]
    _debug(f"    substring candidates for '{name}': {candidates[:5]}")
    if len(candidates) == 1:
        eid = next(eid for eid, data in index.items() if data['name'] == candidates[0])
        _debug(f"    single substring match: '{candidates[0]}' -> eid={eid}")
        return eid

    # Fuzzy fallback — try Levenshtein distance via difflib
    entity_names = list(index.values())
    matches = difflib.get_close_matches(
        needle, [d['name'].lower() for d in entity_names], n=5, cutoff=0.7
    )
    _debug(f'    difflib candidates: {[d["name"] for d in entity_names[:10]]}')
    if matches:
        _debug(f"    fuzzy match candidates for '{name}': {matches[:3]}")
        for eid, data in index.items():
            if data['name'].lower() == matches[0]:
                _debug(f"    fuzzy resolved '{name}' -> '{data['name']}' (eid={eid})")
                return eid

    if not candidates and not matches:
        _debug(
            f"    no match at all for '{name}' — index has {len(index)} entities, "
            f'names include: {[d["name"] for d in entity_names[:10]]}'
        )

    return None


def _get_or_build_index(client, entity_index_cache):
    """Return ``(index_dict, name_map)`` from cache or build fresh."""
    if isinstance(entity_index_cache, dict):
        # Cache is empty — build a fresh index
        index = build_entity_index(client)
        name_map = {}
        for eid, data in index.items():
            name_map[data['name'].strip().lower()] = eid
        _debug(f'  built entity index with {len(index)} entities')
        return index, name_map

    # Cache is a (index, name_map) tuple — use it directly
    return entity_index_cache


# ---------------------------------------------------------------------------
# Main entry point: apply_proposal
# ---------------------------------------------------------------------------


def apply_proposal(client, proposal, entity_index_cache):
    """Apply a single proposal to Kanka.io.

    Handles both ``new_entity`` (create in Kanka) and ``update`` (revise
    synopsis + relations for an existing entity).  Relation changes are
    processed after the synopsis so that approved new entities become valid
    relation targets within the same batch.

    Parameters
    ----------
    client : KankaClient
        An authenticated API client.
    proposal : dict
        A pending-change proposal entry (as stored in ``pending_changes.json``).
    entity_index_cache : dict | tuple
        Entity resolution cache — see :func:`resolve_entity`.  Must be an empty
        dict to trigger a fresh index build, or a ``(index, name_map)`` tuple.

    Returns
    -------
    dict
        ``{'ok': bool, 'message': str, 'warnings': list[str]}``
    """
    ptype_label = proposal.get('proposal_type', '?')
    ename = proposal.get('entity_name', '?')
    _debug(f'=== SYNC apply_proposal: type={ptype_label}, entity={ename} ===')
    _debug(f'  raw proposal keys: {list(proposal.keys())}')

    details = []
    errors = []
    warnings = []

    # --- Build / warm cache if needed (always fresh per call) -------------
    override_map = {}
    if isinstance(entity_index_cache, dict):
        entity_index_cache = _get_or_build_index(client, entity_index_cache)

    def _resolve(name):
        """Thin wrapper that also updates the override map for newly-created entities."""
        result = resolve_entity(client, name, entity_index_cache)
        return result

    # --- 1. Synopsis / new-entity creation ---------------------------------
    try:
        ptype = proposal.get('proposal_type')

        if ptype == 'new_entity':
            _debug(f'  creating {ptype}: suggested_type={proposal.get("suggested_type")!r}')
            entity_type = proposal.get('suggested_type', 'character')
            kind_param_map = {
                'character': 'characters',
                'location': 'locations',
                'organization': 'organisations',
                'creature': 'creatures',
            }
            kind_param = kind_param_map.get(entity_type, 'characters')

            result = getattr(client, f'create_{entity_type}')(
                proposal['entity_name'], entry=proposal.get('draft_entry', '')
            )
            data = result.get('data', {}) if isinstance(result, dict) else {}
            new_entity_id = data.get('entity_id')
            _debug(f'  create response: {result}')

            # Record what was actually created (revert.py needs this)
            proposal['created_local_id'] = data.get('id')
            proposal['created_kind'] = entity_type
            proposal['created_entity_id'] = new_entity_id

            details.append(f"Created {entity_type} '{proposal['entity_name']}'")
            if new_entity_id:
                details.append(f' (entity_id={new_entity_id})')
                # Make immediately available as relation target for later proposals in same batch
                override_map[proposal['entity_name'].strip().lower()] = new_entity_id
            else:
                errors.append(
                    f"Created entity but couldn't read entity_id from response. "
                    f'Raw response: {result}'
                )

        elif ptype == 'update':
            kind_map = {
                'character': 'characters',
                'location': 'locations',
                'organization': 'organisations',
                'creature': 'creatures',
            }
            kind_param = kind_map.get(proposal['entity_kind'], 'characters')
            _debug(
                f"  updating {kind_param}/{proposal['entity_local_id']} "
                f"for '{proposal['entity_name']}'"
            )
            client.update_entity_entry(
                kind_param,
                proposal['entity_local_id'],
                proposal['proposed_entry'],
            )
            details.append(f"Updated synopsis for '{proposal['entity_name']}'")

    except Exception as e:
        _debug(f'  SYNC EXCEPTION (synopsis): type={type(e).__name__} err={e}')
        import traceback as tb_mod
        _debug(f'    full traceback:\n{tb_mod.format_exc()}')
        errors.append(f'Sync error: {e}')

    # --- 2. Relation changes -----------------------------------------------
    rel_changes = proposal.get('relation_changes', [])
    if rel_changes:
        _debug(f'  relation_changes count={len(rel_changes)}')
        for i, rc in enumerate(rel_changes):
            _debug(
                f'    [{i}] action={rc.get("action")!r} target={rc.get("target_name")!r} '
                f'relation={rc.get("relation")!r} attitude={rc.get("attitude")!r}'
            )

        try:
            # Determine the entity_id to modify relations on
            if proposal.get('proposal_type') == 'new_entity':
                entity_id_str = str(proposal.get('created_entity_id', ''))
                _debug(f'  new_entity: created_entity_id_raw={entity_id_str!r}')
                if not entity_id_str or not entity_id_str.isdigit():
                    errors.append(
                        'Cannot resolve relations for new entity: no entity_id available. '
                        'Approve the synopsis first, then retry relations.'
                    )
                else:
                    entity_id = int(entity_id_str)
            else:
                eid = proposal.get('entity_id')
                _debug(f'  update: entity_id from proposal={eid!r}')
                if not eid:
                    eid = _resolve(proposal['entity_name'])
                    _debug(f"  resolved name->id for '{proposal['entity_name']}': {eid!r}")
                    if not eid:
                        errors.append(
                            f"Cannot find entity_id for '{proposal['entity_name']}'. "
                            'Ensure the entity exists in Kanka.'
                        )
                if eid is not None:
                    entity_id = int(eid)

            # Only proceed with relations if we have a valid entity_id and no fatal errors
            fatal_errors = [e for e in errors if 'Cannot resolve' in e or 'Cannot find entity_id' in e]
            if not fatal_errors and 'entity_id' in locals():
                _debug(f'  fetching existing relations for entity_id={entity_id}')
                existing_relations = client.get_relations(entity_id)
                _debug(f'  existing_relations ({len(existing_relations)} total):')
                for ri, er in enumerate(existing_relations):
                    rel_name = (
                        er.get('relation') if isinstance(er, dict) else getattr(er, 'relation', None)
                    )
                    _debug(
                        f'    [{ri}] target_id={_rel_target(er)!r} '
                        f'owner_id={_rel_owner(er)!r} id={_rel_id(er)!r} relation={rel_name!r}'
                    )

                for rc in rel_changes:
                    target_name = rc['target_name']
                    action = (rc.get('action') or '').strip().lower()

                    if action == 'delete':
                        _debug(f'  DELETE relation -> {target_name}')
                        target_entity_id = _resolve(target_name)
                        _debug(f"    resolved target '{target_name}' -> entity_id={target_entity_id!r}")
                        if not target_entity_id:
                            warnings.append(
                                f'Skipped delete relation -> {target_name}: entity not found in Kanka.'
                            )
                            continue

                        existing = next(
                            (r for r in existing_relations if _rel_target(r) == target_entity_id), None
                        )
                        _debug(f'    existing relation lookup: {existing is not None}')
                        if existing and _rel_id(existing):
                            rid = _rel_id(existing)
                            _debug(f'    calling delete_relation eid={entity_id} rid={rid}')
                            client.delete_relation(entity_id, rid)
                            details.append(f'Deleted relation -> {target_name}')
                        elif existing:
                            errors.append(
                                f"Cannot delete relation -> {target_name}: API did not return a "
                                f'relation id. Raw relation object: {existing}. '
                                f'Try deleting manually in Kanka.'
                            )
                        else:
                            details.append(
                                f"No existing relation to '{target_name}' found — "
                                'already removed or deleted externally.'
                            )

                    elif action in ('create', 'update'):
                        _debug(
                            f'  {action.upper()} relation -> {target_name} '
                            f'(relation={rc.get("relation")!r})'
                        )
                        target_entity_id = _resolve(target_name)
                        if target_entity_id:
                            _debug(f"    resolved target '{target_name}' -> entity_id={target_entity_id}")
                        else:
                            _debug(
                                f"    FAILED to resolve '{target_name}' — "
                                'entity not found in Kanka index'
                            )

                        if not target_entity_id:
                            warnings.append(
                                f'Skipped {action} relation -> {target_name}: entity not found in Kanka.'
                            )
                            continue

                        existing = next(
                            (r for r in existing_relations if _rel_target(r) == target_entity_id), None
                        )

                        # Detect reverse-direction relations with the same type.
                        # Kanka returns 409 if trying to create the exact same relation
                        # in the opposite direction (e.g., A 'commands' B and B 'commands' A).
                        proposed_relation = rc.get('relation', '').strip().lower()
                        has_reverse_same_type = any(
                            _rel_owner(r) == target_entity_id
                            and _rel_target(r) == entity_id
                            and (r.get('relation') if isinstance(r, dict)
                                 else getattr(r, 'relation', '')).strip().lower() == proposed_relation
                            for r in existing_relations
                        )
                        _debug(f'    has_reverse_same_type: {has_reverse_same_type}')

                        if has_reverse_same_type:
                            details.append(
                                f"Relation '{proposed_relation}' already exists between this entity "
                                f"and '{target_name}' (in the opposite direction — cannot create a duplicate link)."
                            )

                        elif action == 'create' or not existing:
                            _debug(f'    create_relation eid={entity_id} tid={target_entity_id}')
                            _debug(
                                f'    relation_name={rc.get("relation")!r}, attitude={rc.get("attitude")!r}'
                            )
                            try:
                                resp = client.create_relation(
                                    entity_id, target_entity_id, rc['relation'], rc.get('attitude')
                                )
                                _debug(
                                    f'    create_relation response keys: '
                                    f'{list(resp.keys()) if isinstance(resp, dict) else type(resp)}'
                                )
                                _debug(f'    create_relation response: {resp}')
                                details.append(f"Created relation -> {target_name}: '{rc['relation']}'")
                            except Exception as create_err:
                                import traceback as tb_mod
                                _debug(f'    create_relation ERR: {type(create_err).__name__}')
                                _debug(f'    full traceback:\n{tb_mod.format_exc()}')
                                status_code = getattr(create_err, 'response', None)
                                if hasattr(status_code, 'status_code'):
                                    errors.append(
                                        f"Failed to create relation -> {target_name}: "
                                        f'HTTP {status_code.status_code} — "{create_err}". '
                                        f'This usually means a relation already exists between these entities. '
                                        f'Check Kanka directly and delete any duplicate, then retry.'
                                    )
                                else:
                                    errors.append(f'Failed to create relation -> {target_name}: {create_err}')

                        elif existing and _rel_id(existing):
                            rid = _rel_id(existing)
                            _debug(
                                f'    update_relation eid={entity_id} rid={rid} '
                                f'rel={rc.get("relation")!r}'
                            )
                            try:
                                client.update_relation(
                                    entity_id,
                                    _rel_id(existing),
                                    relation=rc['relation'],
                                    attitude=rc.get('attitude'),
                                )
                                _debug('    update_relation succeeded')
                                details.append(f"Updated relation -> {target_name}: '{rc['relation']}'")
                            except Exception as update_err:
                                import traceback as tb_mod
                                _debug(f'    update_relation ERR: {type(update_err).__name__}')
                                _debug(f'    full traceback:\n{tb_mod.format_exc()}')
                                status_code = getattr(update_err, 'response', None)
                                if hasattr(status_code, 'status_code'):
                                    errors.append(
                                        f"Failed to update relation -> {target_name}: "
                                        f'HTTP {status_code.status_code} — "{update_err}". '
                                        f'This may mean the relation was modified externally. Check Kanka directly.'
                                    )
                                else:
                                    errors.append(f'Failed to update relation -> {target_name}: {update_err}')

                        elif existing and not _rel_id(existing):
                            errors.append(
                                f"Cannot update relation -> {target_name}: "
                                "API returned a relation without an 'id'. "
                                f'Raw: {existing}. Try updating manually in Kanka.'
                            )

        except Exception as e:
            import traceback as tb_mod
            _debug(f'  RELATION SYNC OUTER EXCEPTION: type={type(e).__name__} err={e}')
            _debug(f'    full traceback:\n{tb_mod.format_exc()}')
            # Capture HTTP error details for actionable feedback
            resp = getattr(e, 'response', None)
            if resp is not None:
                try:
                    body = resp.text[:500]
                except Exception:
                    body = '<unreadable>'
                _debug(f'    response status={getattr(resp, "status_code", "?")} body={body!r}')
            errors.append(f'Relation sync error: {e}')

    # --- 3. Assemble result ------------------------------------------------
    if errors:
        msg = '; '.join(errors)
        if warnings:
            msg += ' | Warnings: ' + '; '.join(warnings)
        return {'ok': False, 'message': msg, 'warnings': list(warnings)}

    result_msg = '; '.join(details)
    if warnings:
        result_msg += ' | Warnings: ' + '; '.join(warnings)
    return {'ok': True, 'message': result_msg, 'warnings': list(warnings)}
