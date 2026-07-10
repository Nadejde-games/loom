"""Ping the active LLM provider with one NPC turn — a live diagnostic.

Exercises the full Phase-2 path: the mind is given the action registry, so the
model must return the JSON turn envelope, which is parsed, validated, and shown
here as speech + any validated actions.

The provider is chosen from the environment (see loom.ai.get_default_provider):

  python scripts/try_provider.py                                   # FakeProvider
  LOOM_PROVIDER=ollama LOOM_OLLAMA_MODEL=qwen3.5:35b-a3b \\
      python scripts/try_provider.py                               # local Ollama
  ANTHROPIC_API_KEY=... LOOM_PROVIDER=anthropic \\
      python scripts/try_provider.py                               # Claude

Prints the provider name, the parsed turn, and the wall-clock latency.
"""
from __future__ import annotations
import asyncio
import os
import time
from loom.ai import get_default_provider, NpcMind, Scene
from loom.action import default_registry
from loom.content import load_world

WORLD = os.path.join(os.path.dirname(__file__), "..", "game", "world", "world.json")
PROMPT = os.environ.get(
    "LOOM_PROMPT",
    "Well met, old one. Greet me properly, and show me you mean no harm.")


async def main() -> None:
    provider = get_default_provider()
    print(f"provider: {getattr(provider, 'name', type(provider).__name__)}")

    world, _ = load_world(WORLD)
    hermit = world.entities["hermit"]
    mind = NpcMind(hermit, provider, registry=default_registry())

    # Hand the mind the same perception snapshot the engine builds, so the model
    # can see its exits and choose to move.
    loc = world.locations.get(hermit.location_id)
    scene = Scene(location=loc.name, description=loc.description,
                  exits=list(loc.exits.keys()),
                  others=[e.name for e in world.occupants(loc.id, exclude=hermit.id)]
                  ) if loc else None
    if scene:
        print(f"  (at {scene.location}; exits: {', '.join(scene.exits) or 'none'})")

    print(f"\n> Wanderer: {PROMPT}\n")
    t0 = time.time()
    turn = await mind.converse("Wanderer", PROMPT, scene=scene)
    dt = time.time() - t0

    print(f"{hermit.name}: {turn.speech or '(no speech)'}")
    for intent in turn.actions:
        print(f"  [action] {intent.name} {intent.args}")
    if not turn.actions:
        print("  [action] (none)")
    print(f"\n({dt:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
