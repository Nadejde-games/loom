"""Memory depth (Phase 5, slice 1a), offline: importance + embedding-relevance
retrieval. All deterministic via a FakeEmbeddingProvider (a stable bag-of-words hash)
— no GPU, no network. Proves, from the primitives up:

  * the importance heuristic (kind base + salience cues, clamped 1-10);
  * the fake embedder is deterministic and lexically sensible (shared words -> closer);
  * retrieve() degrades to exactly recent(k) with no embedder / on embedder failure;
  * the headline: a buried, salient, *relevant* memory (an old promise about the key)
    surfaces through retrieve() when it has fallen out of recent(k) and recency alone
    cannot see it — the whole reason the slice exists;
  * the retrieval feeds the mind's prompt (the one honest caller touch);
  * the engine injects the embedder into every mind;
  * state()/load_state round-trip importance and do NOT persist the embedding cache.
"""
import time
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine
from loom.ai import FakeProvider, NpcMind, MemoryStream, score_importance
from loom.ai.memory import _cosine
from loom.ai.embedding import FakeEmbeddingProvider


class RaisingEmbedder:
    """An embedder that always fails — to prove retrieval degrades, never breaks."""
    dim = 8

    async def embed(self, texts):
        raise RuntimeError("embedder is down")


class FakeLoop:
    def __init__(self):
        self.systems = []

    def add_system(self, fn):
        self.systems.append(fn)


# --- 1. the importance heuristic --------------------------------------------

class ImportanceTests(unittest.TestCase):
    def test_kind_sets_the_base(self):
        # A reflection/event outranks idle observation, all else equal.
        self.assertGreater(score_importance("x", "reflection"),
                           score_importance("x", "observation"))
        self.assertGreater(score_importance("x", "event"),
                           score_importance("x", "speech"))

    def test_salience_cues_raise_it(self):
        plain = score_importance("a traveler passed by", "observation")
        salient = score_importance("he swore a vow of death", "observation")
        self.assertGreater(salient, plain)

    def test_clamped_1_to_10(self):
        # Many cues on a high-base kind stays within range.
        hot = score_importance("swore a vow: death, blade, betray, key, curse",
                               "reflection")
        self.assertLessEqual(hot, 10)
        self.assertGreaterEqual(score_importance("", "observation"), 1)


# --- 2. the fake embedder ----------------------------------------------------

class FakeEmbedderTests(unittest.IsolatedAsyncioTestCase):
    async def test_deterministic(self):
        emb = FakeEmbeddingProvider(dim=64)
        a = (await emb.embed(["the black key"]))[0]
        b = (await emb.embed(["the black key"]))[0]
        self.assertEqual(a, b)

    async def test_shared_words_are_closer(self):
        emb = FakeEmbeddingProvider(dim=128)
        key, near, far = await emb.embed(
            ["the black key", "a black key on the floor", "sunlight over the meadow"])
        self.assertGreater(_cosine(key, near), _cosine(key, far))

    async def test_empty_text_is_a_zero_vector(self):
        emb = FakeEmbeddingProvider(dim=16)
        v = (await emb.embed([""]))[0]
        self.assertEqual(_cosine(v, v), 0.0)   # norm 0 -> cosine defined as 0


# --- 3. retrieval ------------------------------------------------------------

class RetrieveTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_embedder_is_recent(self):
        m = MemoryStream()                     # no embedder
        for i in range(10):
            m.add(f"event {i}")
        got = await m.retrieve("anything", k=5)
        self.assertEqual([e.text for e in got], [e.text for e in m.recent(5)])

    async def test_degrades_on_embedder_failure(self):
        m = MemoryStream(embedder=RaisingEmbedder())
        for i in range(6):
            m.add(f"e{i}")
        got = await m.retrieve("q", k=3)
        self.assertEqual([e.text for e in got], [e.text for e in m.recent(3)])

    async def test_surfaces_buried_important_relevant_memory(self):
        """The headline proof: an old, salient, on-topic memory outranks the flood of
        recent trivia — where recent(k) cannot see it at all."""
        m = MemoryStream(embedder=FakeEmbeddingProvider(dim=128))
        promise = m.add("The player swore to bring me the black key.", kind="speech")
        promise.t = time.time() - 100 * 3600   # a hundred game-hours ago: buried
        for i in range(12):                     # a dozen recent, mundane observations
            m.add(f"A traveler passed by, number {i}.", kind="observation")
        # recency alone loses it — it is not among the last 8.
        self.assertNotIn(promise.text, [e.text for e in m.recent(8)])
        # retrieval, asked about the key, brings it back.
        got = await m.retrieve("what about the black key you owe me", k=5)
        self.assertIn(promise.text, [e.text for e in got])

    async def test_returns_all_when_fewer_than_k(self):
        m = MemoryStream(embedder=FakeEmbeddingProvider(dim=16))
        m.add("only one memory")
        got = await m.retrieve("anything", k=5)
        self.assertEqual(len(got), 1)


# --- 4. the caller seam: retrieval feeds the prompt --------------------------

class MindIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_feeds_the_prompt(self):
        npc = Npc(id="n", name="Guide", description="", location_id="a", persona={})
        mind = NpcMind(npc, FakeProvider(), embedder=FakeEmbeddingProvider(dim=128))
        promise = mind.memory.add("I promised to guard the black key.", kind="speech")
        promise.t = time.time() - 100 * 3600
        for i in range(12):
            mind.memory.add(f"idle chatter number {i}")
        # The promise is not in the recent window that the query-free path would use.
        self.assertNotIn("promised to guard",
                         "\n".join(m.text for m in mind.memory.recent(8)))
        # But recall for a key-query surfaces it, and the prompt then carries it.
        mems = await mind._recall("where is the black key")
        prompt = mind._system_prompt(None, mems)
        self.assertIn("promised to guard", prompt)


# --- 5. engine wiring --------------------------------------------------------

def _world_with_npc():
    w = World()
    w.add_location(Location(id="a", name="A", description="", exits={}))
    w.add_entity(Npc(id="npc_guide", name="Guide", description="",
                     location_id="a", persona={}))
    return w


class EngineWiringTests(unittest.TestCase):
    def test_engine_injects_embedder_into_minds_and_director(self):
        emb = FakeEmbeddingProvider()
        engine = Engine(_world_with_npc(), FakeProvider(), start_location="a",
                        embedder=emb)
        self.assertIs(engine.minds["npc_guide"].memory._embedder, emb)
        engine.attach_director(FakeLoop())
        self.assertIs(engine.director.mind.memory._embedder, emb)

    def test_no_embedder_by_default(self):
        engine = Engine(_world_with_npc(), FakeProvider(), start_location="a")
        self.assertIsNone(engine.minds["npc_guide"].memory._embedder)


# --- 6. persistence of the new fields ---------------------------------------

class MemoryStateTests(unittest.TestCase):
    def test_state_round_trips_importance_not_embedding(self):
        m = MemoryStream()
        e = m.add("The player swore to bring the key.", kind="speech")
        e.embedding = [0.1, 0.2, 0.3]          # a filled cache
        st = m.state()
        self.assertNotIn("embedding", st[0])   # the cache is never persisted
        self.assertEqual(st[0]["importance"], e.importance)
        restored = MemoryStream()
        restored.load_state(st)
        self.assertEqual(restored.entries[0].importance, e.importance)
        self.assertIsNone(restored.entries[0].embedding)   # re-derived lazily later

    def test_load_state_tolerant_of_missing_importance(self):
        m = MemoryStream()
        m.load_state([{"text": "a threat of death", "kind": "event", "t": 1.0}])
        self.assertEqual(m.entries[0].importance,
                         score_importance("a threat of death", "event"))


if __name__ == "__main__":
    unittest.main()
