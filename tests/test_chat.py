"""The authoring-agent chat panel (Phase 8, slice 3) — UI smoke, no network.

Drives ``authoring.chat.ChatPanel`` through Textual's ``run_test()`` + ``Pilot`` with an
injected session and a ``FunctionModel``-backed agent, so the propose→confirm control flow,
the discrete contextual controls, the pane toggle, and the refresh-on-apply are all exercised
with no model and no OpenRouter call. Skipped cleanly when either optional extra (textual,
pydantic-ai) is absent.
"""
import json
import unittest

try:
    from textual.app import App
    from textual.widgets import Button, Checkbox, Input, Static, TextArea, Tree
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False

try:
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from pydantic_ai.messages import ModelResponse
    _HAS_PYDANTIC_AI = True
except ImportError:
    _HAS_PYDANTIC_AI = False

from loom.world import World, Location, Npc, Item

_READY = _HAS_TEXTUAL and _HAS_PYDANTIC_AI

if _READY:
    from authoring.agent import AuthoringSession, build_agent
    from authoring.app import WorkbenchApp
    from authoring.chat import ChatPanel
    from loom.atlas import survey
    from loom.worlddraft import Draft


def _world() -> World:
    w = World()
    w.add_location(Location(id="hall", name="The Hall", description="A stone hall.",
                            exits={"north": "cave"}))
    w.add_location(Location(id="cave", name="The Cave", description="A damp cave.",
                            exits={"south": "hall"}))
    w.add_entity(Npc(id="keeper", name="The Keeper", description="An old keeper.",
                     location_id="hall", persona={"backstory": "Guards the hall."}))
    w.add_entity(Item(id="key", name="a brass key", description="A worn key.",
                      holder="hall", aliases=["key"]))
    return w


def _scripted(steps, final="Done."):
    """A STREAMING FunctionModel — emits each (tool, args) as a streamed tool-call delta, in
    order, then the final text. The chat always streams (for live tool-activity progress), so
    its tests use a streaming model to exercise that path for real."""
    async def sfn(messages, info):
        i = sum(1 for m in messages if isinstance(m, ModelResponse))
        if i < len(steps):
            name, args = steps[i]
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args),
                                    tool_call_id=str(i))}
        else:
            yield final
    return FunctionModel(stream_function=sfn)


if _READY:
    class _Host(App):
        """A minimal host that composes the ChatPanel under test as its content."""
        def __init__(self, panel):
            super().__init__()
            self._panel = panel

        def compose(self):
            yield self._panel


