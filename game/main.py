"""Entry point for the example game — the first world built on Loom.

Run:  PYTHONPATH=. python3 game/main.py
Then connect with:  PYTHONPATH=. python3 client/terminal.py
"""
from __future__ import annotations
import asyncio
import os
from loom import GameServer, GameLoop, Engine, log
from loom.ai import (get_default_provider, get_default_embedder, OllamaProvider,
                     VLLMProvider, OpenRouterProvider, MemoryStore)
from loom.content import load_world
from loom.world import Npc

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
# A single file today; point this at the "world" directory to split by region
# later — load_world merges either form (see loom/content.py).
WORLD_FILE = os.path.join(HERE, "world", "world.json")

# Load a repo-root .env (e.g. OPENROUTER_API_KEY) so a secret need not be exported
# by hand; a value already in the real environment always wins (override=False).
# python-dotenv is an application dependency (the `game` extra) — see
# docs/DEPENDENCIES.md; the .env is git-ignored, so the key never enters the repo.
# Degrade gracefully if the extra isn't installed: a real env var still works.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"), override=False)
except ImportError:
    pass


def _director_provider():
    """A larger-context model variant for the game-master, when configured.

    The director is built on the *same backend* as the engine so the two stay on
    one server/account. On OpenRouter it defaults to the denser game-master tier
    (``qwen/qwen3.6-27b``) even when ``LOOM_GM_MODEL`` is unset — the two-tier setup
    the user chose (NPCs on 3.6-35b-a3b, the GM on 3.6-27b); on the local backends it
    returns ``None`` unless ``LOOM_GM_MODEL`` names a variant (then the director
    simply shares the engine provider). Set ``LOOM_GM_MODEL`` to override the slug on
    any backend: vLLM (``LOOM_PROVIDER=vllm``), OpenRouter, else Ollama (e.g.
    ``loom-gm`` from ops/modelfiles/loom-gm.Modelfile).
    """
    gm_model = os.environ.get("LOOM_GM_MODEL")
    choice = os.environ.get("LOOM_PROVIDER", "").strip().lower()
    on_openrouter = (choice == "openrouter"
                     or (not choice and os.environ.get("LOOM_OPENROUTER_MODEL")))
    if on_openrouter:
        # On the hosted backend the GM tier is a real default, not opt-in: NPCs on
        # 3.6-35b-a3b, the game-master on the denser 3.6-27b (LOOM_GM_MODEL overrides).
        return OpenRouterProvider(
            model=gm_model or "qwen/qwen3.6-27b",
            host=os.environ.get("LOOM_OPENROUTER_HOST") or None,
            api_key=(os.environ.get("LOOM_OPENROUTER_API_KEY")
                     or os.environ.get("OPENROUTER_API_KEY") or None))
    if not gm_model:
        return None
    if choice == "vllm" or (not choice and os.environ.get("LOOM_VLLM_MODEL")):
        return VLLMProvider(
            model=gm_model,
            host=os.environ.get("LOOM_VLLM_HOST", "http://localhost:8000"),
            api_key=os.environ.get("LOOM_VLLM_API_KEY") or None)
    return OllamaProvider(
        model=gm_model,
        host=os.environ.get("LOOM_OLLAMA_HOST", "http://localhost:11434"))


