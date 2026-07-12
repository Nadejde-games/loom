# Code walkthrough — reading Loom from the ground up

A guided tour of the codebase, in the order that makes it click. Open each file
as you go (paths are clickable in most editors) and read the few lines called
out. It takes ~30 minutes end to end. When you finish you'll be able to trace a
single keystroke from the player's terminal all the way to an NPC's action and
back.

> Companion docs: `docs/PLAN.md` is *where we're going*; this is *how what
> exists today fits together*.

---

## 0. The one-paragraph mental model

Loom is a **layered, event-driven server**. A thin **transport** moves tagged
JSON lines over a socket. A **world model** holds rooms and characters as plain
data. An **engine** turns player input into world queries and mutations. An **AI
layer** gives an NPC a mind that can *speak and propose actions* — and a
**validation seam** makes sure the engine only ever executes actions that fit a
schema, never raw model text. Everything the model does is a proposal; the
engine is the authority.

```
      ┌────────────┐  newline-delimited JSON: {"c":channel,"d":data}   ┌────────────┐
      │  terminal  │ ───────────────── TCP socket ─────────────────────│ GameServer │
      │  client    │ ◀──────────────── text/system ────────────────────│ (transport)│
      └────────────┘                                                    └─────┬──────┘
                                                                              │ Handler protocol
                                                                     ┌────────▼────────┐   ┌──────────────┐
                                                                     │     Engine      │   │   GameLoop   │
                                                                     │ commands +      │   │ (ticks the   │
                                                                     │ session binding │   │  world live) │
                                                                     └───┬─────────┬───┘   └──────────────┘
                                                            queries/     │         │  say → proposes a turn
                                                            mutations    │         │
                                                              ┌──────────▼──┐   ┌──▼───────────────────────┐
                                                              │    World    │   │  AI layer                │
                                                              │ rooms +     │   │  Provider → NpcMind →     │
                                                              │ entities    │   │  MemoryStream, + Action   │
                                                              │ (JSON data) │   │  registry (the seam)      │
                                                              └─────────────┘   └──────────────────────────┘
```

Two packages, one boundary:

- **`loom/`** — the reusable, game-agnostic framework. Knows nothing about caves
  or hermits.
- **`game/`** — the first world built on it: content (`world.json`) and a tiny
  entry point. **The rule that governs the whole repo:** if a thing mentions a
  specific room, NPC, or story, it lives in `game/`, never in `loom/`.

---

## 1. Suggested reading order

Read *outside-in, following one interaction*, not alphabetically. The path:

1. **The wire** — `protocol.py` (how bytes are shaped)
2. **The transport** — `session.py` → `server.py` → `loop.py`
3. **The wiring** — `game/main.py` (how it all gets assembled and started)
4. **The world** — `world/entity.py` → `world/location.py` → `world/world.py` → `content.py`
5. **The engine** — `engine.py` (the biggest single payoff; read it twice)
6. **The AI layer** — `ai/provider.py` → `ai/memory.py` → `ai/mind.py` → `action.py`
7. **The client** — `client/terminal.py` (proof the world lives on the server, not the client)
8. **The end-to-end trace** (§9 below) — put it all together

---

## 2. The wire — `loom/protocol.py`

Start here; it's 46 lines and everything else assumes it.

- `class Channel` (`loom/protocol.py:22`) — just string constants. `TEXT` and
  `SYSTEM` go server→client; `INPUT` goes client→server; `MAP`/`ENTITIES` are
  **reserved** — named but not emitted yet.
- `class Message` (`loom/protocol.py:34`) — a `(channel, data)` pair with two
  methods: `to_bytes()` serialises to `{"c":…,"d":…}\n`, and `from_line()` parses
  one back.

**The single most important idea in the codebase lives here:** the channel tag
is the entire extensibility story. A terminal reads `text`; a future 2D client
subscribes to `map`/`entities` on the *same* connection with *no* change to this
format. Multiplayer and rich graphics are both "add another channel," not
"rewrite the protocol." Read the module docstring — it says exactly this.

Ask yourself: *why newline-delimited JSON and not a binary protocol?* (Answer:
human-readable, debuggable with `nc`, transport-agnostic — the same bytes work
over TCP now and WebSocket later.)

