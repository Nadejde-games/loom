"""Persistence (Phase 5, slice 1), offline: the mutable runtime overlay survives a
restart. All deterministic — no GPU, no sockets, no wall-clock.

Proves, from the primitives up:
  * each self-contained state holder round-trips through its own state()/load_state
    (Conditions, QuestBook, MemoryStream, Chronicle);
  * the crash-safe file layer — atomic write, a retained .bak, tolerant/fallback load;
  * the version header — pure migration dispatch, tolerant of a missing/older version;
  * the engine-level headline proof: forge an item and leave it in the world, add NPC
    and director memory, save, build a *fresh* engine from the same authored world (the
    forged item gone, the NPC home again), restore — and the item is back on the floor
    with its tier/tags intact, the NPCs remember, the clock/weather/conditions/chronicle
    are as they were. The overlay is round-tripped through real JSON, so anything
    non-serialisable fails loudly.
"""
import json
import os
import shutil
import tempfile
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.protocol import Channel
from loom.clock import Phase
from loom.weather import Weather
from loom.style import plain
from loom import persistence
from loom.world.conditions import Conditions
from loom.quest import QuestBook, REACH, COMPLETE
from loom.ai.memory import MemoryStream
from loom.chronicle import Chronicle


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


class FakeLoop:
    """The only loop surface a subsystem's install() touches — collect the callbacks
    without ever running them (persistence is deterministic; no ticking needed)."""
    def __init__(self):
        self.systems = []

    def add_system(self, fn):
        self.systems.append(fn)


# --- world / engine builders ------------------------------------------------

def _build_world():
    """Two rooms and one authored NPC — the reload-from-source definition. A fresh
    call is a fresh world, exactly as load_world(world.json) yields at every boot."""
    world = World()
    world.add_location(Location(id="a", name="Room A", description="A bare room.",
                                exits={"north": "b"}))
    world.add_location(Location(id="b", name="Room B", description="A far room.",
                                exits={"south": "a"}))
    world.add_entity(Npc(id="npc_guide", name="Guide",
                         description="A quiet guide.", location_id="a", persona={}))
    return world


_PHASES = [Phase(name="day", start=0, condition="Daylight lies over the land."),
           Phase(name="night", start=720, condition="Night has fallen.",
                 ambient="Night falls.")]
_SKY = [Weather(name="clear"),
        Weather(name="rain", condition="Rain falls.", ambient="Rain begins.")]


def _wired_engine():
    """An engine with every persistable subsystem attached (director, clock, weather,
    loot) — the full overlay surface. Deterministic FakeProvider; nothing runs."""
    engine = Engine(_build_world(), FakeProvider(), start_location="a")
    loop = FakeLoop()
    engine.attach_director(loop, persona=None)
    engine.attach_clock(loop, _PHASES, factor=1.0)
    engine.attach_weather(loop, _SKY, rng=__import__("random").Random(0))
    return engine


# --- 1. the primitives round-trip through their own state()/load_state -------

class PrimitiveStateTests(unittest.TestCase):
    def _json(self, obj):
        """A value the way it survives a real save file — proves JSON-serialisable."""
        return json.loads(json.dumps(obj))

    def test_conditions_round_trip(self):
        c = Conditions()
        c.set("b", "storm", "A storm rages.")
        c.set("b", "dark", "Shadows pool.")
        c.set_world("time", "Dusk deepens.")
        restored = Conditions()
        restored.load_state(self._json(c.state()))
        self.assertEqual(restored.texts("b"), ["A storm rages.", "Shadows pool."])
        self.assertEqual(restored.world_texts(), ["Dusk deepens."])
        # Insertion order (which drives rendering) survives.
        self.assertEqual([x.tag for x in restored.at("b")], ["storm", "dark"])

    def test_conditions_load_is_tolerant(self):
        c = Conditions()
        c.load_state({})                       # missing both scopes
        self.assertEqual(c.world_texts(), [])
        self.assertEqual(c.texts("b"), [])

    def test_questbook_round_trip(self):
        qb = QuestBook()
        qb.offer("player:1", title="Climb", summary="Reach the hilltop.",
                 giver="GM", destination="b")
        qb.offer("player:1", title="Return", summary="Come home.", destination="a")
        qb.complete_reached("player:1", "b")   # first quest now COMPLETE
        restored = QuestBook()
        restored.load_state(self._json(qb.state()))
        quests = restored.for_player("player:1")
        self.assertEqual([q.title for q in quests], ["Climb", "Return"])
        self.assertEqual(quests[0].status, COMPLETE)
        self.assertEqual(quests[1].destination, "a")
        self.assertEqual(quests[1].kind, REACH)
        # The id cursor survives, so a fresh offer never re-mints an existing id.
        nxt = restored.offer("player:2", title="New", summary="")
        self.assertEqual(nxt.id, "quest_3")

    def test_memory_stream_round_trip(self):
        m = MemoryStream()
        m.add("I saw a storm.", kind="observation")
        m.add("I spoke.", kind="speech")
        original_t = m.entries[0].t
        restored = MemoryStream()
        restored.load_state(self._json(m.state()))
        self.assertEqual([e.text for e in restored.entries],
                         ["I saw a storm.", "I spoke."])
        self.assertEqual(restored.entries[1].kind, "speech")
        # The original timestamp is restored, never re-stamped to load time.
        self.assertEqual(restored.entries[0].t, original_t)

    def test_chronicle_round_trip_preserves_cursor(self):
        ch = Chronicle(maxlen=3)
        for i in range(5):                     # 5 events, window holds only the last 3
            ch.record(f"event {i}", location_id="a", kind="event")
        self.assertEqual(ch.seq, 5)
        restored = Chronicle(maxlen=3)
        restored.load_state(self._json(ch.state()))
        # The cursor is the high-water mark (5), though only the tail is windowed —
        # so the director's "what is new since N" gate is not reset by a reboot.
        self.assertEqual(restored.seq, 5)
        self.assertEqual([e.text for e in restored.recent()],
                         ["event 2", "event 3", "event 4"])
        self.assertEqual(restored.recent()[-1].seq, 5)
        # A new record continues from the restored cursor.
        self.assertEqual(restored.record("event 5"), 6)


