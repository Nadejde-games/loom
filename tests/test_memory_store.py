"""SQLite-backed memory (Phase 5, slice 1b), offline: the storage move beneath the
1a retrieval feature. Deterministic — stdlib sqlite3 (``:memory:`` or a tempfile), a
fake embedder, no GPU/network. Proves:

  * the store round-trips a memory (text/kind/t/importance) and its embedding BLOB;
  * a SQLite-backed stream writes through on add() and reads its agent's rows back;
  * embeddings persist, so a stream re-opened on the same store does NOT re-embed
    already-embedded memories (only the new query) — the durability 1b buys;
  * agents are isolated by agent_id;
  * load_state migrates an old JSON overlay's memory into an empty store exactly once;
  * an Engine's NPC/director memory survives a restart through the DB;
  * persistence omits the memory block from the JSON overlay when a store is present,
    and an old overlay's memory migrates in on restore.
"""
import math
import os
import shutil
import tempfile
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine
from loom.ai import FakeProvider, MemoryStream, MemoryStore, MemoryEntry
from loom.ai.embedding import FakeEmbeddingProvider
from loom import persistence


class CountingEmbedder:
    """Wraps the fake embedder and counts how many texts it is asked to embed — to
    prove a re-opened stream embeds only the query, not its stored history."""
    dim = 64

    def __init__(self):
        self._inner = FakeEmbeddingProvider(dim=64)
        self.texts_embedded = 0

    async def embed(self, texts):
        self.texts_embedded += len(texts)
        return await self._inner.embed(texts)


def _world_with_npc():
    w = World()
    w.add_location(Location(id="a", name="A", description="", exits={}))
    w.add_entity(Npc(id="npc_guide", name="Guide", description="",
                     location_id="a", persona={}))
    return w


# --- the store primitive -----------------------------------------------------

class MemoryStoreTests(unittest.TestCase):
    def test_insert_and_load_round_trip(self):
        store = MemoryStore(":memory:")
        e = MemoryEntry(text="the player swore an oath", kind="speech", t=12.5,
                        importance=7)
        rowid = store.insert("npc", e)
        self.assertIsInstance(rowid, int)
        loaded = store.load("npc")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].text, "the player swore an oath")
        self.assertEqual(loaded[0].kind, "speech")
        self.assertEqual(loaded[0].t, 12.5)
        self.assertEqual(loaded[0].importance, 7)
        self.assertEqual(loaded[0].rowid, rowid)
        self.assertIsNone(loaded[0].embedding)
        store.close()

    def test_embedding_blob_round_trips(self):
        store = MemoryStore(":memory:")
        vec = [0.5, -0.25, 0.125, 0.0]
        e = MemoryEntry(text="x", kind="observation", t=1.0, importance=2,
                        embedding=vec)
        store.insert("npc", e)
        got = store.load("npc")[0].embedding
        self.assertEqual(len(got), len(vec))
        for a, b in zip(got, vec):
            self.assertTrue(math.isclose(a, b, abs_tol=1e-6))   # float32 tolerance
        store.close()

    def test_update_embedding(self):
        store = MemoryStore(":memory:")
        rowid = store.insert("npc", MemoryEntry(text="x", t=1.0, importance=2))
        store.update_embedding(rowid, [1.0, 2.0, 3.0])
        self.assertIsNotNone(store.load("npc")[0].embedding)
        store.close()

    def test_agent_isolation(self):
        store = MemoryStore(":memory:")
        store.insert("a", MemoryEntry(text="a-mem", t=1.0, importance=1))
        store.insert("b", MemoryEntry(text="b-mem", t=1.0, importance=1))
        self.assertEqual([e.text for e in store.load("a")], ["a-mem"])
        self.assertEqual(store.count("b"), 1)
        self.assertEqual(store.count("c"), 0)
        store.close()


# --- the SQLite-backed stream ------------------------------------------------

class BackedStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_add_writes_through_and_reloads(self):
        store = MemoryStore(":memory:")
        s = MemoryStream(store=store, agent_id="npc")
        s.add("first memory", kind="event")
        s.add("second memory")
        self.assertEqual(store.count("npc"), 2)
        # A fresh stream on the same store reads the agent's history back.
        s2 = MemoryStream(store=store, agent_id="npc")
        self.assertEqual([e.text for e in s2.entries],
                         ["first memory", "second memory"])
        self.assertEqual(s2.entries[0].kind, "event")
        store.close()

    async def test_embeddings_persist_and_are_not_recomputed(self):
        store = MemoryStore(":memory:")
        e1 = CountingEmbedder()
        s = MemoryStream(embedder=e1, store=store, agent_id="npc")
        for i in range(3):
            s.add(f"memory number {i}")
        await s.retrieve("which memory", k=3)         # embeds query + 3 memories
        self.assertEqual(e1.texts_embedded, 4)
        # Re-open on the same store: the 3 memories load WITH their embeddings, so a
        # new retrieval embeds only the query — no re-embedding the history.
        e2 = CountingEmbedder()
        s2 = MemoryStream(embedder=e2, store=store, agent_id="npc")
        self.assertTrue(all(e.embedding is not None for e in s2.entries))
        await s2.retrieve("another query", k=3)
        self.assertEqual(e2.texts_embedded, 1)
        store.close()

    def test_load_state_migrates_once_into_empty_store(self):
        store = MemoryStore(":memory:")
        s = MemoryStream(store=store, agent_id="npc")
        self.assertEqual(store.count("npc"), 0)
        s.load_state([{"text": "an old memory", "kind": "speech", "t": 1.0,
                       "importance": 6}])
        self.assertEqual(store.count("npc"), 1)        # migrated
        self.assertEqual([e.text for e in s.entries], ["an old memory"])
        # A second load_state (store now non-empty) is ignored — no double import,
        # the DB is authoritative.
        s2 = MemoryStream(store=store, agent_id="npc")
        s2.load_state([{"text": "should not import", "kind": "speech", "t": 2.0}])
        self.assertEqual(store.count("npc"), 1)
        self.assertEqual([e.text for e in s2.entries], ["an old memory"])
        store.close()


# --- engine + persistence integration ---------------------------------------

class EngineStoreTests(unittest.TestCase):
    def test_engine_memory_survives_restart_via_store(self):
        d = tempfile.mkdtemp()
        try:
            db = os.path.join(d, "m.db")
            store = MemoryStore(db)
            e1 = Engine(_world_with_npc(), FakeProvider(), start_location="a",
                        memory_store=store)
            e1.minds["npc_guide"].memory.add("the hermit saw a storm", kind="event")
            e1.attach_director(_FakeLoop())
            e1.director.mind.memory.add("I stirred the sky", kind="director")
            store.close()
            # A brand-new process: new store on the same file, new engine.
            store2 = MemoryStore(db)
            e2 = Engine(_world_with_npc(), FakeProvider(), start_location="a",
                        memory_store=store2)
            e2.attach_director(_FakeLoop())
            self.assertIn("the hermit saw a storm",
                          [m.text for m in e2.minds["npc_guide"].memory.entries])
            self.assertIn("I stirred the sky",
                          [m.text for m in e2.director.mind.memory.entries])
            store2.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_snapshot_omits_memory_when_store_present(self):
        store = MemoryStore(":memory:")
        engine = Engine(_world_with_npc(), FakeProvider(), start_location="a",
                        memory_store=store)
        engine.minds["npc_guide"].memory.add("in the db, not the overlay")
        save = persistence.snapshot(engine)
        self.assertEqual(save["memory"], {})      # memory is the DB's job now
        store.close()

    def test_restore_migrates_old_overlay_memory_into_store(self):
        # An old (pre-1b) save still carries a memory block; restoring it into a fresh
        # store imports the memory once — the upgrade path.
        store = MemoryStore(":memory:")
        engine = Engine(_world_with_npc(), FakeProvider(), start_location="a",
                        memory_store=store)
        save = {"version": 1, "positions": {}, "items": [],
                "memory": {"npc_guide": [{"text": "remembered across the upgrade",
                                          "kind": "event", "t": 1.0,
                                          "importance": 5}]}}
        persistence.restore(engine, save)
        self.assertIn("remembered across the upgrade",
                      [m.text for m in engine.minds["npc_guide"].memory.entries])
        self.assertEqual(store.count("npc_guide"), 1)
        store.close()


class _FakeLoop:
    def add_system(self, fn):
        pass


if __name__ == "__main__":
    unittest.main()
