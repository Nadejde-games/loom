"""The authoring draft + safety gate (Phase 8, slice 3) — offline, zero LLM.

Proves the one invariant the whole slice rests on: a candidate that does not survey clean
is **rejected without touching the staged world**; a clean one commits; the checkpoint
stack push/pops. No model, no network — the gate is pure code and tested as such.
"""
import copy
import unittest

from loom.world import World, Location, Npc, Item
from loom.worlddraft import (
    Draft, edit_entity, spawn_entity, fold_region, propose, commit, undo, diff_worlds,
    diff_status,
)


def _world() -> World:
    """A tiny world that surveys perfectly clean: hall <-> cave (reciprocal), a keeper in
    the hall with a persona, a brass key on the hall floor."""
    w = World()
    w.add_location(Location(id="hall", name="The Hall", description="A stone hall.",
                            exits={"north": "cave"}))
    w.add_location(Location(id="cave", name="The Cave", description="A damp cave.",
                            exits={"south": "hall"}))
    w.add_entity(Npc(id="keeper", name="The Keeper", description="An old keeper.",
                     location_id="hall", persona={"backstory": "Guards the hall."},
                     wanders=False))
    w.add_entity(Item(id="key", name="a brass key", description="A worn brass key.",
                      holder="hall", aliases=["key"], portable=True))
    w.meta = {"weather": {"enabled": False}}
    return w


def _draft() -> Draft:
    return Draft.from_world(_world(), "hall")


def _snapshot(d: Draft):
    return (d.start, copy.deepcopy(d.locations), copy.deepcopy(d.npcs),
            copy.deepcopy(d.items))


class DraftLoadTests(unittest.TestCase):
    def test_from_world_surveys_clean(self):
        d = _draft()
        v = d.view()
        self.assertEqual(v.start, "hall")
        self.assertFalse(v.errors)
        self.assertEqual({r.id for r in v.rooms}, {"hall", "cave"})

    def test_meta_carried_for_save(self):
        d = _draft()
        wj = d.to_world_json()
        self.assertEqual(wj["start_location"], "hall")
        self.assertEqual(wj["weather"], {"enabled": False})
        self.assertEqual({l["id"] for l in wj["locations"]}, {"hall", "cave"})
        # canonical keys are real lists, not clobbered by meta
        self.assertIsInstance(wj["npcs"], list)


class EditTests(unittest.TestCase):
    def test_flavour_edit_commits(self):
        d = _draft()
        cand = edit_entity(d, "hall", {"name": "The Great Hall",
                                       "description": "A grand stone hall."})
        p = propose(d, cand)
        self.assertTrue(p.ok)
        self.assertTrue(commit(d, p))
        by_id = {l["id"]: l for l in d.locations}
        self.assertEqual(by_id["hall"]["name"], "The Great Hall")

    def test_dangling_exit_rejected_and_draft_untouched(self):
        d = _draft()
        before = _snapshot(d)
        # replace hall's only exit with one that leads nowhere -> dangling-exit error
        cand = edit_entity(d, "hall", {"exits": {"north": "nowhere"}})
        p = propose(d, cand)
        self.assertFalse(p.ok)
        self.assertIn("dangling-exit", {f.code for f in p.view.errors})
        self.assertFalse(commit(d, p))                # refused
        self.assertEqual(_snapshot(d), before)        # staged world is byte-identical

    def test_edit_unknown_id_raises(self):
        d = _draft()
        with self.assertRaises(KeyError):
            edit_entity(d, "ghost", {"name": "X"})

    def test_propose_does_not_mutate_draft(self):
        d = _draft()
        before = _snapshot(d)
        cand = edit_entity(d, "hall", {"name": "Changed"})
        propose(d, cand)                              # no commit
        self.assertEqual(_snapshot(d), before)


class SpawnTests(unittest.TestCase):
    def test_spawn_npc_into_real_room_commits(self):
        d = _draft()
        cand, nid = spawn_entity(d, "npc", name="Smith", description="A gruff smith.",
                                 where="hall", persona={"backstory": "Works iron."})
        p = propose(d, cand)
        self.assertTrue(p.ok)
        self.assertTrue(commit(d, p))
        self.assertIn(nid, {n["id"] for n in d.npcs})

    def test_spawn_npc_into_unknown_room_rejected(self):
        d = _draft()
        before = _snapshot(d)
        cand, _ = spawn_entity(d, "npc", name="Ghost", where="void")
        p = propose(d, cand)
        self.assertFalse(p.ok)
        self.assertIn("bad-location", {f.code for f in p.view.errors})
        self.assertFalse(commit(d, p))
        self.assertEqual(_snapshot(d), before)

    def test_spawn_item_bad_holder_rejected(self):
        d = _draft()
        cand, _ = spawn_entity(d, "item", name="a lantern", where="void")
        p = propose(d, cand)
        self.assertFalse(p.ok)
        self.assertIn("bad-holder", {f.code for f in p.view.errors})

    def test_spawn_mints_distinct_ids(self):
        d = _draft()
        cand1, id1 = spawn_entity(d, "item", name="a torch", where="hall")
        self.assertTrue(commit(d, propose(d, cand1)))
        cand2, id2 = spawn_entity(d, "item", name="a torch", where="hall")
        self.assertNotEqual(id1, id2)                 # code-owned namespace, no collision

    def test_spawn_bad_kind_raises(self):
        with self.assertRaises(ValueError):
            spawn_entity(_draft(), "room", name="nope", where="hall")


