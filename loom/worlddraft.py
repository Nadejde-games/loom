"""The authoring draft + the safety gate (Phase 8, slice 3): the pure half of the
authoring agent.

The agent that an author chats with lives in ``authoring/`` and is free to carry heavy
dependencies (a native tool-calling model, an agentic framework). **This module is the
opposite pole — pure, offline, dependency-light, and the sole authority over a merge.**
The non-negotiable invariant of the whole slice lives here, in tested code and *not* in
the agent's good behaviour: a proposed change is built onto a **shadow copy** of the
staged world, the atlas surveys it, and it is committed **only if it surveys with zero
errors**. A dirty candidate never merges; it feeds the findings back instead.

The shape:

  * :class:`Draft` — the staged world as plain ``world.json`` dicts plus an undo stack.
    Loaded from a live :class:`~loom.world.World`; the authored file on disk is never
    touched until an explicit :meth:`Draft.to_world_json` save. Mutations never touch the
    draft — each *produces* a candidate.
  * the mutation producers — :func:`edit_entity`, :func:`spawn_entity`, :func:`fold_region`
    — each return a fresh candidate ``{locations, npcs, items}`` (a deep copy of the staged
    world with the change applied); the structure the survey must judge (an exit target, a
    holder, a location) is written here and *never trusted*.
  * the gate — :func:`propose` (survey a candidate, verdict = zero errors), :func:`commit`
    (fold a *clean* proposal in, pushing the prior state onto the undo stack), :func:`undo`
    (pop it back).

Pure and offline like ``loom/atlas.py`` and ``loom/authoring.py``: no I/O beyond the
opt-in :meth:`Draft.from_path` load, no model calls, no new dependencies. Every tool the
agent calls is a thin adapter over these functions, so the agent framework only ever
*orchestrates* — the merge decision is always the atlas's, enforced here.
"""
from __future__ import annotations
import copy
from dataclasses import dataclass, field

from .atlas import survey
from .authoring import assemble, world_to_dicts, mint_id
from .content import load_world

# The fields each kind of entity accepts an edit on. Flavour (name/description/persona/
# aliases) is always safe; the structural fields (exits/location/holder) are patched here
# and validated by the gate — never merged on trust.
_ROOM_FIELDS = ("name", "description", "exits")
_NPC_FIELDS = ("name", "description", "persona", "location", "wanders")
_ITEM_FIELDS = ("name", "description", "holder", "aliases", "portable")
_KIND_KEY = {"room": "locations", "npc": "npcs", "item": "items"}


# ------------------------------------------------------------------------------ draft
@dataclass
class Draft:
    """The staged world under authoring — plain ``world.json`` dicts + an undo stack.

    ``start`` is the start_location id; ``locations``/``npcs``/``items`` are the entity
    dicts (the shape ``loom/content.py`` loads and ``world_to_dicts`` emits); ``meta`` is
    the world-level config (director/clock/weather/loot/start_quests) carried untouched for
    the save path. ``checkpoints`` is the multi-level undo stack — a snapshot of the prior
    staged state per commit."""
    start: str
    locations: list
    npcs: list
    items: list
    meta: dict = field(default_factory=dict)
    checkpoints: list = field(default_factory=list)
    baseline: dict | None = None      # {locations,npcs,items} snapshot at the last save/load

    @classmethod
    def from_world(cls, world, start: str | None) -> "Draft":
        """Stage a live :class:`~loom.world.World` (structure via ``world_to_dicts``; the
        world-level meta carried verbatim). The world is read, never retained or mutated. The
        diff ``baseline`` starts as the loaded state, so a freshly loaded world shows no deltas."""
        d = world_to_dicts(world)
        draft = cls(start=start or "", locations=d["locations"], npcs=d["npcs"],
                    items=d["items"], meta=dict(getattr(world, "meta", {}) or {}))
        draft.baseline = draft.dicts()
        return draft

    @classmethod
    def from_path(cls, path: str) -> "Draft":
        """Stage the world at ``path`` (the one opt-in I/O; a fresh load each call)."""
        world, start = load_world(path)
        return cls.from_world(world, start)

    # ---- read (pure) ----
    def dicts(self) -> dict:
        """A deep copy of the staged ``{locations, npcs, items}`` — the safe-to-mutate base
        every candidate producer starts from."""
        return {"locations": copy.deepcopy(self.locations),
                "npcs": copy.deepcopy(self.npcs),
                "items": copy.deepcopy(self.items)}

    def world(self):
        """A fresh :class:`~loom.world.World` of the current staged state (``assemble``
        deep-copies, so the draft's own lists are never aliased into it)."""
        return assemble({"locations": self.locations, "npcs": self.npcs,
                         "items": self.items})

    def view(self, source: str = "(draft)"):
        """Survey the current staged world — the :class:`~loom.atlas.AtlasView` a caller
        renders (the same gatherer the gate judges a candidate with)."""
        return survey(self.world(), self.start, source=source)

    def to_world_json(self) -> dict:
        """The full ``world.json`` dict for the staged world — meta + start + entities — the
        content the explicit save writes. The canonical keys always win over ``meta`` (which
        never carries them), mirroring ``scripts/author.py:_combined_world``."""
        out = dict(copy.deepcopy(self.meta))
        out["start_location"] = self.start
        out["locations"] = copy.deepcopy(self.locations)
        out["npcs"] = copy.deepcopy(self.npcs)
        out["items"] = copy.deepcopy(self.items)
        return out

    def mark_saved(self) -> None:
        """Reset the diff baseline to the current staged state — called after the world is written
        to disk. The 'unsaved changes' the workbench highlights (badges + before/after) then go
        empty until the next edit; deltas are always measured against the last saved version."""
        self.baseline = self.dicts()


