"""Navigating a surveyed world (Phase 8, the Explorer's logic): the game-agnostic
half of the authoring workbench.

The atlas *gathers and validates* a world into an :class:`~loom.atlas.AtlasView`; this
module makes that view *navigable* — the two things a person exploring a world needs and
a line-REPL cannot give: a **map-model** (rooms as nodes, exits as edges, with the
reciprocity / one-way / reachability flags already computed, plus the incoming edges the
survey does not name) and a **search** across every entity (id / name / alias / tag /
type / containment, with a fuzzy fallback and a local-vs-global scope).

Both sit on *one* survey — the same ``AtlasView`` a renderer or the write side reads — so
the workbench UI holds a single gathered picture and this module is the only thing that
interprets it. Pure and offline like ``loom/atlas.py``: no I/O, no model calls, no new
dependencies (the fuzzy match is stdlib ``difflib``). The Textual UI in ``authoring/``
imports this; this never imports Textual — the framework stays UI-agnostic.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .atlas import AtlasView, ExitView

# The three kinds the Explorer browses. Rooms are places; NPCs and items live *in* a
# place (their "home location"), which is what scope and containment search resolve to.
KINDS = ("room", "npc", "item")


# ------------------------------------------------------------------------- map-model
@dataclass
class Entrance:
    """An edge *into* a room: the room it comes from, the direction taken there, and
    whether the trip is one-way (no exit leads back). The survey names a room's exits
    (edges out); the map-model adds these edges in — what a navigator needs to answer
    "how do I get here" and "who points at me"."""
    from_id: str
    from_name: str
    direction: str         # the direction FROM ``from_id`` that arrives here
    one_way: bool          # this room has no exit back to ``from_id``


@dataclass
class MapNode:
    """One room as a navigable node: its outgoing exits (jump links out — reused
    verbatim from the survey), its entrances (edges in), reachability from start, and
    content badges. ``flags`` mirrors the atlas warnings the UI wants to badge without
    re-deriving them (unreachable / dead-end / no-entrance)."""
    id: str
    name: str
    is_start: bool
    reachable: bool
    exits: list            # list[ExitView], authored order (the out-edges / jump links)
    entrances: list        # list[Entrance] (the in-edges)
    occupants: int         # characters here
    items: int             # items on the floor here
    flags: list = field(default_factory=list)

    @property
    def targets(self) -> list:
        """The distinct room ids this node's exits lead to (navigation destinations),
        in authored order, de-duplicated."""
        out, seen = [], set()
        for e in self.exits:
            if e.target not in seen:
                seen.add(e.target)
                out.append(e.target)
        return out


@dataclass
class MapModel:
    """The whole room graph as a navigable model. ``nodes`` maps id → :class:`MapNode`;
    ``order`` is the rooms in survey (load) order — a stable spine for the navigator."""
    start: str
    nodes: dict = field(default_factory=dict)
    order: list = field(default_factory=list)

    def node(self, room_id: str) -> "MapNode | None":
        return self.nodes.get(room_id)

    def neighbors(self, room_id: str) -> list:
        """The room ids reachable in one step from ``room_id`` (its exit targets)."""
        n = self.nodes.get(room_id)
        return n.targets if n else []

    def __iter__(self):
        for rid in self.order:
            yield self.nodes[rid]

    def __len__(self) -> int:
        return len(self.order)


def map_model(view: AtlasView) -> MapModel:
    """Derive a navigable :class:`MapModel` from a survey. Pure: reads the view, returns
    data. The out-edges and their flags come straight from the survey; this adds the
    in-edges (entrances), the per-node reachability/content badges, and a stable order."""
    room_names = {r.id: r.name for r in view.rooms}
    reachable = set(view.reachable_ids)

    # In-edges: for every exit A →(dir)→ B, record an Entrance on B from A. one_way is
    # the survey's own flag for that exit (no way leads back from B to A).
    entrances: dict = {r.id: [] for r in view.rooms}
    for r in view.rooms:
        for e in r.exits:
            if e.target in entrances:
                entrances[e.target].append(Entrance(
                    from_id=r.id, from_name=r.name,
                    direction=e.direction, one_way=e.one_way))

    nodes, order = {}, []
    for r in view.rooms:
        flags = []
        if view.start and r.id not in reachable:
            flags.append("unreachable")
        if not r.exits:
            flags.append("dead-end")
        if not entrances[r.id] and r.id != view.start:
            flags.append("no-entrance")
        nodes[r.id] = MapNode(
            id=r.id, name=r.name,
            is_start=(r.id == view.start),
            reachable=(r.id in reachable),
            exits=list(r.exits),
            entrances=entrances[r.id],
            occupants=len(r.occupants),
            items=len(r.floor),
            flags=flags,
        )
        order.append(r.id)
    return MapModel(start=view.start, nodes=nodes, order=order)


# ---------------------------------------------------------------------------- search
@dataclass
class SearchHit:
    """One search result: which ``kind`` of entity, its ``id`` and ``name``, the
    ``score`` it ranked at, the ``matched`` axes that earned that score (for a "why did
    this match" hint), and ``where`` — the id of the location that contains it ("" for a
    room, which *is* a place)."""
    kind: str
    id: str
    name: str
    score: float
    matched: list = field(default_factory=list)
    where: str = ""


# Ordering when scores tie: rooms, then NPCs, then items — the browsing spine.
_KIND_RANK = {"room": 0, "npc": 1, "item": 2}


def location_index(view: AtlasView) -> dict:
    """Map every NPC and item to its home location id (a room), resolving an item's
    holder chain (floor → a room; in a character's hands → that character's room; in a
    container → the container's home) to the room it ultimately sits in. "" when it
    resolves to nowhere or a broken reference. The anchor for scope and containment."""
    location_ids = {r.id for r in view.rooms}
    npc_loc = {n.id: (n.location if n.location_ok else "") for n in view.npcs}
    item_holder = {it.id: it.holder for it in view.items}

    def _home(start: str) -> str:
        cur, seen = start, set()
        while cur and cur not in seen:
            seen.add(cur)
            if cur in location_ids:
                return cur
            if cur in npc_loc:               # held by a character → its room
                return npc_loc[cur]
            if cur in item_holder:           # inside a container → chase the holder
                cur = item_holder[cur]
                continue
            return ""                        # names nothing we can resolve
        return ""

    index = {}
    for n in view.npcs:
        index[n.id] = npc_loc[n.id]
    for it in view.items:
        index[it.id] = _home(it.holder)
    return index


def search(view: AtlasView, query: str = "", *, scope: str = "",
           kinds=(), tag: str = "", limit: int = 50) -> list:
    """Rank the world's entities against ``query`` across every axis — id, name, alias,
    tag, type, description, persona — with a fuzzy fallback for typos.

    ``scope`` restricts to a room's neighbourhood (that room, its neighbours, and the
    NPCs/items that sit in it) — the local-vs-global toggle. ``kinds`` filters to
    room/npc/item; ``tag`` requires an item tag. An empty ``query`` is a pure browse:
    every in-scope entity passes (ordered by kind then name), so the box doubles as a
    filter. Returns :class:`SearchHit`\\ s, best first, capped at ``limit``.
    """
    q = query.strip().lower()
    want = set(kinds) & set(KINDS) if kinds else set(KINDS)
    loc_index = location_index(view)
    local = _scope_locations(view, scope) if scope else None

    hits = []
    for kind, ent in _iter_entities(view, want):
        where = "" if kind == "room" else loc_index.get(ent.id, "")
        anchor = ent.id if kind == "room" else where
        if local is not None and anchor not in local:
            continue
        if tag and not (kind == "item" and tag.lower() in {t.lower() for t in ent.tags}):
            continue
        score, matched = _score(kind, ent, q)
        if q and score <= 0.0:
            continue
        hits.append(SearchHit(kind=kind, id=ent.id, name=ent.name,
                              score=round(score, 4), matched=matched, where=where))

    hits.sort(key=lambda h: (-h.score, _KIND_RANK[h.kind],
                             h.name.lower(), h.id))
    return hits[:limit]


def _iter_entities(view: AtlasView, want: set):
    """Yield ``(kind, entity_view)`` for each requested kind, in the browsing spine
    order (rooms, then NPCs, then items)."""
    if "room" in want:
        for r in view.rooms:
            yield "room", r
    if "npc" in want:
        for n in view.npcs:
            yield "npc", n
    if "item" in want:
        for it in view.items:
            yield "item", it


def _scope_locations(view: AtlasView, scope: str) -> set:
    """The set of room ids that count as "local" to ``scope``: the room itself and its
    direct exit neighbours. An NPC/item is in-scope when its home location is in this
    set; a room is in-scope when it is this set."""
    local = {scope}
    room = next((r for r in view.rooms if r.id == scope), None)
    if room:
        local.update(e.target for e in room.exits)
    return local


def _score(kind: str, ent, q: str) -> tuple:
    """Score one entity against the lowercased query ``q`` and name the axes that
    matched. An empty query is a browse — every entity passes at a flat score. Otherwise
    the score is the strongest single axis (so an exact id beats a substring name), and
    ``matched`` lists every axis that contributed — the "why it matched" hint."""
    if not q:
        return 1.0, []

    eid = ent.id.lower()
    name = ent.name.lower()
    axes = []                                   # (score, axis)

    axes.append((_text_score(eid, q), "id"))
    axes.append((_text_score(name, q), "name"))

    if kind == "item":
        best_alias = max((_text_score(a.lower(), q) for a in ent.aliases), default=0.0)
        if best_alias:
            axes.append((best_alias * 0.9, "alias"))
        if q in {t.lower() for t in ent.tags}:
            axes.append((2.2, "tag"))
        if ent.tier and q == ent.tier.lower():
            axes.append((2.0, "tier"))
        if ent.theme and q in ent.theme.lower():
            axes.append((1.2, "theme"))

    desc = getattr(ent, "description", "")
    if desc and q in desc.lower():
        axes.append((1.0, "description"))

    if kind == "npc":
        persona_text = " ".join(_persona_values(ent.persona)).lower()
        if persona_text and q in persona_text:
            axes.append((1.0, "persona"))

    # Fuzzy fallback: a near-miss on id or name catches a typo, but only contributes a
    # small score so it never outranks a real substring/exact hit.
    fuzzy = max(SequenceMatcher(None, eid, q).ratio(),
                SequenceMatcher(None, name, q).ratio())
    if fuzzy >= 0.6:
        axes.append((fuzzy * 1.5, "fuzzy"))

    matched = [axis for sc, axis in axes if sc > 0.0]
    best = max((sc for sc, _ in axes), default=0.0)
    return best, matched


def _text_score(hay: str, q: str) -> float:
    """A graded literal match of ``q`` against a field: exact > prefix > substring > 0.
    The backbone signal; the fuzzy ratio is layered on top of these in ``_score``."""
    if not hay:
        return 0.0
    if hay == q:
        return 5.0
    if hay.startswith(q):
        return 3.5
    if q in hay:
        return 2.5
    return 0.0


def _persona_values(persona) -> list:
    """The flavour strings in a persona (backstory/traits/goals/voice/...), flattened —
    the free text an NPC search reads. Agnostic to which keys a game uses."""
    if not isinstance(persona, dict):
        return []
    out = []
    for val in persona.values():
        if isinstance(val, str):
            out.append(val)
        elif isinstance(val, (list, tuple)):
            out.extend(str(v) for v in val)
    return out
