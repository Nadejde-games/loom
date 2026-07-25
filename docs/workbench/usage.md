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
keeps a live tally — rooms · npcs · items · survey errors/warnings · play mode —
and, once you start changing things, an **unsaved-change count**
(`unsaved ✚2 ✎1`).

The tree marks what the world survey knows: `★` the start room, `⚠` an
unreachable room or one with no entrance, `·` a dead end — the validator's
findings surfaced where you browse, not buried in a lint report. Alongside those
it carries a **change badge** for anything you've edited but not yet saved:
`✚` added, `✎` edited, `✖` removed. (See
[Tracking your changes](#tracking-your-changes).)

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
| ++ctrl+s++ | save to disk (applies any open agent proposal first) |
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
and a clean one lands in the staged draft. The edited entity now carries an `✎`
badge in the tree and opens as a **before/after split** until you save. Structural
graph edits — exits, moving an entity — are deliberately *not* in the form; those
are the agent's territory, where the survey can check the whole graph around the
change.

## The AI author

++a++ swaps the navigator for a chat panel (the inspector stays visible, so you
can watch the world while you talk about it). Talk to it in natural language:

> Give the hilltop room a grander name — call it "The Windy Tor".
>
> Off the hilltop, add a small windswept moor that runs to a ruined watchtower,
> where a lonely beacon-keeper tends a cold signal-fire.

**It knows what you're looking at.** The inspector's current selection is fed to
the agent each turn, so a bare "make **this room** colder", "give **the
blacksmith** a limp", or "rename **it**" resolves to the entity on screen — no
need to name an id. It echoes back the concrete entity it resolved to, and asks
only when the referent is genuinely ambiguous.

The agent works with a small set of deliberate tools:

- **Reads (run freely):** survey the whole world or one room's neighbourhood,
  search entities, re-validate, and *preview-play* — boot a throwaway sandbox on
  the staged world and run a command to see how a change reads in game.
- **Writes (always staged, never committed):** edit an entity (persona fields
  merge into what's there), spawn an NPC or item into an existing room, or
  author a **whole new region** from a brief — the same skeleton-first generator
  as `scripts/author.py`, where code owns the graph (ids, reciprocal exits,
  connectivity) and the model authors only the flavour.

**Refine the proposal in conversation.** A write stages a validated candidate;
the inspector shows it at once as a before/after split, and the panel reveals the
**apply / discard** controls. You don't have to accept it before continuing —
keep talking, and each adjustment *accumulates* onto the same open proposal ("now
make it warmer", "and add a torch on the floor"). Nothing touches the world until
you **apply** (fold the accumulated change into the staged draft) or **discard**
(drop the whole set). A candidate that doesn't survey clean is never staged; the
agent gets the findings back and revises.

**Apply is optional — save implies it.** ++ctrl+s++ applies any open proposal
*first* and then writes `world.json`, so a proposed change is never silently
dropped on save. Reach for apply on its own only to set an undo checkpoint
mid-session, or before switching to a hand-edit. The everyday loop is: talk until
the split looks right → ++ctrl+s++.

While the agent works you see its progress live — each tool call, token ticks,
and elapsed time — including the inference the *tools* trigger (region
authoring, preview play), which run on the engine's own providers.

## Tracking your changes

The workbench tracks everything you've changed since the **last save** and shows
it two ways, so you can build up a complex edit and always see where you are:

- **Badges in the navigator.** Every changed entity carries a coloured mark —
  `✚` added, `✎` edited, `✖` removed — and the header keeps a running tally
  (`unsaved ✚2 ✎1`). Navigate freely; the badges tell you what you've touched.
- **A persistent before/after.** Selecting any changed entity splits the
  inspector — the **saved** version on top, the **working** version below, with
  the changed fields named. It stays put as you navigate away and back, and
  survives **apply**; only **save** clears it. Unchanged entities show the normal
  single card.

The comparison is always against the version **on disk**: "before" is the last
saved world, "after" is everything you've done since — agent changes, hand edits,
and an in-flight proposal alike. **Save** writes it all and resets the baseline,
so the badges and splits clear and the next round of deltas is measured from
there. Nothing is ever written to disk until you save.

## Models and degradation

The agent's model resolves from `LOOM_AUTHOR_MODEL`, falling back to
`LOOM_GM_MODEL`, then the NPC tier. Region authoring inside the agent uses the
author/director tier; preview-play uses the NPC tier. With no usable backend the
chat panel says so and disables input, play degrades to dry-run, and the
explorer works fully — the workbench is never blocked on a model.
