# Spike — the world atlas (B7): the read/validate side of authoring

*Opened 2026-07-21. Phase 7, slice 1. Status: **BUILT & offline-gated 2026-07-21**
(all four decisions signed off; commit pending).*

## Why

Phase 7 is the authoring loop: an AI author **writes** a `world.json`, and we must
be able to **read it back** and **trust** it. You cannot trust a generated world you
cannot see. The atlas is that read side — and, built right, it is also the
*validator* the write side leans on: the same traversal that renders a room's exits
is the one that catches an exit pointing nowhere.

So B7 is two tools sharing one pass over the world:

- **Render** — a whole world at a glance: a map (rooms + exits), a character sheet
  per NPC (the persona), an item table, and the world-level `meta` blocks
  (director, clock, weather, loot).
- **Validate** — a structural lint: broken references, unreachable rooms,
  one-way exits, thin content. Errors block; warnings advise.

The renderer answers the creator's *"how do I explore this world?"*; the validator
answers the generator's *"is what I just wrote sane?"*. One code path, two consumers
— exactly the shape the backlog (B7) called for.

## Prior art (surveyed 2026-07-21)

| Tool | The world-at-a-glance view | What it teaches us |
|---|---|---|
| **Inform 7 — the Index** | Auto-generated *World / Actions / Kinds* panels: a room map, room+contents listing, directional relations, region colour-coding. Every entry cross-links to the source that defined it. | Generate from the **compiled model**, not the source text; cross-reference every entry back to its id. One path feeds overview + validator. |
| **Trizbort / Trizbort.io** | Labelled room boxes joined by lines; per room: name, description (export-only), object list nested by containment, dark flag. Exports to I7/TADS/Quest. | Per-room fields to surface: name, description, exits (with flags), contents nested by holder. |
| **Twine** | Passage cards + link arrows; **broken links** get a red no-entry marker, **empty passages** a dotted translucent card — validation *is* the map. | Fold validation into the view: a dangling exit and an empty room are the two loudest signals. |
| **TADS 3 Cartographer** | Renders the room graph to ASCII / SVG / **Graphviz DOT** / HTML from the source objects. | Treat "draw the map" as a **multi-format output** problem, not one canvas. |
| **Evennia (MUD)** | Map by **graph traversal**: walk each exit, track visited to break loops, emit a grid. Exits are one-way objects; the reverse is opt-in. | Traversal + visited-set is the core algorithm (also the reachability check). One-way is normal, not a fault. |
| **CircleMUD `Fix_exits`** | Boot-time pass logging any exit whose target room doesn't exist. | The canonical "fix your world" signal — a dangling exit is the #1 error. |

**The hard part is the drawn map, and every tool's layout is its weakest point.**
Directions don't imply grid position (a room can be *north of* and *above*
another); real graphs are non-planar; exits are asymmetric by design. The universal
lightweight fallback the field accepts: an **adjacency listing** (`room → dir →
target`) — trivial, always correct, never fails on non-planar input — optionally
plus a **graph dump** (DOT or Mermaid) that hands layout to an existing engine.

## The design

**Architecture — data-gathering in the framework, rendering in the tool.** The
backlog is explicit: *"keep the data-gathering separate from the rendering so both
can sit on it,"* and *"it is the same data the Phase 6 `map`/`entities` channels will
carry — share it."* So:

- `loom/atlas.py` (**new, framework, game-agnostic, zero new deps**) — a pure
  function `survey(world, start) -> AtlasView`. The `AtlasView` is a plain,
  serialisable structure: room records (id, name, description, exits[with flags],
  occupants, floor items), NPC sheets (persona + held items), an item table (id,
  name, holder, portable, aliases, forge tier/tags/theme if set), the `meta` blocks
  verbatim, summary counts, and a **validation report** (a list of
  `{severity, code, where, message}` findings). No I/O, no rendering, no model calls.
- `scripts/atlas.py` (**new, tool**) — a CLI that loads a world (path arg,
  default `game/world/world.json`), calls `survey`, and renders. `PYTHONPATH=.
  python scripts/atlas.py [path] [--format text|md]`.
- `tests/test_atlas.py` (**new, offline**) — deterministic tests over `survey` on
  the game world and on crafted broken worlds (each lint check gets a fixture).

**The validation checklist** (from the survey — the union real tools run):

*Errors — structural integrity, block:*
- **dangling-exit** — an exit target id is not a known location.
- **bad-holder** — an item's `holder`, or an NPC's `location`, names nothing that exists.
- **duplicate-id** — two entities/locations share an id.
- **missing-required** — no id; a location with no name; empty NPC persona.
- **bad-start** — `start_location` absent or not a real location.
- **bad-direction** — an exit direction outside the known set (n/s/e/w/up/down/in/out + aliases).

*Warnings — design oddities, advise only (often intentional):*
- **unreachable-room** — not reachable from start by BFS over exits.
- **one-way-exit** — A→dir→B but B has no exit back to A. (Legitimate for drops/mazes.)
- **reverse-mismatch** — B's return exit is not the opposite direction. (Twisty passages.)
- **dead-end** — a room with no exits, or none leading in.
- **thin-content** — empty description, or an NPC/location with no flavour.
- **floating-entity** — an item or NPC referenced by nothing / placed nowhere sensible.

Errors and warnings are emitted as structured findings so the **same list** serves a
human reading the atlas *and*, later, the write-side's generate→validate→repair loop
(the research confirms: constrained decoding buys *syntactic* validity; only this
semantic lint buys *"exits resolve, rooms reachable"*).

**The map, this slice.** No bespoke drawn canvas (the hardest, weakest part
everywhere). Always the **adjacency listing** + per-room detail. The graph view is a
sign-off fork below.

