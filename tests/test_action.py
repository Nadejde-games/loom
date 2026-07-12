"""The action registry: schema validation and the built-in emote handler.
Pure, offline, no provider — this is the safety layer the golden rule rests on.
"""
import unittest

from loom.action import (
    ActionRegistry, ActionSpec, ActionContext, ActionError, Param,
    default_registry,
)
from loom.world import World, Location, Npc, Item


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
        self.assertIn("give_item", reg)
        self.assertIn("take_item", reg)
        self.assertIn("drop_item", reg)
        self.assertEqual(reg.names(),
                         ["emote", "move", "give_item", "take_item", "drop_item"])

    def test_describe_lists_move(self):
        text = default_registry().describe()
        self.assertIn("move(", text)
        self.assertIn("direction: str", text)

    def test_describe_lists_give_item(self):
        text = default_registry().describe()
        self.assertIn("give_item(", text)
        self.assertIn("item: str", text)
        self.assertIn("recipient: str", text)


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


def _give_world():
    """A room with Odd holding a brass key, and Wren present to receive it."""
    world = World()
    world.add_location(Location(id="room", name="Room"))
    world.add_entity(Npc(id="odd", name="Odd", location_id="room"))
    world.add_entity(Npc(id="wren", name="Wren", location_id="room"))
    world.add_entity(Item(id="key", name="a brass key", holder="odd",
                          aliases=["key", "brass"]))
    return world


class GiveItemValidateTests(unittest.TestCase):
    def setUp(self):
        self.reg = default_registry()

    def test_valid(self):
        self.assertEqual(
            self.reg.validate("give_item", {"item": "key", "recipient": "Wren"}), [])

    def test_missing_item(self):
        errs = self.reg.validate("give_item", {"recipient": "Wren"})
        self.assertTrue(any("missing required" in e for e in errs))

    def test_missing_recipient(self):
        errs = self.reg.validate("give_item", {"item": "key"})
        self.assertTrue(any("missing required" in e for e in errs))

    def test_item_wrong_type(self):
        errs = self.reg.validate("give_item", {"item": 3, "recipient": "Wren"})
        self.assertTrue(any("must be str" in e for e in errs))

    def test_unknown_arg(self):
        errs = self.reg.validate(
            "give_item", {"item": "key", "recipient": "Wren", "gently": True})
        self.assertTrue(any('unknown arg "gently"' in e for e in errs))


class GiveItemHandlerTests(unittest.TestCase):
    def setUp(self):
        self.spec = default_registry().get("give_item")

    def _run(self, world, item, recipient, actor_id="odd"):
        actor = world.entities[actor_id]
        return self.spec.handler(
            ActionContext(world, actor, {"item": item, "recipient": recipient}))

    def test_transfers_narrates_and_remembers(self):
        world = _give_world()
        res = self._run(world, "key", "Wren")
        self.assertEqual(world.entities["key"].holder, "wren")
        self.assertIn("key", [i.id for i in world.contents("wren")])
        self.assertNotIn("key", [i.id for i in world.contents("odd")])
        self.assertEqual(res.narration, "Odd gives a brass key to Wren.")
        self.assertIn("a brass key", res.actor_memory)

    def test_resolves_recipient_by_partial_name(self):
        # Resolution is by scope, not exact string: "Wr" finds Wren.
        world = _give_world()
        self._run(world, "brass", "Wr")
        self.assertEqual(world.entities["key"].holder, "wren")

    def test_item_not_held_raises_and_leaves_world_untouched(self):
        world = _give_world()
        with self.assertRaises(ActionError):
            self._run(world, "lantern", "Wren")     # Odd holds no lantern
        self.assertEqual(world.entities["key"].holder, "odd")

    def test_recipient_absent_raises(self):
        world = _give_world()
        with self.assertRaises(ActionError):
            self._run(world, "key", "Gandalf")      # no such character here
        self.assertEqual(world.entities["key"].holder, "odd")

    def test_ambiguous_item_raises(self):
        world = _give_world()
        world.add_entity(Item(id="key2", name="an iron key", holder="odd",
                              aliases=["key"]))
        with self.assertRaises(ActionError):
            self._run(world, "key", "Wren")         # two keys held → ambiguous
        # Neither key moved.
        self.assertEqual(world.entities["key"].holder, "odd")
        self.assertEqual(world.entities["key2"].holder, "odd")


