"""The loot forge (Phase 4), offline: code rolls the mechanics, the model authors the
flavour, the forge assembles a real Item — all deterministic via a seeded RNG and a
scripted provider, no GPU. Proves the engine seam (a completed quest earns a
context-themed, code-classified reward) and the pure roll/schema/validation beneath it.
"""
import asyncio
import random
import unittest

from loom.world import World, Location
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.protocol import Channel
from loom.quest import COMPLETE
from loom.style import plain
from loom import loot
from loom.loot import (Tier, TagSpec, LootTables, Brief, roll_brief, flavour_schema,
                       parse_flavour, fallback_flavour, _draw_tags)
from loom.ai import loot as loot_ai


# --- stubs ------------------------------------------------------------------

class FakeSession:
    def __init__(self, sid="s1"):
        self.id = sid
        self.player_id = None
        self.sent = []
        self.closed = False

    async def send(self, channel, data):
        self.sent.append((channel, data))

    async def send_text(self, text):
        await self.send(Channel.TEXT, text)

    async def send_system(self, text):
        await self.send(Channel.SYSTEM, text)

    async def close(self):
        self.closed = True

    def texts(self):
        return "\n".join(plain(d) for (c, d) in self.sent if c == Channel.TEXT)

    def systems(self):
        return "\n".join(str(d) for (c, d) in self.sent if c == Channel.SYSTEM)


class ScriptedFlavourProvider:
    """Returns a fixed flavour payload — the model's authored name/lore/aliases."""
    name = "scripted-flavour"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def complete(self, system, messages, schema=None, temperature=None):
        self.calls += 1
        return self.payload


class RaisingProvider:
    name = "raising"

    async def complete(self, system, messages, schema=None, temperature=None):
        raise RuntimeError("provider is down")


_TABLES_CFG = {
    "tiers": [
        {"name": "common", "rank": 1, "weight": 60, "max_tags": 1},
        {"name": "uncommon", "rank": 2, "weight": 30, "max_tags": 2},
        {"name": "rare", "rank": 3, "weight": 10, "max_tags": 3},
    ],
    "themes": ["relic", "charm", "token"],
    "tags": [
        {"tag": "rain-beaded", "group": "weather", "when": ["weather"]},
        {"tag": "storm-worn", "group": "weather", "when": ["weather"], "min_rank": 2},
        {"tag": "dusk-touched", "group": "time", "when": ["time"]},
        {"tag": "moss-grown", "group": "make"},
        {"tag": "iron-bound", "group": "make"},
        {"tag": "rune-marked", "group": "omen", "min_rank": 3},
    ],
}

_FLAVOUR_JSON = ('{"name": "a rain-beaded silver charm", "description": "A small charm '
                 'of dark silver, beaded with cold rain.", "aliases": ["charm", "silver"]}')


# --- the authored tables ----------------------------------------------------

class LootTablesTests(unittest.TestCase):
    def test_from_config_parses_tiers_themes_tags(self):
        t = LootTables.from_config(_TABLES_CFG)
        self.assertEqual([x.name for x in t.tiers], ["common", "uncommon", "rare"])
        self.assertEqual(t.tiers[1].rank, 2)
        self.assertEqual(t.tiers[2].max_tags, 3)
        self.assertIn("charm", t.themes)
        self.assertEqual(t.tags[0].when, ("weather",))
        self.assertEqual(t.tags[1].min_rank, 2)

    def test_empty_config_is_empty(self):
        self.assertTrue(LootTables.from_config(None).is_empty())
        self.assertTrue(LootTables.from_config({"themes": ["x"]}).is_empty())
        self.assertFalse(LootTables.from_config(_TABLES_CFG).is_empty())

    def test_rank_defaults_to_position_when_unspecified(self):
        t = LootTables.from_config({"tiers": [{"name": "a"}, {"name": "b"}]})
        self.assertEqual([x.rank for x in t.tiers], [1, 2])


