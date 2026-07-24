# Capabilities

What is actually built, subsystem by subsystem. Everything here ships in
`loom/` and is exercised by the offline test suite; the model-dependent
behaviors are additionally guarded by the live
[behavioral harness](../development.md#gate-2-the-live-behavioral-harness).

## The wire and the server

Clients and server exchange **newline-delimited JSON envelopes** —
`{"c": channel, "d": payload}` — on named channels: `text` and `system` from
the server, `input` from the client, with `map` and `entities` reserved for
future rich clients (the pattern borrows GMCP's text-plus-side-channels idea,
minus the telnet framing). The server also accepts bare lines, so `nc` or
telnet can drive the game. The `text` payload is polymorphic: a plain string,
or a styled line — a list of spans tagged with *semantic* roles (`name`,
`speech`, `emote`, `item`, `exit`, `place`, `quest`, `ambient`, `danger`). The
engine never emits a color; the client owns the theme and degrades to plain
text.

`GameServer` is a pure transport: all game logic sits behind a three-method
`Handler` protocol (`on_connect` / `on_input` / `on_disconnect`), which is
exactly what a future WebSocket transport would implement. The `GameLoop` ticks
registered systems on a fixed cadence and contains their failures, so a broken
ambient system can never kill the world.

## Player commands

A game-agnostic, world-free **syntactic parser** handles the common case with
no model at all: a verb table with synonyms and multi-word verbs
(`take/get/grab/pick up`), a `verb + object + preposition + object` grammar,
articles tolerated, and symbolic per-slot scopes the engine resolves against
the real room, inventory, and occupants — with disambiguation ("Which do you
mean: brass key, iron key?") inherited from the shared name resolver.

On top of that:

- **Compound and chained commands** — `take lantern and key` takes both;
  `look at Wren and say hello` runs two commands in order; free-text verbs
  swallow their remainder (`say hello and goodbye` is one utterance). Partial
  success is reported per command, and `take all` / `give all to X` expand
  against the verb's own scope.
- **An LLM intent fallback** — when the deterministic parser doesn't know the
  verb, the free text goes to the model constrained by a command grammar and
  comes back as a canonical parse, flowing through the identical dispatch.
  "Offer the map to Wren" → `give`; "head north" → `go`.

Player world-changes run through the **same action registry** NPCs use — one
validated path for every mutation, whoever proposes it.

## The world model

Locations with directional exits; NPCs with a free-form persona (backstory,
traits, goals, voice, and a `disposition` that acts as the talkativeness dial);
items whose single `holder` field is the whole containment story — a room
floor, a character's inventory, or another item as a container. Standing
**conditions** exist at world scope and per-location (a storm, nightfall) and
fold into every look and scene. A world loads from one JSON file or a directory
of region files merged in order, and any non-structural top-level key is
carried as world-level `meta` — which is where the director persona, clock,
weather, loot tables, and starting quests live as authored data.

## NPC minds

Each NPC is an `NpcMind` holding its persona, its memory stream, and the subset
of actions it's offered. The engine hands it a read-only `Scene` — a mind never
touches the `World`. A turn is speech plus up to three validated actions, and
**silence is a first-class outcome**: the empty turn is modeled in the prompt
(not just permitted), an unaddressed remark is framed as overheard rather than
asked, and a reticent character genuinely stays quiet.

Because sampling under-selects world-mutating actions (a guide who *says* "I'll
lead the way!" but never moves), minds support a **two-pass act-gate**: a cheap,
low-temperature pass decides the deed authoritatively, then the blended pass
supplies the in-character speech. Measured live, move-when-asked went from ~70%
to 8/8 with the gate on — it's the game's default.

Before any of that, a deterministic **salience gate** decides who engages at
all: if someone present is named ("Wren, …"), only the named NPC thinks;
bystanders cost zero model calls. The gate is a swappable seam.

## Memory, reflection, and identity

Memory follows the Generative-Agents design: every observation, heard line, and
deed becomes a scored entry, and retrieval ranks by **recency + importance +
relevance** (embedding similarity when an embedder is configured, graceful
degradation to recency when not). Importance is scored at write time by a cheap
deterministic heuristic. The store is SQLite — memories survive restarts, and
embeddings persist so they're computed once.

On a slow cadence, NPCs **reflect**: recent memories prompt salient questions,
each question retrieves evidence from the deeper past, and the answers are
stored as durable, cited beliefs ("… (because of: …)") that then compete in
retrieval like any memory. Players get durable **identity** the same way: the
world asks your name at the door, and your location, inventory, quests, and the
NPCs' memory of you persist across disconnects and full restarts.

## Autonomy: reactions, idle stirs, clock and weather

NPCs react to what happens around them — a director beat, another NPC's line,
nightfall — through a bounded **reaction cascade**: each hop is re-judged for
salience, with a self-guard, per-NPC cooldowns, a decaying budget, and a
generous runaway fuse. The limiter is appropriateness, not an artificial depth
cap, so a real back-and-forth can emerge and machine-gun ping-pong can't.

A quiet room doesn't go dead. The **world-clock** advances an abstract
minute-of-day decoupled from any player and turns authored phases (dawn, dusk…)
into standing conditions plus one ambient beat in occupied rooms. **Weather**
random-walks along an authored chain of sky-states on the same bridge. And the
**idler** lets one NPC in a quiet, player-occupied room stir from its own goals
— most often silently, sometimes walking somewhere if it's authored as a
wanderer (a hard rail in code, not a prompt request, keeps anchored NPCs home).

## The director

An unseen game-master watches the world's chronicle on a slow pulse and shapes
the *world*, never minds: it can stage an ambient event, raise or lift a
standing condition, spawn an item, or offer a quest. Restraint is engineered in
layers — a deterministic floor (enough new events, a cooldown) plus a
model-side **wait/act gate** measured to hold both poles: a bare scene draws no
beat 8/8, an evocative one earns a beat 8/8. A **lull** trigger stirs a room
that's been quiet too long, and **foreshadowing** lets the director set a
condition in the empty room just ahead of a player, sparingly. The director's
own "speech" is a private note; only its validated actions reach the world.

## Quests and loot

Quests are deliberately simple and un-gameable: a `reach` objective completes
on a deterministic engine check when the player arrives — never player-claimed,
never model-adjudicated. Completing one fires the **loot forge**: code rolls
the tier, theme, and tags from authored tables (with a mod-group guard so a
brief never contradicts itself, and a preference for tags resonant with the
room's current conditions), and the model authors only the name, description,
and aliases — bounded, with a plain fallback so the forge never fails to
produce an item.

## Persistence

The authored world is a *definition*, reloaded from `world.json` at every boot;
only the mutable **delta** persists — a versioned JSON overlay (positions,
items, conditions, quests, clock/weather state, players, the chronicle tail)
written atomically with a `.bak` fallback, autosaved on a pulse cadence and on
shutdown. NPC memory lives in its own SQLite store, which the overlay simply
points at.

## Providers and telemetry

The engine's entire AI dependency is one protocol:
`complete(system, messages, schema, temperature) -> str`. Shipped
implementations: a deterministic **fake** (offline), **Ollama**, **vLLM**,
**OpenRouter**, and **Anthropic** — the OpenAI-compatible ones differ only by
base URL and model, and share retry/backoff, connection pooling, and per-model
adaptive pacing for rate limits. Structured output is requested where the
backend honors it (verified on vLLM and OpenRouter) and the parse-validate-retry
layers cover the rest.

Because every model call passes through that one choke point, **inference
telemetry** instruments everything at once: each call logs start and finish
with elapsed time, tokens, and tok/s — streamed live for local backends — plus
an in-place status line for calls in flight. It's inert unless a reporter is
installed, and the workbench installs its own to show the same numbers in the
chat panel.
