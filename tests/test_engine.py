"""End-to-end vertical slice, offline: a player 'say' drives an NPC to both
speak and emote, the narration reaches the room, and the actor remembers acting.
Uses a fake in-memory session; no server socket, no provider network, no GPU.
"""
import asyncio
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.protocol import Channel


class FakeSession:
    def __init__(self, sid="s1"):
        self.id = sid
        self.player_id = None
        self.sent = []          # list[(channel, data)]
        self.closed = False

    async def send(self, channel, data):
        self.sent.append((channel, data))

    async def send_text(self, text):
        await self.send(Channel.TEXT, text)

    async def send_system(self, text):
        await self.send(Channel.SYSTEM, text)

    async def close(self):
        self.closed = True

    def texts(self):
        return "\n".join(d for (c, d) in self.sent if c == Channel.TEXT)


def build_engine():
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    world.add_entity(Npc(id="odd", name="Odd", description="a hermit",
                         location_id="room", persona={"voice": "terse"}))
    return Engine(world, FakeProvider(), start_location="room")


class EngineActionTests(unittest.IsolatedAsyncioTestCase):
    async def _drain(self, engine):
        # Await the background NPC-reply tasks spawned by 'say'.
        while engine._tasks:
            await asyncio.gather(*list(engine._tasks), return_exceptions=True)

    async def test_say_triggers_speech_and_emote(self):
        engine = build_engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "say dance with me")
        await self._drain(engine)

        out = s.texts()
        self.assertIn("Odd:", out)                       # spoke
        self.assertIn("Odd regards", out)                # emoted (room narration)
        self.assertTrue(any(m.kind == "action"
                            for m in engine.minds["odd"].memory.entries))

    async def test_say_speech_only_no_action(self):
        engine = build_engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "say hello friend")
        await self._drain(engine)

        out = s.texts()
        self.assertIn("Odd:", out)
        self.assertNotIn("Odd regards", out)
        self.assertFalse(any(m.kind == "action"
                             for m in engine.minds["odd"].memory.entries))

    async def test_thinking_beat_is_system_channel(self):
        engine = build_engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "say hello")
        await self._drain(engine)
        systems = [d for (c, d) in s.sent if c == Channel.SYSTEM]
        self.assertTrue(any("considering your words" in d for d in systems))


if __name__ == "__main__":
    unittest.main()
