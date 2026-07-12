"""Autonomous NPC reactions (B9), offline: the engine's cascade mechanics and its
rails — self-guard, shared decaying budget, per-NPC cooldown, and the hard fuse —
plus the two triggers (a director world-change, and an NPC's own reply). All
deterministic: scripted minds, no GPU, no network. The mind's *choice* to react
appropriately is the behavioral harness's job; this proves the plumbing and that a
cascade always terminates.
"""
import asyncio
import json
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine, _Cascade
from loom.ai import FakeProvider
from loom.ai.mind import NpcMind
from loom.action import ActionIntent


def _turn(speech="", actions=None):
    return json.dumps({"speech": speech, "actions": actions or []})


class Always:
    """A mind provider that always reacts with a fixed line."""
    name = "always"
    def __init__(self, line):
        self.line = line
    async def complete(self, system, messages, schema=None):
        return _turn(self.line)


class Silent:
    """A mind provider that always stays silent (an empty turn)."""
    name = "silent"
    async def complete(self, system, messages, schema=None):
        return _turn("")


class Keyed:
    """Reacts with ``line`` only when ``trigger`` appears in the prompt, else stays
    silent — so a test can make an NPC react to *one specific* event (e.g. only to
    the storm, or only to another NPC's line) and prove a cascade hop."""
    name = "keyed"
    def __init__(self, trigger, line):
        self.trigger, self.line = trigger, line
    async def complete(self, system, messages, schema=None):
        blob = " ".join(m.get("content", "") for m in messages)
        return _turn(self.line) if self.trigger in blob else _turn("")


def _engine(npc_providers, autonomous=True):
    """One room populated with NPCs, each driven by the given provider."""
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    for nid in npc_providers:
        world.add_entity(Npc(id=nid, name=nid.capitalize(), location_id="room"))
    engine = Engine(world, FakeProvider(), start_location="room",
                    autonomous_reactions=autonomous)
    for nid, prov in npc_providers.items():
        engine.minds[nid] = NpcMind(world.entities[nid], prov,
                                    registry=engine.actions, offered=engine.npc_actions)
    return engine


def _speech(engine):
    return [e.text for e in engine.chronicle.recent() if e.kind == "speech"]


async def _drain(engine):
    for _ in range(50):
        if not engine._tasks:
            return
        await asyncio.gather(*list(engine._tasks))


class ReactionCoreTests(unittest.TestCase):
    def test_a_reaction_is_delivered_and_chronicled(self):
        async def go():
            engine = _engine({"odd": Always("Best find shelter."), "wren": Silent()})
            await engine._react_to_event("room", "director", "A storm begins.",
                                         _Cascade(budget=5, fuse=16))
            lines = _speech(engine)
            self.assertIn("Odd: Best find shelter.", lines)
            self.assertFalse(any(l.startswith("Wren:") for l in lines))
        asyncio.run(go())

    def test_all_silent_produces_nothing(self):
        async def go():
            engine = _engine({"odd": Silent(), "wren": Silent()})
            await engine._react_to_event("room", "director", "A leaf falls.",
                                         _Cascade(budget=5, fuse=16))
            self.assertEqual(_speech(engine), [])
        asyncio.run(go())

    def test_self_guard_source_never_reacts_to_its_own_event(self):
        async def go():
            engine = _engine({"odd": Always("x")})   # odd is the only NPC present
            await engine._react_to_event("room", "odd", "Odd stirs.",
                                         _Cascade(budget=5, fuse=16))
            self.assertEqual(_speech(engine), [])    # the source is skipped
        asyncio.run(go())

    def test_cascade_npc_reacts_to_another_npc(self):
        async def go():
            engine = _engine({"odd": Keyed("storm", "The wind bites."),
                              "wren": Keyed("Odd said", "Aye, best move.")})
            await engine._react_to_event("room", "director", "A storm begins.",
                                         _Cascade(budget=5, fuse=16))
            lines = _speech(engine)
            self.assertIn("Odd: The wind bites.", lines)
            # Wren reacted to *Odd's line*, not the storm — a genuine NPC->NPC hop.
            wren_at = next(i for i, l in enumerate(lines) if "best move" in l)
            odd_at = lines.index("Odd: The wind bites.")
            self.assertLess(odd_at, wren_at)
        asyncio.run(go())


class ReactionRailTests(unittest.TestCase):
    def test_budget_caps_total_reactions(self):
        async def go():
            engine = _engine({"odd": Always("A"), "wren": Always("B")})
            await engine._react_to_event("room", "director", "E",
                                         _Cascade(budget=1, fuse=16))
            self.assertEqual(len(_speech(engine)), 1)   # budget 1 -> exactly one
        asyncio.run(go())

    def test_fuse_caps_a_would_be_runaway(self):
        async def go():
            # Two NPCs who each always react to the other = infinite without a rail.
            engine = _engine({"odd": Always("A"), "wren": Always("B")})
            await engine._react_to_event("room", "director", "E",
                                         _Cascade(budget=999, fuse=3))
            self.assertEqual(len(_speech(engine)), 3)   # the fuse stops it
        asyncio.run(go())

    def test_cooldown_stops_an_npc_reacting_again_too_soon(self):
        async def go():
            engine = _engine({"odd": Always("A"), "wren": Always("B")})
            engine.react_cooldown = 99      # once reacted, out for this whole cascade
            await engine._react_to_event("room", "director", "E",
                                         _Cascade(budget=999, fuse=999))
            lines = _speech(engine)
            # Under a long cooldown each NPC reacts at most once, so the otherwise
            # unbounded exchange settles at two lines.
            self.assertEqual(len(lines), 2)
            self.assertEqual(set(lines), {"Odd: A", "Wren: B"})
        asyncio.run(go())


class ReactionTriggerTests(unittest.TestCase):
    def _director(self):
        return type("D", (), {"id": "director", "name": "the Director"})()

    def test_director_world_change_triggers_a_cascade(self):
        async def go():
            engine = _engine({"wren": Keyed("rain", "Aye, best move.")})
            intent = ActionIntent(name="set_condition",
                                  args={"location": "room", "tag": "storm",
                                        "text": "A cold rain falls."})
            await engine._perform(None, self._director(), None, intent)
            await _drain(engine)
            self.assertEqual(engine.world.conditions.texts("room"),
                             ["A cold rain falls."])
            self.assertTrue(any("best move" in l for l in _speech(engine)))
        asyncio.run(go())

    def test_an_npc_reply_triggers_a_cascade(self):
        async def go():
            # Odd answers the player; Wren then reacts to Odd's reply (the reply hook).
            engine = _engine({"odd": Always("Hello there."),
                              "wren": Keyed("Odd said", "Welcome, friend.")})
            await engine._deliver_npc_reply("room", engine.world.entities["odd"],
                                            engine.minds["odd"], "Player", "hi",
                                            addressed=True)
            await _drain(engine)
            lines = _speech(engine)
            self.assertIn("Odd: Hello there.", lines)
            self.assertTrue(any("Welcome" in l for l in lines))
        asyncio.run(go())

    def test_disabled_engine_never_cascades(self):
        async def go():
            engine = _engine({"wren": Always("hi")}, autonomous=False)
            intent = ActionIntent(name="set_condition",
                                  args={"location": "room", "tag": "storm",
                                        "text": "A cold rain falls."})
            await engine._perform(None, self._director(), None, intent)
            await _drain(engine)
            self.assertEqual(_speech(engine), [])       # opt-in off -> no reactions
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
