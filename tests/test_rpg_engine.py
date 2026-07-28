"""Tier 0 wired into the engine, offline: the `score` query renders a player's sheet,
qualitative health cues surface through `look` and a mind's `Scene`, and sheet live-state
survives a persistence snapshot/restore. Fake session, no provider network, no GPU.
"""
import asyncio
import unittest

from loom.world import World, Location, Npc, Item
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.protocol import Channel
from loom.style import plain
from loom.rpg import Ruleset
from loom import persistence


META = {
    "attributes": [
        {"name": "str", "default": 10}, {"name": "con", "default": 10},
        {"name": "wis", "default": 10},
    ],
    "derived": {"str_mod": "(str - 10) // 2", "con_mod": "(con - 10) // 2",
                "wis_mod": "(wis - 10) // 2"},
    "hit_die": 8,
    "pools": {
        "hp": {"max": "10 + hit_die*level + con_mod*level", "regen": "con_mod + 1",
               "policy": "vital",
               "cues": [[100, "unharmed"], [50, "wounded"], [1, "near death"], [0, "dead"]]},
    },
    "point_buy": {"budget": 27, "floor": 8, "ceil": 15,
                  "cost": {"8": 0, "9": 1, "10": 2, "11": 3, "12": 4, "13": 5, "14": 7, "15": 9}},
    "progression": {"curve": "50 * (level - 1) * level", "points_per_level": 2, "max_level": 20},
}


def ruleset():
    return Ruleset.from_meta(META)


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
        return "\n".join(plain(d) for (c, d) in self.sent if c == Channel.SYSTEM)


def one_room_engine():
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    return Engine(world, FakeProvider(), start_location="room")


def connect(engine):
    session = FakeSession()
    asyncio.run(engine.on_connect(session))
    session.sent.clear()
    return session


class ScoreVerbTests(unittest.TestCase):
    def test_score_renders_the_player_sheet(self):
        engine = one_room_engine()
        session = connect(engine)
        player = engine.players[session.id]
        player.sheet = ruleset().make_sheet(stats={"con": 14}, level=1)
        asyncio.run(engine.on_input(session, "score"))
        out = session.texts()
        self.assertIn("Level 1", out)
        self.assertIn("CON 14 (+2)", out)
        self.assertIn("20/20", out)

    def test_score_alias_sheet(self):
        engine = one_room_engine()
        session = connect(engine)
        engine.players[session.id].sheet = ruleset().make_sheet(level=1)
        asyncio.run(engine.on_input(session, "sheet"))
        self.assertIn("Level 1", session.texts())

    def test_sheetless_player_gets_a_plain_answer(self):
        engine = one_room_engine()
        session = connect(engine)
        asyncio.run(engine.on_input(session, "score"))
        self.assertIn("no character sheet", session.texts())


class PerceptionThroughLookTests(unittest.TestCase):
    def _engine_with_odd(self):
        engine = one_room_engine()
        engine.world.add_entity(Npc(id="odd", name="Odd", description="a hermit",
                                    location_id="room"))
        return engine

    def test_wounded_npc_shows_a_cue_on_look(self):
        engine = self._engine_with_odd()
        odd = engine.world.entities["odd"]
        odd.sheet = ruleset().make_sheet(stats={"con": 14}, level=1)
        odd.sheet.pools["hp"].set(10)            # 50% → wounded
        session = connect(engine)
        asyncio.run(engine.on_input(session, "look"))
        self.assertIn("Odd (wounded)", session.texts())

    def test_full_health_npc_shows_no_cue(self):
        engine = self._engine_with_odd()
        engine.world.entities["odd"].sheet = ruleset().make_sheet(stats={"con": 14})
        session = connect(engine)
        asyncio.run(engine.on_input(session, "look"))
        out = session.texts()
        self.assertIn("Odd", out)
        self.assertNotIn("(", out)               # no parenthetical cue at full health

    def test_sheetless_npc_unchanged(self):
        engine = self._engine_with_odd()          # Odd has no sheet
        session = connect(engine)
        asyncio.run(engine.on_input(session, "look"))
        self.assertIn("Odd", session.texts())


class MindSelfPerceptionTests(unittest.TestCase):
    def test_scene_carries_the_actors_own_condition(self):
        engine = one_room_engine()
        odd = Npc(id="odd", name="Odd", description="a hermit", location_id="room")
        engine.world.add_entity(odd)
        odd.sheet = ruleset().make_sheet(stats={"con": 14}, level=1)
        odd.sheet.pools["hp"].set(10)            # wounded
        scene = engine._scene_for(odd, "room")
        self.assertIn("You are wounded.", scene.conditions)

    def test_full_health_actor_has_no_self_cue(self):
        engine = one_room_engine()
        odd = Npc(id="odd", name="Odd", location_id="room")
        engine.world.add_entity(odd)
        odd.sheet = ruleset().make_sheet(stats={"con": 14})
        scene = engine._scene_for(odd, "room")
        self.assertNotIn("You are unharmed.", scene.conditions)


