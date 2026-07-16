"""Quests: a director-offered goal, tracked per player, with a readable log.

The world-model counterpart of ``loom/world/conditions.py`` — a world-held,
per-key state primitive on the same footing as standing conditions, and the
first subsystem behind a director action rather than a one-off narrated line.

A ``Quest`` is a *definition* (title, summary, giver, objective) plus a single
player's *status*; the ``QuestBook`` holds them keyed by player id. Completion is
a **deterministic engine check bound to a seam event** — the player reaching the
objective place — never player-claimed (gameable) nor model-adjudicated at read
time (un-checkable): the shape the MUD/IF prior art settled on (CircleMUD mob
special-procs on a seam event, LPMud's per-player solved-flags, Evennia/WoW quest
logs, Inform scenes as world-state predicates).

Game-agnostic and dependency-free, like the rest of ``loom``. In-memory only —
quests reset on restart, exactly like conditions and memory today; persistence is
Phase 5. Only the ``reach`` objective is bound to an engine check in this slice;
``deliver`` (give a named item to a character) and ``acquire`` (hold a named item)
are the same pattern on other seam events, scoped for a later slice.
"""
from __future__ import annotations
from dataclasses import dataclass

# Objective kinds. Only REACH is bound to a completion check today.
REACH = "reach"

# Statuses.
ACTIVE = "active"
COMPLETE = "complete"


@dataclass
class Quest:
    """One offered goal and a single player's progress toward it.

    A quest is *per player*: one director offer creates one ``Quest`` for each
    present player, so each carries its own ``status`` and completes on its own.
    ``destination`` is meaningful for a ``reach`` objective (the location id to
    arrive at); other kinds will carry their own binding.
    """
    id: str
    title: str
    summary: str
    giver: str = ""              # who set it (the director's name), for the log
    kind: str = REACH            # objective kind
    destination: str = ""        # reach objective: the location id to arrive at
    status: str = ACTIVE

    @property
    def done(self) -> bool:
        return self.status == COMPLETE


class QuestBook:
    """Per-player quest log: player id -> the quests offered them, newest last.

    Passive until used — an empty book colors nothing. The director writes to it
    through ``offer`` (via the ``offer_quest`` action); the player reads it through
    the engine's ``quests`` command; the engine's arrival hook completes reach
    quests through ``complete_reached``. Quest ids are minted here, namespaced to
    the book and monotonic, so they are unique and deterministic for tests.
    """

    def __init__(self) -> None:
        self._by_player: dict[str, list] = {}
        self._seq = 0

    def offer(self, player_id: str, *, title: str, summary: str, giver: str = "",
              destination: str = "", kind: str = REACH) -> Quest | None:
        """Offer a player a quest, returning the new ``Quest`` — or ``None`` if that
        player already has an *active* quest with this title (a light de-dupe so a
        director that repeats itself never piles the same goal up in the log)."""
        for q in self._by_player.get(player_id, ()):
            if q.status == ACTIVE and q.title == title:
                return None
        self._seq += 1
        quest = Quest(id=f"quest_{self._seq}", title=title, summary=summary,
                      giver=giver, kind=kind, destination=destination,
                      status=ACTIVE)
        self._by_player.setdefault(player_id, []).append(quest)
        return quest

    def for_player(self, player_id: str) -> list:
        """Every quest a player holds, in the order offered — the ``quests`` view."""
        return list(self._by_player.get(player_id, ()))

    def active(self, player_id: str) -> list:
        """A player's still-open quests."""
        return [q for q in self._by_player.get(player_id, ())
                if q.status == ACTIVE]

    def complete_reached(self, player_id: str, location_id: str) -> list:
        """Mark every active ``reach`` quest whose destination is ``location_id``
        complete, and return the quests just completed (for the arrival notice).

        The deterministic completion check, bound to the player-arrival seam event.
        Idempotent: an already-complete quest is never re-fired, so re-entering the
        destination does nothing."""
        done = []
        for q in self._by_player.get(player_id, ()):
            if (q.status == ACTIVE and q.kind == REACH
                    and q.destination == location_id):
                q.status = COMPLETE
                done.append(q)
        return done
