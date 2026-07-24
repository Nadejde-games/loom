# The Loom engine

Loom exists to make a particular kind of game possible: a **persistent,
text-first world that runs forever** — populated by NPCs who genuinely speak,
act, remember, and stir on their own; shaped by an unseen AI game-master; where
the world itself is editable data anyone (or any model) can author. The engine
is the game-agnostic half of that: it provides the server, the wire protocol,
the world model, the continuous game loop, and the AI layer. A *game* is content
and configuration built on top.

## What that means concretely

```
clients ──(newline-delimited JSON envelopes: {"c":channel,"d":data})── server
   terminal reads "text"/"system"; rich clients later add "map"/"entities"
                              │
                     GameServer (async TCP)  ── continuous GameLoop
                              │
                           Engine  ── commands, sessions, player state
                              │
        World (locations/entities, editable JSON)   AI layer
                                              (LLMProvider → NpcMind → MemoryStream)
```

A `GameServer` accepts clients over TCP; a `GameLoop` ticks the world's ambient
systems every few seconds whether or not anyone is typing; the `Engine` binds
sessions to the world and to NPC minds. When you speak in a room, the engine
decides *which* NPCs even engage (a cheap salience gate, before any model call),
then hands each engaged mind a read-only scene and its retrieved memories — and
whatever comes back is validated before it can touch the world.

## The golden rule

**Never execute raw model text.** NPCs speak *and* act, but every state change
goes through a schema-validated action: the mind *proposes*, the engine
*disposes*. This is enforced in layers, not by trust:

1. **Constrained decoding** — the action registry renders a JSON Schema of the
   turn envelope from the same specs the validator reads, so the shape the model
   is forced to emit and the shape the engine checks can never drift (grammar
   enforcement is verified on vLLM and OpenRouter; on Ollama the layers below
   are the real guardrail).
2. **Tolerant parsing** — a messy reply (fenced blocks, prose wrapping, a
   truncated envelope) is repaired where possible, and genuine prose degrades
   gracefully to speech.
3. **Validate, retry once, drop** — every proposed action is checked against
   the registry; the model gets exactly one retry with the precise errors fed
   back, and anything still invalid is dropped.
4. **Never speak raw JSON** — a reply that looks like a failed envelope is
   retried, never shown to a player.
5. **Defense at execution** — handlers re-resolve every argument against the
   real world and contain their own failures.

The same principle runs through everything the model touches: the loot forge
rolls all mechanics in code and lets the model author only flavour text; the
world-author generates the room graph in code (ids, reciprocal exits,
connectivity) and lets the model write only descriptions.

## Design commitments

- **Protocol separated from transport.** Clients speak JSON envelopes on named
  channels; TCP today, WebSocket later, same schema. A richer client subscribes
  to more channels — it isn't a different game.
- **The world is editable data, not code.** One JSON file (or a directory of
  region files) defines rooms, NPCs, items, and the world-level config blocks —
  and a structural survey can validate any world the engine is pointed at.
- **AI behind one interface.** The entire dependency the engine has on AI is one
  protocol: `complete(system, messages, schema, temperature) -> str`. Fake,
  Ollama, vLLM, OpenRouter, and Anthropic providers all satisfy it; the engine
  can't tell them apart.
- **AI runs off the loop.** Every model call happens on a background task — a
  slow reply never stalls the world or blocks another player's input.
- **One memory substrate.** NPCs (and players' identities) share a single
  memory-stream design — relevance-ranked recall that survives restarts.
- **Everything ambient is opt-in.** The base engine has no director, no clock,
  no weather, no loot, no idle NPCs — a game attaches each one explicitly.
  A minimal world is genuinely minimal.

## Deliberately small

The framework core keeps a strict dependency budget: exactly two runtime
dependencies (`httpx` for the provider HTTP path, `json-repair` for envelope
recovery), no native extensions, and no imports of UI or agent frameworks —
the [workbench](../workbench/index.md) imports `loom`, never the reverse. It
runs fully offline on a deterministic fake provider, which is also what makes
the whole system testable [with zero network](../development.md).

Dig deeper: **[Capabilities](capabilities.md)** covers each subsystem and what
it does; **[Configuration](configuration.md)** covers every knob;
**[Roadmap](roadmap.md)** covers what's done and what's next.
