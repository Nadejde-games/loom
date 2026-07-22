# Spike — play-in-editor (Phase 8, slice 2): walk into a room and experience it live

*Opened 2026-07-22. Status: **BUILT & both-gate green 2026-07-22.** The second slice of the
authoring workbench (see `docs/spikes/workbench.md`). The Explorer (slice 1) let an author
*see* a room; this lets them *stand in it* — boot the real engine at a selected room and play
it (look / move / say, live NPC minds) in a throwaway sandbox that is discarded on exit and
never touches the authored world. Gate: offline 691 green; live `behavior_probe.py play` 2/2
on OpenRouter (qwen/qwen3.6-35b-a3b) — real, in-character NPC replies through the sandbox.*

## Why

The original ask named it: "select a room … walk into it and experience it live." Slice 1
is the read surface; this is the felt one. It also earns its keep twice — slice 3's authoring
agent reuses the very same ephemeral-play harness as its `preview_play` tool to test a change
before committing it.

## The reuse map (in-repo recon, cited)

The engine already does almost all of this; the sandbox is thin.

- **Boot at any room is a constructor param.** `Engine(world, provider, start_location=<room>)`
  (`engine.py:84`) drops the anonymous player exactly there (`engine.py:232`). No teleport
  verb exists or is needed.
- **The play path is five calls.** `load_world(path)` → `Engine(...)` → a session →
  `await engine.on_connect(session)` (mints the player + banner + first look, `engine.py:229`)
  → `await engine.on_input(session, line)` per typed line (`engine.py:426`). That is the whole
  runner; `GameServer`/`GameLoop` are not required for basic play.
- **Isolation is nearly free.** The engine *never* writes `world.json` (only `scripts/author.py`
  writes worlds). The overlay save (`attach_persistence`), the SQLite memory store
  (`memory_store=`), and embeddings (`embedder=`) are all opt-in and **off in the bare
  `Engine(...)`**. `load_world` returns a fresh `World` each call (`content.py:44`). So a sandbox
  that loads/►deep-copies its own world and attaches none of those writes **nothing**. Play does
  mutate the world *in memory* (player join, moves, take/drop) — hence a throwaway copy, not the
  live one.
- **Two sharp edges.** (1) Engine output is polymorphic — a plain string *or* a list of styled
  spans (`protocol.py`); flatten with `loom.style.plain` (`style.py:91`) or lines render as raw
  dicts. (2) NPC replies are **asynchronous** — `on_input("say …")` returns after echoing; the
  reply arrives later via `session.send_text` from a detached task in `engine._tasks`
  (`engine.py:601`). The UI must render out-of-order arrivals and cancel `engine._tasks` on
  teardown.
- **Session is socket-shaped** (`session.py`): supply a **duck-typed** session exposing `id`,
  a settable `player_id`, and async `send_text` / `send_system` / `close` (plus a log-only
  `addr`). No `GameServer`.

## Decisions — signed off 2026-07-22

1. **Ambient world → still (reactive only).** No `GameLoop`: NPCs answer when spoken to, but
   the clock / weather / director do not tick and NPCs do not wander. Cheaper, reproducible,
   and the character under test stays in the room. *(Rejected for now: a living, ticking world
   — richer but nondeterministic, more model calls, and a tested NPC can wander out. It becomes
   an opt-in toggle in a later slice.)* Concretely: the bare `Engine(...)` with defaults —
   `autonomous_reactions=False`, `npc_act_gate=False`, no loop, no director.
2. **NPC minds → live by default, with a dry-run toggle.** Jumping in drives real NPCs over
   **OpenRouter** (the true "experience it"); a labelled dry-run toggle swaps in the offline
   `FakeProvider` for a free, deterministic structural walk. Offline tests use Fake; the live
   gate uses real. *(Rejected: fake-by-default — cheaper but you never feel the real characters
   unless you flip it.)* The live provider is built **explicitly** as
   `OpenRouterProvider(model=LOOM_OPENROUTER_MODEL or "qwen/qwen3.6-35b-a3b", api_key=OPENROUTER_API_KEY)`
   — the NPC tier, robust to `LOOM_PROVIDER` being unset (the `.env` holds only the key), and
   OpenRouter-only per the standing rule (the local vLLM server is never touched).
