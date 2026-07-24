# Engine roadmap

The project moves in phases, each landed behind
[both test gates](../development.md#the-two-gates) before the next begins. The
full history with design rationale lives in the repo (`docs/PLAN.md`, with
design notes in `docs/spikes/`); this is the state of play.

## Done

| Phase | What landed |
|-------|-------------|
| **0 — Foundations** | server, protocol, game loop, world model, engine, AI layer, terminal client, smoke test — the vertical slice |
| **1 — Local minds** | Ollama-backed NPCs and provider portability (one OpenAI-compatible waist) |
| **2 — The action seam** | NPCs act as well as speak: emote, move, give/take/drop, staged events; the inventory model; name resolution; the deterministic command parser and the LLM intent fallback; grammar-constrained decoding as hardening |
| **3 — The director** | the game-master with engineered restraint (deterministic floor + model-side wait/act gate), autonomy (world-clock, weather, lull, off-screen foreshadowing), and reach (spawn items, offer quests + the quest subsystem) |
| **5 — Deeper minds & persistence** *(headline threads)* | the save overlay, the Generative-Agents memory stream, SQLite-backed memory with embeddings, reflection into durable cited beliefs, durable player identity, idle-NPC autonomy |
| **7 — Authoring tools** | the atlas (survey / validate / render any world) and the AI region author (skeleton-first: code owns the graph, the model authors flavour, a bounded repair loop judged by the atlas) |
| **8 — The workbench** | the explorer, play-in-editor, and the authoring agent with hand-editing — see the [workbench roadmap](../workbench/roadmap.md) for what it deferred |

Along the way, the backlog discipline (`docs/BACKLOG.md`) landed player-facing
polish worth naming: fused speech-and-action beats, semantic rich-text styling,
NPC choice-to-react with a salience gate, compound and chained commands, and
the two-pass act-gate that made "a guide asked to lead actually walks" reliable.

## In progress

**Phase 4 — the loot forge** shipped its first slice (quest-reward forging:
code rolls tier/theme/tags, the model authors flavour). Deliberately deferred,
to grow without moving the seam:

- combat/stat affixes (typed `(field, number)` apply-pairs)
- more firing paths — a director forge action, discoveries seeded in places
- deeper mod-pool tiers and tuned spawn weights
- unique/fixed items, identification, a wider item-type and slot vocabulary

**Phase 5 — polish threads** (each to start with a prior-art survey and a
design sign-off):

- idle-NPC depth: multi-room pathing toward a destination, real per-NPC
  plans/agendas, authored clock-hooked routines
- reflection polish: persist the reflection watermark across restarts, fairer
  scheduling between agents, reflection trees (depth > 1)
- identity follow-ups: password authentication (the record already reserves the
  slot), a player-owned memory stream for a "previously, on…" recap, an
  account/character split
- memory: LLM-scored importance (batched, off-loop), a `remember_fact` action,
  a local in-process embedder

## Planned

**Phase 6 — rich transport and multiplayer** is the next big phase:

- a WebSocket transport implementing the same three-method `Handler` contract
- emitting the reserved `map` / `entities` channels so an ascii-map or 2D
  client can render the world — the protocol already carries the channel names
- hardening true multiplayer presence

Beyond it, the open threads: measuring the director on a dedicated wide-context
model variant (the wiring exists; it has never been exercised), richer command
handling (`but`/exclusion, cross-command disambiguation, an LLM decompose mode
for genuinely ambiguous lines), and the play-loop papercuts that only surface
in real play — which is, after all, the point of the whole project.
