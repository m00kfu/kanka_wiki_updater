"""Python client library for the Kanka API."""

from ._version import __version__
from .client import KankaClient
from .exceptions import (
    AuthenticationError,
    ForbiddenError,
    KankaException,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from .models import (
    Calendar,
    Character,
    Creature,
    Entity,
    EntityAsset,
    EntityImageData,
    EntityImageInfo,
    Event,
    Family,
    GalleryImage,
    Journal,
    KankaModel,
    Location,
    Note,
    Organisation,
    Post,
    Quest,
    Race,
    Relation,
    SearchResult,
    Tag,
)

__all__ = [
    'KankaClient',
    'KankaException',
    'NotFoundError',
    'ValidationError',
    'AuthenticationError',
    'ForbiddenError',
    'RateLimitError',
    # Base models
    'KankaModel',
    'Entity',
    # Entity models
    'Calendar',
    'Character',
    'Creature',
    'Event',
    'Family',
    'Journal',
    'Location',
    'Note',
    'Organisation',
    'Quest',
    'Race',
    'Relation',
    'Tag',
    # Common models
    'Post',
    'SearchResult',
    'GalleryImage',
    'EntityAsset',
    'EntityImageData',
    'EntityImageInfo',
    '__version__',
]
