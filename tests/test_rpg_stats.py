"""The stat vocabulary (Phase 9, Tier 0), offline: attribute bounds, tolerant coercion vs
strict validation, the derivation namespace, and point-buy costing. Pure, no network.
"""
import unittest

from loom.rpg.expr import ExprError
from loom.rpg.stats import StatDef, StatSet, PointBuy


# The demo world's classic six, declared as data (the framework hardcodes none of this).
CFG = {
    "attributes": [
        {"name": "str", "min": 3, "max": 20, "default": 10},
        {"name": "dex", "min": 3, "max": 20, "default": 10},
        {"name": "con", "min": 3, "max": 20, "default": 10},
        {"name": "int", "min": 3, "max": 20, "default": 10},
        {"name": "wis", "min": 3, "max": 20, "default": 10},
        {"name": "cha", "min": 3, "max": 20, "default": 10},
    ],
    "derived": {
        "str_mod": "(str - 10) // 2",
        "con_mod": "(con - 10) // 2",
        "wis_mod": "(wis - 10) // 2",
        "prof": "2 + (level - 1) // 4",
    },
    "point_buy": {
        "budget": 27, "floor": 8, "ceil": 15,
        "cost": {"8": 0, "9": 1, "10": 2, "11": 3, "12": 4, "13": 5, "14": 7, "15": 9},
    },
}


class StatDefTests(unittest.TestCase):
    def test_clamp_and_bounds(self):
        s = StatDef("str", min=3, max=20, default=10)
        self.assertEqual(s.clamp(25), 20)
        self.assertEqual(s.clamp(1), 3)
        self.assertEqual(s.clamp(12), 12)
        self.assertTrue(s.in_bounds(3))
        self.assertFalse(s.in_bounds(21))


class StatSetTests(unittest.TestCase):
    def setUp(self):
        self.ss = StatSet.from_config(CFG)

    def test_vocabulary(self):
        self.assertEqual(self.ss.names(), ("str", "dex", "con", "int", "wis", "cha"))
        self.assertFalse(self.ss.is_empty())
        self.assertEqual(self.ss.get("con").max, 20)
        self.assertIsNone(self.ss.get("luck"))

    def test_defaults(self):
        self.assertEqual(self.ss.defaults(),
                         {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10})

    def test_coerce_is_tolerant(self):
        block = self.ss.coerce({"con": 14, "str": 99, "luck": 5, "dex": "bad"})
        self.assertEqual(block["con"], 14)
        self.assertEqual(block["str"], 20)     # clamped into bounds
        self.assertEqual(block["dex"], 10)     # non-numeric → default kept
        self.assertNotIn("luck", block)        # unknown key dropped
        self.assertEqual(block["wis"], 10)     # missing → default

    def test_validate_is_strict(self):
        self.assertEqual(self.ss.validate({"con": 14, "str": 12}), [])
        errs = self.ss.validate({"luck": 5, "con": 99, "dex": "x"})
        self.assertEqual(len(errs), 3)
        self.assertTrue(any("luck" in e for e in errs))
        self.assertTrue(any("con" in e for e in errs))
        self.assertTrue(any("dex" in e for e in errs))

    def test_derive_namespace(self):
        ns = self.ss.derive({"con": 14, "str": 16}, extra={"level": 1, "hit_die": 8})
        self.assertEqual(ns["con"], 14)
        self.assertEqual(ns["con_mod"], 2)
        self.assertEqual(ns["str_mod"], 3)
        self.assertEqual(ns["wis_mod"], 0)     # default 10 → +0
        self.assertEqual(ns["prof"], 2)
        self.assertEqual(ns["level"], 1)       # extra passes through
        self.assertEqual(ns["hit_die"], 8)

    def test_derive_prof_scales_with_level(self):
        ns = self.ss.derive(self.ss.defaults(), extra={"level": 9})
        self.assertEqual(ns["prof"], 4)

    def test_derived_may_reference_earlier_derived(self):
        ss = StatSet.from_config({
            "attributes": [{"name": "con", "default": 14}],
            "derived": {"con_mod": "(con - 10) // 2", "big": "con_mod * 10"},
        })
        ns = ss.derive({"con": 14})
        self.assertEqual(ns["con_mod"], 2)
        self.assertEqual(ns["big"], 20)

    def test_empty_vocabulary(self):
        ss = StatSet.from_config(None)
        self.assertTrue(ss.is_empty())
        self.assertEqual(ss.defaults(), {})

    def test_malformed_derived_raises_named(self):
        with self.assertRaises(ExprError) as ctx:
            StatSet.from_config({"attributes": [{"name": "con"}],
                                 "derived": {"bad": "con.__class__"}})
        self.assertIn("bad", str(ctx.exception))


class PointBuyTests(unittest.TestCase):
    def setUp(self):
        self.pb = StatSet.from_config(CFG).point_buy

    def test_parsed(self):
        self.assertIsInstance(self.pb, PointBuy)
        self.assertEqual(self.pb.budget, 27)
        self.assertEqual(self.pb.floor, 8)
        self.assertEqual(self.pb.ceil, 15)

    def test_cost_of_score(self):
        self.assertEqual(self.pb.cost_of_score(8), 0)
        self.assertEqual(self.pb.cost_of_score(14), 7)
        self.assertEqual(self.pb.cost_of_score(15), 9)

    def test_cost_of_score_out_of_range(self):
        with self.assertRaises(ExprError):
            self.pb.cost_of_score(16)

    def test_cost_of_block(self):
        block = {"str": 15, "dex": 15, "con": 15, "int": 8, "wis": 8, "cha": 8}
        self.assertEqual(self.pb.cost_of(block), 27)
        self.assertEqual(self.pb.remaining(block), 0)

    def test_validate_legal_allocation(self):
        block = {"str": 15, "dex": 15, "con": 15, "int": 8, "wis": 8, "cha": 8}
        self.assertEqual(self.pb.validate(block), [])

    def test_validate_over_budget(self):
        block = {"str": 15, "dex": 15, "con": 15, "int": 15, "wis": 8, "cha": 8}
        errs = self.pb.validate(block)
        self.assertEqual(len(errs), 1)
        self.assertIn("budget", errs[0])

    def test_validate_out_of_range(self):
        errs = self.pb.validate({"str": 16, "dex": 7})
        self.assertEqual(len(errs), 2)

    def test_no_point_buy(self):
        ss = StatSet.from_config({"attributes": [{"name": "con"}]})
        self.assertIsNone(ss.point_buy)


if __name__ == "__main__":
    unittest.main()
