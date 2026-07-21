# Project plan — Loom engine & the forever game

Living document. The reference for where we are and where we're going.
Last updated: 2026-07-20.

## Status snapshot — 2026-07-20 (Phase 5: persistence + memory depth + reflection + identity + idle-NPC)

**Phase 5's headline threads are all in** — six landed slices past the Phase 4 status block
below, both gates green throughout (offline **552**):
- **Persistence (`fe07e0c`)** — the world survives a restart. A versioned JSON *overlay*
  of mutable runtime state (entity positions, forged loot, conditions, quests, clock/
  weather, chronicle) composed onto the authored `world.json` reloaded each boot; snapshot
  not event-log; atomic save (temp→fsync→`os.replace`+`.bak`) on shutdown + a loop
  autosave. `loom/persistence.py`. Player-held items drop to the floor at snapshot (players
  are session-ephemeral — durable player identity was deferred here, then **landed as
  slice 3 below**). Spike: `docs/spikes/persistence.md`.
- **Memory depth 1a (`0c7a588`)** — the Generative-Agents retrieval score verbatim
  (`recency + importance + relevance`, min-max, weights 1/1/1). Importance a cheap
  deterministic heuristic at write (not an LLM call per memory); relevance via an
  `EmbeddingProvider` seam (OpenRouter `/api/v1/embeddings`, default `baai/bge-m3` —
  verified live — plus a deterministic offline fake). Spike: `docs/spikes/memory.md`.
- **Memory depth 1b (`b685cae`)** — SQLite-backed memory: one table by `agent_id`, the
  embedding a float32 BLOB, incremental INSERT, embeddings persist across a reboot;
  `load_state` doubles as the one-time JSON→SQLite migration.
- **Reflection (`27fea59`)** — the Generative-Agents reflection step, depth-1: on a slow,
  threshold-gated cadence an agent distills its accumulated memory into higher-level
  `kind="reflection"` memories (a two-call cognition: questions → retrieve evidence →
  grounded insights with `(because of: …)` citations), re-inserted on the same substrate so
  a long-lived NPC forms durable beliefs that colour its dialogue. A `Reflector` orchestrator
  mirrors `Director`; NPCs and the director both reflect. Shipped with **real-time server
  logging** (`loom/log.py`: `event`/`debug` under `LOOM_VERBOSE`, flushed). Spike:
  `docs/spikes/reflection.md`.
- **Durable player identity + accretion (`635ce82`, both gates green)** — the DikuMUD line:
  the player's **name is the key**, identity == character, one durable `PlayerRecord` per name
  folded into the JSON overlay. An opt-in `require_login` flag (base engine unchanged — the
  anonymous Wanderer path all prior tests use is default): connect → name prompt (not auto-mint);
  a known name resumes its location, inventory, and quests; disconnect **persists-and-detaches**
  (never delete-and-drop); a duplicate live name is taken over newest-wins. **Accretion is nearly
  free**: NPCs already record the speaker by name (`mind.py:325`), so a durable name makes every
  existing memory line meaningful across sessions, surfaced by relevance retrieval — the world
  remembers you (reconnect seeds present NPCs with "<name> has returned"). No player memory stream
  (deferred — a recap feature). `loom/identity.py`. Spike: `docs/spikes/identity.md`.
- **Idle-NPC autonomy (uncommitted, both gates green)** — a character that moves **first**, the
  last headline Phase-5 thread and the NPC-side mirror of the director's lull. A slow, opt-in
  `Idler` (the `Reflector` skeleton) lets one NPC in a quiet, player-occupied room *stir* of its
  own accord — act or speak unbidden from its own goal (`NpcMind.stir`, grounded by retrieving
  against its purpose so reflections surface), most often choosing silence. Peer to the director:
  both read "room quiet" off the shared chronicle, so a director beat and an idle stir suppress
  each other. A roamer (an NPC authored `wanders` — Wren) may walk an exit; an anchored one (Odd)
  is held by a hard rail. `loom/ai/idle.py`. Spike: `docs/spikes/idle-npc.md`.
Live: `memory.paraphrase-relevance` 3/3, `reflection.distills-a-belief` 3/3,
`identity.remembers-a-returning-player` 3/3 on bge-m3 + qwen3.6, `idle.stirs-unbidden` 3/8 +
`idle.reticent-stays-still` 6/8 (the engagement/restraint pair), plus live restart proofs.
**Remaining in Phase 5 (polish only — the headline threads are done):** reflection polish
(persist the watermark, fairer scheduling, depth>1, LLM importance); identity follow-ups
(password auth, a player recap stream); idle-NPC depth (multi-room pathing toward a goal, a real
plan object) — see the Phase 5 entry.

**Play-loop polish (agreed direction 2026-07-21 — before Phase 7 authoring, then Phase 6
transport):** the papercuts that hit live play, tracked in `docs/BACKLOG.md` (B11+). First
landed: **B11 — compound & chained player commands** (`take lantern and key`, `look at Wren and
say …`, and bare `all`) via a deterministic verb-led splitter in `command.parse_line` +
in-order per-command dispatch; offline **582**, live `command.*` 4/4 + a live compound E2E.
Spike: `docs/spikes/commands.md`. More papercuts (B12+) as they surface in play.

## Status snapshot — line drawn 2026-07-14

Committed through the vLLM backend (`98b2341`) + its README (`5ad200a`), with the
Phase 3 *reach* (spawn_item + the quest subsystem, `eeee5ca`) beneath; the **B2**
fused rendering and the **B3** rich-text foundation land in this commit.
**Phases 0–2 and the Phase 2 hardening are complete; the Phase 3 game-master director
is done** — it shapes the world (standing conditions, and now *spawns things* and
*offers quests*), the characters react on their own (the reaction path), the world
turns on its own clock even when no one is present, the director stirs even a *quiet*
room on a lull, foreshadows the rooms ahead, and judges whether the moment wants a
beat before paying for one (the act-gate). In one sentence: NPCs with persona + memory
converse and *act* through a grammar-hardened action seam; players issue rich,
phrasing-tolerant commands through that *same* seam; an unseen director shapes ambient
scene and now real events — a thing appears, a quest pulls onward — on a slow,
restrained, self-judging cadence; and an autonomous world-clock turns time itself while
the weather wanders — all on local GPU inference, all editable as world data. NPC
speech and deeds now render as **one styled beat** to the room, and the same engine
runs on local GPUs (vLLM/Ollama) or hosted (OpenRouter) on one env switch. **404
offline tests + 29 live behavioral scenarios, both gates green.**

The clean milestone: the director is *complete* on the seam — restrained, self-judging,
grounded, persona-as-data, and now reaching from atmosphere into events (a thing
appears; a goal pulls a wanderer onward). Open threads from here (detailed in the phase
notes and `docs/BACKLOG.md`):
- **Director reach** — the director shapes the *world*, not minds. **DONE
  (2026-07-14).** Standing environmental conditions landed 2026-07-12
  (`set_condition` / `clear_condition`); **`spawn_item`** (a real, persistent,
  perceivable object dropped into a room) and **`offer_quest`** (a real per-player
  quest subsystem — `loom/quest.py`: a `QuestBook` log, a player `quests`/`journal`
  command, and deterministic *reach*-completion bound to the player-arrival seam
  event) landed 2026-07-14. NPCs stay autonomous and react on their own; `nudge_npc`
  (writing a goal/memory into a character) stays a framework-optional action,
  **unregistered and disabled for our game** (see the Phase 3 design decision).
- **Director judgment** — the model-side two-pass "should I act?" **landed
  (2026-07-13, B8)**: a cheap low-temp wait/act gate before the compose. Its
  authoritative-action variant **also fixed the NPC `move` ceiling (B5)** — a willing
  guide now reliably *moves* when asked (8/8 gated vs ~70% blended).
- **Autonomy** — NPC side **landed (2026-07-12): NPCs react to the world and to
  each other of their own volition** — a bounded cascade whose limiter is
  engine-enforced *appropriateness*, not a depth cap (the reaction path). Director /
  world side **landed too (2026-07-12): a world-clock** turns time-of-day on its own,
  a **director lull trigger** stirs a *quiet* room with a gentle beat,
  **off-screen staging** lets the director foreshadow into the empty rooms just ahead
  of the players, and a **weather** system wanders the sky over its own random walk —
  so a still world stirs with no player present, the characters feel it, and the way
  ahead is shaped before they arrive. **B9 is closed (2026-07-12): the world and the
  director are autonomous, and the characters feel both.** The one remaining thread —
  *purely unprompted* NPC initiative (idle-NPC autonomy) — was decided into **Phase 5**
  (its quality wants the mind depth that lives there). Also open: the `loom-gm`
  wide-context variant, wired but never exercised (B10).
