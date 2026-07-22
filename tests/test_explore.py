"""The Explorer's logic (Phase 8, slice 1): the map-model and the search that make a
surveyed world navigable. Pure and offline like the atlas tests — build a World, survey
it, and assert over the derived model; no provider, engine, or GPU. The Textual UI that
sits on this is smoke-tested separately (tests/test_workbench.py)."""
import os
import unittest

from loom.atlas import survey
from loom.content import load_world
from loom.explore import (map_model, search, location_index, MapModel, MapNode,
                         SearchHit)
from loom.world import World, Location, Npc, Item

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_WORLD = os.path.join(HERE, "..", "game", "world", "world.json")


def _world(locations=(), npcs=(), items=()):
    w = World()
    for loc in locations:
        w.add_location(Location(**loc))
    for n in npcs:
        w.add_entity(Npc(**n))
    for it in items:
        w.add_entity(Item(**it))
    return w


def _demo():
    """A small world exercising every map flag: hall (start) ↔ cave, cave → pit
    (one-way, pit a dead-end), and attic (orphan: unreachable, no-entrance, dead-end).
    Contents chain three ways: torch on a floor, key in a guard's hand, gem in a chest."""
    w = _world(
        locations=[
            {"id": "hall", "name": "Great Hall", "description": "A vast echoing hall.",
             "exits": {"north": "cave"}},
            {"id": "cave", "name": "Damp Cave", "description": "Water drips here.",
             "exits": {"south": "hall", "down": "pit"}},
            {"id": "pit", "name": "The Pit", "description": "A lightless drop.",
             "exits": {}},
            {"id": "attic", "name": "Dusty Attic", "description": "Forgotten and sealed.",
             "exits": {}},
        ],
        npcs=[
            {"id": "guard", "name": "Stone Guard", "location_id": "cave",
             "persona": {"backstory": "Sworn to the vault.", "traits": ["stoic"],
                         "goals": ["guard the gem"], "voice": "clipped"}},
            {"id": "wraith", "name": "Pale Wraith", "location_id": "attic",
             "wanders": True, "persona": {"voice": "a whisper"}},
        ],
        items=[
            {"id": "torch", "name": "Brass Torch", "holder": "hall",
             "aliases": ["light", "brand"]},
            {"id": "key", "name": "Iron Key", "holder": "guard"},
            {"id": "chest", "name": "Oak Chest", "holder": "cave", "portable": False},
            {"id": "gem", "name": "Green Gem", "holder": "chest",
             "tier": "rare", "tags": ["gem", "treasure"], "theme": "verdant",
             "aliases": ["emerald"]},
        ],
    )
    return survey(w, "hall", source="demo")


# ----------------------------------------------------------------------- map-model
class MapModelTests(unittest.TestCase):
    def setUp(self):
        self.m = map_model(_demo())

    def test_type_and_order(self):
        self.assertIsInstance(self.m, MapModel)
        self.assertEqual(self.m.order, ["hall", "cave", "pit", "attic"])
        self.assertEqual(len(self.m), 4)
        self.assertEqual(self.m.start, "hall")
        self.assertTrue(all(isinstance(n, MapNode) for n in self.m))

    def test_start_and_reachability(self):
        self.assertTrue(self.m.node("hall").is_start)
        self.assertFalse(self.m.node("cave").is_start)
        self.assertTrue(self.m.node("pit").reachable)
        self.assertFalse(self.m.node("attic").reachable)

    def test_neighbors_and_targets(self):
        self.assertEqual(self.m.neighbors("cave"), ["hall", "pit"])
        self.assertEqual(self.m.node("hall").targets, ["cave"])
        self.assertEqual(self.m.neighbors("pit"), [])

    def test_entrances_two_way(self):
        # hall is entered from cave (south), and cave has a way back → not one-way.
        ent = self.m.node("hall").entrances
        self.assertEqual(len(ent), 1)
        self.assertEqual((ent[0].from_id, ent[0].direction), ("cave", "south"))
        self.assertFalse(ent[0].one_way)

    def test_entrances_one_way(self):
        # pit is entered from cave (down); pit has no exit back → one-way.
        ent = self.m.node("pit").entrances
        self.assertEqual(len(ent), 1)
        self.assertEqual(ent[0].from_id, "cave")
        self.assertTrue(ent[0].one_way)

    def test_flags(self):
        self.assertEqual(self.m.node("hall").flags, [])         # start, well-connected
        self.assertIn("dead-end", self.m.node("pit").flags)
        self.assertNotIn("no-entrance", self.m.node("pit").flags)  # cave leads in
        self.assertEqual(sorted(self.m.node("attic").flags),
                         ["dead-end", "no-entrance", "unreachable"])

    def test_content_badges(self):
        self.assertEqual(self.m.node("hall").items, 1)          # the torch
        self.assertEqual(self.m.node("cave").occupants, 1)      # the guard
        self.assertEqual(self.m.node("cave").items, 1)          # the chest (gem is inside)

    def test_exits_carry_survey_flags(self):
        pit_exit = next(e for e in self.m.node("cave").exits if e.target == "pit")
        self.assertTrue(pit_exit.one_way)
        self.assertTrue(pit_exit.ok)


