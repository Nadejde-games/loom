# Project plan — Loom engine & the forever game

Living document. The reference for where we are and where we're going.
Last updated: 2026-07-10.

## Vision

An LLM-driven, text-first, server–client game world that runs forever, populated
by NPCs with personality and memory, shaped by an AI game-master, with dynamic
AI-authored loot and quests — and a player whose own history and personality
accrete through play. Built so the whole world is editable, specifiable data.

## Framework-first mandate

The reusable pieces form a **game-agnostic engine framework — "Loom"** (`loom/`,
working name). The specific game (`game/`) is only the first world built on it.
Loom must remain packageable and independent of any one game's content.

## Design commitments (do not drift from these)

1. **Protocol separated from transport.** Messages are typed JSON envelopes
   `{"c":channel,"d":data}`. TCP now → WebSocket later, same schema. A terminal
   reads `text`/`system`; rich clients add `map`/`entities`/`tiles` additively.
2. **World is editable data, not code.** Rooms/NPCs/items live in human-readable
   files behind a schema — the surface an authoring agent edits.
3. **AI behind one interface.** Everything depends on `LLMProvider`; the engine
   can't tell fake from real. Provider is swappable and lazy.
4. **Never execute raw model text.** Dialogue may be shown; every *state change*
   goes through a schema-validated tool-call. This seam is load-bearing.
5. **AI calls run off the game loop** (async), so latency never stalls the world.
6. **Memory is one substrate** shared by NPCs and players (Generative-Agents
   memory stream; recency now, importance/embeddings/reflection later).

Settled research behind these choices is recorded separately; see the research
conclusions in project memory (engine survey, protocol, AI architecture).

## Roadmap

Status legend: ✓ done · ▶ next · ○ planned

### Phase 0 — Foundations & vertical slice  ✓ (2026-07-09)
Server (async TCP), JSON-envelope protocol, continuous game loop, world model
loaded from editable JSON, default engine with a command set, AI layer
(`LLMProvider` → `NpcMind` → `MemoryStream`), reference terminal client, and an
end-to-end smoke test. One NPC ("Odd the Hermit") converses in character and
remembers. Runs fully offline via `FakeProvider`.

### Phase 1 — Local minds (Ollama) + provider portability  ▶ in progress
Drive NPCs with **local inference on the 2× RTX 6000 Ada (96 GB)** instead of a
paid API. Built and proven (2026-07-09): `OpenAICompatibleProvider` + a thin
`OllamaProvider` subclass, talking the OpenAI `/v1/chat/completions` schema so
the same client swaps Ollama ↔ vLLM/SGLang by base_url + model name only.
Now verified on **GPU** end-to-end (socket → server → engine → mind → provider):
`qwen3.5:35b-a3b` answers in-character at **~0.8 s warm** on one Ada, beating
dense `qwen3.5:27b` (~2.1 s). Critical gotcha: Qwen3.5 routes chain-of-thought
into a separate `reasoning` field and counts it against `max_tokens`, so
`OllamaProvider` defaults `think=False` → `reasoning_effort:"none"` (NOT
`/no_think`, which does nothing on 3.5) — otherwise `content` comes back empty.

Backend decision (research 2026-07-09): **Ollama for dev now** (trivial, already
installed; concurrency via `OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_LOADED_MODELS`);
**graduate to SGLang or vLLM for scale** — 1 model replica per GPU (data-parallel,
no NVLink needed), FP8, strong prefix caching over the shared persona prompt.

Model decision (updated 2026-07-09 after verifying the **Qwen3.5** release,
Feb 2026 — post-dates the assistant's Jan-2026 training cutoff, hence the earlier
Qwen3 default): primary **`qwen3.5:35b-a3b`** (MoE, ~3B active → fast NPC turns;
~24GB q4 fits one Ada with large context headroom; native tool-calling, 256K ctx).
Quality/GM tier **`qwen3.5:27b`** dense (~17GB q4; richer persona fidelity when
latency is loose). A/B both on-hardware via `scripts/try_provider.py` — swapping
is env-only, zero code. Superseded: qwen3:14b (older arch, 40K ctx). Qwen3.6 also
exists now; the swappable provider means the model is never locked in.