# --- 2. the crash-safe file layer -------------------------------------------

class FileLayerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "world.save.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_then_load(self):
        persistence.save_atomic(self.path, {"version": 1, "positions": {"n": "a"}})
        self.assertEqual(persistence.load(self.path)["positions"], {"n": "a"})

    def test_second_write_keeps_prior_as_bak(self):
        persistence.save_atomic(self.path, {"version": 1, "tag": "first"})
        persistence.save_atomic(self.path, {"version": 1, "tag": "second"})
        self.assertEqual(persistence.load(self.path)["tag"], "second")
        with open(self.path + ".bak", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["tag"], "first")
        # No stray temp file is left behind after a successful write.
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_load_falls_back_to_bak_when_primary_corrupt(self):
        persistence.save_atomic(self.path, {"version": 1, "tag": "first"})
        persistence.save_atomic(self.path, {"version": 1, "tag": "second"})
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")     # simulate a torn write
        # The prior good state (now in .bak) is recovered, not lost.
        self.assertEqual(persistence.load(self.path)["tag"], "first")

    def test_load_missing_returns_none(self):
        self.assertIsNone(persistence.load(self.path))

    def test_load_corrupt_without_bak_returns_none(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("garbage")
        self.assertIsNone(persistence.load(self.path))


# --- 3. the version header: migration dispatch ------------------------------

class MigrationTests(unittest.TestCase):
    def test_identity_at_current_version(self):
        out = persistence._migrate({"version": persistence.SAVE_VERSION, "x": 1})
        self.assertEqual(out["version"], persistence.SAVE_VERSION)
        self.assertEqual(out["x"], 1)

    def test_missing_version_defaults_to_current(self):
        out = persistence._migrate({"positions": {}})
        self.assertEqual(out["version"], persistence.SAVE_VERSION)

    def test_dispatch_runs_registered_migration(self):
        seen = []

        def bump(save):
            seen.append(save.get("version"))
            save["migrated"] = True
            return save

        persistence._MIGRATIONS[0] = bump
        try:
            out = persistence._migrate({"version": 0})
            self.assertTrue(out.get("migrated"))
            self.assertEqual(out["version"], persistence.SAVE_VERSION)
            self.assertEqual(seen, [0])
        finally:
            del persistence._MIGRATIONS[0]

    def test_future_version_loads_without_migration(self):
        # A save from a newer build: no migration for it, but it still loads (restore
        # defaults anything it does not understand). Must not loop or raise.
        out = persistence._migrate({"version": persistence.SAVE_VERSION + 5, "x": 2})
        self.assertEqual(out["x"], 2)


# --- 4. snapshot shape ------------------------------------------------------

class SnapshotShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_player_held_item_snapshots_to_the_floor(self):
        # A held reward belongs to the world; with no durable player identity it is
        # snapshotted where the player stands (drop-on-disconnect semantics).
        engine = _wired_engine()
        s = FakeSession()
        await engine.on_connect(s)
        player = engine.players[s.id]
        engine.world.move(player.id, "b")
        engine.world.spawn_item("a talisman", "A humming talisman.",
                                holder_id=player.id, tier="rare", tags=["storm-worn"],
                                theme="relic")
        save = persistence.snapshot(engine)
        rec = next(r for r in save["items"] if r["name"] == "a talisman")
        self.assertEqual(rec["holder"], "b")   # the player's floor, not the player id
        self.assertEqual(rec["tier"], "rare")
        # Players are not persisted as positions; only the authored NPC is.
        self.assertNotIn(player.id, save["positions"])
        self.assertEqual(save["positions"].get("npc_guide"), "a")

    async def test_snapshot_is_pure_json(self):
        engine = _wired_engine()
        engine.world.spawn_item("a shard", holder_id="b", tier="common", tags=["x"])
        # Round-trips through real JSON with no default= handler — proves serialisable.
        json.dumps(persistence.snapshot(engine))


# --- 5. the engine-level headline proof: a full restart --------------------

class EngineRoundTripTests(unittest.IsolatedAsyncioTestCase):
    def _mutate(self, engine):
        """Drive an engine into a rich, distinctive runtime state — the world after a
        session of play — and return the facts a restart must reproduce."""
        w = engine.world
        w.move("npc_guide", "b")                       # the NPC wandered
        item = w.spawn_item("a storm-glass shard", "A shard humming with storm-light.",
                            holder_id="b", tier="rare", tags=["storm-worn"],
                            theme="relic")             # a forged reward on the floor
        w.conditions.set("b", "storm", "A storm rages over the far room.")
        w.conditions.set_world("time", "Night has fallen.")
        w.quests.offer("player:1", title="Climb", summary="Reach the far room.",
                       destination="b")
        engine._pcount = 4                             # players have come and gone
        engine.minds["npc_guide"].memory.add("I watched the storm break.",
                                              kind="observation")
        engine.director.mind.memory.add("I stirred the sky.", kind="director")
        engine.chronicle.record("The Guide walked north.", location_id="b",
                                kind="move")
        engine.clock._minute = 900.0             # past the 720 boundary -> night
        engine.clock._phase = engine.clock._phase_at(900.0)
        engine.weather._i = 1
        engine.weather._pulses = 5
        return item.id

    async def test_full_restart_reproduces_the_world(self):
        source = _wired_engine()
        item_id = self._mutate(source)
        # Serialise exactly as a real shutdown would — through the file layer and JSON.
        save_dir = tempfile.mkdtemp()
        try:
            path = os.path.join(save_dir, "world.save.json")
            persistence.save_atomic(path, persistence.snapshot(source))

            # A brand-new process: the authored world reloaded from source (the NPC
            # home again, no forged item), every subsystem re-wired, then the overlay.
            fresh = _wired_engine()
            self.assertEqual(fresh.world.occupants("a")[0].id, "npc_guide")  # home
            self.assertEqual(fresh.world.contents("b"), [])                  # no loot
            self.assertTrue(fresh.restore(path))

            w = fresh.world
            # The forged reward is back on the floor of b, with its mechanics intact.
            floor = w.contents("b")
            shard = next(i for i in floor if i.id == item_id)
            self.assertEqual(shard.name, "a storm-glass shard")
            self.assertEqual(shard.tier, "rare")
            self.assertEqual(shard.tags, ["storm-worn"])
            self.assertEqual(shard.theme, "relic")
            # The NPC is where it wandered to, and the occupants index rebuilt.
            self.assertEqual([e.id for e in w.occupants("b")], ["npc_guide"])
            self.assertEqual(w.occupants("a"), [])
            # The id counter survived, so a new spawn never collides with the reward.
            self.assertNotEqual(w.spawn_item("another").id, item_id)
            self.assertEqual(fresh._pcount, 4)
            # Conditions, quests.
            self.assertEqual(w.conditions.texts("b"),
                             ["A storm rages over the far room."])
            self.assertEqual(w.conditions.world_texts(), ["Night has fallen."])
            self.assertEqual([q.title for q in w.quests.for_player("player:1")],
                             ["Climb"])
            # Memory: the NPC and the director both remember across the reboot.
            self.assertIn("I watched the storm break.",
                          [e.text for e in fresh.minds["npc_guide"].memory.entries])
            self.assertIn("I stirred the sky.",
                          [e.text for e in fresh.director.mind.memory.entries])
            # Chronicle tail + cursor.
            self.assertEqual(fresh.chronicle.seq, source.chronicle.seq)
            self.assertEqual(fresh.chronicle.recent()[-1].text,
                             "The Guide walked north.")
            # Clock and weather scalars.
            self.assertEqual(fresh.clock.minute, 900.0)
            self.assertEqual(fresh.clock.phase.name, "night")
            self.assertEqual(fresh.weather._i, 1)
            self.assertEqual(fresh.weather._pulses, 5)
        finally:
            shutil.rmtree(save_dir, ignore_errors=True)

    def test_restore_no_save_is_a_noop(self):
        engine = _wired_engine()
        self.assertFalse(engine.restore("/nonexistent/world.save.json"))

    def test_restore_tolerates_sparse_and_unknown_save(self):
        # A minimal, forward-incompatible overlay must compose without error: unknown
        # keys ignored, every absent section left at its authored default.
        engine = _wired_engine()
        persistence.restore(engine, {"version": 1, "future_field": 99,
                                     "positions": {"npc_guide": "b"}})
        self.assertEqual([e.id for e in engine.world.occupants("b")], ["npc_guide"])
        # Absent sections did not blow up or corrupt anything.
        self.assertEqual(engine.world.quests.for_player("player:1"), [])


if __name__ == "__main__":
    unittest.main()
