"""Skeleton-first world authoring (B7 write side): the deterministic half.

The atlas *reads and validates* a world; this *frames* one. The generation research
(docs/spikes/authoring.md) settled the golden rule one step past the atlas: code does
not merely *validate* structure, it **generates** it. A model proposes a region in
prose — some rooms, who and what is in them, roughly how they connect — and this module
turns that proposal into a graph that is valid **by construction**:

  * ids are a code-owned namespace — the model never invents one, so nothing dangles;
  * every connection is written as a **reciprocal pair** (forward + opposite back-edge),
    so the one-way-exit bug that plagued hand-authored MUDs cannot occur;
  * every room is reachable — orphans the plan left unconnected are wired to the graph
    in code, and in extend-mode the region attaches to the existing world through one
    real exit, so reachability is a property of the build, not a hope;
  * NPCs and items land on real room ids.

The model authors only *flavour* afterwards (``loom/ai/author.py``), into a skeleton
that already holds together. The model's proposed directions are advisory — a collision
or a bad direction is resolved to a free one here, never emitted as a fault.

Pure and offline, like ``loom/atlas.py``: no I/O, no model calls, no new dependencies.
The plan comes in as plain dicts (the shape the plan pass returns); the region goes out
as plain dicts (the shape ``loom/content.py`` loads). The atlas is the judge on the far
side of the flavour pass — its ``Finding`` list drives the repair helpers at the foot of
this file.
"""
from __future__ import annotations
import copy
import re
from dataclasses import dataclass, field

from .atlas import KNOWN_DIRECTIONS, OPPOSITE, Finding
from .world import World, Location, Npc, Item

# The order code hands out directions when it must place an edge itself (a collision,
# a bad proposal, an orphan to connect, the attach edge). Stable, so framing is
# deterministic and offline tests reproduce.
_DIR_ORDER = ("north", "south", "east", "west", "up", "down", "in", "out")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- schemas
def plan_schema() -> dict:
    """The small, flat grammar for the plan pass — rooms, their proposed links, the
    NPCs and items in them, and which room is the entry. Deliberately NOT a ``oneOf``
    (the command_schema lesson: a weak model collapses a discriminated union to its
    simplest branch under strict decoding). Directions are an enum of the known set;
    everything the model says here is a *proposal* the framer realises safely."""
    directions = sorted(KNOWN_DIRECTIONS)
    return {
        "type": "object",
        "properties": {
            "rooms": {"type": "array", "items": {
                "type": "object",
                "properties": {"slug": {"type": "string"},
                               "role": {"type": "string"}},
                "required": ["slug"], "additionalProperties": False}},
            "links": {"type": "array", "items": {
                "type": "object",
                "properties": {"from": {"type": "string"},
                               "to": {"type": "string"},
                               "direction": {"enum": directions}},
                "required": ["from", "to"], "additionalProperties": False}},
            "npcs": {"type": "array", "items": {
                "type": "object",
                "properties": {"slug": {"type": "string"},
                               "room": {"type": "string"},
                               "role": {"type": "string"},
                               "anchored": {"type": "boolean"}},
                "required": ["slug", "room"], "additionalProperties": False}},
            "items": {"type": "array", "items": {
                "type": "object",
                "properties": {"slug": {"type": "string"},
                               "room": {"type": "string"},
                               "role": {"type": "string"}},
                "required": ["slug", "room"], "additionalProperties": False}},
            "entry": {"type": "string"},
        },
        "required": ["rooms"],
        "additionalProperties": False,
    }


