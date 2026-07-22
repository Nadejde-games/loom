"""Offline tests for the write side (B7): the skeleton framer, the repair helpers, and
the full author_region loop against scripted providers. Deterministic, no network — the
model is faked, and the code-owned structure is asserted directly (reciprocity, ids,
connectivity), which is exactly the half that must never depend on the model."""
import unittest

from loom.world import World, Location, Npc, Item
from loom.atlas import survey, Finding, OPPOSITE
from loom.authoring import (
    frame_skeleton, normalize_plan, apply_flavour, entity_kind, world_to_dicts,
    assemble, repair_targets, targets_signature, stalled, distinct_where,
    format_report, plan_schema, flavour_schema,
)
from loom.ai.author import author_region, propose_plan
from loom.ai.provider import FakeProvider


# --------------------------------------------------------------------------- helpers
def _reach(locations, start):
    by_id = {loc["id"]: loc for loc in locations}
    seen, stack = set(), [start]
    while stack:
        rid = stack.pop()
        if rid in seen or rid not in by_id:
            continue
        seen.add(rid)
        stack.extend(by_id[rid]["exits"].values())
    return seen


def _reciprocal(locations):
    """Every internal edge has its opposite back-edge — the invariant the framer must
    guarantee. (Edges to ids outside this set — an attach edge — are skipped.)"""
    by_id = {loc["id"]: loc for loc in locations}
    for loc in locations:
        for direction, target in loc["exits"].items():
            if target in by_id:
                back = by_id[target]["exits"].get(OPPOSITE[direction])
                if back != loc["id"]:
                    return False
    return True


_PLAN = {
    "rooms": [{"slug": "moor", "role": "open"},
              {"slug": "track", "role": "path"},
              {"slug": "tower", "role": "ruin"}],
    "links": [{"from": "moor", "to": "track", "direction": "east"},
              {"from": "track", "to": "tower", "direction": "east"}],
    "npcs": [{"slug": "keeper", "room": "tower", "role": "keeper", "anchored": True}],
    "items": [{"slug": "horn", "room": "tower", "role": "relic"}],
    "entry": "moor",
}


def _base_world():
    """A tiny, clean two-room world to extend (both rooms flavoured, reciprocal)."""
    w = World()
    w.add_location(Location(id="home", name="Home", description="A warm room.",
                            exits={"north": "yard"}))
    w.add_location(Location(id="yard", name="The Yard", description="An open yard.",
                            exits={"south": "home"}))
    return w, "home"