# ------------------------------------------------------------------- mutation producers
def edit_entity(draft: Draft, entity_id: str, patch: dict) -> dict:
    """A candidate world with ``patch`` merged onto ``entity_id`` (a room, npc, or item).

    Only the fields valid for that kind are applied, each **replacing** the field wholesale
    (``patch={"exits": {...}}`` sets the room's exits to exactly that). Structural fields
    (exits / location / holder) are written here and left for :func:`propose` to judge — a
    patch that dangles an exit or misplaces the entity produces a candidate the gate rejects,
    never a silent break. Returns the candidate ``{locations, npcs, items}``; the draft is
    untouched. Raises :class:`KeyError` if no entity bears ``entity_id``."""
    cand = draft.dicts()
    for kind, key in _KIND_KEY.items():
        fields = {"room": _ROOM_FIELDS, "npc": _NPC_FIELDS, "item": _ITEM_FIELDS}[kind]
        for rec in cand[key]:
            if rec["id"] == entity_id:
                for f in fields:
                    if f in patch:
                        rec[f] = copy.deepcopy(patch[f])
                return cand
    raise KeyError(f"no entity {entity_id!r} in the draft")


def spawn_entity(draft: Draft, kind: str, *, name: str, description: str = "",
                 where: str | None = None, persona: dict | None = None,
                 wanders: bool = False, aliases=None, portable: bool = True) -> tuple:
    """A candidate world with a new ``npc`` or ``item`` minted into it.

    The id is code-owned (:func:`~loom.authoring.mint_id`), so a spawn can never collide;
    ``where`` is the npc's room / the item's holder and must name a real place, which
    :func:`propose` verifies (a bad ``where`` surveys as a ``bad-location`` / ``bad-holder``
    error and is rejected). Returns ``(candidate, new_id)``; the draft is untouched. Raises
    :class:`ValueError` for a kind other than npc/item (rooms come from ``region_author``)."""
    if kind not in ("npc", "item"):
        raise ValueError(f"cannot spawn kind {kind!r}; expected 'npc' or 'item'")
    cand = draft.dicts()
    taken = ({l["id"] for l in cand["locations"]}
             | {n["id"] for n in cand["npcs"]} | {i["id"] for i in cand["items"]})
    new_id = mint_id(name or kind, taken)
    if kind == "npc":
        cand["npcs"].append({"id": new_id, "name": name or new_id,
                             "description": description, "location": where,
                             "persona": dict(persona or {}), "wanders": bool(wanders)})
    else:
        cand["items"].append({"id": new_id, "name": name or new_id,
                              "description": description, "holder": where,
                              "aliases": list(aliases or []), "portable": bool(portable)})
    return cand, new_id


def fold_region(draft: Draft, region: dict, attach: dict | None = None) -> dict:
    """A candidate world with a ``region`` (already validated by
    :func:`~loom.ai.author.author_region`) folded into the staged world through its
    ``attach`` edge — the ``region_author`` apply path. The attach edge is written onto the
    anchor room (mirroring ``scripts/author.py:_combined_world``); the gate re-surveys the
    whole result, so even a pre-validated region merges only if it stays clean *in context*.
    Returns the candidate; the draft is untouched."""
    cand = draft.dicts()
    if attach:
        for loc in cand["locations"]:
            if loc["id"] == attach.get("anchor"):
                loc.setdefault("exits", {})[attach["direction"]] = attach["entry"]
    region = copy.deepcopy(region or {})
    for key in ("locations", "npcs", "items"):
        cand[key] = cand[key] + region.get(key, [])
    return cand