def flavour_schema(kind: str) -> dict:
    """The small, flat grammar for one entity's flavour pass. Structure is already
    fixed by the framer, so the model only ever writes strings here — near-100% schema
    coverage, no topology under constraint (the write-side's safe side of the seam)."""
    if kind == "npc":
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "persona": {
                    "type": "object",
                    "properties": {
                        "backstory": {"type": "string"},
                        "traits": {"type": "array", "items": {"type": "string"}},
                        "goals": {"type": "array", "items": {"type": "string"}},
                        "voice": {"type": "string"},
                    },
                    "required": ["backstory", "traits", "goals", "voice"],
                    "additionalProperties": False},
            },
            "required": ["name", "description", "persona"],
            "additionalProperties": False}
    if kind == "item":
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "description"],
            "additionalProperties": False}
    # room (default)
    return {
        "type": "object",
        "properties": {"name": {"type": "string"},
                       "description": {"type": "string"}},
        "required": ["name", "description"],
        "additionalProperties": False}


# ----------------------------------------------------------------------------- model
@dataclass
class RegionDraft:
    """A structurally-valid region, still to be flavoured. ``locations``/``npcs``/
    ``items`` are plain dicts in the ``world.json`` schema (placeholder names, empty
    descriptions). ``attach`` is the single edge that folds the region into an existing
    world (empty for greenfield). ``entry`` is the region's doorway room id."""
    locations: list = field(default_factory=list)
    npcs: list = field(default_factory=list)
    items: list = field(default_factory=list)
    attach: dict = field(default_factory=dict)
    entry: str = ""

    @property
    def ids(self) -> set:
        """Every id this region owns — the scope the repair loop is allowed to touch
        (the existing world stays frozen)."""
        return ({loc["id"] for loc in self.locations}
                | {n["id"] for n in self.npcs} | {it["id"] for it in self.items})

    def region(self) -> dict:
        """The mutable region document the flavour pass writes into."""
        return {"locations": self.locations, "npcs": self.npcs, "items": self.items}


# --------------------------------------------------------------------------- framing
def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("_", str(text).strip().lower()).strip("_")
    return s or "room"


def _titleize(slug: str) -> str:
    return slug.replace("_", " ").title()


def _unique_id(base: str, taken: set) -> str:
    """A collision-free id from a slug, against ``taken`` (existing + already-assigned
    ids). The code-owned namespace guarantee: two entities never share an id, and a
    region id never collides with the world it joins."""
    root = _slugify(base)
    if root not in taken:
        return root
    n = 2
    while f"{root}_{n}" in taken:
        n += 1
    return f"{root}_{n}"


def mint_id(base: str, taken) -> str:
    """A collision-free id from ``base``, against the ``taken`` ids — the public seam onto
    the code-owned namespace. The staging surface (``loom/worlddraft.py``) mints a spawned
    entity's id through this, so a new thing can never collide with the world it joins,
    exactly as the framer's own ids cannot. Thin public wrapper over :func:`_unique_id`."""
    return _unique_id(base, set(taken))


def normalize_plan(plan: dict | None) -> dict:
    """Coerce a raw (model-authored) plan into a well-formed one — dropping malformed
    entries, deduping room slugs, and defaulting the entry — so the framer can trust
    its input. Never raises; a junk plan degrades to a single bare room."""
    plan = plan if isinstance(plan, dict) else {}
    rooms, seen = [], set()
    for r in plan.get("rooms") or []:
        if not isinstance(r, dict):
            continue
        slug = _slugify(r.get("slug") or r.get("role") or "room")
        while slug in seen:
            slug += "_x"
        seen.add(slug)
        rooms.append({"slug": slug, "role": str(r.get("role", ""))})
    if not rooms:
        rooms = [{"slug": "room", "role": ""}]
        seen = {"room"}
    entry = _slugify(plan.get("entry", "")) if plan.get("entry") else ""
    if entry not in seen:
        entry = rooms[0]["slug"]

    def _room_ref(val):
        s = _slugify(val) if val else entry
        return s if s in seen else entry

    links = []
    for ln in plan.get("links") or []:
        if not isinstance(ln, dict):
            continue
        a, b = _slugify(ln.get("from", "")), _slugify(ln.get("to", ""))
        if a in seen and b in seen and a != b:
            d = ln.get("direction", "")
            links.append({"from": a, "to": b,
                          "direction": d if d in KNOWN_DIRECTIONS else ""})
    npcs = []
    for n in plan.get("npcs") or []:
        if not isinstance(n, dict) or not n.get("slug"):
            continue
        npcs.append({"slug": _slugify(n["slug"]), "room": _room_ref(n.get("room")),
                     "role": str(n.get("role", "")),
                     "anchored": bool(n.get("anchored", False))})
    items = []
    for it in plan.get("items") or []:
        if not isinstance(it, dict) or not it.get("slug"):
            continue
        items.append({"slug": _slugify(it["slug"]), "room": _room_ref(it.get("room")),
                      "role": str(it.get("role", ""))})
    return {"rooms": rooms, "entry": entry, "links": links,
            "npcs": npcs, "items": items}


