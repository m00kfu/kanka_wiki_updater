"""Entity models for Kanka API."""

from pydantic import AliasChoices, Field

from .base import Entity, KankaModel


class Character(Entity):
    """Character entity representing people in the campaign."""

    location_id: int | None = None
    title: str | None = None
    age: str | None = None
    sex: str | None = None
    pronouns: str | None = None
    race_id: int | None = None
    type: str | None = None
    family_id: int | None = None
    is_dead: bool = False


class Location(Entity):
    """Location entity representing places in the campaign."""

    type: str | None = None
    map: str | None = None
    map_url: str | None = None
    is_map_private: int | None = None
    parent_location_id: int | None = None


class Organisation(Entity):
    """Organisation entity representing groups in the campaign."""

    location_id: int | None = None
    type: str | None = None
    organisation_id: int | None = None


class Note(Entity):
    """Note entity for campaign documentation."""

    type: str | None = None
    location_id: int | None = None


class Race(Entity):
    """Race entity representing character races/species."""

    type: str | None = None
    race_id: int | None = None


class Quest(Entity):
    """Quest entity representing objectives and missions."""

    type: str | None = None
    quest_id: int | None = None
    character_id: int | None = None


class Journal(Entity):
    """Journal entity for session logs and chronicles."""

    type: str | None = None
    date: str | None = None
    character_id: int | None = None


class Family(Entity):
    """Family entity representing family groups and lineages."""

    location_id: int | None = None
    family_id: int | None = None


class Event(Entity):
    """Event entity representing historical or campaign events."""

    type: str | None = None
    date: str | None = None
    location_id: int | None = None


class Creature(Entity):
    """Creature entity representing monsters and beasts."""

    type: str | None = None
    location_id: int | None = None


class Tag(Entity):
    """Tag entity for organizing and categorizing content."""

    type: str | None = None
    colour: str | None = None
    tag_id: int | None = None


class Calendar(Entity):
    """Calendar entity for campaign time tracking."""

    type: str | None = None
    date: str | None = None
    parameters: str | None = None
    months: list[dict] | None = None
    weekdays: list[str] | None = None
    years: dict | list | None = None
    seasons: list[dict] | None = None
    moons: list[dict] | None = None
    suffix: str | None = None
    has_leap_year: bool | None = None
    leap_year_amount: int | None = None
    leap_year_month: int | None = None
    leap_year_offset: int | None = None
    leap_year_start: int | None = None


class Relation(KankaModel):
    """Represents a relation between two entities."""

    id: int
    owner_id: int = Field(validation_alias=AliasChoices('ownerId', 'owner_id'))
    target_id: int = Field(validation_alias=AliasChoices('targetId', 'target_id'))
    relation: str
    attitude: str | None = None
    two_way: bool = Field(default=False, validation_alias=AliasChoices('twoWay', 'two_way'))
    visibility_id: int = Field(default=1, validation_alias=AliasChoices('visibilityId', 'visibility_id'))


# Forward reference updates
Character.model_rebuild()
Location.model_rebuild()
Organisation.model_rebuild()
Note.model_rebuild()
Race.model_rebuild()
Quest.model_rebuild()
Journal.model_rebuild()
Family.model_rebuild()
Event.model_rebuild()
Creature.model_rebuild()
Tag.model_rebuild()
Calendar.model_rebuild()
Relation.model_rebuild()
