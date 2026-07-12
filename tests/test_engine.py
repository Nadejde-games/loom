"""End-to-end vertical slice, offline: a player 'say' drives an NPC to both
speak and emote, the narration reaches the room, and the actor remembers acting.
Uses a fake in-memory session; no server socket, no provider network, no GPU.
"""
import asyncio
import unittest

from loom.world import World, Location, Npc, Item
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


def build_two_room_engine():
    """Room A (north→B) with Odd; Room B (south→A). Start in A."""
    world = World()
    world.add_location(Location(id="a", name="Room A", description="A bare room.",
                               exits={"north": "b"}))
    world.add_location(Location(id="b", name="Room B", description="A far room.",
                               exits={"south": "a"}))
    world.add_entity(Npc(id="odd", name="Odd", description="a hermit",
                         location_id="a", persona={"voice": "terse"}))
    return Engine(world, FakeProvider(), start_location="a")


def build_two_npc_engine():
    """One room, two NPCs — Odd the Hermit and Wren the Wayfinder — to exercise
    the salience gate (who answers when) and chosen silence."""
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    world.add_entity(Npc(id="odd", name="Odd the Hermit", location_id="room",
                         persona={"voice": "terse"}))
    world.add_entity(Npc(id="wren", name="Wren the Wayfinder", location_id="room",
                         persona={"voice": "warm"}))
    return Engine(world, FakeProvider(), start_location="room")


def build_item_engine():
    """One room, NPC Wren present, a lantern on the floor — for the player's
    take -> inventory -> give loop, give routed through the action seam."""
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    world.add_entity(Npc(id="wren", name="Wren", location_id="room",
                         persona={"voice": "warm"}))
    world.add_entity(Item(id="lantern", name="a rusty lantern", holder="room",
                          aliases=["lantern", "lamp"]))
    return Engine(world, FakeProvider(), start_location="room")


class SilentProvider:
    """A provider whose NPC always chooses silence — an empty structured turn."""
    name = "silent"

    async def complete(self, system, messages, schema=None):
        return '{"speech":"","actions":[]}'


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

    async def test_move_broadcasts_departure_to_origin_and_arrival_to_dest(self):
        engine = build_two_room_engine()
        s1 = FakeSession("s1")                      # stays in Room A with Odd
        s2 = FakeSession("s2")                      # will stand in Room B
        await engine.on_connect(s1)
        await engine.on_connect(s2)
        await engine.on_input(s2, "north")          # walk s2 into Room B
        s2.sent.clear()                             # ignore that room's own text

        await engine.on_input(s1, "say please leave")
        await self._drain(engine)

        # The NPC actually relocated in the world model.
        self.assertEqual(engine.world.entities["odd"].location_id, "b")
        self.assertIn("odd", engine.world.locations["b"].occupants)
        self.assertNotIn("odd", engine.world.locations["a"].occupants)

        # Origin sees the departure only; destination sees the arrival only.
        origin, dest = s1.texts(), s2.texts()
        self.assertIn("Odd leaves, heading north.", origin)
        self.assertNotIn("arrives", origin)
        self.assertIn("Odd arrives from the south.", dest)
        self.assertNotIn("leaves", dest)

        # The actor remembers having moved.
        self.assertTrue(any(m.kind == "action"
                            for m in engine.minds["odd"].memory.entries))

    async def test_move_with_no_exit_is_silent_and_safe(self):
        # Single room, no exits: the fake proposes no move, nothing breaks.
        engine = build_engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "say please leave")
        await self._drain(engine)
        self.assertEqual(engine.world.entities["odd"].location_id, "room")
        self.assertNotIn("leaves", s.texts())

    async def test_directed_address_engages_only_the_named_npc(self):
        # Naming Wren gates Odd out entirely: no beat, no line, no LLM call.
        engine = build_two_npc_engine()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()                              # drop the look/occupant listing
        await engine.on_input(s, "say Wren, are you there?")
        await self._drain(engine)

        out = s.texts()
        self.assertIn("Wren", out)                  # the named one answered
        self.assertNotIn("Odd", out)                # the bystander stayed out
        systems = [d for (c, d) in s.sent if c == Channel.SYSTEM]
        self.assertTrue(any("Wren" in d for d in systems))   # Wren's beat present
        self.assertFalse(any("Odd" in d for d in systems))   # no beat for Odd

    async def test_undirected_remark_lets_every_npc_consider(self):
        engine = build_two_npc_engine()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.on_input(s, "say hello, well met")
        await self._drain(engine)
        out = s.texts()
        self.assertIn("Wren", out)                  # both were let through
        self.assertIn("Odd", out)

    async def test_chosen_silence_emits_no_line(self):
        # Odd's mind returns an empty turn: the engine renders nothing for him,
        # while Wren still answers. Silence is honored, not a stock line.
        engine = build_two_npc_engine()
        engine.minds["odd"].provider = SilentProvider()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.on_input(s, "say hello, well met")
        await self._drain(engine)
        out = s.texts()
        self.assertIn("Wren", out)
        self.assertNotIn("Odd the Hermit:", out)    # Odd spoke no line
        self.assertFalse(any(m.kind == "action"
                             for m in engine.minds["odd"].memory.entries))


class EngineInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_look_lists_floor_items(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)                  # on_connect calls _look
        self.assertIn("a rusty lantern", s.texts())

    async def test_take_then_inventory(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.on_input(s, "take lantern")
        self.assertIn("You take a rusty lantern.", s.texts())
        self.assertEqual(engine.world.entities["lantern"].holder, s.player_id)
        s.sent.clear()
        await engine.on_input(s, "inventory")
        self.assertIn("a rusty lantern", s.texts())

    async def test_take_unknown_item_is_safe(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.on_input(s, "take dragon")
        self.assertIn("no", s.texts().lower())
        self.assertEqual(engine.world.entities["lantern"].holder, "room")

    async def test_give_routes_through_seam_and_rehomes(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "take lantern")
        s.sent.clear()
        await engine.on_input(s, "give lantern to Wren")
        # Re-homed to the NPC via the same give_item action an NPC would use.
        self.assertEqual(engine.world.entities["lantern"].holder, "wren")
        self.assertIn("gives a rusty lantern to Wren", s.texts())

    async def test_give_without_holding_fails_gracefully(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.on_input(s, "give lantern to Wren")   # never picked it up
        self.assertIn("can't give", s.texts().lower())
        self.assertEqual(engine.world.entities["lantern"].holder, "room")

    async def test_give_bad_syntax_prompts(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.on_input(s, "give lantern")           # no "to <who>"
        self.assertIn("Give what to whom", s.texts())

    async def test_drop_returns_item_to_the_floor(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "take lantern")
        s.sent.clear()
        await engine.on_input(s, "drop lantern")
        self.assertIn("You drop a rusty lantern.", s.texts())
        self.assertEqual(engine.world.entities["lantern"].holder, "room")


if __name__ == "__main__":
    unittest.main()