def _reachable(locations: dict, start: str) -> set:
    """Rooms reachable from ``start`` over the exits built so far (edges are reciprocal
    pairs, so this is effectively undirected)."""
    seen, stack = set(), [start]
    while stack:
        rid = stack.pop()
        if rid in seen:
            continue
        seen.add(rid)
        stack.extend(t for t in locations[rid]["exits"].values() if t in locations)
    return seen


def frame_skeleton(plan: dict | None, existing_ids=(), *, prefix: str = "",
                   anchor: str | None = None, anchor_used_dirs=()) -> RegionDraft:
    """Realise a (model-proposed) plan as a structurally-valid :class:`RegionDraft`.

    ``existing_ids`` are the ids of the world being extended (so region ids never
    collide). ``anchor`` is the existing room the region attaches to (None for
    greenfield); ``anchor_used_dirs`` its already-used exit directions, so code picks a
    *free* one for the attach edge. Every invariant the atlas checks for *structure* is
    guaranteed on the way out; the model's directions are honoured when they fit and
    silently corrected when they don't."""
    p = normalize_plan(plan)
    taken = set(existing_ids)
    id_of_slug: dict = {}
    for room in p["rooms"]:
        rid = _unique_id(prefix + room["slug"], taken)
        taken.add(rid)
        id_of_slug[room["slug"]] = rid
    locations = {rid: {"id": rid, "name": _titleize(slug), "description": "",
                       "exits": {}}
                 for slug, rid in id_of_slug.items()}
    used: dict = {rid: set() for rid in id_of_slug.values()}

    def _pair(a_id: str, b_id: str, want: str) -> bool:
        """Write a reciprocal exit pair a<->b, preferring direction ``want``; returns
        False only if both rooms are saturated (all 8 directions used)."""
        order = ([want] if want in KNOWN_DIRECTIONS else []) + list(_DIR_ORDER)
        for d in order:
            if d in used[a_id] or OPPOSITE[d] in used[b_id]:
                continue
            locations[a_id]["exits"][d] = b_id
            locations[b_id]["exits"][OPPOSITE[d]] = a_id
            used[a_id].add(d)
            used[b_id].add(OPPOSITE[d])
            return True
        return False

    for ln in p["links"]:
        a, b = id_of_slug.get(ln["from"]), id_of_slug.get(ln["to"])
        if not a or not b or a == b:
            continue
        if b in locations[a]["exits"].values():   # already linked either way
            continue
        _pair(a, b, ln.get("direction", ""))

    entry_id = id_of_slug[p["entry"]]
    # Connectivity by construction: wire any room the plan left unreachable to a room
    # that is reachable, until the whole region hangs together from the entry.
    reached = _reachable(locations, entry_id)
    for rid in id_of_slug.values():
        if rid in reached:
            continue
        for target in list(reached):
            if _pair(rid, target, ""):
                break
        reached = _reachable(locations, entry_id)

    attach: dict = {}
    if anchor:
        anchor_used = set(anchor_used_dirs)
        for d in _DIR_ORDER:
            if d in anchor_used or OPPOSITE[d] in used[entry_id]:
                continue
            # The region side of the attach edge (entry -> anchor) is written here; the
            # world side (anchor -> entry) is applied at assemble time, where the anchor
            # location lives.
            locations[entry_id]["exits"][OPPOSITE[d]] = anchor
            used[entry_id].add(OPPOSITE[d])
            attach = {"anchor": anchor, "direction": d,
                      "entry": entry_id, "back": OPPOSITE[d]}
            break

    npcs = []
    for n in p["npcs"]:
        nid = _unique_id(prefix + n["slug"], taken)
        taken.add(nid)
        npcs.append({"id": nid, "name": _titleize(n["slug"]), "description": "",
                     "location": id_of_slug.get(n["room"], entry_id),
                     "persona": {}, "wanders": not n["anchored"]})
    items = []
    for it in p["items"]:
        iid = _unique_id(prefix + it["slug"], taken)
        taken.add(iid)
        items.append({"id": iid, "name": _titleize(it["slug"]), "description": "",
                      "holder": id_of_slug.get(it["room"], entry_id),
                      "aliases": [], "portable": True})

    return RegionDraft(locations=list(locations.values()), npcs=npcs, items=items,
                       attach=attach, entry=entry_id)


