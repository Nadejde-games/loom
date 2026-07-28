"""The world atlas (B7): the read/validate side of Phase 7 authoring. Pure and
offline — the survey gathers a loaded World into a serialisable view and lints it;
these tests need no provider, engine, or GPU. The renderers are smoke-tested (they
are presentation over the same survey); the survey + validator carry the assertions.
"""
import os
import unittest

from loom.atlas import (survey, render_text, render_markdown, mermaid,
                        KNOWN_DIRECTIONS)
from loom.content import load_world
from loom.world import World, Location, Npc, Item

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_WORLD = os.path.join(HERE, "..", "game", "world", "world.json")


def _world(locations=(), npcs=(), items=(), meta=None):
    """Build a World directly from field dicts — precise control over the broken
    states the loader passes through unchanged (dangling exits, bad holders)."""
    w = World()
    for loc in locations:
        w.add_location(Location(**loc))
    for n in npcs:
        w.add_entity(Npc(**n))
    for it in items:
        w.add_entity(Item(**it))
    if meta:
        w.meta.update(meta)
    return w


def _codes(view):
    return {f.code for f in view.findings}


def _error_codes(view):
    return {f.code for f in view.errors}


def _warn_codes(view):
    return {f.code for f in view.warnings}


class DemoWorldTests(unittest.TestCase):
    """The shipped world is the golden case: it must survey clean."""
    def setUp(self):
        world, start = load_world(GAME_WORLD)
        self.view = survey(world, start, source="game")

    def test_summary_is_internally_consistent(self):
        # Invariants that hold however the shipped world is authored — so editing content (a new
        # room, an item, an npc) never turns these red. Exact-count baselines were brittle: they
        # re-broke on every legitimate world edit and had to be hand-rebaselined each time. The
        # structural guarantees below are what actually matter; test_shipped_world_is_clean is the
        # real canary, not a magic number.
        s = self.view.summary()
        self.assertEqual(s["locations"], len(self.view.rooms))   # summary matches the survey
        self.assertEqual(s["npcs"], len(self.view.npcs))
        self.assertEqual(s["items"], len(self.view.items))
        self.assertGreaterEqual(s["locations"], 1)               # a playable world has a start room
        self.assertEqual(s["exits"], sum(len(r.exits) for r in self.view.rooms))

    def test_all_rooms_reachable(self):
        # Every room reachable from the start — an invariant, not a count. Robust to adding rooms.
        s = self.view.summary()
        self.assertEqual(s["reachable"], s["locations"])

    def test_shipped_world_is_clean(self):
        self.assertEqual(self.view.errors, [], msg=[f.message for f in self.view.errors])
        self.assertEqual(self.view.warnings, [],
                         msg=[f.message for f in self.view.warnings])

    def test_meta_blocks_surface(self):
        # The authored non-structural blocks each surface in the atlas view's meta. Asserted
        # as a subset (every known block present) rather than an exact set, so adding a new
        # authored block — e.g. the Phase 9 "stats" ruleset — extends the world without
        # tripping this invariant.
        self.assertLessEqual(
            {"director", "clock", "weather", "loot", "start_quests", "stats"},
            set(self.view.meta))


class SurveyShapeTests(unittest.TestCase):
    def test_two_way_exit_flags(self):
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"north": "b"}},
            {"id": "b", "name": "B", "description": "d", "exits": {"south": "a"}},
        ])
        v = survey(w, "a")
        a = next(r for r in v.rooms if r.id == "a")
        e = a.exits[0]
        self.assertTrue(e.ok)
        self.assertFalse(e.one_way)
        self.assertEqual(e.back, "south")
        self.assertEqual(e.target_name, "B")

    def test_one_way_exit_flag(self):
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"north": "b"}},
            {"id": "b", "name": "B", "description": "d", "exits": {"up": "c"}},
            {"id": "c", "name": "C", "description": "d", "exits": {"down": "b"}},
        ])
        v = survey(w, "a")
        a = next(r for r in v.rooms if r.id == "a")
        self.assertTrue(a.exits[0].one_way)
        self.assertEqual(a.exits[0].back, "")

    def test_dangling_exit_flag(self):
        w = _world([{"id": "a", "name": "A", "description": "d",
                     "exits": {"north": "ghost"}}])
        v = survey(w, "a")
        e = v.rooms[0].exits[0]
        self.assertFalse(e.ok)
        self.assertEqual(e.target_name, "")

    def test_holder_kinds(self):
        w = _world(
            [{"id": "room", "name": "R", "description": "d"}],
            npcs=[{"id": "npc", "name": "N", "description": "d", "location_id": "room",
                   "persona": {"voice": "v"}}],
            items=[
                {"id": "onfloor", "name": "f", "holder": "room"},
                {"id": "carried", "name": "c", "holder": "npc"},
                {"id": "box", "name": "box", "holder": "room"},
                {"id": "inbox", "name": "i", "holder": "box"},
                {"id": "lost", "name": "l", "holder": None},
                {"id": "broke", "name": "b", "holder": "ghost"},
            ])
        v = survey(w, "room")
        kind = {it.id: it.holder_kind for it in v.items}
        self.assertEqual(kind["onfloor"], "floor")
        self.assertEqual(kind["carried"], "held")
        self.assertEqual(kind["inbox"], "container")
        self.assertEqual(kind["lost"], "nowhere")
        self.assertEqual(kind["broke"], "broken")

    def test_npc_held_items(self):
        w = _world(
            [{"id": "room", "name": "R", "description": "d"}],
            npcs=[{"id": "npc", "name": "N", "description": "d", "location_id": "room",
                   "persona": {"voice": "v"}}],
            items=[{"id": "thing", "name": "a thing", "holder": "npc"}])
        v = survey(w, "room")
        npc = v.npcs[0]
        self.assertEqual([n for _, n in npc.held], ["a thing"])


