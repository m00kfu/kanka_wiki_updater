"""Entity managers for Kanka API."""

import contextlib
import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, TypeVar  # noqa: UP035

from .exceptions import NotFoundError
from .models.base import Entity, Post
from .models.common import EntityAsset, EntityImageInfo

if TYPE_CHECKING:
    from .client import KankaClient


T = TypeVar('T', bound=Entity)

# Pattern to identify SDK-managed asset names: name:12hexchars
_MANAGED_ASSET_RE = re.compile(r'^(.+):([0-9a-f]{12})$')


class EntityManager[T: Entity]:
    """Manages CRUD operations for a specific entity type."""

    def __init__(self, client: 'KankaClient', endpoint: str, model: type[T]):
        """Initialize the entity manager."""
        self.client = client
        self.endpoint = endpoint
        self.model = model

    def get(self, id: int, related: bool = False) -> T:
        """Get a single entity by ID."""
        params: dict[str, int | str] = {'related': 1} if related else {}
        url = f'{self.endpoint}/{id}'

        response = self.client._request('GET', url, params=params)
        return self.model(**response['data'])

    @property
    def pagination_meta(self) -> dict[str, Any]:
        """Get pagination metadata from the last list() call."""
        return getattr(self, '_last_meta', {})

    @property
    def pagination_links(self) -> dict[str, str | None]:
        """Get pagination links from the last list() call."""
        return getattr(self, '_last_links', {})

    @property
    def has_next_page(self) -> bool:
        """Check if there's a next page available."""
        return bool(self.pagination_links.get('next'))

    def list(
        self,
        page: int = 1,
        limit: int = 30,
        related: bool = False,
        last_sync: str | None = None,
        **filters,
    ) -> list[T]:
        """List entities with optional filters."""
        params: dict[str, int | str] = {'page': page, 'limit': limit}

        if related:
            params['related'] = 1

        if last_sync is not None:
            params['lastSync'] = last_sync

        for key, value in filters.items():
            if value is not None:
                if key == 'tags' and isinstance(value, list):
                    params['tags'] = ','.join(map(str, value))
                elif key == 'types' and isinstance(value, list):
                    params['types'] = ','.join(value)
                elif isinstance(value, bool):
                    params[key] = int(value)
                elif isinstance(value, list | tuple):
                    params[key] = ','.join(map(str, value))
                else:
                    params[key] = value

        response = self.client._request('GET', self.endpoint, params=params)

        self._last_meta = response.get('meta', {})
        self._last_links = response.get('links', {})
        self._last_sync: str | None = response.get('sync')

        return [self.model(**item) for item in response['data']]

    def create(
        self,
        *,
        images: dict[str, str | Path] | None = None,
        **kwargs,
    ) -> T:
        """Create a new entity."""
        data = kwargs.copy()

        for field in [
            'id',
            'entity_id',
            'created_at',
            'created_by',
            'updated_at',
            'updated_by',
        ]:
            data.pop(field, None)

        response = self.client._request('POST', self.endpoint, json=data)
        entity = self.model(**response['data'])

        if images:
            updated_entry = self._process_images_for_create(entity.entity_id, entity.entry, images)
            if updated_entry and updated_entry != entity.entry:
                url = f'{self.endpoint}/{entity.id}'
                response = self.client._request('PATCH', url, json={'entry': updated_entry})
                entity = self.model(**response['data'])

        return entity

    def update(
        self,
        entity_or_id: T | int,
        *,
        images: dict[str, str | Path] | None = None,
        **kwargs,
    ) -> T:
        """Update an entity with partial data."""
        if isinstance(entity_or_id, int):
            entity_id = entity_or_id
            data = kwargs
        else:
            entity = entity_or_id
            entity_id = entity.id

            updates = entity.model_copy(update=kwargs)

            data = updates.model_dump(
                exclude_unset=True,
                exclude={
                    'id',
                    'entity_id',
                    'created_at',
                    'created_by',
                    'updated_at',
                    'updated_by',
                },
            )

            original_data = entity.model_dump(
                exclude={
                    'id',
                    'entity_id',
                    'created_at',
                    'created_by',
                    'updated_at',
                    'updated_by',
                }
            )
            data = {k: v for k, v in data.items() if k not in original_data or original_data[k] != v}

        if images:
            if isinstance(entity_or_id, int):
                eid = self._extract_entity_id(self.get(entity_or_id))
            else:
                eid = entity_or_id.entity_id

            entry = data.get('entry')
            if entry is None and not isinstance(entity_or_id, int):
                entry = entity_or_id.entry
            if entry is None:
                fetched = self.get(entity_id)
                entry = fetched.entry

            updated_entry = self._process_images_for_update(eid, entry, images)
            if updated_entry is not None:
                data['entry'] = updated_entry

        if not data:
            if isinstance(entity_or_id, int):
                return self.get(entity_id)
            else:
                return entity_or_id

        url = f'{self.endpoint}/{entity_id}'
        response = self.client._request('PATCH', url, json=data)
        return self.model(**response['data'])

    def delete(self, entity_or_id: T | int) -> bool:
        """Delete an entity."""
        entity_id = entity_or_id if isinstance(entity_or_id, int) else entity_or_id.id

        url = f'{self.endpoint}/{entity_id}'
        self.client._request('DELETE', url)
        return True

    @property
    def last_page_meta(self) -> dict[str, Any]:
        """Get metadata from the last list() call."""
        return getattr(self, '_last_meta', {})

    @property
    def last_page_links(self) -> dict[str, Any]:
        """Get pagination links from the last list() call."""
        return getattr(self, '_last_links', {})

    @property
    def last_sync(self) -> str | None:
        """Get the sync timestamp from the last list() call."""
        return getattr(self, '_last_sync', None)

    def _extract_entity_id(self, entity_or_id: T | int) -> int:
        """Extract entity_id from an entity object or integer."""
        if isinstance(entity_or_id, int):
            return entity_or_id
        return entity_or_id.entity_id

    @staticmethod
    def _compute_file_hash(file_path: str | Path) -> str:
        """Compute SHA-256 hash of a file, returning first 12 hex chars."""
        h = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()[:12]

    @staticmethod
    def _format_managed_asset_name(name: str, hash_prefix: str) -> str:
        """Build a managed asset name: truncated_name:hash12."""
        max_name_len = 32
        truncated = name[:max_name_len]
        return f'{truncated}:{hash_prefix}'

    @staticmethod
    def _parse_managed_asset_name(asset_name: str) -> tuple[str, str] | None:
        """Parse a managed asset name back to (name, hash)."""
        m = _MANAGED_ASSET_RE.match(asset_name)
        if m:
            return m.group(1), m.group(2)
        return None

    @staticmethod
    def _extract_gallery_uuid(url: str | None) -> str | None:
        """Extract gallery image UUID from a CDN URL."""
        if not url:
            return None
        m = re.search(
            r'/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.\w+',
            url,
        )
        return m.group(1) if m else None

    @staticmethod
    def _rewrite_image_srcs(html: str, url_map: dict[str, str]) -> str:
        """Rewrite <img src="key"> tags, replacing keys with CDN URLs."""
        for placeholder, cdn_url in url_map.items():
            escaped = re.escape(placeholder)
            html = re.sub(
                rf'src=(["\']){escaped}\1',
                f'src=\\1{cdn_url}\\1',
            )
        return html

    def _process_images_for_create(
        self,
        entity_id: int,
        entry: str | None,
        images: dict[str, str | Path],
    ) -> str | None:
        """Upload images as assets and rewrite entry HTML."""
        if not images or not entry:
            return entry

        url_map: dict[str, str] = {}
        for placeholder, file_path in images.items():
            file_path = Path(file_path)
            file_hash = self._compute_file_hash(file_path)
            managed_name = self._format_managed_asset_name(placeholder, file_hash)
            asset = self.create_file_asset(entity_id, file_path, name=managed_name)
            if asset.url:
                url_map[placeholder] = asset.url

        if url_map:
            return self._rewrite_image_srcs(entry, url_map)
        return entry

    def _process_images_for_update(
        self,
        entity_id: int,
        entry: str | None,
        images: dict[str, str | Path],
    ) -> str | None:
        """Handle image assets for update."""
        if not images or not entry:
            return entry

        existing_assets = self.list_assets(entity_id)
        managed: dict[str, tuple[str, EntityAsset]] = {}
        for asset in existing_assets:
            parsed = self._parse_managed_asset_name(asset.name)
            if parsed:
                name_part, hash_part = parsed
                managed[name_part] = (hash_part, asset)

        url_map: dict[str, str] = {}
        used_names: set[str] = set()

        for placeholder, file_path in images.items():
            file_path = Path(file_path)
            file_hash = self._compute_file_hash(file_path)
            lookup_name = placeholder[:32]
            used_names.add(lookup_name)

            if lookup_name in managed:
                old_hash, old_asset = managed[lookup_name]
                if old_hash == file_hash:
                    if old_asset.url:
                        url_map[placeholder] = old_asset.url
                    continue
                else:
                    self.delete_asset(entity_id, old_asset.id, delete_gallery_image=True)

            managed_name = self._format_managed_asset_name(placeholder, file_hash)
            asset = self.create_file_asset(entity_id, file_path, name=managed_name)
            if asset.url:
                url_map[placeholder] = asset.url

        for name_part, (_, old_asset) in managed.items():
            if name_part not in used_names:
                self.delete_asset(entity_id, old_asset.id, delete_gallery_image=True)

        if url_map:
            return self._rewrite_image_srcs(entry, url_map)
        return entry

    # Posts functionality
    def list_posts(self, entity_or_id: T | int, page: int = 1, limit: int = 30) -> List[Post]:  # noqa: UP006
        """List posts for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        params: dict[str, int | str] = {'page': page, 'limit': limit}

        url = f'entities/{entity_id}/posts'
        response = self.client._request('GET', url, params=params)

        self._last_posts_meta = response.get('meta', {})
        self._last_posts_links = response.get('links', {})

        return [Post(**item) for item in response['data']]

    def create_post(
        self,
        entity_or_id: T | int,
        name: str,
        entry: str,
        *,
        images: dict[str, str | Path] | None = None,
        visibility_id: int | None = None,
        **kwargs,
    ) -> Post:
        """Create a post for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        if images:
            updated_entry = self._process_images_for_create(entity_id, entry, images)
            if updated_entry is not None:
                entry = updated_entry

        data: dict[str, Any] = {'name': name, 'entry': entry, **kwargs}
        if visibility_id is not None:
            data['visibility_id'] = visibility_id

        url = f'entities/{entity_id}/posts'
        response = self.client._request('POST', url, json=data)
        return Post(**response['data'])

    def get_post(self, entity_or_id: T | int, post_id: int) -> Post:
        """Get a specific post for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        url = f'entities/{entity_id}/posts/{post_id}'
        response = self.client._request('GET', url)
        return Post(**response['data'])

    def update_post(
        self,
        entity_or_id: T | int,
        post_id: int,
        *,
        images: dict[str, str | Path] | None = None,
        visibility_id: int | None = None,
        **kwargs,
    ) -> Post:
        """Update a post for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        if images:
            entry = kwargs.get('entry')
            if entry is None:
                post = self.get_post(entity_or_id, post_id)
                entry = post.entry
            updated_entry = self._process_images_for_update(entity_id, entry, images)
            if updated_entry is not None:
                kwargs['entry'] = updated_entry

        if visibility_id is not None:
            kwargs['visibility_id'] = visibility_id

        url = f'entities/{entity_id}/posts/{post_id}'
        response = self.client._request('PATCH', url, json=kwargs)
        return Post(**response['data'])

    def delete_post(self, entity_or_id: T | int, post_id: int) -> bool:
        """Delete a post for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        url = f'entities/{entity_id}/posts/{post_id}'
        self.client._request('DELETE', url)
        return True

    @property
    def last_posts_meta(self) -> dict[str, Any]:
        """Get metadata from the last list_posts() call."""
        return getattr(self, '_last_posts_meta', {})

    @property
    def last_posts_links(self) -> dict[str, Any]:
        """Get pagination links from the last list_posts() call."""
        return getattr(self, '_last_posts_links', {})

    # Entity Assets functionality
    def list_assets(self, entity_or_id: T | int, page: int = 1, limit: int = 30) -> List[EntityAsset]:  # noqa: UP006
        """List assets for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)
        params: dict[str, int | str] = {'page': page, 'limit': limit}
        url = f'entities/{entity_id}/entity_assets'
        response = self.client._request('GET', url, params=params)
        self._last_assets_meta = response.get('meta', {})
        self._last_assets_links = response.get('links', {})
        return [EntityAsset(**item) for item in response['data']]

    def get_asset(self, entity_or_id: T | int, asset_id: int) -> EntityAsset:
        """Get a specific asset for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)
        url = f'entities/{entity_id}/entity_assets/{asset_id}'
        response = self.client._request('GET', url)
        return EntityAsset(**response['data'])

    def create_file_asset(
        self,
        entity_or_id: T | int,
        file_path: str | Path,
        name: str | None = None,
        visibility_id: int | None = None,
        is_pinned: bool = False,
    ) -> EntityAsset:
        """Upload a file asset to an entity."""
        entity_id = self._extract_entity_id(entity_or_id)
        file_path = Path(file_path)

        data: dict[str, Any] = {
            'type_id': 1,
            'name': name or file_path.stem,
        }
        if visibility_id is not None:
            data['visibility_id'] = visibility_id
        data['is_pinned'] = 1 if is_pinned else 0

        url = f'entities/{entity_id}/entity_assets'
        with open(file_path, 'rb') as f:
            files = {'file': (file_path.name, f)}
            response = self.client._upload_request('POST', url, files=files, data=data)

        return EntityAsset(**response['data'])

    def create_link_asset(
        self,
        entity_or_id: T | int,
        name: str,
        url: str,
        icon: str | None = None,
        visibility_id: int | None = None,
    ) -> EntityAsset:
        """Create a link asset on an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        metadata: dict[str, str] = {'url': url}
        if icon is not None:
            metadata['icon'] = icon

        data: dict[str, Any] = {
            'type_id': 2,
            'name': name,
            'metadata': metadata,
        }
        if visibility_id is not None:
            data['visibility_id'] = visibility_id

        endpoint = f'entities/{entity_id}/entity_assets'
        response = self.client._request('POST', endpoint, json=data)
        return EntityAsset(**response['data'])

    def create_alias_asset(
        self,
        entity_or_id: T | int,
        name: str,
        visibility_id: int | None = None,
    ) -> EntityAsset:
        """Create an alias asset on an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        data: dict[str, Any] = {
            'type_id': 3,
            'name': name,
        }
        if visibility_id is not None:
            data['visibility_id'] = visibility_id

        endpoint = f'entities/{entity_id}/entity_assets'
        response = self.client._request('POST', endpoint, json=data)
        return EntityAsset(**response['data'])

    def delete_asset(
        self,
        entity_or_id: T | int,
        asset_id: int,
        *,
        delete_gallery_image: bool = False,
    ) -> bool:
        """Delete an asset from an entity."""
        entity_id = self._extract_entity_id(entity_or_id)

        gallery_uuid: str | None = None
        if delete_gallery_image:
            asset = self.get_asset(entity_id, asset_id)
            gallery_uuid = self._extract_gallery_uuid(asset.url)

        url = f'entities/{entity_id}/entity_assets/{asset_id}'
        self.client._request('DELETE', url)

        if gallery_uuid:
            with contextlib.suppress(NotFoundError):
                self.client.gallery_delete(gallery_uuid)

        return True

    @property
    def last_assets_meta(self) -> dict[str, Any]:
        """Get metadata from the last list_assets() call."""
        return getattr(self, '_last_assets_meta', {})

    @property
    def last_assets_links(self) -> dict[str, Any]:
        """Get pagination links from the last list_assets() call."""
        return getattr(self, '_last_assets_links', {})

    # Entity Image functionality
    def get_image(self, entity_or_id: T | int) -> EntityImageInfo:
        """Get image information for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)
        url = f'entities/{entity_id}/image'
        response = self.client._request('GET', url)
        return EntityImageInfo(**response['data'])

    def set_image(
        self,
        entity_or_id: T | int,
        file_path: str | Path,
        is_header: bool = False,
    ) -> EntityImageInfo:
        """Set the main image or header image for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)
        file_path = Path(file_path)

        data: dict[str, Any] = {}
        if is_header:
            data['is_header'] = 1

        url = f'entities/{entity_id}/image'
        with open(file_path, 'rb') as f:
            files = {'file': (file_path.name, f)}
            response = self.client._upload_request('POST', url, files=files, data=data)

        return EntityImageInfo(**response['data'])

    def delete_image(self, entity_or_id: T | int, is_header: bool = False) -> bool:
        """Delete the main image or header image for an entity."""
        entity_id = self._extract_entity_id(entity_or_id)
        url = f'entities/{entity_id}/image'
        params: dict[str, int | str] = {}
        if is_header:
            params['is_header'] = 1
        self.client._request('DELETE', url, params=params)
        return True


class RelationsManager(EntityManager):
    """Manages relations between entities."""

    def __init__(self, client: 'KankaClient'):
        """Initialize the relations manager."""
        from .models.entities import Relation  # avoid circular import

        super().__init__(client, 'relations', Relation)  # type: ignore[arg-type]

    def list_for_entity(self, entity_id):
        response = self.client._request('GET', f'entities/{entity_id}/relations')
        data = response['data']
        if isinstance(data, list):
            return [Relation(**item) for item in data]
        return [Relation(**data)]

    def create(
        self,
        entity_id: int,
        target_id: int,
        relation: str,
        attitude: str | None = None,
        two_way: bool = False,
        visibility_id: int = 1,
    ):
        body = {'ownerId': entity_id, 'targetId': target_id, 'relation': relation, 'visibilityId': visibility_id}
        if attitude is not None:
            body['attitude'] = attitude
        if two_way:
            body['twoWay'] = True
        response = self.client._request('POST', f'entities/{entity_id}/relations', json=body)
        data = response['data']
        if isinstance(data, list):
            data = data[0] if data else {}
        return Relation(**data)

    def update(self, entity_id: int, relation_id: int, **fields):
        response = self.client._request('PATCH', f'entities/{entity_id}/relations/{relation_id}', json=fields)
        data = response['data']
        if isinstance(data, list):
            data = data[0] if data else {}
        return Relation(**data)

    def delete(self, entity_id: int, relation_id: int):
        self.client._request('DELETE', f'entities/{entity_id}/relations/{relation_id}')
        return True