# --------------------------------------------------------------------------- flavour
_ROOM_FIELDS = ("name", "description")
_NPC_FIELDS = ("name", "description", "persona")
_ITEM_FIELDS = ("name", "description", "aliases")


def apply_flavour(region: dict, flavour_by_id: dict) -> None:
    """Merge model-authored flavour into the region in place, keyed by entity id. Only
    the flavour fields are ever touched — ids, exits, locations and holders (the
    code-owned structure) are immutable here, so flavour can never break the graph."""
    def _merge(rec, fields, fl):
        for k in fields:
            if k not in fl:
                continue
            v = fl[k]
            if k in ("aliases",) and isinstance(v, list):
                rec[k] = [str(x) for x in v]
            elif k == "persona" and isinstance(v, dict):
                rec[k] = v
            elif isinstance(v, str) and v.strip():
                rec[k] = v.strip()

    index = {}
    for loc in region.get("locations", []):
        index[loc["id"]] = (loc, _ROOM_FIELDS)
    for n in region.get("npcs", []):
        index[n["id"]] = (n, _NPC_FIELDS)
    for it in region.get("items", []):
        index[it["id"]] = (it, _ITEM_FIELDS)
    for eid, fl in flavour_by_id.items():
        if eid in index and isinstance(fl, dict):
            rec, fields = index[eid]
            _merge(rec, fields, fl)


def entity_kind(region: dict, eid: str) -> str:
    """"room" | "npc" | "item" | "" — which kind of region entity an id names (or ""
    if it is not the region's, e.g. an existing-world id the repair loop must not
    touch)."""
    if any(loc["id"] == eid for loc in region.get("locations", [])):
        return "room"
    if any(n["id"] == eid for n in region.get("npcs", [])):
        return "npc"
    if any(it["id"] == eid for it in region.get("items", [])):
        return "item"
    return ""


# -------------------------------------------------------------------------- assemble
def world_to_dicts(world: World) -> dict:
    """Serialise an existing world back to plain ``world.json`` dicts (structure only,
    no meta) — so a candidate world can be rebuilt fresh each repair round without ever
    mutating the loaded original."""
    locations = [{"id": loc.id, "name": loc.name, "description": loc.description,
                  "exits": dict(loc.exits)} for loc in world.locations.values()]
    npcs, items = [], []
    for ent in world.entities.values():
        if isinstance(ent, Npc):
            npcs.append({"id": ent.id, "name": ent.name,
                         "description": ent.description, "location": ent.location_id,
                         "persona": dict(ent.persona), "wanders": bool(ent.wanders)})
        elif isinstance(ent, Item):
            items.append({"id": ent.id, "name": ent.name,
                          "description": ent.description, "holder": ent.holder,
                          "aliases": list(ent.aliases), "portable": bool(ent.portable)})
    return {"locations": locations, "npcs": npcs, "items": items}