- **Presentation** — fused speech+action rendering **(B2 — done 2026-07-17)** and
  rich text **(B3 — done 2026-07-19: route-(b) semantic styling across every
  player-facing surface, incl. the world's own `ambient` voice)**.
- **Later phases** — loot forge (4 — first slice done), deeper memory + persistence
  (5 — **underway**: persistence + memory importance/embeddings/SQLite landed
  2026-07-20; world, NPC/director memory, and the chronicle now survive a restart),
  rich transport + multiplayer (6), authoring tools incl. the world atlas (7 / B7).

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
   goes through a schema-validated tool-call. This seam is load-bearing. Its
   strongest form is **grammar-constrained decoding** — preventing a malformed
   envelope at the token level rather than correcting one after the fact (see
   *Phase 2 hardening*); validate-and-retry remains behind it as defense-in-depth.
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

### Phase 1 — Local minds (Ollama) + provider portability  ✓ (2026-07-09; only the optional plain-text mode deferred)
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

### Phase 2 — The action seam  ✓ seam complete (2026-07-12: emote · move · give/take/drop · stage_event; hardened by constrained decoding)
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
- `loom/salience.py` — a swappable **salience gate** (B4): the default filters
  on *directed address* (name a present NPC → only they engage), run before any
  thinking beat or LLM call so bystanders cost nothing. Silence is now a
  first-class turn outcome (`Turn.is_silent`; the engine renders an empty turn as
  nothing). And the NPC's *own* choice to stay silent was made real (not just
  permitted) with three prompt levers in `mind.py`: a silent few-shot example,
  ambient "overheard" framing for unaddressed lines (`converse(addressed=…)`), and
  a persona `disposition` field. Live: reticent Odd stays silent on ~3/5 idle
  remarks and speaks only when a topic touches him; gregarious Wren answers 5/5.
  A game or the Phase 3 director can still install a smarter gate behind the seam.
- **The inventory keel + `give_item`** (2026-07-12) — brought forward from "near
  Phase 4" because it is the shared prerequisite for B1 (player commands) *and*
  the loot forge. Built game-agnostic in `loom/`:
  - `world/entity.py` gains `Item` (a sibling of `Character`, located by a single
    `holder` — a location = floor, a character = inventory, later an item =
    container). `World` keeps a reverse index (`_contents`) mirroring `holder`,
    exactly as `Location.occupants` mirrors `Character.location_id`. One mutator,
    `place_item`, backs take/drop/give (each is just a change of holder);
    `contents()` and `scope()` (room occupants + floor items + own inventory) are
    the query surface. Items load from a `"items":[…]` array in `world.json`.
  - `loom/naming.py` — `resolve(phrase, scope)` turns a noun phrase into the entity
    meant, returning `Resolved` / `Ambiguous` / `NoMatch` (exact ▸ whole-word ▸
    prefix, honest about ambiguity). The IF/MUD scope-resolution model in
    miniature; the generalisation of `salience.is_addressed`. Prior-art survey
    recorded on B1.
  - `give_item` — the first *multi-object* action: the schema guarantees two
    strings; the handler resolves the item against what the actor holds and the
    recipient against who is present, refusing (dropped `ActionError`) when either
    is unknown or ambiguous. Shared by NPCs and the player path.
  - Perception: `Scene` gains `items` (on the ground) and `inventory` (what the
    NPC carries) — the latter added after the behavioral harness caught an NPC
    denying it held an item it was carrying. NPCs still never read `World`.
  - Player payoff: `take` / `drop` / `inventory` and `give <item> to <who>`; give
    routes through the *same* registry the NPCs use — the player-side mirror of
    the seam in miniature (a trivial parser now; the rich grammar is B1).
- **The player command parser (B1a)** (2026-07-12) — `loom/command.py`, a
  game-agnostic, world-free *syntactic* parser (verb table with synonyms and
  multi-word verbs, `verb + DO + prep + IO` grammar, symbolic per-slot scopes the
  engine maps to real candidates). Flexible player text now resolves against
  scope (disambiguation inherited from `naming.resolve`) and world-changing verbs
  run through the *same* registry the NPCs use. `take`/`drop` were promoted into
  registry actions (`take_item`, `drop_item`) so every player world-change is one
  path; a **per-mind offered-action subset** (`NpcMind(offered=…)`, narrowing both
  the prompt catalogue and the constrained grammar) keeps NPCs off the player-only
  verbs, so the NPC catalogue and the harness are unchanged. New reach: `look at
  X`/`examine X`, `take X [from Y]`, articles and phrasing tolerance.
- **The free-text intent fallback (B1b)** (2026-07-12) — when the deterministic
  parser doesn't recognise the verb, the engine hands the free text to the model
  constrained by a *command grammar* (`command.command_schema`, the verb-side
  counterpart of the action registry's `json_schema`) and gets back a canonical
  `{verb, dobj, iobj}` (`loom/ai/intent.py`, world-free). That is rebuilt into the
  *same* `Parse` the deterministic parser produces and flows through the identical
  dispatch — one path. Trigger is an unknown verb only; toggleable
  (`Engine(intent_fallback=…)`); degrades to "unknown command". Live: phrasings the
  table misses ("offer … to Wren" → give, "scoop up …" → take, "head north" → go)
  map correctly. This is the payoff of hardening-before-B1: the fallback reuses the
  constrained-decoding waist so the model *cannot* emit a non-command.
- `tests/` — **404 offline tests** (the above + validation, parse-tolerance incl.
  the live malformations, retry recovery, engine end-to-end emote/move/give/take/
  drop, perception rendering, the salience gate, chosen silence, the resolver, the
  containment model, the constrained-decoding schema emitter + drift cross-check,
  the command parser + per-mind offered subset, the command grammar + intent
  parser, and — Phase 3 — the chronicle, the `stage_event` / `set_condition` /
  `clear_condition` handlers, the conditions registry (per-location and world-scope),
  the `DirectorMind` turn, the `Director` cadence + the lull trigger + off-screen
  staging (adjacent-room snapshot), the autonomous-reaction cascade + its rails, and
  — B9 — the `WorldClock` advancement / boundaries / bridge and the `WeatherSystem`
  bounded random walk, and — B8/B5 — the director's act-gate (decision + activity-only
  scoping, lull left ungated) and the NPC two-pass act-gate (authoritative action,
  blended speech, silence preserved), and — the reach slice — `World.fresh_id` /
  `spawn_item`, the `spawn_item` / `offer_quest` handlers, the `QuestBook` subsystem
  (offer / de-dupe / reach-completion / idempotence), the arrival-completion hook, and
  the `quests` query render, and — **B2/B3** — the styled composition layer
  (`compose_beat` fusing speech + deeds into one beat, the execute-from-render split)
  plus the semantic-styling vocabulary, the polymorphic wire payload, and the client's
  themed rendering + plain degrade). No GPU needed. **Plus a live behavioral harness** — see
  *Testing discipline* below — 29 scenarios green against `qwen3.5:35b-a3b`.
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

The seam itself is complete and hardened; the player and NPC and director paths
all run through it. Further actions are now scheduled into the phase that owns
them, rather than left loose here: **offer_quest** is a *subsystem* (quest state +
completion tracking + a per-player log), not just an action — it belongs to Phase 3
(the director); **remember_fact** belongs to Phase 5 (when memory gains importance
and retrieval). Player-side acting has its rich, phrasing-tolerant deterministic
parser (B1a) and its LLM free-text fallback (B1b) — both landed 2026-07-12.

### Phase 2 hardening — constrained decoding (grammar-guaranteed envelopes)  ✓ (2026-07-12)
Make design commitment #4 *structural*: constrain generation to the turn-envelope
grammar so the model **cannot** emit a malformed `{"speech","actions":[…]}` object
in the first place — the shape is guaranteed at the token level, not validated and
repaired after.

**Landed (2026-07-12), exactly to the design below.** `ActionRegistry.json_schema()`
renders the turn envelope from the same `Param` specs `describe()`/`validate()`
read — `speech:string` + an `actions` array of a `oneOf` over each action (its
`name` a `const`, its typed `args` an object with `additionalProperties:false`).
`LLMProvider.complete()` gained an optional `schema=None` (default = today's
behaviour verbatim); `OpenAICompatibleProvider` forwards it via a `_schema_payload`
hook as `response_format:{type:json_schema, …, strict:true}` — the single place a
backend that wants `guided_json` / native `format` gets translated. `NpcMind`
passes the grammar on both the initial call and the retry when a registry is
present; `FakeProvider` and the offline path ignore it, and the validate → retry →
degrade-to-speech layer stays underneath as defense-in-depth. Verified on GPU:
`qwen3.5:35b-a3b` over Ollama `/v1` compiles the `oneOf`+`strict` grammar and
returns a conformant envelope; the behavioral harness gained an `envelope.well-formed`
scenario (5/5) — **B6 is retired by construction**, and all prior behavior held
(B5 untouched, as predicted — the grammar guarantees shape, not choice). Offline
grew to 129 tests, including a *drift* cross-check (the schema's required args equal
what `validate()` demands, so the forced shape and the checked shape cannot diverge).

The prior-art survey (2026-07-11) confirmed this is the standard
approach and strictly dominates generate-then-validate: Gigax drives local NPC
actions through Outlines constrained decoding, and vLLM (`guided_json` /
`response_format`), llama.cpp (GBNF grammars) and recent Ollama (`format` /
`response_format: json_schema`) all expose it over the same OpenAI-compatible waist
we already speak. This **retires B6** (the tolerant parser's degrade-to-speech
branch could leak a raw broken envelope to the room) by construction, and hardens
every action still to come — give_item, offer_quest, the Phase 3 director and the
Phase 4 loot forge all inherit the guarantee for free. Do it *before* building more
actions, so nothing new is written against the weaker guarantee.