# --- the roll ---------------------------------------------------------------

class RollBriefTests(unittest.TestCase):
    def setUp(self):
        self.tables = LootTables.from_config(_TABLES_CFG)

    def test_none_when_no_tiers(self):
        self.assertIsNone(roll_brief(LootTables(), random.Random(1)))

    def test_same_seed_same_brief(self):
        a = roll_brief(self.tables, random.Random(42), context_tags=["weather"])
        b = roll_brief(self.tables, random.Random(42), context_tags=["weather"])
        self.assertEqual((a.tier, a.tags, a.theme), (b.tier, b.tags, b.theme))

    def test_tier_and_theme_come_from_the_tables(self):
        for seed in range(30):
            br = roll_brief(self.tables, random.Random(seed))
            self.assertIn(br.tier, {"common", "uncommon", "rare"})
            self.assertIn(br.theme, {"relic", "charm", "token"})

    def test_tag_count_never_exceeds_the_tier_cap(self):
        cap = {"common": 1, "uncommon": 2, "rare": 3}
        for seed in range(60):
            br = roll_brief(self.tables, random.Random(seed))
            self.assertLessEqual(len(br.tags), cap[br.tier])

    def test_weighting_favours_the_common_tier(self):
        # 60/30/10 weights: over many rolls, common should dominate. A loose bound —
        # this is a statistical sanity check, not an exact-count assertion.
        rng = random.Random(0)
        counts = {"common": 0, "uncommon": 0, "rare": 0}
        for _ in range(600):
            counts[roll_brief(self.tables, rng).tier] += 1
        self.assertGreater(counts["common"], counts["uncommon"])
        self.assertGreater(counts["uncommon"], counts["rare"])


class DrawTagsTests(unittest.TestCase):
    def setUp(self):
        self.specs = LootTables.from_config(_TABLES_CFG).tags

    def test_min_rank_gates_out_high_tier_tags(self):
        # At rank 1, neither storm-worn (min 2) nor rune-marked (min 3) may appear.
        for seed in range(40):
            got = _draw_tags(self.specs, 1, random.Random(seed), set(), max_n=5)
            self.assertNotIn("storm-worn", got)
            self.assertNotIn("rune-marked", got)

    def test_one_tag_per_family_group(self):
        # Two 'make' tags and two 'weather' tags exist; a draw must never take two of
        # the same group. Ask for many; the family guard caps each group at one.
        for seed in range(40):
            got = _draw_tags(self.specs, 3, random.Random(seed), set(), max_n=6)
            groups = [next(s.group or s.tag for s in self.specs if s.tag == g)
                      for g in got]
            self.assertEqual(len(groups), len(set(groups)))

    def test_context_resonant_tag_is_preferred(self):
        # With a single slot and a weather-resonant context, the one drawn tag must be
        # a weather tag — resonance wins the slot over the rest.
        got = _draw_tags(self.specs, 1, random.Random(3), {"weather"}, max_n=1)
        self.assertEqual(got, ["rain-beaded"])

    def test_no_context_still_draws_within_rank(self):
        got = _draw_tags(self.specs, 1, random.Random(5), set(), max_n=1)
        self.assertEqual(len(got), 1)
        self.assertIn(got[0], {"rain-beaded", "dusk-touched", "moss-grown",
                               "iron-bound"})


# --- the flavour schema + validation ----------------------------------------

class FlavourSchemaTests(unittest.TestCase):
    def test_schema_is_flat_with_required_name_and_description(self):
        s = flavour_schema()
        self.assertEqual(s["type"], "object")
        self.assertEqual(s["required"], ["name", "description"])
        self.assertFalse(s["additionalProperties"])
        self.assertEqual(set(s["properties"]), {"name", "description", "aliases"})

    def test_schema_has_no_oneof_anywhere(self):
        # The collapse-prone branch shape stays banned: no oneOf/anyOf/allOf.
        blob = repr(flavour_schema())
        for forbidden in ("oneOf", "anyOf", "allOf"):
            self.assertNotIn(forbidden, blob)

    def test_aliases_are_bounded(self):
        self.assertEqual(flavour_schema(max_aliases=2)["properties"]["aliases"]
                         ["maxItems"], 2)


