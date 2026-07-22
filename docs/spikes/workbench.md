# Spike — the authoring workbench (Phase 8): one text-mode home for world-building

*Opened 2026-07-22. Status: **slice 1 (the Explorer) BUILT & offline-green 2026-07-22;
slices 2–3 planned.** Decisions (2026-07-22): substrate **Textual**; first slice the
**Explorer**. The successor to Phase 7 (the atlas read the world, the author wrote it —
both via clumsy CLIs); this makes them usable.*

## Slice 1 as built (2026-07-22)

The Explorer ships as a read-only Textual three-pane workbench over one `atlas.survey`:

- **`loom/explore.py`** (framework, pure, zero new deps — stdlib `difflib` for fuzzy): the
  game-agnostic logic. `map_model(view)` derives a navigable graph (nodes = rooms; the
  survey's out-edges + their reciprocity/one-way flags, plus the **in-edges/entrances** the
  survey does not name; per-node reachability and dead-end/no-entrance/unreachable badges).
  `search(view, query, *, scope, kinds, tag, limit)` ranks every entity across id / name /
  alias / tag / tier / description / persona with a fuzzy fallback and a local-vs-global
  `scope`; an empty query is a pure browse. `location_index(view)` resolves an item's
  holder chain to the room it sits in.
- **`authoring/`** (the TUI tool — imports `loom`, never the reverse): `cards.py` (pure
  `(text, links)` per entity, unit-tested Textual-free) and `app.py` (the `WorkbenchApp`:
  search box → live-filtered navigator tree; inspector card; a jump-link option-list that
  turns exits/occupants/contents into one-keystroke moves). `python -m authoring [world]`.
- **Gates:** offline only (no model call). `tests/test_explore.py` (30, the logic on a
  crafted world + the shipped world) and `tests/test_workbench.py` (12 = 7 pure cards +
  5 UI smoke via `App.run_test()`+`Pilot`, skipped cleanly when the extra is absent).
  Full suite 681 green. Textual added as the `authoring` optional extra (not core).
- **Known gap → next slice: selection↔navigator sync.** `_select` repaints the inspector
  but does not move the tree cursor, so selecting via search-submit or a jump-link leaves
  the navigator highlight out of step with the card. A `g` "home to start room" binding
  was tried and **cut** in slice 1: it only half-worked (swallowed while the search box
  held focus; moved the inspector but not the tree). The fix is one change — make
  `_select` drive the tree cursor onto the chosen entity — which keeps every path (search,
  jump-link, and any future home key) in sync for free. Folded into the next slice.

*Below is the original plan (slices 2–3 remain as written).*

## Why

The tooling works but its surface is a string of CLI incantations — `set -a && . ./.env &&
… python scripts/author.py "…" --attach hilltop --out …`. Wrong ergonomic for an author. The
ask (2026-07-22): **one text-mode application** an author lives in — an EXPLORER (see the map,
select a room, read it, walk into it and experience it live), the same for NPCs / items / the
rest, SEARCH as the world grows, and an **agent chat** that inspects a zone or NPC and edits /
generates / tests from natural language, hiding the tools beneath it.

Most of it stands on machinery already built: `atlas.survey` gathers every room (exits +
reciprocity), NPC sheet, item, and the graph; `author_region` writes a validated region;
`assemble` + `survey` validate throwaway candidate worlds without touching the live one; the
engine boots from a `World` + start id; persistence is already an overlay over an immutable
`world.json`. The workbench is mostly a *surface* over these, not new engine.

## Prior art (two sweeps, 2026-07-22, to the loot-spike bar)

### A. The substrate & the builder UX

