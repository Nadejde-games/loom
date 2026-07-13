"""The conditions registry: standing environmental state, keyed by location.

Pure data, offline. Proves the persistence primitive the director's world-shaping
rests on — set/upsert/clear/query — independent of any action or provider.
"""
import unittest

from loom.world import Condition, Conditions, World


class ConditionsRegistryTests(unittest.TestCase):
    def setUp(self):
        self.c = Conditions()

    def test_empty_by_default(self):
        self.assertEqual(self.c.at("clearing"), [])
        self.assertEqual(self.c.texts("clearing"), [])

    def test_set_then_query(self):
        self.c.set("clearing", "storm", "A cold rain falls.")
        self.assertEqual(self.c.texts("clearing"), ["A cold rain falls."])
        at = self.c.at("clearing")
        self.assertEqual(at, [Condition(tag="storm", text="A cold rain falls.")])

    def test_set_strips_whitespace(self):
        self.c.set("clearing", "  storm  ", "  rain  ")
        self.assertEqual(self.c.at("clearing")[0], Condition("storm", "rain"))

    def test_same_tag_upserts_in_place(self):
        self.c.set("clearing", "storm", "first")
        self.c.set("clearing", "night", "dark")
        self.c.set("clearing", "storm", "second")   # replace, keep position
        self.assertEqual([x.tag for x in self.c.at("clearing")], ["storm", "night"])
        self.assertEqual(self.c.texts("clearing"), ["second", "dark"])

    def test_different_tags_coexist_in_order(self):
        self.c.set("clearing", "storm", "rain")
        self.c.set("clearing", "night", "dark")
        self.assertEqual(self.c.texts("clearing"), ["rain", "dark"])

    def test_locations_are_independent(self):
        self.c.set("clearing", "storm", "rain")
        self.c.set("cave", "dark", "pitch black")
        self.assertEqual(self.c.texts("clearing"), ["rain"])
        self.assertEqual(self.c.texts("cave"), ["pitch black"])

    def test_clear_removes_and_reports(self):
        self.c.set("clearing", "storm", "rain")
        self.assertTrue(self.c.clear("clearing", "storm"))
        self.assertEqual(self.c.texts("clearing"), [])

    def test_clear_absent_is_false(self):
        self.assertFalse(self.c.clear("clearing", "storm"))
        self.c.set("clearing", "night", "dark")
        self.assertFalse(self.c.clear("clearing", "storm"))   # wrong tag
        self.assertEqual(self.c.texts("clearing"), ["dark"])  # untouched

    def test_clear_one_of_many_leaves_the_rest(self):
        self.c.set("clearing", "storm", "rain")
        self.c.set("clearing", "night", "dark")
        self.assertTrue(self.c.clear("clearing", "storm"))
        self.assertEqual(self.c.texts("clearing"), ["dark"])


class WorldScopeConditionsTests(unittest.TestCase):
    """World-wide conditions — the world-clock's time-of-day, held everywhere and
    kept apart from any one place's standing conditions."""

    def setUp(self):
        self.c = Conditions()

    def test_empty_by_default(self):
        self.assertEqual(self.c.world_at(), [])
        self.assertEqual(self.c.world_texts(), [])

    def test_set_then_query(self):
        self.c.set_world("time", "It is night.")
        self.assertEqual(self.c.world_texts(), ["It is night."])
        self.assertEqual(self.c.world_at(), [Condition("time", "It is night.")])

    def test_same_tag_upserts(self):
        self.c.set_world("time", "It is day.")
        self.c.set_world("time", "It is night.")     # one time-of-day, not a pile
        self.assertEqual(self.c.world_texts(), ["It is night."])

    def test_set_strips_whitespace(self):
        self.c.set_world("  time  ", "  night  ")
        self.assertEqual(self.c.world_at()[0], Condition("time", "night"))

    def test_clear_removes_and_reports(self):
        self.c.set_world("time", "It is night.")
        self.assertTrue(self.c.clear_world("time"))
        self.assertEqual(self.c.world_texts(), [])
        self.assertFalse(self.c.clear_world("time"))  # already gone

    def test_world_and_location_scopes_are_independent(self):
        self.c.set_world("time", "It is night.")
        self.c.set("cave", "dark", "pitch black")
        self.assertEqual(self.c.world_texts(), ["It is night."])
        self.assertEqual(self.c.texts("cave"), ["pitch black"])
        # A location query never leaks the world scope, and vice versa.
        self.assertEqual(self.c.texts("time"), [])


class WorldHasConditionsTests(unittest.TestCase):
    def test_world_owns_a_conditions_registry(self):
        world = World()
        self.assertIsInstance(world.conditions, Conditions)
        world.conditions.set("a", "storm", "rain")
        self.assertEqual(world.conditions.texts("a"), ["rain"])


if __name__ == "__main__":
    unittest.main()
