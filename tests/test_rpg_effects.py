"""The modifier / effect stack and gear (Phase 9, Tier 1), offline: apply-pairs, the
effective-stat fold, pool-bound bonuses, equip/unequip recompute, slot replacement, and
persistence of worn gear. Pure, no network.
"""
import unittest

from loom.rpg.effects import Modifier
from loom.rpg.sheet import Ruleset


META = {
    "attributes": [
        {"name": "str", "min": 3, "max": 20, "default": 10},
        {"name": "con", "min": 3, "max": 20, "default": 10},
    ],
    "derived": {"str_mod": "(str - 10) // 2", "con_mod": "(con - 10) // 2"},
    "hit_die": 8,
    "pools": {
        "hp": {"max": "10 + con_mod * level", "regen": "1"},
        "stamina": {"max": "20 + str_mod * 2", "regen": "3"},
    },
}

SWORD = [{"target": "str", "value": 2}, {"target": "max_stamina", "value": 5}]


def ruleset():
    return Ruleset.from_meta(META)


class ModifierTests(unittest.TestCase):
    def test_from_config_and_round_trip(self):
        m = Modifier.from_config({"target": "str", "value": 2})
        self.assertEqual((m.target, m.value, m.op, m.type, m.source), ("str", 2, "add", "", ""))
        d = Modifier(target="str", value=2, source="sword").to_dict()
        self.assertEqual(Modifier.from_dict(d).source, "sword")


class EffectiveStatsTests(unittest.TestCase):
    def setUp(self):
        self.sheet = ruleset().make_sheet(stats={"str": 10, "con": 10}, level=1)

    def test_stat_modifier_folds_into_effective(self):
        self.sheet.equip("main_hand", "sword", SWORD)
        self.assertEqual(self.sheet.effective_stats()["str"], 12)   # 10 + 2
        self.assertEqual(self.sheet.namespace()["str_mod"], 1)      # (12-10)//2

    def test_pool_bound_modifier_and_derived_growth(self):
        self.assertEqual(self.sheet.pools["stamina"].max, 20)       # base: 20 + 0
        self.sheet.equip("main_hand", "sword", SWORD)
        # str 12 → str_mod 1 → formula 22; plus the +5 max_stamina modifier → 27.
        self.assertEqual(self.sheet.pools["stamina"].max, 27)

    def test_unequip_reverts(self):
        self.sheet.equip("main_hand", "sword", SWORD)
        self.assertTrue(self.sheet.is_equipped("sword"))
        result = self.sheet.unequip_item("sword")
        self.assertTrue(result["ok"])
        self.assertEqual(result["slot"], "main_hand")
        self.assertEqual(self.sheet.effective_stats()["str"], 10)
        self.assertEqual(self.sheet.pools["stamina"].max, 20)
        self.assertFalse(self.sheet.is_equipped("sword"))

    def test_slot_replacement_strips_old_modifiers(self):
        self.sheet.equip("main_hand", "sword", SWORD)
        r = self.sheet.equip("main_hand", "axe", [{"target": "str", "value": 4}])
        self.assertEqual(r["replaced"], "sword")
        self.assertEqual(self.sheet.effective_stats()["str"], 14)   # only the axe now
        self.assertFalse(self.sheet.is_equipped("sword"))
        self.assertTrue(self.sheet.is_equipped("axe"))

    def test_equip_same_item_twice_is_idempotent(self):
        self.sheet.equip("main_hand", "sword", SWORD)
        r = self.sheet.equip("main_hand", "sword", SWORD)
        self.assertTrue(r.get("already"))
        self.assertEqual(self.sheet.effective_stats()["str"], 12)   # not doubled

    def test_current_clamps_when_max_shrinks(self):
        self.sheet.equip("main_hand", "sword", SWORD)               # stamina max 27
        self.sheet.pools["stamina"].set(27)
        self.sheet.unequip_item("sword")                           # max back to 20
        self.assertEqual(self.sheet.pools["stamina"].current, 20)  # clamped down


class GearPersistenceTests(unittest.TestCase):
    def test_worn_gear_survives_round_trip(self):
        rs = ruleset()
        s = rs.make_sheet(stats={"str": 10, "con": 10})
        s.equip("main_hand", "sword", SWORD)
        state = s.live_state()
        self.assertIn("equipment", state)
        self.assertIn("modifiers", state)

        fresh = rs.make_sheet(stats={"str": 10, "con": 10})
        fresh.load_live_state(state)
        self.assertTrue(fresh.is_equipped("sword"))
        self.assertEqual(fresh.effective_stats()["str"], 12)
        self.assertEqual(fresh.pools["stamina"].max, 27)


if __name__ == "__main__":
    unittest.main()
