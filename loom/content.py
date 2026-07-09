"""Load a world from a human-editable JSON file.

This is the seam that makes the world 'editable data, not code' — and the
surface an authoring agent edits. Format::

    {
      "start_location": "cave_mouth",
      "locations": [
        {"id": "...", "name": "...", "description": "...",
         "exits": {"north": "other_id"}}
      ],
      "npcs": [
        {"id": "...", "name": "...", "description": "...", "location": "...",
         "persona": {"backstory": "...", "traits": [], "goals": [], "voice": "..."}}
      ]
    }
"""
from __future__ import annotations
import json
from .world import World, Location, Npc


def load_world(path: str) -> tuple[World, str]:
    """Return ``(world, start_location_id)``."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    world = World()
    for loc in data.get("locations", []):
        world.add_location(Location(
            id=loc["id"],
            name=loc.get("name", loc["id"]),
            description=loc.get("description", ""),
            exits=dict(loc.get("exits", {})),
        ))
    for n in data.get("npcs", []):
        world.add_entity(Npc(
            id=n["id"],
            name=n.get("name", n["id"]),
            description=n.get("description", ""),
            location_id=n.get("location"),
            persona=n.get("persona", {}),
        ))
    start = data.get("start_location")
    if not start and data.get("locations"):
        start = data["locations"][0]["id"]
    return world, start