# -------------------------------------------------------------------- location index
class LocationIndexTests(unittest.TestCase):
    def setUp(self):
        self.idx = location_index(_demo())

    def test_npc_home_is_its_room(self):
        self.assertEqual(self.idx["guard"], "cave")
        self.assertEqual(self.idx["wraith"], "attic")

    def test_item_on_floor(self):
        self.assertEqual(self.idx["torch"], "hall")

    def test_item_held_resolves_to_holders_room(self):
        self.assertEqual(self.idx["key"], "cave")     # key → guard → cave

    def test_item_in_container_resolves_up(self):
        self.assertEqual(self.idx["gem"], "cave")     # gem → chest → cave

    def test_broken_holder_resolves_to_nothing(self):
        w = _world(
            locations=[{"id": "r", "name": "R", "description": "d", "exits": {}}],
            items=[{"id": "lost", "name": "Lost", "holder": "ghost_ref"}])
        idx = location_index(survey(w, "r"))
        self.assertEqual(idx["lost"], "")


# ---------------------------------------------------------------------------- search
class SearchTests(unittest.TestCase):
    def setUp(self):
        self.view = _demo()

    def _ids(self, **kw):
        return [h.id for h in search(self.view, **kw)]

    def test_returns_hits(self):
        hits = search(self.view, "gem")
        self.assertTrue(hits and isinstance(hits[0], SearchHit))

    def test_exact_id_outranks_substring(self):
        # "key" is the exact id of the Iron Key; nothing else should outrank it.
        hits = search(self.view, "key")
        self.assertEqual(hits[0].id, "key")
        self.assertIn("id", hits[0].matched)

    def test_name_match(self):
        hits = search(self.view, "wraith")
        self.assertEqual(hits[0].id, "wraith")
        self.assertIn("name", hits[0].matched)

    def test_alias_match(self):
        # "emerald" is only an alias of the gem.
        hits = search(self.view, "emerald")
        self.assertEqual(hits[0].id, "gem")
        self.assertIn("alias", hits[0].matched)

    def test_tag_axis_and_filter(self):
        hits = search(self.view, "treasure")            # a tag of the gem
        self.assertEqual(hits[0].id, "gem")
        self.assertIn("tag", hits[0].matched)
        # tag= as a pure filter: only items carrying it, browse-style (empty query).
        self.assertEqual(self._ids(tag="gem"), ["gem"])

    def test_tier_axis(self):
        hits = search(self.view, "rare")
        self.assertEqual(hits[0].id, "gem")
        self.assertIn("tier", hits[0].matched)

    def test_kind_filter(self):
        self.assertEqual(set(h.kind for h in search(self.view, "", kinds=("room",))),
                         {"room"})
        self.assertNotIn("guard", self._ids(kinds=("room", "item")))

    def test_where_context(self):
        gem = next(h for h in search(self.view, "gem") if h.id == "gem")
        self.assertEqual(gem.where, "cave")
        hall = next(h for h in search(self.view, "hall") if h.id == "hall")
        self.assertEqual(hall.where, "")                # a room is a place, not in one

    def test_empty_query_is_browse(self):
        # No query, no filter → every entity, ordered rooms→npcs→items.
        hits = search(self.view, "")
        self.assertEqual(len(hits), 4 + 2 + 4)
        self.assertEqual([h.kind for h in hits][:4], ["room"] * 4)

    def test_local_scope(self):
        # Scope to cave: cave + its neighbours (hall, pit) as rooms, and the entities
        # that sit IN cave (guard, chest, gem, key). The attic and its wraith are out.
        ids = set(self._ids(scope="cave"))
        self.assertIn("cave", ids)
        self.assertIn("hall", ids)                      # a neighbour room
        self.assertIn("guard", ids)                     # in cave
        self.assertIn("gem", ids)                       # in cave (via the chest)
        self.assertNotIn("attic", ids)
        self.assertNotIn("wraith", ids)

    def test_fuzzy_typo(self):
        # "wriath" is a transposition of the wraith's name — the fuzzy fallback catches
        # it where no substring would.
        hits = search(self.view, "wriath")
        self.assertTrue(hits)
        self.assertEqual(hits[0].id, "wraith")
        self.assertIn("fuzzy", hits[0].matched)

    def test_no_match_is_empty(self):
        self.assertEqual(search(self.view, "zzzxqq"), [])

    def test_limit(self):
        self.assertEqual(len(search(self.view, "", limit=3)), 3)

    def test_ordering_is_deterministic(self):
        a = search(self.view, "")
        b = search(self.view, "")
        self.assertEqual([(h.id, h.score) for h in a],
                         [(h.id, h.score) for h in b])


class ShippedWorldTests(unittest.TestCase):
    """The map-model and search hold up on the real shipped world, not just fixtures."""
    def setUp(self):
        world, start = load_world(GAME_WORLD)
        self.view = survey(world, start, source="game")

    def test_map_model_covers_every_room(self):
        m = map_model(self.view)
        self.assertEqual(len(m), self.view.summary()["locations"])
        self.assertTrue(all(m.node(r.id) for r in self.view.rooms))

    def test_every_room_reachable_flagged(self):
        m = map_model(self.view)
        self.assertTrue(all(n.reachable for n in m))    # the shipped world is clean

    def test_browse_lists_everything(self):
        s = self.view.summary()
        self.assertEqual(len(search(self.view, "")),
                         s["locations"] + s["npcs"] + s["items"])


if __name__ == "__main__":
    unittest.main()
