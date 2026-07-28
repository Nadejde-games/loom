"""Resource pools (Phase 9, Tier 0), offline: derived max/regen and clamping, the
qualitative condition cues, the depleted signal, and the regen loop system driven by
injected pulses. Pure, no network.
"""
import asyncio
import unittest

from loom.rpg.pool import Pool, PoolSpec, RegenSystem


HP_CUES = [[100, "unharmed"], [75, "grazed"], [50, "wounded"],
           [25, "badly wounded"], [1, "near death"], [0, "dead"]]


def hp_spec():
    return PoolSpec.from_config("hp", {
        "max": "10 + hit_die*level + con_mod*level",
        "regen": "con_mod + 1", "floor": 0, "policy": "vital", "cues": HP_CUES})


class PoolSpecTests(unittest.TestCase):
    def test_cue_bands(self):
        s = hp_spec()
        self.assertEqual(s.cue_for(50, 50), "unharmed")   # 100%
        self.assertEqual(s.cue_for(45, 50), "grazed")     # 90%
        self.assertEqual(s.cue_for(30, 50), "wounded")    # 60%
        self.assertEqual(s.cue_for(13, 50), "badly wounded")  # 26%
        self.assertEqual(s.cue_for(5, 50), "near death")  # 10%
        self.assertEqual(s.cue_for(0, 50), "dead")        # 0%
        self.assertEqual(s.top_label, "unharmed")

    def test_cue_empty_when_no_capacity_or_no_cues(self):
        self.assertEqual(hp_spec().cue_for(0, 0), "")
        bare = PoolSpec.from_config("x", {"max": "5"})
        self.assertEqual(bare.cue_for(3, 5), "")

    def test_regen_optional(self):
        self.assertIsNone(PoolSpec.from_config("x", {"max": "5"}).regen)
        self.assertIsNone(PoolSpec.from_config("x", {"max": "5", "regen": ""}).regen)


class PoolTests(unittest.TestCase):
    def test_recompute_derives_and_clamps(self):
        p = Pool(spec=hp_spec())
        p.recompute({"hit_die": 8, "level": 1, "con_mod": 2})
        self.assertEqual(p.max, 20)        # 10 + 8 + 2
        self.assertEqual(p.regen, 3)       # con_mod + 1
        self.assertEqual(p.current, 0)     # clamped into [0, 20], started at 0

    def test_set_clamps_and_signals_depletion(self):
        p = Pool(spec=hp_spec())
        p.recompute({"hit_die": 8, "level": 1, "con_mod": 2})
        self.assertFalse(p.set(100))       # clamps to max, not a depletion
        self.assertEqual(p.current, 20)
        self.assertTrue(p.set(0))          # crossed to the floor from above → signal
        self.assertTrue(p.is_depleted())
        self.assertFalse(p.set(0))         # already at floor → no repeat signal

    def test_apply_regen_positive_and_negative(self):
        p = Pool(spec=hp_spec())
        p.recompute({"hit_die": 8, "level": 1, "con_mod": 2})
        p.set(5)
        self.assertTrue(p.apply_regen())   # +3
        self.assertEqual(p.current, 8)
        p.set(20)
        self.assertFalse(p.apply_regen())  # already full → no change

        decay = Pool(spec=PoolSpec.from_config("hunger", {"max": "10", "regen": "-1"}))
        decay.recompute({})
        decay.set(3)
        self.assertTrue(decay.apply_regen())
        self.assertEqual(decay.current, 2)

    def test_notable_suppresses_top_band(self):
        p = Pool(spec=hp_spec())
        p.recompute({"hit_die": 8, "level": 1, "con_mod": 2})
        p.set(20)
        self.assertFalse(p.is_notable())   # unharmed → not surfaced
        p.set(10)
        self.assertTrue(p.is_notable())    # wounded → surfaced
        self.assertEqual(p.condition(), "wounded")


# --- the regen loop system --------------------------------------------------

class _StubSheet:
    def __init__(self):
        self.ticks = 0

    def regen_tick(self):
        self.ticks += 1


class _StubEnt:
    def __init__(self, sheet):
        self.sheet = sheet


class _StubWorld:
    def __init__(self, entities):
        self.entities = entities


class _StubEngine:
    def __init__(self, entities):
        self.world = _StubWorld(entities)


class RegenSystemTests(unittest.TestCase):
    def test_ticks_every_period_and_skips_sheetless(self):
        sheet = _StubSheet()
        engine = _StubEngine({"npc": _StubEnt(sheet), "rock": _StubEnt(None)})
        rs = RegenSystem(engine, period_pulses=2)
        asyncio.run(rs.tick(1.0))
        self.assertEqual(sheet.ticks, 0)   # first pulse of the period — no regen yet
        asyncio.run(rs.tick(1.0))
        self.assertEqual(sheet.ticks, 1)   # period elapsed — one regen step
        asyncio.run(rs.tick(1.0))
        asyncio.run(rs.tick(1.0))
        self.assertEqual(sheet.ticks, 2)   # sheetless "rock" never raised


if __name__ == "__main__":
    unittest.main()
