"""The world atlas (B7): see and validate a world at a glance — the *read* side of
Phase 7 authoring.

One pass over a loaded :class:`~loom.world.World` produces a plain, serialisable
:class:`AtlasView`: room records (with exits + reciprocity flags), a character sheet
per NPC (the persona), an item table, the world-level ``meta`` blocks, and a
structural-lint report of :class:`Finding`\\ s (errors that break the world, warnings
that flag design oddities). Two consumers sit on the one survey: a human exploring a
world (rendered here as text or Markdown), and — later — the *write* side's
generate→validate→repair loop, which reads the same ``findings`` as machine feedback.

Pure and offline: no I/O, no model calls, no new dependencies. ``survey`` gathers the
data; ``render_text`` / ``render_markdown`` present it (two surfaces, one survey — so
Phase 6's ``map``/``entities`` channels can sit on the same gatherer). The thin CLI
that loads a file and prints a rendering lives in ``scripts/atlas.py``.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .command import DIRECTIONS as _CMD_DIRECTIONS
from .world import World, Npc, Item

# The exit directions the engine can traverse: the compass/vertical set the command
# parser recognises (command.DIRECTIONS), plus in/out which the ``go`` handler
# resolves against a location's exit keys. Anything else is a bad-direction.
KNOWN_DIRECTIONS = set(_CMD_DIRECTIONS.values()) | {"in", "out"}
OPPOSITE = {
    "north": "south", "south": "north", "east": "west", "west": "east",
    "up": "down", "down": "up", "in": "out", "out": "in",
}

# Persona keys that carry flavour — an NPC with none of these is 'thin'.
_PERSONA_KEYS = ("backstory", "traits", "goals", "voice", "disposition")


# --------------------------------------------------------------------------- view
@dataclass
class Finding:
    """One lint result. ``severity`` is "error" (breaks the world) or "warning"
    (a design oddity, often intentional); ``code`` is a stable kebab-case slug;
    ``where`` is the id it concerns ("" for world-level); ``message`` is human text.
    The shape doubles as machine feedback for the write-side repair loop."""
    severity: str
    code: str
    where: str
    message: str


@dataclass
class ExitView:
    direction: str
    target: str            # target location id, as authored
    target_name: str       # "" if the target is unknown
    ok: bool               # target is a known location
    one_way: bool          # no exit leads back from the target to this room
    back: str              # the direction back, "" if none


@dataclass
class RoomView:
    id: str
    name: str
    description: str
    exits: list            # list[ExitView], in authored order
    occupants: list        # list[(id, name)] — characters here, by name
    floor: list            # list[(id, name)] — items on the floor, by name


@dataclass
class NpcView:
    id: str
    name: str
    location: str          # location_id as authored ("" if none)
    location_ok: bool
    wanders: bool
    persona: dict
    held: list             # list[(id, name)] — items carried


@dataclass
class ItemView:
    id: str
    name: str
    holder: str            # holder id ("" if none)
    holder_kind: str       # floor | held | container | nowhere | broken
    portable: bool
    aliases: list
    tier: str
    tags: list
    theme: str


@dataclass
class AtlasView:
    source: str            # where this world was loaded from (a label)
    start: str             # start_location id ("" if none)
    rooms: list            # list[RoomView], in load order
    npcs: list             # list[NpcView]
    items: list            # list[ItemView]
    meta: dict             # world-level config blocks, verbatim
    findings: list         # list[Finding]
    reachable_ids: list = field(default_factory=list)  # rooms reachable from start

    @property
    def errors(self) -> list:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list:
        return [f for f in self.findings if f.severity == "warning"]

    def summary(self) -> dict:
        return {
            "locations": len(self.rooms),
            "npcs": len(self.npcs),
            "items": len(self.items),
            "exits": sum(len(r.exits) for r in self.rooms),
            "reachable": len(self.reachable_ids),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
        }


# ------------------------------------------------------------------------- survey
def survey(world: World, start: str | None = None, *, source: str = "") -> AtlasView:
    """Gather a whole world into an :class:`AtlasView` — the one pass both renderers
    and the validator sit on. Pure: reads ``world``, returns data, touches nothing."""
    reachable = _reachable_from(world, start)
    return AtlasView(
        source=source,
        start=start or "",
        rooms=_survey_rooms(world),
        npcs=_survey_npcs(world),
        items=_survey_items(world),
        meta=dict(world.meta),
        findings=_validate(world, start, reachable),
        reachable_ids=sorted(reachable),
    )


def _survey_rooms(world: World) -> list:
    rooms = []
    for loc in world.locations.values():
        exits = []
        for direction, target in loc.exits.items():
            dest = world.locations.get(target)
            back = _way_back(dest, loc.id) if dest else ""
            exits.append(ExitView(
                direction=direction, target=target,
                target_name=dest.name if dest else "",
                ok=dest is not None,
                one_way=dest is not None and not back,
                back=back,
            ))
        occ = sorted(((e.id, e.name) for e in world.occupants(loc.id)),
                     key=lambda t: t[1])
        floor = [(i.id, i.name) for i in world.contents(loc.id)]
        rooms.append(RoomView(id=loc.id, name=loc.name,
                              description=loc.description, exits=exits,
                              occupants=occ, floor=floor))
    return rooms


def _survey_npcs(world: World) -> list:
    npcs = []
    for ent in world.entities.values():
        if not isinstance(ent, Npc):
            continue
        loc = ent.location_id or ""
        npcs.append(NpcView(
            id=ent.id, name=ent.name, location=loc,
            location_ok=bool(loc) and loc in world.locations,
            wanders=bool(ent.wanders), persona=dict(ent.persona),
            held=[(i.id, i.name) for i in world.contents(ent.id)],
        ))
    return npcs


def _survey_items(world: World) -> list:
    items = []
    for ent in world.entities.values():
        if not isinstance(ent, Item):
            continue
        holder = ent.holder or ""
        items.append(ItemView(
            id=ent.id, name=ent.name, holder=holder,
            holder_kind=_holder_kind(world, holder),
            portable=bool(ent.portable), aliases=list(ent.aliases),
            tier=ent.tier, tags=list(ent.tags), theme=ent.theme,
        ))
    return items


def _way_back(dest, origin_id: str) -> str:
    """The direction from ``dest`` back to ``origin_id``, or "" if none leads back."""
    for direction, target in dest.exits.items():
        if target == origin_id:
            return direction
    return ""


def _holder_kind(world: World, holder: str) -> str:
    if not holder:
        return "nowhere"
    if holder in world.locations:
        return "floor"
    h = world.entities.get(holder)
    if isinstance(h, Item):
        return "container"
    if h is not None:
        return "held"          # a character's inventory
    return "broken"            # holder id names nothing at all


def _reachable_from(world: World, start: str | None) -> set:
    """The set of location ids reachable from ``start`` by walking exits (the same
    traversal a map-drawer uses; here it feeds the unreachable-room check)."""
    if not start or start not in world.locations:
        return set()
    seen = {start}
    stack = [start]
    while stack:
        for target in world.locations[stack.pop()].exits.values():
            if target in world.locations and target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


# ---------------------------------------------------------------------- validation
def _validate(world: World, start: str | None, reachable: set) -> list:
    """Structural lint. Errors break the world (broken references, no start);
    warnings flag design oddities that are frequently intentional (one-way exits,
    unreachable rooms, thin content). Findings are ordered errors-first, then by the
    order the world declares things — deterministic for tests and repair loops."""
    findings: list = []
    err = lambda code, where, msg: findings.append(Finding("error", code, where, msg))
    warn = lambda code, where, msg: findings.append(Finding("warning", code, where, msg))

    # --- errors: broken references and unplayability -------------------------
    if not start:
        err("bad-start", "", "no start_location is declared")
    elif start not in world.locations:
        err("bad-start", start, f'start_location "{start}" is not a known location')

    for cid in sorted(set(world.locations) & set(world.entities)):
        err("id-collision", cid,
            f'id "{cid}" names both a location and an entity')

    for loc in world.locations.values():
        for direction, target in loc.exits.items():
            if direction.lower() not in KNOWN_DIRECTIONS:
                err("bad-direction", loc.id,
                    f'exit "{direction}" from {loc.id} is not a known direction')
            if target not in world.locations:
                err("dangling-exit", loc.id,
                    f'exit "{direction}" from {loc.id} leads to unknown "{target}"')

    for ent in world.entities.values():
        if isinstance(ent, Item):
            h = ent.holder
            if h and h not in world.locations and h not in world.entities:
                err("bad-holder", ent.id,
                    f'item {ent.id} is held by unknown "{h}"')
        elif isinstance(ent, Npc):
            lid = ent.location_id
            if lid and lid not in world.locations:
                err("bad-location", ent.id,
                    f'{ent.id} is placed in unknown location "{lid}"')

    # --- warnings: reachability, reciprocity, thin content -------------------
    if start and start in world.locations:
        for lid in world.locations:
            if lid not in reachable:
                warn("unreachable-room", lid,
                     f'{lid} is not reachable from start ({start})')

    incoming = {lid: 0 for lid in world.locations}
    for loc in world.locations.values():
        for target in loc.exits.values():
            if target in world.locations:
                incoming[target] += 1

    for loc in world.locations.values():
        if not loc.exits:
            warn("dead-end", loc.id, f'{loc.id} has no exits')
        for direction, target in loc.exits.items():
            dest = world.locations.get(target)
            if dest is None:
                continue                       # already an error
            back = [d for d, t in dest.exits.items() if t == loc.id]
            if not back:
                warn("one-way-exit", loc.id,
                     f'{loc.id} → {direction} → {target} has no way back')
            else:
                opp = OPPOSITE.get(direction.lower())
                if opp and opp not in {b.lower() for b in back}:
                    warn("reverse-mismatch", loc.id,
                         f'{loc.id} → {direction} → {target}, but the way back is '
                         f'"{back[0]}" (expected "{opp}")')

    for lid, n in incoming.items():
        if n == 0 and lid != start:
            warn("no-entrance", lid, f'no exit leads into {lid}')

    for loc in world.locations.values():
        if not loc.description.strip():
            warn("thin-content", loc.id, f'{loc.id} has no description')

    for ent in world.entities.values():
        if isinstance(ent, Npc) and not _persona_has_flavour(ent.persona):
            warn("thin-content", ent.id, f'{ent.id} has an empty persona')
        elif isinstance(ent, Item) and not ent.holder:
            warn("floating-item", ent.id, f'{ent.id} is not placed anywhere')

    return findings


def _persona_has_flavour(persona) -> bool:
    return isinstance(persona, dict) and any(persona.get(k) for k in _PERSONA_KEYS)


# ------------------------------------------------------------------------ renderers
def render_text(view: AtlasView) -> str:
    """A terminal-native rendering: header + adjacency map + rooms + character
    sheets + item table + world config + lint."""
    s = view.summary()
    out = [
        f"WORLD ATLAS — {view.source or '(unnamed world)'}",
        f"  {s['locations']} locations · {s['npcs']} NPCs · {s['items']} items"
        f" · start: {view.start or '(none)'}",
        f"  exits: {s['exits']} · reachable: {s['reachable']}/{s['locations']}"
        f" · errors: {s['errors']} · warnings: {s['warnings']}",
        "",
        "MAP (adjacency)",
    ]
    for r in view.rooms:
        out.append(f"  {r.name} ({r.id})")
        if not r.exits:
            out.append("    (no exits)")
        for e in r.exits:
            tgt = f"{e.target_name} ({e.target})" if e.ok else f"?? {e.target}"
            flag = ""
            if not e.ok:
                flag = "  [dangling]"
            elif e.one_way:
                flag = "  [one-way]"
            out.append(f"    {e.direction:<5} → {tgt}{flag}")

    out += ["", "ROOMS"]
    for r in view.rooms:
        out.append(f"  {r.id} — \"{r.name}\"")
        if r.description:
            out.append(f"    {_truncate(r.description, 100)}")
        here = ", ".join(n for _, n in r.occupants) or "(nobody)"
        floor = ", ".join(n for _, n in r.floor) or "(nothing)"
        out.append(f"    here: {here} · floor: {floor}")

    out += ["", "CHARACTERS"]
    for c in view.npcs:
        loc = c.location or "(nowhere)"
        if c.location and not c.location_ok:
            loc = f"?? {c.location}"
        tag = "  (wanders)" if c.wanders else ""
        out.append(f"  {c.id} — {c.name}  @ {loc}{tag}")
        out += _persona_lines(c.persona, "    ")
        held = ", ".join(n for _, n in c.held) or "(nothing)"
        out.append(f"    holds: {held}")

    out += ["", "ITEMS"]
    for it in view.items:
        where = _item_where(it)
        extra = ""
        if it.aliases:
            extra = f"   aliases: {', '.join(it.aliases)}"
        forge = _forge_tag(it)
        out.append(f"  {it.id:<12} {it.name:<24} @ {where}{extra}{forge}")

    out += ["", "WORLD CONFIG (meta)"]
    if view.meta:
        out.append(f"  {' · '.join(_meta_summary(view.meta))}")
    else:
        out.append("  (none)")

    out += ["", "LINT"]
    if not view.findings:
        out.append("  ✓ clean — no errors, no warnings")
    else:
        for f in view.errors:
            out.append(f"  ⚠ ERROR   {f.code}: {f.message}")
        for f in view.warnings:
            out.append(f"  · warning {f.code}: {f.message}")
    return "\n".join(out)


def render_markdown(view: AtlasView) -> str:
    """A Markdown rendering: the same survey, with a Mermaid map that renders in
    Markdown / GitHub / an Artifact, plus tables. One step from a shareable page."""
    s = view.summary()
    out = [
        f"# World atlas — {view.source or '(unnamed world)'}",
        "",
        f"**{s['locations']}** locations · **{s['npcs']}** NPCs · **{s['items']}** "
        f"items · start: `{view.start or '(none)'}`  ",
        f"exits: {s['exits']} · reachable: {s['reachable']}/{s['locations']} · "
        f"errors: **{s['errors']}** · warnings: {s['warnings']}",
        "",
        "## Map",
        "",
        "```mermaid",
        mermaid(view),
        "```",
        "",
        "| Room | Exit | Leads to |",
        "| --- | --- | --- |",
    ]
    for r in view.rooms:
        if not r.exits:
            out.append(f"| {r.name} (`{r.id}`) | — | *(no exits)* |")
        for e in r.exits:
            tgt = f"{e.target_name} (`{e.target}`)" if e.ok else f"⚠ `{e.target}`"
            d = e.direction + (" ¹" if e.one_way else "")
            out.append(f"| {r.name} (`{r.id}`) | {d} | {tgt} |")
    out.append("")
    out.append("*¹ one-way (no exit leads back).*")

    out += ["", "## Rooms", ""]
    for r in view.rooms:
        out.append(f"### {r.name} `{r.id}`")
        if r.description:
            out.append(f"> {r.description}")
        here = ", ".join(n for _, n in r.occupants) or "*(nobody)*"
        floor = ", ".join(n for _, n in r.floor) or "*(nothing)*"
        out.append(f"- here: {here}")
        out.append(f"- floor: {floor}")
        out.append("")

    out += ["## Characters", ""]
    for c in view.npcs:
        loc = c.location or "(nowhere)"
        if c.location and not c.location_ok:
            loc = f"⚠ {c.location}"
        tag = " · *wanders*" if c.wanders else ""
        out.append(f"### {c.name} `{c.id}` — @ `{loc}`{tag}")
        for line in _persona_lines(c.persona, ""):
            out.append(f"- {line.strip()}")
        held = ", ".join(n for _, n in c.held) or "*(nothing)*"
        out.append(f"- holds: {held}")
        out.append("")

    out += ["## Items", "", "| Item | Name | Where | Aliases |",
            "| --- | --- | --- | --- |"]
    for it in view.items:
        aliases = ", ".join(it.aliases) or "—"
        out.append(f"| `{it.id}` | {it.name} | {_item_where(it)} | {aliases} |")

    out += ["", "## World config", ""]
    if view.meta:
        for line in _meta_summary(view.meta):
            out.append(f"- {line}")
    else:
        out.append("*(none)*")

    out += ["", "## Lint", ""]
    if not view.findings:
        out.append("✓ **clean** — no errors, no warnings")
    else:
        out += ["| Severity | Code | Where | Message |", "| --- | --- | --- | --- |"]
        for f in view.errors + view.warnings:
            sev = "**error**" if f.severity == "error" else "warning"
            out.append(f"| {sev} | `{f.code}` | `{f.where or '—'}` | {f.message} |")
    return "\n".join(out)


def mermaid(view: AtlasView) -> str:
    """A Mermaid ``graph LR`` of the room adjacency: solid arrows for two-way exits,
    dotted for one-way or dangling. Zero-dependency text; the seed of the Phase 6
    ``map`` channel and a drop-in for Markdown/Artifact viewers."""
    lines = ["graph LR"]
    for r in view.rooms:
        label = r.name.replace('"', "'")
        lines.append(f'  {_node(r.id)}["{label}"]')
    for r in view.rooms:
        for e in r.exits:
            arrow = "-->" if (e.ok and not e.one_way) else "-.->"
            lines.append(f'  {_node(r.id)} {arrow}|{e.direction}| {_node(e.target)}')
    return "\n".join(lines)


# --------------------------------------------------------------------------- format
def _node(entity_id: str) -> str:
    """A Mermaid-safe node id (our ids are already word-safe; guard anyway)."""
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in entity_id)


def _persona_lines(persona: dict, indent: str) -> list:
    lines = []
    for key in _PERSONA_KEYS:
        val = persona.get(key)
        if not val:
            continue
        text = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val)
        lines.append(f"{indent}{key}: {_truncate(text, 140)}")
    return lines


def _item_where(it: ItemView) -> str:
    label = {"floor": "floor", "held": "held", "container": "inside",
             "nowhere": "nowhere", "broken": "??"}[it.holder_kind]
    if it.holder_kind in ("nowhere",):
        return "(nowhere)"
    return f"{it.holder} ({label})"


def _forge_tag(it: ItemView) -> str:
    if not (it.tier or it.tags or it.theme):
        return ""
    bits = [b for b in (it.tier, it.theme, "/".join(it.tags)) if b]
    return f"   [{' '.join(bits)}]"


def _meta_summary(meta: dict) -> list:
    """A compact one-line-per-block description of world-level config, without
    interpreting it (the framework is agnostic to what a game puts in meta)."""
    out = []
    for key, val in meta.items():
        if isinstance(val, dict):
            sub = ", ".join(k for k in val)
            out.append(f"{key}: {{{sub}}}")
        elif isinstance(val, list):
            out.append(f"{key}: {len(val)} entr{'y' if len(val) == 1 else 'ies'}")
        else:
            out.append(f"{key}: {_truncate(str(val), 60)}")
    return out


def _truncate(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"