class FoldRegionTests(unittest.TestCase):
    def test_fold_clean_region_commits_and_grows_reachable(self):
        d = _draft()
        region = {"locations": [{"id": "smithy", "name": "The Smithy",
                                 "description": "A forge glows red.",
                                 "exits": {"west": "hall"}}],
                  "npcs": [], "items": []}
        attach = {"anchor": "hall", "direction": "east", "entry": "smithy", "back": "west"}
        cand = fold_region(d, region, attach)
        p = propose(d, cand)
        self.assertTrue(p.ok)
        self.assertTrue(commit(d, p))
        v = d.view()
        self.assertIn("smithy", {r.id for r in v.rooms})
        self.assertIn("smithy", v.reachable_ids)      # reachable from hall via the attach edge

    def test_fold_diff_names_the_change(self):
        d = _draft()
        region = {"locations": [{"id": "smithy", "name": "The Smithy",
                                 "description": "A forge.", "exits": {"west": "hall"}}]}
        attach = {"anchor": "hall", "direction": "east", "entry": "smithy", "back": "west"}
        diff = diff_worlds(d, fold_region(d, region, attach))
        self.assertIn("+ room smithy (The Smithy)", diff)
        self.assertTrue(any(line.startswith("~ room hall") for line in diff))


class UndoTests(unittest.TestCase):
    def test_undo_restores_prior_state(self):
        d = _draft()
        before = _snapshot(d)
        cand = edit_entity(d, "hall", {"name": "Renamed"})
        commit(d, propose(d, cand))
        self.assertNotEqual(_snapshot(d), before)
        self.assertTrue(undo(d))
        self.assertEqual(_snapshot(d), before)

    def test_multi_level_stack(self):
        d = _draft()
        commit(d, propose(d, edit_entity(d, "hall", {"name": "Great Hall"})))
        commit(d, propose(d, spawn_entity(d, "npc", name="Smith", where="hall",
                                          persona={"backstory": "x"})[0]))
        self.assertEqual(len(d.checkpoints), 2)
        self.assertTrue(undo(d))                      # undo the spawn
        self.assertEqual({n["id"] for n in d.npcs}, {"keeper"})
        self.assertEqual({l["id"]: l["name"] for l in d.locations}["hall"], "Great Hall")
        self.assertTrue(undo(d))                      # undo the rename
        self.assertEqual({l["id"]: l["name"] for l in d.locations}["hall"], "The Hall")
        self.assertFalse(undo(d))                     # stack empty

    def test_commit_refused_leaves_stack_empty(self):
        d = _draft()
        cand = edit_entity(d, "hall", {"exits": {"north": "nowhere"}})
        self.assertFalse(commit(d, propose(d, cand)))
        self.assertEqual(d.checkpoints, [])


class BaselineDiffTests(unittest.TestCase):
    """The saved-baseline delta model the workbench draws badges + before/after from."""

    def test_fresh_draft_has_no_deltas(self):
        d = _draft()
        self.assertIsNotNone(d.baseline)
        self.assertEqual(diff_status(d.baseline, d.dicts()), {})   # loaded == baseline

    def test_edit_shows_as_tilde_until_saved(self):
        d = _draft()
        commit(d, propose(d, edit_entity(d, "hall", {"name": "The Great Hall"})))
        self.assertEqual(diff_status(d.baseline, d.dicts()), {("room", "hall"): "~"})

    def test_spawn_shows_as_plus(self):
        d = _draft()
        cand, new_id = spawn_entity(d, "item", name="a torch", where="hall")
        commit(d, propose(d, cand))
        self.assertEqual(diff_status(d.baseline, d.dicts()), {("item", new_id): "+"})

    def test_removed_entity_shows_as_minus(self):
        # No delete tool exists yet, so exercise diff_status directly against a hand-built after.
        d = _draft()
        after = d.dicts()
        after["items"] = [i for i in after["items"] if i["id"] != "key"]
        self.assertEqual(diff_status(d.baseline, after), {("item", "key"): "-"})

    def test_mark_saved_clears_deltas(self):
        d = _draft()
        commit(d, propose(d, edit_entity(d, "hall", {"name": "The Great Hall"})))
        self.assertTrue(diff_status(d.baseline, d.dicts()))        # dirty before save
        d.mark_saved()
        self.assertEqual(diff_status(d.baseline, d.dicts()), {})   # clean after save
        # a further edit is a delta against the NEW baseline
        commit(d, propose(d, edit_entity(d, "hall", {"description": "Warmer now."})))
        self.assertEqual(diff_status(d.baseline, d.dicts()), {("room", "hall"): "~"})


if __name__ == "__main__":
    unittest.main()
