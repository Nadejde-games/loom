"""The item/inventory world-model: containment as a single 'holder' relation,
mirrored by World's reverse index. Take, drop, and give are all just re-homing.
Pure, offline."""
import unittest

from loom.world import World, Location, Npc, Player, Item


def _world():
    """A room with Odd, a Player, a lantern on the floor, a key in Odd's hand."""
    w = World()
    w.add_location(Location(id="room", name="Room"))
    w.add_entity(Npc(id="odd", name="Odd", location_id="room"))
    w.add_entity(Player(id="p1", name="Wanderer", location_id="room"))
    w.add_entity(Item(id="lantern", name="a lantern", holder="room",
                      aliases=["lantern"]))
    w.add_entity(Item(id="key", name="a key", holder="odd", aliases=["key"]))
    return w


class ContainmentIndexTests(unittest.TestCase):
    def test_add_entity_indexes_by_holder(self):
        w = _world()
        self.assertEqual([i.id for i in w.contents("room")], ["lantern"])
        self.assertEqual([i.id for i in w.contents("odd")], ["key"])

    def test_contents_is_sorted_by_name(self):
        w = _world()
        w.add_entity(Item(id="apple", name="an apple", holder="room"))
        # "a lantern" < "an apple" alphabetically -> stable, name-sorted order.
        self.assertEqual([i.name for i in w.contents("room")],
                         ["a lantern", "an apple"])

    def test_empty_holder(self):
        self.assertEqual(_world().contents("nowhere"), [])

    def test_items_are_not_room_occupants(self):
        # Items must not leak into occupants (which stays character-only, so the
        # salience gate and Scene.others are unaffected).
        names = [e.id for e in _world().occupants("room")]
        self.assertIn("odd", names)
        self.assertIn("p1", names)
        self.assertNotIn("lantern", names)

    def test_load_order_independent(self):
        # An item can be added before the entity that holds it.
        w = World()
        w.add_entity(Item(id="ring", name="a ring", holder="latecomer"))
        w.add_entity(Npc(id="latecomer", name="Latecomer", location_id="room"))
        self.assertEqual([i.id for i in w.contents("latecomer")], ["ring"])


class PlaceItemTests(unittest.TestCase):
    def test_take_rehomes_floor_to_inventory(self):
        w = _world()
        self.assertTrue(w.place_item("lantern", "p1"))
        self.assertEqual(w.entities["lantern"].holder, "p1")
        self.assertEqual([i.id for i in w.contents("p1")], ["lantern"])
        self.assertEqual(w.contents("room"), [])

    def test_give_rehomes_between_characters(self):
        w = _world()
        self.assertTrue(w.place_item("key", "p1"))
        self.assertEqual(w.contents("odd"), [])
        self.assertEqual([i.id for i in w.contents("p1")], ["key"])

    def test_unknown_item_refused(self):
        self.assertFalse(_world().place_item("ghost", "p1"))

    def test_unknown_holder_refused(self):
        w = _world()
        self.assertFalse(w.place_item("lantern", "void"))
        self.assertEqual(w.entities["lantern"].holder, "room")   # untouched

    def test_non_portable_refused(self):
        w = _world()
        w.add_entity(Item(id="statue", name="a statue", holder="room",
                          portable=False))
        self.assertFalse(w.place_item("statue", "p1"))
        self.assertEqual(w.entities["statue"].holder, "room")


class ScopeTests(unittest.TestCase):
    def test_scope_gathers_occupants_floor_and_inventory(self):
        w = _world()
        w.place_item("lantern", "p1")            # p1 now holds the lantern
        ids = {getattr(e, "id", None) for e in w.scope("p1")}
        self.assertIn("odd", ids)                # another occupant
        # key is in Odd's inventory, not on the floor or in p1's — not in scope:
        self.assertNotIn("key", ids)
        self.assertIn("lantern", ids)            # p1's own inventory
        self.assertNotIn("p1", ids)              # never yourself

    def test_scope_excludes_self(self):
        self.assertNotIn("p1", {getattr(e, "id", None)
                                for e in _world().scope("p1")})


class RemoveEntityTests(unittest.TestCase):
    def test_removing_holder_drops_items_to_the_floor(self):
        w = _world()                             # Odd holds the key, in "room"
        w.remove_entity("odd")
        self.assertNotIn("odd", w.entities)
        self.assertNotIn("odd", w.locations["room"].occupants)
        # The key wasn't lost — it fell to the room floor.
        self.assertEqual(w.entities["key"].holder, "room")
        self.assertIn("key", [i.id for i in w.contents("room")])

    def test_removing_an_item_unlinks_it_from_its_holder(self):
        w = _world()
        w.remove_entity("key")
        self.assertNotIn("key", w.entities)
        self.assertEqual(w.contents("odd"), [])


if __name__ == "__main__":
    unittest.main()
