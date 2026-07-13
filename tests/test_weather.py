"""Probabilistic weather (B9), offline: the bounded random walk and its bridge into
the world. Deterministic — the walk is driven by a *scripted* RNG (every roll's
change-check and direction is controlled), and rolls are counted in injected pulses,
so the whole sequence is reproducible. Proves the mechanics (holding, the period gate,
bounded stepping at each end, the standing-condition turn, the perceivable-only beat,
clear lifting the tag, the reaction trigger); the mind's *choice* to react to a storm
is already the behavioral harness's `npc.reacts-to-world`.
"""
import asyncio
import json
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine
from loom.weather import WeatherSystem, Weather
from loom.ai import FakeProvider
from loom.ai.mind import NpcMind


def _turn(speech="", actions=None):
    return json.dumps({"speech": speech, "actions": actions or []})


class Always:
    name = "always"
    def __init__(self, line):
        self.line = line
    async def complete(self, system, messages, schema=None):
        return _turn(self.line)


class ScriptRng:
    """A deterministic stand-in for ``random.Random``: ``.random()`` yields scripted
    values (cycling), so a test controls every roll — the change-check first, then the
    direction (consumed only at an interior node)."""
    def __init__(self, *values):
        self._v = list(values) or [0.0]
        self._i = 0
    def random(self):
        v = self._v[self._i % len(self._v)]
        self._i += 1
        return v


class _StubLoop:
    def __init__(self):
        self.systems = []
    def add_system(self, fn):
        self.systems.append(fn)


# clear (no standing condition) ↔ cloudy ↔ rain ↔ storm — a four-link chain.
STATES = [
    Weather("clear", "", "The sky clears."),
    Weather("cloudy", "Grey cloud lies over the hills.", "Cloud draws over the sky."),
    Weather("rain", "A cold rain is falling.", "Rain begins to fall."),
    Weather("storm", "A hard storm drives down.", "The wind rises to a howl."),
]


def _engine(npc_providers=None):
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    for nid in (npc_providers or {}):
        world.add_entity(Npc(id=nid, name=nid.capitalize(), location_id="room"))
    engine = Engine(world, FakeProvider(), start_location="room",
                    autonomous_reactions=True)
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


