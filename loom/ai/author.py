"""The world-author: a brief -> a region's *flavour*, over a code-framed skeleton.

The AI-layer counterpart of the deterministic ``loom/authoring.py`` framer, and the
sibling of ``loom/ai/loot.py`` and ``intent.py``. The golden rule at world scale: code
owns the graph (ids, reciprocal exits, connectivity — all guaranteed by
``frame_skeleton``); the model authors ONLY flavour — a region's shape as a prose plan,
then each room's name and description, each NPC's persona, each item's lore — never a
structure it could break. Two model passes sit here:

  * **plan** — brief -> a small, flat proposal (rooms, rough links, who/what is where);
    the framer realises it safely, correcting any bad or colliding direction.
  * **flavour** — per entity, one small constrained call writes the words into the fixed
    skeleton (near-100% schema coverage; no topology under constraint).

``author_region`` orchestrates plan -> frame -> flavour -> survey -> a bounded repair
loop (the atlas is the judge; cap 3, targeted re-flavour, stop on clean/stall). Every
model reply is run through the code framer and the atlas before anything is trusted —
the mind never mutates a world, and structure is never taken on the model's word.

World-adjacent but world-*safe*: it reads an existing world to extend it and builds
throwaway candidate worlds to validate, but it never touches the live one.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .provider import LLMProvider
from .mind import _extract_json          # shared tolerant single-object JSON extraction
from ..atlas import survey
from ..authoring import (
    plan_schema, flavour_schema, frame_skeleton, apply_flavour, entity_kind,
    world_to_dicts, assemble, repair_targets, targets_signature, stalled,
    distinct_where, format_report,
)


# ------------------------------------------------------------------------ plan pass
def _plan_system(attach_context: str) -> str:
    base = (
        "You are the architect of a small region in a living text-adventure world. "
        "From the brief, design the SHAPE of the place — its bones, not its words. "
        "Lay out a handful of rooms, how they connect, and who and what stands in "
        "them. Names and descriptions come later; here you plan structure only.\n\n"
        "Rules:\n"
        "- Propose 3 to 6 rooms. Give each a short slug (lowercase, words joined by "
        "underscores, e.g. 'ruined_watchtower') and a one-phrase role.\n"
        "- Connect the rooms with links so the whole place is walkable: each link is "
        "a from-slug, a to-slug, and a compass/vertical direction. A player should be "
        "able to reach every room.\n"
        "- Place NPCs and items into rooms by slug. Mark an NPC 'anchored' if it keeps "
        "to one place (a keeper, a guard); leave it off for a wanderer.\n"
        "- Name the 'entry' room — the one the rest of the world arrives at.\n"
        "- Do NOT write descriptions, personas, or prose. Structure only.\n\n"
        "Reply with a single JSON object and nothing else, matching the given shape."
    )
    return base + (("\n\n" + attach_context) if attach_context else "")


async def propose_plan(provider: LLMProvider, brief: str, *,
                       attach_context: str = "") -> dict | None:
    """Ask the model for a region plan. Returns the parsed dict (the framer normalises
    it), or ``None`` when nothing object-shaped comes back (an unconstrained backend, or
    the offline ``FakeProvider``) so the caller degrades to 'could not author'. Never
    raises — a failed authoring run must never crash the tool that drove it."""
    system = _plan_system(attach_context)
    messages = [{"role": "user", "content": brief}]
    try:
        raw = await provider.complete(system, messages, schema=plan_schema())
    except Exception as exc:
        print(f"[loom] author plan failed ({type(exc).__name__}: {exc}); "
              "no region planned")
        return None
    obj = _extract_json(raw)
    return obj if isinstance(obj, dict) else None


# --------------------------------------------------------------------- flavour pass
def _flavour_system(kind: str, context: str, report: str) -> str:
    if kind == "npc":
        shape = ('{"name": "<the character\'s name>", "description": "<a sentence a '
                 'passer-by sees>", "persona": {"backstory": "<who they are, a few '
                 'sentences, second person>", "traits": ["<trait>", "..."], "goals": '
                 '["<what they want>", "..."], "voice": "<how they speak>"}}')
        who = ("You give a character in a text adventure their name and inner life. "
               "Where they stand and whether they roam is already fixed; write only "
               "who they are. Make the persona vivid and specific to the brief and "
               "this place — a real person with a history, wants, and a way of "
               "speaking, not a placeholder.")
    elif kind == "item":
        shape = ('{"name": "<the item\'s name, carrying its own article, e.g. \'a '
                 'cracked signal-horn\'>", "description": "<a sentence or two of lore, '
                 'read on examination>", "aliases": ["<a short noun it answers to>"]}')
        who = ("You give an object in a text adventure its name and lore. What it is "
               "and where it lies is already fixed; write only the words. Keep the "
               "name short; let the description carry the flavour.")
    else:  # room
        shape = ('{"name": "<the room\'s name, e.g. \'The Windswept Moor\'>", '
                 '"description": "<two or three sentences a player reads on entering>"}')
        who = ("You give a room in a text adventure its name and description. Its "
               "exits and what stands in it are already fixed; write only the words a "
               "player reads on entering. Make the place feel real and of a piece with "
               "the brief.")
    parts = [who, "", "The brief for the whole region:", context, "",
             "Reply with a single JSON object and nothing else, in this shape:",
             shape]
    if report:
        parts += ["",
                  "A validator found problems with the previous attempt — most likely "
                  "the text was empty or too thin. Fix them; write real, full flavour:",
                  report]
    return "\n".join(parts)


async def author_flavour(provider: LLMProvider, kind: str, brief: str, context: str, *,
                         report: str = "") -> dict | None:
    """Author one entity's flavour (``kind`` = room | npc | item) into the fixed
    skeleton. ``context`` grounds it (the brief + this entity's place in the region);
    ``report`` carries the atlas findings on a repair round. Returns the parsed flavour
    dict, or ``None`` on an empty/unconstrained reply (the caller keeps the placeholder
    and the atlas flags it as thin). Never raises."""
    system = _flavour_system(kind, f"{brief}\n\n{context}".strip(), report)
    messages = [{"role": "user", "content": "Write it."}]
    try:
        raw = await provider.complete(system, messages, schema=flavour_schema(kind))
    except Exception as exc:
        print(f"[loom] author flavour ({kind}) failed ({type(exc).__name__}: {exc}); "
              "leaving the placeholder")
        return None
    obj = _extract_json(raw)
    return obj if isinstance(obj, dict) else None


# ----------------------------------------------------------------- orchestration
@dataclass
class AuthorResult:
    """The outcome of an authoring run. ``ok`` is True iff the assembled world surveys
    with zero *errors*; ``view`` is that final :class:`~loom.atlas.AtlasView` (warnings
    and all) for the caller to render; ``region``/``attach`` are the validated output
    to write; ``rounds`` counts repair passes; ``reason`` explains a failure."""
    ok: bool = False
    region: dict | None = None
    attach: dict = field(default_factory=dict)
    view: object = None
    rounds: int = 0
    reason: str = ""


def _name_map(base_dicts: dict | None, region: dict) -> dict:
    names = {}
    for src in (base_dicts or {}, region):
        for loc in src.get("locations", []):
            names[loc["id"]] = loc.get("name") or loc["id"]
        for n in src.get("npcs", []):
            names[n["id"]] = n.get("name") or n["id"]
        for it in src.get("items", []):
            names[it["id"]] = it.get("name") or it["id"]
    return names


def _room_context(loc: dict, region: dict, names: dict) -> str:
    lines = [f"This is one room. Its exits (already built): "]
    exits = [f"{d} to {names.get(t, t)}" for d, t in loc.get("exits", {}).items()]
    lines[0] += ("; ".join(exits) if exits else "(none)") + "."
    here_npcs = [names.get(n["id"], n["id"]) for n in region.get("npcs", [])
                 if n.get("location") == loc["id"]]
    here_items = [names.get(i["id"], i["id"]) for i in region.get("items", [])
                  if i.get("holder") == loc["id"]]
    if here_npcs:
        lines.append("Here you find: " + ", ".join(here_npcs) + ".")
    if here_items:
        lines.append("Lying here: " + ", ".join(here_items) + ".")
    return "\n".join(lines)


def _npc_context(npc: dict, region: dict, names: dict) -> str:
    where = names.get(npc.get("location"), npc.get("location", "somewhere"))
    roam = "They wander the region." if npc.get("wanders") else "They keep to this place."
    return f"This character stands in: {where}. {roam}"


def _item_context(item: dict, region: dict, names: dict) -> str:
    where = names.get(item.get("holder"), item.get("holder", "somewhere"))
    return f"This object lies in: {where}."


def _context_for(kind: str, rec: dict, region: dict, names: dict) -> str:
    if kind == "npc":
        return _npc_context(rec, region, names)
    if kind == "item":
        return _item_context(rec, region, names)
    return _room_context(rec, region, names)


def _find(region: dict, eid: str):
    for loc in region.get("locations", []):
        if loc["id"] == eid:
            return "room", loc
    for n in region.get("npcs", []):
        if n["id"] == eid:
            return "npc", n
    for it in region.get("items", []):
        if it["id"] == eid:
            return "item", it
    return "", None


async def author_region(provider: LLMProvider, brief: str, *,
                        base_world=None, start: str | None = None,
                        attach_to: str | None = None, cap: int = 3,
                        prefix: str = "", log=None) -> AuthorResult:
    """Author a whole region from ``brief``: plan -> frame -> flavour -> survey -> a
    bounded repair loop. Extend-mode when ``base_world``/``attach_to`` are given (the
    region joins that world through one code-written edge); greenfield otherwise (the
    entry room becomes the start). Returns an :class:`AuthorResult`. Never raises."""
    def _say(msg):
        if log:
            log(msg)

    existing_ids = set()
    anchor_used = ()
    attach_context = ""
    if base_world is not None:
        existing_ids = set(base_world.locations) | set(base_world.entities)
        if attach_to and attach_to in base_world.locations:
            anchor = base_world.locations[attach_to]
            anchor_used = set(anchor.exits)
            desc = (anchor.description or "").strip()
            attach_context = (
                f"This region attaches to an existing place: \"{anchor.name}\""
                + (f" — {desc}" if desc else "")
                + ". The entry room should lead on naturally from it.")

    _say("planning the region…")
    plan = await propose_plan(provider, brief, attach_context=attach_context)
    if not plan:
        return AuthorResult(ok=False, reason="the model proposed no region plan "
                            "(an unconstrained backend, or the offline fake)")

    draft = frame_skeleton(plan, existing_ids, prefix=prefix,
                           anchor=attach_to if base_world is not None else None,
                           anchor_used_dirs=anchor_used)
    region = draft.region()
    base_dicts = world_to_dicts(base_world) if base_world is not None else None
    survey_start = start or draft.entry
    _say(f"framed {len(region['locations'])} rooms, {len(region['npcs'])} npcs, "
         f"{len(region['items'])} items — flavouring…")

    # First flavour pass — every entity once.
    names = _name_map(base_dicts, region)
    flavour = {}
    for kind, records in (("room", region["locations"]),
                          ("npc", region["npcs"]), ("item", region["items"])):
        for rec in records:
            fl = await author_flavour(provider, kind, brief,
                                      _context_for(kind, rec, region, names))
            if fl:
                flavour[rec["id"]] = fl
    apply_flavour(region, flavour)

    def _resurvey():
        world = assemble(region, base=base_dicts, attach=draft.attach)
        return survey(world, survey_start, source="(authored)")

    view = _resurvey()

    # Bounded repair loop — the atlas is the judge. Targeted re-flavour of the flagged
    # region entities, the full report fed back each round, stop on clean / stall / cap.
    region_ids = draft.ids
    prev_sig, prev_n = None, None
    rounds = 0
    while rounds < cap:
        targets = repair_targets(view.findings, region_ids)
        if not targets:
            break
        sig, n = targets_signature(targets), len(targets)
        if stalled(prev_sig, prev_n, sig, n):
            _say(f"repair stalled at {n} issue(s); stopping")
            break
        prev_sig, prev_n = sig, n
        report = format_report(view.findings)
        _say(f"repair round {rounds + 1}: {n} issue(s) — re-flavouring")
        names = _name_map(base_dicts, region)
        patched = False
        for eid in distinct_where(targets):
            kind, rec = _find(region, eid)
            if not kind:
                continue
            fl = await author_flavour(provider, kind, brief,
                                      _context_for(kind, rec, region, names),
                                      report=report)
            if fl:
                apply_flavour(region, {eid: fl})
                patched = True
        if not patched:
            _say("no repair could be applied; stopping")
            break
        rounds += 1
        view = _resurvey()

    ok = not view.errors
    reason = "" if ok else f"{len(view.errors)} structural error(s) remain after repair"
    return AuthorResult(ok=ok, region=region, attach=draft.attach, view=view,
                        rounds=rounds, reason=reason)
