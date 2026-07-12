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