- **Build on Textual** ([textual](https://github.com/Textualize/textual)). The only Python TUI
  that ships all four requirements: split-pane TCSS layout; `Tree`/`DataTable`/`ListView`/
  `Input` widgets that map onto map/inspector/chat; **bubbling `Click` events carrying the
  target widget + coordinates** (so a map node is genuinely clickable); native asyncio + a
  **Worker API** to run the engine beside the UI. `textual serve` runs the *same app* in a
  browser (xterm.js) — so TUI-vs-web is not a fork. Cost: it pulls `rich`/`markdown-it-py`/
  `pygments` (heavy) and moves fast (v8, API churn). Fallbacks: **urwid** (light full TUI,
  mouse, `wcwidth` only) if the dep is unacceptable even for a tool; **stdlib `cmd`** (zero-dep
  REPL) only if the workflow collapses to command-in/result-out — but a line-REPL cannot do
  "click a room on a map," which the ask names explicitly.
- **The field-standard layout is a three-way split** — a **map/graph** view (Inform's World
  index, Twine's story map, Trizbort's canvas), an **entity inspector** (OLC field menus,
  Twine passage editor, Inform Index detail pages), and a **play/preview** pane (Inform Story,
  Twine Play, TADS window). Loom adds a fourth: the **agent chat**.
- **Patterns to steal:** the map is a *projection of the data you already store* (Inform
  auto-draws it from room/direction declarations — "stylised," never perfect for a non-planar
  graph; ship a jump-to-source click on every node, Inform's orange-arrow). **Reference-spawns-
  node** (authoring an exit to an unknown room offers to create it — Twine `[[link]]`).
  **Broken-link flagging** (the atlas already lints dangling exits — surface them as Twine's
  arrow-ending-in-X). **Auto-create the reverse exit** with a `/oneway` escape — the #1 raw-OLC
  pain point, which `frame_skeleton` *already* guarantees. Distinguish a raw-record `examine`
  from the player-facing `look`.
- **Search:** a **fuzzy quick-jump palette** over id + name + description (Twine `P`, VS Code
  Ctrl-P) as the everyday finder, backed by structured filters — **id / name / alias / type /
  tag / containment / free-text** — with a **local-vs-global scope** toggle (Evennia) and
  click-to-source from a category browser (Inform Index). Anchor everything on the stable
  entity id (our dbref/vnum analog).
- **Play-in-editor MUST NOT mutate the authored world.** Inform recompiles-and-replays from
  turn zero; Twine layers a volatile store over immutable passages; Unity discards Play-Mode
  edits on stop. The MUD default (edit/test against one shared live DB) is the corruption trap
  to avoid. Jump-to-room = seed an *ephemeral* session with `location = <selected id>` (+
  optional inventory), **seed the RNG** for reproducible walkthroughs, discard on exit.

### B. The authoring agent

- **The loop:** `ground → plan → read-tools (free) → propose mutation → preview diff →
  validate on a SHADOW copy → confirm → apply → checkpoint`, ReAct-style, with reasoning and
  editing kept in **separate phases** (the Aider architect/editor split; Copilot/Claude-Code
  `plan` mode). **Read is free, write is gated** — auto-approve survey/search, require
  confirmation for author/edit/spawn (LangGraph risk-based interrupt).
- **The one non-negotiable safety invariant — validator-gated apply on a shadow copy.** A
  mutation runs against a *copy* of the world; the atlas re-surveys it; it merges into the live
  world **only if it stays clean**; a dirty survey feeds the existing repair loop or the human.
  This is what prevents silent, cascading corruption — and Loom already has the parts
  (`assemble` builds the candidate, `survey` judges it). Never trust a tool's self-declared
  "safe" flag; the atlas + the apply-gate are the enforcement.
- **Tool catalogue:** few, coarse, **workflow-named**, flat, ≤~8 params — `zone_survey`,
  `world_search`, `region_author`, `entity_edit`, `entity_spawn`, `preview_play`,
  `world_revalidate` — *not* one tool per CLI flag. Return the atlas's structured findings
  verbatim as the model's observations. Loom's existing schema-validated tool-call seam is the
  transport (MCP only if external hosts ever need the catalogue).
- **Grounding:** feed the workbench's current selection (open zone id, inspected NPC id) into
  the agent as explicit context so "this NPC / the north wing" binds deterministically; have it
  read the survey before proposing; **echo the resolved referent** back. Edit locally (a zone
  subgraph), **validate globally** (the atlas checks whole-world reachability). Ask a
  clarifying question only when a required parameter or a referent is ambiguous; else propose a
  diff. Bound the loop (max turns / budget) against repair spirals.

## The design

**A new tool, not core.** The workbench is an authoring *application* — its home is a new
top-level `authoring/` (or `tools/workbench/`), **never `loom/` core**, so the Textual
dependency stays out of the framework. But the *logic* it needs — search over a survey, the
shadow-copy → validate → apply gate, the agent's tool catalogue — is game-agnostic and belongs
in `loom/` (e.g. `loom/authoring.py` grows a `search` and an `apply`/`checkpoint` surface, and
a `loom/ai/author.py` grows the agent loop). **Textual sees only `loom` data structures; `loom`
never imports Textual.** `docs/DEPENDENCIES.md` gets a new *authoring* extra (Textual), strict
policy on core unchanged.

**The four panes, each on existing machinery:**
- **Map** — a clickable node graph rendered from `AtlasView` (the exits we already survey);
  select a node → the inspector fills; broken/one-way exits already flagged by the survey.
- **Inspector** — a read view first (room card / NPC sheet / item card, straight from the
  survey), an *edit* view later (through the agent or direct field edits, both gated by the
  atlas). `examine` (raw record) distinct from `look` (player-facing).
- **Search** — a fuzzy palette + structured filters over the survey; grows with the world.
- **Preview ("experience it")** — deep-copy the world (or overlay ephemeral state), boot the
  engine at the selected room, run the real play loop (look/move/say/examine, NPC minds live),
  discard on exit. Reuses `Engine` + the play client's loop; never writes the authored file.
- **Chat** — the agent: NL → read-tools → propose → shadow-validate → diff → confirm → apply →
  checkpoint. Tool catalogue over `survey`/search/`author_region`/edit/spawn/`preview`.

**Slice plan (each its own build + gates):**
1. **Explorer** — the Textual app: map (clickable) + inspector (read) + search, over
   `atlas.survey`. Read-only, no model call. Proves the substrate; builds the surface the agent
   will inhabit. *(Recommended first slice.)*
2. **Experience it** — the play-in-editor sandbox (jump-to-room, ephemeral state, discard on
   exit, seeded RNG).
3. **The authoring agent** — the NL chat: tool catalogue + shadow-validate-confirm-apply +
   checkpoint/undo. The write half; the live gate returns here.

## Decisions — signed off 2026-07-22

**Decision 1 — the substrate → Textual.** The clickable map + inspector + chat the ask names,
one codebase for TUI *and* browser (`textual serve`), engine-alongside-UI via Workers. Cost
accepted: a heavier **tool-scoped** dependency (kept out of `loom/` core — the UI imports
`loom`, never the reverse) and a fast-moving library. *(Rejected: a line-REPL — no map/click,
does not deliver the explorer as asked; urwid — lighter but more hand-wiring, no browser-serve.)*
When build begins, Textual is added to `pyproject.toml` as a new **`authoring` optional extra**
(not core `dependencies`); `docs/DEPENDENCIES.md` gets the tool-scoped entry. *(Textual is not
installed yet — no build has started.)*

**Decision 2 — the first slice → the Explorer.** Read-only navigation over `atlas.survey`
(clickable navigator + entity cards + search); de-risks the substrate, makes no model call, and
is the surface every later slice sits inside. *(Experience-first and agent-first both need a
surface to live in; they follow as slices 2 and 3.)* Honest scope note: slice 1's "map" is a
navigable **adjacency/room navigator** (rooms + their exits, click-to-select, exits as jump
links), not a drawn 2D node canvas — the layout engine is the field's weakest part everywhere
and is deferred (see Deferrals).

## Both gates

- **Offline** — the workbench's *logic* is pure and testable on `FakeProvider`: the map-model
  derivation from a survey, the search index (each axis + fuzzy match), the shadow-copy →
  survey → apply/reject gate, the agent's tool-catalogue validation and loop control (read-free
  / write-gated, clarify-on-ambiguity, stop conditions, checkpoint/undo). Textual ships a test
  harness (`App.run_test()` + a `Pilot` that simulates clicks/keys) for a UI smoke pass.
- **Live** — returns at the **agent slice** (`scripts/behavior_probe.py`, an `author-agent`
  family): an NL request ("add a smithy east of the market with a gruff blacksmith") drives the
  agent to author + validate a change that surveys clean. The explorer/experience slices make
  no model call → offline only.

## Deferrals (out of the first slices)

- **Direct field editing in the inspector** (vs editing through the agent) — start read-only +
  agent-mediated; add hand-editing once the apply-gate is proven.
- **Map layout** — a stylised auto-layout first (adjacency / the atlas `mermaid`), not a hand-
  placed 2D canvas; per-node layout hints (Twee-style, in metadata) later.
- **`textual serve` / browser** — the same app serves to a browser for free, but ship the TUI
  first.
- **Reference-spawns-node, drag-to-link, undo-tree depth, multi-author** — grow after the spine.
- **Selection↔navigator sync + a home-to-start key** — carried from slice 1 (see the build
  note above): `_select` should move the tree cursor so search / jump-link / home all keep
  the navigator highlight in step with the inspector. First task of the next slice.
