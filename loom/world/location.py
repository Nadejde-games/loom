from __future__ import annotations
from dataclasses import dataclass, field
from .entity import Entity


@dataclass
class Location(Entity):
    # direction -> location id, e.g. {"north": "cave_interior"}
    exits: dict = field(default_factory=dict)
    # ids of entities currently present
    occupants: set = field(default_factory=set)