class ParseFlavourTests(unittest.TestCase):
    def test_valid_object_is_normalised(self):
        out = parse_flavour({"name": "  a charm ", "description": " lore ",
                             "aliases": ["charm", "charm", "  silver "]})
        self.assertEqual(out["name"], "a charm")
        self.assertEqual(out["description"], "lore")
        self.assertEqual(out["aliases"], ["charm", "silver"])   # trimmed + de-duped

    def test_missing_name_or_description_is_none(self):
        self.assertIsNone(parse_flavour({"description": "lore"}))
        self.assertIsNone(parse_flavour({"name": "a charm"}))
        self.assertIsNone(parse_flavour({"name": "  ", "description": "lore"}))

    def test_non_dict_is_none(self):
        self.assertIsNone(parse_flavour(None))
        self.assertIsNone(parse_flavour("a charm"))
        self.assertIsNone(parse_flavour(["a", "b"]))

    def test_aliases_bounded_and_non_list_tolerated(self):
        out = parse_flavour({"name": "n", "description": "d",
                             "aliases": ["a", "b", "c", "d", "e"]}, max_aliases=2)
        self.assertEqual(len(out["aliases"]), 2)
        out2 = parse_flavour({"name": "n", "description": "d", "aliases": "nope"})
        self.assertEqual(out2["aliases"], [])

    def test_fields_are_truncated(self):
        out = parse_flavour({"name": "x" * 200, "description": "y" * 999},
                            max_name=10, max_desc=20)
        self.assertEqual(len(out["name"]), 10)
        self.assertEqual(len(out["description"]), 20)


class FallbackFlavourTests(unittest.TestCase):
    def test_fallback_is_a_real_item(self):
        out = fallback_flavour(Brief(tier="rare", rank=3, theme="relic",
                                     tags=["storm-worn"]))
        self.assertTrue(out["name"].strip())
        self.assertTrue(out["description"].strip())
        self.assertIn("storm", out["name"])           # strongest quality shows

    def test_fallback_without_tags_or_theme(self):
        out = fallback_flavour(Brief(tier="common", rank=1, theme="", tags=[]))
        self.assertTrue(out["name"].strip())
        self.assertTrue(out["description"].strip())


# --- the world mint ---------------------------------------------------------

class SpawnItemFieldsTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.world.add_location(Location(id="room", name="Room", description=""))

    def test_spawn_attaches_code_owned_fields_and_aliases(self):
        item = self.world.spawn_item("a charm", "lore", holder_id="room",
                                     aliases=["charm"], tier="rare",
                                     tags=["storm-worn"], theme="relic")
        self.assertEqual(item.tier, "rare")
        self.assertEqual(item.tags, ["storm-worn"])
        self.assertEqual(item.theme, "relic")
        self.assertEqual(item.aliases, ["charm"])
        self.assertIn(item, self.world.contents("room"))

    def test_plain_spawn_leaves_the_fields_empty(self):
        item = self.world.spawn_item("a stick", holder_id="room")
        self.assertEqual(item.tier, "")
        self.assertEqual(item.tags, [])
        self.assertEqual(item.theme, "")
        self.assertEqual(item.aliases, [])


# --- the AI-layer flavour call ----------------------------------------------

class AuthorFlavourTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.brief = Brief(tier="uncommon", rank=2, theme="charm",
                           tags=["rain-beaded"], context="a reward at the hill")
        self.schema = flavour_schema()

    async def test_scripted_json_is_parsed_to_a_dict(self):
        obj = await loot_ai.author_flavour(
            ScriptedFlavourProvider(_FLAVOUR_JSON), self.schema, self.brief)
        self.assertIsInstance(obj, dict)
        self.assertEqual(obj["name"], "a rain-beaded silver charm")

    async def test_prose_reply_yields_none(self):
        obj = await loot_ai.author_flavour(
            ScriptedFlavourProvider("I cannot forge that."), self.schema, self.brief)
        self.assertIsNone(obj)

    async def test_provider_error_is_contained_as_none(self):
        obj = await loot_ai.author_flavour(RaisingProvider(), self.schema, self.brief)
        self.assertIsNone(obj)

    async def test_the_brief_reaches_the_prompt(self):
        prov = ScriptedFlavourProvider(_FLAVOUR_JSON)
        captured = {}

        async def capture(system, messages, schema=None, temperature=None):
            captured["system"] = system
            return _FLAVOUR_JSON
        prov.complete = capture
        await loot_ai.author_flavour(prov, self.schema, self.brief)
        self.assertIn("uncommon", captured["system"])      # tier
        self.assertIn("rain-beaded", captured["system"])   # a quality
        self.assertIn("hill", captured["system"])          # the context blurb


# --- the engine seam: a completed quest earns a forged reward ---------------

def _two_room_engine():
    world = World()
    world.add_location(Location(id="a", name="Room A", description="A bare room.",
                                exits={"north": "b"}))
    world.add_location(Location(id="b", name="Room B", description="A far room.",
                                exits={"south": "a"}))
    return Engine(world, FakeProvider(), start_location="a")


class ForgeRewardEngineTests(unittest.IsolatedAsyncioTestCase):
    async def _drain(self, engine):
        while engine._tasks:
            await __import__("asyncio").gather(*list(engine._tasks),
                                               return_exceptions=True)

    async def _arrive_with_quest(self, engine, provider=None, seed=1):
        if provider is not None or seed is not None:
            engine.attach_loot(_TABLES_CFG, provider=provider, seed=seed)
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        engine.world.quests.offer(player.id, title="Climb the Hill",
                                  summary="Reach Room B.", destination="b")
        s.sent.clear()
        await engine.on_input(s, "north")     # arrive in B -> completes the quest
        await self._drain(engine)
        return s, player

    async def test_completed_quest_forges_a_reward_into_inventory(self):
        engine = _two_room_engine()
        s, player = await self._arrive_with_quest(
            engine, provider=ScriptedFlavourProvider(_FLAVOUR_JSON))
        held = engine.world.contents(player.id)
        self.assertEqual(len(held), 1)
        item = held[0]
        self.assertEqual(item.name, "a rain-beaded silver charm")   # model flavour
        self.assertIn(item.tier, {"common", "uncommon", "rare"})    # code-owned
        self.assertIn(item.theme, {"relic", "charm", "token"})
        self.assertIsInstance(item.tags, list)
        self.assertIn("✓ Quest complete", s.systems())
        self.assertIn("you receive", s.texts().lower())

    async def test_degrades_to_a_brief_only_item_when_the_model_is_unusable(self):
        # FakeProvider returns prose, not flavour JSON -> parse fails -> fallback item.
        engine = _two_room_engine()
        s, player = await self._arrive_with_quest(engine, provider=FakeProvider())
        held = engine.world.contents(player.id)
        self.assertEqual(len(held), 1)
        self.assertTrue(held[0].name.strip())          # a real item, always
        self.assertIn(held[0].tier, {"common", "uncommon", "rare"})

    async def test_no_forge_attached_completes_the_quest_without_a_reward(self):
        engine = _two_room_engine()
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        engine.world.quests.offer(player.id, title="Go", summary="s", destination="b")
        await engine.on_input(s, "north")
        await self._drain(engine)
        self.assertEqual(engine.world.contents(player.id), [])   # no reward
        self.assertIn("✓ Quest complete", s.systems())           # quest still completes

    async def test_arrival_without_a_matching_quest_forges_nothing(self):
        engine = _two_room_engine()
        engine.attach_loot(_TABLES_CFG, provider=ScriptedFlavourProvider(_FLAVOUR_JSON),
                           seed=1)
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        await engine.on_input(s, "north")     # no quest offered
        await self._drain(engine)
        self.assertEqual(engine.world.contents(player.id), [])

    async def test_context_conditions_theme_the_reward_brief(self):
        # A weather condition at the destination makes a weather-resonant tag preferred;
        # the forged item's tags should reflect the moment. Uses the fallback path
        # (FakeProvider) so the assertion reads the code-rolled brief, not model text.
        engine = _two_room_engine()
        engine.world.conditions.set_world("weather", "A steady cold rain is falling.")
        s, player = await self._arrive_with_quest(engine, provider=FakeProvider())
        item = engine.world.contents(player.id)[0]
        # The reward carries a weather-resonant quality (rain-beaded / storm-worn),
        # surfaced in the fallback name from the brief's strongest tag.
        self.assertTrue(any(w in " ".join(item.tags) for w in ("rain", "storm"))
                        or item.tier == "common" and not item.tags,
                        f"expected a weather-themed tag, got {item.tags}")


