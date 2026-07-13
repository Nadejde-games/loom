"""Entry point for the example game — the first world built on Loom.

Run:  PYTHONPATH=. python3 game/main.py
Then connect with:  PYTHONPATH=. python3 client/terminal.py
"""
from __future__ import annotations
import asyncio
import os
from loom import GameServer, GameLoop, Engine
from loom.ai import get_default_provider, OllamaProvider
from loom.content import load_world

HERE = os.path.dirname(os.path.abspath(__file__))
# A single file today; point this at the "world" directory to split by region
# later — load_world merges either form (see loom/content.py).
WORLD_FILE = os.path.join(HERE, "world", "world.json")


def _director_provider():
    """A larger-context model variant for the game-master, when configured.

    Set ``LOOM_GM_MODEL`` (e.g. ``loom-gm``, built from
    ops/modelfiles/loom-gm.Modelfile) to give the director its own wide-context
    model; otherwise it shares the engine's provider.
    """
    gm_model = os.environ.get("LOOM_GM_MODEL")
    if not gm_model:
        return None
    return OllamaProvider(
        model=gm_model,
        host=os.environ.get("LOOM_OLLAMA_HOST", "http://localhost:11434"))


async def main(host: str = "127.0.0.1", port: int = 4000) -> None:
    world, start = load_world(WORLD_FILE)
    provider = get_default_provider()
    print(f"[game] AI provider: {getattr(provider, 'name', type(provider).__name__)}")

    # autonomous_reactions: our NPCs react to what happens around them — a change
    # in the world (a storm the director raises) or each other's words and deeds —
    # of their own volition, cascading among themselves under the engine's rails
    # (B9). A framework capability we opt into here; NPCs are not puppets.
    engine = Engine(world, provider, start_location=start,
                    autonomous_reactions=True)
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
    lull = int(os.environ.get("LOOM_DIRECTOR_LULL", "4"))
    # Off-screen staging (B9): let the director see and foreshadow into the empty
    # rooms just ahead of the players. On here; LOOM_DIRECTOR_FORESHADOW=0 to disable.
    foreshadow = os.environ.get("LOOM_DIRECTOR_FORESHADOW", "1") not in ("0", "", "false")
    # The GM persona is world content — authored in world.json ("director" block),
    # captured into world.meta by the loader — not baked into this entry point.
    persona = world.meta.get("director")
    engine.attach_director(loop, persona=persona,
                           provider=gm_provider, period_ticks=period,
                           min_new_events=min_events, cooldown_pulses=cooldown,
                           lull_pulses=lull, foreshadow=foreshadow)
    gm_name = getattr(gm_provider or provider, "name", "engine provider")
    print(f"[game] game-master director: every {period} ticks "
          f"(>= {min_events} new events, >= {cooldown} pulses apart"
          + (f", or a {lull}-pulse lull" if lull else "")
          + (", foreshadowing ahead" if foreshadow else "") + f"), via {gm_name}")

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

    await asyncio.gather(server.serve_forever(), loop.run())


if __name__ == "__main__":
    host = os.environ.get("LOOM_HOST", "127.0.0.1")
    port = int(os.environ.get("LOOM_PORT", "4000"))
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        print("\n[game] shutting down.")
