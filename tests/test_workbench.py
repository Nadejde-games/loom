"""The authoring workbench (Phase 8, slice 1): the inspector cards (pure) and a Textual
UI smoke pass. The cards are formatting over the survey and run in the offline gate
always; the UI test drives the real Textual runtime (``App.run_test()`` + a ``Pilot``)
and is skipped cleanly when the ``authoring`` extra (Textual) is not installed — so the
suite stays green without the tool dependency."""
import os
import unittest

from loom.atlas import survey
from loom.content import load_world
from loom.world import World, Location, Npc, Item
from authoring.cards import card, parse_ref, pack_ref

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_WORLD = os.path.join(HERE, "..", "game", "world", "world.json")


def _demo_view():
    w = World()
    for loc in [
        {"id": "hall", "name": "Great Hall", "description": "A vast echoing hall.",
         "exits": {"north": "cave"}},
        {"id": "cave", "name": "Damp Cave", "description": "Water drips.",
         "exits": {"south": "hall", "down": "pit"}},
        {"id": "pit", "name": "The Pit", "description": "A drop.", "exits": {}},
    ]:
        w.add_location(Location(**loc))
    w.add_entity(Npc(id="guard", name="Stone Guard", location_id="cave",
                     persona={"backstory": "Sworn to the vault.", "traits": ["stoic"],
                              "goals": ["guard"], "voice": "clipped"}))
    w.add_entity(Item(id="torch", name="Brass Torch", holder="hall",
                      aliases=["light"]))
    w.add_entity(Item(id="key", name="Iron Key", holder="guard",
                      tier="common", tags=["key"], theme="iron"))
    return survey(w, "hall", source="demo")


# ------------------------------------------------------------------------- the cards
class CardTests(unittest.TestCase):
    def setUp(self):
        from loom.explore import map_model
        self.view = _demo_view()
        self.map = map_model(self.view)

    def _card(self, kind, eid):
        return card(self.view, self.map, kind, eid)

    def test_ref_roundtrip(self):
        self.assertEqual(parse_ref(pack_ref("room", "cave")), ("room", "cave"))
        self.assertEqual(parse_ref("garbage"), ("", ""))

    def test_room_card_text_and_links(self):
        text, links = self._card("room", "cave")
        self.assertIn("Damp Cave", text)
        self.assertIn("EXITS", text)
        refs = {ref for _, ref in links}
        self.assertIn("room:hall", refs)     # an exit target → a jump-link
        self.assertIn("room:pit", refs)
        self.assertIn("npc:guard", refs)     # the occupant → a jump-link
        # The key is in the guard's hand, not on the cave floor, and nothing else lies
        # here — so the cave card carries no item jump-links.
        self.assertFalse([r for r in refs if r.startswith("item:")])

    def test_room_card_flags_shown(self):
        text, _ = self._card("room", "pit")
        self.assertIn("DEAD-END", text)      # pit has no exits

    def test_start_room_marked(self):
        text, _ = self._card("room", "hall")
        self.assertIn("START", text)

    def test_npc_card(self):
        text, links = self._card("npc", "guard")
        self.assertIn("Stone Guard", text)
        self.assertIn("PERSONA", text)
        self.assertIn("Sworn to the vault", text)
        self.assertIn("room:cave", {ref for _, ref in links})   # @ its location
        self.assertIn("item:key", {ref for _, ref in links})    # holds the key

    def test_item_card(self):
        text, links = self._card("item", "key")
        self.assertIn("Iron Key", text)
        self.assertIn("FORGE", text)
        self.assertIn("iron", text)
        refs = {ref for _, ref in links}
        self.assertIn("room:cave", refs)     # resolves to the guard's room
        self.assertIn("npc:guard", refs)     # and offers the holder directly

    def test_unknown_degrades(self):
        text, links = self._card("room", "nope")
        self.assertIn("unknown", text.lower())
        self.assertEqual(links, [])
        self.assertEqual(card(self.view, self.map, "bogus", "x"), ("(nothing selected)", []))


