"""The character sheet and ruleset (Phase 9, Tier 0), offline: sheet construction from
world data, the derivation namespace, pool recompute on level-up, qualitative perception,
the exact `score` render, and live-state persistence round-trips. Pure, no network.
"""
import unittest

from loom.rpg.sheet import Ruleset, Sheet


META = {
    "attributes": [
        {"name": "str", "min": 3, "max": 20, "default": 10},
        {"name": "dex", "min": 3, "max": 20, "default": 10},
        {"name": "con", "min": 3, "max": 20, "default": 10},
        {"name": "int", "min": 3, "max": 20, "default": 10},
        {"name": "wis", "min": 3, "max": 20, "default": 10},
        {"name": "cha", "min": 3, "max": 20, "default": 10},
    ],
    "derived": {
        "str_mod": "(str - 10) // 2", "con_mod": "(con - 10) // 2",
        "wis_mod": "(wis - 10) // 2", "prof": "2 + (level - 1) // 4",
    },
    "hit_die": 8,
    "pools": {
        "hp": {"max": "10 + hit_die*level + con_mod*level", "regen": "con_mod + 1",
               "floor": 0, "policy": "vital",
               "cues": [[100, "unharmed"], [75, "grazed"], [50, "wounded"],
                        [25, "badly wounded"], [1, "near death"], [0, "dead"]]},
        "mana": {"max": "wis_mod*level + 4", "regen": "wis_mod", "floor": 0,
                 "cues": [[100, "clear"], [25, "drained"]]},
    },
    "point_buy": {"budget": 27, "floor": 8, "ceil": 15,
                  "cost": {"8": 0, "10": 2, "14": 7, "15": 9}},
}


def ruleset():
    return Ruleset.from_meta(META)


class RulesetTests(unittest.TestCase):
    def test_from_meta(self):
        rs = ruleset()
        self.assertFalse(rs.is_empty())
        self.assertEqual(rs.stats.names(), ("str", "dex", "con", "int", "wis", "cha"))
        self.assertEqual([p.key for p in rs.pools], ["hp", "mana"])
        self.assertEqual(rs.default_hit_die, 8)

    def test_empty_ruleset(self):
        rs = Ruleset.from_meta(None)
        self.assertTrue(rs.is_empty())
        sheet = rs.make_sheet()
        self.assertEqual(sheet.pools, {})
        self.assertIn("Character sheet", sheet.render())


class SheetDerivationTests(unittest.TestCase):
    def setUp(self):
        self.rs = ruleset()
        self.sheet = self.rs.make_sheet(stats={"con": 14, "wis": 12, "str": 16}, level=1)

    def test_namespace(self):
        ns = self.sheet.namespace()
        self.assertEqual(ns["con_mod"], 2)
        self.assertEqual(ns["wis_mod"], 1)
        self.assertEqual(ns["str_mod"], 3)
        self.assertEqual(ns["prof"], 2)
        self.assertEqual(ns["hit_die"], 8)

    def test_pools_start_full_from_derivations(self):
        self.assertEqual(self.sheet.pools["hp"].max, 20)     # 10 + 8 + 2
        self.assertEqual(self.sheet.pools["hp"].current, 20)
        self.assertEqual(self.sheet.pools["hp"].regen, 3)
        self.assertEqual(self.sheet.pools["mana"].max, 5)    # 1 + 4
        self.assertEqual(self.sheet.pools["mana"].current, 5)

    def test_level_up_grows_pools_via_recompute(self):
        self.sheet.level = 2
        self.sheet.recompute()
        self.assertEqual(self.sheet.pools["hp"].max, 30)     # 10 + 16 + 4
        self.assertEqual(self.sheet.pools["hp"].current, 20) # current unchanged, still valid
        self.assertEqual(self.sheet.pools["mana"].max, 6)    # 2 + 4

    def test_coerce_fills_and_clamps_base(self):
        s = self.rs.make_sheet(stats={"con": 99, "luck": 5})
        self.assertEqual(s.stats["con"], 20)     # clamped to max
        self.assertEqual(s.stats["str"], 10)     # default
        self.assertNotIn("luck", s.stats)        # unknown dropped


class PerceptionTests(unittest.TestCase):
    def setUp(self):
        self.sheet = ruleset().make_sheet(stats={"con": 14, "wis": 12}, level=1)

    def test_full_health_has_no_descriptor(self):
        self.assertEqual(self.sheet.health_descriptor(), "")
        self.assertEqual(self.sheet.notable_cues(), [])

    def test_wounded_descriptor_and_self_cue(self):
        self.sheet.pools["hp"].set(10)           # 50% → wounded
        self.assertEqual(self.sheet.health_descriptor(), "wounded")
        self.assertEqual(self.sheet.notable_cues(), ["wounded"])

    def test_descriptor_reads_the_vital_pool_only(self):
        self.sheet.pools["mana"].set(1)          # 20% mana → drained, but not vital
        self.assertEqual(self.sheet.health_descriptor(), "")   # hp still full
        self.assertIn("drained", self.sheet.notable_cues())    # self still perceives it


class RenderTests(unittest.TestCase):
    def test_score_shows_exact_numbers(self):
        sheet = ruleset().make_sheet(stats={"con": 14, "str": 16}, level=1)
        out = sheet.render(name="Hero")
        self.assertIn("== Hero ==", out)
        self.assertIn("Level 1", out)
        self.assertIn("CON 14 (+2)", out)
        self.assertIn("STR 16 (+3)", out)
        self.assertIn("20/20", out)              # HP exact
        self.assertIn("MANA", out)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.rs = ruleset()

    def test_live_state_round_trip(self):
        s = self.rs.make_sheet(stats={"con": 14, "wis": 12}, level=1)
        s.pools["hp"].set(8)
        s.pools["mana"].set(2)
        s.xp = 150
        s.points = 3
        state = s.live_state()

        fresh = self.rs.make_sheet(stats={"con": 14, "wis": 12}, level=1)
        fresh.load_live_state(state)
        self.assertEqual(fresh.pools["hp"].current, 8)
        self.assertEqual(fresh.pools["mana"].current, 2)
        self.assertEqual(fresh.xp, 150)
        self.assertEqual(fresh.points, 3)

    def test_load_recomputes_max_before_seating_current(self):
        # A level gained offline: the saved current (25) exceeds the level-1 max (20) but is
        # valid at the restored level 2 (max 30). Recompute must precede the clamp.
        s = self.rs.make_sheet(stats={"con": 14, "wis": 12}, level=1)
        s.level = 2
        s.recompute()
        s.pools["hp"].set(25)
        state = s.live_state()

        fresh = self.rs.make_sheet(stats={"con": 14, "wis": 12}, level=1)
        fresh.load_live_state(state)
        self.assertEqual(fresh.level, 2)
        self.assertEqual(fresh.pools["hp"].max, 30)
        self.assertEqual(fresh.pools["hp"].current, 25)

    def test_tolerant_load(self):
        s = self.rs.make_sheet(stats={"con": 14})
        s.load_live_state({})                    # no keys — must not raise
        s.load_live_state({"pools": {"ghost": 5}, "level": 3})  # unknown pool ignored
        self.assertEqual(s.level, 3)


if __name__ == "__main__":
    unittest.main()