class StartQuestGameplayTests(unittest.IsolatedAsyncioTestCase):
    """The playable path to the forge: a starting quest handed on connect, completed by
    reaching its destination, which forges a reward — all through the same seam."""

    async def _drain(self, engine):
        while engine._tasks:
            await asyncio.gather(*list(engine._tasks), return_exceptions=True)

    async def test_start_quests_are_offered_and_named_on_connect(self):
        engine = _two_room_engine()
        engine.attach_start_quests(
            [{"title": "Reach B", "summary": "Go to Room B.", "destination": "b"}])
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        active = engine.world.quests.active(player.id)
        self.assertEqual([q.title for q in active], ["Reach B"])
        self.assertIn("purpose", s.texts().lower())        # the goal was named
        self.assertIn("journal", s.systems().lower())      # pointed at the journal

    async def test_reaching_the_destination_completes_and_forges_a_reward(self):
        engine = _two_room_engine()
        engine.attach_loot(_TABLES_CFG,
                           provider=ScriptedFlavourProvider(_FLAVOUR_JSON), seed=1)
        engine.attach_start_quests(
            [{"title": "Reach B", "summary": "Go to Room B.", "destination": "b"}])
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        await engine.on_input(s, "north")                  # walk to the destination
        await self._drain(engine)
        held = engine.world.contents(player.id)
        self.assertEqual(len(held), 1)                     # the forged reward
        self.assertIn("✓ Quest complete", s.systems())
        self.assertIn(COMPLETE, [q.status for q in engine.world.quests
                                 .for_player(player.id)])

    async def test_a_bad_destination_is_dropped(self):
        engine = _two_room_engine()
        kept = engine.attach_start_quests(
            [{"title": "Nowhere", "summary": "x", "destination": "does_not_exist"},
             {"title": "No title", "summary": "x", "destination": "b"},
             {"destination": "b"}])                        # missing title
        self.assertEqual([d["title"] for d in kept], ["No title"])
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        self.assertEqual([q.title for q in engine.world.quests.active(player.id)],
                         ["No title"])

    async def test_no_start_quests_by_default(self):
        engine = _two_room_engine()
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        self.assertEqual(engine.world.quests.active(player.id), [])


class AttachLootTests(unittest.TestCase):
    def test_attach_returns_none_for_an_empty_config(self):
        engine = _two_room_engine()
        self.assertIsNone(engine.attach_loot({"themes": ["x"]}))
        self.assertIsNone(engine.loot)

    def test_attach_wires_the_forge(self):
        engine = _two_room_engine()
        forge = engine.attach_loot(_TABLES_CFG, seed=1)
        self.assertIsNotNone(forge)
        self.assertIs(engine.loot, forge)
        self.assertFalse(forge.tables.is_empty())
        self.assertIn("required", forge.schema)


if __name__ == "__main__":
    unittest.main()