Remaining in this phase:
- ✓ **GPUs unblocked** (2026-07-09): rebooted into kernel 6.17.0-35 → driver
  535.309.01; both RTX 6000 Ada online, Ollama runs `100% GPU`.
- ✓ **Models pulled & wired**: `qwen3.5:35b-a3b` + `qwen3.5:27b`; game runs via
  `LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b python game/main.py`.
- ✓ **Engine polish**: NPC replies dispatched as background tasks — non-blocking
  (room commands respond instantly while an NPC "considers your words…") — and a
  provider outage degrades to a stock in-character line, never crashing the
  connection. Provider already had timeouts + retries on 429/503/529.
- ✓ **Service tuning** installed (2026-07-09) from `ops/ollama.service.d/override.conf`
  (NUM_PARALLEL / CONTEXT_LENGTH / MAX_LOADED_MODELS / KEEP_ALIVE / FLASH_ATTENTION);
  verified default ctx 8192, keep-alive forever. (No passwordless sudo — the user
  ran the install.)
- Optional: a server "plain-text mode" so a bare telnet/nc client gets clean prose
  instead of JSON envelopes (graceful degradation, GMCP-style).
- Deferred to when it pays: Anthropic path stays available for comparison.

### Phase 2 — The action seam  ▶ in progress (emote + move slices landed 2026-07-10)
Give NPCs (and later the GM) the ability to *act*, not just speak, via
schema-validated actions. A validation + retry layer turns model output into
safe game actions. This is the interface the game-master and loot engine both
build on.

Design (signed off 2026-07-10): **structured JSON envelope**, not native
OpenAI tool-calling. A turn is one JSON object `{"speech": ..., "actions":[...]}`;
chosen because it keeps `LLMProvider` a plain `complete()->str` (no per-backend
tool-call fidelity dependence), lets `FakeProvider` and the whole test suite run
offline, and is one round trip. Native tools can graduate later *behind the same
registry* — the registry (schemas + handlers) is the durable part, how the model
is asked is swappable like the provider.

Built:
- `loom/action.py` — game-agnostic `ActionRegistry`: an action is a name +
  description + tiny param schema (`str|int|float|bool|enum`, required/optional)
  + a handler. Dependency-free validation. `default_registry()` ships `emote`
  and `move`. `ActionResult` grew a `broadcasts` list — `(location_id, text)`
  lines for actions that touch more than the actor's room (move's departure +
  arrival), generalising to N rooms without another seam change.
- `loom/ai/mind.py` — `NpcMind.converse(scene=…)` returns a validated `Turn`
  (speech + `ActionIntent`s). Tolerant JSON extraction (bare / fenced /
  prose-wrapped / **trailing-junk** — Qwen3.5 sometimes appends a stray `}` or
  sends `args` as a bare list); one bounded **retry** feeding the exact
  validation error back; degrades to pure speech on total parse failure. Never
  mutates the world. `Scene` is a read-only perception snapshot the engine hands
  in (place, exits, others present) so the NPC can choose real exits — the mind
  still never reads `World`.
- `loom/engine.py` — the mind proposes, the engine disposes: it composes a
  `Scene` from the world (`_scene_for`), executes only validated intents via the
  registry handler, writes the actor's memory, and **broadcasts** narration —
  including per-room `broadcasts` for a move — to everyone in the affected rooms
  (correct for multiplayer: each room hears only its own line).
- The `move` action: schema is `direction: str` (exit vocabulary is world-data,
  never hardcoded); the handler is the world-aware gate — resolves the direction
  against the actor's *actual* exits, mutates via `World.move`, reads the
  arrival direction back from the destination's own exits (asymmetric/one-way
  safe), and raises `ActionError` (silently dropped) on a bad direction.
