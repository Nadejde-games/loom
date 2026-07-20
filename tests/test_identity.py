"""Durable player identity (Phase 5, slice 3), offline: a named player persists across a
disconnect and a full restart, and the world's memory of them accretes on the shared
substrate. Deterministic — FakeProvider, no sockets, no wall-clock, no embedder.

Proves, from the primitives up:
  * the identity normalization (slug/display/validate) and the ``PlayerRecord`` round-trip;
  * the login gate: a connection is not a player until it names itself; bad/reserved names
    re-prompt; the name is the durable key (case-insensitive);
  * persist-and-detach on disconnect (never delete-and-drop) and reconnect-restores;
  * newest-wins takeover of a duplicate live name;
  * the persistence overlay: a durable player's held items travel in their record, not the
    floor, and a full save→fresh-engine→reconnect round-trip;
  * accretion: a named player's words are recorded by the NPC under the durable name — the
    seam that makes "the world remembers you" work (live retrieval proven in behavior_probe).
"""
import asyncio
import json
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.protocol import Channel
from loom.style import plain
from loom import identity, persistence
from loom.identity import PlayerRecord


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


async def _drain(engine):
    """Await any background NPC-reply / reaction tasks spawned this beat."""
    if engine._tasks:
        await asyncio.gather(*list(engine._tasks))


def _build_world():
    world = World()
    world.add_location(Location(id="a", name="Cave Mouth", description="A dim mouth.",
                                exits={"north": "b"}))
    world.add_location(Location(id="b", name="The Hollow", description="A still hollow.",
                                exits={"south": "a"}))
    world.add_entity(Npc(id="npc_hermit", name="Odd", description="A wary hermit.",
                         location_id="a", persona={}))
    return world


def _engine(**kw):
    kw.setdefault("require_login", True)
    return Engine(_build_world(), FakeProvider(), start_location="a", **kw)


async def _login(engine, session, name):
    """Full login: connect (name gate) then submit the name. Returns the live player."""
    await engine.on_connect(session)
    await engine.on_input(session, name)
    return engine.players.get(session.id)


# --- 1. identity primitives -------------------------------------------------

class PrimitiveTests(unittest.TestCase):
    def test_slugify_normalizes(self):
        self.assertEqual(identity.slugify("Andrei"), "andrei")
        self.assertEqual(identity.slugify("  Odd  the   Wary!! "), "odd-the-wary")
        self.assertEqual(identity.slugify("ANDREI"), "andrei")
        self.assertEqual(identity.slugify("!!!"), "")       # nothing usable

    def test_display_name_preserves_and_caps(self):
        self.assertEqual(identity.display_name("  Andrei   the Bold  "),
                         "Andrei the Bold")
        self.assertEqual(len(identity.display_name("x" * 100)), 32)

    def test_validate_name_accepts_and_reports_slug(self):
        slug, err = identity.validate_name("Andrei")
        self.assertEqual((slug, err), ("andrei", None))

    def test_validate_name_rejects_empty_punct_reserved(self):
        for bad in ("", "   ", "!!!", "director", "World", "me"):
            slug, err = identity.validate_name(bad)
            self.assertEqual(slug, "")
            self.assertIsNotNone(err)

    def test_case_variants_share_a_slug(self):
        self.assertEqual(identity.validate_name("Andrei")[0],
                         identity.validate_name("ANDREI")[0])

    def test_player_id_slug_round_trip(self):
        self.assertEqual(identity.player_id("andrei"), "player:andrei")
        self.assertEqual(identity.slug_of("player:andrei"), "andrei")
        self.assertEqual(identity.slug_of("npc_hermit"), "npc_hermit")  # non-player id

    def test_item_record_rebuild_round_trip(self):
        w = _build_world()
        it = w.spawn_item("a brass key", "An ornate key.", holder_id="a",
                          aliases=["key", "brass"], tier="rare", tags=["old"],
                          theme="relic")
        rec = json.loads(json.dumps(identity.item_record(it)))   # survives real JSON
        rebuilt = identity.rebuild_item(rec, holder="player:andrei")
        self.assertEqual(rebuilt.id, it.id)
        self.assertEqual(rebuilt.name, "a brass key")
        self.assertEqual(rebuilt.holder, "player:andrei")
        self.assertEqual(rebuilt.aliases, ["key", "brass"])
        self.assertEqual((rebuilt.tier, rebuilt.tags, rebuilt.theme),
                         ("rare", ["old"], "relic"))

    def test_player_record_json_round_trip(self):
        rec = PlayerRecord(id="player:andrei", name="Andrei", location_id="b",
                           held=[{"id": "item_1", "name": "a key"}],
                           created_t=1.0, last_seen_t=2.0)
        back = PlayerRecord.from_dict(json.loads(json.dumps(rec.to_dict())))
        self.assertEqual(back.id, "player:andrei")
        self.assertEqual(back.name, "Andrei")
        self.assertEqual(back.location_id, "b")
        self.assertEqual(back.held[0]["name"], "a key")
        self.assertIsNone(back.password_hash)   # reserved, defaults None