---

## 3. The transport — `session.py`, `server.py`, `loop.py`

### `loom/session.py` — one connected client
A `Session` (`loom/session.py:7`) wraps a socket's reader/writer and offers
`send_text` / `send_system` (`:24`, `:27`). It holds **no game logic** — just
"how do I push a line to this one client." Note `player_id` (`:13`): the session
is *bound* to a character later, by the engine.

### `loom/server.py` — the async TCP server
- `class Handler` (`loom/server.py:12`) is a `Protocol` (structural interface):
  anything with `on_connect` / `on_input` / `on_disconnect` can be driven by the
  server. **The `Engine` is what implements this** — but the server doesn't know
  that. This is the seam that keeps transport and game logic apart.
- `serve_forever()` (`:26`) starts `asyncio.start_server` pointed at
  `_on_connect`.
- `_on_connect` (`:33`) is the heart: assign a session id, build a `Session`,
  call `handler.on_connect`, then loop `reader.readline()` and hand each line to
  `handler.on_input`. On disconnect it calls `handler.on_disconnect`.
- Notice `:48` — it accepts **either** a raw text line (so `nc`/`telnet` works)
  **or** a JSON `input` envelope (what the real client sends). Graceful
  degradation, built in.

> Detail worth clocking: the server keeps its *own* `self.sessions` dict
> (`:23`) for transport-level `broadcast()`. The engine *also* keeps a session
> registry (§5) for room-scoped delivery. Two registries, both keyed by the same
> session id — one is "everyone connected," the other is "who's in this room."

### `loom/loop.py` — the world's heartbeat
`GameLoop` (`loom/loop.py:12`) calls each registered system every
`tick_seconds`, forever (`run()`, `:22`). It's what makes the world *live* when
nobody is typing. **Today no systems are attached** — it ticks emptily. That is
deliberate: it's the hook where ambient events and the Phase-3 game-master's
slow pulse will hang. Note `:29` — a system that throws is caught and logged so
one bad system can't kill the loop.

---

## 4. The wiring — `game/main.py`

Only ~35 lines, and it shows how every piece snaps together:

```
world, start = load_world(WORLD_FILE)      # data
provider      = get_default_provider()     # which brain (env-selected)
engine        = Engine(world, provider, …) # the Handler
loop          = GameLoop(tick_seconds=5.0) # the heartbeat (idle for now)
server        = GameServer(engine, …)      # transport, driven BY the engine
await asyncio.gather(server.serve_forever(), loop.run())   # run both forever
```

The key line is `GameServer(engine, …)` (`game/main.py:24`): the **engine is
handed to the server as its handler**. That's the whole transport↔logic
connection. And `asyncio.gather` (`:26`) runs the socket server and the game
loop concurrently on one event loop.

This file is in `game/`, not `loom/`, because *choosing this world and this
port* is a game decision. Loom provides the parts; the game assembles them.

---

## 5. The world — `world/` and `content.py`

Plain data, no AI, no networking. These are the nouns an author edits.

- `world/entity.py` — a clean dataclass hierarchy (`loom/world/entity.py`):
  `Entity`(id, name, description) → `Character`(+location_id) →
  `Npc`(+persona dict) and `Player`(+session_id). Persona is **free-form**
  (`:20`): backstory/traits/goals/voice by convention, but the schema doesn't
  force it — the AI layer turns whatever's there into a prompt. `Item` is a
  *sibling* of `Character`, **not** a Character: it has no `location_id`; instead
  it is located by a single `holder` (a location = on the floor, a character = in
  inventory, later an item = a container), plus `aliases` and `portable`.
- `world/location.py` — a `Location` is an `Entity` plus `exits`
  (direction→location-id) and `occupants` (a set of entity ids).
- `world/world.py` — `World` is two dicts (`locations`, `entities`) plus a
  containment reverse-index (`_contents`), and the spatial operations over them:
  `add_entity` (files the entity into its room's occupants *and* indexes an
  item under its holder), `move` (the canonical "leave here, arrive there" for
  characters), and — for items — `place_item` (the one mutator behind take/drop/
  give: each is just a change of holder), `contents(holder)`, and `scope(actor)`
  (room occupants + floor items + own inventory: the candidate set a parser
  resolves a noun against). Note the symmetry: `holder`⇄`_contents` for items is
  the exact pattern as `location_id`⇄`occupants` for characters.
