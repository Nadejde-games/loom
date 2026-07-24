# Using the workbench

```bash
make workbench
# or, explicitly:
python -m authoring                      # opens game/world/world.json
python -m authoring path/to/world.json   # a single world file
python -m authoring path/to/world-dir/   # a directory of region files, merged
```

The workbench auto-loads the repo-root `.env`, so whatever backend the
[setup wizard](../setup/quickstart.md) configured is what the agent and
in-editor play will use.

## The screen

Two columns. On the left, the **navigator**: a search box and a tree of the
world grouped into Rooms, NPCs, and Items. On the right, the **inspector**: a
card for the selected entity plus a list of jump-links. The header subtitle
keeps a live tally — rooms · npcs · items · survey errors/warnings · play mode.

The tree marks what the world survey knows: `★` the start room, `⚠` an
unreachable room or one with no entrance, `·` a dead end — the validator's
findings surfaced where you browse, not buried in a lint report.

**Cards show the authored record, not the player view.** A room card lists its
description, exits (with `[one-way]` and `[dangling]` markers), entrances,
occupants, and floor items. An NPC card shows its full persona — backstory,
traits, goals, voice, disposition — plus where it is, whether it wanders, and
what it holds. An item card shows who or what holds it, portability, and
aliases. Every reference is a jump-link: select an exit and you're looking at
the room next door.

## Keys

| Key | Action |
|-----|--------|
| ++slash++ | focus the search box (filters the tree as you type; ++enter++ selects the top hit) |
| ++escape++ | clear search / leave play / leave chat |
| ++p++ | play from the selected room (or the room the selected NPC or item is in) |
| ++a++ | toggle the navigator ↔ the authoring-agent chat |
| ++e++ | toggle the inspector ↔ the edit form |
| ++f++ | toggle play between live minds and offline dry-run |
| ++ctrl+s++ | save the staged world to disk |
| ++q++ | quit |

## Playing inside the editor

++p++ drops you into a modal play screen at the selected location, running the
**real engine** on a sandboxed deep copy of the staged world — no persistence,
no memory database, and nothing carries back to the draft. With a configured
backend you get live NPC minds (the header says so:
`LIVE — real NPC minds (Ollama|OpenRouter)`); with none, or with ++f++ toggled,
you get deterministic canned NPCs (`dry-run`). Type ordinary play commands —
`look`, `go north`, `say hello` — and ++escape++ to step back out.

## Editing by hand

++e++ turns the inspector into a form for the fields that are safe to edit
directly: name and description for anything; the persona fields, `wanders`, for
an NPC; aliases and portability for an item. Clearing a persona field drops it.

Apply routes the edit through the exact same shadow-validate gate the agent
uses — an edit that would break the world is rejected with the failing findings,
and a clean one lands in the staged draft with a reminder that ++ctrl+s++ writes
it to disk. Structural graph edits — exits, moving an entity — are deliberately
*not* in the form; those are the agent's territory, where the survey can check
the whole graph around the change.

## The AI author

++a++ swaps the navigator for a chat panel (the inspector stays visible, so you
can watch the world while you talk about it). Talk to it in natural language:

> Give the hilltop room a grander name — call it "The Windy Tor".
>
> Off the hilltop, add a small windswept moor that runs to a ruined watchtower,
> where a lonely beacon-keeper tends a cold signal-fire.

The agent works with a small set of deliberate tools:

- **Reads (run freely):** survey the whole world or one room's neighbourhood,
  search entities, re-validate, and *preview-play* — boot a throwaway sandbox on
  the staged world and run a command to see how a change reads in game.
- **Writes (always staged, never committed):** edit an entity (persona fields
  merge into what's there), spawn an NPC or item into an existing room, or
  author a **whole new region** from a brief — the same skeleton-first generator
  as `scripts/author.py`, where code owns the graph (ids, reciprocal exits,
  connectivity) and the model authors only the flavour.

Every write ends the same way: a validated candidate is staged, the panel shows
the diff (`+ room moor (The Windswept Moor)`, `~ npc khalen: persona`), and
**apply / discard buttons appear**. Apply commits to the staged draft and
repaints the explorer; discard drops it; undo steps back through applied
changes; save writes `world.json`. One proposal is in flight at a time — the
agent refuses to stack a second on top of an unconfirmed one.

While the agent works you see its progress live — each tool call, token ticks,
and elapsed time — including the inference the *tools* trigger (region
authoring, preview play), which run on the engine's own providers.

## Models and degradation

The agent's model resolves from `LOOM_AUTHOR_MODEL`, falling back to
`LOOM_GM_MODEL`, then the NPC tier. Region authoring inside the agent uses the
author/director tier; preview-play uses the NPC tier. With no usable backend the
chat panel says so and disables input, play degrades to dry-run, and the
explorer works fully — the workbench is never blocked on a model.