def _world_from_dicts(data: dict) -> World:
    world = World()
    for loc in data.get("locations", []):
        world.add_location(Location(id=loc["id"], name=loc.get("name", loc["id"]),
                                    description=loc.get("description", ""),
                                    exits=dict(loc.get("exits", {}))))
    for n in data.get("npcs", []):
        world.add_entity(Npc(id=n["id"], name=n.get("name", n["id"]),
                             description=n.get("description", ""),
                             location_id=n.get("location"),
                             persona=n.get("persona", {}),
                             wanders=bool(n.get("wanders", False))))
    for it in data.get("items", []):
        world.add_entity(Item(id=it["id"], name=it.get("name", it["id"]),
                              description=it.get("description", ""),
                              holder=it.get("holder"),
                              aliases=list(it.get("aliases", [])),
                              portable=bool(it.get("portable", True))))
    return world


def assemble(region: dict, *, base: dict | None = None, attach: dict | None = None) -> World:
    """Build a fresh candidate :class:`World` from a region and (optionally) the dicts
    of the world it extends, applying the attach edge to the anchor room. Pure: the
    inputs are deep-copied, so surveying a candidate never touches the loaded world —
    the loop can rebuild-and-survey every round with no aliasing."""
    combined = {"locations": [], "npcs": [], "items": []}
    base = copy.deepcopy(base) if base else {"locations": [], "npcs": [], "items": []}
    if attach:
        for loc in base["locations"]:
            if loc["id"] == attach["anchor"]:
                loc.setdefault("exits", {})[attach["direction"]] = attach["entry"]
    region = copy.deepcopy(region)
    for key in ("locations", "npcs", "items"):
        combined[key] = base.get(key, []) + region.get(key, [])
    return _world_from_dicts(combined)


# ---------------------------------------------------------------------- repair helpers
# The atlas findings the repair loop acts on: any structural error the model somehow
# introduced (near-impossible under skeleton-first, but the net is honest), plus
# thin-content on a region entity (the flavour pass returned nothing usable → re-ask).
REPAIR_WARNING_CODES = ("thin-content",)


def repair_targets(findings, region_ids) -> list:
    """The findings the loop will act on this round: those about a *region* entity that
    are either errors or a repairable-warning code. Existing-world ids are never a
    target — extend-mode freezes the world it joins."""
    ids = set(region_ids)
    return [f for f in findings if f.where in ids
            and (f.severity == "error" or f.code in REPAIR_WARNING_CODES)]


def targets_signature(findings) -> tuple:
    """A stable fingerprint of a finding set — unchanged across a round means the loop
    made no progress and should stop (the anti-thrashing guard)."""
    return tuple(sorted((f.code, f.where) for f in findings))


def stalled(prev_sig, prev_n, cur_sig, cur_n) -> bool:
    """True when a repair round did not make progress: the same finding set, or a count
    that did not strictly fall. Either means keep-iterating will only thrash."""
    if prev_sig is None:
        return False
    return cur_sig == prev_sig or cur_n >= prev_n


def distinct_where(findings) -> list:
    """The region ids named by these findings, de-duplicated, errors first — the
    entities to re-flavour this round, in priority order."""
    ordered, seen = [], set()
    for f in sorted(findings, key=lambda x: 0 if x.severity == "error" else 1):
        if f.where and f.where not in seen:
            seen.add(f.where)
            ordered.append(f.where)
    return ordered


def format_report(findings) -> str:
    """The full atlas report as feedback lines for a repair prompt — the whole current
    picture each round (not one finding), rich and localized (code · where · message),
    per the repair research."""
    if not findings:
        return "(no problems reported)"
    return "\n".join(f"- [{f.severity}] {f.code} @ {f.where or 'world'}: {f.message}"
                     for f in findings)
