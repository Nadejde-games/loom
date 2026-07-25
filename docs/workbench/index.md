# The authoring workbench

The workbench is a **text-mode application for exploring, playing, and editing a
world** — by hand or by asking an AI author in natural language. It exists
because authoring a world through CLI incantations
(`python scripts/author.py "…" --attach hilltop --out …`) is the wrong ergonomic
for a creative task: an author wants to *see* the world, wander through it, poke
at a character, and make a change — all in one place.

```bash
make workbench        # or: python -m authoring [world.json | world-dir]
```

It opens on the bundled world by default and needs the `[authoring]` extra
(Textual for the UI, Pydantic-AI for the agent). Without a live model backend it
still opens read-only, and in-editor play degrades to an offline dry run.

## What it does

Three capabilities, in one app:

- **Explore** — a navigable world tree (rooms, NPCs, items) with live search
  over ids, names, aliases, and tags; an inspector card for every entity showing
  its raw authored record (persona, exits, holders); and one-keystroke
  jump-links to walk the world graph. Structural problems the world survey finds
  — an unreachable room, a dead end — are marked right in the tree, where you
  browse.
- **Play, inside the editor** — press ++p++ on any room and you're standing in
  it, in a sandboxed copy of the engine. Real NPC minds if a backend is
  configured, canned offline ones if not. The sandbox is a deep copy with no
  persistence attached: nothing you do in play touches the authored world.
- **Edit — by hand or by agent.** Press ++e++ to edit an entity's fields
  directly (name, description, persona, aliases), or press ++a++ to chat with
  the **AI world-author**: "add a locked door to the cellar", "give Khalen a
  backstory", "off the hilltop, add a windswept moor running to a ruined
  watchtower". The agent can survey, search, preview-play, edit entities, spawn
  NPCs and items, and author whole new regions — and it knows which entity you're
  looking at, so "make **this room** colder" just works.
- **Review before you commit.** Every change you haven't saved is tracked against
  the version on disk: changed entities are badged in the tree (`✚` added, `✎`
  edited, `✖` removed), and selecting one splits the inspector into a **saved vs
  working** before/after that persists as you navigate — so a complex, multi-step
  edit is always reviewable, right up until you save.

## The safety gate: propose, validate, apply

Every change — typed by you or proposed by the agent — goes through the same
tested gate in the framework (`loom/worlddraft.py`), never through the agent's
good behavior:

1. **Shadow copy.** Mutations are built on a deep copy of the staged world; the
   draft — and the file on disk — are untouched.
2. **Validate.** The candidate is surveyed by the atlas validator: dangling
   exits, bad directions, broken holders, id collisions are *errors* and block
   the merge; softer findings (a one-way exit, a thin persona) pass as warnings.
3. **Refine, then apply.** A candidate that surveys clean is *staged* and shown
   at once as a before/after — and you can keep talking to refine it, each
   adjustment accumulating onto the same proposal. The agent has **no tool that
   commits**; you apply (fold it into the staged draft) or discard. Multi-level
   undo covers you after the fact.

A candidate that doesn't survey clean is never staged at all — the agent gets
the findings back as prose and is told to revise and re-propose. Nothing reaches
disk until you save (++ctrl+s++), which applies any open proposal first so a
staged change is never lost.

## A deliberately separate stack

The AI author is **not** built on the engine's slim `LLMProvider`. NPC turns are
a single schema-constrained call; a multi-step authoring session wants native
tool-calling, a ReAct loop, and streamed progress — so the agent runs on
Pydantic-AI, and the workbench UI on Textual. Both are tool dependencies that
live outside the framework's dependency budget: `loom/` never imports either
(the workbench imports `loom`, never the reverse), which is exactly why the
safety gate lives in `loom/worlddraft.py` and not in the agent framework.

The agent runs on its own model tier, `LOOM_AUTHOR_MODEL` — an authoring session
wants reliable judgment more than speed. See
[Choosing models](../setup/models.md) for what to run it on, and
[Using the workbench](usage.md) for the full tour.
