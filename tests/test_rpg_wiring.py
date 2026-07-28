"""Tier 0 wired into the demo world (Phase 9, Slice 3), offline: the game's own
``world.json`` "stats" block builds a ruleset, a connecting player is furnished with the
default template stack's sheet via the engine's player hook, and ``score`` renders it.
Loads the real game world; no provider network, no GPU.
"""
import asyncio
import os
import unittest

from loom.content import load_world
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.protocol import Channel
from loom.style import plain
from loom.rpg import Ruleset

HERE = os.path.dirname(os.path.abspath(__file__))
WORLD_FILE = os.path.join(HERE, os.pardir, "game", "world", "world.json")


class FakeSession:
    def __init__(self, sid="s1"):
        self.id = sid
        self.player_id = None
        self.sent = []

    async def send(self, channel, data):
        self.sent.append((channel, data))

    async def send_text(self, text):
        await self.send(Channel.TEXT, text)

    async def send_system(self, text):
        await self.send(Channel.SYSTEM, text)

    async def close(self):
        pass

    def texts(self):
        return "\n".join(plain(d) for (c, d) in self.sent if c == Channel.TEXT)


def demo_engine():
    """The real demo world with the RPG player-hook wired exactly as game/main.py does."""
    world, start = load_world(WORLD_FILE)
    engine = Engine(world, FakeProvider(), start_location=start)
    ruleset = Ruleset.from_meta(world.meta.get("stats"))
    engine.player_hook = lambda player: setattr(player, "sheet", ruleset.default_sheet())
    return engine, ruleset


class DemoWorldRulesetTests(unittest.TestCase):
    def test_stats_block_present_and_valid(self):
        world, _ = load_world(WORLD_FILE)
        cfg = world.meta.get("stats")
        self.assertIsNotNone(cfg, "the demo world should declare a 'stats' block")
        rs = Ruleset.from_meta(cfg)
        self.assertEqual(rs.stats.names(), ("str", "dex", "con", "int", "wis", "cha"))
        self.assertEqual([p.key for p in rs.pools], ["hp", "mana", "stamina"])
        self.assertIsNotNone(rs.skills)

    def test_default_sheet_is_point_buy_legal(self):
        _, rs = demo_engine()
        # The authored default stat allocation stays within the declared point-buy budget —
        # the balancing discipline of §H, checked as an invariant of the demo content.
        base = {n: rs.stats.get(n).default for n in rs.stats.names()}
        self.assertEqual(rs.stats.point_buy.validate(base), [])


class DemoGearTests(unittest.TestCase):
    def test_walking_staff_is_equippable(self):
        world, _ = load_world(WORLD_FILE)
        staff = world.entities.get("walking_staff")
        self.assertIsNotNone(staff)
        self.assertEqual(staff.slot, "main_hand")
        self.assertTrue(staff.modifiers)

    def test_equipping_staff_raises_stats(self):
        world, _ = load_world(WORLD_FILE)
        _, rs = demo_engine()
        sheet = rs.default_sheet()
        before_stamina = sheet.pools["stamina"].max
        before_str = sheet.effective_stats()["str"]
        staff = world.entities["walking_staff"]
        sheet.equip(staff.slot, staff.id, staff.modifiers)
        self.assertEqual(sheet.effective_stats()["str"], before_str + 1)
        self.assertEqual(sheet.pools["stamina"].max, before_stamina + 4)


class PlayerGetsSheetOnConnectTests(unittest.TestCase):
    def test_connecting_player_can_score(self):
        engine, _ = demo_engine()
        session = FakeSession()
        asyncio.run(engine.on_connect(session))
        player = engine.players[session.id]
        self.assertIsNotNone(player.sheet)

        session.sent.clear()
        asyncio.run(engine.on_input(session, "score"))
        out = session.texts()
        self.assertIn("Level 1", out)
        self.assertIn("HP", out)
        self.assertIn("STAMINA", out)
        self.assertIn("observation", out)      # granted by the wanderer background

    def test_pools_present_and_full(self):
        engine, _ = demo_engine()
        session = FakeSession()
        asyncio.run(engine.on_connect(session))
        sheet = engine.players[session.id].sheet
        self.assertEqual(set(sheet.pools), {"hp", "mana", "stamina"})
        for pool in sheet.pools.values():
            self.assertEqual(pool.current, pool.max)
            self.assertGreater(pool.max, 0)


if __name__ == "__main__":
    unittest.main()
