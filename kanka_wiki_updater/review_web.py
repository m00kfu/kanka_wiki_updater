#!/usr/bin/env python3
"""Web-based review UI for Kanka Wiki Updater.

Launch with: ./kanka_wiki_updater/review_web.py or python -m kanka_wiki_updater.review_web

Serves a single-page web app at http://127.0.0.1:5555 that reads and writes
data/pending_changes.json — the same file used by review.py. Both can coexist
without conflict; they just need to agree on the JSON schema.
"""

import contextlib
import difflib
import json
import os
import subprocess
import sys
import threading
import time
import traceback as tb_mod
from collections import defaultdict, deque  # noqa: F401 (defaultdict needed for Task 2 SSE streaming)
from pathlib import Path

_DEBUG = bool(os.environ.get('KANKA_DEBUG'))


def _debug(*args):
    if _DEBUG:
        print('[DEBUG]', *args, file=sys.stderr)


if __name__ == '__main__' and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template, request  # noqa: E402

try:
    from . import config as pkg_config
except ImportError:
    from kanka_wiki_updater import config as pkg_config

# Import KankaClient at module level so tests can mock it
try:
    from .kanka_client import KankaClient
except ImportError:
    from kanka_wiki_updater.kanka_client import KankaClient

try:
    from .sync_pipeline import build_entity_index
except ImportError:
    from kanka_wiki_updater.sync_pipeline import build_entity_index
_sync_lock = threading.Lock()


def _rel_target(rel):
    """Extract the target_id from a Relation model or dict."""
    return getattr(rel, 'target_id', None) or (rel.get('target_id') if isinstance(rel, dict) else None)


def _rel_owner(rel):
    """Extract the owner_id from a Relation model or dict."""
    return getattr(rel, 'owner_id', None) or (rel.get('owner_id') if isinstance(rel, dict) else None)


def _rel_id(rel):
    """Extract the id from a Relation model or dict."""
    return getattr(rel, 'id', None) or (rel.get('id') if isinstance(rel, dict) else None)


_sync_jobs = {}
_job_counter = [0]
_sync_cancel_lock = threading.Lock()  # protects _sync_jobs mutations
_entity_index_cache = {}  # cached (index, name_to_id) for current review batch
_name_to_id_override = {}  # newly-created entities during this batch


def _next_job_id():
    """Generate a unique job ID."""
    _job_counter[0] += 1
    return f'sync-{_job_counter[0]}'


