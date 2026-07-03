"""Wrapper around python-kanka library for Kanka API."""

import os
import sys

import kanka

from . import config

_DEBUG = bool(os.environ.get('KANKA_DEBUG'))


def _debug(*args):
    if _DEBUG:
        print('[KANKA-DEBUG]', *args, file=sys.stderr)


class KankaError(RuntimeError):
    """Raised when a Kanka operation fails (backward-compatible alias)."""

    pass


class KankaClient:
    def __init__(self):
        self._client = kanka.KankaClient(
            token=config.KANKA_TOKEN,
            campaign_id=int(config.KANKA_CAMPAIGN_ID),
            enable_rate_limit_retry=True,
        )

    # -- Journals (session notes) ---------------------------------------

    def get_journals(self, since=None, journal_type=None):
        params = {}
        if since:
            params['last_sync'] = since
        if journal_type:
            params['type'] = journal_type
        return self._client.journals.list(**params)  # returns list[Journal]

    # -- Characters / Locations ------------------------------------------

    def get_characters(self):
        return self._client.characters.list(related=True)  # returns list[Character]

    def get_locations(self):
        return self._client.locations.list(related=True)  # returns list[Location]

    def update_entity_entry(self, kind, entity_local_id, entry_text):
        """kind is 'characters' or 'locations'; entity_local_id is the
        type-specific `id` field (NOT `entity_id`)."""
        html = entry_text.replace('\n\n', '<br><br>').replace('\n', '<br>')
        if kind == 'characters':
            self._client.characters.update(entity_local_id, entry=html)
        elif kind == 'locations':
            self._client.locations.update(entity_local_id, entry=html)

    def create_character(self, name, entry=None, **extra):
        """Create a new character. `name` is the only required field."""
        data = {'name': name}
        if entry:
            html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
            data['entry'] = html
        data.update(extra)
        char = self._client.characters.create(**data)
        return {'data': {k: getattr(char, k, None) for k in ['id', 'entity_id', 'name', 'entry']}}

    def create_location(self, name, entry=None, **extra):
        """Create a new location. `name` is the only required field."""
        data = {'name': name}
        if entry:
            html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
            data['entry'] = html
        data.update(extra)
        loc = self._client.locations.create(**data)
        return {'data': {k: getattr(loc, k, None) for k in ['id', 'entity_id', 'name', 'entry']}}

    def delete_character(self, local_id):
        return self._client.characters.delete(local_id)  # returns bool

    def delete_location(self, local_id):
        return self._client.locations.delete(local_id)  # returns bool

    # -- Relations --------------------------------------------------------

    def get_relations(self, entity_id):
        _debug(f'get_relations(entity_id={entity_id})')
        # Use direct API call — python-kanka's list_for_entity tries to
        # instantiate Relation objects but the vendor library has a broken import.
        resp = self._client._request('GET', f'entities/{entity_id}/relations')
        data = resp.get('data') or []
        if isinstance(data, dict):
            data = [data]
        _debug(f'  -> {len(data)} relations returned')
        return data

    def create_relation(self, entity_id, target_id, relation, attitude=None, two_way=False, visibility_id=1):
        _debug(
            f'create_relation(entity_id={entity_id}, target_id={target_id}, relation={relation!r}, '
            f'attitude={attitude!r}, two_way={two_way}, visibility_id={visibility_id})'
        )
        # Call the API directly with snake_case fields — python-kanka sends
        # camelCase (ownerId, targetId) which Kanka's current API rejects.
        body = {
            'owner_id': entity_id,
            'target_id': target_id,
            'relation': relation,
            'visibility_id': visibility_id,
        }
        if attitude is not None:
            body['attitude'] = attitude
        if two_way:
            body['two_way'] = True
        resp = self._client._request('POST', f'entities/{entity_id}/relations', json=body)
        data = resp.get('data') or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        result = {'data': {k: data.get(k) for k in ['id', 'owner_id', 'target_id', 'relation']}}
        _debug(f'  -> {result}')
        return result

    def update_relation(self, entity_id, relation_id, **fields):
        _debug(f'update_relation(entity_id={entity_id}, relation_id={relation_id}, {fields})')
        self._client._request('PATCH', f'entities/{entity_id}/relations/{relation_id}', json=fields)
        _debug('  -> succeeded')

    def delete_relation(self, entity_id, relation_id):
        _debug(f'delete_relation(entity_id={entity_id}, relation_id={relation_id})')
        self._client._request('DELETE', f'entities/{entity_id}/relations/{relation_id}')
        _debug('  -> succeeded')
        return True