async def main(host: str = "127.0.0.1", port: int = 4000) -> None:
    # Force stdout line-buffered so every event prints the instant it happens (piped or
    # wrapped stdout otherwise block-buffers and the console lags in batches). The engine
    # logs world events through loom.log in real time; LOOM_VERBOSE=1 adds the debug
    # firehose (salience skips, act-gate reasons, reflection accumulation and questions).
    log.real_time_stdout()
    world, start = load_world(WORLD_FILE)
    provider = get_default_provider()
    print(f"[game] AI provider: {getattr(provider, 'name', type(provider).__name__)}")

    # Inference telemetry: surface every model call in the server log in real time — elapsed
    # seconds, tokens as they stream (local backends), and a final tok/s tally — so gameplay
    # shows what the model is doing and how fast. A slow local call ticks live; a fast hosted
    # call shows just its start and end tally. LOOM_INFER_LOG=0 to silence.
    if os.environ.get("LOOM_INFER_LOG", "1") not in ("0", "", "false"):
        from loom.ai import LogInferenceReporter, set_reporter
        set_reporter(LogInferenceReporter())
        print("[game] inference telemetry: on (per-call elapsed + tokens; LOOM_INFER_LOG=0 to silence)")
    if log.VERBOSE:
        print("[game] verbose logging on (LOOM_VERBOSE) — full debug trace")

    # autonomous_reactions: our NPCs react to what happens around them — a change
    # in the world (a storm the director raises) or each other's words and deeds —
    # of their own volition, cascading among themselves under the engine's rails
    # (B9). A framework capability we opt into here; NPCs are not puppets.
    # npc_act_gate (B5): a cheap low-temp pass decides each NPC's action before the
    # blended turn speaks, so a willing guide reliably *moves* when asked (raising the
    # ~70% blended ceiling — measured 8/8 gated, with silence/give/perception all
    # preserved). On here; LOOM_NPC_ACT_GATE=0 to disable. Costs a second model call
    # per engaged NPC turn (bounded by salience — only engaged NPCs pay it).
    npc_gate = os.environ.get("LOOM_NPC_ACT_GATE", "1") not in ("0", "", "false")
    # Memory depth (Phase 5): an embedder lets every mind's memory rank by *relevance*
    # to the current moment, not only recency — so an old-but-salient memory (a
    # promise, a threat) can surface when it matters. Hosted OpenRouter embeddings on
    # the same key as chat (get_default_embedder); None falls back to recency+
    # importance, still a real upgrade. LOOM_EMBEDDER=none to disable.
    embedder = get_default_embedder()
    # SQLite-backed memory (slice 1b): a memory is an incremental INSERT and survives a
    # restart in the DB (embeddings persisted as BLOBs), rather than being re-serialized
    # into the JSON overlay on every autosave. The DB lives beside the world save, NOT
    # inside game/world/ (load_world would ingest it). LOOM_MEMORY_DB overrides.
    memory_db = os.environ.get("LOOM_MEMORY_DB") or os.path.join(HERE, "world.memory.db")
    memory_store = MemoryStore(memory_db)
    # Durable player identity (Phase 5, slice 3): a connecting player is prompted for a
    # name, which maps to a durable record — so held loot, quests, location, and the
    # world's memory of them survive a disconnect. A returning name resumes where it stood;
    # NPCs remember it (their memories already record the speaker by name), surfaced when
    # you next speak to them. On here; LOOM_REQUIRE_LOGIN=0 falls back to anonymous
    # session-ephemeral Wanderers. See docs/spikes/identity.md.
    require_login = os.environ.get("LOOM_REQUIRE_LOGIN", "1") not in ("0", "", "false")
    engine = Engine(world, provider, start_location=start,
                    autonomous_reactions=True, npc_act_gate=npc_gate,
                    require_login=require_login,
                    embedder=embedder, memory_store=memory_store)
    print(f"[game] memory embedder: "
          f"{getattr(embedder, 'name', 'none (recency + importance only)')}")
    print(f"[game] memory store: {memory_db}")
    print(f"[game] player identity: "
          + ("durable — name on connect, records persist, the world remembers you"
             if require_login else "anonymous session-ephemeral Wanderers"))
    loop = GameLoop(tick_seconds=5.0)  # ambient world systems hang off here
    server = GameServer(engine, host=host, port=port)

    # The unseen game-master: a slow, lazy pulse that shapes ambient beats. Runs
    # on its own model variant if LOOM_GM_MODEL is set, else the engine provider.
    gm_provider = _director_provider()
    period = int(os.environ.get("LOOM_DIRECTOR_PERIOD", "12"))  # ticks between pulses
    min_events = int(os.environ.get("LOOM_DIRECTOR_MIN_EVENTS", "3"))
    cooldown = int(os.environ.get("LOOM_DIRECTOR_COOLDOWN", "2"))
    # The lull trigger (B9): after this many quiet pulses since its last beat, the
    # director stirs a still room with one gentle beat — a liveliness floor beside
    # the world-clock's sparser turnings. 0 = off (pure restraint). We opt in here.
    # Set to 6 (from 4) so a quiet room is poked less often — the ambient world was
    # tuned calmer overall (slower clock/weather too), leaving room for idle-NPC stirs.
    lull = int(os.environ.get("LOOM_DIRECTOR_LULL", "6"))
    # Off-screen staging (B9): let the director see and foreshadow into the empty
    # rooms just ahead of the players. On here; LOOM_DIRECTOR_FORESHADOW=0 to disable.
    foreshadow = os.environ.get("LOOM_DIRECTOR_FORESHADOW", "1") not in ("0", "", "false")
    # The model-side act-gate (B8): a cheap low-temp wait/act decision before each
    # activity-driven beat, so the director stages only when the moment truly calls
    # for it. Proven live (bare scene 8/8 wait, evocative 8/8 act; behavior_probe
    # director.restraint / director.gate-acts-on-cue) — on here. LOOM_DIRECTOR_ACT_GATE=0
    # to disable and fall back to the deterministic-only restraint. (Scoped to the
    # activity path; the lull floor stays deterministic — the model would silence it.)
    act_gate = os.environ.get("LOOM_DIRECTOR_ACT_GATE", "1") not in ("0", "", "false")
    # The GM persona is world content — authored in world.json ("director" block),
    # captured into world.meta by the loader — not baked into this entry point.
    persona = world.meta.get("director")
    engine.attach_director(loop, persona=persona,
                           provider=gm_provider, period_ticks=period,
                           min_new_events=min_events, cooldown_pulses=cooldown,
                           lull_pulses=lull, foreshadow=foreshadow, act_gate=act_gate)
    gm_name = getattr(gm_provider or provider, "name", "engine provider")
    print(f"[game] game-master director: every {period} ticks "
          f"(>= {min_events} new events, >= {cooldown} pulses apart"
          + (f", or a {lull}-pulse lull" if lull else "")
          + (", foreshadowing ahead" if foreshadow else "")
          + (", act-gated" if act_gate else "") + f"), via {gm_name}")

    # Memory reflection (Phase 5, slice 2): on a threshold-gated cadence, an agent distills
    # its accumulated memory into higher-level "reflection" memories on the same substrate
    # — so a long-lived NPC forms durable beliefs that then colour its dialogue, and the
    # director notices the patterns across its own beats. A second cognition (two model
    # calls per reflection). The live gate cleared 3/3, so it is ON here; LOOM_REFLECT=0 to
    # disable. The defaults are tuned for OBSERVABILITY in a play session (a few pointed,
    # salient exchanges with one NPC trip it within ~20-40s) — raise LOOM_REFLECT_PERIOD
    # and _THRESHOLD for a calmer, more economical long-run world. LOOM_REFLECT_TELL=0
    # silences the perceptible "gaze turns inward" tell (memory-only reflection).
    if os.environ.get("LOOM_REFLECT", "1") not in ("0", "", "false"):
        r_period = int(os.environ.get("LOOM_REFLECT_PERIOD", "20"))
        r_threshold = int(os.environ.get("LOOM_REFLECT_THRESHOLD", "24"))
        r_tell = os.environ.get("LOOM_REFLECT_TELL", "1") not in ("0", "", "false")
        engine.attach_reflector(loop, period_ticks=r_period,
                                importance_threshold=r_threshold, tell=r_tell)
        print(f"[game] memory reflection: every {r_period} ticks "
              f"(when an agent's accumulated importance >= {r_threshold}), NPCs + director"
              + (", with a visible tell" if r_tell else ""))

    # Idle-NPC autonomy (Phase 5, slice 4): a character that moves *first*. On a slow
    # cadence, one NPC in a quiet, occupied room acts or speaks unbidden from its own
    # goal (grounded in its reflections) — the NPC-side mirror of the director's lull,
    # and its peer: both read "room quiet" off the shared chronicle, so a director beat
    # and an idle stir suppress each other. LOOM_IDLE_NPC is the quiet-pulse floor (0 =
    # off); kept level with LOOM_DIRECTOR_LULL so neither always breaks the silence
    # first. A roamer (an NPC authored "wanders" — Wren) may walk an exit; an anchored
    # one (Odd) is held in place. On here; LOOM_IDLE_NPC=0 to disable.
    idle_quiet = int(os.environ.get("LOOM_IDLE_NPC", "4"))
    if idle_quiet > 0:
        idle_period = int(os.environ.get("LOOM_IDLE_PERIOD", "12"))  # ticks between pulses
        idle_cooldown = int(os.environ.get("LOOM_IDLE_COOLDOWN", "3"))
        engine.attach_idler(loop, period_ticks=idle_period,
                            quiet_pulses=idle_quiet, cooldown_pulses=idle_cooldown)
        roamers = [e.name for e in world.entities.values()
                   if isinstance(e, Npc) and getattr(e, "wanders", False)]
        print(f"[game] idle-NPC autonomy: a stir every {idle_period} ticks after "
              f"{idle_quiet} quiet pulses"
              + (f"; roamers: {', '.join(roamers)}" if roamers else "; all NPCs stay put"))

    # The world-clock: the world's own time, advancing on the loop whether or not
    # anyone is present (B9). Time-of-day turns through a table authored in the
    # world's "clock" block; each turning sets a standing condition and lands one
    # ambient beat — deterministic, no model call — that the characters answer of
    # their own volition through the reaction path. Off unless the game wires it.
    clock_cfg = world.meta.get("clock")
    if clock_cfg and clock_cfg.get("phases"):
        factor = float(os.environ.get("LOOM_CLOCK_FACTOR",
                                      clock_cfg.get("factor", 1.0)))
        engine.attach_clock(loop, clock_cfg["phases"], factor=factor,
                            start_minute=clock_cfg.get("start_minute"))
        print(f"[game] world-clock: {len(clock_cfg['phases'])} phases, "
              f"{factor} game-min/sec (a day every {24 * 60 / factor:.0f}s)")

    # Weather (B9): a bounded random walk over sky-states on the same loop — the
    # clock's sibling. Authored in the world's "weather" block; opt-in like the clock.
    weather_cfg = world.meta.get("weather")
    if weather_cfg and weather_cfg.get("states"):
        engine.attach_weather(loop, weather_cfg["states"],
                              period_pulses=int(weather_cfg.get("period_pulses", 12)),
                              change_chance=float(weather_cfg.get("change_chance", 0.4)),
                              start_index=int(weather_cfg.get("start_index", 0)))
        print(f"[game] weather: {len(weather_cfg['states'])} sky-states, "
              f"a roll every {weather_cfg.get('period_pulses', 12)} pulses")

    # The loot forge (Phase 4): dynamic, context-aware items on the golden rule. Code
    # rolls an item's mechanical brief (tier/tags/theme) from the authored "loot" tables,
    # gated by the place's current conditions (weather/time); the model authors only its
    # flavour (name/lore) under a flat constrained schema; the forge assembles a real
    # Item. Fires on quest completion — a per-player reward. On the denser game-master
    # tier (creative flavour, and it is a rare, slow call). Off unless a "loot" block is
    # authored; LOOM_LOOT_SEED pins the roll for a reproducible run (else fresh variety).
    loot_cfg = world.meta.get("loot")
    if loot_cfg and loot_cfg.get("tiers"):
        seed_env = os.environ.get("LOOM_LOOT_SEED")
        forge = engine.attach_loot(loot_cfg, provider=gm_provider,
                                   seed=int(seed_env) if seed_env else None)
        if forge:
            print(f"[game] loot forge: {len(loot_cfg['tiers'])} tiers, "
                  f"{len(loot_cfg.get('tags', []))} tags, via "
                  f"{getattr(gm_provider or provider, 'name', 'engine provider')} "
                  "(on quest completion)")

    # Starting quests: a goal every connecting player is handed, so the world has a
    # purpose from the first step — and a deterministic way to reach the loot forge (walk
    # to a quest's destination → it completes → a reward is forged). Authored as the
    # "start_quests" block; off unless present.
    start_quests = world.meta.get("start_quests")
    if start_quests:
        kept = engine.attach_start_quests(start_quests)
        print(f"[game] starting quests: {len(kept)} offered on connect")

    # Persistence (Phase 5): make the world survive a restart. The authored world.json
    # above is always the definition; a versioned JSON overlay carries only the mutable
    # delta — entity positions, forged loot, conditions, clock/weather, every mind's
    # memory, the chronicle. Save on shutdown (the finally below) + a coarse autosave.
    # Restore runs here, last, so the overlay lands on the fully-wired world (director,
    # clock, weather all attached). The save must NOT live inside game/world/, where
    # load_world would ingest it as content. LOOM_SAVE_PATH overrides the location;
    # LOOM_AUTOSAVE_PULSES the cadence (0 = shutdown-only).
    save_path = os.environ.get("LOOM_SAVE_PATH") or os.path.join(HERE, "world.save.json")
    autosave = int(os.environ.get("LOOM_AUTOSAVE_PULSES", "60"))
    engine.attach_persistence(loop, save_path, autosave_pulses=autosave)
    restored = engine.restore()
    print(f"[game] persistence: {save_path} "
          + (f"(restored)" if restored else "(fresh world)")
          + (f", autosave every {autosave} pulses" if autosave else ", save on shutdown"))

    try:
        await asyncio.gather(server.serve_forever(), loop.run())
    finally:
        # Graceful shutdown (SIGINT cancels this task) — persist the final state so a
        # forged reward left in the world, and what the NPCs remember, outlast the run.
        if engine.save():
            print("[game] world saved.")
        memory_store.close()


if __name__ == "__main__":
    host = os.environ.get("LOOM_HOST", "127.0.0.1")
    port = int(os.environ.get("LOOM_PORT", "4000"))
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        print("\n[game] shutting down.")