3. **Play surface → a modal screen.** Entering play pushes a full-width Textual `Screen`
   (scrolling transcript + a command line); Escape tears it down and returns to the Explorer.
   *(Rejected: a fourth pane beside navigator/inspector — cramped; the transcript fights for
   width.)*

**Architecture (the standing invariant, not a fork):** the game-agnostic *ephemeral-play
harness* — boot-at-room, run, guarantee-no-writes, teardown — lives in the framework
(`loom/sandbox.py`), offline-testable on `FakeProvider`, and is exactly the seam slice 3's
agent reuses. The Textual play screen lives in `authoring/`. Provider *choice* (live vs fake,
which model) is app policy → `authoring/`. `loom` never imports Textual.

## The design

- **`loom/sandbox.py`** (framework, pure of UI): `Sandbox(world, room_id, provider, emit)` —
  deep-copies the world (belt-and-braces isolation), builds the bare `Engine` at `room_id`, and
  wraps a socket-free `_SinkSession` that flattens every engine payload through `style.plain`
  and forwards it to the `emit(text, system)` callback. `Sandbox.from_path(path, …)` loads a
  fresh world. `await start()` (on_connect), `await send(line)` (on_input), `await close()`
  (cancel `engine._tasks`, on_disconnect — nothing persisted), `await drain()` (await in-flight
  NPC replies, so a test can observe async output). Attaches no persistence / memory / embedder.
- **`authoring/play.py`** — `PlayScreen(Screen)`: a `RichLog` transcript + an `Input` command
  line; on mount it builds a `Sandbox.from_path` with the chosen provider and an `emit` that
  appends to the log (system beats dimmed); a worker awaits `start()`, each submit awaits
  `send()`; Escape awaits `close()` and pops. The screen title shows the room and the mode
  (LIVE / dry-run).
- **`authoring/app.py`** — a `p` binding enters play for the current selection's room (a room →
  itself; an NPC/item → its home location via `location_index`); an `f` binding toggles dry-run;
  the mode shows in the subtitle. The live provider is built in the app (OpenRouter, above);
  the entrypoint gains a tiny dependency-free `.env` loader (python-dotenv is not in the
  authoring extra) so `OPENROUTER_API_KEY` reaches the provider from `python -m authoring`.
- **Carried from slice 1 — selection↔navigator sync** (this slice's first task): `_select`
  drives the tree cursor onto the chosen entity, so search / jump-link / play-target all keep
  the navigator highlight in step (guarded against a select→highlight→select loop).

## Both gates

- **Offline** — `tests/test_sandbox.py`: on `FakeProvider`, boot at a room and assert the first
  look names it; `go <dir>` reaches the neighbour (deterministic, code-driven, no model); a
  `say` + `drain` surfaces an NPC line; **isolation** — the passed-in `World` is unmutated (no
  player leaks in) and the source `world.json` is byte-identical after a full session. Plus a UI
  smoke (`tests/test_workbench.py` grows a `PlayScreen` case via `App.run_test()`+`Pilot`, Fake
  provider) and the pane-sync assertion.
- **Live** — the gate returns here: `scripts/behavior_probe.py` gains a `play` family — jump
  into `cave_mouth`, `say` something, and assert a real NPC reply arrives over OpenRouter within
  a timeout.

## Deferrals

- **Living/ticking world in the sandbox** (director, clock, weather, wandering) — the toggle
  after the still baseline proves out.
- **Inventory injection / "play as this character", save-a-walk, seeded reproducible live runs,
  a jump-to-any-room picker inside play** — grow after the spine.
- **`autonomous_reactions` / `npc_act_gate` fidelity** — the bare engine first; layer the game's
  richer NPC flags in behind a fidelity toggle later.
