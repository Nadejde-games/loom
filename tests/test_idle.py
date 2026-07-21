"""Idle-NPC autonomy (Phase 5, slice 4), offline: the ``Idler``'s plumbing and its
rails — the per-room quiet clock read off the chronicle, the per-room audience gate,
the per-NPC cooldown, one stir per pulse, non-overlap, and the ``wanders`` hard rail
— plus ``NpcMind.stir`` and the ``wanders`` flag's load. All deterministic: scripted
minds, no GPU, no network. Whether a stir is *appropriate* and *goal-shaped* is the
behavioral harness's job (``scripts/behavior_probe.py``); this proves the mechanism.
"""
import asyncio
import json
import os
import tempfile
import unittest

from loom.world import World, Location, Npc, Player
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.ai.mind import NpcMind
from loom.ai.idle import Idler, _MOVE
from loom.content import load_world


def _turn(speech="", actions=None):
    return json.dumps({"speech": speech, "actions": actions or []})


class Speaks:
    """A mind that always stirs with a fixed spoken line."""
    name = "speaks"
    def __init__(self, line):
        self.line = line
    async def complete(self, system, messages, schema=None, temperature=None):
        return _turn(self.line)


class Silent:
    """A mind that always stays silent (an empty turn)."""
    name = "silent"
    async def complete(self, system, messages, schema=None, temperature=None):
        return _turn("")


class Moves:
    """A mind that speaks and proposes a move in ``direction`` — to prove the
    ``wanders`` rail: kept for a roamer, stripped for an anchored NPC."""
    name = "moves"
    def __init__(self, line, direction):
        self.line, self.direction = line, direction
    async def complete(self, system, messages, schema=None, temperature=None):
        return _turn(self.line, [{"name": "move", "args": {"direction": self.direction}}])


class Keyed:
    """Reacts with ``line`` only when ``trigger`` appears in the prompt — so a stir
    can be shown to spawn a cascade (a bystander answering the unbidden beat)."""
    name = "keyed"
    def __init__(self, trigger, line):
        self.trigger, self.line = trigger, line
    async def complete(self, system, messages, schema=None, temperature=None):
        blob = " ".join(m.get("content", "") for m in messages)
        return _turn(self.line) if self.trigger in blob else _turn("")


def _build(npcs, *, autonomous=False, with_player=True,
           exits=None, extra_locs=None):
    """One room with the given NPCs (each ``{provider, name?, wanders?}``), a player
    present by default, and any extra adjacent rooms for a wander test."""
    world = World()
    world.add_location(Location(id="room", name="Room",
                                description="A bare room.", exits=exits or {}))
    for lid in (extra_locs or []):
        world.add_location(Location(id=lid, name=lid.capitalize(), description=""))
    for nid, spec in npcs.items():
        world.add_entity(Npc(id=nid, name=spec.get("name", nid.capitalize()),
                             location_id="room",
                             wanders=spec.get("wanders", False)))
    engine = Engine(world, FakeProvider(), start_location="room",
                    autonomous_reactions=autonomous)
    for nid, spec in npcs.items():
        engine.minds[nid] = NpcMind(world.entities[nid], spec["provider"],
                                    registry=engine.actions,
                                    offered=engine.npc_actions)
    if with_player:
        world.add_entity(Player(id="player:tester", name="Tester",
                                location_id="room"))
    return engine


def _speech(engine):
    return [e.text for e in engine.chronicle.recent() if e.kind == "speech"]


async def _drain(engine):
    for _ in range(50):
        tasks = list(engine._tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks)


async def _pulse(engine, idler, n=1):
    """Run ``n`` idler ticks and drain any stir (and cascade) each raises. Assumes
    ``period_ticks == 1`` so every tick is a pulse."""
    for _ in range(n):
        await idler.tick(1.0)
        await _drain(engine)


