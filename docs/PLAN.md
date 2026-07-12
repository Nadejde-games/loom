# Project plan — Loom engine & the forever game

Living document. The reference for where we are and where we're going.
Last updated: 2026-07-12.

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

### Phase 2 — The action seam  ▶ in progress (emote + move 2026-07-10; inventory keel + give_item 2026-07-12)
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
- `tests/` — **191 offline tests** (the above + validation, parse-tolerance incl.
  the live malformations, retry recovery, engine end-to-end emote/move/give/take/
  drop, perception rendering, the salience gate, chosen silence, the resolver, the
  containment model, the constrained-decoding schema emitter + drift cross-check,
  the command parser + per-mind offered subset, and the command grammar + intent
  parser). No GPU needed. **Plus a live behavioral harness** — see *Testing
  discipline* below — 12 scenarios green against `qwen3.5:35b-a3b`.
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
capability where needed): **offer_quest**, **remember_fact** (`give_item`,
`take_item`, `drop_item` and the item/inventory world-model landed 2026-07-12).
Player-side acting now has its rich, phrasing-tolerant deterministic parser
(B1a) and its LLM free-text fallback (B1b) — both landed 2026-07-12.

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

## Testing discipline — two gates, run BOTH after every implementation phase

There are two layers to every feature, and they need two different gates.

1. **The offline unit/integration suite** — `python -m unittest discover -s tests`
   (191 tests, no GPU, deterministic via `FakeProvider`). Proves the **engine**:
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
single-pass on `qwen3.5:35b-a3b`; see B5). Thresholds are set to catch a *collapse*
or a *regression*, not to force the model past its ceiling. Label claims as
*mechanical* (guaranteed by the offline suite) or *behavioral* (characterised, with
a rate) — never conflate them.

## Cross-cutting (ongoing)
Tests alongside each phase (BOTH gates — see *Testing discipline*) · schema/
versioning for save data · cost & latency budgets for AI calls · keeping `loom/`
free of game-specific content.

Unscheduled improvements noticed during review live in `docs/BACKLOG.md`
(richer command grammar · fused speech+action lines · rich text formatting ·
NPC choice-to-react). Promote them into a phase with a real design when the
moment is right.

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
LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b   # local inference
   LOOM_OLLAMA_HOST=http://localhost:11434           # (default)
LOOM_PROVIDER=anthropic ANTHROPIC_API_KEY=...        # Claude
```
Same envs apply to `game/main.py` and `scripts/try_provider.py`.
