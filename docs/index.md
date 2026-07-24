# Loom

Loom is a reusable, **game-agnostic framework for AI-driven, text-first,
server–client worlds** — a MUD / interactive-fiction engine built from scratch in
Python (3.11+, asyncio). NPCs are LLM-driven minds that speak *and* act; an unseen
game-master "director" shapes ambient beats — weather, a turning world-clock,
foreshadowing in the rooms just ahead of the players.

The framework runs fully offline with no API key (NPCs fall back to a deterministic
`FakeProvider`). Point it at a real model — hosted or local — and every NPC upgrades
with no code change.

## What lives in the repository

| Component | What it is |
|-----------|------------|
| **`loom/`** | The framework: server, engine, world model, persistence, quests, loot, clock and weather, the salience gate, the action validator, and the AI layer (NPC minds, the director, memory, reflection). |
| **`game/`** | The first world built on Loom — [the demo game](demo-game.md). Content and configuration only, no engine logic: the world is editable data (`game/world/world.json`), not code. |
| **`client/`** | A minimal reference terminal client for playing. |
| **`authoring/`** | The [workbench](workbench/index.md) — a text-mode app to explore, play, and edit a world, by hand or by asking an AI world-author. |

## Design commitments

Four commitments shape everything in the engine:

- **Never execute raw model text.** NPCs speak and act, but every state change goes
  through a schema-validated action. Dialogue is shown; actions are validated,
  invalid ones retried once and then dropped. Model output is parsed tolerantly and
  never leaks to the player as raw JSON.
- **AI sits behind one interface.** A single `LLMProvider` abstraction — the engine
  cannot tell a fake provider from a real one, and hosted (OpenRouter), local
  (Ollama, vLLM), Anthropic, and offline backends are interchangeable by
  configuration alone.
- **The world is editable data, not code.** A world is a JSON file the loader
  ingests; authoring means editing data, and the engine works on any world that
  passes its structural survey.
- **Protocol is separated from transport.** Clients speak newline-delimited JSON
  envelopes — over TCP today, over WebSocket later, with the same schema.

## Where to go next

- **[Quick start](setup/quickstart.md)** — get a world running in a few minutes
  with the interactive setup wizard.
- **[Choosing models](setup/models.md)** — which models to run on which hardware,
  and the trade-offs.
- **[Loom engine](engine/index.md)** — what the engine is meant to achieve, what is
  built, and how to configure it.
- **[Workbench](workbench/index.md)** — exploring and editing worlds, including the
  AI world-author.
- **[Development](development.md)** — how to work on the engine and the workbench,
  and the two-gate testing philosophy.
- **[Demo game](demo-game.md)** — the small bundled world and why it exists.