def create_app():
    """Create and configure the Flask application."""
    app = Flask(__name__)

    @app.route('/')
    def index():
        try:
            from . import config as pkg_config
        except ImportError:
            from kanka_wiki_updater import config as pkg_config
        queue = _load_queue()
        return render_template('index.html', PROPOSALS=queue, KANKA_CAMPAIGN_ID=pkg_config.KANKA_CAMPAIGN_ID)

    @app.route('/api/proposals')
    def get_proposals():
        queue = _load_queue()
        status_filter = request.args.get('status')
        type_filter = request.args.get('type')

        if status_filter:
            queue = [p for p in queue if p.get('status') == status_filter]
        if type_filter:
            queue = [p for p in queue if p.get('proposal_type') == type_filter]

        return jsonify(queue)

    @app.route('/api/proposals/<int:index>/status', methods=['POST'])
    def update_status(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        status_value = data.get('status')
        valid_statuses = ('approved_all', 'approved_synopsis_only', 'rejected')
        if status_value not in valid_statuses:
            return jsonify({'error': f'Invalid status. Must be one of {valid_statuses}'}), 400

        sync_result = None
        if status_value in ('approved_all', 'approved_synopsis_only'):
            with _sync_lock:
                sync_result = _sync_proposal_to_kanka(index)
            queue = _load_queue()  # Reload after potential modifications
            proposal = queue[index]
            if not sync_result['ok']:
                return jsonify(
                    {
                        'proposal': proposal,
                        'ok': False,
                        'sync_error': True,
                        'sync_message': sync_result['message'],
                    }
                ), 409

        mapping = {
            'approved_all': 'applied',
            'approved_synopsis_only': 'applied',
            'rejected': 'rejected',
        }
        queue[index]['status'] = mapping[status_value]
        _save_queue(queue)
        result = {'proposal': queue[index], 'ok': True}
        if sync_result:
            result['sync'] = sync_result
        return jsonify(result)

    @app.route('/api/proposals/<int:index>/edit', methods=['POST'])
    def edit_proposal(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        entry_text = data.get('entry', '')
        proposal = queue[index]

        if proposal['proposal_type'] == 'new_entity':
            proposal['draft_entry'] = entry_text
        else:
            proposal['proposed_entry'] = entry_text

        _save_queue(queue)
        return jsonify({'proposal': proposal, 'ok': True})

    @app.route('/api/proposals/<int:index>/relation', methods=['POST'])
    def update_relation(index):
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        data = request.get_json()
        action = (data.get('action') or '').strip().lower()
        target_name = data.get('target_name', '')
        proposal = queue[index]
        relations = proposal.get('relation_changes', [])

        if action == 'create':
            new_rel = {
                'action': 'create',
                'relation': data.get('relation', ''),
                'target_name': target_name,
                'attitude': data.get('attitude', ''),
                'reason': data.get('reason', ''),
            }
            relations.append(new_rel)

        elif action == 'delete':
            found = next((r for r in relations if r['target_name'] == target_name), None)
            if not found:
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404
            relations.remove(found)

        elif action == 'update':
            found = next((r for r in relations if r['target_name'] == target_name), None)
            if not found:
                return jsonify({'error': f"No relation to '{target_name}' found"}), 404
            found['relation'] = data.get('relation', found['relation'])
            found['attitude'] = data.get('attitude', found['attitude'])
            found['reason'] = data.get('reason', found['reason'])

        else:
            return jsonify({'error': f'Invalid action: {action}'}), 400

        proposal['relation_changes'] = relations
        _save_queue(queue)
        return jsonify({'proposal': proposal, 'ok': True})

    def _sync_proposal_to_kanka(idx):
        """Actually push a proposal to Kanka.io. Returns (success, details)."""
        with contextlib.suppress(ImportError):
            from .mentions import add_missing_entity_tags  # noqa: F401

        queue = _load_queue()
        if idx >= len(queue):
            return False, 'Proposal not found in queue'

        # Reset per-session caches so stale data doesn't persist across reviews
        global _entity_index_cache, _name_to_id_override
        _entity_index_cache = {}
        _name_to_id_override = {}

        proposal = queue[idx]
        client = KankaClient()
        details = []
        errors = []
        warnings = []

        ptype = proposal.get('proposal_type')
        pname = proposal.get('entity_name', '?')
        _debug(f'=== SYNC {idx}: type={ptype}, entity={pname} ===')
        _debug(f'  raw proposal keys: {list(proposal.keys())}')

        def resolve_name_to_id(client, name):
            """Resolve an entity name to its entity_id via a full index build."""
            global _entity_index_cache
            _debug(f'@@@ resolve_name_to_id CALLED: {name!r} cache_has={bool(_entity_index_cache)} @@@')
            try:
                # Check override map first (newly-created entities in this batch)
                needle = name.strip().lower()
                for n, eid in _name_to_id_override.items():
                    if n == needle:
                        return eid

                # Parse Kanka wiki links like [organisation:9419438|Zhentarim] or [entity:123]
                import re as _re

                wiki_match = _re.search(
                    r'\[(?:entity|character|location|organisation|monster|deity|background|class|subrace|race):(\d+)',
                    name,
                )
                if wiki_match:
                    # The number inside is a Kanka entity_id — look it up in the index to verify
                    candidate_eid = int(wiki_match.group(1))
                    _debug(f"    parsed wiki link entity_id={candidate_eid} from '{name}'")
                    # Build index if needed so we can validate the eid exists and get its kind
                    if not _entity_index_cache:
                        index = build_entity_index(client)
                        name_map = {}
                        for eid, data in index.items():
                            name_map[data['name'].strip().lower()] = eid
                        _entity_index_cache = (index, name_map)
                        _debug(f'  built entity index with {len(index)} entities')
                    index, name_map = _entity_index_cache
                    if candidate_eid in index:
                        _debug(f"    wiki link eid found in index -> '{index[candidate_eid]['name']}'")
                        return candidate_eid

                # Use cached index if available, else build fresh
                if not _entity_index_cache:
                    index = build_entity_index(client)
                    name_map = {}
                    for eid, data in index.items():
                        name_map[data['name'].strip().lower()] = eid
                    _entity_index_cache = (index, name_map)
                    _debug(f'  built entity index with {len(index)} entities')

                index, name_map = _entity_index_cache
                _debug(f"    resolving '{name}' (needle={needle!r})")

                # Exact match via pre-built map — always wins over fuzzy/substring
                if needle in name_map:
                    eid = name_map[needle]
                    _debug(f"    exact match found: '{index[eid]['name']}' -> eid={eid}")
                    return eid

                # Fuzzy fallback — try partial substring match (case-insensitive)
                candidates = [data['name'] for data in index.values() if needle in data['name'].lower()]
                _debug(f"    substring candidates for '{name}': {candidates[:5]}")
                if len(candidates) == 1:
                    _debug(f"    single substring match: '{candidates[0]}'")
                    return next(eid for eid, data in index.items() if data['name'] == candidates[0])

                # Fuzzy fallback — try Levenshtein distance via difflib
                entity_names = list(index.values())
                matches = difflib.get_close_matches(needle, [d['name'].lower() for d in entity_names], n=5, cutoff=0.7)
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
            except Exception as e:
                errors.append(f'Resolution warning: {e}')
            return None

        try:
            if proposal.get('proposal_type') == 'new_entity':
                entity_type = proposal.get('suggested_type', 'character')
                kind_param_map = {
                    'character': 'characters',
                    'location': 'locations',
                    'organization': 'organisations',
                    'creature': 'creatures',
                }
                kind_param = kind_param_map.get(entity_type, 'characters')
                _debug(f'  creating {entity_type}: name={proposal["entity_name"]!r}')
                result = getattr(client, f'create_{entity_type}')(
                    proposal['entity_name'], entry=proposal.get('draft_entry', '')
                )
                data = result.get('data', {}) if isinstance(result, dict) else {}
                new_entity_id = data.get('entity_id')
                _debug(f'  create response: {result}')
                proposal['created_local_id'] = data.get('id')
                proposal['created_kind'] = entity_type
                proposal['created_entity_id'] = new_entity_id
                details.append(f"Created {entity_type} '{proposal['entity_name']}'")
                if new_entity_id:
                    details.append(f' (entity_id={new_entity_id})')
                    # Make immediately available as relation target for later proposals in same batch
                    queue[idx]['_resolved'] = True
                    _name_to_id_override[proposal['entity_name'].strip().lower()] = new_entity_id
                else:
                    errors.append(f"Created entity but couldn't read entity_id from response. Raw response: {result}")

            elif proposal.get('proposal_type') == 'update':
                # Update synopsis entry — map entity_kind to API plural form
                kind_map = {
                    'character': 'characters',
                    'location': 'locations',
                    'organization': 'organisations',
                    'creature': 'creatures',
                }
                kind_param = kind_map.get(proposal['entity_kind'], 'characters')
                _debug(f"  updating {kind_param}/{proposal['entity_local_id']} for '{proposal['entity_name']}'")
                client.update_entity_entry(
                    kind_param,
                    proposal['entity_local_id'],
                    proposal['proposed_entry'],
                )
                details.append(f"Updated synopsis for '{proposal['entity_name']}'")

        except Exception as e:
            _debug(f'  SYNC EXCEPTION (synopsis): type={type(e).__name__} err={e}')
            _debug(f'    full traceback:\n{tb_mod.format_exc()}')
            errors.append(f'Sync error: {e}')

        # Handle relation changes (both new_entity and update)
        rel_changes = proposal.get('relation_changes', [])
        if rel_changes:
            _debug(f'  relation_changes count={len(rel_changes)}')
            for i, rc in enumerate(rel_changes):
                _debug(
                    f'    [{i}] action={rc.get("action")!r} target={rc.get("target_name")!r} '
                    f'relation={rc.get("relation")!r} attitude={rc.get("attitude")!r}'
                )
            try:
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
                        eid = resolve_name_to_id(client, proposal['entity_name'])
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
                        rel_name = er.get('relation') if isinstance(er, dict) else getattr(er, 'relation', None)
                        _debug(
                            f'    [{ri}] target_id={_rel_target(er)!r} '
                            f'owner_id={_rel_owner(er)!r} id={_rel_id(er)!r} relation={rel_name!r}'
                        )
                    for rc in rel_changes:
                        target_name = rc['target_name']
                        action = (rc.get('action') or '').strip().lower()

                        if action == 'delete':
                            _debug(f'  DELETE relation -> {target_name}')
                            target_entity_id = resolve_name_to_id(client, target_name)
                            _debug(f"    resolved target '{target_name}' -> entity_id={target_entity_id!r}")
                            if not target_entity_id:
                                warnings.append(f'Skipped delete relation -> {target_name}: entity not found in Kanka.')
                                continue
                            existing = next((r for r in existing_relations if _rel_target(r) == target_entity_id), None)
                            _debug(f'    existing relation lookup: {existing is not None}')
                            if existing and _rel_id(existing):
                                rid = _rel_id(existing)
                                _debug(f'    calling delete_relation eid={entity_id} rid={rid}')
                                client.delete_relation(entity_id, rid)
                                details.append(f'Deleted relation -> {target_name}')
                            elif existing:
                                errors.append(
                                    f'Cannot delete relation -> {target_name}: API did not return a relation id. '
                                    f'Raw relation object: {existing}. Try deleting manually in Kanka.'
                                )
                            else:
                                details.append(
                                    f"No existing relation to '{target_name}' found — "
                                    'already removed or deleted externally.'
                                )

                        elif action in ('create', 'update'):
                            _debug(f'  {action.upper()} relation -> {target_name} (relation={rc.get("relation")!r})')
                            target_entity_id = resolve_name_to_id(client, target_name)
                            if target_entity_id:
                                _debug(f"    resolved target '{target_name}' -> entity_id={target_entity_id}")
                            else:
                                _debug(f"    FAILED to resolve '{target_name}' — entity not found in Kanka index")
                            if not target_entity_id:
                                warnings.append(
                                    f'Skipped {action} relation -> {target_name}: entity not found in Kanka.'
                                )
                                continue

                            existing = next((r for r in existing_relations if _rel_target(r) == target_entity_id), None)

                            # Also detect reverse-direction relations with the same type.
                            # Kanka returns 409 if trying to create the exact same relation
                            # in the opposite direction (e.g., A 'commands' B and B 'commands' A).
                            # Different relation types between two entities are allowed.
                            proposed_relation = rc.get('relation', '').strip().lower()
                            has_reverse_same_type = any(
                                _rel_owner(r) == target_entity_id
                                and _rel_target(r) == entity_id
                                and (r.get('relation') if isinstance(r, dict) else getattr(r, 'relation', ''))
                                .strip()
                                .lower()
                                == proposed_relation
                                for r in existing_relations
                            )
                            _debug(f'    has_reverse_same_type: {has_reverse_same_type}')

                            if has_reverse_same_type:
                                details.append(
                                    f"Relation '{proposed_relation}' already exists between this entity and '{target_name}' "
                                    '(in the opposite direction — cannot create a duplicate link).'
                                )

                            elif action == 'create' or not existing:
                                _debug(f'    create_relation eid={entity_id} tid={target_entity_id}')
                                _debug(f'    relation_name={rc.get("relation")!r}, attitude={rc.get("attitude")!r}')
                                _debug(f'    action={action!r}, existing={existing is not None}')
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
                                    # Capture the HTTP status code if available so 409s are actionable
                                    _debug(f'    create_relation ERR: {type(create_err).__name__}')
                                    _debug(f'    full traceback:\n{tb_mod.format_exc()}')
                                    status_code = getattr(create_err, 'response', None)
                                    if hasattr(status_code, 'status_code'):
                                        errors.append(
                                            f'Failed to create relation -> {target_name}: '
                                            f'HTTP {status_code.status_code} — "{create_err}". '
                                            f'This usually means a relation already exists between these entities. '
                                            f'Check Kanka directly and delete any duplicate, then retry.'
                                        )
                                    else:
                                        errors.append(f'Failed to create relation -> {target_name}: {create_err}')

                            elif existing and _rel_id(existing):
                                rid = _rel_id(existing)
                                _debug(f'    update_relation eid={entity_id} rid={rid} rel={rc.get("relation")!r}')
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
                                    _debug(f'    update_relation ERR: {type(update_err).__name__}')
                                    _debug(f'    full traceback:\n{tb_mod.format_exc()}')
                                    status_code = getattr(update_err, 'response', None)
                                    if hasattr(status_code, 'status_code'):
                                        errors.append(
                                            f'Failed to update relation -> {target_name}: '
                                            f'HTTP {status_code.status_code} — "{update_err}". '
                                            f'This may mean the relation was modified externally. Check Kanka directly.'
                                        )
                                    else:
                                        errors.append(f'Failed to update relation -> {target_name}: {update_err}')

                            elif existing and not _rel_id(existing):
                                errors.append(
                                    f'Cannot update relation -> {target_name}: '
                                    "API returned a relation without an 'id'. "
                                    f'Raw: {existing}. Try updating manually in Kanka.'
                                )

            except Exception as e:
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

        if errors:
            msg = '; '.join(errors)
            if warnings:
                msg += ' | Warnings: ' + '; '.join(warnings)
            return {'ok': False, 'message': msg, 'warnings': list(warnings)}
        result_msg = '; '.join(details)
        if warnings:
            result_msg += ' | Warnings: ' + '; '.join(warnings)
        return {'ok': True, 'message': result_msg, 'warnings': list(warnings)}

    @app.route('/api/proposals/<int:index>/sync', methods=['POST'])
    def sync_proposal(index):
        """Sync a single proposal to Kanka.io. Used before marking as applied."""
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        with _sync_lock:
            sync_result = _sync_proposal_to_kanka(index)

        result = {
            'ok': sync_result['ok'],
            'message': sync_result['message'],
            'warnings': sync_result.get('warnings', []),
            'proposal': queue[index],
        }
        # Mark as applied after successful sync; caller should also update local state via /status
        _save_queue(queue)
        return jsonify(result)

    @app.route('/api/proposals/<int:index>/regenerate', methods=['POST'])
    def regenerate_proposal(index):
        """Re-run a truncated update proposal through the LLM with higher token limits."""
        try:
            queue = _load_queue()
        except Exception as e:
            print(f'[REGEN] ERROR loading queue: {e}', file=sys.stderr, flush=True)
            import traceback

            traceback.print_exc(file=sys.stderr)
            return jsonify({'error': str(e)}), 500
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        proposal = queue[index]
        if _DEBUG:
            _debug(
                'regenerate #{} type={} truncated={} entity_id={} journal_id={} source_journal={}'.format(
                    index,
                    proposal.get('proposal_type'),
                    proposal.get('truncated'),
                    proposal.get('entity_id'),
                    proposal.get('_journal_id'),
                    proposal.get('source_journal'),
                )
            )

        if proposal.get('proposal_type') != 'update':
            return jsonify({'error': 'Only update proposals can be regenerated'}), 400

        # Allow regeneration of truncated proposals, or force-regenerate via ?force=1.
        # Stale proposals without _journal_id are OK as long as source_journal is present.
        _debug('about to enter main try block')
        journal_id = proposal.get('_journal_id')
        entity_id = proposal.get('entity_id')
        if not entity_id:
            return jsonify(
                {
                    'ok': False,
                    'error': (
                        'This proposal lacks the data needed to regenerate. Re-run sync_pipeline for fresh proposals.'
                    ),
                }
            ), 400

        # Need at least _journal_id for journal lookup.
        if not journal_id:
            return jsonify(
                {
                    'ok': False,
                    'error': (
                        'This proposal lacks both _journal_id and source_journal — cannot locate the original session.'
                    ),
                }
            ), 400

        # Delegate synopsis regeneration to the shared generator.
        from kanka_wiki_updater.synopsis_generator import build_synopsis_proposal

        try:
            client = KankaClient()
        except Exception as e:
            return jsonify({'ok': False, 'error': f'Failed to initialize API client: {e}'}), 500

        # Fetch the source journal directly via the single-journal endpoint.
        try:
            src_journal = client.get_journal(journal_id)
        except Exception as api_err:
            return jsonify(
                {
                    'ok': False,
                    'error': f'Cannot fetch journal from Kanka: {api_err}',
                }
            ), 400

        if not src_journal:
            return jsonify({'ok': False, 'error': 'Source journal not found.'}), 404

        # Helper to safely get attrs from dict or SimpleNamespace.
        def _safe_get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # Fetch fresh entity data (may have changed since original sync).
        try:
            kind_param = f'{proposal["entity_kind"]}s'
            entity_raw = getattr(client, f'get_{kind_param}')()
        except Exception as api_err:
            return jsonify(
                {
                    'ok': False,
                    'error': f'Cannot contact Kanka to fetch entities: {api_err}',
                }
            ), 400

        entity_data = next(
            (e for e in entity_raw if _safe_get(e, 'id') == proposal['entity_local_id']),
            None,
        )
        if not entity_data:
            return jsonify({'ok': False, 'error': 'Entity not found.'}), 404

        # Build the entity dict expected by build_synopsis_proposal.
        entity = {
            'name': proposal['entity_name'],
            'kind': proposal['entity_kind'],
            'entry': _safe_get(entity_data, 'entry') or '',
            'local_id': proposal['entity_local_id'],  # internal DB ID for API calls
            'entity_id': _safe_get(entity_data, 'entity_id'),  # public wiki page number for [journal:N] tags
        }

        # Use 2x max_tokens for regeneration.
        import kanka_wiki_updater.config as pkg_config

        regen_max = (
            pkg_config.LLM_MAX_TOKENS * 2 if pkg_config.LLM_PROVIDER != 'gemini' else pkg_config.GEMINI_MAX_TOKENS * 2
        )

        # Build entity index for relation resolution.
        idx = build_entity_index(client)

        result_proposal = build_synopsis_proposal(int(entity_id), entity, src_journal, idx, max_tokens=regen_max)

        force_regenerate = request.args.get('force', '0').lower() in ('1', 'true')

        # LLM connection/call error — show the real message instead of masking as "no change".
        if isinstance(result_proposal, dict) and result_proposal.get('_llm_error'):
            return jsonify(
                {
                    'ok': False,
                    'error': f'LLM call failed: {result_proposal["_llm_error"]}',
                }
            ), 500

        if result_proposal is None and not force_regenerate:
            return jsonify(
                {
                    'ok': False,
                    'error': 'LLM returned no meaningful change (identical to current).',
                }
            ), 409

        # When forcing regeneration with identical output, build a minimal proposal.
        if result_proposal is None:
            result_proposal = {
                'proposed_entry': queue[index].get('proposed_entry', entity.get('entry') or ''),
                'change_summary': '(forced regeneration - no meaningful change)',
            }

        # Merge the new proposal into the existing queue entry.
        queue[index]['proposed_entry'] = result_proposal['proposed_entry']
        queue[index]['change_summary'] = result_proposal.get('change_summary', '')
        queue[index]['relation_changes'] = []
        queue[index]['uncertain'] = result_proposal.get('uncertain', [])
        queue[index]['truncated'] = result_proposal.get('truncated', False)

        _save_queue(queue)
        return jsonify(
            {
                'ok': True,
                'proposal': queue[index],
            }
        )

    @app.route('/api/sync/run', methods=['POST'])
    def run_sync():
        job_id = _next_job_id()
        module_dir = str(Path(pkg_config.DATA_DIR).parent)
        proc = subprocess.Popen(
            [sys.executable, '-m', 'kanka_wiki_updater.sync_pipeline'],
            cwd=module_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )
        buffer = deque(maxlen=500)
        _sync_jobs[job_id] = {
            'process': proc,
            'buffer': buffer,
            'status': 'running',
            'started_at': time.time(),
            'finished_at': None,
        }
        thread = threading.Thread(target=_sync_thread, args=(job_id, proc, buffer), daemon=True)
        thread.start()
        return jsonify({'job_id': job_id, 'status': 'running'})

    @app.route('/api/sync/output')
    def sync_output():
        job_id = request.args.get('job_id')
        if not job_id or job_id not in _sync_jobs:
            from flask import Response

            return Response('Job not found', status=404, mimetype='text/plain')

        job = _sync_jobs[job_id]
        buffer = job['buffer']

        def generate():
            try:
                while True:
                    # Drain any buffered lines first
                    while buffer:
                        line = buffer.popleft()
                        yield f'event: message\ndata: {json.dumps({"type": "output", "text": line})}\n\n'

                    if job['status'] in ('completed', 'error', 'cancelled'):
                        yield f'event: status\ndata: {json.dumps({"status": job["status"]})}\n\n'
                        yield 'event: end\n\n'
                        return

                    time.sleep(0.2)  # poll interval
            except GeneratorExit:
                # Client disconnected — mark job as cancelled so stale connections don't serve it
                with _sync_cancel_lock:
                    if job['status'] == 'running':
                        job['status'] = 'cancelled'
                        job['finished_at'] = time.time()

        from flask import Response

        return Response(generate(), mimetype='text/event-stream')

    @app.route('/api/sync/cancel', methods=['POST'])
    def cancel_sync():
        job_id = request.args.get('job_id')
        if not job_id or job_id not in _sync_jobs:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404

        with _sync_cancel_lock:
            job = _sync_jobs[job_id]
            if job['process'] and job['process'].poll() is None:
                try:
                    job['process'].terminate()
                    job['process'].wait(timeout=3)
                except Exception:
                    try:
                        job['process'].kill()
                        job['process'].wait(timeout=5)
                    except Exception:
                        pass
            job['status'] = 'cancelled'
            job['finished_at'] = time.time()

        return jsonify({'ok': True, 'job_id': job_id})

    @app.route('/api/sync/status')
    def sync_status():
        jobs = []
        for jid, job in _sync_jobs.items():
            jobs.append(
                {
                    'job_id': jid,
                    'status': job['status'],
                    'output_lines_count': len(job['buffer']),
                    'started_at': job['started_at'],
                    'finished_at': job.get('finished_at'),
                }
            )
        return jsonify({'active': bool(jobs), 'jobs': jobs})

    return app


def _load_queue():
    try:
        from . import config, state
    except ImportError:
        from kanka_wiki_updater import config, state

    queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
    return state._load(queue_file, [])


def _save_queue(queue):
    try:
        from . import config, state
    except ImportError:
        from kanka_wiki_updater import config, state

    queue_file = os.path.join(config.DATA_DIR, 'pending_changes.json')
    state._save(queue_file, queue)


def _sync_thread(job_id, proc, buffer):
    """Background thread that reads subprocess stdout and pushes to deque."""
    try:
        for line in proc.stdout:
            with _sync_cancel_lock:
                if job_id in _sync_jobs and _sync_jobs[job_id]['status'] == 'cancelled':
                    break
            buffer.append(line.rstrip('\n'))
        proc.wait()
        with _sync_cancel_lock:
            if job_id not in _sync_jobs or _sync_jobs[job_id]['status'] == 'cancelled':
                return
            if proc.returncode == 0:
                _sync_jobs[job_id]['status'] = 'completed'
            else:
                _sync_jobs[job_id]['status'] = 'error'
    except Exception as e:
        with _sync_cancel_lock:
            if job_id in _sync_jobs and _sync_jobs[job_id]['status'] != 'cancelled':
                buffer.append(f'Sync error: {e}')
                _sync_jobs[job_id]['status'] = 'error'


# ── HTML template (embedded single-page app) ───────────────────────────────



def main():
    """Entry point for `python -m kanka_wiki_updater.review_web`."""
    app = create_app()
    print('Starting Kanka Wiki Review UI...')
    print('Open http://127.0.0.1:5555 in your browser')
    app.run(host='127.0.0.1', port=5555, debug=False)


if __name__ == '__main__':
    main()
