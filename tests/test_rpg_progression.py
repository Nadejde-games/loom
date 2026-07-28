"""Progression (Phase 9, Tier 1), offline: the XP curve, multi-level level-up granting build
points and growing pools, and the train economy paying from points via the point-buy table.
Pure, no network.
"""
import unittest

from loom.rpg.progression import Progression
from loom.rpg.sheet import Ruleset


META = {
    "attributes": [
        {"name": "str", "min": 3, "max": 20, "default": 10},
        {"name": "con", "min": 3, "max": 20, "default": 10},
    ],
    "derived": {"str_mod": "(str - 10) // 2", "con_mod": "(con - 10) // 2"},
    "hit_die": 8,
    "pools": {"hp": {"max": "10 + hit_die*level + con_mod*level", "regen": "1"}},
    "point_buy": {"budget": 27, "floor": 8, "ceil": 15,
                  "cost": {"8": 0, "9": 1, "10": 2, "11": 3, "12": 4, "13": 5, "14": 7, "15": 9}},
    "progression": {"curve": "50 * (level - 1) * level", "points_per_level": 2, "max_level": 20},
}


def ruleset():
    return Ruleset.from_meta(META)


class ProgressionRuleTests(unittest.TestCase):
    def test_curve(self):
        p = ruleset().progression
        self.assertEqual(p.xp_to_level(1), 0)
        self.assertEqual(p.xp_to_level(2), 100)      # 50 * 1 * 2
        self.assertEqual(p.xp_to_level(3), 300)      # 50 * 2 * 3
        self.assertEqual(p.cap(), 20)

    def test_from_config_none(self):
        self.assertIsNone(Progression.from_config(None))


class GrantXpTests(unittest.TestCase):
    def setUp(self):
        self.sheet = ruleset().make_sheet(stats={"con": 12}, level=1)

    def test_single_level(self):
        r = self.sheet.grant_xp(120)
        self.assertEqual(r["gained"], 1)
        self.assertEqual(self.sheet.level, 2)
        self.assertEqual(self.sheet.points, 2)       # points_per_level
        self.assertEqual(self.sheet.xp, 120)

    def test_multi_level_catch_up(self):
        r = self.sheet.grant_xp(300)                 # crosses L2 (100) and L3 (300)
        self.assertEqual(r["gained"], 2)
        self.assertEqual(self.sheet.level, 3)
        self.assertEqual(self.sheet.points, 4)

    def test_pools_grow_on_level(self):
        before = self.sheet.pools["hp"].max          # con 12 → con_mod 1; L1: 10+8+1 = 19
        self.assertEqual(before, 19)
        self.sheet.grant_xp(100)                      # → L2: 10 + 16 + 2 = 28
        self.assertEqual(self.sheet.pools["hp"].max, 28)

    def test_below_threshold_no_level(self):
        r = self.sheet.grant_xp(50)
        self.assertEqual(r["gained"], 0)
        self.assertEqual(self.sheet.level, 1)

    def test_no_progression_rule(self):
        rs = Ruleset.from_meta({"attributes": [{"name": "con"}]})   # no progression block
        sheet = rs.make_sheet()
        self.assertEqual(sheet.grant_xp(9999)["gained"], 0)
        self.assertEqual(sheet.level, 1)


class TrainTests(unittest.TestCase):
    def setUp(self):
        self.sheet = ruleset().make_sheet(stats={"str": 10, "con": 10}, level=1)
        self.sheet.points = 5

    def test_train_raises_stat_and_spends_points(self):
        r = self.sheet.train("str")                  # 10 → 11: cost = cost[11]-cost[10] = 3-2 = 1
        self.assertTrue(r["ok"])
        self.assertEqual(self.sheet.stats["str"], 11)
        self.assertEqual(r["spent"], 1)
        self.assertEqual(self.sheet.points, 4)

    def test_train_recomputes_pools(self):
        self.assertEqual(self.sheet.pools["hp"].max, 18)   # con 10 → con_mod 0: 10 + 8 + 0
        self.sheet.train("con")                      # 10 → 11: con_mod still 0, hp unchanged
        self.sheet.train("con")                      # 11 → 12: con_mod 1 → hp max grows
        self.assertEqual(self.sheet.stats["con"], 12)
        self.assertEqual(self.sheet.pools["hp"].max, 19)   # 10 + 8*1 + 1*1

    def test_escalating_cost(self):
        # 13 → 14 costs 2 (cost 7 vs 5); 14 → 15 costs 2 (9 vs 7) — the table's steepening.
        self.sheet.stats["con"] = 13
        self.sheet.points = 2
        r = self.sheet.train("con")
        self.assertTrue(r["ok"])
        self.assertEqual(r["spent"], 2)
        self.assertEqual(self.sheet.points, 0)

    def test_unaffordable(self):
        self.sheet.stats["str"] = 14
        self.sheet.points = 1
        r = self.sheet.train("str")                  # 14 → 15 costs 2, only 1 point
        self.assertFalse(r["ok"])
        self.assertIn("costs", r["reason"])
        self.assertEqual(self.sheet.stats["str"], 14)

    def test_ceiling(self):
        self.sheet.stats["str"] = 15                 # already at point-buy ceil
        r = self.sheet.train("str")
        self.assertFalse(r["ok"])
        self.assertIn("maximum", r["reason"])

    def test_unknown_stat(self):
        r = self.sheet.train("luck")
        self.assertFalse(r["ok"])
        self.assertIn("no attribute", r["reason"])


if __name__ == "__main__":
    unittest.main()
