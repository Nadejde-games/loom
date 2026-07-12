"""Load a world from human-editable JSON.

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
      ],
      "items": [
        {"id": "...", "name": "...", "description": "...", "holder": "...",
         "aliases": [], "portable": true}
      ]
    }

An item's ``holder`` is the id of whatever holds it — a location (on the
floor), a character (in inventory), or another item (a container).

Any top-level key beyond the structural ones above (e.g. ``"director"``, a world
``"tone"``, shared tables) is captured verbatim into ``world.meta`` — world-level
authored config the framework does not interpret but a game can read.

**One file or many.** ``path`` may be a single ``.json`` file *or* a directory of
them. A directory's ``*.json`` files are loaded in sorted order and merged —
locations/npcs/items accumulate, ``meta`` blocks shallow-merge — so a world can
start as one file and split by region later with no engine change. The first file
that declares ``start_location`` sets it (falling back to the first location).
"""
from __future__ import annotations
import json
import os
from .world import World, Location, Npc, Item

# Top-level keys the loader understands structurally; everything else is meta.
_STRUCTURAL = {"start_location", "locations", "npcs", "items"}


def load_world(path: str) -> tuple[World, str]:
    """Return ``(world, start_location_id)`` from a file or a directory of files."""
    world = World()
    start = None
    for data in _iter_sources(path):
        s = _ingest(world, data)
        if s and not start:            # first declared start_location wins
            start = s
    if not start and world.locations:  # fall back to the first location loaded
        start = next(iter(world.locations))
    return world, start


def _iter_sources(path: str):
    """Yield the parsed JSON dict of each source: one file, or every ``*.json`` in
    a directory (sorted, for deterministic merge order)."""
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.endswith(".json"):
                with open(os.path.join(path, name), "r", encoding="utf-8") as f:
                    yield json.load(f)
    else:
        with open(path, "r", encoding="utf-8") as f:
            yield json.load(f)


def _ingest(world: World, data: dict) -> str | None:
    """Add one source's entities to ``world`` and merge its meta; return the
    ``start_location`` it declares, if any. Idempotent across files — entities
    accumulate, so many files build one world."""
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
    for it in data.get("items", []):
        world.add_entity(Item(
            id=it["id"],
            name=it.get("name", it["id"]),
            description=it.get("description", ""),
            holder=it.get("holder"),
            aliases=list(it.get("aliases", [])),
            portable=bool(it.get("portable", True)),
        ))
    for key, val in data.items():
        if key in _STRUCTURAL:
            continue
        # Shallow-merge dict meta across files (so a later region file can extend a
        # block); otherwise last value wins.
        if isinstance(val, dict) and isinstance(world.meta.get(key), dict):
            world.meta[key].update(val)
        else:
            world.meta[key] = val
    return data.get("start_location")
