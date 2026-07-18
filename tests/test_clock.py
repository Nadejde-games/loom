"""The world-clock (B9), offline: the autonomous event source and its bridge into
the world. All deterministic — the clock is driven by *injected* pulses, never
wall-clock, so a boundary crossing is reproducible. Proves the mechanics (phase
advancement, boundary detection, the standing-condition turn, the perceivable-only
ambient beat, the reaction trigger, the world-scope perception fold-in); the mind's
*choice* to react to nightfall is the behavioral harness's job.
"""
import asyncio
import json
import unittest

from loom.style import plain
from loom.world import World, Location, Npc, Player
from loom.engine import Engine
from loom.clock import WorldClock, Phase, MINUTES_PER_DAY
from loom.ai import FakeProvider
from loom.ai.mind import NpcMind


# --- scripted minds, so a reaction is deterministic (mirrors test_reactions) ---
def _turn(speech="", actions=None):
    return json.dumps({"speech": speech, "actions": actions or []})


class Always:
    name = "always"
    def __init__(self, line):
        self.line = line
    async def complete(self, system, messages, schema=None):
        return _turn(self.line)


class _StubLoop:
    """A minimal loop: install() only needs somewhere to register the tick."""
    def __init__(self):
        self.systems = []
    def add_system(self, fn):
        self.systems.append(fn)


# Three phases with room to cross boundaries and to wrap past midnight.
PHASES = [
    Phase("day", 400, "It is day.", "Day breaks over the hills."),
    Phase("dusk", 1000, "It is dusk.", "Dusk gathers in the valley."),
    Phase("night", 1200, "It is night.", "Night comes down cold."),
]


def _engine(npc_providers=None, autonomous=True, empty_room=False):
    """A room 'room' with the given NPCs; optionally a second, empty 'void' to
    prove the perceivable-only rule (no beat where no one is)."""
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    if empty_room:
        world.add_location(Location(id="void", name="Void", description="Nowhere."))
    for nid in (npc_providers or {}):
        world.add_entity(Npc(id=nid, name=nid.capitalize(), location_id="room"))
    engine = Engine(world, FakeProvider(), start_location="room",
                    autonomous_reactions=autonomous)
    for nid, prov in (npc_providers or {}).items():
        engine.minds[nid] = NpcMind(world.entities[nid], prov,
                                    registry=engine.actions, offered=engine.npc_actions)
    return engine


def _events(engine, kind):
    return [e.text for e in engine.chronicle.recent() if e.kind == kind]


def _speech(engine):
    return _events(engine, "speech")


async def _drain(engine):
    for _ in range(50):
        if not engine._tasks:
            return
        await asyncio.gather(*list(engine._tasks))


class ClockAdvanceTests(unittest.TestCase):
    def test_minute_advances_by_dt_times_factor(self):
        clock = WorldClock(_engine(), PHASES, factor=2.0, start_minute=500)
        asyncio.run(clock.tick(10))          # 10s * 2 = 20 game-minutes
        self.assertEqual(clock.minute, 520)

    def test_minute_wraps_at_midnight(self):
        clock = WorldClock(_engine(), PHASES, factor=1.0, start_minute=1430)
        asyncio.run(clock.tick(30))          # 1430 + 30 = 1460 -> 20
        self.assertEqual(clock.minute, 20)

    def test_phase_at_scans_and_wraps(self):
        clock = WorldClock(_engine(), PHASES, start_minute=400)
        self.assertEqual(clock._phase_at(400).name, "day")
        self.assertEqual(clock._phase_at(999).name, "day")
        self.assertEqual(clock._phase_at(1000).name, "dusk")
        self.assertEqual(clock._phase_at(1250).name, "night")
        # Before the first phase's start (the small hours) is the last phase, which
        # runs across the midnight wrap.
        self.assertEqual(clock._phase_at(50).name, "night")

    def test_starts_in_the_phase_of_its_start_minute(self):
        self.assertEqual(WorldClock(_engine(), PHASES, start_minute=1100).phase.name,
                         "dusk")
        # No start given -> the first phase.
        self.assertEqual(WorldClock(_engine(), PHASES).phase.name, "day")

    def test_empty_phase_table_is_rejected(self):
        with self.assertRaises(ValueError):
            WorldClock(_engine(), [])


class ClockInstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_sets_initial_condition_silently(self):
        engine = _engine()
        clock = WorldClock(engine, PHASES, start_minute=1100)   # dusk
        loop = _StubLoop()
        clock.install(loop)
        # The world reads as dusk from the first moment...
        self.assertEqual(engine.world.conditions.world_texts(), ["It is dusk."])
        # ...but nothing was announced (no startup 'dusk falls' beat).
        self.assertEqual(engine.chronicle.recent(), [])
        # And the tick was registered on the loop.
        self.assertEqual(loop.systems, [clock.tick])


class ClockBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_crossing_a_boundary_turns_the_condition(self):
        engine = _engine()
        clock = WorldClock(engine, PHASES, factor=1.0, start_minute=990)  # day
        clock.install(_StubLoop())
        await clock.tick(20)                 # 990 -> 1010, crosses into dusk
        self.assertEqual(clock.phase.name, "dusk")
        self.assertEqual(engine.world.conditions.world_texts(), ["It is dusk."])

    async def test_crossing_lands_one_ambient_beat_where_someone_is(self):
        engine = _engine({"odd": Always("Best find shelter.")}, empty_room=True)
        clock = WorldClock(engine, PHASES, factor=1.0, start_minute=990)
        clock.install(_StubLoop())
        await clock.tick(20)                 # into dusk
        await _drain(engine)
        ambient = _events(engine, "ambient")
        # Exactly one ambient beat, and only into the occupied room (not 'void').
        self.assertEqual(ambient, ["Dusk gathers in the valley."])

    async def test_crossing_offers_the_present_npc_a_reaction(self):
        engine = _engine({"odd": Always("Best find shelter.")})
        clock = WorldClock(engine, PHASES, factor=1.0, start_minute=990)
        clock.install(_StubLoop())
        await clock.tick(20)
        await _drain(engine)
        # The NPC answered the world's turn of its own volition (source 'world' is
        # no NPC, so the self-guard blocks no one).
        self.assertIn("Odd: Best find shelter.", _speech(engine))

    async def test_no_boundary_no_beat(self):
        engine = _engine({"odd": Always("x")})
        clock = WorldClock(engine, PHASES, factor=1.0, start_minute=500)  # mid-day
        clock.install(_StubLoop())
        await clock.tick(20)                 # 500 -> 520, still day
        await _drain(engine)
        self.assertEqual(_events(engine, "ambient"), [])
        self.assertEqual(_speech(engine), [])

    async def test_disabled_reactions_still_beats_but_no_cascade(self):
        engine = _engine({"odd": Always("x")}, autonomous=False)
        clock = WorldClock(engine, PHASES, factor=1.0, start_minute=990)
        clock.install(_StubLoop())
        await clock.tick(20)
        await _drain(engine)
        # The beat lands regardless (the clock is independently valuable)...
        self.assertEqual(_events(engine, "ambient"), ["Dusk gathers in the valley."])
        # ...but with reactions off, no NPC answers.
        self.assertEqual(_speech(engine), [])

    async def test_wrap_from_night_to_dawn_beats(self):
        phases = [Phase("dawn", 300, "It is dawn.", "The dark thins to dawn."),
                  Phase("night", 1200, "It is night.", "Night comes down.")]
        engine = _engine({"odd": Always("x")})
        clock = WorldClock(engine, phases, factor=1.0, start_minute=1439)  # night
        clock.install(_StubLoop())
        self.assertEqual(clock.phase.name, "night")
        await clock.tick(320)                # 1439 -> (1759 % 1440) = 319, into dawn
        await _drain(engine)
        self.assertEqual(clock.phase.name, "dawn")
        self.assertEqual(engine.world.conditions.world_texts(), ["It is dawn."])
        self.assertIn("The dark thins to dawn.", _events(engine, "ambient"))

    async def test_a_big_pulse_that_skips_a_phase_lands_one_beat(self):
        engine = _engine({"odd": Always("x")})
        clock = WorldClock(engine, PHASES, factor=1.0, start_minute=990)  # day
        clock.install(_StubLoop())
        await clock.tick(400)                # 990 -> 1390: past dusk, into night
        await _drain(engine)
        self.assertEqual(clock.phase.name, "night")
        # It lands on the phase it is now in — one beat, dusk passed unremarked.
        self.assertEqual(_events(engine, "ambient"), ["Night comes down cold."])

    async def test_empty_condition_lifts_the_world_tag(self):
        phases = [Phase("day", 400, "It is day.", "Day breaks."),
                  Phase("clear", 800, "", "The last cloud passes.")]
        engine = _engine()
        clock = WorldClock(engine, phases, factor=1.0, start_minute=790)  # day
        clock.install(_StubLoop())
        self.assertEqual(engine.world.conditions.world_texts(), ["It is day."])
        await clock.tick(20)                 # into 'clear' (no standing text)
        self.assertEqual(engine.world.conditions.world_texts(), [])


class ClockPerceptionTests(unittest.IsolatedAsyncioTestCase):
    """The world-scope condition folds into every perception surface, alongside a
    place's own standing conditions."""

    async def test_world_condition_reads_in_look(self):
        engine = _engine()
        engine.world.conditions.set_world("time", "Night has fallen.")
        engine.world.conditions.set("room", "storm", "Rain hammers down.")
        s = _FakeSession()
        await engine.on_connect(s)           # on_connect calls _look
        out = s.texts()
        self.assertIn("A bare room.", out)
        self.assertIn("Night has fallen.", out)   # world-wide, read first
        self.assertIn("Rain hammers down.", out)  # this place's own, read after

    def test_world_condition_in_scene(self):
        engine = _engine({"odd": Always("x")})
        engine.world.conditions.set_world("time", "It is night.")
        engine.world.conditions.set("room", "storm", "Rain hammers down.")
        scene = engine._scene_for(engine.world.entities["odd"], "room")
        self.assertEqual(scene.conditions, ["It is night.", "Rain hammers down."])

    def test_world_condition_heads_the_snapshot(self):
        engine = _engine()
        engine.world.add_entity(Player(id="p1", name="P", location_id="room"))
        engine.world.conditions.set_world("time", "It is night.")
        snap = engine.world_snapshot()
        self.assertIn('Everywhere: time ("It is night.")', snap)


class _FakeSession:
    def __init__(self, sid="s1"):
        self.id = sid
        self.player_id = None
        self.sent = []
        self.closed = False
    async def send(self, channel, data):
        self.sent.append((channel, data))
    async def send_text(self, text):
        from loom.protocol import Channel
        await self.send(Channel.TEXT, text)
    async def send_system(self, text):
        from loom.protocol import Channel
        await self.send(Channel.SYSTEM, text)
    async def close(self):
        self.closed = True
    def texts(self):
        from loom.protocol import Channel
        return "\n".join(plain(d) for (c, d) in self.sent if c == Channel.TEXT)


if __name__ == "__main__":
    unittest.main()
