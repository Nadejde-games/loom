"""Use-based skills (Phase 9), offline: the XP curve, the award-on-use with a success bonus,
multi-level jumps, the level cap, and a non-increasing-curve guard against runaway loops.
Pure, no network.
"""
import unittest

from loom.rpg.skills import SkillDef, SkillSet


def skillset(curve="50 * level * level", xp_per_use=5, success_bonus=5, skills=None):
    return SkillSet.from_config({
        "curve": curve, "xp_per_use": xp_per_use, "success_bonus": success_bonus,
        "list": skills if skills is not None else [
            {"name": "blades", "governing": "str_mod"},
            {"name": "archery", "governing": "dex_mod"}],
    })


class SkillSetTests(unittest.TestCase):
    def test_vocabulary_and_curve(self):
        ss = skillset()
        self.assertEqual(ss.names(), ("blades", "archery"))
        self.assertEqual(ss.get("blades").governing, "str_mod")
        self.assertEqual(ss.xp_to_level(0), 0)
        self.assertEqual(ss.xp_to_level(1), 50)
        self.assertEqual(ss.xp_to_level(2), 200)

    def test_award_accumulates_xp(self):
        ss = skillset()
        skills = {}
        r = ss.award(skills, "blades", success=True)
        self.assertEqual(r["gain"], 10)          # 5 use + 5 success
        self.assertEqual(skills["blades"], {"xp": 10, "level": 0})
        self.assertFalse(r["leveled"])

    def test_award_on_use_without_success(self):
        ss = skillset()
        skills = {}
        self.assertEqual(ss.award(skills, "blades")["gain"], 5)

    def test_levels_up_when_threshold_crossed(self):
        ss = skillset()
        skills = {}
        last = None
        for _ in range(5):                       # 5 × 10 xp = 50 = threshold for L1
            last = ss.award(skills, "blades", success=True)
        self.assertTrue(last["leveled"])
        self.assertEqual(skills["blades"]["level"], 1)

    def test_multi_level_jump_in_one_award(self):
        ss = skillset(curve="10 * level", xp_per_use=35)
        skills = {}
        r = ss.award(skills, "blades")           # 35 xp: past L1(10), L2(20), L3(30)
        self.assertEqual(r["level"], 3)
        self.assertTrue(r["leveled"])

    def test_max_level_cap(self):
        ss = skillset(curve="10 * level", xp_per_use=1000,
                      skills=[{"name": "blades", "max_level": 2}])
        skills = {}
        r = ss.award(skills, "blades")
        self.assertEqual(r["level"], 2)          # capped despite ample xp

    def test_non_increasing_curve_terminates(self):
        ss = skillset(curve="5", xp_per_use=1000)   # constant curve
        skills = {}
        r = ss.award(skills, "blades")           # must not loop forever
        self.assertEqual(r["level"], 1)

    def test_from_config_none_and_empty(self):
        self.assertIsNone(SkillSet.from_config(None))
        self.assertIsNone(SkillSet.from_config({}))
        ss = SkillSet.from_config({"list": []})
        self.assertTrue(ss.is_empty())


if __name__ == "__main__":
    unittest.main()