# --------------------------------------------------------------------- the framer
class FrameSkeletonTests(unittest.TestCase):
    def test_reciprocal_edges(self):
        draft = frame_skeleton(_PLAN)
        self.assertTrue(_reciprocal(draft.locations))

    def test_all_rooms_reachable_from_entry(self):
        draft = frame_skeleton(_PLAN)
        ids = {loc["id"] for loc in draft.locations}
        self.assertEqual(_reach(draft.locations, draft.entry) & ids, ids)

    def test_orphan_room_is_connected_in_code(self):
        # 'lonely' is in no link — the framer must wire it in anyway.
        plan = {"rooms": [{"slug": "a"}, {"slug": "b"}, {"slug": "lonely"}],
                "links": [{"from": "a", "to": "b", "direction": "north"}],
                "entry": "a"}
        draft = frame_skeleton(plan)
        ids = {loc["id"] for loc in draft.locations}
        self.assertIn("lonely", ids)
        self.assertEqual(_reach(draft.locations, draft.entry) & ids, ids)
        self.assertTrue(_reciprocal(draft.locations))

    def test_direction_collision_is_resolved(self):
        # Two links leave 'a' in the same proposed direction; code must split them.
        plan = {"rooms": [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}],
                "links": [{"from": "a", "to": "b", "direction": "north"},
                          {"from": "a", "to": "c", "direction": "north"}],
                "entry": "a"}
        draft = frame_skeleton(plan)
        a = next(loc for loc in draft.locations if loc["id"] == "a")
        self.assertEqual(len(a["exits"]), 2)                 # two distinct exits
        self.assertEqual(len(set(a["exits"].values())), 2)   # to two distinct rooms
        self.assertTrue(_reciprocal(draft.locations))

    def test_ids_never_collide_with_existing(self):
        draft = frame_skeleton(_PLAN, existing_ids={"moor"})
        ids = [loc["id"] for loc in draft.locations]
        self.assertIn("moor_2", ids)          # the region's 'moor' got renamed
        self.assertNotIn("moor", ids)         # never collides with the existing id

    def test_npc_and_item_land_on_real_rooms(self):
        draft = frame_skeleton(_PLAN)
        room_ids = {loc["id"] for loc in draft.locations}
        self.assertTrue(all(n["location"] in room_ids for n in draft.npcs))
        self.assertTrue(all(it["holder"] in room_ids for it in draft.items))
        self.assertFalse(draft.npcs[0]["wanders"])           # anchored -> stays put

    def test_attach_edge_is_reciprocal_and_recorded(self):
        draft = frame_skeleton(_PLAN, existing_ids={"yard"}, anchor="yard",
                               anchor_used_dirs={"south"})
        self.assertEqual(draft.attach["anchor"], "yard")
        self.assertEqual(draft.attach["entry"], draft.entry)
        entry = next(loc for loc in draft.locations if loc["id"] == draft.entry)
        # the region side of the attach edge points back at the anchor
        self.assertEqual(entry["exits"][draft.attach["back"]], "yard")
        self.assertNotEqual(draft.attach["direction"], "south")   # 'south' was taken

    def test_greenfield_has_no_attach(self):
        draft = frame_skeleton(_PLAN)     # no anchor
        self.assertEqual(draft.attach, {})


class NormalizePlanTests(unittest.TestCase):
    def test_bad_direction_dropped_to_free_choice(self):
        p = normalize_plan({"rooms": [{"slug": "a"}, {"slug": "b"}],
                            "links": [{"from": "a", "to": "b",
                                       "direction": "sideways"}], "entry": "a"})
        self.assertEqual(p["links"][0]["direction"], "")

    def test_junk_degrades_to_one_room(self):
        p = normalize_plan(None)
        self.assertEqual(len(p["rooms"]), 1)
        self.assertEqual(p["entry"], p["rooms"][0]["slug"])

    def test_entry_defaults_to_first_room(self):
        p = normalize_plan({"rooms": [{"slug": "hall"}, {"slug": "cell"}]})
        self.assertEqual(p["entry"], "hall")

    def test_npc_room_ref_falls_back_to_entry(self):
        p = normalize_plan({"rooms": [{"slug": "hall"}],
                            "npcs": [{"slug": "ghost", "room": "nowhere"}]})
        self.assertEqual(p["npcs"][0]["room"], "hall")