# --- 2. the login gate ------------------------------------------------------

class LoginGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_prompts_and_makes_no_player(self):
        engine = _engine()
        s = FakeSession()
        await engine.on_connect(s)
        self.assertNotIn(s.id, engine.players)          # not a player yet
        self.assertIn(s.id, engine._login_state)        # held at the gate
        self.assertIn("name", s.systems().lower() + s.texts().lower())

    async def test_naming_creates_and_places_player(self):
        engine = _engine()
        s = FakeSession()
        player = await _login(engine, s, "Andrei")
        self.assertIsNotNone(player)
        self.assertEqual(player.id, "player:andrei")
        self.assertEqual(player.name, "Andrei")
        self.assertEqual(player.location_id, "a")       # the start
        self.assertNotIn(s.id, engine._login_state)     # gate cleared
        self.assertIn("andrei", engine.player_records)  # durable record minted

    async def test_bad_name_reprompts_and_holds_gate(self):
        engine = _engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "!!!")                 # no letters/digits
        self.assertNotIn(s.id, engine.players)
        self.assertIn(s.id, engine._login_state)        # still at the gate
        # A real name after the rejection still works.
        await engine.on_input(s, "Andrei")
        self.assertEqual(engine.players[s.id].id, "player:andrei")

    async def test_reserved_name_rejected(self):
        engine = _engine()
        s = FakeSession()
        await engine.on_connect(s)
        await engine.on_input(s, "director")
        self.assertNotIn(s.id, engine.players)
        self.assertIn(s.id, engine._login_state)

    async def test_name_is_case_insensitive_identity(self):
        engine = _engine()
        s1 = FakeSession("s1")
        await _login(engine, s1, "Andrei")
        await engine.on_disconnect(s1)
        s2 = FakeSession("s2")
        p2 = await _login(engine, s2, "andrei")         # different case, same person
        self.assertEqual(p2.id, "player:andrei")
        self.assertEqual(len(engine.player_records), 1)

    async def test_anonymous_engine_still_auto_mints(self):
        engine = _engine(require_login=False)
        s = FakeSession()
        await engine.on_connect(s)                       # no name prompt
        self.assertIn(s.id, engine.players)
        self.assertTrue(engine.players[s.id].name.startswith("Wanderer-"))
        self.assertEqual(engine._login_state, {})


# --- 3. disconnect: persist-and-detach --------------------------------------

class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_saves_record_and_removes_body(self):
        engine = _engine()
        s = FakeSession()
        player = await _login(engine, s, "Andrei")
        engine.world.move(player.id, "b")
        engine.world.spawn_item("a talisman", holder_id=player.id, tier="rare")
        await engine.on_disconnect(s)
        # The body is gone from the live world; the session is clear.
        self.assertNotIn(s.id, engine.players)
        self.assertNotIn("player:andrei", engine.world.entities)
        # The record survives, carrying the last location and the held item.
        rec = engine.player_records["andrei"]
        self.assertEqual(rec.location_id, "b")
        self.assertEqual(rec.held[0]["name"], "a talisman")
        self.assertEqual(rec.held[0]["tier"], "rare")

    async def test_held_items_do_not_drop_to_the_floor(self):
        engine = _engine()
        s = FakeSession()
        player = await _login(engine, s, "Andrei")
        it = engine.world.spawn_item("a talisman", holder_id=player.id)
        await engine.on_disconnect(s)
        # The item travels in the record, not onto the floor where the player stood.
        self.assertNotIn(it.id, engine.world.entities)
        self.assertEqual(engine.world.contents("a"), [])


# --- 4. reconnect -----------------------------------------------------------

class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_restores_location_and_inventory(self):
        engine = _engine()
        s1 = FakeSession("s1")
        player = await _login(engine, s1, "Andrei")
        engine.world.move(player.id, "b")
        it = engine.world.spawn_item("a talisman", holder_id=player.id, tier="rare")
        item_id = it.id
        await engine.on_disconnect(s1)

        s2 = FakeSession("s2")
        back = await _login(engine, s2, "Andrei")
        self.assertEqual(back.location_id, "b")                 # resumed where it stood
        held = engine.world.contents(back.id)
        self.assertEqual([i.id for i in held], [item_id])       # same item, same id
        self.assertEqual(held[0].tier, "rare")

    async def test_new_player_offered_start_quests_returning_not(self):
        engine = _engine()
        engine.attach_start_quests([{"title": "Reach the Hollow",
                                     "summary": "Go north.", "destination": "b"}])
        s1 = FakeSession("s1")
        player = await _login(engine, s1, "Andrei")
        self.assertEqual([q.title for q in engine.world.quests.for_player(player.id)],
                         ["Reach the Hollow"])
        await engine.on_disconnect(s1)
        s2 = FakeSession("s2")
        back = await _login(engine, s2, "Andrei")
        # Still exactly one — durable (kept in the QuestBook by id), not re-offered.
        self.assertEqual([q.title for q in engine.world.quests.for_player(back.id)],
                         ["Reach the Hollow"])

    async def test_returning_player_welcomed_and_recall_seeded(self):
        engine = _engine(autonomous_reactions=True)
        s1 = FakeSession("s1")
        await _login(engine, s1, "Andrei")
        await engine.on_disconnect(s1)
        s2 = FakeSession("s2")
        await _login(engine, s2, "Andrei")              # returns to "a", where Odd is
        self.assertIn("walked here before", s2.texts().lower())
        # A recall reaction was seeded for the NPC present (emergent, name-seeded).
        self.assertTrue(engine._tasks)
        await _drain(engine)


