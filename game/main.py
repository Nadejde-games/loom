"""Entry point for the example game — the first world built on Loom.

Run:  PYTHONPATH=. python3 game/main.py
Then connect with:  PYTHONPATH=. python3 client/terminal.py
"""
from __future__ import annotations
import asyncio
import os
from loom import GameServer, GameLoop, Engine
from loom.ai import get_default_provider
from loom.content import load_world

HERE = os.path.dirname(os.path.abspath(__file__))
WORLD_FILE = os.path.join(HERE, "world", "world.json")


async def main(host: str = "127.0.0.1", port: int = 4000) -> None:
    world, start = load_world(WORLD_FILE)
    provider = get_default_provider()
    print(f"[game] AI provider: {getattr(provider, 'name', type(provider).__name__)}")

    engine = Engine(world, provider, start_location=start)
    loop = GameLoop(tick_seconds=5.0)  # ambient world systems will attach here
    server = GameServer(engine, host=host, port=port)

    await asyncio.gather(server.serve_forever(), loop.run())


if __name__ == "__main__":
    host = os.environ.get("LOOM_HOST", "127.0.0.1")
    port = int(os.environ.get("LOOM_PORT", "4000"))
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        print("\n[game] shutting down.")