# ------------------------------------------------------------------ flavour + assemble
class FlavourAndAssembleTests(unittest.TestCase):
    def test_apply_flavour_touches_only_flavour(self):
        draft = frame_skeleton(_PLAN)
        room = draft.locations[0]
        original_exits = dict(room["exits"])
        apply_flavour(draft.region(), {room["id"]: {
            "name": "The Windswept Moor", "description": "Heather bows flat.",
            "exits": {"hacked": "nowhere"}}})   # a structure key must be ignored
        self.assertEqual(room["name"], "The Windswept Moor")
        self.assertEqual(room["description"], "Heather bows flat.")
        self.assertEqual(room["exits"], original_exits)   # structure untouched

    def test_apply_flavour_skips_empty_strings(self):
        draft = frame_skeleton(_PLAN)
        room = draft.locations[0]
        placeholder = room["description"]
        apply_flavour(draft.region(), {room["id"]: {"description": "   "}})
        self.assertEqual(room["description"], placeholder)   # empty -> not applied

    def test_persona_and_aliases_coerced(self):
        draft = frame_skeleton(_PLAN)
        npc, item = draft.npcs[0], draft.items[0]
        apply_flavour(draft.region(), {
            npc["id"]: {"persona": {"backstory": "b", "traits": ["t"],
                                    "goals": ["g"], "voice": "v"}},
            item["id"]: {"aliases": ["horn", 7]}})
        self.assertEqual(npc["persona"]["voice"], "v")
        self.assertEqual(item["aliases"], ["horn", "7"])   # coerced to strings

    def test_entity_kind(self):
        draft = frame_skeleton(_PLAN)
        region = draft.region()
        self.assertEqual(entity_kind(region, draft.locations[0]["id"]), "room")
        self.assertEqual(entity_kind(region, draft.npcs[0]["id"]), "npc")
        self.assertEqual(entity_kind(region, draft.items[0]["id"]), "item")
        self.assertEqual(entity_kind(region, "home"), "")   # not the region's

    def test_assemble_does_not_mutate_base(self):
        base, _ = _base_world()
        base_dicts = world_to_dicts(base)
        draft = frame_skeleton(_PLAN, existing_ids=set(base.locations),
                               anchor="yard", anchor_used_dirs={"south"})
        assemble(draft.region(), base=base_dicts, attach=draft.attach)
        # the loaded base world's anchor room still has only its original exit
        self.assertEqual(base.locations["yard"].exits, {"south": "home"})

    def test_framed_and_flavoured_region_surveys_clean(self):
        base, start = _base_world()
        base_dicts = world_to_dicts(base)
        draft = frame_skeleton(_PLAN, existing_ids=set(base.locations),
                               anchor="yard", anchor_used_dirs={"south"})
        region = draft.region()
        # give every region entity real flavour so nothing is thin
        flav = {}
        for loc in region["locations"]:
            flav[loc["id"]] = {"name": loc["id"].title(),
                               "description": "A described place, several words long."}
        for n in region["npcs"]:
            flav[n["id"]] = {"persona": {"backstory": "A long-kept vigil here.",
                                         "traits": ["weary"], "goals": ["keep watch"],
                                         "voice": "low"}}
        for it in region["items"]:
            flav[it["id"]] = {"description": "A brass thing, split along a seam."}
        apply_flavour(region, flav)
        view = survey(assemble(region, base=base_dicts, attach=draft.attach), start)
        self.assertEqual(view.errors, [])
        self.assertEqual([f for f in view.warnings if f.code == "thin-content"], [])


# ---------------------------------------------------------------- repair helpers
class RepairHelperTests(unittest.TestCase):
    def _fs(self):
        return [Finding("error", "dangling-exit", "moor", "->x"),
                Finding("warning", "thin-content", "keeper", "empty persona"),
                Finding("warning", "one-way-exit", "moor", "no way back"),
                Finding("warning", "thin-content", "home", "base room")]

    def test_repair_targets_scopes_to_region_errors_and_thin(self):
        region_ids = {"moor", "keeper"}   # 'home' is existing-world, frozen
        got = repair_targets(self._fs(), region_ids)
        codes = sorted((f.code, f.where) for f in got)
        self.assertEqual(codes, [("dangling-exit", "moor"),
                                 ("thin-content", "keeper")])

    def test_stalled(self):
        self.assertFalse(stalled(None, None, ("a",), 1))         # first round
        self.assertTrue(stalled(("a",), 2, ("a",), 2))           # same set
        self.assertTrue(stalled(("a", "b"), 2, ("c",), 2))       # count didn't fall
        self.assertFalse(stalled(("a", "b"), 2, ("a",), 1))      # progress

    def test_distinct_where_errors_first(self):
        order = distinct_where(self._fs())
        self.assertEqual(order[0], "moor")        # the error leads
        self.assertIn("keeper", order)
        self.assertEqual(len(order), len(set(order)))

    def test_format_report_is_localized(self):
        report = format_report(self._fs())
        self.assertIn("dangling-exit", report)
        self.assertIn("@ moor", report)
        self.assertIn("[error]", report)