# --- 5. newest-wins takeover ------------------------------------------------

class TakeoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_login_takes_over_and_kicks_old(self):
        engine = _engine()
        s1 = FakeSession("s1")
        await _login(engine, s1, "Andrei")
        s2 = FakeSession("s2")
        await _login(engine, s2, "Andrei")              # same name, second live session
        # Old socket cut; the body is bound to the new session only — no duplicate.
        self.assertTrue(s1.closed)
        self.assertNotIn("s1", engine.players)
        self.assertEqual(engine.players["s2"].id, "player:andrei")
        # Exactly one live body, bound to the new session — not a duplicate.
        self.assertIn("player:andrei", engine.world.entities)
        self.assertEqual(sum(1 for p in engine.players.values()
                             if p.id == "player:andrei"), 1)
        self.assertIn("reconnecting", s2.systems().lower())


# --- 6. persistence overlay integration -------------------------------------

class OverlayTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_held_item_in_players_block_not_items(self):
        engine = _engine()
        s = FakeSession()
        player = await _login(engine, s, "Andrei")
        engine.world.move(player.id, "b")
        engine.world.spawn_item("a talisman", holder_id=player.id, tier="rare")
        save = persistence.snapshot(engine)
        # Not in the global item list (it belongs to the player)...
        self.assertFalse([r for r in save["items"] if r["name"] == "a talisman"])
        # ...but captured in the player's durable record, with the live location synced.
        rec = save["players"]["andrei"]
        self.assertEqual(rec["location_id"], "b")
        self.assertEqual(rec["held"][0]["name"], "a talisman")

    async def test_snapshot_is_pure_json(self):
        engine = _engine()
        s = FakeSession()
        player = await _login(engine, s, "Andrei")
        engine.world.spawn_item("a shard", holder_id=player.id, tier="common")
        json.dumps(persistence.snapshot(engine))        # no default= — proves serialisable

    async def test_full_restart_reconnect_round_trip(self):
        source = _engine()
        s1 = FakeSession("s1")
        player = await _login(source, s1, "Andrei")
        source.world.move(player.id, "b")
        it = source.world.spawn_item("a storm-glass shard", holder_id=player.id,
                                     tier="rare", tags=["storm-worn"], theme="relic")
        item_id = it.id
        source.world.quests.offer(player.id, title="Climb", summary="", destination="b")
        await source.on_disconnect(s1)                  # detach -> record
        save = json.loads(json.dumps(persistence.snapshot(source)))

        # A brand-new process from the same authored world.
        fresh = _engine()
        persistence.restore(fresh, save)
        # The offline player is a record only — no body or item in the world yet.
        self.assertNotIn("player:andrei", fresh.world.entities)
        self.assertNotIn(item_id, fresh.world.entities)
        self.assertIn("andrei", fresh.player_records)

        # Reconnecting materializes it all: location, the item (same id), the quest.
        s2 = FakeSession("s2")
        back = await _login(fresh, s2, "Andrei")
        self.assertEqual(back.location_id, "b")
        held = fresh.world.contents(back.id)
        self.assertEqual([i.id for i in held], [item_id])
        self.assertEqual(held[0].tier, "rare")
        self.assertEqual([q.title for q in fresh.world.quests.for_player(back.id)],
                         ["Climb"])

    async def test_restore_tolerates_missing_players_block(self):
        # A v1 overlay (pre-identity) has no players block — loads to no durable players.
        engine = _engine()
        persistence.restore(engine, {"version": 1, "positions": {"npc_hermit": "b"}})
        self.assertEqual(engine.player_records, {})


# --- 7. accretion: the world records you by your durable name ---------------

class AccretionTests(unittest.IsolatedAsyncioTestCase):
    async def test_npc_records_the_durable_name(self):
        engine = _engine()
        s = FakeSession()
        await _login(engine, s, "Andrei")
        await engine.on_input(s, "say Odd, I swear I will return")
        await _drain(engine)
        # The Hermit's memory names the speaker by their durable identity — so the same
        # line is meaningful when Andrei returns, surfaced by relevance retrieval (live).
        texts = [e.text for e in engine.minds["npc_hermit"].memory.entries]
        self.assertTrue(any("Andrei" in t and "said to me" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()
