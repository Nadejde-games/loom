"""The action registry: schema validation and the built-in emote handler.
Pure, offline, no provider — this is the safety layer the golden rule rests on.
"""
import unittest

from loom.action import (
    ActionRegistry, ActionSpec, ActionContext, ActionError, Param,
    default_registry,
)
from loom.world import World, Location, Npc


class _Actor:
    name = "Odd"
    id = "odd"


def _two_room_world(back_exit=True):
    """Room A (north→B); Room B (south→A when back_exit) with Odd standing in A."""
    world = World()
    world.add_location(Location(id="a", name="Room A", exits={"north": "b"}))
    world.add_location(Location(id="b", name="Room B",
                               exits={"south": "a"} if back_exit else {}))
    world.add_entity(Npc(id="odd", name="Odd", location_id="a"))
    return world


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.reg = default_registry()

    def test_valid_emote(self):
        self.assertEqual(self.reg.validate("emote", {"text": "nods"}), [])

    def test_unknown_action(self):
        errs = self.reg.validate("teleport", {"text": "x"})
        self.assertTrue(errs and "unknown action" in errs[0])

    def test_missing_required(self):
        errs = self.reg.validate("emote", {})
        self.assertTrue(any("missing required" in e for e in errs))

    def test_unknown_arg(self):
        errs = self.reg.validate("emote", {"text": "nods", "loudness": 11})
        self.assertTrue(any('unknown arg "loudness"' in e for e in errs))

    def test_wrong_type(self):
        errs = self.reg.validate("emote", {"text": 5})
        self.assertTrue(any("must be str" in e for e in errs))

    def test_args_not_object(self):
        self.assertTrue(self.reg.validate("emote", ["nods"]))

    def test_non_string_name(self):
        self.assertTrue(self.reg.validate(None, {"text": "x"}))


class TypeSystemTests(unittest.TestCase):
    def _reg(self, param):
        r = ActionRegistry()
        r.register(ActionSpec("act", "test action", {"v": param}, lambda ctx: None))
        return r

    def test_int_rejects_bool(self):
        self.assertTrue(self._reg(Param("int")).validate("act", {"v": True}))

    def test_int_accepts_int(self):
        self.assertEqual(self._reg(Param("int")).validate("act", {"v": 3}), [])

    def test_enum_ok_and_bad(self):
        r = self._reg(Param("enum", choices=("left", "right")))
        self.assertEqual(r.validate("act", {"v": "left"}), [])
        self.assertTrue(r.validate("act", {"v": "up"}))

    def test_optional_arg_may_be_absent(self):
        self.assertEqual(self._reg(Param("str", required=False)).validate("act", {}), [])


class RegistryTests(unittest.TestCase):
    def test_describe_lists_emote(self):
        text = default_registry().describe()
        self.assertIn("emote(", text)
        self.assertIn("text: str", text)

    def test_membership_and_names(self):
        reg = default_registry()
        self.assertIn("emote", reg)
        self.assertIn("move", reg)
        self.assertEqual(reg.names(), ["emote", "move"])

    def test_describe_lists_move(self):
        text = default_registry().describe()
        self.assertIn("move(", text)
        self.assertIn("direction: str", text)


class EmoteHandlerTests(unittest.TestCase):
    def test_narration_and_memory(self):
        spec = default_registry().get("emote")
        res = spec.handler(ActionContext(world=None, actor=_Actor(),
                                         args={"text": "nods slowly"}))
        self.assertEqual(res.narration, "Odd nods slowly")
        self.assertEqual(res.actor_memory, "I nods slowly.")


class MoveValidateTests(unittest.TestCase):
    def setUp(self):
        self.reg = default_registry()

    def test_valid_move(self):
        self.assertEqual(self.reg.validate("move", {"direction": "north"}), [])

    def test_missing_direction(self):
        self.assertTrue(any("missing required" in e
                            for e in self.reg.validate("move", {})))

    def test_direction_wrong_type(self):
        self.assertTrue(any("must be str" in e
                            for e in self.reg.validate("move", {"direction": 3})))

    def test_unknown_arg(self):
        errs = self.reg.validate("move", {"direction": "north", "speed": "fast"})
        self.assertTrue(any('unknown arg "speed"' in e for e in errs))


class MoveHandlerTests(unittest.TestCase):
    def setUp(self):
        self.spec = default_registry().get("move")

    def _run(self, world, direction):
        actor = world.entities["odd"]
        return self.spec.handler(ActionContext(world, actor, {"direction": direction}))

    def test_relocates_actor(self):
        world = _two_room_world()
        self._run(world, "north")
        self.assertEqual(world.entities["odd"].location_id, "b")
        self.assertIn("odd", world.locations["b"].occupants)
        self.assertNotIn("odd", world.locations["a"].occupants)

    def test_two_room_broadcasts_with_reverse_arrival(self):
        res = self._run(_two_room_world(), "north")
        self.assertEqual(res.broadcasts, [
            ("a", "Odd leaves, heading north."),
            ("b", "Odd arrives from the south."),
        ])
        self.assertIn("Room A", res.actor_memory)
        self.assertIn("Room B", res.actor_memory)

    def test_one_way_passage_narrates_plain_arrival(self):
        res = self._run(_two_room_world(back_exit=False), "north")
        self.assertEqual(res.broadcasts[1], ("b", "Odd arrives."))

    def test_bad_direction_raises(self):
        world = _two_room_world()
        with self.assertRaises(ActionError):
            self._run(world, "west")
        # world untouched on failure
        self.assertEqual(world.entities["odd"].location_id, "a")

    def test_direction_is_case_and_space_tolerant(self):
        world = _two_room_world()
        self._run(world, "  NORTH ")
        self.assertEqual(world.entities["odd"].location_id, "b")


if __name__ == "__main__":
    unittest.main()
