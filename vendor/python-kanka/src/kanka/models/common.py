"""Common models used across the Kanka API."""

from datetime import datetime
from typing import Any, TypeVar

from pydantic import Field

from .base import KankaModel


class SearchResult(KankaModel):
    """Search result item from global search."""

    id: int
    entity_id: int
    name: str
    type: str | None = None
    url: str
    image: str | None = None
    is_private: bool = False
    tooltip: str | None = None
    tags: list[int] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GalleryImage(KankaModel):
    """Campaign gallery image."""

    id: str
    name: str | None = None
    is_folder: bool = False
    folder_id: str | None = None
    path: str | None = None
    ext: str | None = None
    size: int | None = None
    created_at: datetime | None = None
    created_by: int | None = None
    updated_at: datetime | None = None
    visibility_id: int | None = None
    focus_x: int | None = None
    focus_y: int | None = None


class EntityAsset(KankaModel):
    """Entity file, link, or alias asset."""

    id: int
    entity_id: int
    name: str
    type_id: int
    visibility_id: int | None = None
    is_pinned: bool = False
    is_private: bool = False
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    created_by: int | None = None
    updated_at: datetime | None = None
    updated_by: int | None = None
    url: str | None = Field(default=None, alias="_url")


class EntityImageData(KankaModel):
    """Image URL data for an entity."""

    uuid: str | None = None
    full: str | None = None
    thumbnail: str | None = None


class EntityImageInfo(KankaModel):
    """Entity image and header information."""

    image: EntityImageData | None = None
    header: EntityImageData | None = None


T = TypeVar("T", bound=KankaModel)


class EntityResponse[KankaModel]:
    """Single entity API response wrapper."""

    data: KankaModel  # type: ignore[type-arg]


class ListResponse[KankaModel]:
    """List API response wrapper with pagination."""

    data: list[KankaModel]  # type: ignore[type-arg]
    links: dict[str, Any]
    meta: dict[str, Any]
