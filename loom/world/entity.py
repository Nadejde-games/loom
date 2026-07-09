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