@unittest.skipUnless(_READY, "textual + pydantic-ai (the authoring extra) are not installed")
class ChatPanelTests(unittest.IsolatedAsyncioTestCase):
    def _chat(self, steps):
        session = AuthoringSession(draft=Draft.from_world(_world(), "hall"))
        return ChatPanel(session=session, agent=build_agent(_scripted(steps))), session

    async def test_mounts_with_controls_hidden(self):
        chat, _ = self._chat([("world_search", {"query": "hall"})])
        async with _Host(chat).run_test() as pilot:
            await pilot.pause()
            # nothing pending, nothing to undo -> apply/discard/undo are hidden (discrete)
            self.assertFalse(chat.query_one("#confirm", Button).display)
            self.assertFalse(chat.query_one("#reject", Button).display)
            self.assertFalse(chat.query_one("#undo", Button).display)
            self.assertFalse(chat.query_one("#msg", Input).disabled)

    async def test_turn_proposes_then_confirm_button_commits(self):
        chat, session = self._chat(
            [("entity_edit", {"entity_id": "hall", "name": "The Great Hall"})])
        async with _Host(chat).run_test() as pilot:
            await pilot.pause()
            await chat._turn("rename the hall to The Great Hall")
            # a clean change is staged and the Apply control is revealed
            self.assertIsNotNone(session.pending)
            self.assertTrue(chat.query_one("#confirm", Button).display)
            # the staged world is untouched until the human confirms
            self.assertEqual({l["id"]: l["name"] for l in session.draft.locations}["hall"],
                             "The Hall")
            await pilot.pause()                      # let the just-revealed button lay out
            await pilot.click("#confirm")
            await pilot.pause()
        self.assertIsNone(session.pending)
        self.assertEqual({l["id"]: l["name"] for l in session.draft.locations}["hall"],
                         "The Great Hall")

    async def test_reject_button_discards(self):
        chat, session = self._chat(
            [("entity_spawn", {"kind": "item", "name": "a torch", "where": "hall"})])
        async with _Host(chat).run_test() as pilot:
            await pilot.pause()
            await chat._turn("add a torch to the hall")
            self.assertIsNotNone(session.pending)
            await pilot.pause()                      # let the just-revealed button lay out
            await pilot.click("#reject")
            await pilot.pause()
        self.assertIsNone(session.pending)
        self.assertEqual({i["id"] for i in session.draft.items}, {"key"})

    async def test_transcript_is_readonly_textarea_and_accumulates(self):
        chat, _ = self._chat([("world_search", {"query": "hall"})])
        async with _Host(chat).run_test() as pilot:
            await pilot.pause()
            ta = chat.query_one("#transcript", TextArea)
            self.assertTrue(ta.read_only)          # selectable + copyable, not user-editable
            await chat._turn("find the hall")
            self.assertIn("you › find the hall", ta.text)
            self.assertIn("agent ›", ta.text)

    async def test_tool_activity_streams_into_transcript(self):
        chat, _ = self._chat([("world_search", {"query": "hall"})])
        async with _Host(chat).run_test() as pilot:
            await pilot.pause()
            await chat._turn("find the hall")
            text = chat.query_one("#transcript", TextArea).text
            self.assertIn("⏳ world_search", text)     # the agent is working — a tool started
            self.assertIn("✓ world_search", text)      # …and returned

    async def test_save_log_action_records(self):
        chat, _ = self._chat([("world_search", {"query": "hall"})])
        async with _Host(chat).run_test() as pilot:
            await pilot.pause()
            chat._lines.append("you › hello")
            chat.action_save_log()
            await pilot.pause()
            self.assertTrue(any("authoring-chat.log" in line for line in chat._lines))


