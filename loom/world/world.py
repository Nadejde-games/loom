"""Container for all world state, plus simple spatial queries."""
from __future__ import annotations
from .entity import Entity, Character
from .location import Location


class World:
    def __init__(self):
        self.locations: dict[str, Location] = {}
        self.entities: dict[str, Entity] = {}  # characters, npcs, players, items

    # --- mutation ---
    def add_location(self, loc: Location) -> None:
        self.locations[loc.id] = loc

    def add_entity(self, ent: Entity) -> None:
        self.entities[ent.id] = ent
        loc_id = getattr(ent, "location_id", None)
        if loc_id and loc_id in self.locations:
            self.locations[loc_id].occupants.add(ent.id)

    def remove_entity(self, entity_id: str) -> None:
        ent = self.entities.pop(entity_id, None)
        loc = self.location_of(entity_id) if ent else None
        if loc:
            loc.occupants.discard(entity_id)

    def move(self, entity_id: str, dest_id: str) -> bool:
        ent = self.entities.get(entity_id)
        if not isinstance(ent, Character) or dest_id not in self.locations:
            return False
        if ent.location_id and ent.location_id in self.locations:
            self.locations[ent.location_id].occupants.discard(entity_id)
        ent.location_id = dest_id
        self.locations[dest_id].occupants.add(entity_id)
        return True

    # --- queries ---
    def occupants(self, location_id: str, exclude: str | None = None) -> list[Entity]:
        loc = self.locations.get(location_id)
        if not loc:
            return []
        return [self.entities[i] for i in loc.occupants
                if i in self.entities and i != exclude]

    def location_of(self, entity_id: str) -> Location | None:
        ent = self.entities.get(entity_id)
        loc_id = getattr(ent, "location_id", None)
        return self.locations.get(loc_id) if loc_id else None