@unittest.skipUnless(_HAS_TEXTUAL, "textual (the authoring extra) is not installed")
class WorkbenchAppTests(unittest.IsolatedAsyncioTestCase):
    """Drive the real Textual runtime: mount the app, then assert the panes wire up —
    the tree builds, the card fills, the search filters, and a jump-link navigates."""

    def _app(self):
        from authoring.app import WorkbenchApp
        return WorkbenchApp(_demo_view())

    async def test_boots_on_start_room(self):
        from textual.widgets import Static, Tree
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            card_text = str(app.query_one("#card", Static).render())
            self.assertIn("Great Hall", card_text)          # the start room
            tree = app.query_one("#nav", Tree)
            # three category nodes: Rooms / NPCs / Items
            self.assertEqual(len(tree.root.children), 3)
            rooms_cat = tree.root.children[0]
            self.assertEqual(len(rooms_cat.children), 3)     # hall, cave, pit

    async def test_search_filters_tree(self):
        from textual.widgets import Input, Tree
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_focus(app.query_one("#search", Input))
            await pilot.press("g", "u", "a", "r", "d")
            await pilot.pause()
            tree = app.query_one("#nav", Tree)
            counts = {c.label.plain.split(" (")[0]: len(c.children)
                      for c in tree.root.children}
            self.assertEqual(counts["NPCs"], 1)             # only the guard matches
            self.assertEqual(counts["Rooms"], 0)
            self.assertEqual(counts["Items"], 0)

    async def test_tree_selection_updates_card(self):
        from textual.widgets import Static, Tree
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            tree = app.query_one("#nav", Tree)
            cave_leaf = tree.root.children[0].children[1]    # Rooms → cave
            app.on_tree_node_selected(Tree.NodeSelected(cave_leaf))
            await pilot.pause()
            self.assertIn("Damp Cave", str(app.query_one("#card", Static).render()))

    async def test_jump_link_navigates(self):
        from textual.widgets import Static, OptionList
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("room", "hall")                      # start: exit north → cave
            await pilot.pause()
            idx = next(i for i, (_, ref) in enumerate(app._links) if ref == "room:cave")
            ol = app.query_one("#links", OptionList)
            app.on_option_list_option_selected(
                OptionList.OptionSelected(ol, ol.get_option_at_index(idx), idx))
            await pilot.pause()
            self.assertIn("Damp Cave", str(app.query_one("#card", Static).render()))

    async def test_from_path_shipped_world(self):
        from authoring.app import WorkbenchApp
        from textual.widgets import Static
        app = WorkbenchApp.from_path(GAME_WORLD)
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertTrue(str(app.query_one("#card", Static).render()).strip())

    async def test_selection_syncs_tree_cursor(self):
        # Carried from slice 1: selecting via a non-tree path must move the navigator
        # cursor onto that entity, keeping the two panes in step.
        from textual.widgets import Tree
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("room", "cave")
            await pilot.pause()
            self.assertEqual(app.query_one("#nav", Tree).cursor_node.data, ("room", "cave"))
            app._select("npc", "guard")
            await pilot.pause()
            self.assertEqual(app.query_one("#nav", Tree).cursor_node.data, ("npc", "guard"))


@unittest.skipUnless(_HAS_TEXTUAL, "textual (the authoring extra) is not installed")
class PlayScreenTests(unittest.IsolatedAsyncioTestCase):
    """The modal play screen boots the sandbox and wires it to the transcript. Dry-run
    (FakeProvider) so it makes no model call; the sandbox itself is tested at the text
    level in tests/test_sandbox.py."""

    async def test_enter_play_boots_and_leaves(self):
        from authoring.app import WorkbenchApp
        from authoring.play import PlayScreen
        from textual.widgets import RichLog, Input
        app = WorkbenchApp.from_path(GAME_WORLD)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._dry_run = True                 # offline, no model call
            app.action_play()                   # push the play screen for the start room
            await pilot.pause()
            self.assertIsInstance(app.screen, PlayScreen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            log = app.screen.query_one("#transcript", RichLog)
            self.assertGreater(len(log.lines), 0)       # the boot look rendered

            app.screen.set_focus(app.screen.query_one("#cmd", Input))
            await pilot.press("l", "o", "o", "k", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertGreater(len(log.lines), 0)       # still rendering, no crash

            app.screen.action_exit_play()
            await pilot.pause()
            self.assertNotIsInstance(app.screen, PlayScreen)   # back to the Explorer

    async def test_play_target_is_selected_entitys_room(self):
        # An NPC/item selection plays that entity's room, not the entity.
        from authoring.app import WorkbenchApp
        app = WorkbenchApp.from_path(GAME_WORLD)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("npc", "hermit")        # Odd the Hermit sits in cave_mouth
            self.assertEqual(app._target_room(), "cave_mouth")


if __name__ == "__main__":
    unittest.main()
