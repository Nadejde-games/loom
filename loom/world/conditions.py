"""Standing environmental conditions — the persistent counterpart to the chronicle.

Where the chronicle is a *transient* feed of what happened (a line, then gone from
the window), a condition is a *standing* fact about a place that persists until it
is lifted: a storm over the clearing, night fallen on the road. The game-master
director sets one and later clears it; meanwhile it colors every look at that place
and rides into every mind's perception of it. This is the split the prior art
insists on (CircleMUD's announce-only weather forgot the standing state; LPMud and
Inform 7 keep the two apart) — a one-shot 'it begins' line is a chronicle beat; the
lasting condition lives here.

Held at the *world* level, keyed by location id — deliberately not buried on each
``Location``. That one indirection is free now and future-proof: region-wide
weather becomes a condition keyed by a region id that every room in the region
folds in, and a world-clock's time-of-day is one more entry each outdoor place
resolves — all without touching the room model. Game-agnostic and dependency-free:
the engine decides what is worth setting; this only remembers it.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Condition:
    """One standing environmental condition on a place.

    ``tag`` is a short handle ("storm", "night"): it de-dupes (setting the same tag
    again replaces it, so a place never accretes two storms) and is the handle the
    director clears by. ``text`` is the perceivable fragment, written to read
    naturally where a place is described ("A cold rain lashes the clearing.")."""
    tag: str
    text: str


class Conditions:
    """A world-level registry of standing conditions, keyed by location id.

    Within a location, conditions are keyed by ``tag`` so setting the same tag
    upserts (one storm, not a pile) while different tags coexist (a storm *and*
    nightfall). Insertion order within a location is preserved for stable
    rendering; re-setting an existing tag keeps its place.
    """

    def __init__(self) -> None:
        # location id -> {tag -> text}, order-preserving on both levels.
        self._by_location: dict[str, dict[str, str]] = {}
        # World-scope conditions: {tag -> text}, folded into *every* place. This is
        # the region/world generalisation this registry was built for (see the
        # module docstring) in its simplest form — one reserved scope that is
        # "everywhere". A world-clock's time-of-day lives here (one entry, not one
        # per room), and a look at any place reads it alongside that place's own
        # standing conditions.
        self._world: dict[str, str] = {}

    def set(self, location_id: str, tag: str, text: str) -> Condition:
        """Set — or replace — the condition ``tag`` at ``location_id``. Returns the
        stored ``Condition``. Callers (the action handler) are the gate for
        non-empty tag/text; this only records what it is given, stripped."""
        tag = (tag or "").strip()
        text = (text or "").strip()
        self._by_location.setdefault(location_id, {})[tag] = text
        return Condition(tag=tag, text=text)

    def clear(self, location_id: str, tag: str) -> bool:
        """Lift the condition ``tag`` at ``location_id``. Returns True if one was
        present and removed, False if there was nothing there to lift."""
        tag = (tag or "").strip()
        at = self._by_location.get(location_id)
        if not at or tag not in at:
            return False
        del at[tag]
        if not at:                       # keep the registry sparse — no empty rooms
            self._by_location.pop(location_id, None)
        return True

    def at(self, location_id: str) -> list[Condition]:
        """The standing conditions at a location, in the order they were set —
        each with its tag (the director's clear handle) and its text."""
        at = self._by_location.get(location_id)
        if not at:
            return []
        return [Condition(tag=t, text=x) for t, x in at.items()]

    def texts(self, location_id: str) -> list[str]:
        """Just the perceivable fragments at a location — what a player or an NPC
        reads, tag-free (only the director needs the tag, to clear by it)."""
        at = self._by_location.get(location_id)
        return list(at.values()) if at else []

    # ---- world scope: conditions that hold everywhere ----
    def set_world(self, tag: str, text: str) -> Condition:
        """Set — or replace — a world-wide condition ``tag`` (e.g. the time of day).
        Upserts by tag exactly as the per-location ``set`` does, keeping insertion
        order for stable rendering. Folded into every place's perception."""
        tag = (tag or "").strip()
        text = (text or "").strip()
        self._world[tag] = text
        return Condition(tag=tag, text=text)

    def clear_world(self, tag: str) -> bool:
        """Lift the world-wide condition ``tag``. Returns True if one was present
        and removed, False if there was nothing to lift."""
        tag = (tag or "").strip()
        if tag not in self._world:
            return False
        del self._world[tag]
        return True

    def world_at(self) -> list[Condition]:
        """The standing world-wide conditions, in the order they were set — each
        with its tag (the clear handle) and its text."""
        return [Condition(tag=t, text=x) for t, x in self._world.items()]

    def world_texts(self) -> list[str]:
        """Just the perceivable fragments that hold everywhere — read by every
        place alongside its own standing conditions."""
        return list(self._world.values())

    # ---- persistence (Phase 5) ----
    # A plain-data view of the standing conditions and its inverse, so the
    # persistence layer serialises them without reaching into privates. The shape
    # mirrors the internal one exactly (both scopes are already plain str->str
    # maps), and insertion order — which drives stable rendering — survives a JSON
    # round-trip because dicts preserve it.
    def state(self) -> dict:
        """A JSON-ready snapshot: the per-location conditions and the world scope."""
        return {"by_location": {loc: dict(tags)
                                for loc, tags in self._by_location.items()},
                "world": dict(self._world)}

    def load_state(self, data: dict) -> None:
        """Replace all standing conditions from a ``state()`` snapshot. Tolerant of
        a missing scope (an older save) — that scope simply loads empty."""
        data = data or {}
        self._by_location = {loc: dict(tags)
                             for loc, tags in (data.get("by_location") or {}).items()}
        self._world = dict(data.get("world") or {})
