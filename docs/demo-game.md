# The demo game

`game/` is the first world built on Loom, and it exists to prove the central
rule of the repository: **everything that mentions a specific room, character,
or story is content and configuration — never engine code.** The whole world is
one editable JSON file, `game/world/world.json`, plus an entry point
(`game/main.py`) that wires the engine's systems from your `.env`. It's small
by design, and it earns its keep three ways: it's the world you play, the
fixture every dev tool and the live behavioral harness run against, and a
worked example of every authored config block the engine understands.

## The world

A hushed, folkloric hill wilderness with a cave labyrinth beneath it. The
director's authored tone sets the register: *"a hushed, folkloric wilderness
where small omens carry weight and the land seems half-awake."*

Eight locations — a cave mouth opening onto a hill path and a hilltop with a
dead signal-fire, and below, a cave interior branching into a narrow passage,
an echoing cavern, a deep chamber, and a hidden grotto lit by glowing crystals.
Fittingly, the four deep-cave rooms were **authored by the AI author itself**
and merged through the same validation gate the [workbench](workbench/index.md)
uses — the demo world is partly a product of the tooling it demonstrates.

Five NPCs, led by a deliberately contrasting pair used throughout the project's
behavioral testing: **Odd the Hermit**, wary and deeply reticent — he only
breaks silence when directly addressed — and **Wren the Wayfinder**, gregarious,
authored to wander, and glad to physically lead a traveler where they want to
go. Deeper in you'll meet Kaelen, haunted by a prophetic echo; Orrin the
Listener, a blindfolded ex-cartographer who "sees" by sound; and Elara the
Light-Weaver, tending the crystals. Items include a rusty lantern and a brass
key at the cave mouth, and a worn hill-map that Wren carries — ask nicely.

The world file also carries all five engine
[meta blocks](engine/configuration.md#the-other-surface-worldjson-meta-blocks)
in miniature: a four-phase day clock, a four-state weather chain, loot tables
(three tiers, five themes, condition-resonant tags), the director persona, and
two starting quests — *The Old Signal-Fire* and *The Dead Hearth*.

## Playing it

```bash
make server     # one shell
make play       # another
```

The world asks your name at the door; type it. Your identity — location,
inventory, quests, and the NPCs' memory of you — survives disconnects and full
server restarts. Then:

```text
look · look at <thing> · examine <thing>
go <dir> — or just n/s/e/w/u/d
take <item> [from <who>] · drop <item> · give <item> to <who> · inventory
say <words> · who · quests · help · quit
```

Phrasing is flexible — "the"/"a" are fine, unknown phrasings fall back to the
intent parser, and compound lines work (`take lantern and key`,
`look at Wren and say what is this place?`).

Things worth trying, because each exercises a real engine behavior:

- `say Wren, will you lead me north?` — a willing guide actually *walks*, not
  just talks (the act-gate at work). Address Odd instead and enjoy the silence.
- `say Wren, may I have your map?` — NPCs hand over what they hold.
- `quests`, then walk to the hilltop — arriving completes *The Old
  Signal-Fire*, and the loot forge authors your reward on the spot.
- Stop typing for a minute — the light shifts, weather turns, the director
  stirs the quiet, and someone may speak unbidden.

The server log narrates the machinery live if you're curious: every inference
call with tokens and speed, director pulses, reflections tripping.

Runtime state lands beside the world, never in it: `game/world.save.json` (the
mutable overlay) and `game/world.memory.db` (NPC memory) — both git-ignored;
delete them for a fresh world.