class WeatherWalkTests(unittest.TestCase):
    def test_empty_states_rejected(self):
        with self.assertRaises(ValueError):
            WeatherSystem(_engine(), [])

    def test_single_state_never_walks(self):
        async def go():
            eng = _engine({"odd": Always("x")})
            w = WeatherSystem(eng, [Weather("calm", "Still air.", "It is calm.")],
                              period_pulses=1, change_chance=1.0, rng=ScriptRng(0.0))
            w.install(_StubLoop())
            for _ in range(5):
                await w.tick(1.0)
            await _drain(eng)
            self.assertEqual(w.state.name, "calm")
            self.assertEqual(_events(eng, "ambient"), [])   # never a step, never a beat
        asyncio.run(go())

    def test_holds_when_change_chance_zero(self):
        async def go():
            eng = _engine({"odd": Always("x")})
            w = WeatherSystem(eng, STATES, period_pulses=1, change_chance=0.0,
                              start_index=1, rng=ScriptRng(0.5))
            w.install(_StubLoop())
            for _ in range(5):
                await w.tick(1.0)
            await _drain(eng)
            self.assertEqual(w.state.name, "cloudy")        # the sky never shifts
            self.assertEqual(_events(eng, "ambient"), [])
        asyncio.run(go())

    def test_period_pulses_gates_the_roll(self):
        async def go():
            eng = _engine({"odd": Always("x")})
            w = WeatherSystem(eng, STATES, period_pulses=3, change_chance=1.0,
                              start_index=0, rng=ScriptRng(0.0))
            w.install(_StubLoop())
            await w.tick(1.0); await w.tick(1.0)             # 2 < 3 -> no roll yet
            self.assertEqual(w.state.name, "clear")
            await w.tick(1.0)                                 # the 3rd pulse rolls
            self.assertEqual(w.state.name, "cloudy")
        asyncio.run(go())

    def test_steps_inward_from_each_boundary(self):
        async def go():
            low = WeatherSystem(_engine(), STATES, period_pulses=1, change_chance=1.0,
                                start_index=0, rng=ScriptRng(0.0))
            low.install(_StubLoop())
            await low.tick(1.0)
            self.assertEqual(low.state.name, "cloudy")       # 0 (bottom) -> +1
            high = WeatherSystem(_engine(), STATES, period_pulses=1, change_chance=1.0,
                                 start_index=3, rng=ScriptRng(0.0))
            high.install(_StubLoop())
            await high.tick(1.0)
            self.assertEqual(high.state.name, "rain")        # 3 (top) -> -1
        asyncio.run(go())

    def test_deterministic_walk_applies_conditions_and_beats(self):
        async def go():
            eng = _engine({"odd": Always("Best find shelter.")})
            # All-0.0 rng: change always fires, interior always steps up; bounded.
            w = WeatherSystem(eng, STATES, period_pulses=1, change_chance=0.5,
                              start_index=1, rng=ScriptRng(0.0))
            w.install(_StubLoop())
            await w.tick(1.0)                                 # cloudy -> rain
            await w.tick(1.0)                                 # rain -> storm
            await w.tick(1.0)                                 # storm (top) -> rain
            await _drain(eng)
            self.assertEqual(w.state.name, "rain")
            self.assertEqual(eng.world.conditions.world_texts(),
                             ["A cold rain is falling."])     # the standing sky now
            self.assertEqual(_events(eng, "ambient"),
                             ["Rain begins to fall.", "The wind rises to a howl.",
                              "Rain begins to fall."])
            # The world stirred on its own; the NPC answered of its own volition.
            self.assertTrue(any("Best find shelter" in l for l in _speech(eng)))
        asyncio.run(go())

    def test_stepping_into_clear_lifts_the_weather_tag(self):
        async def go():
            eng = _engine({"odd": Always("x")})
            # change fires (0.0), then direction down (0.9): cloudy -> clear.
            w = WeatherSystem(eng, STATES, period_pulses=1, change_chance=0.5,
                              start_index=1, rng=ScriptRng(0.0, 0.9))
            w.install(_StubLoop())
            self.assertEqual(eng.world.conditions.world_texts(),
                             ["Grey cloud lies over the hills."])  # initial cloudy
            await w.tick(1.0)                                 # cloudy -> clear
            await _drain(eng)
            self.assertEqual(w.state.name, "clear")
            self.assertEqual(eng.world.conditions.world_texts(), [])   # tag lifted
            self.assertIn("The sky clears.", _events(eng, "ambient"))
        asyncio.run(go())


class WeatherInstallTests(unittest.TestCase):
    def test_install_sets_initial_weather_silently(self):
        async def go():
            eng = _engine()
            WeatherSystem(eng, STATES, start_index=2).install(_StubLoop())  # rain
            self.assertEqual(eng.world.conditions.world_texts(),
                             ["A cold rain is falling."])
            self.assertEqual(eng.chronicle.recent(), [])     # no startup beat
        asyncio.run(go())

    def test_install_at_clear_sets_nothing(self):
        async def go():
            eng = _engine()
            WeatherSystem(eng, STATES, start_index=0).install(_StubLoop())  # clear
            self.assertEqual(eng.world.conditions.world_texts(), [])
        asyncio.run(go())

    def test_clock_and_weather_conditions_coexist(self):
        # The two world systems key by different tags, so time-of-day and sky both
        # show — the reason apply_world_condition is shared, not clock-specific.
        eng = _engine()
        eng.world.conditions.set_world("time", "Night has fallen.")
        WeatherSystem(eng, STATES, start_index=3).install(_StubLoop())     # storm
        self.assertEqual(eng.world.conditions.world_texts(),
                         ["Night has fallen.", "A hard storm drives down."])


if __name__ == "__main__":
    unittest.main()
