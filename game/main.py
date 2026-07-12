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

    engine = Engine(world, provider, start_location=start)
    loop = GameLoop(tick_seconds=5.0)  # ambient world systems hang off here
    server = GameServer(engine, host=host, port=port)

    # The unseen game-master: a slow, lazy pulse that shapes ambient beats. Runs
    # on its own model variant if LOOM_GM_MODEL is set, else the engine provider.
    gm_provider = _director_provider()
    period = int(os.environ.get("LOOM_DIRECTOR_PERIOD", "12"))  # ticks between pulses
    min_events = int(os.environ.get("LOOM_DIRECTOR_MIN_EVENTS", "3"))
    cooldown = int(os.environ.get("LOOM_DIRECTOR_COOLDOWN", "2"))
    # The GM persona is world content — authored in world.json ("director" block),
    # captured into world.meta by the loader — not baked into this entry point.
    persona = world.meta.get("director")
    engine.attach_director(loop, persona=persona,
                           provider=gm_provider, period_ticks=period,
                           min_new_events=min_events, cooldown_pulses=cooldown)
    gm_name = getattr(gm_provider or provider, "name", "engine provider")
    print(f"[game] game-master director: every {period} ticks "
          f"(>= {min_events} new events, >= {cooldown} pulses apart), via {gm_name}")

    await asyncio.gather(server.serve_forever(), loop.run())


if __name__ == "__main__":
    host = os.environ.get("LOOM_HOST", "127.0.0.1")
    port = int(os.environ.get("LOOM_PORT", "4000"))
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        print("\n[game] shutting down.")
