"""Pydantic models for Kanka API entities."""

from .base import Entity, KankaModel, Post
from .common import (
    EntityAsset,
    EntityImageData,
    EntityImageInfo,
    GalleryImage,
    SearchResult,
)
from .entities import (
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
    Relation,
    Tag,
)

__all__ = [
    # Base models
    "KankaModel",
    "Entity",
    # Entity models
    "Calendar",
    "Character",
    "Creature",
    "Event",
    "Family",
    "Journal",
    "Location",
    "Note",
    "Organisation",
    "Quest",
    "Race",
    "Relation",
    "Tag",
    # Common models
    "Post",
    "SearchResult",
    "GalleryImage",
    "EntityAsset",
    "EntityImageData",
    "EntityImageInfo",
]
