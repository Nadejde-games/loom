"""Character templates (Phase 9, Tier 0), offline: races/classes/backgrounds as one
composition mechanism — additive stat deltas, granted skills and points, set level/hit-die,
and the default template stack a new player is assigned. Pure, no network.
"""
import unittest

from loom.rpg.sheet import Ruleset
from loom.rpg.template import Template, compose, default_sheet


META = {
    "attributes": [
        {"name": "str", "min": 3, "max": 20, "default": 10},
        {"name": "dex", "min": 3, "max": 20, "default": 10},
        {"name": "con", "min": 3, "max": 20, "default": 10},
        {"name": "wis", "min": 3, "max": 20, "default": 10},
    ],
    "derived": {"con_mod": "(con - 10) // 2"},
    "hit_die": 8,
    "pools": {"hp": {"max": "10 + hit_die*level + con_mod*level", "regen": "1"}},
    "templates": [
        {"name": "human", "kind": "race", "stats": {"con": 1, "cha": 1}},
        {"name": "wanderer", "kind": "background", "stats": {"dex": 1, "con": 1},
         "skills": {"observation": 1}, "points": 2},
        {"name": "veteran", "kind": "class", "level": 3, "hit_die": 10,
         "skills": {"blades": 2}, "stats": {"str": 2}},
    ],
    "default": ["human", "wanderer"],
}


def ruleset():
    return Ruleset.from_meta(META)


class TemplateParseTests(unittest.TestCase):
    def test_from_config(self):
        t = Template.from_config(META["templates"][2])
        self.assertEqual(t.name, "veteran")
        self.assertEqual(t.kind, "class")
        self.assertEqual(t.stats, {"str": 2})
        self.assertEqual(t.skills, {"blades": 2})
        self.assertEqual(t.level, 3)
        self.assertEqual(t.hit_die, 10)

    def test_defaults_absent(self):
        t = Template.from_config({"name": "bare"})
        self.assertIsNone(t.level)
        self.assertIsNone(t.hit_die)
        self.assertEqual(t.stats, {})
        self.assertEqual(t.points, 0)


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.rs = ruleset()

    def test_additive_stat_deltas(self):
        human = self.rs.template("human")
        wanderer = self.rs.template("wanderer")
        sheet = compose(self.rs, [human, wanderer])
        # con gets +1 from each → 12; dex +1 → 11; str untouched → 10.
        self.assertEqual(sheet.stats["con"], 12)
        self.assertEqual(sheet.stats["dex"], 11)
        self.assertEqual(sheet.stats["str"], 10)

    def test_skills_points_and_level_hitdie_set(self):
        stack = [self.rs.template(n) for n in ("human", "wanderer", "veteran")]
        sheet = compose(self.rs, stack)
        self.assertEqual(sheet.level, 3)             # veteran sets it
        self.assertEqual(sheet.hit_die, 10)          # veteran sets it
        self.assertEqual(sheet.points, 2)            # wanderer grants 2
        self.assertEqual(sheet.skills["observation"]["level"], 1)
        self.assertEqual(sheet.skills["blades"]["level"], 2)

    def test_hp_reflects_composed_stats_and_level(self):
        stack = [self.rs.template(n) for n in ("human", "wanderer", "veteran")]
        sheet = compose(self.rs, stack)
        # con 12 → con_mod 1; level 3; hit_die 10 → 10 + 10*3 + 1*3 = 43.
        self.assertEqual(sheet.pools["hp"].max, 43)
        self.assertEqual(sheet.pools["hp"].current, 43)

    def test_empty_stack_yields_default_sheet(self):
        sheet = compose(self.rs, [])
        self.assertEqual(sheet.stats["con"], 10)
        self.assertEqual(sheet.level, 1)


class DefaultSheetTests(unittest.TestCase):
    def test_default_stack_composed(self):
        rs = ruleset()
        sheet = rs.default_sheet()
        self.assertEqual(sheet.stats["con"], 12)     # human + wanderer
        self.assertEqual(sheet.points, 2)
        self.assertIn("observation", sheet.skills)

    def test_default_sheet_free_function(self):
        rs = ruleset()
        self.assertEqual(default_sheet(rs).stats["dex"], 11)

    def test_no_templates_declared(self):
        rs = Ruleset.from_meta({"attributes": [{"name": "con"}]})
        sheet = rs.default_sheet()                    # empty default stack
        self.assertEqual(sheet.stats["con"], 10)


if __name__ == "__main__":
    unittest.main()