The seam's durable half is unchanged — only *how the model is asked* gets stronger,
exactly as the Phase 2 design anticipated ("native tools can graduate later behind
the same registry"):
- **One source of truth.** `ActionRegistry` grows a schema emitter (a
  `json_schema()` beside the existing `describe()`) that renders the same `Param`
  specs it already validates against into a JSON Schema for the whole turn:
  `speech: str` + an `actions` array whose items are a `oneOf` over the registered
  actions (each action's `name` as a const + its typed `args`). Schema and prompt
  catalogue derive from one place, so they can never drift.
- **Provider gains an optional constraint.** `LLMProvider.complete()` takes an
  optional `response_format`/`schema` (default `None` → today's behaviour verbatim;
  `FakeProvider` ignores it, so the offline test suite stays green with no live
  backend). `OpenAICompatibleProvider` forwards it as
  `response_format: {"type":"json_schema", …}`; per-backend field differences
  (Ollama `format`, vLLM `guided_json`) are translated *inside* the provider — the
  same place the `reasoning_effort` / `num_ctx` quirks already live — with the JSON
  Schema as the portable form.
- **Belt *and* suspenders.** The validate → retry → degrade-to-speech layer stays
  as defense-in-depth for backends without constraint support and for the
  `FakeProvider` / offline path. Constrained decoding is an *additional*, stronger
  guarantee, never a replacement for the seam.
- **Shape, not judgement.** The grammar guarantees a well-formed envelope; it does
  **not** decide *which* action fits or whether to stay silent. The prompt catalogue
  and the silence levers (B4) still do that — so **B5** (the model under-selecting
  `move`) is untouched by this and remains a sampling/prompt lever, not something a
  grammar can fix.

Verified (2026-07-12): schema-emitter + drift unit tests offline (129 green); a live
run on the real Ollama `/v1` path — the constrained turn envelope compiles and
returns conformant, and the `envelope.well-formed` harness scenario (5/5) confirms
a constrained turn cannot reproduce the B6 malformation.

### Phase 3 — Game-master director  ✓ (2026-07-14 — restraint, autonomy, judgment, and reach all landed; only the ops thread B10 remains)
A world-observing "director" agent on a slow cadence (hangs off the game loop),
injecting events/quests and adjusting the world to player behaviour, while
characters stay in-role. Emits only tool-call actions (Phase 2). Runs on its own
model variant with a large baked context (`ops/modelfiles/loom-gm.Modelfile`) —
Ollama's `/v1` ignores per-request `num_ctx`, and a Modelfile `PARAMETER num_ctx`
overrides the global 8192 NPC default upward. Large context ⇒ large KV-cache
VRAM, so budget one card for it (or quantize KV).

Prior art settled before building (the TaleWeave spike, `docs/spikes/`): its "DM"
is a synchronous narrator invoked *inside* a character's turn — no cadence, no
world-observation between turns, quest-gen a stub — so the slow-cadence director
is ours to build. Patterns adopted: the **turn-digest** (a feed of what changed)
and, held for later, the two-phase plan→act split and memory-as-tools.

**Built — the first slice ("the director's first breath", 2026-07-12), both
gates green.** The tightest proof of the director seam, exactly as `emote` was for
NPC actions: prove the whole new structure — a bodiless, slow-cadence,
world-observing actor on the seam — with the single lightest action, then build
the catalogue out on it.
- **The director is a bodiless actor on the *same* seam.** No new registry: its
  action registers on the one `ActionRegistry` and is offered to it (and only it)
  via a `DIRECTOR_ONLY_ACTIONS` subset — the exact mirror of `PLAYER_ONLY_ACTIONS`.
  It inherits constrained decoding, validation, and the bounded retry for free;
  NPCs and players are never offered it, so the NPC catalogue (and all 12 prior
  behavioral scenarios) are unchanged. It acts *on* rooms, not from one: the target
  location is an explicit arg, so a bodiless director stub fills `ActionContext`
  and `_perform`'s multi-room `broadcasts` deliver the beat where aimed.
- **`stage_event(location, text)`** — narrate an ambient beat into a room. Pure
  narration, no state change; the handler is the gate (the named location must
  exist, the line non-empty). Its world-mutating siblings (`spawn_item`,
  `offer_quest`) will build on this same location-addressed shape.
- **Perception = a chronicle + a snapshot.** `loom/chronicle.py` — a bounded,
  game-agnostic event feed (arrivals, speech, actions, moves) with a monotonic
  cursor; the engine records salient beats as they broadcast. The director reads
  the chronicle (what *changed* — the turn-digest) plus `engine.world_snapshot()`
  (how things *stand now*, only rooms with someone present, keyed by location id).
- **`DirectorMind`** (`loom/ai/director.py`) mirrors `NpcMind`: persona + memory +
  the seam, one constrained turn where an empty `actions` list means *watch and do
  nothing*; the shared `parse_turn` (factored out of `NpcMind`) validates its turn
  identically. Its "speech" is a private note, never broadcast.
- **`Director`** — the orchestrator on the loop: slow (every `period_ticks`), lazy
  (no model call when nothing has changed or no players are present — a cheapness
  gate like B4's salience gate), non-blocking (each beat on a background task), and
  non-overlapping. A broken beat is logged, never fatal. **Restraint (B8):** a beat
  also needs both `min_new_events` new chronicle events *and* `cooldown_pulses`
  pulses since the last one — so most pulses it spends no call and does nothing,
  capping frequency in code regardless of the model's eagerness. NPC speech is
  chronicled too (not only NPC actions), so it perceives conversation.
  `engine.attach_director(loop, persona, provider, period_ticks, min_new_events,
  cooldown_pulses)` wires it (env: `LOOM_GM_MODEL` for the `loom-gm` variant,
  `LOOM_DIRECTOR_PERIOD` / `_MIN_EVENTS` / `_COOLDOWN`).
- Verified live on GPU (`qwen3.5:35b-a3b`): the director stages a grounded,
  well-formed ambient beat into the room where players are (measured 7/8 and 6/6
  into the right location, 0 envelope leaks) — new harness scenario
  `director.sets-a-scene` (4/5). And end-to-end through the loop and seam: a player
  utterance → chronicle → a director pulse → a real ambient line delivered to the
  room. **Honest limit:** on this model the director stages a beat on nearly every
  pulse — restraint ("intervene sparingly") is under-weighted, a ceiling like B5,
  held down deterministically in the orchestrator (min-events + cooldown, **BACKLOG
  B8**), the honest first lever; the model-side "should I act?" pass landed later
  (2026-07-13, the act-gate below).
- **The line, drawn here:** the director MVP is complete — bodiless actor on the
  seam, constrained, restrained (B8), grounded, persona authored as world data.
  Of *reach*, *judgment*, and *autonomy*, **all three have now landed**; only the
  ops thread (B10) remains:
  - **Reach** — **DONE (2026-07-14).** The director shapes the *world*, not minds
    (see the design decision below). Standing conditions landed 2026-07-12 (a storm
    rolls in, night falls); **`spawn_item`** and **`offer_quest`** landed 2026-07-14:
    - **`spawn_item(location, name, description, text)`** — the director brings a
      real, persistent, portable `Item` into a room (minted by the new
      `World.spawn_item` / `World.fresh_id`, so `loom/action.py` never imports the
      world model) and announces its appearance. It is perceived and taken through
      the *same* paths every authored item is — nothing spawn-specific downstream.
      Deliberately dumb: naming a concrete thing, not *authoring* balanced lore-rich
      loot (that is Phase 4, the loot forge).
    - **`offer_quest(location, title, summary, destination, text)`** — a real
      subsystem, not a narrated line: **`loom/quest.py`** (a `Quest` + `QuestBook`,
      a world-model primitive on the same footing as `conditions`) holds a
      **per-player log**; a new player **`quests`/`journal`** command reads it; and
      completion is a **deterministic engine check bound to a seam event** — reaching
      the objective place — never player-claimed nor model-adjudicated, the shape the
      MUD/IF prior art (CircleMUD mob-procs, LPMud solved-flags, Evennia/WoW logs,
      Inform scenes) settled on. The offer is location-addressed like every director
      action (it reaches the *players* present, via `World.players_in`) and writes a
      goal into a **player's** log, never a character's mind.
    - `nudge_npc` (writing a goal/memory into a character) stays a framework-optional
      action, **unregistered and disabled for this game** — our director speaks to
      the world; our characters answer for themselves.
    - Both gates green: offline 332→357 (the quest subsystem, both handlers,
      `fresh_id`/`spawn_item`, the arrival-completion hook, the `quests` render); live
      27→29 (`director.spawns-item`, `director.offers-quest`), every neighbour held —
      the enlarged director catalogue collapsed no prior selection. Verified live E2E
      on GPU: a model-authored spawn dropped a real item on the floor, and a
      model-authored quest completed when the player walked to the destination it
      named (`✓ Quest complete`), idempotently and for multiple matching quests at
      once. *Honest limit:* offered its full catalogue, the model still tends to emit
      more than one action in a single beat (pre-existing — the turn envelope always
      allowed an array; the "one beat at a time" is a prompt request the grammar does
      not enforce). Harmless (each action validates independently) but a candidate
      for a future one-beat cap.
  - **Judgment** — **DONE 2026-07-13** (B8, `DirectorMind.decide`, `act_gate`): a
    cheap, low-temp, constrained wait/act pass before the compose, layered after the
    deterministic ceiling/floor and scoped to the activity path (the lull stays a
    deterministic floor). Proven live to restrain the over-staging model without
    collapsing it (bare 8/8 wait, evocative 8/8 act), guarded by a tension pair
    (`director.restraint` + `director.gate-acts-on-cue`); game default on. The same
    machinery, made *authoritative* over the action, also cleared the NPC `move`
    ceiling (**B5**, 2026-07-13).
  - **Autonomy** — **DONE (2026-07-12 / -13, B9).** Director side: a world-clock
    turns time-of-day, weather wanders, and a lull trigger stirs a still room, all on
    the loop with no player present; off-screen staging lets it shape empty rooms
    just ahead. NPC side: NPCs react to the world and to each other of their own
    volition (the bounded reaction cascade). Still open — **B10**: run the director on
    the wired-but-unexercised `loom-gm` wide-context variant and verify it live.

**Design decision (2026-07-12): the director shapes the *world*, not minds.** NPCs
are autonomous agents — they perceive what happens around them and react of their
own volition; the director never puppets them. So the director's reach is
*environmental*: it changes the world (a storm rolls in, a thing appears, night
falls), and NPCs — and players — respond to that change independently. The
mind-writing action (`nudge_npc`: set a goal/memory on a character) is still worth
having in the **framework** as an optional capability another game may want — the
per-mind offered-subset already makes it opt-in — but it is **disabled for this
game**. Our director speaks to the world; our characters answer for themselves.
The counterpart work is NPC *autonomy*: minds that react to world/ambient events
unprompted, not only to a player's `say` (overlaps B9 and Phase 5).

**Built — the world-shaping slice: standing conditions (2026-07-12), both gates
green.** The first realisation of the design decision — the director *changing the
world*, where `stage_event` only narrated. Prior art settled it first (CircleMUD /
LPMud / Evennia / Inform 7): a persistent condition must be held *apart* from its
one-shot announcement and *re-resolved into the room at look-time*, or (Diku's
mistake) it announces once and a later look shows it gone.
- **A new world-model primitive — the conditions registry** (`loom/world/conditions.py`):
  a `Conditions` map keyed by *location id*, each entry a `Condition(tag, text)`.
  Deliberately world-level, not a field on `Location` — so region-wide weather and
  a world-clock extend the *same* shape later (a condition keyed by a region id
  every room folds in) without touching the room model. `tag` de-dupes (one storm,
  not a pile) and is the handle to clear by; `text` is the perceivable fragment.
- **Two director-only actions on the one seam** — `set_condition(location, tag,
  text)` raises a standing condition; `clear_condition(location, tag, text)` lifts
  it. Both join `DIRECTOR_ONLY_ACTIONS`, so they inherit constrained decoding,
  validation, and the bounded retry for free, and NPCs/players are never offered
  them. The handler does the split the prior art demands: it *stores* the condition
  (the standing change) **and** *announces* it once to the room (the one-shot beat,
  which is also what lands in the chronicle). Validation precedes mutation, so a
  rejected clear never half-lifts a condition.
- **It colors every perception.** A standing condition surfaces in the player's
  `look` (appended after the room description at look-time), in each NPC's `Scene`
  (`Scene.conditions` — so a mind can answer or act in light of the weather), and
  in the director's `world_snapshot` *with its tag* (the handle it needs to clear).
- **Gates.** Offline grew to **257** (the registry; both action handlers incl.
  upsert / coexist / clear-order; the perception plumbing in `look` / `Scene` /
  snapshot; and an end-to-end integration test driving `set_condition` through the
  real `_perform` → registry → world path and proving it persists across a later
  look). Live: a new `director.sets-a-condition` scenario — given a sky that is
  turning, the director raises a grounded, well-formed standing condition — **5/5**
  on `qwen3.5:35b-a3b`, with every prior scenario unregressed (**14/14**).
- **Still open here:** `clear` today is the director's explicit choice; automatic
  expiry / a world-clock is B9. And this is the *world* half of the pair — the NPC
  *reaction* half (minds feeling the weather of their own volition) is slice 2.

**Built — the reaction path: NPC autonomy (2026-07-12), both gates green.** The
counterpart half — the director makes the weather, and now the characters *feel*
it, and each other. Prior art settled the shape first (Diku MobProgs / Evennia
hooks / Stanford Generative Agents): a cheap gate before the model, and — the
crux — a bounded, *not* depth-capped cascade.
- **Design directive (user): cascade is the feature; the limiter is engine-enforced
  *appropriateness*, not a count.** NPCs react to each other freely; a hard
  one-level cap is artificial and was rejected. The engine strictly frames the ask
  (a high "only if this genuinely concerns you" bar in `NpcMind.react`, distinct
  from answering a direct address), and the mind's own chosen silence is the gate —
  an NPC that would only react *to react* stays quiet. A cascade ends when
  appropriateness runs out, never at a fixed depth.
- **The re-entrant core** (`engine._react_to_event`): an event in a room offers the
  NPCs present (except its source — the self-guard) a chance to react; a reaction
  is *itself* an event the others may answer, so characters play off each other.
  Bounded only by cheap, non-artificial rails: a **shared decaying budget** per
  originating event, a **per-NPC cooldown** (real back-and-forth, no machine-gun),
  and a **high fuse** as a pure runaway circuit-breaker. Deterministic and fully
  offline-tested (self-guard, budget, cooldown, fuse, the cascade itself).
- **Two triggers:** a director world-change (a condition raised or lifted, a staged
  beat) → the room reacts to the weather; and an NPC's own reply → the others react
  to it (the player's `say` first-hop is untouched, so the 8 prior `_say` scenarios
  hold). The reacting NPC's `Scene` already carries slice-1's conditions, so
  "react to the storm" needs nothing new.
- **A framework capability, off by default; the game opts in.** `Engine(...,
  autonomous_reactions=True)` — set in `game/main.py`. So every prior test and
  scenario, constructed with the default, keeps its exact behaviour: enabling this
  regresses nothing. NPCs are offered the capability, never forced — they are not
  puppets.
- **Gates.** Offline **267** (the cascade core + every rail + both triggers +
  the disabled-path, all with scripted minds). Live: three new scenarios probing
  `NpcMind.react` directly — `npc.reacts-to-world` (an NPC answers a storm) and
  `npc.reacts-to-npc` (answers another's deed) and, the restraint pole,
  `npc.ignores-trivial` (a reticent NPC lets a trivial flicker pass, 5/5). All 17
  behavioral scenarios green; every prior one held. **Honest reading:** NPC→NPC
  reaction is a genuine ~50% coin-flip on this model (an NPC legitimately may or
  may not answer another's remark — the "own volition" point), so its threshold
  catches a *collapse* of the react path, not the rate. Verified E2E on GPU: the
  director raised a squall and Wren answered with word + gesture while reticent Odd
  answered with a *deed* (shifting deeper into the cave) — two independent,
  in-character reactions, neither puppeted, the cascade settling without runaway.
- **Still open:** the *autonomous* trigger (a world-clock / lull so a still room
  stirs with no player present — B9's other half); and, for scale, a cheap
  deterministic salience *pre-gate* before the model in a crowded room (deferred —
  unnecessary at 2–3 NPCs).

**Built — the autonomous trigger: the world-clock (2026-07-12), both gates green.**
The last gap in the pair. The reaction path and the director were both
*player-driven* — they fire on a beat someone caused, so a *still* room (no one
typing, no director pulse) produced nothing and the world went inert the moment you
stopped. The clock is the missing autonomous **event source**. Prior art settled the
shape first (DikuMUD's `weather.c` weather-daemon, LPMud `heart_beat`, Evennia's
`TickerHandler` / Gametime, Inform's turn-clocked "every turn" as the *counter*-
example): a real-time heartbeat **decoupled from player action**, advancing an
explicit game-clock, changing **perceivable, persistent** world-state, narrated
**deterministically**, broadcast **only where seen**.
- **`loom/clock.py` — the `WorldClock`** (a new game-agnostic primitive): a loop
  system mirroring `Director`'s shape (`install(loop)` + a `tick(dt)` callback) that
  advances an abstract minute-of-day by `dt * factor` game-minutes each pulse,
  *decoupled from any player*. On crossing into a new `Phase` (a table the game
  supplies as data) it lands that phase's standing condition and one ambient beat.
  Tick-driven, not wall-clock — deterministic and testable by injected pulses. It
  knows nothing about "dusk"; the phase names, boundaries, text, and speed are all
  content (the `"clock"` block in `world.json`).
- **It reuses both slices at near-zero new seam.** A phase turning is a *world
  change*, so it flows through the machinery already built: `engine.apply_time_of_day`
  upserts a **world-scope** condition (the one small new world-model piece — the
  region/world generalisation the conditions registry was designed for, folded into
  every place's `look` / `Scene` / snapshot), then in each *occupied* room
  (perceivable-only) broadcasts the beat, records it to the chronicle (so the
  director may build on it under B8 restraint), and `_spawn_reaction(…, "world", …)`
  — so the characters answer the world's own turn of their own volition, through the
  *same* reaction path. The beat text is **deterministic** (no model call); the life
  is the minds reacting. This *feeds* B8 restraint (a real new event) rather than
  fighting it — the reason to build the clock before a director *lull* trigger.
- **Off by default; the game opts in.** `engine.attach_clock(loop, phases, …)`
  (`LOOM_CLOCK_FACTOR` overrides the authored speed); the base engine has no clock,
  exactly as it has no director. So every prior test/scenario is unregressed.
- **Gates.** Offline **290** (from 267): `tests/test_clock.py` — advancement,
  boundary detection incl. the midnight wrap and a skipped-phase pulse, the silent
  initial set, the deterministic condition turn, the perceivable-only ambient beat,
  the reaction trigger + the disabled path, and the world-scope perception fold-in;
  plus world-scope registry tests in `tests/test_conditions.py`. Live: a new
  `npc.reacts-to-nightfall` scenario — an NPC reacts of its own volition to the
  world's own turn — **18/18**, every prior scenario held. *Honest reading:*
  nightfall is a deliberately *mild* stimulus (~28% for the wayfinder, to whom it is
  routine, vs ~85% for a storm), so its threshold is a pure **collapse-detector**
  (fails only if the world-scope beat stops reaching a mind at all), the rate
  characterised not enforced. Verified live E2E on GPU: an idle player did nothing;
  the clock turned day→dusk on its own, the standing condition and beat landed, and
  reticent Odd — who ignores most things — felt it and answered with a *deed*
  (shrinking into the shadows, watching the light fail). The world stirred with no
  one touching it, and a character felt it, unpuppeted.
**Built — the director lull trigger (2026-07-12), both gates green.** The clock made
the *world* autonomous; this makes the *director* autonomous — it now stirs a *quiet*
room, not only reacts to activity. Prior art surveyed first (Left 4 Dead's AI-director
pacing state machine; drama-management's intervene-cost-vs-value; the tabletop DM's
"pause"): a lull is a pacing state, and the quiet-time beat should be *gentle*.
- **The mechanism, on the existing `Director`.** `_should_beat` became `_beat_reason`
  → `"activity"` | `"lull"` | `None`. The activity path is unchanged (≥`min_new_events`
  new events + `cooldown_pulses`); the **lull path** fires when the scene has stayed
  quiet for `lull_pulses` pulses since the last beat (≫ the cooldown) — a liveliness
  *floor* beside the activity *ceiling* (B8). A lull beat runs the same compose path
  but `observe(lull=True)` swaps the nudge for a gentler one ("keep it gentle — a
  sound, a shift of light, a small sign — never a dramatic intrusion"). It still needs
  an audience and the cooldown's breathing room.
- **Opt-in, so B8 restraint is preserved exactly.** `lull_pulses=0` (the base default)
  = off → the "quiet world never beats" guarantee is untouched; the game opts in via
  `attach_director(..., lull_pulses=…)` / `LOOM_DIRECTOR_LULL` (default 4). This is the
  deliberate, deterministic B9 counter-force to B8 — the two *balance* (ceiling on
  activity, floor in its absence) rather than fight; the model-side "should I act?"
  act-gate (B8/B5) remains the open, separate lever that would make the lull
  *judgment-based* rather than timer-based.
- **Gates.** Offline **295** (from 290): `DirectorLullTests` — off-by-default silence,
  the lull firing after its window, the gentler nudge carried to `observe`, the
  activity path still firing first, and the audience requirement. Live: a new
  `director.lull-beat` scenario — **5/5**, all 19 green. Verified live E2E on GPU: an
  idle player, no clock, nothing happening — the director stirred the quiet room with
  gentle sensory beats ("a draft from deep within shifts the dust… a faint scent of
  wet stone"), exactly the low-key touch the nudge asks for.
**Built — off-screen staging (2026-07-12), both gates green.** B9 sub-gap 2: the
director could only see and shape *occupied* rooms, so it could not foreshadow into a
room the player is about to enter. Prior art: DikuMUD adjacent-room echoes + Inform
backdrops — adjacency is the natural scope for "just ahead."
- **The mechanism.** `world_snapshot(include_adjacent=False)` gains an opt-in that
  also lists the *empty* rooms one exit from an occupied one, marked `[ahead, empty]`
  (with their exits, floor, and standing conditions) — the player's likely next step,
  bounded, deduped, never a room that is itself occupied. `Director(foreshadow=…)`
  passes it through, and `observe(foreshadow=True)` adds a prompt line inviting the
  director to shape a place ahead **sparingly**, preferring a *standing condition*
  (which outlasts the walk) over a one-off beat (which reaches no one in an empty
  room). No seam change — `set_condition` / `stage_event` already take any valid
  location; only the director's *view* and *prompt* changed.
- **Opt-in, so the snapshot is byte-identical when off.** `foreshadow=False` (base
  default); the game turns it on via `attach_director(..., foreshadow=…)` /
  `LOOM_DIRECTOR_FORESHADOW`. The non-adjacent "a distant place stirs off-screen"
  desire stays deferred (it needs a hear-from-afar propagation mechanism).
- **Gates.** Offline **303** (from 295): `WorldSnapshotAdjacencyTests` (the ahead
  room shown marked with its detail, hidden by default, never double-listed when
  occupied, carrying its own condition) + `DirectorForeshadowTests` (the flag reaches
  the prompt; off by default; a real `Director.tick` beat sees the room ahead). Live:
  a new `director.foreshadows` scenario — **20/20**, all prior held. *Honest reading:*
  foreshadowing is a *sometimes* touch (~37% — often the director correctly shapes the
  occupied room instead), so its threshold is a **collapse-detector** (fails only if
  the director never reaches the room ahead), the rate characterised not enforced.

**Built — probabilistic weather (2026-07-12), both gates green.** The clock's sibling,
and the last B9 slice. Prior art: DikuMUD's `weather.c` — a **bounded random walk** over
a small chain of sky-states driven by a pressure model, so change reads with continuity,
not as noise.
- **`loom/weather.py` — the `WeatherSystem`** (a new game-agnostic loop primitive,
  mirroring `WorldClock`): a chain of `Weather(name, condition, ambient)` states
  (clear ↔ cloudy ↔ rain ↔ storm); every `period_pulses` pulses it *may* step one
  place (probability `change_chance`), bounded at the ends. Deterministic and testable
  — the walk is driven by an injected `random.Random` (seed it) and rolls are counted
  in loop pulses, not wall-clock.
- **It reuses the clock's bridge.** `apply_time_of_day` was generalised to
  `apply_world_condition(tag, …)`, so the clock (tag `"time"`) and weather (tag
  `"weather"`) both drive the same path — a world-scope condition turn + one
  deterministic ambient beat where seen + a reaction — and *coexist* (a look reads both
  "It is dusk." and "Rain begins to fall."). The `"clear"` state carries an empty
  condition, so clearing weather *lifts* the tag rather than showing a line.
- **Opt-in; the game supplies the sky.** `engine.attach_weather(loop, states, …)`
  reading the `"weather"` block in `world.json`; the base engine has no weather.
- **Gates.** Offline **313** (from 303): `tests/test_weather.py` — holding, the period
  gate, bounded stepping at each end, the deterministic walk applying conditions + beats,
  clear lifting the tag, the silent initial set, and clock/weather coexistence — all via
  a scripted RNG. **No new behavioral scenario:** weather stirs the world exactly as the
  clock does (a deterministic beat → the reaction path), and "an NPC reacts to a storm"
  is already `npc.reacts-to-world`; the new part is the random walk, which is
  deterministic and offline-proven. The full harness (20/20) was re-run regardless, per
  the two-gate discipline.

**The line, drawn under B9 (2026-07-12).** All four autonomy slices are in — the world
turns and weathers on its own, the director stirs a quiet room and foreshadows the way
ahead, and the characters feel all of it through the reaction cascade. The one remaining
thread, **idle-NPC autonomy** (*purely unprompted* NPC initiative — mobact-style), was
**decided into Phase 5**: its mechanism is a cheap NPC-side lull mirror, but its
*quality* wants Phase 5's mind depth (reflection, evolving goals) and the model-side
act-gate, so a naive version now would risk a noisy/mechanical room. Also deferred: the
non-adjacent off-screen stir (needs hear-from-afar propagation).

### Phase 4 — Loot forge  ▶ (first slice landed 2026-07-19: the quest-reward forge)
Dynamic, context-aware item generation: the LLM authors an item's *flavour*
(name/lore/aliases) as flat schema-constrained JSON; its *balance* (a rarity tier +
tags/theme) stays in code and tables. Tied to player history, quests, location, and what
the world is currently doing.

Prior art settled first (the loot-generation spike, `docs/spikes/loot-generation.md`):
every mature loot system — Path of Exile, Diablo, Borderlands, DikuMUD — separates
authored *flavour* from tuned *mechanical data*, and derives an item's name from mechanics
a weighted table rolls, gated by a single level scalar (ilvl / affix-level / item-power).
The LLM literature is the complement: models are strong at situated flavour text and
measured-unreliable at numeric balance and constraint satisfaction. The synthesis
(design signed off with the user 2026-07-19): keep the industry separation, invert one
step — **code rolls the mechanics; the model authors the name/lore conditioned on that
roll and the world state, as one flat, grammar-constrained JSON object.** The model never
emits a number, a tier, or a category — the golden rule, applied to loot.

**What "balance" means with no combat system.** Loom has no stat/combat mechanic, so
nothing consumes a number yet. Rather than invent combat stats, code owns one lightweight
**`tier` ordinal** (rarity/power — common/uncommon/rare) plus a **tags/theme
classification**, rolled from tables gated by the tier and by the place's current
conditions. Zero combat math. The `tier` scalar stands exactly where PoE's ilvl stands,
so when a combat system eventually lands the *same* scalar gates stat-range tables with no
change to the seam.

**Built — the first slice (2026-07-19), both gates green.** The tightest end-to-end proof
of the forge seam, fired from a single trigger (quest completion, because the `QuestBook`
already gives per-player context and a deterministic hook):
- **The forge, three stages.** `loom/loot.py` (the code-owned, deterministic half —
  game-agnostic, offline-testable via a seeded RNG): `LootTables` (authored `tiers` /
  `themes` / `tags`), `roll_brief` (a weighted tier, then tags gated by its rank and
  *filtered toward the moment* — a tag whose `when` matches the place's conditions is
  preferred — one per family group, PoE's mod-group guard, so a brief never contradicts
  itself; higher tier ⇒ more tags), and the flat `flavour_schema` / tolerant
  `parse_flavour` / `fallback_flavour`. The model call is `loom/ai/loot.py`
  (`author_flavour`, world-free, the sibling of `loom/ai/intent.py`): a brief + the flat
  schema → the model authors `{name, description, aliases}` only, constrained so it cannot
  emit a number or a `oneOf` branch. The engine owns the orchestration (`attach_loot` +
  `_forge_reward`): gather context (the completion place's standing conditions theme the
  reward; the quest names why it appears) → roll → author → assemble a real `Item` via the
  existing `World.spawn_item` (extended with the code-owned `tier`/`tags`/`theme` + model
  `aliases`) into the player's own inventory, and tell them what they earned. Runs off the
  loop (a background task, like an NPC reply), so a completed quest never blocks on the
  model call; a failed call degrades to a brief-only item — the reward is always real,
  never a crash.
- **Opt-in and game-agnostic.** The base engine forges nothing; the game wires it via
  `attach_loot` reading the authored `"loot"` block in world.json (like the
  clock/weather/director blocks), on the denser game-master tier. `Item` gains
  `tier`/`tags`/`theme` (default empty → all authored items and every prior test
  unregressed). Not a registered action on the shared catalogue — a separate flavour call
  on a seam event — so it dilutes no NPC/director action selection (the neighbours are
  structurally untouched).
- **A playable path to it — starting quests.** So the forge can actually be *played*
  (not only reached through the stochastic director), the engine gained opt-in **starting
  quests** (`attach_start_quests`, authored as a `"start_quests"` block): every connecting
  player is handed opening goals and told them, and reaching a destination completes one
  through the same arrival hook and forges the reward. The demo world gained a themed
  destination — a **hilltop** with a long-cold signal-fire, up the hill path — so the
  opening quest *"The Old Signal-Fire"* is a real, short journey. Verified live E2E: a
  wanderer walked to the hilltop under a storm at dusk and was rewarded *"a rain-beaded
  clay charm"* — code-rolled `common` / `rain-beaded` / `charm` (weather-resonant), the
  name and lore authored by the model and grounded in the storm and the signal-fire.
- **Gates.** Offline **440** (from 405): `tests/test_loot.py` — the table parse, the roll
  (determinism under seed, tier weighting, rank gating, the family guard, context
  preference), the flat schema (no `oneOf`) + parse/fallback bounds, the extended
  `spawn_item`, the async flavour call (scripted / prose / error), the engine seam (a
  completed quest forges a context-themed, code-classified reward into inventory;
  degrade-to-brief; no-forge; no-completion), and the starting-quest gameplay path
  (offered on connect, a bad destination dropped, reaching one forges the reward). Live: a
  new `loot.forges-in-theme`
  behavioral scenario — the model authors schema-valid, in-theme flavour for a code-rolled
  brief — **6/6** on `qwen3.6-35b-a3b` (e.g. *"the dusk-wreathed storm-shard of ill-omened
  grace"* for a rare storm-at-dusk brief), the mechanics all code-owned.
- **Deferred (grow the tables, not the seam):** combat stats / `(field, number)`
  apply-pairs (no combat to balance against); the other firing paths (a director forge
  action; a discovery seeded in a place); deeper mod-pool tiers + tuned spawn weights;
  unique/set fixed items; identification; a wider item-type/slot vocabulary. All grow the
  content later without moving the seam.

### Phase 5 — Deeper minds & persistence  ▶ (persistence + memory depth + reflection + identity + idle-NPC landed 2026-07-20; polish remains)
Importance scoring, embedding retrieval (SQLite + brute-force cosine), and reflection on
the memory stream; persist world + memories across restarts; player personality/history
accretion on the same substrate as NPCs. **The loose end it closes:** the world, all NPC
*and* director memory, and the chronicle were in-memory only — everything reset on
restart. For a world meant to run *forever*, this is the phase where that stops being true.

**Built — persistence (slice 1, `fe07e0c`, both gates green).** A **versioned JSON
overlay** of the mutable runtime state, composed onto the authored `world.json` reloaded
every boot (the DikuMUD authored-vs-mutable line — the authored file is the definition,
never overwritten; only the delta persists). Snapshot, **not** event-log; the chronicle
rides along *as data* (tail + `seq` cursor), never a rebuild source. `loom/persistence.py`
— `snapshot`/`restore`, crash-safe `save_atomic` (temp → `fsync` → `os.replace`, retained
`.bak` with fallback on load), a version int + pure `_migrate` dispatch + tolerant `.get`
load — plus `state()`/`load_state()` on Conditions/QuestBook/MemoryStream/Chronicle and
`Engine.attach_persistence/save/restore` + a loop autosave. Saves on shutdown + autosave;
`game/main.py` restores before serving. **Player-held items snapshot to the floor**
(drop-on-disconnect semantics — players are session-ephemeral; `pcount` persists so ids
never collide); weather reseeds rather than serialising RNG bit-state. The proof: forge a
reward, restart, it is still on the floor with `tier`/`tags` intact and NPCs remember.
Spike: `docs/spikes/persistence.md`. Offline **+19**.

**Built — memory depth (slices 1a + 1b, `0c7a588` + `b685cae`, both gates green).** The
memory stream lifted past recency-only, so an old-but-salient memory (a promise, a threat)
can outrank recent trivia and a memory *relevant to the moment* surfaces after leaving the
last-k window.
- **1a — retrieval.** The Generative-Agents score verbatim: `retrieve(query,k) = recency
  (0.995 decay) + importance + relevance-cosine`, each min-max normalized, weights 1/1/1.
  **Importance is a cheap deterministic heuristic at write** (kind base + salience cues) —
  *not* an LLM call per memory (the per-memory tax every affordable system avoids). An
  `EmbeddingProvider` seam mirrors `LLMProvider` (`loom/ai/embedding.py`): **OpenRouter
  `/api/v1/embeddings`** (same key as chat, default `baai/bge-m3` 1024-dim, ~1e-7/memory —
  verified live; *not* the vLLM box) + a deterministic `FakeEmbeddingProvider` for offline.
  The one honest caller touch: mind/director pass the current utterance/chronicle into
  `retrieve` (relevance needs a query; `recent()` did not). Spike: `docs/spikes/memory.md`.
- **1b — SQLite backing.** `loom/ai/memory_store.py`: one table by `agent_id`, the embedding
  a float32 BLOB via stdlib `array`, brute-force cosine; a new memory is an incremental
  INSERT (no whole-file rewrite), embeddings persist so a rebooted NPC needn't re-embed its
  history; `MemoryStream.load_state` doubles as the one-time JSON→SQLite migration (import
  into an empty store only). Memory leaves the JSON overlay when a store is present.
- **Gates.** Offline **488** (15 memory + 10 store tests; headline: the *buried-promise*
  proof — a salient on-topic memory surfaces where `recent(8)` cannot). Live: the
  `memory.paraphrase-relevance` gate — a real embedder ranks paraphrase the lexical fake
  misses — **3/3** on bge-m3, folded into `scripts/behavior_probe.py memory`; plus a live
  restart proof (real BLOB embeddings reload, history not re-embedded).

**Built — reflection (slice 2, `27fea59`, both gates green).** The Generative-Agents
reflection step, **depth-1**: on a slow, threshold-gated cadence an agent distills its
accumulated memory into higher-level insights and writes them back as `kind="reflection"`
memories on the same substrate — so a long-lived NPC forms durable beliefs (*"Men's vows are
fleeting wind…"*) that then colour its dialogue through ordinary relevance retrieval. A
reflection is *just another memory*, so no memory-seam change.
- **Cognition** (`loom/ai/reflection.py`, provider-agnostic): two model calls — recent
  memories → up to 3 salient questions; each question `retrieve()`s evidence (the reach back
  past the recent window that makes it more than summarisation); evidence → grounded insights,
  each stored GA-style as `"<insight> (because of: <src>; <src>)"`.
- **Orchestrator** (`Reflector`, mirrors `Director`): slow tick, threshold-gated on
  accumulated memory-importance, one agent per pulse on a background task; **depth-1 by
  construction** (the per-agent watermark advances past the new reflections so a reflection
  never re-triggers itself; in-memory + lazy-init, so a restored backlog is not re-reflected).
  NPCs **and** the director reflect, each with its own provider under one shared rule set.
- **Engine/game:** `attach_reflector` + `narrate_reflection` (a quiet in-client *tell* when an
  NPC reflects; the belief stays private); `NpcMind/DirectorMind.reflection_subject()` seams;
  on by default, tuned for observability (`LOOM_REFLECT*`).
- **Shipped with it: real-time server logging** (`loom/log.py`) — `event()` always-on +
  `debug()` under `LOOM_VERBOSE`, both flushed (`real_time_stdout()` defeats block-buffering).
  The engine/director/loop now log connect/move/say/NPC-turns/world-beats/quests/forge/
  director-beats/reflections as they happen, with the *why* (salience skips, act-gate reasons,
  reflection questions/evidence) behind `LOOM_VERBOSE`.
- **Gates.** Offline **510** (`tests/test_reflection.py`, 22 tests: the cognition, the
  buried-promise → distilled-belief value proof, durable through the store, the trigger +
  depth-1 watermark, citation dedup/bounds, the perceptible tell, graceful degrades). Live:
  `reflection.distills-a-belief` **3/3** — the real NPC model distilled repeated broken
  promises into grounded, in-voice beliefs that surface on a paraphrase query. Spike:
  `docs/spikes/reflection.md`.

**Built — durable player identity + accretion (slice 3, `635ce82`, both gates green).**
The crux persistence deferred — a player who survives a disconnect and a restart, and whose
history accretes on the same memory substrate as the NPCs. The DikuMUD line
(`docs/spikes/identity.md`, from a two-front prior-art survey — MUD identity/login/link-dead
and LLM-agent player memory): **the name is the durable key; identity == character** (no
account object); one `PlayerRecord` per name, folded into the JSON overlay.
- **Opt-in `require_login` flag** (base engine unchanged — the anonymous session-ephemeral
  Wanderer path every prior test uses stays the default; the game opts in). Connect enters an
  explicit **name-prompt gate** (Diku's `CON_GET_NAME`), not an auto-mint: a known name resumes
  its saved location, inventory (items travel *in* the record, not to the floor), and quests
  (`QuestBook` already keys by the now-durable id); a new name creates a record; a name already
  live on another session is **taken over newest-wins** (Evennia mode-0 / Diku USURP). Disconnect
  **persists-and-detaches** — never delete-and-drop.
- **Accretion is nearly free.** NPCs already record the speaker by name (`mind.py:325`); a durable
  name makes every existing memory line meaningful across sessions, surfaced by the relevance
  retrieval that already ships — *the world remembers you*. Reconnect **seeds present NPCs** with
  "<name> has returned" through the autonomous-reaction cascade, so recall emerges (GA-faithful;
  no bespoke welcome-back store). **No player-owned memory stream** (deferred — it serves only a
  recap; the payoff lives in the witnesses' streams, exactly the Generative-Agents model).
- **Framework/persistence:** `loom/identity.py` (slug/display/validate, `PlayerRecord`, item
  (de)serialization); `Engine(require_login=)` + the login/usurp/detach/`sync_player_records`
  lifecycle; a `players` block in the overlay (`SAVE_VERSION` 2, tolerant of a v1 save that lacks
  it); a durable player's held items excluded from the global item snapshot. `password_hash`
  reserved (auth deferred). `game/main.py` opts in (`LOOM_REQUIRE_LOGIN`).
- **Gates.** Offline **535** (`tests/test_identity.py`, 25 tests: the primitives, the login gate +
  bad/reserved/case-insensitive names, persist-and-detach, reconnect restore, newest-wins takeover,
  the overlay round-trip, and the accretion seam). Live:
  `identity.remembers-a-returning-player` **3/3** — a real embedder surfaces the returning player's
  memory above four others' purely by durable name (person-keyed recall). Spike:
  `docs/spikes/identity.md`.

**Built — idle-NPC autonomy (slice 4, uncommitted, both gates green).** The last headline
Phase-5 thread: a character that moves **first**, unprompted, from its own goal — the missing
half of a living world (the quiet room already breathes; the NPCs already *react*; what was
absent was *initiative*). Its quality wanted reflection + the act-gate, both now in
(`docs/spikes/idle-npc.md`, from a prior-art survey — DikuMUD `mobact.c`, Generative-Agents
plans, The Sims' motives, L4D pacing).
- **The `Idler`** (`loom/ai/idle.py`) copies the `Reflector` skeleton — install → `add_system` →
  tick gated on `_ticks`/`_running`, one stir per pulse on a tracked background task. Each pulse
  it reads **per-room quiet off the shared chronicle** (a room's max `seq` per pulse), and stirs
  one NPC in a room that has been quiet `quiet_pulses` pulses, has a **player present** (a
  per-room audience gate, stricter than the director's global one), and holds an NPC off its
  per-NPC cooldown. Lazy first-sight init (never stirs on attach); one stir per pulse, no chorus.
- **`NpcMind.stir`** — a single goal-grounded call (like `react`, not the two-pass gate: idle
  pulses are frequent). It retrieves against the NPC's *purpose* so persona goals and distilled
  reflections surface, frames "nothing prompted you — act unbidden or stay silent; silence is the
  honored default", and returns a `Turn` usually empty. Delivered through `_deliver_turn` and
  then `_spawn_reaction`, so a stir chronicles, records memory when engaged, and **cascades** like
  any turn — nothing downstream can tell an unbidden beat from a reply.
- **Coordination — peer systems, mutual suppression (signed off).** The director owns the
  *environment's* initiative, the Idler a *character's*; both read the same chronicle quiet
  signal, so a director beat resets a room's idle clock (the Idler skips it) and an idle stir
  reads to the director as real activity — at most one silence-break per window, keeping the
  Phase-3 line intact (the director shapes the world; minds move themselves). **Field-tuned
  2026-07-21:** the quiet clock discounts pure atmosphere (`kind="ambient"` clock/weather),
  which was starving the Idler in a world with a running clock; the director's beat records
  `kind="action"` and still suppresses. Ambient cadence eased too (slower clock/weather, director
  lull 4→6). See the spike's "Field tuning" note.
- **Wandering — signed off (also wander).** A stir may take the existing validated `move`, so an
  NPC walks an exit of its own accord (mobact's wander), with departure/arrival narration free via
  `_deliver_turn`. Guarded by a per-NPC **`wanders` flag** (the `SENTINEL` mirror, opt-in default
  off) enforced as a **hard rail** — the Idler strips any move from a non-`wanders` NPC. The game
  authors the policy: Wren the Wayfinder roams, Odd the Hermit anchors his cave. `attach_idler`;
  `game/main.py` opts in (`LOOM_IDLE_NPC`, level with `LOOM_DIRECTOR_LULL`).
- **Gates.** Offline **552** (`tests/test_idle.py`, 17 tests: the quiet clock, the audience gate,
  cadence, one-stir-per-pulse + rotation, non-overlap, director-beat suppression, the `wanders`
  strip rail, wanderer-moves, silence honored, stir→cascade, and the flag's load). Live: the
  engagement/restraint pair — `idle.stirs-unbidden` **3/8** (Wren stirred from her goal and walked
  the path: *"Come along, the view is worth the climb!"* → move north) and
  `idle.reticent-stays-still` **6/8** (Odd mostly still, never wandering) — a discriminating pair,
  not always-on, not always-silent. Spike: `docs/spikes/idle-npc.md`.

**Remaining in Phase 5 (polish only — the headline threads are done; start each with a
prior-art survey + a design proposal for sign-off, the standing rule):**
- **Idle-NPC depth (follow-ups to slice 4):** multi-room pathing (walk *several* rooms toward a
  destination, not one adjacent step per stir); a real per-NPC plan/agenda object (the GA
  hierarchical day-plan we approximate with retrieval); an optional two-pass act-gate on the stir
  if the single-call silence bar proves too loose live; authored clock-hooked routines (Ultima-VII
  schedules) if wanted. All refinements, not gaps.
- **Reflection polish (follow-ups to `27fea59`):** persist the reflection watermark (in-memory
  today, resets on restart); fairer scheduling (one-agent-per-pulse lets the busiest agent
  monopolize); depth>1 (reflections on reflections — the GA tree); LLM importance for the
  trigger. All optional tightening, not blockers.
- **Identity follow-ups (to the slice-3 build):** password authentication (the `password_hash`
  slot is reserved — a `GET_PASSWORD` state insertion, not a migration; deferred as there is no
  adversary in a personal/trusted game); a player-owned memory stream for a "previously, on…"
  recap (near-free on the shared substrate, but the payoff does not need it); an account/character
  split (only if multi-character or account-level bans ever arrive — Evennia's documented path).
- **Also:** LLM importance scoring (a batched off-loop fidelity pass; the heuristic
  suffices now); `remember_fact` (the deferred Phase 2 action — a thin high-importance
  `add`, now that retrieval exists); in-process local embedder + sqlite-vec/ANN only if
  scale ever demands (per-agent brute-force is fine indefinitely).

### Phase 6 — Rich transport & multiplayer  ○
WebSocket transport implementing the same `Handler` contract; emit `map` /
`entities` channels for an ascii/2D client. Harden true multiplayer presence
(players see and hear each other; per-room broadcast).

### Phase 7 — Authoring tools  ◐
AI-assisted world creation and editing over the world schema — descriptions,
regions, NPCs, story — with validation. The GM/creator toolkit.

*Opened 2026-07-21.* Building the **read/validate side first**, then the AI author.
- **B7 — the world atlas (BUILT 2026-07-21, offline-gated, commit pending).**
  `loom/atlas.py` surveys a loaded world into a serialisable `AtlasView` (rooms +
  exits, character sheets, item table, `meta`) and lints it (errors that break the
  world · warnings for design oddities); `render_text` / `render_markdown` / `mermaid`
  present it; `scripts/atlas.py` is the CLI (exits non-zero on error → CI-gateable).
  611 offline green. Spike: `docs/spikes/atlas.md`; status: `docs/BACKLOG.md` B7.
- **Next — the write side:** AI-assisted authoring (a brief → a valid `world.json`)
  with a generate→validate→repair loop, using the atlas's findings as its judge. Its
  own research + spike + sign-off when we reach it.

## Testing discipline — two gates, run BOTH after every implementation phase

There are two layers to every feature, and they need two different gates.

1. **The offline unit/integration suite** — `python -m unittest discover -s tests`
   (552 tests, no GPU, deterministic via `FakeProvider`). Proves the **engine**:
   given a valid action, the world changes correctly. Fast; run constantly.
2. **The live behavioral harness** — `scripts/behavior_probe.py` against the real
   model. Proves the **mind**: does the model *choose* the right action and *use*
   what it perceives. The offline suite structurally **cannot** see this
   (`FakeProvider` is scripted), and real faults live here — a guide that spoke of
   leading but never moved (0/5), an NPC blind to the item at its feet, an NPC
   denying it held what it carried. Each scenario runs N live trials and passes on
   a threshold (behavior is stochastic). One scenario per behavior we have
   verified working; once a behavior is in the harness it is tested **every time**.

**The rule: after each implementation phase — and specifically after ANY change
that touches a prompt, the action catalogue, or perception — run BOTH gates.** Not
only the thing you changed: the action catalogue is a shared, competitive surface,
so adding one action can regress the *selection* of another (adding `give_item`
measurably diluted `move`). Probe the neighbours too. If a verified behavior
regressed, the harness fails and names it; fix it before moving on. When a new
behavior lands and is confirmed live, add a scenario so it is guarded from then on.

Honest limit: this is measurement, not proof. Behavior is a distribution — some
actions have a real ceiling on the current local model (move-when-asked is ~70%
single-pass, *blended*, on `qwen3.5:35b-a3b`; the two-pass act-gate clears it to 8/8
by deciding the deed on its own — see B5). Thresholds are set to catch a *collapse*
or a *regression*, not to force the model past its ceiling. Label claims as
*mechanical* (guaranteed by the offline suite) or *behavioral* (characterised, with
a rate) — never conflate them.

**Re-baselined 2026-07-19 to qwen3.6** (was `qwen3.5:35b-a3b`/Ollama): the harness now
distinguishes hard **gates** from **watch items** (`gated=False` — measured but not
failing the run), for behaviours that genuinely degraded on the current models and
are known-limited rather than broken. A cross-model sweep (35b-a3b · 27b · 9b · 14b ·
two 30b-a3b MoEs) characterised where behaviours hold vs break; **the core NPC loop
holds down to 9B.** The behaviour we want holds across the range with ONE comprehensive prompt rule set,
not per-model profiles (that idea was weighed and rejected): the free-text command
fallback (a flat-enum schema over a collapse-prone `oneOf`), the NPC's strong-stimulus
reaction (a magnitude rule), and the director's world-shaping reach (an observe
de-hedge — the act-gate already owns restraint) were each fixed with a single rule
proven on more than one model, then re-gated in the harness. **`docs/PROMPTING.md`** is
now the game-creator's guide to writing NPC and game-master personas; the engineering
detail behind these fixes lives in the harness scenario comments and project memory. Note
OpenRouter intermittently throttles bursts, so harness runs against it are measurement-
noisy; `OpenRouterProvider` retries the throttle and paces adaptively (idle in play).

## Cross-cutting (ongoing)
Tests alongside each phase (BOTH gates — see *Testing discipline*) · schema/
versioning for save data · cost & latency budgets for AI calls · keeping `loom/`
free of game-specific content.

Unscheduled improvements noticed during review live in `docs/BACKLOG.md`. Open as
of 2026-07-20: world atlas (B7) · exercising the `loom-gm` variant (B10) · plus the
Phase 5 polish (idle-NPC depth · reflection polish · identity follow-ups) — the headline
threads (persistence · memory depth · reflection · identity · idle-NPC) are all landed.
Done and folded in: B2 (fused rendering) · B3 (rich text) · B1 (command
grammar), B4 (choice-to-react), **B5 (NPC `move` ceiling — the two-pass act-gate,
2026-07-13: authoritative-action decision, 8/8 gated vs ~70% blended, no regressions,
game default on)**, B6 (envelope leak, retired by constrained decoding), **B8
(director restraint — the model-side act-gate, 2026-07-13: proven live to restrain
without collapsing, game default on)**, **B9 (director/world autonomy — world-clock,
director lull, off-screen staging, weather; idle-NPC autonomy moved to Phase 5)**.
**Phase 3 closed 2026-07-14** with the director's *reach* (`spawn_item` + the
`offer_quest` quest subsystem); only B10 (an ops/measurement thread) remains on it.
Promote an item into a phase with a real design when the moment is right.

## How to run (current)
With the venv active (`source .venv/bin/activate`), `PYTHONPATH` is not needed:
```
Server:     python game/main.py
Client:     python client/terminal.py
Unit tests: python -m unittest discover -s tests    (offline, no GPU — the engine gate)
Behavioral: LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b \
              python scripts/behavior_probe.py       (live — the mind gate; add a tag/name to filter)
Smoke test: python scripts/smoke.py      (server must be running)
Wire demo:  python scripts/wire_demo.py  (self-contained)
Provider ping: python scripts/try_provider.py   (shows speech + validated actions)
```

### Choosing the AI provider (env)
```
LOOM_PROVIDER=fake                                   # deterministic, offline (default)
LOOM_PROVIDER=vllm  LOOM_VLLM_MODEL=qwen-local       # local inference via vLLM
   LOOM_VLLM_HOST=http://localhost:8000              # (default)
   LOOM_VLLM_API_KEY=...                             # (optional; only if vLLM was started with --api-key)
LOOM_PROVIDER=openrouter                             # hosted inference via OpenRouter
   OPENROUTER_API_KEY=sk-or-...                      # required (auto-loaded from a repo-root .env)
   LOOM_OPENROUTER_MODEL=qwen/qwen3.6-35b-a3b        # NPC model (default)
   LOOM_GM_MODEL=qwen/qwen3.6-27b                    # director model (default on OpenRouter)
LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b   # local inference via Ollama
   LOOM_OLLAMA_HOST=http://localhost:11434           # (default)
LOOM_PROVIDER=anthropic ANTHROPIC_API_KEY=...        # Claude
```
Same envs apply to `game/main.py` and `scripts/try_provider.py`. All three model
backends are the OpenAI-compatible waist the design always anticipated —
`VLLMProvider` and `OpenRouterProvider` are siblings of `OllamaProvider`, differing
only in `base_url`, auth, and the thinking-suppression quirk (Qwen3.x reasons into
`content` unless told not to; the local backends send `reasoning_effort: "none"`,
OpenRouter uses its portable `reasoning: {enabled: false}` lever). All honor the
standard `response_format` grammar the action seam emits, so constrained decoding
carries over unchanged. **OpenRouter** (hosted) reaches models the local GPUs cannot
hold and needs no running server — the two-tier default is NPCs on
`qwen/qwen3.6-35b-a3b`, the game-master director on the denser `qwen/qwen3.6-27b`
(`_director_provider` sets that even with `LOOM_GM_MODEL` unset). The key is read from
a git-ignored `.env` at the repo root by `python-dotenv` (the `game` extra) in `game/main.py`.
Note: a vLLM shared with another workload can be slow to first token; the engine runs
replies off the loop, so latency never stalls the world.
