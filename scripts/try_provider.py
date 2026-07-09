"""Ping the active LLM provider with one NPC turn — a live diagnostic.

The provider is chosen from the environment (see loom.ai.get_default_provider):

  python scripts/try_provider.py                                   # FakeProvider
  LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3:1.7b \\
      python scripts/try_provider.py                               # local Ollama
  ANTHROPIC_API_KEY=... LOOM_PROVIDER=anthropic \\
      python scripts/try_provider.py                               # Claude

Prints the provider name, the reply, and the wall-clock latency.
"""
from __future__ import annotations
import asyncio
import os
import time
from loom.ai import get_default_provider, NpcMind
from loom.content import load_world

WORLD = os.path.join(os.path.dirname(__file__), "..", "game", "world", "world.json")


async def main() -> None:
    provider = get_default_provider()
    print(f"provider: {getattr(provider, 'name', type(provider).__name__)}")

    world, _ = load_world(WORLD)
    hermit = world.entities["hermit"]
    mind = NpcMind(hermit, provider)

    prompt = "Hello there, old one. What is this place, and who are you?"
    print(f"\n> Wanderer: {prompt}\n")
    t0 = time.time()
    reply = await mind.hear_and_respond("Wanderer", prompt)
    dt = time.time() - t0
    print(f"{hermit.name}: {reply}")
    print(f"\n({dt:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