- `tests/` — 48 offline tests (validation, parse-tolerance incl. the live
  malformations, retry recovery, engine end-to-end emote **and** a two-room
  move, perception rendering). No GPU needed.
- Verified live on GPU: `qwen3.5:35b-a3b` returns clean speech + a validated
  emote ~0.6 s warm (emote broadcasts over the socket end-to-end), and — given a
  compliant persona and a scene — a valid `move` bound to a real exit ~1 s warm.
  A stubborn persona correctly *declines* to move (chooses `emote`): the NPC's
  choice is real, foreshadowing B4.
- Demo content (`game/world/world.json`): a second NPC, **Wren the Wayfinder**, a
  cheerful guide, now shares the start room with Odd — a willing foil to his
  refusal, so one `say` shows both minds and gives `move` a character who will
  actually walk. The two-NPC room is itself the live case for B1 (`say to X`) and
  B4 (choice-to-react): today *both* answer every `say`. It also surfaced **B5**
  — the model under-selects the world-mutating `move` in favour of `emote`
  (persona wording can't fix it; it's a sampling/prompt-weight lever).

Remaining actions to build out on this seam (each ~a handler + schema + a world
capability where needed): **give_item** (needs an item/inventory world-model
first — near Phase 4), **offer_quest**, **remember_fact**. Also: let *players*
emote/act (the player-side mirror of the seam — see B1).

### Phase 3 — Game-master director  ○
A world-observing "director" agent on a slow cadence (hangs off the game loop),
injecting events/quests and adjusting the world to player behaviour, while
characters stay in-role. Emits only tool-call actions (Phase 2). Runs on its own
model variant with a large baked context (`ops/modelfiles/loom-gm.Modelfile`) —
Ollama's `/v1` ignores per-request `num_ctx`, and a Modelfile `PARAMETER num_ctx`
overrides the global 8192 NPC default upward. Large context ⇒ large KV-cache
VRAM, so budget one card for it (or quantize KV).

### Phase 4 — Loot forge  ○
Dynamic, context-aware item generation: the LLM authors name/lore/tags as
schema-constrained JSON; numeric balance stays in code/tables. Tied to player
history, quests, location, and what the world is currently doing.

### Phase 5 — Deeper minds & persistence  ○
Importance scoring, embedding retrieval (start SQLite + brute-force cosine),
and reflection on the memory stream. Persist world + memories across restarts.
Player personality/history accretion on the same substrate as NPCs.

### Phase 6 — Rich transport & multiplayer  ○
WebSocket transport implementing the same `Handler` contract; emit `map` /
`entities` channels for an ascii/2D client. Harden true multiplayer presence
(players see and hear each other; per-room broadcast).

### Phase 7 — Authoring tools  ○
AI-assisted world creation and editing over the world schema — descriptions,
regions, NPCs, story — with validation. The GM/creator toolkit.

## Cross-cutting (ongoing)
Tests alongside each phase · schema/versioning for save data · cost & latency
budgets for AI calls · keeping `loom/` free of game-specific content.

Unscheduled improvements noticed during review live in `docs/BACKLOG.md`
(richer command grammar · fused speech+action lines · rich text formatting ·
NPC choice-to-react). Promote them into a phase with a real design when the
moment is right.

## How to run (current)
With the venv active (`source .venv/bin/activate`), `PYTHONPATH` is not needed:
```
Server:     python game/main.py
Client:     python client/terminal.py
Unit tests: python -m unittest discover -s tests    (offline, no GPU)
Smoke test: python scripts/smoke.py      (server must be running)
Wire demo:  python scripts/wire_demo.py  (self-contained)
Provider ping: python scripts/try_provider.py   (shows speech + validated actions)
```

### Choosing the AI provider (env)
```
LOOM_PROVIDER=fake                                   # deterministic, offline (default)
LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b   # local inference
   LOOM_OLLAMA_HOST=http://localhost:11434           # (default)
LOOM_PROVIDER=anthropic ANTHROPIC_API_KEY=...        # Claude
```
Same envs apply to `game/main.py` and `scripts/try_provider.py`.
