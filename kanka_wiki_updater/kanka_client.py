"""Wrapper around python-kanka library for Kanka API."""

import kanka

from . import config


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
        return self._client.relations.list_for_entity(entity_id)  # returns list[Relation]

    def create_relation(self, entity_id, target_id, relation, attitude=None, two_way=False, visibility_id=1):
        rel = self._client.relations.create(
            entity_id, target_id, relation,
            attitude=attitude, two_way=two_way,
            visibility_id=visibility_id,
        )
        return {'data': {k: getattr(rel, k, None) for k in ['id', 'owner_id', 'target_id', 'relation']}}

    def update_relation(self, entity_id, relation_id, **fields):
        self._client.relations.update(entity_id, relation_id, **fields)

    def delete_relation(self, entity_id, relation_id):
        return self._client.relations.delete(entity_id, relation_id)  # returns bool