@unittest.skipUnless(_READY, "textual + pydantic-ai (the authoring extra) are not installed")
class ExplorerChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _app(self):
        draft = Draft.from_world(_world(), "hall")
        view = survey(draft.world(), "hall", source="test")
        return WorkbenchApp(view, source_label="test", world_path="test.json", draft=draft)

    async def test_chat_toggles_left_pane_inspector_stays(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            nav = app.query_one("#nav-view")
            panel = app.query_one(ChatPanel)
            inspector = app.query_one("#inspector")
            self.assertTrue(nav.display)             # starts on the navigator
            self.assertFalse(panel.display)
            app.action_chat()                        # -> agent chat
            self.assertFalse(nav.display)
            self.assertTrue(panel.display)
            self.assertTrue(inspector.display)       # inspector visible in BOTH modes
            app.action_chat()                        # -> back to the navigator
            self.assertTrue(nav.display)
            self.assertFalse(panel.display)
            self.assertTrue(inspector.display)

    async def test_explorer_inspector_refreshes_on_apply(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual({r.id: r.name for r in app.view.rooms}["hall"], "The Hall")
            app.action_chat()                        # reveal the embedded panel
            panel = app.query_one(ChatPanel)
            panel.agent = build_agent(_scripted(     # inject a scripted agent (no network)
                [("entity_edit", {"entity_id": "hall", "name": "The Great Hall"})]))
            await panel._turn("rename the hall")
            await pilot.pause()
            await pilot.click("#confirm")            # Apply -> on_session_changed -> repaints
            await pilot.pause()
        self.assertEqual({r.id: r.name for r in app.view.rooms}["hall"], "The Great Hall")

    async def test_proposed_edit_splits_inspector_before_after(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_chat()                        # reveal the chat panel
            panel = app.query_one(ChatPanel)
            panel.agent = build_agent(_scripted(
                [("entity_edit", {"entity_id": "hall", "name": "The Great Hall"})]))
            await panel._turn("rename the hall to The Great Hall")
            await pilot.pause()
            # while the change is pending the inspector splits: normal card hidden, split shown
            self.assertTrue(app.query_one("#diff-view").display)
            self.assertFalse(app.query_one("#card-scroll").display)
            before = str(app.query_one("#card-before", Static).render())
            after = str(app.query_one("#card-after", Static).render())
            self.assertIn("The Hall", before)        # top half: the entity as it is now
            self.assertIn("The Great Hall", after)   # bottom half: as proposed
            self.assertNotIn("The Great Hall", before)
            # discarding the proposal returns the inspector to the single card
            await pilot.pause()
            await pilot.click("#reject")
            await pilot.pause()
            self.assertIsNone(app._session.pending)
            self.assertFalse(app.query_one("#diff-view").display)
            self.assertTrue(app.query_one("#card-scroll").display)

    async def test_proposed_spawn_marks_new_entity_in_before_half(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_chat()
            panel = app.query_one(ChatPanel)
            panel.agent = build_agent(_scripted(
                [("entity_spawn", {"kind": "item", "name": "a torch", "where": "hall"})]))
            await panel._turn("add a torch to the hall")
            await pilot.pause()
            self.assertTrue(app.query_one("#diff-view").display)
            before = str(app.query_one("#card-before", Static).render())
            after = str(app.query_one("#card-after", Static).render())
            self.assertIn("not in the saved world", before.lower())   # nothing to compare against
            self.assertIn("a torch", after)                           # the proposed new item

    async def test_badge_and_split_persist_after_apply_until_save(self):
        import os as _os, json as _json, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = _os.path.join(d, "world.json")
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(Draft.from_world(_world(), "hall").to_world_json(), f)
            app = WorkbenchApp.from_path(path)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_chat()
                panel = app.query_one(ChatPanel)
                panel.agent = build_agent(_scripted(
                    [("entity_edit", {"entity_id": "hall", "name": "The Great Hall"})]))
                await panel._turn("rename the hall to The Great Hall")
                await pilot.pause()
                await pilot.click("#confirm")                 # Apply — committed, still UNSAVED
                await pilot.pause()
                self.assertIsNone(app._session.pending)
                self.assertEqual(app._status.get(("room", "hall")), "~")   # a delta vs saved
                self.assertIn("✎", str(app._find_node("room", "hall").label))  # badged in nav
                self.assertTrue(app.query_one("#diff-view").display)       # split persists
                # navigate away to an unchanged room (plain card), then back (split returns)
                app._select("room", "cave")
                await pilot.pause()
                self.assertFalse(app.query_one("#diff-view").display)
                app._select("room", "hall")
                await pilot.pause()
                self.assertTrue(app.query_one("#diff-view").display)
                before = str(app.query_one("#card-before", Static).render())
                after = str(app.query_one("#card-after", Static).render())
                self.assertIn("The Hall", before)            # saved side (baseline)
                self.assertIn("The Great Hall", after)       # working side
                # Save resets the baseline — badges and split clear
                app.action_save_world()
                await pilot.pause()
                self.assertEqual(app._status, {})
                self.assertFalse(app.query_one("#diff-view").display)
                self.assertEqual(str(app._find_node("room", "hall").label), "★ The Great Hall")

    async def test_save_applies_a_pending_proposal_and_persists_it(self):
        # Reproduces the reported bug end-to-end: propose via the agent, DON'T Apply, hit Save,
        # reload from disk — the change must be there.
        import os as _os, json as _json, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = _os.path.join(d, "world.json")
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(Draft.from_world(_world(), "hall").to_world_json(), f)
            app = WorkbenchApp.from_path(path)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.action_chat()
                panel = app.query_one(ChatPanel)
                panel.agent = build_agent(_scripted(
                    [("entity_edit", {"entity_id": "hall", "name": "The Great Hall"})]))
                await panel._turn("rename the hall to The Great Hall")
                await pilot.pause()
                self.assertIsNotNone(app._session.pending)    # proposed, NOT applied
                app.action_save_world()                        # Save should apply then persist
                await pilot.pause()
                self.assertIsNone(app._session.pending)        # applied by save
                self.assertEqual(app._status, {})              # saved -> no unsaved deltas
            with open(path, encoding="utf-8") as f:
                data = _json.load(f)
        self.assertEqual({l["id"]: l["name"] for l in data["locations"]}["hall"],
                         "The Great Hall")                     # it is really on disk

    async def test_hand_edit_badges_and_splits_the_entity(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("npc", "keeper")
            app.action_edit()
            await pilot.pause()
            app.query_one("#f-backstory", TextArea).text = "Haunted by the deep."
            app._apply_edit()
            await pilot.pause()
            self.assertEqual(app._status.get(("npc", "keeper")), "~")
            self.assertTrue(app.query_one("#diff-view").display)          # edited -> split
            self.assertIn("✎", str(app._find_node("npc", "keeper").label))

    async def test_inspector_edit_room_name_commits_through_gate(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("room", "hall")
            app.action_edit()                        # inspector -> edit form
            await pilot.pause()
            self.assertTrue(app._editing)
            app.query_one("#f-name", TextArea).text = "The Grand Hall"
            app._apply_edit()
            await pilot.pause()
            self.assertFalse(app._editing)           # back to the read card
            self.assertEqual({r.id: r.name for r in app.view.rooms}["hall"], "The Grand Hall")
            self.assertTrue(app.draft.checkpoints)   # went through the gate (undo checkpoint)

    async def test_edit_mode_does_not_filter_navigator(self):
        # regression: populating the edit form sets #f-name, whose Input.Changed bubbles to the
        # app — it must NOT be treated as a search query (only #search filters the navigator).
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("room", "hall")
            app.action_edit()                        # sets #f-name = "The Hall"
            await pilot.pause()
            leaves = [leaf for cat in app.query_one("#nav", Tree).root.children
                      for leaf in cat.children]
            self.assertEqual(len(leaves), 4)         # hall, cave, keeper, key — all still listed

    async def test_inspector_edit_npc_backstory(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("npc", "keeper")
            app.action_edit()
            await pilot.pause()
            app.query_one("#f-backstory", TextArea).text = "Haunted by the deep."
            app._apply_edit()
            await pilot.pause()
            keeper = next(n for n in app.draft.npcs if n["id"] == "keeper")
            self.assertEqual(keeper["persona"]["backstory"], "Haunted by the deep.")

    async def test_inspector_edit_npc_full_persona(self):
        app = self._app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._select("npc", "keeper")
            app.action_edit()
            await pilot.pause()
            app.query_one("#f-traits", TextArea).text = "wary, patient"
            app.query_one("#f-goals", TextArea).text = "guard the hall"
            app.query_one("#f-disposition", TextArea).text = "reticent"
            app.query_one("#f-wanders", Checkbox).value = True
            app._apply_edit()
            await pilot.pause()
            keeper = next(n for n in app.draft.npcs if n["id"] == "keeper")
            self.assertEqual(keeper["persona"]["traits"], ["wary", "patient"])
            self.assertEqual(keeper["persona"]["goals"], ["guard the hall"])
            self.assertEqual(keeper["persona"]["disposition"], "reticent")
            self.assertTrue(keeper["wanders"])
            self.assertEqual(keeper["persona"]["backstory"], "Guards the hall.")  # merge kept it

    async def test_manual_edit_then_save_persists(self):
        import json as _json
        import os as _os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = _os.path.join(d, "world.json")
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(Draft.from_world(_world(), "hall").to_world_json(), f)
            app = WorkbenchApp.from_path(path)
            async with app.run_test() as pilot:
                await pilot.pause()
                app._select("room", "hall")
                app.action_edit()
                await pilot.pause()
                app.query_one("#f-name", TextArea).text = "Saved Hall"
                app._apply_edit()
                await pilot.pause()
                app.action_save_world()
                await pilot.pause()
            with open(path, encoding="utf-8") as f:
                data = _json.load(f)
        self.assertEqual({l["id"]: l["name"] for l in data["locations"]}["hall"], "Saved Hall")


if __name__ == "__main__":
    unittest.main()
