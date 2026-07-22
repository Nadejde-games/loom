"""The play-in-editor harness (Phase 8, slice 2): boot the real engine at a room and play
it in a throwaway sandbox. Offline and deterministic on the FakeProvider — no network, no
GPU. The load-bearing assertions are behavioural (boot lands in the room, movement reaches
the neighbour, an NPC answers) and, above all, ISOLATION: the caller's world and the source
files are untouched. The Textual play screen that sits on this is smoke-tested in
tests/test_workbench.py."""
import hashlib
import os
import unittest

from loom.ai import FakeProvider
from loom.sandbox import Sandbox
from loom.world import World, Location, Npc, Item

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_WORLD = os.path.join(HERE, "..", "game", "world", "world.json")


class _Collector:
    """A sync emit sink that records every (text, system) line the engine produces."""
    def __init__(self):
        self.lines = []

    def __call__(self, text, system):
        self.lines.append((text, system))

    @property
    def text(self):
        return "\n".join(t for t, _ in self.lines)

    def npc_text(self):
        """The non-system lines — room prose, echoes, and NPC replies."""
        return "\n".join(t for t, sys in self.lines if not sys)


def _world():
    w = World()
    w.add_location(Location(id="hall", name="Great Hall",
                            description="A vast echoing hall.", exits={"north": "cave"}))
    w.add_location(Location(id="cave", name="Damp Cave",
                            description="Water drips here.", exits={"south": "hall"}))
    w.add_entity(Npc(id="guard", name="Stone Guard", location_id="hall",
                     persona={"backstory": "Sworn to the hall.", "traits": ["watchful"],
                              "goals": ["greet visitors"], "voice": "gruff"}))
    w.add_entity(Item(id="torch", name="Brass Torch", holder="hall"))
    return w


class SandboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_boots_into_the_chosen_room(self):
        c = _Collector()
        sb = Sandbox(_world(), "hall", FakeProvider(), c)
        await sb.start()
        self.assertIn("Great Hall", c.text)          # the first look names the room
        await sb.close()

    async def test_boots_into_an_arbitrary_room(self):
        # Not the "start": the sandbox drops the player into whatever room was chosen.
        c = _Collector()
        sb = Sandbox(_world(), "cave", FakeProvider(), c)
        await sb.start()
        self.assertIn("Damp Cave", c.text)
        self.assertNotIn("Great Hall", c.text)
        await sb.close()

    async def test_movement_reaches_the_neighbour(self):
        # Movement is code, not model — a deterministic assertion regardless of provider.
        c = _Collector()
        sb = Sandbox(_world(), "hall", FakeProvider(), c)
        await sb.start()
        await sb.send("go north")
        self.assertIn("Damp Cave", c.text)
        await sb.close()

    async def test_npc_answers_when_spoken_to(self):
        c = _Collector()
        sb = Sandbox(_world(), "hall", FakeProvider(), c)
        await sb.start()
        before = len(c.lines)
        await sb.send("say hello there")
        await sb.drain()                              # let the async NPC reply land
        self.assertGreater(len(c.lines), before)      # something came back
        await sb.close()

    async def test_caller_world_is_never_mutated(self):
        world = _world()
        before_ids = set(world.entities)
        c = _Collector()
        sb = Sandbox(world, "hall", FakeProvider(), c)
        await sb.start()
        await sb.send("go north")
        await sb.send("say hi")
        await sb.drain()
        await sb.close()
        # The sandbox ran on a deep copy: no player leaked in, nothing was removed/moved
        # in the caller's world.
        self.assertEqual(set(world.entities), before_ids)
        self.assertFalse([e for e in world.entities if e.startswith("player")])
        self.assertEqual(world.locations["hall"].exits, {"north": "cave"})

    async def test_source_file_is_byte_identical_after_play(self):
        digest_before = _digest(GAME_WORLD)
        world_start = _load_start(GAME_WORLD)
        c = _Collector()
        sb = Sandbox.from_path(GAME_WORLD, world_start, FakeProvider(), c)
        await sb.start()
        await sb.send("look")
        await sb.send("say hello")
        await sb.drain()
        await sb.close()
        digest_after = _digest(GAME_WORLD)
        self.assertEqual(digest_before, digest_after)   # the authored world is read-only
        self.assertTrue(c.text.strip())

    async def test_from_path_boots(self):
        c = _Collector()
        start = _load_start(GAME_WORLD)
        sb = Sandbox.from_path(GAME_WORLD, start, FakeProvider(), c)
        await sb.start()
        self.assertTrue(c.text.strip())
        await sb.close()


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _load_start(path):
    from loom.content import load_world
    _, start = load_world(path)
    return start


if __name__ == "__main__":
    unittest.main()