# --------------------------------------------------------------------------- the gate
@dataclass
class Proposal:
    """The verdict on a candidate world. ``ok`` is True iff it surveys with **zero errors**
    (warnings — one-way exits, thin content — are allowed through, as everywhere on the write
    side); ``view`` is that :class:`~loom.atlas.AtlasView` (findings and all) for the caller
    to render as remediation prose; ``candidate`` is the dicts to commit if clean; ``diff`` is
    a human-readable change list against the current staged world."""
    ok: bool
    view: object
    candidate: dict
    diff: list = field(default_factory=list)


def propose(draft: Draft, candidate: dict, *, source: str = "(candidate)") -> Proposal:
    """Judge a candidate against the atlas — **the safety gate**. Pure: surveys a throwaway
    world built from the candidate (via ``assemble``) and never touches the draft. ``ok`` is
    True iff the survey reports no structural errors."""
    view = survey(assemble(candidate), draft.start, source=source)
    return Proposal(ok=not view.errors, view=view, candidate=candidate,
                    diff=diff_worlds(draft, candidate))


def commit(draft: Draft, proposal: Proposal) -> bool:
    """Fold a **clean** proposal into the staged world, pushing the prior staged state onto
    the undo stack. Refuses a proposal that did not survey clean — the invariant that a dirty
    candidate never merges is enforced right here. Returns True on commit, False if refused."""
    if not proposal.ok:
        return False
    draft.checkpoints.append((draft.start, copy.deepcopy(draft.locations),
                              copy.deepcopy(draft.npcs), copy.deepcopy(draft.items)))
    cand = proposal.candidate
    draft.locations = copy.deepcopy(cand["locations"])
    draft.npcs = copy.deepcopy(cand["npcs"])
    draft.items = copy.deepcopy(cand["items"])
    return True


def undo(draft: Draft) -> bool:
    """Pop the last commit off the undo stack, restoring the prior staged world. Returns True
    if a checkpoint was restored, False if the stack was empty."""
    if not draft.checkpoints:
        return False
    draft.start, draft.locations, draft.npcs, draft.items = draft.checkpoints.pop()
    return True


# ----------------------------------------------------------------------------- the diff
_KIND_LABEL = {"locations": "room", "npcs": "npc", "items": "item"}


def diff_status(before: dict | None, after: dict | None) -> dict:
    """``(kind, id) -> '+' | '-' | '~'`` for every entity that differs between two world
    snapshots (``{locations, npcs, items}`` dicts): ``'+'`` present only in ``after`` (added),
    ``'-'`` present only in ``before`` (removed), ``'~'`` in both but changed. Unchanged entities
    are omitted. Pure — the workbench drives the navigator's per-entity badges and the persistent
    before/after inspector from this, measuring the staged world against the last-saved baseline."""
    status: dict = {}
    for kind, key in (("room", "locations"), ("npc", "npcs"), ("item", "items")):
        b = {r["id"]: r for r in (before or {}).get(key, [])}
        a = {r["id"]: r for r in (after or {}).get(key, [])}
        for eid, rec in a.items():
            if eid not in b:
                status[(kind, eid)] = "+"
            elif rec != b[eid]:
                status[(kind, eid)] = "~"
        for eid in b:
            if eid not in a:
                status[(kind, eid)] = "-"
    return status


def diff_worlds(draft: Draft, candidate: dict) -> list:
    """A human-readable change list from the staged world to ``candidate`` — added (``+``),
    removed (``-``), and field-changed (``~``) entities, in room/npc/item order. What the
    confirm step shows an author before a merge; what the agent echoes to name the change."""
    before = {key: {r["id"]: r for r in getattr(draft, attr)}
              for key, attr in (("locations", "locations"), ("npcs", "npcs"),
                                ("items", "items"))}
    after = {key: {r["id"]: r for r in candidate.get(key, [])}
             for key in ("locations", "npcs", "items")}
    lines: list = []
    for key in ("locations", "npcs", "items"):
        b, a = before[key], after[key]
        label = _KIND_LABEL[key]
        for eid, rec in a.items():
            if eid not in b:
                lines.append(f"+ {label} {eid} ({rec.get('name', eid)})")
        for eid, rec in b.items():
            if eid not in a:
                lines.append(f"- {label} {eid} ({rec.get('name', eid)})")
        for eid, rec in a.items():
            if eid in b and rec != b[eid]:
                changed = [f for f in rec if rec.get(f) != b[eid].get(f)]
                changed += [f for f in b[eid] if f not in rec]
                lines.append(f"~ {label} {eid}: {', '.join(sorted(set(changed)))}")
    return lines
