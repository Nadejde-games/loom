# Workbench roadmap

The workbench shipped in three slices — the explorer, play-in-editor, and the
authoring agent with hand-editing — and each slice deliberately deferred things.
This page collects what's ahead, from the design notes in `docs/spikes/` and the
project plan.

## Editor

- **A drawn map.** Today's "map" is honestly a navigable adjacency tree. The
  plan is a stylised auto-layout first (the atlas can already emit a Mermaid
  graph of the world), and per-room layout hints (Twine-style, kept in metadata)
  later — a real 2D node canvas is the far end of that road.
- **Structural hand-edits.** Exits and entity relocation are currently
  agent-only; a direct-edit path for them (still through the same
  shadow-validate gate) is a named next candidate.
- **Reference-spawns-node** — authoring an exit to a room that doesn't exist yet
  should offer to create it, the way Twine links do. Drag-to-link belongs to the
  same family.
- **Run in the browser.** Textual can serve the same app to a browser
  (`textual serve`) essentially for free; the TUI shipped first.
- **Deeper undo.** The current undo is a multi-level in-memory checkpoint
  stack; git-backed history (and with it, branching) is the deferred upgrade.
- **Multi-author** sessions, eventually.

## Play-in-editor

- A **living-world toggle** — running the director, clock, weather, and NPC
  wandering inside the play sandbox, for full-fidelity previews.
- **Play as a character** (inventory injection included), saving a walk as a
  repeatable script, and seeded live runs for reproducible previews.
- A jump-to-any-room picker inside play.

## The AI author

- **Greenfield worlds** — authoring a whole new world from a blank brief. The
  region framer already supports it (no anchor; the entry room becomes the
  start), it just isn't surfaced as a workflow yet.
- **Meta-block authoring** — generating the director persona, clock phases,
  weather chain, and loot tables, not just rooms, NPCs, and items.
- **Quest authoring**, leaning on the engine's quest subsystem.
- **Multi-region campaigns** and region-file (directory) output, for worlds
  bigger than one file.
- Possibly an **architect/editor split** — a second model tier for mechanical
  editing distinct from planning — but only if measurement shows the two halves
  actually want different models.
