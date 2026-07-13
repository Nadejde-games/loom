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


class EngineConditionPerceptionTests(unittest.IsolatedAsyncioTestCase):
    """A standing condition the director set must surface in every perception:
    the player's look, the NPC's Scene, and the director's own snapshot."""

    async def test_look_appends_standing_condition(self):
        engine = build_engine()
        engine.world.conditions.set("room", "storm", "Rain hammers the roof.")
        s = FakeSession()
        await engine.on_connect(s)                  # on_connect calls _look
        out = s.texts()
        self.assertIn("A bare room.", out)          # base description still shows
        self.assertIn("Rain hammers the roof.", out)  # …with the condition after it

    async def test_look_still_clean_with_no_conditions(self):
        engine = build_engine()
        s = FakeSession()
        await engine.on_connect(s)
        self.assertIn("A bare room.", s.texts())

    def test_scene_for_carries_conditions_to_the_npc(self):
        engine = build_engine()
        engine.world.conditions.set("room", "storm", "Rain hammers the roof.")
        npc = engine.world.entities["odd"]
        scene = engine._scene_for(npc, "room")
        self.assertEqual(scene.conditions, ["Rain hammers the roof."])

    def test_world_snapshot_shows_condition_with_its_tag(self):
        engine = build_engine()
        # A player must be present for the room to appear in the snapshot at all.
        from loom.world import Player
        engine.world.add_entity(Player(id="p1", name="P", location_id="room"))
        engine.world.conditions.set("room", "storm", "Rain hammers the roof.")
        snap = engine.world_snapshot()
        self.assertIn("storm", snap)                # the tag — the clear handle
        self.assertIn("Rain hammers the roof.", snap)


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
        # The actor gets a second-person acknowledgement (the room hears the
        # third-person narration, from which the actor's own session is excluded).
        self.assertIn("You give a rusty lantern to Wren.", s.texts())

    async def test_give_without_holding_fails_gracefully(self):
        engine = build_item_engine()
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.on_input(s, "give lantern to Wren")   # never picked it up
        # Caught at resolution (the player isn't holding it) with a clear reason.
        self.assertIn("not carrying", s.texts().lower())
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


class EngineB1Tests(unittest.IsolatedAsyncioTestCase):
    """The richer parser (B1): phrasing tolerance, multi-object take-from,
    examine, disambiguation, and unknown handling — all through on_input."""

    async def _connect(self, engine):
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        return s

    async def test_take_synonyms(self):
        for verb in ("grab lantern", "get lantern", "pick up the lantern"):
            with self.subTest(verb=verb):
                engine = build_item_engine()
                s = await self._connect(engine)
                await engine.on_input(s, verb)
                self.assertIn("You take a rusty lantern.", s.texts())
                self.assertEqual(engine.world.entities["lantern"].holder,
                                 s.player_id)

    async def test_take_from_a_source(self):
        engine = build_item_engine()
        engine.world.add_entity(Item(id="map", name="a worn map", holder="wren",
                                     aliases=["map"]))
        s = await self._connect(engine)
        await engine.on_input(s, "take map from Wren")
        self.assertIn("You take a worn map from Wren.", s.texts())
        self.assertEqual(engine.world.entities["map"].holder, s.player_id)

    async def test_put_down_synonym(self):
        engine = build_item_engine()
        s = await self._connect(engine)
        await engine.on_input(s, "take lantern")
        s.sent.clear()
        await engine.on_input(s, "put down lantern")
        self.assertIn("You drop a rusty lantern.", s.texts())
        self.assertEqual(engine.world.entities["lantern"].holder, "room")

    async def test_examine_a_described_thing(self):
        engine = build_item_engine()
        engine.world.add_entity(Item(id="orb", name="a glowing orb", holder="room",
                                     description="It pulses with a faint light.",
                                     aliases=["orb"]))
        s = await self._connect(engine)
        await engine.on_input(s, "look at orb")
        self.assertIn("It pulses with a faint light.", s.texts())

    async def test_examine_undescribed_thing(self):
        engine = build_item_engine()
        s = await self._connect(engine)
        await engine.on_input(s, "examine lantern")     # lantern has no description
        self.assertIn("nothing special", s.texts().lower())

    async def test_examine_absent_thing(self):
        engine = build_item_engine()
        s = await self._connect(engine)
        await engine.on_input(s, "look at dragon")
        self.assertIn('no "dragon"', s.texts())

    async def test_disambiguation_prompts_which(self):
        engine = build_item_engine()
        engine.world.add_entity(Item(id="lantern2", name="a brass lantern",
                                     holder="room", aliases=["lantern"]))
        s = await self._connect(engine)
        await engine.on_input(s, "take lantern")        # two match "lantern"
        self.assertIn("Which do you mean", s.texts())
        # Nothing moved on an ambiguous request.
        self.assertEqual(engine.world.entities["lantern"].holder, "room")
        self.assertEqual(engine.world.entities["lantern2"].holder, "room")

    async def test_unknown_command(self):
        engine = build_item_engine()
        s = await self._connect(engine)
        await engine.on_input(s, "frobnicate the widget")
        self.assertIn("Unknown command", s.texts())

    async def test_who_excludes_self(self):
        engine = build_item_engine()
        s = await self._connect(engine)
        await engine.on_input(s, "who")
        out = s.texts()
        self.assertIn("Wren", out)
        self.assertNotIn("Wanderer", out)              # not the player themselves


class CannedProvider:
    """Returns a fixed reply to every complete() — a stand-in for the model in
    the B1b free-text fallback path. Counts calls so a test can assert the model
    was (or was not) consulted."""
    name = "canned"

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def complete(self, system, messages, schema=None):
        self.calls += 1
        return self.reply