## Decisions — all signed off 2026-07-21

**Decision 1 — output surface. → Both, one data layer.** `scripts/atlas.py
--format text|md`. A format-neutral `survey` feeds a terminal text renderer and a
Markdown renderer — the backlog's "two surfaces, one renderer." *(Refinement on
build: the render functions live in `loom/atlas.py` as pure, importable, unit-tested
functions; `scripts/atlas.py` is a thin CLI over them. This keeps the renderer
reusable — Phase 6's `map` channel can call `mermaid(view)` directly — and testable
without a subprocess.)*

**Decision 2 — the graph view. → Adjacency + Mermaid.** The always-present adjacency
listing, plus a Mermaid `graph LR` block (solid arrow = two-way, dotted = one-way or
dangling). Zero-dependency text; renders in Markdown / GitHub / an Artifact; seeds the
Phase 6 `map` channel. DOT/SVG deferred.

**Decision 3 — validation depth. → Errors + warnings, full.** Errors (dangling-exit,
bad-holder, bad-location, bad-start, bad-direction, id-collision) and warnings
(unreachable-room, one-way-exit, reverse-mismatch, dead-end, no-entrance,
thin-content, floating-item). Each is a `Finding{severity, code, where, message}` —
the same list a human reads and the write-side repair loop will consume.

**Sequencing. → Atlas first, then the AI author.** See and trust generated worlds
before generating them; the validator becomes the generator's judge.

### Refinements made while building

- **`duplicate-id` scoped to cross-namespace collision.** The loader keys entities by
  id in a dict, so two items sharing an id *within a list* silently collapse before a
  `World` exists — the atlas cannot see it. What it *can* and does catch: an id shared
  between a **location and an entity** (`id-collision`), which breaks `place_item` /
  `move` (both check either namespace). Catching within-list duplicates belongs in the
  loader (`content.py` warning on overwrite) — a separate slice; noted as a deferral.
- **`bad-direction` allows `in`/`out`.** The known set is `command.DIRECTIONS`'s
  compass/vertical values plus `in`/`out` (the `go` handler resolves those against a
  location's exit keys), so an authored `in`/`out` exit is not a false positive.
- **Thin persona / no-name are warnings, not errors.** An NPC with an empty persona or
  a room defaulting its name to its id still runs mechanically; these are
  `thin-content` warnings, reserving errors for broken references and unplayability.
- **The shipped game world validates clean** — 0 errors, 0 warnings (the appendix
  below is illustrative; the real world surfaces no findings). This is the golden test.

## Slice as built

`loom/atlas.py` (`survey` + the `AtlasView` dataclasses + `render_text` /
`render_markdown` / `mermaid`) + the thin CLI `scripts/atlas.py` +
`tests/test_atlas.py` (29 tests). Pure, deterministic, offline — **no model calls, no
GPU, no new deps** → the offline gate alone covers it (the live gate is not
applicable; this slice changes no prompt, action catalogue, or perception).

**Gate:** offline **611 green** (582 + 29 new). The shipped game world surveys clean
(0/0); broken fixtures fire every check; the CLI exits non-zero on any error (broken
world → 1, clean → 0), so it can gate a generated world in CI.

## Deferrals (out of this slice)

- A **drawn 2D/grid map** — the layout engine. Defer; adjacency + Mermaid suffices.
  When wanted, steal Inform's **layout-hint layer** (`map X <dir> of Y`) kept
  *separate* from the actual exits, so hints never corrupt the model.
- **Region / zone colour-grouping** — the schema has no region concept yet; add when
  regions land (they will, as the world grows in Phase 7).
- **DOT / SVG / HTML** output surfaces — Mermaid first; these follow if needed.
- The **write side** — AI-assisted authoring (brief → valid `world.json`) with the
  generate→validate→repair loop. Its own spike; this atlas is its judge.

## Appendix — the atlas, sketched

```
WORLD ATLAS — game/world/world.json
  4 locations · 2 NPCs · 3 items · start: cave_mouth
  exits: 6 · reachable: 4/4 · warnings: 2 · errors: 0

MAP (adjacency)
  The Cave Mouth (cave_mouth)
    north → The Hill Path (hill_path)
    down  → Inside the Cave (cave_interior)
  The Hill Path (hill_path)
    south → The Cave Mouth        [one-way? no, ← cave_mouth]
    up    → The Hilltop (hilltop)
  ...

ROOMS
  cave_mouth — "The Cave Mouth"
    A jagged opening in the hillside breathes cold air...
    here: Odd the Hermit, Wren the Wayfinder · floor: a rusty lantern, an ornate brass key

CHARACTERS
  hermit — Odd the Hermit  @ cave_mouth
    backstory: You have lived alone at this cave for thirty winters...
    traits: wary, dryly humorous, observant, lonely but proud
    goals:  protect your solitude · judge whether this stranger is a threat
    voice:  terse, old-fashioned, fond of short proverbs
    holds:  (nothing)
  guide — Wren the Wayfinder  @ cave_mouth  (wanders)
    ...  holds: a worn hill-map

ITEMS
  lantern     a rusty lantern        @ cave_mouth (floor)   aliases: lantern, lamp, light
  brass_key   an ornate brass key    @ cave_mouth (floor)   aliases: key, brass key, brass
  wren_map    a worn hill-map        @ guide (held)         aliases: map, hill-map

WORLD CONFIG (meta)
  director · clock (4 phases) · weather (4 states) · loot (3 tiers) · start_quests (2)

LINT
  ⚠ thin-content  hermit holds nothing / cave_interior has no occupants
  ✓ no errors
```
