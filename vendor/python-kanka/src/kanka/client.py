"""Main Kanka API client for interacting with the Kanka API."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .exceptions import (
    AuthenticationError,
    ForbiddenError,
    KankaException,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from .managers import EntityManager, RelationsManager
from .models.common import GalleryImage, SearchResult
from .models.entities import (
    Calendar,
    Character,
    Creature,
    Event,
    Family,
    Journal,
    Location,
    Note,
    Organisation,
    Quest,
    Race,
    Tag,
)


class KankaClient:
    """Main client for Kanka API interaction with automatic rate limit handling."""

    BASE_URL = "https://api.kanka.io/1.0"

    def __init__(
        self,
        token: str,
        campaign_id: int,
        *,
        enable_rate_limit_retry: bool = True,
        max_retries: int = 8,
        retry_delay: float = 1.0,
        max_retry_delay: float = 15.0,
    ):
        """Initialize the Kanka client."""
        self.token = token
        self.campaign_id = campaign_id
        self.enable_rate_limit_retry = enable_rate_limit_retry
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay

        self._debug_mode = os.environ.get("KANKA_DEBUG_MODE", "").lower() == "true"
        self._debug_dir = Path(os.environ.get("KANKA_DEBUG_DIR", "kanka_debug"))
        self._request_counter = 0

        if self._debug_mode:
            self._debug_dir.mkdir(exist_ok=True)

        import requests

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        self._init_managers()

    def _init_managers(self):
        """Initialize entity managers for each entity type."""
        # Core entities
        self._calendars = EntityManager(self, "calendars", Calendar)
        self._characters = EntityManager(self, "characters", Character)
        self._creatures = EntityManager(self, "creatures", Creature)
        self._events = EntityManager(self, "events", Event)
        self._families = EntityManager(self, "families", Family)
        self._journals = EntityManager(self, "journals", Journal)
        self._locations = EntityManager(self, "locations", Location)
        self._notes = EntityManager(self, "notes", Note)
        self._organisations = EntityManager(self, "organisations", Organisation)
        self._quests = EntityManager(self, "quests", Quest)
        self._races = EntityManager(self, "races", Race)
        self._tags = EntityManager(self, "tags", Tag)

        # Relations manager
        self.relations = RelationsManager(self)

    @property
    def calendars(self) -> EntityManager[Calendar]:
        """Access calendar entities."""
        return self._calendars

    @property
    def characters(self) -> EntityManager[Character]:
        """Access character entities."""
        return self._characters

    @property
    def creatures(self) -> EntityManager[Creature]:
        """Access creature entities."""
        return self._creatures

    @property
    def events(self) -> EntityManager[Event]:
        """Access event entities."""
        return self._events

    @property
    def families(self) -> EntityManager[Family]:
        """Access family entities."""
        return self._families

    @property
    def journals(self) -> EntityManager[Journal]:
        """Access journal entities."""
        return self._journals

    @property
    def locations(self) -> EntityManager[Location]:
        """Access location entities."""
        return self._locations

    @property
    def notes(self) -> EntityManager[Note]:
        """Access note entities."""
        return self._notes

    @property
    def organisations(self) -> EntityManager[Organisation]:
        """Access organisation entities."""
        return self._organisations

    @property
    def quests(self) -> EntityManager[Quest]:
        """Access quest entities."""
        return self._quests

    @property
    def races(self) -> EntityManager[Race]:
        """Access race entities."""
        return self._races

    @property
    def tags(self) -> EntityManager[Tag]:
        """Access tag entities."""
        return self._tags

    def search(self, term: str, page: int = 1) -> list[SearchResult]:
        """Search across all entity types."""
        params: dict[str, int | str] = {"page": page}
        response = self._request("GET", f"search/{term}", params=params)

        self._last_search_meta = response.get("meta", {})
        self._last_search_links = response.get("links", {})
        self._last_search_sync: str | None = response.get("sync")

        return [SearchResult(**item) for item in response["data"]]

    def entity(self, entity_id: int) -> dict[str, Any]:
        """Get a single entity by entity_id."""
        response = self._request("GET", f"entities/{entity_id}")
        return response["data"]  # type: ignore[no-any-return]

    def entities(
        self,
        page: int = 1,
        limit: int = 15,
        last_sync: str | None = None,
        **filters,
    ) -> list[dict[str, Any]]:
        """Access the /entities endpoint with filters."""
        params: dict[str, int | str] = {"page": page, "limit": limit}

        if last_sync is not None:
            params["lastSync"] = last_sync

        if "types" in filters and isinstance(filters["types"], list):
            params["types"] = ",".join(filters["types"])
        elif "types" in filters:
            params["types"] = filters["types"]

        if "tags" in filters and isinstance(filters["tags"], list):
            params["tags"] = ",".join(map(str, filters["tags"]))
        elif "tags" in filters:
            params["tags"] = filters["tags"]

        for key in ["name", "is_private", "created_by", "updated_by"]:
            if key in filters and filters[key] is not None:
                if isinstance(filters[key], bool):
                    params[key] = int(filters[key])
                else:
                    params[key] = filters[key]

        response = self._request("GET", "entities", params=params)
        self._last_entities_meta = response.get("meta", {})
        self._last_entities_links = response.get("links", {})
        self._last_entities_sync: str | None = response.get("sync")
        return response["data"]  # type: ignore[no-any-return]

    def _parse_rate_limit_headers(self, response) -> float | None:
        """Parse rate limit headers from response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                from email.utils import parsedate_to_datetime

                try:
                    retry_date = parsedate_to_datetime(retry_after)
                    delta = retry_date - parsedate_to_datetime(
                        response.headers.get("Date", "")
                    )
                    return float(delta.total_seconds())
                except Exception:
                    pass

        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")

        if remaining and reset:
            try:
                if int(remaining) == 0:
                    reset_time = int(reset)
                    current_time = int(time.time())
                    return max(0, reset_time - current_time)
            except (ValueError, TypeError):
                pass

        return None

    def _log_debug_request(
        self, method: str, url: str, request_data: dict, response, response_time: float
    ):
        """Log request and response to debug file."""
        if not self._debug_mode:
            return

        self._request_counter += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        endpoint_parts = url.replace(self.BASE_URL, "").strip("/").split("/")
        endpoint_name = "_".join(endpoint_parts[2:])
        if not endpoint_name:
            endpoint_name = "root"

        filename = (
            f"{self._request_counter:04d}_{timestamp}_{method}_{endpoint_name}.json"
        )
        filepath = self._debug_dir / filename

        debug_data = {
            "timestamp": datetime.now().isoformat(),
            "request_number": self._request_counter,
            "request": {
                "method": method,
                "url": url,
                "headers": dict(self.session.headers),
                "params": request_data.get("params", {}),
                "json": request_data.get("json", {}),
            },
            "response": {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "time_seconds": response_time,
                "body": None,
            },
        }

        try:
            response_body = response.json()
            if isinstance(debug_data["response"], dict):
                debug_data["response"]["body"] = response_body
        except Exception:
            if isinstance(debug_data["response"], dict):
                debug_data["response"]["body"] = response.text

        with open(filepath, "w") as f:
            json.dump(debug_data, f, indent=2, default=str)

    def _handle_response(self, response, method: str, endpoint: str) -> dict[str, Any]:
        """Handle HTTP response, raising appropriate exceptions for errors."""
        if response.status_code == 401:
            raise AuthenticationError("Invalid authentication token")
        elif response.status_code == 403:
            raise ForbiddenError("Access forbidden")
        elif response.status_code == 404:
            raise NotFoundError(f"Resource not found: {endpoint}")
        elif response.status_code == 422:
            error_data = response.json() if response.text else {}
            raise ValidationError(f"Validation error: {error_data}")
        elif response.status_code == 429:
            raise RateLimitError("Rate limit exceeded")
        elif response.status_code >= 400:
            raise KankaException(f"API error {response.status_code}: {response.text}")

        if method == "DELETE":
            return {}

        return response.json()  # type: ignore[no-any-return]

    def _request(self, method: str, endpoint: str, **kwargs) -> dict[str, Any]:
        """Make HTTP request to Kanka API with automatic retry on rate limits."""
        url = f"{self.BASE_URL}/campaigns/{self.campaign_id}/{endpoint}"

        attempts = 0
        delay = self.retry_delay
        last_exception = None

        while attempts <= self.max_retries:
            try:
                start_time = time.time()

                response = self.session.request(method, url, **kwargs)

                response_time = time.time() - start_time

                self._log_debug_request(method, url, kwargs, response, response_time)

                if response.status_code == 429:
                    attempts += 1
                    if not self.enable_rate_limit_retry or attempts > self.max_retries:
                        raise RateLimitError(
                            f"Rate limit exceeded after {attempts-1} retries"
                        )

                    suggested_delay = self._parse_rate_limit_headers(response)
                    if suggested_delay is not None:
                        delay = min(suggested_delay, self.max_retry_delay)

                    time.sleep(delay)
                    delay = min(delay * 2, self.max_retry_delay)
                    continue

                return self._handle_response(response, method, endpoint)

            except RateLimitError as e:
                last_exception = e
                if attempts >= self.max_retries:
                    raise

        if last_exception:
            raise last_exception
        raise KankaException("Unexpected error in request retry logic")

    def _upload_request(
        self,
        method: str,
        endpoint: str,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Make a multipart upload request to the Kanka API."""
        url = f"{self.BASE_URL}/campaigns/{self.campaign_id}/{endpoint}"

        saved_content_type = self.session.headers.pop("Content-Type", None)
        try:
            response = self.session.request(
                method, url, files=files, data=data, **kwargs
            )
        finally:
            if saved_content_type is not None:
                self.session.headers["Content-Type"] = saved_content_type

        return self._handle_response(response, method, endpoint)

    # Campaign Gallery methods
    def gallery(self, page: int = 1, limit: int = 30) -> list[GalleryImage]:
        """List campaign gallery images."""
        params: dict[str, int | str] = {"page": page, "limit": limit}
        response = self._request("GET", "images", params=params)
        self._last_gallery_meta = response.get("meta", {})
        self._last_gallery_links = response.get("links", {})
        return [GalleryImage(**item) for item in response["data"]]

    def gallery_get(self, image_id: str) -> GalleryImage:
        """Get a specific gallery image by UUID."""
        response = self._request("GET", f"images/{image_id}")
        return GalleryImage(**response["data"])

    def gallery_upload(
        self,
        file_path: str | Path,
        folder_id: str | None = None,
        visibility_id: int | None = None,
    ) -> GalleryImage:
        """Upload an image to the campaign gallery."""
        file_path = Path(file_path)
        data: dict[str, Any] = {}
        if folder_id is not None:
            data["folder_id"] = folder_id
        if visibility_id is not None:
            data["visibility_id"] = visibility_id

        with open(file_path, "rb") as f:
            files = {"file[]": (file_path.name, f)}
            response = self._upload_request("POST", "images", files=files, data=data)

        return GalleryImage(**response["data"][0])

    def gallery_delete(self, image_id: str) -> bool:
        """Delete a gallery image."""
        self._request("DELETE", f"images/{image_id}")
        return True

    @property
    def last_gallery_meta(self) -> dict[str, Any]:
        """Get metadata from the last gallery() call."""
        return getattr(self, "_last_gallery_meta", {})

    @property
    def last_gallery_links(self) -> dict[str, Any]:
        """Get pagination links from the last gallery() call."""
        return getattr(self, "_last_gallery_links", {})

    @property
    def last_search_meta(self) -> dict[str, Any]:
        """Get metadata from the last search() call."""
        return getattr(self, "_last_search_meta", {})

    @property
    def last_search_links(self) -> dict[str, Any]:
        """Get pagination links from the last search() call."""
        return getattr(self, "_last_search_links", {})

    @property
    def last_search_sync(self) -> str | None:
        """Get the sync timestamp from the last search() call."""
        return getattr(self, "_last_search_sync", None)

    @property
    def last_entities_meta(self) -> dict[str, Any]:
        """Get metadata from the last entities() call."""
        return getattr(self, "_last_entities_meta", {})

    @property
    def last_entities_links(self) -> dict[str, Any]:
        """Get pagination links from the last entities() call."""
        return getattr(self, "_last_entities_links", {})

    @property
    def last_entities_sync(self) -> str | None:
        """Get the sync timestamp from the last entities() call."""
        return getattr(self, "_last_entities_sync", None)

    @property
    def entities_has_next_page(self) -> bool:
        """Check if more pages are available from the last entities() call."""
        return bool(getattr(self, "_last_entities_links", {}).get("next"))
