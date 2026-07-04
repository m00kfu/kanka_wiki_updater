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

from flask import Flask, jsonify, render_template_string, request  # noqa: E402

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
        queue = _load_queue()
        return render_template_string(INDEX_HTML, PROPOSALS=queue)

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
                success, message = _sync_proposal_to_kanka(index)
            queue = _load_queue()  # Reload after potential modifications
            proposal = queue[index]
            sync_result = {'ok': success, 'message': message}
            if not success:
                return jsonify(
                    {
                        'proposal': proposal,
                        'ok': False,
                        'sync_error': True,
                        'sync_message': message,
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
                wiki_match = _re.search(r'\[(?:entity|character|location|organisation|monster|deity|background|class|subrace|race):(\d+)\]', name)
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
                kind_param_map = {'character': 'characters', 'location': 'locations', 'organization': 'organizations'}
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
                kind_map = {'character': 'characters', 'location': 'locations', 'organization': 'organizations'}
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
                    else:
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
                                errors.append(f'Cannot delete relation -> {target_name}: entity not found')
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
                                errors.append(
                                    f'Cannot {action} relation -> {target_name}: entity not found. '
                                    f'Check the spelling — the name must match exactly as it appears in Kanka.'
                                )
                                continue

                            existing = next((r for r in existing_relations if _rel_target(r) == target_entity_id), None)

                            # Also detect reverse-direction relations — Kanka returns 409 on create
                            # if any relation already exists between these two entities.
                            has_reverse = any(
                                _rel_owner(r) == target_entity_id and _rel_target(r) == entity_id
                                for r in existing_relations
                            )
                            _debug(f'    has_reverse (stale check): {has_reverse}')

                            if has_reverse:
                                details.append(
                                    f"Relation already exists between this entity and '{target_name}' "
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
            return False, '; '.join(errors)
        return True, '; '.join(details)

    @app.route('/api/proposals/<int:index>/sync', methods=['POST'])
    def sync_proposal(index):
        """Sync a single proposal to Kanka.io. Used before marking as applied."""
        queue = _load_queue()
        if index >= len(queue):
            return jsonify({'error': 'Proposal not found'}), 404

        with _sync_lock:
            success, message = _sync_proposal_to_kanka(index)

        result = {
            'ok': success,
            'message': message,
            'proposal': queue[index],
        }
        # Mark as applied after successful sync; caller should also update local state via /status
        _save_queue(queue)
        return jsonify(result)

    @app.route('/api/proposals/<int:index>/regenerate', methods=['POST'])
    def regenerate_proposal(index):
        """Re-run a truncated update proposal through the LLM with higher token limits."""
        print(f'[REGEN] START index={index}', file=sys.stderr, flush=True)
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

        # Need at least _journal_id or source_journal for journal lookup
        if not journal_id and not proposal.get('source_journal'):
            return jsonify(
                {
                    'ok': False,
                    'error': (
                        'This proposal lacks both _journal_id and source_journal — cannot locate the original session.'
                    ),
                }
            ), 400

        try:
            _debug('about to import modules')
            from kanka_wiki_updater.llm_client import chat_json
            from kanka_wiki_updater.mentions import normalize_text, strip_html
            from kanka_wiki_updater.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
            from kanka_wiki_updater.sync_pipeline import relation_summary
        except ImportError:
            _debug('ImportError:', tb_mod.format_exc())
            return jsonify({'error': 'Import error — cannot regenerate'}), 500

        try:
            _debug('about to create KankaClient')
            client = KankaClient()
            _debug('KankaClient created OK')
        except Exception as e:
            _debug(f'KankaClient creation failed: {e}')
            return jsonify({'ok': False, 'error': f'Failed to initialize API client: {e}'}), 500

        try:
            # Fetch the original journal entry (fallback: search by source_journal name if _journal_id missing)
            if journal_id:
                try:
                    journals = client.get_journals(journal_ids=[journal_id])
                except Exception as api_err:
                    return jsonify(
                        {
                            'ok': False,
                            'error': f'Cannot fetch journal #{journal_id} from Kanka: {api_err}',
                        }
                    ), 400
            else:
                source_name = proposal.get('source_journal', '')
                if not source_name:
                    return jsonify(
                        {
                            'ok': False,
                            'error': (
                                'This proposal lacks _journal_id and source_journal — '
                                'cannot locate the original session.'
                            ),
                        }
                    ), 400
                _debug(f'fetching all journals to find "{source_name}"')
                try:
                    all_journals = client.get_journals()
                except Exception as api_err:
                    return jsonify(
                        {
                            'ok': False,
                            'error': f'Cannot contact Kanka to look up journal "{source_name}": {api_err}',
                        }
                    ), 400
                _debug(f'total journals fetched: {len(all_journals)}')
                def _jname(j):
                    return (j.get('name') if isinstance(j, dict) else getattr(j, 'name', '')) or ''
                journals = [j for j in all_journals if _jname(j).lower() == source_name.lower()]
                _debug(f'journal matches after filter: {len(journals)}, searching for: "{source_name}"')

            if not journals:
                sample_names = [_jname(j) for j in all_journals[:5]]
                _debug('no journal match found. sample names:', sample_names)
                return jsonify(
                    {
                        'ok': False,
                        'error': f'Could not find journal matching "{source_name}" — fetched {len(all_journals)} journals.',
                    }
                ), 400
            journal = journals[0]

            # Fetch fresh entity data (may have changed since original sync)
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
                (
                    e
                    for e in entity_raw
                    if (isinstance(e, dict) and e.get('id') == proposal['entity_local_id'])
                    or getattr(e, 'id', None) == proposal['entity_local_id']
                ),
                None,
            )
        except Exception as api_err:
            _debug('regenerate error:', tb_mod.format_exc())
            return jsonify(
                {
                    'ok': False,
                    'error': f'Unexpected error during regeneration: {api_err}',
                }
            ), 500

        if _DEBUG:
            _debug(
                'journal found:', journal.get('name') if isinstance(journal, dict) else getattr(journal, 'name', None)
            )
            _debug('entity_data:', entity_data)

        session_text = strip_html(
            (journal.get('entry') or '') if isinstance(journal, dict) else (getattr(journal, 'entry', '') or '')
        )
        if not session_text.strip():
            return jsonify({'ok': False, 'error': 'Journal entry is empty.'}), 400

        # Build entity index for relation resolution
        idx = build_entity_index(client)
        entity_info = {
            'kind': proposal['entity_kind'],
            'local_id': proposal['entity_local_id'],
            'name': proposal['entity_name'],
            'entry': (entity_data.get('entry') or '')
            if isinstance(entity_data, dict)
            else (getattr(entity_data, 'entry', '') or ''),
            'relations': [],
        }
        rels = (
            (entity_data.get('relations') or [])
            if isinstance(entity_data, dict)
            else (getattr(entity_data, 'relations', []) or [])
        )
        for r in rels:
            entity_info['relations'].append(
                r
                if isinstance(r, dict)
                else {
                    'target_id': getattr(r, 'target_id', None),
                    'owner_id': getattr(r, 'owner_id', None),
                    'relation': getattr(r, 'relation', ''),
                    'attitude': getattr(r, 'attitude', None) if hasattr(r, 'attitude') else None,
                }
            )

        user_prompt = USER_PROMPT_TEMPLATE.format(
            name=entity_info['name'],
            entity_kind=entity_info['kind'],
            current_entry=strip_html(entity_info['entry']) or '(no synopsis yet)',
            current_relations=relation_summary(entity_info['relations'], idx),
            journal_name=(journal.get('name') if isinstance(journal, dict) else getattr(journal, 'name', None))
            or 'Session note',
            journal_date=(
                (journal.get('date') if isinstance(journal, dict) else getattr(journal, 'date', None))
                or (journal.get('created_at') if isinstance(journal, dict) else getattr(journal, 'created_at', ''))
                or ''
            ),
            session_text=session_text,
        )

        # Use 2x max_tokens for regeneration to give the model more room
        regen_max = (
            (pkg_config.LLM_MAX_TOKENS * 2)
            if pkg_config.LLM_PROVIDER != 'gemini'
            else (pkg_config.GEMINI_MAX_TOKENS * 2)
        )
        try:
            result = chat_json(SYSTEM_PROMPT, user_prompt, max_tokens=regen_max)
        except Exception as e:
            return jsonify({'ok': False, 'error': f'LLM error during regeneration: {e}'}), 500

        no_text_change = normalize_text(result.get('updated_entry', '')) == normalize_text(entity_info['entry'])
        if no_text_change and not result.get('relation_changes'):
            return jsonify(
                {'ok': False, 'error': 'Regenerated output is identical to current synopsis — nothing changed.'}
            ), 409

        queue[index]['proposed_entry'] = result.get('updated_entry', '') or entity_info['entry']
        queue[index]['change_summary'] = result.get('change_summary', '')
        queue[index]['relation_changes'] = result.get('relation_changes', [])
        queue[index]['uncertain'] = result.get('uncertain', [])
        queue[index]['truncated'] = False

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

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kanka Wiki Review</title>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #c9d1d9; --text-dim: #8b949e;
  --green: #3fb950; --red: #f85149; --yellow: #d29922;
  --cyan: #39d2c0; --magenta: #bc8cff; --blue: #58a6ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); height: 100vh; overflow: hidden; }
.app { display: flex; flex-direction: column; height: 100vh; }
.header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; }
.header h1 { font-size: 16px; font-weight: 600; color: var(--cyan); }
.header .stats { font-size: 13px; color: var(--text-dim); }
.main { display: flex; flex: 1; overflow: hidden; }
.sidebar { width: 280px; background: var(--surface); border-right: 1px solid var(--border); flex-shrink: 0; display: flex; flex-direction: column; }
.tab-bar { display: flex; border-bottom: 1px solid var(--border); padding: 0 8px; flex-shrink: 0; }
.sidebar-list { overflow-y: auto; flex: 1; }
.tab-btn { background: none; border: none; color: var(--text-dim); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; padding: 10px 12px; cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s; }
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--cyan); border-bottom-color: var(--cyan); }
.tab-btn.inactive { opacity: 0.6; }
.proposal-item { padding: 12px 16px; cursor: pointer; border-bottom: 1px solid var(--border); transition: background 0.15s; }
.proposal-item:hover { background: #1c2128; }
.proposal-item.active { background: #1f2a37; border-left: 3px solid var(--cyan); }
.proposal-item .name { font-size: 14px; font-weight: 500; margin-bottom: 4px; }
.proposal-item .meta { font-size: 11px; color: var(--text-dim); }
.badge { display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; text-transform: uppercase; margin-right: 6px; }
.badge-new { background: #1a2e3a; color: var(--cyan); }
.badge-upd { background: #1c2833; color: var(--blue); }
.content { flex: 1; overflow-y: auto; padding: 24px; }
.proposal-header { margin-bottom: 20px; }
.proposal-header h2 { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
.proposal-header .source { font-size: 13px; color: var(--text-dim); }
.proposal-header .summary { font-size: 14px; color: var(--text); margin-top: 8px; padding: 10px; background: var(--surface); border-radius: 6px; border-left: 3px solid var(--magenta); }
.diff-section { margin-bottom: 24px; }
.diff-section h3 { font-size: 13px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.diff-container { background: #0d1117; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.diff-line { padding: 4px 12px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 15px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.diff-add { background: #0d1f0d; color: var(--green); border-left: 3px solid var(--green); }
.diff-del { background: #2a0d0d; color: var(--red); border-left: 3px solid var(--red); }
.relations-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.relation-card { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; }
.relation-card .rel-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.rel-action { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 3px; text-transform: uppercase; }
.rel-create { background: #0d2817; color: var(--green); }
.rel-update { background: #2a2000; color: var(--yellow); }
.rel-delete { background: #2a0d0d; color: var(--red); }
.relation-card .rel-target { font-size: 14px; font-weight: 500; }
.relation-card .rel-reason { font-size: 13px; color: var(--text-dim); margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border); }
.warning { background: #2a2000; border: 1px solid var(--yellow); border-radius: 6px; padding: 10px 14px; margin-bottom: 16px; font-size: 13px; color: var(--yellow); }
.warning.critical { background: #2a0d0d; border-color: var(--red); color: var(--red); }
.editor-section { margin-bottom: 24px; }
textarea.synopsis-editor { width: 100%; min-height: 180px; max-height: 45vh; background: #0d1117; border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-family: inherit; font-size: 14px; line-height: 1.6; padding: 12px; resize: vertical; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; overflow-y: auto; }
textarea.synopsis-editor:focus { outline: none; border-color: var(--cyan); }
.action-bar { position: sticky; bottom: 0; background: rgba(22, 27, 34, 0.95); backdrop-filter: blur(8px); border-top: 1px solid var(--border); padding: 16px 24px; display: flex; gap: 10px; align-items: center; }
.btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: 1px solid var(--border); transition: all 0.15s; background: var(--surface); color: var(--text); }
.btn:hover { opacity: 0.85; transform: translateY(-1px); }
.btn-primary { background: #238636; color: white; border-color: #238636; }
.btn-danger { background: #da3633; color: white; border-color: #da3633; }
.toast { position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%) translateY(100px); padding: 12px 24px; border-radius: 6px; font-size: 14px; font-weight: 500; opacity: 0; transition: all 0.3s ease; z-index: 100; }
.toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
.toast-success { background: #238636; color: white; }
.toast-error { background: #da3633; color: white; }
.empty-state { text-align: center; padding: 60px 20px; color: var(--text-dim); }
.empty-state h3 { font-size: 18px; margin-bottom: 8px; color: var(--text); }
.sync-container { display: flex; flex-direction: column; height: calc(100vh - 205px); min-height: 120px; max-height: calc(100vh - 205px); }
.empty-state { display: flex; flex-direction: column; height: 100%; }
.sync-output { background:#0d1117;border:1px solid var(--border);border-radius:6px;padding:12px;color:var(--green);font-family:'SF Mono',monospace;font-size:13px;line-height:1.6;overflow-y:auto;white-space:pre-wrap;text-align:left;flex:1;min-height:0;width:100%; }
.progress-bar { height: 3px; background: var(--border); position: relative; }
.progress-fill { height: 100%; background: linear-gradient(90deg, var(--cyan), var(--blue)); transition: width 0.3s ease; }
.edit-mode-banner { background: #0d2837; border-bottom: 1px solid var(--cyan); padding: 8px 24px; font-size: 13px; color: var(--cyan); display: none; }
.edit-mode-banner.visible { display: flex; justify-content: space-between; align-items: center; }
.relation-editor { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.relation-editor select, .relation-editor input { background: #0d1117; border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 13px; margin-right: 8px; }
.shortcuts { position: fixed; bottom: 70px; right: 20px; font-size: 11px; color: var(--text-dim); text-align: right; line-height: 1.8; }
kbd { background: var(--surface); border: 1px solid var(--border); padding: 1px 6px; border-radius: 3px; font-family: monospace; }
.add-relation-form { margin-top: 12px; padding: 10px; background: var(--surface); border: 1px dashed var(--border); border-radius: 6px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.add-relation-form input, .add-relation-form select { background: #0d1117; border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 13px; }
.add-relation-form button { padding: 4px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; background: var(--green); color: white; border: none; }
.entity-name-editor, .type-selector { margin-bottom: 8px; }
.entity-name-editor input { background: #0d1117; border: 1px solid var(--cyan); color: var(--text); padding: 6px 10px; border-radius: 4px; font-size: 16px; width: 300px; }
.type-selector select { background: #0d1117; border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 13px; margin-left: 8px; }
</style>
</head>
<body>
<div class="app">
  <div class="header">
    <h1>Kanka Wiki Review</h1>
    <div class="stats" id="stats"></div>
  </div>
  <div class="edit-mode-banner" id="editBanner">
    <span>Edit mode — modify the text above, then save or cancel.</span>
    <button class="btn btn-primary" onclick="saveEdit()" style="padding:4px 12px;font-size:12px;">Save</button>
    <button class="btn btn-secondary" onclick="cancelEdit()" style="padding:4px 12px;font-size:12px;">Cancel</button>
  </div>
  <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
  <div class="main">
    <div class="sidebar" id="sidebar"></div>
    <div class="content" id="content"></div>
  </div>
  <div class="action-bar" id="actionBar">
    <button class="btn btn-primary" onclick="approveAll()">Approve All</button>
    <button class="btn btn-secondary" onclick="approveSynopsisOnly()">Synopsis Only</button>
    <button class="btn" onclick="regenerateProposal()" style="display:none;background:#1f6feb;color:white;border-color:#1f6feb" id="regenerateBtn">Regenerate</button>
    <button class="btn btn-danger" onclick="rejectCurrent()">Reject</button>
    <div style="flex:1"></div>
    <span style="font-size:12px;color:var(--text-dim)">[n]ext [p]rev [e]dit [esc]cancel [a]pprove [s]ynopsis [g]enerate [r]eject [q]uit</span>
  </div>
  <div class="shortcuts">
    <kbd>n</kbd> next &nbsp; <kbd>p</kbd> prev<br>
    <kbd>e</kbd> edit &nbsp; <kbd>esc</kbd> cancel<br>
    <kbd>a</kbd> approve all &nbsp; <kbd>s</kbd> synopsis only &nbsp; <kbd>g</kbd> regenerate<br>
    <kbd>r</kbd> reject &nbsp; <kbd>q</kbd> quit (close tab)
  </div>
  <div class="toast" id="toast"></div>
</div>

<script>
let proposals = {{ PROPOSALS | tojson }};
let selectedIndex = null;
let currentTab = 'new'; // default tab
let editingField = null; // 'synopsis' or 'name' for new entities
let editingOriginal = ''; // original text when entering edit mode (for escape-to-cancel)
let currentSyncJob = null; // {job_id, status, output}
let syncEventSource = null; // EventSource reference for proper cleanup

function getPending() { return proposals.filter(p => p.status === 'pending'); }

function getVisibleIndices() {
  if (currentTab === 'new') {
    return proposals.reduce((acc, p, i) => { if (p.status === 'pending') acc.push(i); return acc; }, []);
  } else {
    return proposals.reduce((acc, p, i) => { if (p.status !== 'pending') acc.push(i); return acc; }, []);
  }
}
function updateStats() {
  const pending = getPending();
  const applied = proposals.filter(p => p.status === 'applied').length;
  const rejected = proposals.filter(p => p.status === 'rejected').length;
  document.getElementById('stats').textContent = pending.length + ' pending | ' + applied + ' approved | ' + rejected + ' rejected';
  const total = proposals.length;
  const done = applied + rejected;
  const pct = total ? (done / total * 100) : 0;
  document.getElementById('progressFill').style.width = pct + '%';
}

function renderSidebar() {
  const sidebar = document.getElementById('sidebar');
  let html = '<div class="tab-bar" id="tabBar">' +
    '<button class="tab-btn ' + (currentTab === "new" ? "active" : "inactive") + '" data-tab="new" onclick="switchTab(\\'new\\')">New</button>' +
    '<button class="tab-btn ' + (currentTab === "reviewed" ? "active" : "inactive") + '" data-tab="reviewed" onclick="switchTab(\\'reviewed\\')">Reviewed</button>' +
    '<button class="tab-btn ' + (currentTab === "sync" ? "active" : "inactive") + '" data-tab="sync" onclick="switchTab(\\'sync\\')">Sync</button>' +
    '</div>';
  html += '<div class="sidebar-list">';
  let pending = proposals.filter(p => p.status === 'pending');
  if (currentTab === 'new') {
    pending.sort((a, b) => {
      const aNew = a.proposal_type === 'new_entity' ? 0 : 1;
      const bNew = b.proposal_type === 'new_entity' ? 0 : 1;
      return aNew - bNew;
    });
  }
  const filtered = currentTab === 'new' ? pending : proposals.filter(p => p.status !== 'pending');
  for (let f = 0; f < filtered.length; f++) {
    const origIdx = proposals.indexOf(filtered[f]);
    var isActive = origIdx === selectedIndex ? ' active' : '';
    var kind = filtered[f].proposal_type === 'new_entity' ? 'NEW' : 'UPD';
    var badgeClass = filtered[f].proposal_type === 'new_entity' ? 'badge-new' : 'badge-upd';
    var statusBadge = '';
    if (filtered[f].status === 'applied') { statusBadge = '<span style="color:var(--green)">&#10003;</span>'; }
    else if (filtered[f].status === 'rejected') { statusBadge = '<span style="color:var(--red)">&#10007;</span>'; }
    html += '<div class="proposal-item' + isActive + '" onclick="selectProposal(' + origIdx + ')">' +
      '<div class="name"><span class="badge ' + badgeClass + '">' + kind + '</span>' + escapeJsHtml(filtered[f].entity_name) + statusBadge + '</div>' +
      '<div class="meta">' + escapeJsHtml(filtered[f].source_journal) + '</div></div>';
  }
  html += '</div>';
  sidebar.innerHTML = html;
}

function switchTab(tab) {
  if (tab === currentTab) return;
  if (editingField) cancelEdit();
  currentTab = tab;
  selectedIndex = null;
  renderSidebar();
  renderContent();
}

function renderContent() {
  var content = document.getElementById('content');
  if (selectedIndex === null || selectedIndex >= proposals.length) {
    if (currentTab !== 'sync') {
      content.innerHTML = '<div class="empty-state"><h3>No proposal selected</h3>Select one from the sidebar.</div>';
    }
    renderSyncContent(content);
    return;
  }
  var p = proposals[selectedIndex];
  var html = '';

  // Header
  if (p.proposal_type === 'new_entity') {
    html += '<div class="proposal-header"><h2>New ' + p.suggested_type + ': <span id="entityNameDisplay">' + escapeJsHtml(p.entity_name) + '</span></h2>';
    html += '<div class="source">&larr; ' + escapeJsHtml(p.source_journal) + '</div></div>';
  } else {
    var statusIcon = {pending:'&#9675;', applied:'<span style="color:var(--green)">&#10003;</span>', rejected:'<span style="color:var(--red)">&#10007;</span>'}[p.status] || '&#9675;';
    html += '<div class="proposal-header"><h2>' + statusIcon + ' ' + escapeJsHtml(p.entity_name) + ' <span style="font-weight:400;color:var(--text-dim);font-size:16px">(' + p.entity_kind + ')</span></h2>';
    html += '<div class="source">&larr; ' + escapeJsHtml(p.source_journal) + '</div>';
    if (p.change_summary) { html += '<div class="summary">' + escapeJsHtml(p.change_summary) + '</div>'; }
    html += '</div>';

    // Regenerate link for update proposals
    html += '<div style="padding:8px 0">' +
      '<button class="btn" onclick="regenerateProposal()" style="font-size:12px;padding:4px 12px;">&#x21bb; Regenerate Proposal</button>' +
      ' <span style="font-size:11px;color:var(--text-dim)">or press [g]</span></div>';

  }

  // Warnings for dropped mentions (all proposal types)
  var prevEntry = p.previous_entry || '';
  var proposedEntry = p.proposed_entry || '';
  if (prevEntry && proposedEntry) {
    var oldIds = (prevEntry.match(/\\[entity:(\\d+)\\]/g) || []).map(function(s){ return s.match(/(\\d+)/)[1]; });
    var newIds = (proposedEntry.match(/\\[entity:(\\d+)\\]/g) || []).map(function(s){ return s.match(/(\\d+)/)[1]; });
    var dropped = oldIds.filter(function(x){ return newIds.indexOf(x) === -1; });
    if (dropped.length > 0) {
      html += '<div class="warning critical">!! ' + dropped.length + ' mention link(s) missing from new version!</div>';
    }
  }

  // Truncation warning with regenerate button
  var isTruncated = p.truncated === true || (p.change_summary && p.change_summary.indexOf('[TRUNCATED:') !== -1);
  if (isTruncated && p.proposal_type === 'update') {
    html += '<div class="warning" id="truncationWarning">' +
      '&#9888; This proposal may be truncated — the LLM hit its token limit and output was cut off. ' +
      '<button class="btn" onclick="regenerateProposal()" style="padding:4px 12px;font-size:12px;margin-left:12px;">Regenerate (higher max_tokens)</button>' +
      '</div>';
  } else if (isTruncated && p.proposal_type === 'new_entity') {
    html += '<div class="warning">&#9888; This new-entity suggestion may be truncated — the LLM output was cut off. Edit manually to fix.</div>';
  }

  // Synopsis / draft editing area
  html += '<div class="diff-section"><h3>' + (p.proposal_type === 'new_entity' ? 'Draft Synopsis' : 'Synopsis') + '</h3><div class="diff-container">';
  if (editingField === 'synopsis') {
    var currentText = p.proposal_type === 'new_entity' ? p.draft_entry : p.proposed_entry;
    html += '<textarea class="synopsis-editor" id="synopsisEditor">' + escapeHtmlForTextarea(stripHtml(currentText) || '') + '</textarea>';
  } else {
    if (p.proposal_type === 'new_entity') {
      html += '<div class="diff-line" style="cursor:pointer" onclick="startEdit(&quot;synopsis&quot;)">' + escapeJsHtml((p.draft_entry || '(none)').replace(/\\n/g, ' ')) + '</div>';
      html += '<div style="padding:4px 12px;font-size:11px;color:var(--text-dim)">Click to edit</div>';
    } else {
      var prevLines = stripHtml(p.previous_entry).split('\\n');
      var newLines = (p.proposed_entry || '').split('\\n');
      var maxLen = Math.max(prevLines.length, newLines.length);
      for (var i = 0; i < maxLen; i++) {
        if (i >= prevLines.length) {
          html += '<div class="diff-line diff-add">' + escapeJsHtml(newLines[i]) + '</div>';
        } else if (i >= newLines.length) {
          html += '<div class="diff-line diff-del">' + escapeJsHtml(prevLines[i]) + '</div>';
        } else if (prevLines[i] !== newLines[i]) {
          html += '<div class="diff-line diff-del">' + escapeJsHtml(prevLines[i]) + '</div>';
          html += '<div class="diff-line diff-add">' + escapeJsHtml(newLines[i]) + '</div>';
        } else {
          html += '<div class="diff-line" style="padding-left:20px">' + escapeJsHtml(prevLines[i]) + '</div>';
        }
      }
    }
  }
  html += '</div></div>';

  // Relation changes (update proposals only)
  if (p.relation_changes && p.relation_changes.length > 0) {
    html += '<div class="diff-section"><h3>Relationship Changes</h3><div class="relations-list">';
    p.relation_changes.forEach(function(rc, idx) {
      var actionClass = rc.action === 'create' ? 'rel-create' : rc.action === 'update' ? 'rel-update' : 'rel-delete';
      html += '<div class="relation-card" id="rel-' + idx + '">' +
        '<div class="rel-header">' +
          '<span class="rel-action ' + actionClass + '">' + escapeJsHtml(rc.action) + '</span>' +
          '<span class="rel-target">' + escapeJsHtml(p.entity_name) + ' --' + escapeJsHtml(rc.relation) + '--> ' + escapeJsHtml(rc.target_name) + '</span>' +
          '<button class="btn" onclick="deleteRelation(' + idx + ')" style="padding:2px 8px;font-size:11px;margin-left:auto;">Delete</button>' +
        '</div>' +
        '<div style="font-size:12px;color:var(--text-dim)">Attitude: ' + escapeJsHtml(rc.attitude || 'N/A') + '</div>' +
        '<div class="rel-reason">Reason: ' + escapeJsHtml(rc.reason) + '</div></div>';
    });
    html += '</div>';

    // Add new relation form
    html += '<div class="add-relation-form">' +
      '<input type="text" id="newRelTarget" placeholder="Target name">' +
      '<select id="newRelAction"><option value="create">create</option><option value="update">update</option></select>' +
      '<input type="text" id="newRelRelation" placeholder="Relation (e.g. ally)">' +
      '<input type="text" id="newRelAttitude" placeholder="Attitude">' +
      '<button onclick="addRelation()">Add</button></div>';
    html += '</div>';
  }

  // Status indicator
  if (p.status !== 'pending') {
    var statusColor = p.status === 'applied' ? 'var(--green)' : 'var(--red)';
    html += '<div style="text-align:center;padding:16px;color:' + statusColor + ';font-weight:600;font-size:14px">Status: ' + p.status.toUpperCase() + '</div>';
  }

  // Show/hide regenerate button for update proposals
  var regenBtn = document.getElementById('regenerateBtn');
  if (regenBtn) {
    regenBtn.style.display = (p.proposal_type === 'update') ? '' : 'none';
  }

  content.innerHTML = html;
}

function renderSyncContent(content) {
  if (currentTab !== 'sync') return;
  var jobStatus = currentSyncJob ? currentSyncJob.status : 'idle';
  var statusColor = {'running':'var(--blue)','completed':'var(--green)','error':'var(--red)','idle':'var(--text-dim)'}[jobStatus] || 'var(--text-dim)';

  content.innerHTML = '<div class="empty-state">' +
    '<h3>Run Sync Pipeline</h3>' +
    '<div class="sync-container">' +
      '<div style="margin:16px 0;">' +
        '<button class="btn btn-primary" id="runSyncBtn" onclick="runSync()">' + (currentSyncJob ? 'Cancel' : 'Run Sync') + '</button>' +
        '<span style="margin-left:12px;color:' + statusColor + ';font-weight:600;font-size:14px">&#9679; ' + jobStatus.toUpperCase() + '</span>' +
      '</div>' +
      '<pre id="syncOutput" class="sync-output">' +
        (currentSyncJob && currentSyncJob.output ? escapeJsHtml(currentSyncJob.output) : 'No sync run in progress.') +
      '</pre>' +
    '</div>' +
  '</div>';
}

function selectProposal(i) {
  selectedIndex = i;
  if (editingField) cancelEdit();
  renderSidebar();
  renderContent();
}

function escapeHtml(text) {
  var div = document.createElement('div');
  div.appendChild(document.createTextNode(text || ''));
  return div.innerHTML;
}

function escapeJsHtml(str) {
  // Escape backslash, single quote, HTML special chars, convert newlines to <br> for safe innerHTML insertion and JS string literal safety
  var escaped = (escapeHtml(str || '')).replace(/\\\\/g, '\\\\').replace(/'/g, "\\'");
  return escaped.replace(/\\r\\n/g, '<br>').replace(/\\r/g, '<br>').replace(/\\n/g, '<br>')
    .replace(/\\\\n/g, '\\\\n').replace(/\\\\r/g, '\\\\r');
}

function escapeHtmlForTextarea(str) {
  // Escape HTML special chars but preserve newlines as-is (for textarea content via innerHTML)
  return escapeHtml(str || '');
}

function escapeJs(str) {
  // Escape backslash, single/double quote, newline, carriage return, and forward slash for safe JS string literal insertion
  return (str || '').replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"').replace(/\\n/g, '\\\\n').replace(/\\r/g, '\\\\r').replace(/\\//g, '\\/');
}

function stripHtml(html) {
  var tmp = document.createElement('div');
  tmp.innerHTML = html || '';
  return tmp.textContent || tmp.innerText || '';
}

// ── Actions ────────────────────────────────────────────────────────────────

async function apiCall(url, method, body) {
  try {
    var res = await fetch(url, {
      method: method,
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return await res.json();
  } catch (e) {
    showToast('Error: ' + e.message, 'error');
    return null;
  }
}

async function approveAll() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_all'})
    .then(function(data) {
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync && data.sync.ok) {
        showToast('Synced to Kanka: ' + data.sync.message, 'success');
      } else if (data.sync && !data.sync.ok) {
        showToast('Kanka sync failed: ' + data.sync.message, 'error');
      } else {
        showToast('Approved all', 'success');
      }
    });
}

async function approveSynopsisOnly() {
  if (selectedIndex === null) return;
  if (editingField) await saveEdit();
  var oldIndex = selectedIndex;
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'approved_synopsis_only'})
    .then(function(data) {
      if (!data) return;
      proposals[selectedIndex] = data.proposal;
      _advance(oldIndex);
      if (data.sync && data.sync.ok) {
        showToast('Synopsis synced to Kanka: ' + data.sync.message, 'success');
      } else if (data.sync && !data.sync.ok) {
        showToast('Kanka sync failed: ' + data.sync.message, 'error');
      } else {
        showToast('Synopsis approved', 'success');
      }
    });
}

async function rejectCurrent() {
  if (selectedIndex === null) return;
  var oldIndex = selectedIndex;
  if (editingField) await saveEdit();
  apiCall('/api/proposals/' + selectedIndex + '/status', 'POST', {status: 'rejected'})
    .then(function(data) { if (data) { proposals[selectedIndex] = data.proposal; _advance(oldIndex); showToast('Rejected', 'error'); } });
}

function _advance(fromIndex) {
  var visible = getVisibleIndices();
  for (var i = 0; i < visible.length; i++) {
    if (visible[i] > fromIndex) {
      selectedIndex = visible[i];
      renderSidebar();
      renderContent();
      return;
    }
  }
  selectedIndex = null;
  renderSidebar();
  renderContent();
}

// ── Editor ─────────────────────────────────────────────────────────────────

function startEdit(field) {
  editingField = field;
  renderContent();
  var editor = document.getElementById('synopsisEditor');
  if (editor) {
    editingOriginal = editor.value;
    editor.focus();
    editor.selectionStart = editor.value.length;
  }
}

async function saveEdit() {
  if (selectedIndex === null || !editingField) return;
  var editor = document.getElementById('synopsisEditor');
  var text = editor.value;
  var fieldKey = editingField === 'synopsis' ? (proposals[selectedIndex].proposal_type === 'new_entity' ? 'draft_entry' : 'proposed_entry') : null;

  if (fieldKey) {
    var result = await apiCall('/api/proposals/' + selectedIndex + '/edit', 'POST', {entry: text});
    if (result && result.proposal) {
      proposals[selectedIndex] = result.proposal;
      editingField = null;
      renderSidebar();
      renderContent();
      showToast('Changes saved', 'success');
    }
  }
}

function cancelEdit() {
  editingField = null;
  renderContent();
}

// ── Relation management ────────────────────────────────────────────────────

async function addRelation() {
  if (selectedIndex === null) return;
  var target = document.getElementById('newRelTarget').value.trim();
  var action = document.getElementById('newRelAction').value;
  var relation = document.getElementById('newRelRelation').value.trim();
  var attitude = document.getElementById('newRelAttitude').value.trim();

  if (!target || !relation) { showToast('Target and relation are required', 'error'); return; }

  var result = await apiCall('/api/proposals/' + selectedIndex + '/relation', 'POST', {
    action: action, target_name: target, relation: relation, attitude: attitude, reason: ''
  });
  if (result && result.proposal) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Relation added', 'success');
  }
}

async function deleteRelation(idx) {
  if (selectedIndex === null) return;
  var p = proposals[selectedIndex];
  var rel = p.relation_changes[idx];
  var result = await apiCall('/api/proposals/' + selectedIndex + '/relation', 'POST', {
    action: 'delete', target_name: rel.target_name
  });
  if (result && result.proposal) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Relation deleted', 'success');
  }
}

// ── Truncation regeneration ────────────────────────────────────────────────

async function regenerateProposal() {
  if (selectedIndex === null) return;
  var p = proposals[selectedIndex];
  if (!p || p.proposal_type !== 'update') { showToast('Only update proposals can be regenerated', 'error'); return; }

  // Show loading state
  var banner = document.getElementById('truncationWarning');
  if (banner) {
    banner.innerHTML += ' <span style="color:var(--blue);font-size:12px">Generating...</span>';
  }

  var result = await apiCall('/api/proposals/' + selectedIndex + '/regenerate', 'POST');
  if (!result) return;

  if (result.ok) {
    proposals[selectedIndex] = result.proposal;
    renderSidebar();
    renderContent();
    showToast('Regeneration successful — proposal updated with fresh LLM output.', 'success');
  } else {
    var msg = result.error || 'Regeneration failed';
    if (banner) {
      banner.innerHTML = '&#9888; Regeneration: <strong>' + escapeHtml(msg) + '</strong> ' + banner.innerHTML;
    }
    showToast(msg, 'error');
  }
}

// ── Toast ──────────────────────────────────────────────────────────────────

function showToast(message, type) {
  var toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'toast toast-' + type + ' show';
  setTimeout(function(){ toast.classList.remove('show'); }, 7000);
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (editingField || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

  switch(e.key.toLowerCase()) {
    case 'n': {
      const visible = getVisibleIndices();
      const pos = visible.indexOf(selectedIndex);
      if (pos !== null && pos < visible.length - 1) {
        selectedIndex = visible[pos + 1];
        renderSidebar();
        renderContent();
      }
      break;
    }
    case 'p': {
      const visible = getVisibleIndices();
      const pos = visible.indexOf(selectedIndex);
      if (pos !== null && pos > 0) {
        selectedIndex = visible[pos - 1];
        renderSidebar();
        renderContent();
      }
      break;
    }
    case 'e': e.preventDefault(); if (!editingField) startEdit('synopsis'); break;
    case 'a': approveAll(); break;
    case 's': approveSynopsisOnly(); break;
    case 'r': rejectCurrent(); break;
    case 'g': regenerateProposal(); break;
    case 'q': window.close(); break;
  }
});

// ── Escape key to cancel editing ───────────────────────────────────────────

document.addEventListener('keydown', function(e) {
  if (e.key !== 'Escape' || !editingField) return;
  var editor = document.getElementById('synopsisEditor');
  if (!editor) return;
  var hasChanges = editor.value !== editingOriginal;
  if (hasChanges && !confirm('Discard unsaved changes?')) return;
  cancelEdit();
});

// ── Sync pipeline runner ───────────────────────────────────────────────────

async function runSync() {
  if (currentSyncJob) {
    // Cancel: close EventSource and notify server to stop subprocess
    if (syncEventSource) {
      syncEventSource.close();
      syncEventSource = null;
    }
    var jobId = currentSyncJob.job_id;
    currentSyncJob = null;
    renderContent();
    // Notify server to terminate the running process
    apiCall('/api/sync/cancel?job_id=' + encodeURIComponent(jobId), 'POST')
      .catch(function() { /* best-effort */ });
    return;
  }

  var result = await apiCall('/api/sync/run', 'POST');
  if (!result || !result.job_id) return;

  currentSyncJob = { job_id: result.job_id, status: 'running', output: '' };
  renderContent();

  // Connect to SSE stream
  syncEventSource = new EventSource('/api/sync/output?job_id=' + result.job_id);

  syncEventSource.addEventListener('message', function(e) {
    var data = JSON.parse(e.data);
    if (data.type === 'output') {
      currentSyncJob.output += data.text + '\\n';
      var pre = document.getElementById('syncOutput');
      if (pre) {
        pre.textContent = currentSyncJob.output;
        pre.scrollTop = pre.scrollHeight;
      }
    }
  });

  syncEventSource.addEventListener('status', function(e) {
    var data = JSON.parse(e.data);
    currentSyncJob.status = data.status;
    renderContent();
  });

  syncEventSource.addEventListener('end', function() {
    syncEventSource.close();
    syncEventSource = null;
    currentSyncJob = null;
    renderContent();
    // Refresh proposals after sync completes
    loadProposals();
  });
}

function loadProposals() {
  fetch('/api/proposals')
    .then(function(r){ return r.json(); })
    .then(function(data) {
      var existing = proposals.slice();
      for (var i = 0; i < data.length; i++) {
        var found = false;
        for (var j = 0; j < existing.length; j++) {
          if (existing[j].entity_name === data[i].entity_name && existing[j].source_journal === data[i].source_journal) {
            existing[j] = data[i];
            found = true;
            break;
          }
        }
        if (!found) { existing.push(data[i]); }
      }
      proposals = existing;
      updateStats();
      renderSidebar();
      renderContent();
    })
    .catch(function() { /* silently ignore — keep current state */ });
}

// ── Init ───────────────────────────────────────────────────────────────────

updateStats();
renderSidebar();
if (proposals.length > 0) { selectProposal(0); }
else { document.getElementById('content').innerHTML = '<div class="empty-state"><h3>No pending proposals</h3>Run sync_pipeline first.</div>'; }

setInterval(updateStats, 5000);
</script>
</body>
</html>"""


def main():
    """Entry point for `python -m kanka_wiki_updater.review_web`."""
    app = create_app()
    print('Starting Kanka Wiki Review UI...')
    print('Open http://127.0.0.1:5555 in your browser')
    app.run(host='127.0.0.1', port=5555, debug=False)


if __name__ == '__main__':
    main()