class TrainVerbTests(unittest.TestCase):
    def _player_with_points(self, points=3, con=10):
        engine = one_room_engine()
        session = connect(engine)
        sheet = ruleset().make_sheet(stats={"con": con}, level=1)
        sheet.points = points
        engine.players[session.id].sheet = sheet
        return engine, session, sheet

    def test_train_raises_stat(self):
        engine, session, sheet = self._player_with_points()
        asyncio.run(engine.on_input(session, "train con"))
        self.assertIn("You train con to 11", session.texts())
        self.assertEqual(sheet.stats["con"], 11)

    def test_train_unaffordable(self):
        engine, session, sheet = self._player_with_points(points=0)
        asyncio.run(engine.on_input(session, "train con"))
        self.assertIn("costs", session.texts())

    def test_train_needs_a_target(self):
        engine, session, _ = self._player_with_points()
        asyncio.run(engine.on_input(session, "train"))
        self.assertIn("which attribute", session.texts().lower())

    def test_train_without_sheet(self):
        engine = one_room_engine()
        session = connect(engine)
        asyncio.run(engine.on_input(session, "train con"))
        self.assertIn("no character sheet", session.texts())


class QuestXpTests(unittest.TestCase):
    def _player(self, xp_per_quest):
        engine = one_room_engine()
        engine.xp_per_quest = xp_per_quest
        session = connect(engine)
        player = engine.players[session.id]
        player.sheet = ruleset().make_sheet(stats={"con": 10}, level=1)
        session.sent.clear()
        return engine, session, player

    def test_award_grants_xp(self):
        engine, session, player = self._player(80)
        asyncio.run(engine._award_quest_xp(session, player))
        self.assertEqual(player.sheet.xp, 80)
        self.assertIn("You gain 80 experience", session.systems())

    def test_award_announces_level_up(self):
        engine, session, player = self._player(120)      # threshold for L2 is 100
        asyncio.run(engine._award_quest_xp(session, player))
        self.assertEqual(player.sheet.level, 2)
        self.assertIn("reach level 2", session.systems())

    def test_no_xp_configured_is_silent(self):
        engine, session, player = self._player(0)
        asyncio.run(engine._award_quest_xp(session, player))
        self.assertEqual(player.sheet.xp, 0)
        self.assertEqual(session.systems(), "")


class EquipVerbTests(unittest.TestCase):
    def _engine_with_gear(self):
        engine = one_room_engine()
        session = connect(engine)
        player = engine.players[session.id]
        player.sheet = ruleset().make_sheet(stats={"con": 10}, level=1)
        engine.world.add_entity(Item(id="sword", name="a sword", holder=player.id,
                                     aliases=["sword"], slot="main_hand",
                                     modifiers=[{"target": "max_hp", "value": 5}]))
        engine.world.add_entity(Item(id="rock", name="a rock", holder=player.id,
                                     aliases=["rock"]))
        session.sent.clear()
        return engine, session, player

    def test_equip_applies_modifier(self):
        engine, session, player = self._engine_with_gear()
        base = player.sheet.pools["hp"].max
        asyncio.run(engine.on_input(session, "equip sword"))
        self.assertIn("You equip", session.texts())
        self.assertTrue(player.sheet.is_equipped("sword"))
        self.assertEqual(player.sheet.pools["hp"].max, base + 5)

    def test_unequip_reverts(self):
        engine, session, player = self._engine_with_gear()
        base = player.sheet.pools["hp"].max
        asyncio.run(engine.on_input(session, "equip sword"))
        session.sent.clear()
        asyncio.run(engine.on_input(session, "unequip sword"))
        self.assertIn("You remove", session.texts())
        self.assertFalse(player.sheet.is_equipped("sword"))
        self.assertEqual(player.sheet.pools["hp"].max, base)

    def test_inventory_marks_worn(self):
        engine, session, player = self._engine_with_gear()
        asyncio.run(engine.on_input(session, "equip sword"))
        session.sent.clear()
        asyncio.run(engine.on_input(session, "inventory"))
        self.assertIn("a sword (worn)", session.texts())

    def test_cannot_equip_plain_item(self):
        engine, session, _ = self._engine_with_gear()
        asyncio.run(engine.on_input(session, "equip rock"))
        self.assertIn("cannot equip", session.texts())

    def test_equip_something_not_carried(self):
        engine, session, _ = self._engine_with_gear()
        asyncio.run(engine.on_input(session, "equip axe"))
        self.assertIn("not carrying", session.texts())


class SheetPersistenceTests(unittest.TestCase):
    def _engine_with_sheeted_odd(self, hp_current=None):
        engine = one_room_engine()
        engine.world.add_entity(Npc(id="odd", name="Odd", location_id="room"))
        engine.world.entities["odd"].sheet = ruleset().make_sheet(stats={"con": 14}, level=1)
        if hp_current is not None:
            engine.world.entities["odd"].sheet.pools["hp"].set(hp_current)
        return engine

    def test_sheet_survives_snapshot_restore(self):
        source = self._engine_with_sheeted_odd(hp_current=7)
        source.world.entities["odd"].sheet.xp = 42
        save = persistence.snapshot(source)
        self.assertIn("sheets", save)
        self.assertEqual(save["version"], persistence.SAVE_VERSION)

        target = self._engine_with_sheeted_odd()   # a fresh full sheet
        self.assertEqual(target.world.entities["odd"].sheet.pools["hp"].current, 20)
        persistence.restore(target, save)
        restored = target.world.entities["odd"].sheet
        self.assertEqual(restored.pools["hp"].current, 7)
        self.assertEqual(restored.xp, 42)

    def test_old_save_without_sheets_loads_harmlessly(self):
        target = self._engine_with_sheeted_odd(hp_current=15)
        persistence.restore(target, {"version": 2})   # a pre-Tier-0 overlay
        # No sheets block → the authored/full sheet is left as built, nothing errors.
        self.assertEqual(target.world.entities["odd"].sheet.pools["hp"].current, 15)


if __name__ == "__main__":
    unittest.main()
