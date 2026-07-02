"""Base Pydantic models for Kanka API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KankaModel(BaseModel):
    """Base for all Kanka models with common configuration."""

    model_config = ConfigDict(
        extra="allow",
        validate_assignment=True,
        use_enum_values=True,
        populate_by_name=True,
    )


class Post(KankaModel):
    """Post/comment attached to entities."""

    id: int
    name: str
    entry: str
    entity_id: int
    created_by: int
    updated_by: int | None = None
    created_at: datetime
    updated_at: datetime
    visibility_id: int | None = None


class Entity(KankaModel):
    """Base model for all Kanka entities."""

    id: int
    entity_id: int
    name: str
    image: str | None = None
    image_full: str | None = None
    image_thumb: str | None = None
    is_private: bool = False
    tags: list[int] = Field(default_factory=list)
    created_at: datetime
    created_by: int
    updated_at: datetime
    updated_by: int | None = None
    entry: str | None = None

    posts: list[Post] | None = None
    attributes: list[dict] | None = None

    @property
    def entity_type(self) -> str:
        """Return the entity type name."""
        return self.__class__.__name__.lower()
