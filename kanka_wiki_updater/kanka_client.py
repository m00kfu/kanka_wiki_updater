"""Thin HTTP wrapper around the Kanka API v1 using requests.Session."""

import os
import sys
import time as _time
from email.utils import parsedate_to_datetime as _parsed

import requests

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

        self._base_url = config.KANKA_BASE_URL.rstrip('/')
        self._campaign_id = int(config.KANKA_CAMPAIGN_ID)
        self._retry_on_rate_limit = True

        self._session = requests.Session()
        self._session.headers.update(
            {
                'Authorization': f'Bearer {config.KANKA_TOKEN}',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            }
        )

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make HTTP request to Kanka API with automatic retry on rate limits."""
        url = f'{self._base_url}/campaigns/{self._campaign_id}/{endpoint}'
        attempts = 0
        delay = 1.0
        max_delay = 15.0
        max_retries = 8

        while attempts <= max_retries:
            try:
                response = self._session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                raise KankaError(f'Request failed: {exc}') from exc

            if response.status_code == 429:
                attempts += 1
                if not self._retry_on_rate_limit or attempts > max_retries:
                    raise KankaError('Rate limit exceeded after retries')
                retry_after = response.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        try:
                            delta = _parsed(retry_after) - _parsed(response.headers.get('Date', ''))
                            delay = max(0, delta.total_seconds())
                        except Exception:
                            delay *= 2
                else:
                    remaining = response.headers.get('X-RateLimit-Remaining')
                    reset = response.headers.get('X-RateLimit-Reset')
                    if remaining and reset:
                        try:
                            if int(remaining) == 0:
                                delay = max(0, int(reset) - _time.time())
                        except (ValueError, TypeError):
                            pass
                    else:
                        delay *= 2
                delay = min(delay, max_delay)
                _time.sleep(delay)
                continue

            if response.status_code == 401:
                raise KankaError('Invalid authentication token')
            elif response.status_code == 403:
                raise KankaError('Access forbidden')
            elif response.status_code == 404:
                raise KankaError(f'Resource not found: {endpoint}')
            elif response.status_code >= 400:
                msg = response.text or f'HTTP {response.status_code}'
                raise KankaError(f'API error {response.status_code}: {msg}')

            if method == 'DELETE':
                return {}
            return response.json()

        raise KankaError('Unexpected error in request retry logic')

    # -- Journals (session notes) ---------------------------------------

    def get_journals(self, since=None, journal_type=None, journal_ids=None):
        params = {}
        if since:
            params['last_sync'] = since
        if journal_type:
            params['type'] = journal_type
        if journal_ids:
            params['filter[id]'] = journal_ids
        resp = self._request('GET', 'journals', params=params)
        return resp.get('data') or []

    # -- Characters / Locations ------------------------------------------

    def _get_all_pages(self, endpoint, page_size=50, extra_params=None):
        """Fetch all pages of results from a paginated Kanka API endpoint.

        Follows the `links.next` URL from each response until exhausted.
        Returns a combined list of all items across every page.
        """
        params = {'related': True}
        if extra_params:
            params.update(extra_params)
        params['page[size]'] = page_size

        all_items = []
        url = f'{self._base_url}/campaigns/{self._campaign_id}/{endpoint}'
        attempts = 0
        max_retries = 3

        while True:
            if attempts > max_retries:
                break
            try:
                response = self._session.request('GET', url, params=params)
            except requests.RequestException as exc:
                raise KankaError(f'Request failed: {exc}') from exc

            if response.status_code == 429:
                attempts += 1
                _time.sleep(3.0)
                continue

            data = response.json()
            items = data.get('data') or []
            all_items.extend(items)

            links = data.get('links', {})
            next_url = links.get('next')
            if not next_url:
                break
            url = next_url
            params.clear()
            attempts = 0

        return all_items

    def get_characters(self):
        return self._get_all_pages('characters') or []

    def get_locations(self):
        return self._get_all_pages('locations') or []

    def get_organizations(self):
        return self._get_all_pages('organisations') or []

    def get_creatures(self):
        return self._get_all_pages('creatures') or []

    def update_entity_entry(self, kind, entity_local_id, entry_text):
        """kind is 'characters', 'locations', 'organisations', or 'creatures'; entity_local_id is the
        type-specific `id` field (NOT `entity_id`)."""
        html = entry_text.replace('\n\n', '<br><br>').replace('\n', '<br>')
        if kind == 'characters':
            self._request('PATCH', f'characters/{entity_local_id}', json={'entry': html})
        elif kind == 'locations':
            self._request('PATCH', f'locations/{entity_local_id}', json={'entry': html})
        elif kind == 'organisations':
            self._request('PATCH', f'organisations/{entity_local_id}', json={'entry': html})
        elif kind == 'creatures':
            self._request('PATCH', f'creatures/{entity_local_id}', json={'entry': html})

    def create_character(self, name, entry=None, **extra):
        """Create a new character. `name` is the only required field."""
        data = {'name': name}
        if entry:
            html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
            data['entry'] = html
        data.update(extra)
        resp = self._request('POST', 'characters', json=data)
        char_data = resp.get('data') or {}
        return {'data': {k: char_data.get(k) for k in ['id', 'entity_id', 'name', 'entry']}}

    def create_location(self, name, entry=None, **extra):
        """Create a new location. `name` is the only required field."""
        data = {'name': name}
        if entry:
            html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
            data['entry'] = html
        data.update(extra)
        resp = self._request('POST', 'locations', json=data)
        loc_data = resp.get('data') or {}
        return {'data': {k: loc_data.get(k) for k in ['id', 'entity_id', 'name', 'entry']}}

    def delete_character(self, local_id):
        self._request('DELETE', f'characters/{local_id}')
        return True

    def delete_location(self, local_id):
        self._request('DELETE', f'locations/{local_id}')
        return True

    def create_creature(self, name, entry=None, **extra):
        """Create a new creature. `name` is the only required field."""
        data = {'name': name}
        if entry:
            html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
            data['entry'] = html
        data.update(extra)
        resp = self._request('POST', 'creatures', json=data)
        creature_data = resp.get('data') or {}
        return {'data': {k: creature_data.get(k) for k in ['id', 'entity_id', 'name', 'entry']}}

    def delete_creature(self, local_id):
        self._request('DELETE', f'creatures/{local_id}')
        return True

    # -- Relations --------------------------------------------------------

    def get_relations(self, entity_id):
        _debug(f'get_relations(entity_id={entity_id})')
        resp = self._request('GET', f'entities/{entity_id}/relations')
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
        resp = self._request('POST', f'entities/{entity_id}/relations', json=body)
        data = resp.get('data') or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        result = {'data': {k: data.get(k) for k in ['id', 'owner_id', 'target_id', 'relation']}}
        _debug(f'  -> {result}')
        return result

    def update_relation(self, entity_id, relation_id, **fields):
        _debug(f'update_relation(entity_id={entity_id}, relation_id={relation_id}, {fields})')
        self._request('PATCH', f'entities/{entity_id}/relations/{relation_id}', json=fields)
        _debug('  -> succeeded')

    def delete_relation(self, entity_id, relation_id):
        _debug(f'delete_relation(entity_id={entity_id}, relation_id={relation_id})')
        self._request('DELETE', f'entities/{entity_id}/relations/{relation_id}')
        _debug('  -> succeeded')
        return True