- `naming.py` — `resolve(phrase, scope)` turns a typed noun phrase into the entity
  meant, returning `Resolved` / `Ambiguous` / `NoMatch` (matching exact ▸
  whole-word ▸ prefix, honest about ambiguity). Game-agnostic, duck-typed, no
  `World` import. It is the generalisation of `salience.is_addressed` (which only
  asks "was this name mentioned?"); both the NPC give-handler and the player's
  take/drop/give use it. Prior-art survey behind it is recorded on B1.
- `content.py` — `load_world` reads world data into a `World`. **This is design
  commitment #2 made real:** the world is editable data, not code. Open
  `game/world/world.json` alongside it and match the fields to the parser — the
  cave, the exits, its two NPCs ("Odd the Hermit", wary and command-averse; "Wren
  the Wayfinder", a cheerful guide who walks you around), an `"items"` array (a
  lantern and a key on the cave floor; a hill-map in Wren's hand), and a
  `"director"` block (the game-master persona). Two things to note about the
  loader: (a) any top-level key beyond `locations`/`npcs`/`items`/`start_location`
  (like `director`) is captured into `world.meta` — world-level config the
  framework doesn't interpret but the game reads; (b) `path` may be a single file
  *or a directory* of `*.json` files it merges in sorted order (locations/npcs/
  items accumulate, meta blocks shallow-merge), so a world starts as one file and
  splits by region later with no engine change. The two NPCs share the start room
  so one `say` shows the contrast, Wren exercises `move`, and the items exercise
  take/drop/`give_item`.

---

## 6. The engine — `loom/engine.py`

Read this file twice. First for the command handling, then for the async NPC
path. It's where sessions, the world, and minds meet.

### Construction
`Engine.__init__` (`loom/engine.py:14`): stores the world/provider, builds an
**action registry** (`self.actions`, `:19` — defaults to `default_registry()`),
and gives **every NPC a mind** wired to that registry (`:28`). So each NPC gets
its own `NpcMind`, all sharing one action vocabulary.

### The Handler contract (what the server calls)
- `on_connect` (`:31`) — spawns a `Player`, drops it in the start room, registers
  **both** `self.players[sid]` and `self.sessions[sid]` (`:37–38`), sends the
  banner, and runs `_look`. (That `self.sessions` registry is what room broadcast
  uses — see below.)
- `on_disconnect` (`:44`) — removes the player and its session.
- `on_input` (`:50`) — routes player input through `command.parse` (B1): a verb
  table (synonyms, multi-word verbs like `pick up`), a `verb + DO + prep + IO`
  grammar, and articles/phrasing tolerance. It dispatches by verb *kind*:
  free-text (`say`), read-only queries (`_query`: look, examine, go, inventory,
  who, help, quit), and world-changing actions (`_player_action`). The last
  resolves the object phrases against scope — for disambiguation and a
  second-person acknowledgement — then routes `take`/`drop`/`give` through the
  **same** `ActionRegistry` the NPCs use (the handler re-resolves as the
  authoritative gate). When the verb is *unrecognised* (B1b), `_interpret` hands
  the free text to the model via `ai/intent.py`, constrained by
  `command.command_schema` (the verb-side grammar), and rebuilds the answer into
  the same `Parse` — so an LLM-interpreted command flows through the identical
  dispatch. Toggle with `Engine(intent_fallback=…)`; degrades to "unknown command".

### The commands
`_look` (`:78`) and `_go` (`:168`) are **pure engine code** — deterministic,
provider-agnostic. They query and mutate the `World` and send text. No AI is
involved in movement or looking; keep that distinction sharp.

### The interesting one: `_say` and the async NPC path
This is the payoff. Read `_say` (`:92`) → `_deliver_npc_reply` (`:110`) →
`_perform` (`:131`) → `_broadcast` (`:149`) in sequence.

- `_say` echoes your words, then for each NPC in the room **dispatches the reply
  as a background task** (`asyncio.create_task`, `:105`). This is design
  commitment #5: the LLM call runs *off* the input path, so a slow model never
  freezes your prompt (or anyone else's). The task is tracked in `self._tasks`
  and self-removes when done.
- `_deliver_npc_reply` (`:110`) sends the out-of-band *"…is considering your
  words…"* beat on the `system` channel (covers the latency), then calls
  `mind.converse(...)`. Note the try/except (`:116`): a provider failure degrades
  to a stock in-character line — the connection never dies.
- `_perform` (`:131`) executes **one validated action**: look it up in the
  registry, run its handler against an `ActionContext`, write the actor's memory,
  and broadcast the narration. Note `:133` — it re-checks the action exists even
  though the mind already validated it (defense in depth).
- `_broadcast` (`:149`) sends a line to **every player currently in a room** —
  correct for multiplayer even though there's one player today.

**The load-bearing sentence:** the mind *proposes* a turn; the engine *disposes*
of it here. The engine is the only thing that touches the `World`.

---

## 7. The AI layer — `ai/provider.py`, `ai/memory.py`, `ai/mind.py`, `action.py`

### `ai/provider.py` — the swappable brain
- `LLMProvider` (`loom/ai/provider.py:27`) is a one-method Protocol:
  `complete(system, messages, schema=None) -> str`. **The entire engine depends
  only on this.** The optional `schema` is the constrained-decoding grammar
  (default `None` = unconstrained); `OpenAICompatibleProvider` forwards it as a
  `response_format` json_schema via its `_schema_payload` hook, `FakeProvider`
  and `AnthropicProvider` ignore it.
- `FakeProvider` (`:43`) — deterministic, offline, no key. It fabricates
  persona-flavoured replies and (in structured mode) emits the JSON turn
  envelope. This is why the whole test suite runs with no GPU: nothing else in
  the engine can tell fake from real.
- `OpenAICompatibleProvider` (`:87`) — talks the OpenAI `/v1/chat/completions`
  schema over stdlib `urllib`, in a worker thread so it stays awaitable, with
  retries on 429/503/529. This is the *portable waist*.
- `OllamaProvider` (`:165`) — a thin subclass pointing at local Ollama. Read its
  docstring: it defaults `think=False` → sends `reasoning_effort:"none"`, because
  Qwen3.5 otherwise spends its whole token budget on hidden reasoning and returns
  empty content. (vLLM/SGLang later = same class, different `base_url`.)
- `get_default_provider` (`:215`) — env-driven selection (`LOOM_PROVIDER`).

### `ai/memory.py` — the memory substrate
`MemoryStream` (`loom/ai/memory.py:20`) is an append-only log with `recent(k)`
retrieval — the minimal useful slice of the Generative-Agents "memory stream."
Importance, embeddings, and reflection are deferred *behind this same interface*
(design commitment #6). One substrate will later serve both NPCs and players.

### `ai/mind.py` — persona + memory + the turn pipeline
This is where a model becomes a character.
- `_system_prompt` (`loom/ai/mind.py`) composes the persona (backstory, traits,
  goals, voice) + recent memories into the system message. If a registry is
  present it appends `_action_instructions` (the JSON-envelope contract + the
  action catalogue); if not, it stays a pure conversationalist.
- `converse` (the main method) — the pipeline you should trace closely:
  1. record what was heard into memory,
  2. `provider.complete(..., schema=registry.json_schema())` → raw string — with a
     registry present the model is **grammar-constrained** to the turn envelope
     (Phase 2 hardening), so a malformed shape is impossible at the token level,
  3. `_parse_turn` → tolerant JSON extraction + **validate each proposed action
     against the registry** (kept as defense-in-depth for unconstrained backends),
  4. if anything was invalid **and** actions are possible, do **one bounded
     retry**, feeding the exact validation error back to the model,
  5. return a `Turn(speech, actions)` of *validated* intents.
- `_extract_json` — handles a bare object, a ```json fenced block, prose-wrapped
  JSON, and **trailing junk** (the live model sometimes appends a stray `}` or
  sends `args` as a bare list). Total parse failure degrades to pure speech. It
  never raises; it never lets unvalidated text become an action.
- `Scene` — a read-only perception snapshot the engine composes (`_scene_for`)
  and hands in: place, description, `exits`, `others` present, `items` on the
  ground, and the NPC's own `inventory`. The mind renders it into the prompt so
  it can choose real exits and reason about real objects — but it reads the
  `Scene`, never the `World`.

The golden rule lives in the split: **`NpcMind` never imports or touches
`World`.** It only returns proposals.

### `action.py` — the validation seam (the heart of Phase 2)
Game-agnostic and dependency-free.
- `Param` / `ActionSpec` (`loom/action.py`) — an action = name + description +
  a tiny param schema (`str|int|float|bool|enum`, required/optional) + a handler.
- `ActionRegistry` — holds two halves: `validate(name, args)` (pure — used by the
  *mind* to check its own proposals, no world access) and the handlers (used by
  the *engine* to actually mutate the world). Two renderings of the *same* `Param`
  specs feed the model: `describe()` (the prose catalogue for the prompt) and
  `json_schema()` (the turn-envelope grammar for constrained decoding) — one
  source, so the shape the model is forced to emit and the shape the engine checks
  cannot drift (a unit test asserts it). Both `describe(names)` and
  `json_schema(names)` accept an optional subset, so one actor can be *offered*
  fewer actions than the registry holds (`NpcMind(offered=…)`) — the player takes
  and drops where the demo's NPCs do not — without a second registry.
- `default_registry()` now ships six built-ins:
  - `emote` (`_emote`) — a purely expressive action, zero world-state change: the
    tightest possible proof that the seam works (schema → validate → execute →
    narrate → remember).
  - `move` (`_move`) — the first world-mutating action; schema is `direction: str`
    (exit vocabulary is world-data), the handler resolves it against the actor's
    real exits and speaks to two rooms via `ActionResult.broadcasts`.
  - `give_item` (`_give_item`) — the first *multi-object* action. The schema
    guarantees two strings; the handler `resolve()`s the item against what the
    actor holds and the recipient against who is present, refusing (a dropped
    `ActionError`) when either is unknown or ambiguous. Both ends are confirmed
    against the world before anything moves — the golden rule for inventory.
  - `take_item` / `drop_item` — the taking side (from the floor, or from a named
    source) and its inverse. Promoted onto the seam so the player's `take`/`drop`
    run the same validated path as `give`; offered to the player but not (by
    default) to NPCs, via the offered-subset above.
  - `stage_event` (`_stage_event`) — the game-master director's action (Phase 3),
    and the mirror of `emote` on the director side: it narrates an ambient beat
    into a room, changing nothing. Unlike every actor action above it has *no
    body* — the target room is an explicit arg, so the handler's gate is "does
    this location exist?" and the beat broadcasts where aimed. Offered only to the
    `DirectorMind` (never NPCs or players), via `DIRECTOR_ONLY_ACTIONS` — the same
    offered-subset lever `PLAYER_ONLY_ACTIONS` uses. See §7 `ai/director.py`.

Adding an action later (`spawn_item`, `offer_quest`, `nudge_npc`) = a schema + a
handler registered here. Nothing else in the pipeline changes. That's the point
of the seam.

### `chronicle.py` + `ai/director.py` — the game-master (Phase 3)
The director is the unseen hand over the whole stage, on a slow pulse — where an
`NpcMind` is one character answering one line. It rides the *same* seam.
- `loom/chronicle.py` — a bounded, game-agnostic event feed with a monotonic
  `seq`. The engine `record()`s salient beats as they broadcast (arrival, speech,
  action, move); the director reads it as its perception of *what changed* (the
  turn-digest). `seq == last_seen` is the laziness gate — nothing happened, no
  model call.
- `ai/director.py`, `DirectorMind` — mirrors `NpcMind`: persona + memory + the
  seam, `observe(chronicle, snapshot)` → a validated `Turn`, constrained-decoded
  and offered only its `DIRECTOR_ONLY_ACTIONS`. An empty `actions` list = *watch
  and do nothing* (a first-class outcome). Its `speech` is a private note, never
  broadcast. The turn is parsed by the shared `mind.parse_turn` (factored out of
  `NpcMind`) so every mind on the seam validates identically.
- `ai/director.py`, `Director` — the orchestrator hung off the loop (§3) by
  `engine.attach_director(loop, …)`. Every `period_ticks` it *considers* a beat,
  but only if a player is present **and** restraint allows (B8): at least
  `min_new_events` new chronicle events since its last beat *and* `cooldown_pulses`
  pulses of breathing room — so most pulses cost nothing and it does nothing,
  capping frequency in code no matter how eager the model is. Each beat runs on a
  background task (non-blocking, non-overlapping); its validated actions execute
  through the same `engine._perform` as everyone. It acts *on* rooms, not from one:
  a bodiless `_DirectorActor` stub fills the seam's `actor`, and
  `engine.world_snapshot()` (rooms with someone present, keyed by location id) is
  the still-frame that complements the chronicle.
- **The perception pair the director reads:** the chronicle (*what changed*) + the
  snapshot (*how it stands now*). Assembled in `game/main.py`; the GM persona is
  authored as data (the `"director"` block in `world.json`, captured into
  `world.meta` by the loader), and `LOOM_GM_MODEL` selects the wide-context
  `loom-gm` model variant.

---

## 8. The client — `client/terminal.py`

Read it last, to confirm the thesis. It's two coroutines: `_reader` prints the
`text`/`system` channels (dimming system lines) and **ignores `map`/`entities`
by design** (`client/terminal.py:27`); `_writer` sends each typed line as an
`input` envelope. It knows *nothing* about caves, hermits, or commands — proof
that the protocol, not the client, carries the world. A graphical client is
"subscribe to more channels," not "a different game."

---

## 9. The end-to-end trace — one `say` command

Now put it together. Follow a single interaction through every layer:

```
You type:  say hello, old one
   │
1. client/terminal.py  _writer → Message(INPUT,"say hello, old one").to_bytes() → socket
2. loom/server.py      _on_connect loop: readline → parses the input envelope → handler.on_input(session,"say hello, old one")
3. loom/engine.py      on_input: command.parse → say verb (free text) → _say(session, player, "hello, old one")
4.                     _say echoes 'You say: "…"' to you, finds Odd in the room,
                       dispatches _deliver_npc_reply as a BACKGROUND task and returns immediately
                       (your prompt is free again — nothing blocks)
   ┄┄┄ meanwhile, on the background task ┄┄┄
5. loom/engine.py      _deliver_npc_reply broadcasts "Odd is considering your words…" (system)
6. loom/ai/mind.py     converse: builds persona+memory+action prompt → provider.complete(...)
7. loom/ai/provider.py Ollama POSTs to qwen3.5 → returns {"speech":"…","actions":[{"name":"emote",…}]}
8. loom/ai/mind.py     _parse_turn: extract JSON (tolerant), validate "emote" against the registry,
                       retry once if invalid → Turn(speech, [ActionIntent("emote", {...})])
9. loom/engine.py      broadcasts 'Odd: "…"' (text) to the room
10.                    _perform: registry handler runs → narration "Odd narrows his eyes"
                       → writes Odd's memory → _broadcast to the room
11. loom/server.py     each Session.send writes JSON lines back over the socket
12. client/terminal.py _reader prints the speech and the emote
```

The two things to take away from the trace:
- **Step 4 is asynchronous.** Speech and action arrive as *follow-up* messages,
  not a blocking reply. That's why the world stays responsive.
- **Steps 8 and 10 are the golden rule.** The model's action is validated
  (step 8, in the mind) before the engine will execute it (step 10). Raw model
  text is *never* executed as a state change.

---

## 10. Design commitments → where they live in code

| # | Commitment | Where to see it |
|---|------------|-----------------|
| 1 | Protocol separated from transport | `protocol.py` (channel tag); `server.py` accepts raw *or* JSON |
| 2 | World is editable data, not code | `content.py` + `game/world/world.json` |
| 3 | AI behind one interface | `LLMProvider` Protocol (`provider.py:27`); engine depends only on it |
| 4 | Never execute raw model text | `mind.converse` validates → `engine._perform` executes; `action.py` is the schema |
| 5 | AI calls run off the game loop | `_say` → `asyncio.create_task` (`engine.py:105`) |
| 6 | Memory is one substrate | `memory.py` `MemoryStream`; NPCs use it now, players later |

---

## 11. Exercises to cement it

Do these in order; each is small and confirms you understand a layer.

1. **Watch the wire.** Start the server, then `nc 127.0.0.1 4000` (raw mode).
   Type `look`. You'll see the JSON envelopes the client normally hides — the
   protocol laid bare.
2. **Add a command.** Add an `emote` verb to `command.default_verbs()` and handle
   it in the engine so the *player* can emote to the room (reuse `_broadcast`, or
   route it through the registry's `emote`). Proves you can read both the parser
   and the engine's deterministic half.
3. **Trace an action's validation.** Run `python scripts/try_provider.py` with a
   prompt that invites a gesture; watch it print `speech` + a validated action.
   Then temporarily break the emote schema (rename its param) and watch the retry
   fire in the logs.
4. **Read a test as a spec.** Open `tests/test_engine.py` — it drives the exact
   §9 trace with a `FakeSession` and `FakeProvider`, offline. It's the whole
   architecture in one readable file.
5. **Find the seam for Phase 2's next action.** Decide where `move` (an
   NPC-initiated room change) would plug in: a schema + handler in `action.py`,
   and nothing else. Sketch the handler using `World.move`.

---

## 12. Full file map (one line each)

```
loom/                     the reusable, game-agnostic framework
  protocol.py             wire format: {"c":channel,"d":data} + newline
  session.py              one connected client; send_text/send_system
  server.py               async TCP server; drives any Handler
  loop.py                 continuous tick loop; the director hangs off it (Phase 3)
  engine.py               parses input, resolves nouns, routes acts through the seam, NPC dispatch, records the chronicle, attach_director
  action.py               ActionRegistry: schema + validation + json_schema + handlers (emote/move/give/take/drop/stage_event)
  command.py              player-command parser (B1): verb table + DO/prep/IO grammar → a Parse; command_schema for B1b
  chronicle.py            bounded world event feed with a monotonic cursor — the director's turn-digest perception (Phase 3)
  salience.py             SalienceGate: which NPC engages (default: directed address)
  naming.py               resolve(phrase, scope) → Resolved / Ambiguous / NoMatch (noun-phrase resolution)
  content.py              load a World from editable JSON — one file OR a directory of region files; world-level config → world.meta
  world/
    entity.py             Entity → Character → Npc / Player, and Item (held by a `holder`)
    location.py           Location: exits + occupants
    world.py              World state + queries: move/occupants + place_item/contents/scope
  ai/
    provider.py           LLMProvider + Fake / OpenAI-compat / Ollama / Anthropic
    memory.py             MemoryStream (append + recency; the substrate)
    mind.py               NpcMind: persona + memory + turn pipeline + Scene; parse_turn (shared with the director)
    intent.py             free-text → one command via the command grammar (B1b fallback; world-free)
    director.py           DirectorMind (game-master turn) + Director (slow, lazy, non-blocking cadence on the loop) — Phase 3

game/                     the first world built on Loom (content only)
  main.py                 entry point: assemble + run server & loop
  world/world.json        the cave, its NPCs (Odd, Wren), items (lantern, key, map), and the "director" persona block
                          (one file today; point WORLD_FILE at world/ to split by region — the loader merges either form)

client/
  terminal.py             minimal reference client; knows only the protocol

scripts/                  smoke.py (e2e) · wire_demo.py (self-contained) · try_provider.py (provider ping)
                          · behavior_probe.py (LIVE behavioral regression harness — the mind gate)
tests/                    offline unit + integration tests (no GPU) — the engine gate
docs/                     PLAN.md (roadmap) · WALKTHROUGH.md (this file) · BACKLOG.md (noticed improvements)

Two test gates (see PLAN.md → Testing discipline): the offline suite proves the
engine; scripts/behavior_probe.py proves the mind against the live model. Run BOTH
after any change to a prompt, the action catalogue, or perception.
```

---

Once §9 reads like an obvious sequence rather than a puzzle, you understand the
architecture. From there, `docs/PLAN.md` tells you what's built on top of it next.
