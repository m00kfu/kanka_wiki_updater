"""
Minimal Kanka API client.

Implements just what the sync pipeline needs: reading journals, characters
and locations (with pagination + lastSync support), and writing entry text
and relations. Built directly against Kanka's documented REST API rather
than a third-party wrapper, so behavior is transparent and easy to debug.

Docs: https://app.kanka.io/api-docs/1.0/overview
"""

import time

import requests

from . import config


class KankaError(RuntimeError):
    pass


class KankaClient:
    def __init__(self):
        self.base_url = f'{config.KANKA_BASE_URL}/campaigns/{config.KANKA_CAMPAIGN_ID}'
        self.session = requests.Session()
        self.session.headers.update(
            {
                'Authorization': f'Bearer {config.KANKA_TOKEN}',
                'Content-Type': 'application/json',
            }
        )
        self._last_request_time = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        wait = config.MIN_SECONDS_BETWEEN_REQUESTS - elapsed
        if wait > 0:
            time.sleep(wait)

    def _request(self, method, url_or_path, **kwargs):
        url = url_or_path if url_or_path.startswith('http') else f'{self.base_url}/{url_or_path}'
        for _ in range(5):
            self._throttle()
            resp = self.session.request(method, url, **kwargs)
            self._last_request_time = time.time()
            if resp.status_code == 429:
                retry_after = float(resp.headers.get('Retry-After', 5))
                time.sleep(retry_after)
                continue
            if resp.status_code >= 400:
                raise KankaError(f'{method} {url} -> {resp.status_code}: {resp.text[:500]}')
            return resp.json() if resp.text else {}
        raise KankaError(f'Gave up on {method} {url} after repeated 429s')

    def _get_all(self, path, params=None):
        """Depaginate a Kanka index endpoint, following links.next."""
        items = []
        url = f'{self.base_url}/{path}'
        next_params = dict(params or {})
        while url:
            data = self._request('GET', url, params=next_params)
            items.extend(data.get('data', []))
            url = (data.get('links') or {}).get('next')
            next_params = None  # the next URL already has its querystring baked in
        return items

    # -- Journals (session notes) ---------------------------------------

    def get_journals(self, since=None, journal_type=None):
        params = {}
        if since:
            params['lastSync'] = since
        if journal_type:
            params['type'] = journal_type
        return self._get_all('journals', params=params)

    # -- Characters / Locations ------------------------------------------

    def get_characters(self):
        return self._get_all('characters', params={'related': 1})

    def get_locations(self):
        return self._get_all('locations', params={'related': 1})

    def update_entity_entry(self, kind, entity_local_id, entry_text):
        """kind is 'characters' or 'locations'; entity_local_id is the
        type-specific `id` field (NOT `entity_id`).

        Kanka's entry field expects HTML.  Convert \\n\\n sequences into
        <br><br> so paragraph breaks render correctly in the wiki."""
        html = entry_text.replace('\n\n', '<br><br>').replace('\n', '<br>')
        return self._request('PATCH', f'{kind}/{entity_local_id}', json={'entry': html})

    def create_character(self, name, entry=None, **extra):
        """Create a new character. `name` is the only required field --
        everything else (entry, location_id, type, tags, ...) is optional."""
        body = {'name': name}
        if entry:
            html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
            body['entry'] = html
        body.update(extra)
        return self._request('POST', 'characters', json=body)

    def create_location(self, name, entry=None, **extra):
        """Create a new location. `name` is the only required field --
        everything else (entry, ...) is optional."""
        body = {'name': name}
        if entry:
            html = entry.replace('\n\n', '<br><br>').replace('\n', '<br>')
            body['entry'] = html
        body.update(extra)
        return self._request('POST', 'locations', json=body)

    def delete_character(self, local_id):
        return self._request('DELETE', f'characters/{local_id}')

    def delete_location(self, local_id):
        return self._request('DELETE', f'locations/{local_id}')

    # -- Relations --------------------------------------------------------

    def get_relations(self, entity_id):
        return self._get_all(f'entities/{entity_id}/relations')

    def create_relation(self, entity_id, target_id, relation, attitude=None, two_way=False, visibility_id=1):
        # visibility_id=1 ("all") is set explicitly rather than relying on
        # whatever Kanka defaults to when it's omitted.
        body = {'owner_id': entity_id, 'target_id': target_id, 'relation': relation, 'visibility_id': visibility_id}
        if attitude is not None:
            body['attitude'] = attitude
        if two_way:
            body['two_way'] = True
        return self._request('POST', f'entities/{entity_id}/relations', json=body)

    def update_relation(self, entity_id, relation_id, **fields):
        return self._request('PATCH', f'entities/{entity_id}/relations/{relation_id}', json=fields)

    def delete_relation(self, entity_id, relation_id):
        return self._request('DELETE', f'entities/{entity_id}/relations/{relation_id}')