class ValidationErrorTests(unittest.TestCase):
    def test_dangling_exit(self):
        w = _world([{"id": "a", "name": "A", "description": "d",
                     "exits": {"north": "ghost"}}])
        self.assertIn("dangling-exit", _error_codes(survey(w, "a")))

    def test_bad_direction(self):
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"sideways": "b"}},
            {"id": "b", "name": "B", "description": "d", "exits": {"in": "a"}},
        ])
        self.assertIn("bad-direction", _error_codes(survey(w, "a")))

    def test_in_out_are_known_directions(self):
        # in/out must NOT be flagged (the go-handler resolves them).
        self.assertIn("in", KNOWN_DIRECTIONS)
        self.assertIn("out", KNOWN_DIRECTIONS)
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"in": "b"}},
            {"id": "b", "name": "B", "description": "d", "exits": {"out": "a"}},
        ])
        self.assertNotIn("bad-direction", _error_codes(survey(w, "a")))

    def test_bad_holder(self):
        w = _world([{"id": "room", "name": "R", "description": "d"}],
                   items=[{"id": "x", "name": "x", "holder": "ghost"}])
        self.assertIn("bad-holder", _error_codes(survey(w, "room")))

    def test_bad_npc_location(self):
        w = _world([{"id": "room", "name": "R", "description": "d"}],
                   npcs=[{"id": "n", "name": "N", "description": "d",
                          "location_id": "ghost", "persona": {"voice": "v"}}])
        self.assertIn("bad-location", _error_codes(survey(w, "room")))

    def test_bad_start_missing(self):
        w = _world([{"id": "a", "name": "A", "description": "d"}])
        self.assertIn("bad-start", _error_codes(survey(w, None)))

    def test_bad_start_unknown(self):
        w = _world([{"id": "a", "name": "A", "description": "d"}])
        self.assertIn("bad-start", _error_codes(survey(w, "ghost")))

    def test_id_collision(self):
        w = _world([{"id": "x", "name": "X", "description": "d"}],
                   npcs=[{"id": "x", "name": "X2", "description": "d",
                          "location_id": "x", "persona": {"voice": "v"}}])
        self.assertIn("id-collision", _error_codes(survey(w, "x")))


class ValidationWarningTests(unittest.TestCase):
    def test_unreachable_room(self):
        w = _world([
            {"id": "a", "name": "A", "description": "d"},
            {"id": "island", "name": "Island", "description": "d"},
        ])
        self.assertIn("unreachable-room", _warn_codes(survey(w, "a")))

    def test_one_way_exit(self):
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"north": "b"}},
            {"id": "b", "name": "B", "description": "d", "exits": {"up": "c"}},
            {"id": "c", "name": "C", "description": "d", "exits": {"down": "b"}},
        ])
        self.assertIn("one-way-exit", _warn_codes(survey(w, "a")))

    def test_reverse_mismatch(self):
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"north": "b"}},
            {"id": "b", "name": "B", "description": "d", "exits": {"west": "a"}},
        ])
        self.assertIn("reverse-mismatch", _warn_codes(survey(w, "a")))

    def test_dead_end(self):
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"north": "b"}},
            {"id": "b", "name": "B", "description": "d"},
        ])
        self.assertIn("dead-end", _warn_codes(survey(w, "a")))

    def test_no_entrance(self):
        # 'a' points to 'b'; nothing points back to 'a'. 'a' is start, so it is
        # exempt — give a non-start room with no entrance.
        w = _world([
            {"id": "a", "name": "A", "description": "d", "exits": {"north": "b"}},
            {"id": "b", "name": "B", "description": "d", "exits": {"south": "a"}},
            {"id": "c", "name": "C", "description": "d", "exits": {"north": "a"}},
        ])
        self.assertIn("no-entrance", _warn_codes(survey(w, "a")))

    def test_thin_description(self):
        w = _world([{"id": "a", "name": "A", "description": "  "}])
        self.assertIn("thin-content", _warn_codes(survey(w, "a")))

    def test_thin_persona(self):
        w = _world([{"id": "a", "name": "A", "description": "d"}],
                   npcs=[{"id": "n", "name": "N", "description": "d",
                          "location_id": "a", "persona": {}}])
        self.assertIn("thin-content", _warn_codes(survey(w, "a")))

    def test_floating_item(self):
        w = _world([{"id": "a", "name": "A", "description": "d"}],
                   items=[{"id": "x", "name": "x", "holder": None}])
        self.assertIn("floating-item", _warn_codes(survey(w, "a")))


class RenderSmokeTests(unittest.TestCase):
    def setUp(self):
        world, start = load_world(GAME_WORLD)
        self.view = survey(world, start, source="game")

    def test_text_render_sections(self):
        out = render_text(self.view)
        for marker in ("WORLD ATLAS", "MAP (adjacency)", "ROOMS", "CHARACTERS",
                       "ITEMS", "LINT"):
            self.assertIn(marker, out)

    def test_markdown_render_has_mermaid(self):
        out = render_markdown(self.view)
        self.assertIn("```mermaid", out)
        self.assertIn("## Lint", out)

    def test_mermaid_is_a_graph(self):
        m = mermaid(self.view)
        self.assertTrue(m.startswith("graph LR"))
        self.assertIn("-->|north|", m)

    def test_findings_render_in_text(self):
        w = _world([{"id": "a", "name": "A", "description": "d",
                     "exits": {"north": "ghost"}}])
        out = render_text(survey(w, "a"))
        self.assertIn("dangling-exit", out)


if __name__ == "__main__":
    unittest.main()
