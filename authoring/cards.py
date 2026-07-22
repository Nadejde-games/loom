"""Per-entity inspector cards for the workbench — pure formatting over the survey.

The atlas renders a *whole world*; the Explorer needs *one entity at a time*, as a card
plus a set of navigable references (an exit, an occupant, a held item) that become the
UI's jump-links. This module turns a survey's :class:`RoomView`/:class:`NpcView`/
:class:`ItemView` into ``(text, links)`` — plain text (no markup, so a bracket in a
description is never mis-parsed) and a list of ``(label, ref)`` where ``ref`` is a packed
``"kind:id"`` the app resolves back to a selection.

Pure and Textual-free: it reads the same :class:`~loom.atlas.AtlasView` the app holds and
the map-model derives from, so it is unit-tested without a UI (tests/test_workbench.py).
The cards are the survey's *raw record* (the "examine" view), deliberately distinct from
the player-facing "look": an NPC shows its persona sheet, an item its forge tags.
"""
from __future__ import annotations
import textwrap

from loom.explore import MapModel, location_index


def pack_ref(kind: str, entity_id: str) -> str:
    """Pack a selection reference the app round-trips through a widget id/option id."""
    return f"{kind}:{entity_id}"


def parse_ref(ref: str) -> tuple:
    """Unpack ``"kind:id"`` → ``(kind, id)``; ``("", "")`` if malformed."""
    kind, sep, entity_id = (ref or "").partition(":")
    return (kind, entity_id) if sep else ("", "")


def card(view, m: MapModel, kind: str, entity_id: str) -> tuple:
    """Dispatch to the right card. Returns ``(text, links)``; a blank card for an
    unknown reference (so the UI degrades rather than raises)."""
    if kind == "room":
        return _room_card(view, m, entity_id)
    if kind == "npc":
        return _npc_card(view, entity_id)
    if kind == "item":
        return _item_card(view, entity_id)
    return ("(nothing selected)", [])


# --------------------------------------------------------------------------- helpers
def _names(view) -> dict:
    """id → display name across every entity, for resolving a reference's label."""
    out = {}
    for r in view.rooms:
        out[r.id] = r.name
    for c in view.npcs:
        out[c.id] = c.name
    for it in view.items:
        out[it.id] = it.name
    return out


def _wrap(text: str, width: int = 68) -> list:
    return textwrap.wrap(text, width=width) or []


def _flags_line(node) -> str:
    marks = []
    if node.is_start:
        marks.append("START")
    marks += [f.upper() for f in node.flags]
    return "  ".join(marks)


# ----------------------------------------------------------------------------- cards
def _room_card(view, m: MapModel, rid: str) -> tuple:
    r = next((x for x in view.rooms if x.id == rid), None)
    node = m.node(rid)
    if r is None or node is None:
        return (f"(unknown room: {rid})", [])
    names = _names(view)

    out = [f"ROOM   {r.name}", f"       ({r.id})"]
    flags = _flags_line(node)
    if flags:
        out.append(f"       {flags}")
    out.append("")
    out += _wrap(r.description) if r.description else ["(no description)"]

    links = []
    out += ["", "EXITS"]
    if not r.exits:
        out.append("  (no exits)")
    for e in r.exits:
        if e.ok:
            tag = "  [one-way]" if e.one_way else ""
            out.append(f"  {e.direction:<5} -> {e.target_name} ({e.target}){tag}")
            links.append((f"-> {e.direction:<5} {e.target_name}",
                          pack_ref("room", e.target)))
        else:
            out.append(f"  {e.direction:<5} -> ?? {e.target}  [dangling]")

    if node.entrances:
        out += ["", "ENTRANCES"]
        for en in node.entrances:
            tag = "  [one-way]" if en.one_way else ""
            out.append(f"  {en.from_name} ({en.from_id}) via {en.direction}{tag}")

    out += ["", "HERE"]
    if r.occupants:
        for eid, nm in r.occupants:
            out.append(f"  @ {nm} ({eid})")
            links.append((f"@ {nm}", pack_ref("npc", eid)))
    else:
        out.append("  (nobody)")

    out += ["", "FLOOR"]
    if r.floor:
        for eid, nm in r.floor:
            out.append(f"  . {nm} ({eid})")
            links.append((f". {nm}", pack_ref("item", eid)))
    else:
        out.append("  (nothing)")

    return ("\n".join(out), links)


def _npc_card(view, nid: str) -> tuple:
    c = next((x for x in view.npcs if x.id == nid), None)
    if c is None:
        return (f"(unknown npc: {nid})", [])
    names = _names(view)

    loc = c.location or "(nowhere)"
    if c.location and not c.location_ok:
        loc = f"?? {c.location}"
    tag = "  (wanders)" if c.wanders else "  (anchored)"
    out = [f"NPC    {c.name}", f"       ({c.id})", f"       @ {loc}{tag}", ""]

    if c.persona:
        out.append("PERSONA")
        for key in ("backstory", "traits", "goals", "voice", "disposition"):
            val = c.persona.get(key)
            if not val:
                continue
            text = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val)
            wrapped = _wrap(f"{key}: {text}", 66)
            out.append(f"  {wrapped[0]}")
            out += [f"    {ln}" for ln in wrapped[1:]]
    else:
        out.append("(empty persona)")

    links = []
    if c.location and c.location_ok:
        links.append((f"@ {names.get(c.location, c.location)}",
                      pack_ref("room", c.location)))

    out += ["", "HOLDS"]
    if c.held:
        for eid, nm in c.held:
            out.append(f"  . {nm} ({eid})")
            links.append((f". {nm}", pack_ref("item", eid)))
    else:
        out.append("  (nothing)")

    return ("\n".join(out), links)


def _item_card(view, iid: str) -> tuple:
    it = next((x for x in view.items if x.id == iid), None)
    if it is None:
        return (f"(unknown item: {iid})", [])
    names = _names(view)
    home = location_index(view).get(iid, "")

    label = {"floor": "on the floor of", "held": "held by", "container": "inside",
             "nowhere": "nowhere", "broken": "?? broken holder"}[it.holder_kind]
    where = "(nowhere)" if it.holder_kind == "nowhere" else f"{label} {it.holder}"
    out = [f"ITEM   {it.name}", f"       ({it.id})", f"       {where}"]
    out.append(f"       portable: {'yes' if it.portable else 'no'}")
    if it.aliases:
        out.append(f"       aliases: {', '.join(it.aliases)}")

    forge = [b for b in (it.tier, it.theme, "/".join(it.tags)) if b]
    if forge:
        out += ["", "FORGE", f"  {' · '.join(forge)}"]

    links = []
    if home:
        links.append((f"in {names.get(home, home)}", pack_ref("room", home)))
    # If the item is held by a character or nested in a container, offer that too.
    if it.holder and it.holder != home:
        if any(c.id == it.holder for c in view.npcs):
            links.append((f"held by {names.get(it.holder, it.holder)}",
                          pack_ref("npc", it.holder)))
        elif any(x.id == it.holder for x in view.items):
            links.append((f"inside {names.get(it.holder, it.holder)}",
                          pack_ref("item", it.holder)))

    return ("\n".join(out), links)