class EngineFallbackTests(unittest.IsolatedAsyncioTestCase):
    """The free-text intent fallback (B1b): an unrecognised verb gets one LLM
    interpretation against the command grammar, mapped back onto the same Parse
    dispatch. No NPCs in these worlds, so the only provider call is the fallback."""

    def _item_world(self):
        world = World()
        world.add_location(Location(id="room", name="Room", description="A bare room."))
        world.add_entity(Item(id="lantern", name="a rusty lantern", holder="room",
                              aliases=["lantern", "lamp"]))
        return world

    async def _connect(self, engine):
        s = FakeSession()
        await engine.on_connect(s)
        s.sent.clear()
        return s

    async def test_fallback_maps_unknown_verb_to_action(self):
        provider = CannedProvider('{"command":{"verb":"take","dobj":"lantern"}}')
        engine = Engine(self._item_world(), provider, start_location="room")
        s = await self._connect(engine)
        await engine.on_input(s, "snatch the lantern")   # 'snatch' unknown to the table
        self.assertIn("You take a rusty lantern.", s.texts())
        self.assertEqual(engine.world.entities["lantern"].holder, s.player_id)
        self.assertGreaterEqual(provider.calls, 1)

    async def test_fallback_reaches_a_query_verb(self):
        # Full command vocabulary: the fallback maps onto go, not just actions.
        world = World()
        world.add_location(Location(id="a", name="Room A", description="A.",
                                    exits={"north": "b"}))
        world.add_location(Location(id="b", name="Room B", description="B.",
                                    exits={"south": "a"}))
        provider = CannedProvider('{"command":{"verb":"go","dobj":"north"}}')
        engine = Engine(world, provider, start_location="a")
        s = await self._connect(engine)
        await engine.on_input(s, "wander onward")
        self.assertEqual(engine.players[s.id].location_id, "b")

    async def test_unmappable_falls_back_to_unknown(self):
        provider = CannedProvider("I really cannot tell what you want.")
        engine = Engine(self._item_world(), provider, start_location="room")
        s = await self._connect(engine)
        await engine.on_input(s, "xyzzy plugh")
        self.assertIn("Unknown command", s.texts())

    async def test_fallback_can_be_disabled(self):
        provider = CannedProvider('{"command":{"verb":"take","dobj":"lantern"}}')
        engine = Engine(self._item_world(), provider, start_location="room",
                        intent_fallback=False)
        s = await self._connect(engine)
        await engine.on_input(s, "snatch the lantern")
        self.assertIn("Unknown command", s.texts())
        self.assertEqual(provider.calls, 0)              # the model was never asked
        self.assertEqual(engine.world.entities["lantern"].holder, "room")

    async def test_recognised_verb_never_triggers_fallback(self):
        # A known verb whose object doesn't resolve is deterministic, not LLM'd.
        provider = CannedProvider('{"command":{"verb":"take","dobj":"lantern"}}')
        engine = Engine(self._item_world(), provider, start_location="room")
        s = await self._connect(engine)
        await engine.on_input(s, "take sword")           # 'take' known; there is no sword
        self.assertIn('no "sword"', s.texts())
        self.assertEqual(provider.calls, 0)              # model not consulted


class WorldSnapshotAdjacencyTests(unittest.TestCase):
    """B9 off-screen staging: world_snapshot(include_adjacent=True) surfaces the
    *empty* rooms one exit from the players — where they may step next — so the
    director can foreshadow there; off by default, the snapshot is unchanged."""

    def _two_rooms(self):
        from loom.world import Player
        world = World()
        world.add_location(Location(id="clearing", name="The Clearing",
                                    description="Open grass.",
                                    exits={"north": "grove"}))
        world.add_location(Location(id="grove", name="The Grove",
                                    description="Dark trees.",
                                    exits={"south": "clearing"}))
        world.add_entity(Player(id="p1", name="P", location_id="clearing"))
        return Engine(world, FakeProvider(), start_location="clearing")

    def test_default_hides_the_empty_room_ahead(self):
        engine = self._two_rooms()
        snap = engine.world_snapshot()
        self.assertIn("clearing", snap)
        self.assertNotIn("The Grove", snap)              # empty, unoccupied -> hidden
        self.assertNotIn("ahead", snap)

    def test_include_adjacent_shows_the_room_ahead_marked(self):
        engine = self._two_rooms()
        snap = engine.world_snapshot(include_adjacent=True)
        self.assertIn("- clearing (The Clearing): P", snap)   # occupied, as before
        self.assertIn("grove (The Grove) [ahead, empty]", snap)
        self.assertIn("exits: south", snap)              # the ahead room's own detail

    def test_occupied_adjacent_room_is_not_relisted_as_ahead(self):
        from loom.world import Player
        engine = self._two_rooms()
        engine.world.add_entity(Player(id="p2", name="Q", location_id="grove"))
        snap = engine.world_snapshot(include_adjacent=True)
        self.assertIn("- grove (The Grove): Q", snap)    # occupied -> a normal line
        self.assertNotIn("[ahead, empty]", snap)         # never both

    def test_ahead_room_carries_its_standing_condition(self):
        engine = self._two_rooms()
        engine.world.conditions.set("grove", "mist", "A cold mist hangs here.")
        snap = engine.world_snapshot(include_adjacent=True)
        self.assertIn("grove (The Grove) [ahead, empty]", snap)
        self.assertIn('mist ("A cold mist hangs here.")', snap)


if __name__ == "__main__":
    unittest.main()
