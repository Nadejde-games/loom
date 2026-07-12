"""World-model primitives. Pure data — no AI, no networking. These are the
nouns an authoring agent creates and edits."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Entity:
    id: str
    name: str
    description: str = ""


@dataclass
class Character(Entity):
    location_id: str | None = None


@dataclass
class Npc(Character):
    # Free-form persona the AI layer turns into a system prompt.
    # Conventional keys: backstory, traits[], goals[], voice.
    persona: dict = field(default_factory=dict)


@dataclass
class Player(Character):
    session_id: str | None = None


@dataclass
class Item(Entity):
    # What holds this item: the id of a location (lying on the floor), a
    # character (in their inventory), or later another item (a container).
    # This is the source of truth for where the item is; ``World`` keeps a
    # reverse index for fast lookup — exactly as ``Character.location_id`` is
    # the truth mirrored by ``Location.occupants``.
    holder: str | None = None
    # Extra nouns the item answers to, beyond its name — e.g. ["key", "brass"]
    # for "the ornate brass key". Used by name-resolution (loom/naming.py).
    aliases: list = field(default_factory=list)
    # Whether it can be picked up, dropped, or given. A fixed feature of the
    # scene (a wall carving, a fountain) is not portable.
    portable: bool = True