def _take_world():
    """A room with a lantern on the floor, Odd present, and Wren holding a map."""
    world = World()
    world.add_location(Location(id="room", name="Room"))
    world.add_entity(Npc(id="odd", name="Odd", location_id="room"))
    world.add_entity(Npc(id="wren", name="Wren", location_id="room"))
    world.add_entity(Item(id="lantern", name="a rusty lantern", holder="room",
                          aliases=["lantern", "lamp"]))
    world.add_entity(Item(id="map", name="a worn map", holder="wren",
                          aliases=["map"]))
    return world


class TakeItemHandlerTests(unittest.TestCase):
    def setUp(self):
        self.spec = default_registry().get("take_item")

    def _run(self, world, actor_id="odd", **args):
        actor = world.entities[actor_id]
        return self.spec.handler(ActionContext(world, actor, args))

    def test_take_from_floor(self):
        world = _take_world()
        res = self._run(world, item="lantern")
        self.assertEqual(world.entities["lantern"].holder, "odd")
        self.assertEqual(res.narration, "Odd takes a rusty lantern.")
        self.assertIn("a rusty lantern", res.actor_memory)

    def test_take_from_a_source(self):
        world = _take_world()
        res = self._run(world, item="map", source="Wren")
        self.assertEqual(world.entities["map"].holder, "odd")
        self.assertEqual(res.narration, "Odd takes a worn map from Wren.")

    def test_take_absent_item_raises(self):
        world = _take_world()
        with self.assertRaises(ActionError):
            self._run(world, item="sword")
        self.assertEqual(world.entities["lantern"].holder, "room")

    def test_take_item_not_on_named_source_raises(self):
        world = _take_world()
        with self.assertRaises(ActionError):
            self._run(world, item="lantern", source="Wren")   # Wren hasn't got it
        self.assertEqual(world.entities["lantern"].holder, "room")

    def test_take_unknown_source_raises(self):
        world = _take_world()
        with self.assertRaises(ActionError):
            self._run(world, item="map", source="Gandalf")

    def test_take_non_portable_raises(self):
        world = _take_world()
        world.add_entity(Item(id="statue", name="a stone statue", holder="room",
                              aliases=["statue"], portable=False))
        with self.assertRaises(ActionError):
            self._run(world, item="statue")
        self.assertEqual(world.entities["statue"].holder, "room")


class DropItemHandlerTests(unittest.TestCase):
    def setUp(self):
        self.spec = default_registry().get("drop_item")

    def _run(self, world, actor_id="odd", **args):
        actor = world.entities[actor_id]
        return self.spec.handler(ActionContext(world, actor, args))

    def test_drop_held_item_to_floor(self):
        world = _take_world()
        world.place_item("map", "odd")             # Odd now holds the map
        res = self._run(world, item="map")
        self.assertEqual(world.entities["map"].holder, "room")
        self.assertEqual(res.narration, "Odd drops a worn map.")

    def test_drop_unheld_item_raises(self):
        world = _take_world()
        with self.assertRaises(ActionError):
            self._run(world, item="lantern")       # on the floor, not held
        self.assertEqual(world.entities["lantern"].holder, "room")


class DescribeSubsetTests(unittest.TestCase):
    def test_describe_narrows_to_named_actions(self):
        text = default_registry().describe(["emote", "move"])
        self.assertIn("emote(", text)
        self.assertIn("move(", text)
        self.assertNotIn("give_item(", text)
        self.assertNotIn("take_item(", text)

    def test_describe_none_lists_all(self):
        text = default_registry().describe()
        for name in ("emote(", "move(", "give_item(", "take_item(", "drop_item("):
            self.assertIn(name, text)

    def test_unknown_names_are_skipped(self):
        text = default_registry().describe(["emote", "nonesuch"])
        self.assertIn("emote(", text)
        self.assertNotIn("nonesuch", text)


if __name__ == "__main__":
    unittest.main()