class PrimitiveTests(unittest.TestCase):
    def test_npc_wanders_defaults_false(self):
        self.assertFalse(Npc(id="x", name="X").wanders)

    def test_wanders_loads_from_json(self):
        data = {"start_location": "r",
                "locations": [{"id": "r", "name": "R"}],
                "npcs": [{"id": "a", "name": "A", "location": "r", "wanders": True},
                         {"id": "b", "name": "B", "location": "r"}]}
        d = tempfile.mkdtemp()
        p = os.path.join(d, "w.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
        world, _start = load_world(p)
        self.assertTrue(world.entities["a"].wanders)
        self.assertFalse(world.entities["b"].wanders)

    def test_attach_installs_on_loop(self):
        engine = _build({"odd": {"provider": Speaks("x")}})

        class L:
            def __init__(self):
                self.systems = []
            def add_system(self, fn):
                self.systems.append(fn)
        loop = L()
        idler = engine.attach_idler(loop, period_ticks=5, quiet_pulses=2)
        self.assertIs(engine.idler, idler)
        self.assertEqual(len(loop.systems), 1)


class StirCognitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stir_records_engaged_speech(self):
        engine = _build({"aoife": {"provider": Speaks("The spring lies east."),
                                   "name": "Aoife"}})
        mind = engine.minds["aoife"]
        turn = await mind.stir(None, may_wander=False)
        self.assertEqual(turn.speech, "The spring lies east.")
        # An unbidden line is recorded, marked so recall can tell it from a reply.
        self.assertTrue(any("Unbidden" in e.text for e in mind.memory.entries))

    async def test_silent_stir_records_nothing(self):
        engine = _build({"odd": {"provider": Silent()}})
        mind = engine.minds["odd"]
        turn = await mind.stir(None)
        self.assertTrue(turn.is_silent)
        self.assertEqual(mind.memory.entries, [])


class QuietClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_stirs_after_quiet_pulses(self):
        engine = _build({"odd": {"provider": Speaks("Cold night.")}})
        idler = Idler(engine, period_ticks=1, quiet_pulses=2, cooldown_pulses=2)
        await _pulse(engine, idler, 1)          # first sight: idle 0
        self.assertEqual(_speech(engine), [])
        await _pulse(engine, idler, 1)          # idle 1 — not yet
        self.assertEqual(_speech(engine), [])
        await _pulse(engine, idler, 1)          # idle 2 — stir
        self.assertIn("Odd: Cold night.", _speech(engine))

    async def test_activity_blocks_stir(self):
        engine = _build({"odd": {"provider": Speaks("Cold night.")}})
        idler = Idler(engine, period_ticks=1, quiet_pulses=2)
        for i in range(6):                       # a live scene: an event every pulse
            engine.chronicle.record(f"the player fidgets {i}",
                                    location_id="room", kind="event")
            await _pulse(engine, idler, 1)
        self.assertEqual(_speech(engine), [])    # never crossed the quiet bar
        await _pulse(engine, idler, 3)           # goes quiet -> stirs
        self.assertIn("Odd: Cold night.", _speech(engine))

    async def test_director_beat_resets_idle(self):
        """A director beat in the room (a real one records kind='action') is genuine
        scene activity: it advances the substantive chronicle, so the Idler's quiet
        clock resets and it does not double-stir — the signed-off mutual suppression."""
        engine = _build({"odd": {"provider": Speaks("Cold night.")}})
        idler = Idler(engine, period_ticks=1, quiet_pulses=2, cooldown_pulses=2)
        await _pulse(engine, idler, 2)           # idle climbs to 1 (not yet)
        self.assertEqual(_speech(engine), [])
        engine.chronicle.record("a shape moves at the treeline",
                                location_id="room", kind="action")
        await _pulse(engine, idler, 1)           # observes the beat -> idle resets
        self.assertEqual(_speech(engine), [])
        await _pulse(engine, idler, 2)           # quiet again -> stir
        self.assertIn("Odd: Cold night.", _speech(engine))

    async def test_ambient_beat_does_not_reset(self):
        """Pure atmosphere (weather/time, kind='ambient') does NOT reset the quiet
        clock — a character may stir *as the light fades*. Without this the running
        clock + weather would starve the Idler in a live world (the field-observed bug)."""
        engine = _build({"odd": {"provider": Speaks("Cold night.")}})
        idler = Idler(engine, period_ticks=1, quiet_pulses=2, cooldown_pulses=2)
        await _pulse(engine, idler, 1)           # first sight: idle 0
        engine.chronicle.record("Rain begins to fall.",
                                location_id="room", kind="ambient")
        await _pulse(engine, idler, 1)           # ambient ignored -> idle climbs to 1
        self.assertEqual(_speech(engine), [])
        engine.chronicle.record("Dusk gathers in the valley.",
                                location_id="room", kind="ambient")
        await _pulse(engine, idler, 1)           # still ignored -> idle 2 -> stir anyway
        self.assertIn("Odd: Cold night.", _speech(engine))

    async def test_period_ticks_cadence(self):
        engine = _build({"odd": {"provider": Speaks("Cold.")}})
        idler = Idler(engine, period_ticks=3, quiet_pulses=1)
        for _ in range(5):                       # tick 3 is the only pulse so far
            await idler.tick(1.0)
            await _drain(engine)
        self.assertEqual(_speech(engine), [])    # one pulse, idle 0 — not yet
        await idler.tick(1.0)                     # tick 6: second pulse, idle 1 -> stir
        await _drain(engine)
        self.assertIn("Odd: Cold.", _speech(engine))


class AudienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_stir_without_player(self):
        engine = _build({"odd": {"provider": Speaks("Cold night.")}},
                        with_player=False)
        idler = Idler(engine, period_ticks=1, quiet_pulses=1)
        await _pulse(engine, idler, 5)
        self.assertEqual(_speech(engine), [])    # no one to witness -> no beat
        engine.world.add_entity(Player(id="player:t", name="T", location_id="room"))
        await _pulse(engine, idler, 3)
        self.assertIn("Odd: Cold night.", _speech(engine))


class RestraintTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_room_stays_silent(self):
        engine = _build({"odd": {"provider": Silent()}})
        idler = Idler(engine, period_ticks=1, quiet_pulses=1)
        await _pulse(engine, idler, 8)
        self.assertEqual(_speech(engine), [])    # a mind that chooses silence: nothing

    async def test_running_blocks_new_pulse(self):
        engine = _build({"odd": {"provider": Speaks("x")}})
        idler = Idler(engine, period_ticks=1, quiet_pulses=1)
        idler._running = True                    # a stir already in flight
        await idler.tick(1.0)
        self.assertEqual(engine._tasks, set())   # no second stir spawned
        self.assertEqual(_speech(engine), [])

    async def test_one_stir_per_pulse_and_rotates(self):
        engine = _build({"odd": {"provider": Speaks("A."), "name": "Odd"},
                         "wren": {"provider": Speaks("B."), "name": "Wren"}})
        idler = Idler(engine, period_ticks=1, quiet_pulses=1, cooldown_pulses=3)
        await _pulse(engine, idler, 1)           # first sight
        await _pulse(engine, idler, 1)           # one — and only one — stirs
        self.assertEqual(len(_speech(engine)), 1)
        await _pulse(engine, idler, 8)           # the other gets its turn later
        speakers = set(_speech(engine))
        self.assertIn("Odd: A.", speakers)
        self.assertIn("Wren: B.", speakers)


class WanderTests(unittest.IsolatedAsyncioTestCase):
    async def test_wanderer_moves_to_exit(self):
        engine = _build({"wren": {"provider": Moves("To the ridge, then.", "north"),
                                  "name": "Wren", "wanders": True}},
                        exits={"north": "other"}, extra_locs=["other"])
        idler = Idler(engine, period_ticks=1, quiet_pulses=1)
        await _pulse(engine, idler, 3)
        self.assertEqual(engine.world.entities["wren"].location_id, "other")

    async def test_anchored_npc_move_stripped(self):
        engine = _build({"odd": {"provider": Moves("I'll not leave my cave.", "north"),
                                 "name": "Odd", "wanders": False}},
                        exits={"north": "other"}, extra_locs=["other"])
        idler = Idler(engine, period_ticks=1, quiet_pulses=1)
        await _pulse(engine, idler, 3)
        # The hard rail: the move is dropped, the character stays — but still speaks.
        self.assertEqual(engine.world.entities["odd"].location_id, "room")
        self.assertIn("Odd: I'll not leave my cave.", _speech(engine))

    def test_move_constant(self):
        self.assertEqual(_MOVE, "move")


class CascadeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stir_spawns_cascade(self):
        """An unbidden beat is an event the room may answer — a stir delivered
        through ``_deliver_turn`` then spawns a reaction cascade like any turn."""
        engine = _build(
            {"odd": {"provider": Speaks("Storm's coming."), "name": "Odd"},
             "wren": {"provider": Keyed("Storm", "Aye, batten down."), "name": "Wren"}},
            autonomous=True)
        idler = Idler(engine, period_ticks=1, quiet_pulses=1, cooldown_pulses=5)
        await _pulse(engine, idler, 2)           # odd (lowest id) stirs; wren reacts
        speech = _speech(engine)
        self.assertIn("Odd: Storm's coming.", speech)
        self.assertIn("Wren: Aye, batten down.", speech)


if __name__ == "__main__":
    unittest.main()