# ----------------------------------------------------------- the author_region loop
class _Scripted:
    """A provider that returns canned plan/flavour JSON, distinguished by the schema it
    is handed. ``rooms_empty_until_report`` forces a repair round: rooms come back with
    an empty description on the first pass (-> thin-content) and full once the atlas
    report is present."""
    name = "scripted"

    def __init__(self, rooms_empty_until_report=False, rooms_always_empty=False):
        self.rooms_empty_until_report = rooms_empty_until_report
        self.rooms_always_empty = rooms_always_empty

    async def complete(self, system, messages, schema=None, temperature=None):
        props = (schema or {}).get("properties", {})
        if "links" in props:                         # the plan pass
            import json
            return json.dumps(_PLAN)
        if "persona" in props:                       # an npc flavour call
            import json
            return json.dumps({"name": "The Keeper", "description": "A hooded figure.",
                               "persona": {"backstory": "You have tended the fire for "
                                           "thirty winters.", "traits": ["weary", "proud"],
                                           "goals": ["keep the flame"], "voice": "low"}})
        if "aliases" in props:                       # an item flavour call
            import json
            return json.dumps({"name": "a cracked signal-horn",
                               "description": "A brass horn split along one seam.",
                               "aliases": ["horn"]})
        # a room flavour call
        import json
        has_report = "validator found problems" in system
        empty = self.rooms_always_empty or (self.rooms_empty_until_report
                                            and not has_report)
        return json.dumps({"name": "The Windswept Moor",
                           "description": "" if empty else
                           "Heather bows flat under a wind that never rests."})


class AuthorRegionTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_surveys_clean(self):
        base, start = _base_world()
        res = await author_region(_Scripted(), "a moor and a tower",
                                  base_world=base, start=start, attach_to="yard")
        self.assertTrue(res.ok)
        self.assertEqual(res.rounds, 0)
        self.assertEqual(res.view.errors, [])
        region_ids = {loc["id"] for loc in res.region["locations"]}
        thin = [f for f in res.view.findings
                if f.code == "thin-content" and f.where in region_ids]
        self.assertEqual(thin, [])
        self.assertEqual(len(res.region["locations"]), 3)
        self.assertEqual(res.attach["anchor"], "yard")

    async def test_degrades_when_no_plan(self):
        base, start = _base_world()
        # FakeProvider returns prose, not JSON -> no plan -> a clean failure, not a crash
        res = await author_region(FakeProvider(), "a moor",
                                  base_world=base, start=start, attach_to="yard")
        self.assertFalse(res.ok)
        self.assertIsNone(res.view)
        self.assertIn("plan", res.reason)

    async def test_repair_round_fixes_thin_content(self):
        base, start = _base_world()
        res = await author_region(_Scripted(rooms_empty_until_report=True),
                                  "a moor", base_world=base, start=start,
                                  attach_to="yard", cap=3)
        self.assertTrue(res.ok)               # no structural errors either way
        self.assertGreaterEqual(res.rounds, 1)
        region_ids = {loc["id"] for loc in res.region["locations"]}
        thin = [f for f in res.view.findings
                if f.code == "thin-content" and f.where in region_ids]
        self.assertEqual(thin, [])            # the repair round cleared it

    async def test_stall_guard_terminates(self):
        base, start = _base_world()
        res = await author_region(_Scripted(rooms_always_empty=True),
                                  "a moor", base_world=base, start=start,
                                  attach_to="yard", cap=3)
        # rooms never improve -> the loop must stop (stall guard), not run forever
        self.assertLessEqual(res.rounds, 3)
        self.assertTrue(res.ok)               # thin-content is a warning, not an error


class SchemaShapeTests(unittest.TestCase):
    def test_plan_schema_flat_with_direction_enum(self):
        s = plan_schema()
        self.assertEqual(s["type"], "object")
        self.assertFalse(s["additionalProperties"])
        direction = s["properties"]["links"]["items"]["properties"]["direction"]
        self.assertIn("north", direction["enum"])

    def test_flavour_schema_by_kind(self):
        self.assertIn("persona", flavour_schema("npc")["properties"])
        self.assertIn("aliases", flavour_schema("item")["properties"])
        self.assertNotIn("persona", flavour_schema("room")["properties"])


if __name__ == "__main__":
    unittest.main()
