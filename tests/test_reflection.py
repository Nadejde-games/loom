"""Reflection — the second memory cognition (Phase 5, slice 2), offline.

Deterministic: a *scripted* provider returns canned questions/insights JSON keyed off
which reflection call it is (no network, no GPU), a ``FakeEmbeddingProvider`` for the
relevance path, SQLite ``:memory:`` for the durability angle. Proves:

  * ``reflect`` runs the two-call Generative-Agents synthesis and writes an insight back
    as a ``kind="reflection"`` memory carrying the GA-style ``(because of: …)`` provenance;
  * the value claim — a later ``retrieve`` about the subject surfaces the distilled belief
    (high importance + newest) over the raw lines;
  * reflections are durable through the store (survive a re-open);
  * the ``Reflector`` orchestrator fires only once an agent crosses the importance
    threshold, reflects the most-over-threshold agent, and advances the watermark so a
    reflection never re-triggers itself (depth-1) — and does NOT re-reflect a loaded
    backlog on first sight;
  * citations map, dedup, bound-check, and truncate; an uncited insight stores clean;
  * every degrade path (too few memories, no questions, a prose-only provider) returns
    ``[]`` and touches nothing.
"""
import asyncio
import json
import unittest

from loom.world import World, Location, Npc
from loom.engine import Engine
from loom.ai import (FakeProvider, MemoryStream, MemoryStore, MemoryEntry,
                     Reflector, reflect)
from loom.ai.embedding import FakeEmbeddingProvider
from loom.ai.reflection import _cited_texts
from loom.style import plain
from loom.protocol import Channel


async def _drain(engine):
    """Await any background reflection tasks the reflector spawned this tick."""
    if engine._tasks:
        await asyncio.gather(*list(engine._tasks))


class _CapturingSession:
    """A transport stub that records what was sent — to prove the perceptible tell
    reaches a present player. Mirrors the session surface the engine broadcasts through."""
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


class ScriptedReflectionProvider:
    """Returns canned reflection JSON, keyed off the schema shape echoed in the system
    prompt: the questions call carries ``"questions"``, the synthesis call ``"insights"``
    (disjoint markers). Any other call returns empty — a clean degrade. Counts its calls."""
    name = "scripted"

    def __init__(self, questions, insights_obj):
        self._questions = questions
        self._insights = insights_obj
        self.calls = []

    async def complete(self, system, messages, schema=None, temperature=None):
        self.calls.append(system)
        if '"insights"' in system:
            return json.dumps(self._insights)
        if '"questions"' in system:
            return json.dumps({"questions": self._questions})
        return ""


def _promise_provider():
    """A scripted provider that asks one promise question and returns one grounded
    insight citing the first two evidence memories."""
    return ScriptedReflectionProvider(
        questions=["Does the player keep the promises they make to me?"],
        insights_obj={"insights": [
            {"text": "The player's promises mean nothing", "because_of": [1, 2]}]})


def _world_with_npc():
    w = World()
    w.add_location(Location(id="a", name="A", description="", exits={}))
    w.add_entity(Npc(id="npc_hermit", name="the Hermit", description="",
                     location_id="a", persona={"backstory": "A wary recluse."}))
    return w


class _FakeLoop:
    def add_system(self, fn):
        pass


# --- the cognition -----------------------------------------------------------

class ReflectCognitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reflect_writes_a_grounded_reflection(self):
        s = MemoryStream()
        s.add("The player swore to bring me the black key.", kind="speech")
        s.add("The player left without a word.", kind="observation")
        s.add("The player returned empty-handed.", kind="observation")
        prov = _promise_provider()

        added = await reflect(s, prov, subject="You are the Hermit, a wary recluse.")

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].kind, "reflection")
        self.assertGreaterEqual(added[0].importance, 8)     # reflection kind weight
        self.assertIn("The player's promises mean nothing", added[0].text)
        # GA-style provenance: the cited evidence texts echoed into the stored insight.
        self.assertIn("(because of:", added[0].text)
        self.assertIn("The player swore to bring me the black key.", added[0].text)
        self.assertEqual(prov.calls.__len__(), 2)           # questions + insights
        # The reflection joined the stream and can be recalled like any memory.
        self.assertIs(s.entries[-1], added[0])

    async def test_reflection_surfaces_as_a_durable_belief(self):
        # The value claim: after reflecting, a later retrieval about the subject ranks the
        # distilled belief top — it is the newest and the most important memory there is.
        s = MemoryStream(embedder=FakeEmbeddingProvider(dim=64))
        for line in ["The player swore an oath to me.",
                     "The player broke the oath.",
                     "A traveller passed through.",
                     "The player made another promise.",
                     "The player broke it too."]:
            s.add(line, kind="speech")
        await reflect(s, _promise_provider(), subject="You are the Hermit.")

        top = await s.retrieve("does the player keep their word", k=3)
        self.assertEqual(top[0].kind, "reflection")
        self.assertIn("promises mean nothing", top[0].text)

    async def test_reflection_is_durable_through_the_store(self):
        store = MemoryStore(":memory:")
        s = MemoryStream(store=store, agent_id="npc_hermit")
        s.add("The player swore to bring me the black key.", kind="speech")
        s.add("The player never came back.", kind="observation")
        s.add("The player was seen laughing in the tavern.", kind="observation")
        added = await reflect(s, _promise_provider(), subject="You are the Hermit.")
        self.assertEqual(len(added), 1)
        # A fresh stream on the same store reads the reflection back — it persisted.
        s2 = MemoryStream(store=store, agent_id="npc_hermit")
        reflections = [e for e in s2.entries if e.kind == "reflection"]
        self.assertEqual(len(reflections), 1)
        self.assertIn("promises mean nothing", reflections[0].text)
        store.close()

    async def test_uncited_insight_stores_clean(self):
        s = MemoryStream()
        for i in range(3):
            s.add(f"observation {i}")
        prov = ScriptedReflectionProvider(
            questions=["What is happening?"],
            insights_obj={"insights": [{"text": "Nothing much recurs here",
                                        "because_of": []}]})
        added = await reflect(s, prov, subject="You are a watcher.")
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].text, "Nothing much recurs here")   # no clause
        self.assertNotIn("because of", added[0].text)


# --- degradation -------------------------------------------------------------

class ReflectDegradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_too_few_memories_is_a_noop(self):
        s = MemoryStream()
        s.add("only one memory")
        prov = _promise_provider()
        self.assertEqual(await reflect(s, prov, subject="X", min_memories=3), [])
        self.assertEqual(prov.calls, [])          # not even a question was asked

    async def test_no_questions_is_a_noop(self):
        s = MemoryStream()
        for i in range(3):
            s.add(f"memory {i}")
        prov = ScriptedReflectionProvider(questions=[], insights_obj={"insights": []})
        self.assertEqual(await reflect(s, prov, subject="X"), [])
        self.assertEqual(len(prov.calls), 1)      # asked, got nothing, stopped

    async def test_prose_only_provider_degrades(self):
        # The real FakeProvider returns persona prose, not reflection JSON — reflection
        # must no-op cleanly, so the existing suite and a fake-backed world are unharmed.
        s = MemoryStream()
        for i in range(4):
            s.add(f"memory {i}")
        self.assertEqual(await reflect(s, FakeProvider(), subject="You are X."), [])


# --- citations ---------------------------------------------------------------

class CitationTests(unittest.TestCase):
    def _ev(self, *texts):
        return [MemoryEntry(text=t) for t in texts]

    def test_maps_dedups_and_bounds(self):
        ev = self._ev("first", "second", "third")
        # 2 and 9(out of range) and a duplicate 1 and a string "1"
        got = _cited_texts([1, 2, 9, 1, "1"], ev)
        self.assertEqual(got, ["first", "second"])

    def test_dedups_identical_texts_across_indices(self):
        # An NPC that said the same line thrice: three distinct evidence rows, one text.
        ev = self._ev("A vow is wind.", "A vow is wind.", "I broke my word.")
        got = _cited_texts([1, 2, 3], ev)
        self.assertEqual(got, ["A vow is wind.", "I broke my word."])

    def test_non_list_is_empty(self):
        self.assertEqual(_cited_texts(None, self._ev("x")), [])
        self.assertEqual(_cited_texts(5, self._ev("x")), [])

    def test_truncates_a_long_citation(self):
        long = "x" * 200
        got = _cited_texts([1], self._ev(long))
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].endswith("…"))
        self.assertLess(len(got[0]), 90)


# --- the orchestrator --------------------------------------------------------

class ReflectorTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self):
        return Engine(_world_with_npc(), _promise_provider(), start_location="a")

    async def _drain(self, engine):
        await _drain(engine)

    async def test_backlog_is_not_re_reflected_on_first_sight(self):
        # A store-backed restart loads a mind's history; the reflector must NOT reflect
        # that whole backlog the first time it sees the agent (lazy watermark = now).
        engine = self._engine()
        mem = engine.minds["npc_hermit"].memory
        for i in range(6):
            mem.add("The player swore an oath and broke it.", kind="speech")
        r = Reflector(engine, period_ticks=1, importance_threshold=10,
                      include_director=False)
        await r.tick(0.0)
        await self._drain(engine)
        self.assertEqual([e for e in mem.entries if e.kind == "reflection"], [])
        self.assertEqual(engine.provider.calls, [])   # no model call spent on the backlog

    async def test_fires_when_new_memory_crosses_threshold_then_holds(self):
        engine = self._engine()
        mem = engine.minds["npc_hermit"].memory
        r = Reflector(engine, period_ticks=1, importance_threshold=10,
                      include_director=False)
        # First pulse: nothing yet — sets the watermark to now.
        await r.tick(0.0)
        await self._drain(engine)
        self.assertEqual([e for e in mem.entries if e.kind == "reflection"], [])
        # New salient memory accrues past the threshold.
        for _ in range(3):
            mem.add("The player swore an oath and broke it.", kind="speech")
        # Next pulse: the agent is over the bar — it reflects exactly once.
        await r.tick(0.0)
        await self._drain(engine)
        reflections = [e for e in mem.entries if e.kind == "reflection"]
        self.assertEqual(len(reflections), 1)
        # Depth-1: the watermark advanced past the reflection, so a further pulse — with
        # no new memory — does not reflect again.
        calls_after_first = len(engine.provider.calls)
        await r.tick(0.0)
        await self._drain(engine)
        self.assertEqual(len([e for e in mem.entries if e.kind == "reflection"]), 1)
        self.assertEqual(len(engine.provider.calls), calls_after_first)

    async def test_below_threshold_never_fires(self):
        engine = self._engine()
        mem = engine.minds["npc_hermit"].memory
        r = Reflector(engine, period_ticks=1, importance_threshold=1000,
                      include_director=False)
        await r.tick(0.0)
        await self._drain(engine)
        for _ in range(3):
            mem.add("The player swore an oath and broke it.", kind="speech")
        await r.tick(0.0)
        await self._drain(engine)
        self.assertEqual([e for e in mem.entries if e.kind == "reflection"], [])

    async def test_director_is_in_scope_when_included(self):
        engine = self._engine()
        engine.attach_director(_FakeLoop())
        r = Reflector(engine, period_ticks=1, importance_threshold=10,
                      include_director=True)
        agents = {aid for aid, _ in r._minds()}
        self.assertIn("director", agents)
        self.assertIn("npc_hermit", agents)
        # The director distills over its own beats — same mechanism, its own subject.
        dmem = engine.director.mind.memory
        await r.tick(0.0)                      # init watermarks
        await self._drain(engine)
        for _ in range(4):
            dmem.add("The wanderers linger at the ruined shrine.", kind="event")
        await r.tick(0.0)
        await self._drain(engine)
        self.assertTrue(any(e.kind == "reflection" for e in dmem.entries))


# --- the perceptible tell ----------------------------------------------------

class ReflectionTellTests(unittest.IsolatedAsyncioTestCase):
    def _engine_with_player(self):
        engine = Engine(_world_with_npc(), _promise_provider(), start_location="a")
        return engine

    async def test_tell_reaches_a_present_player(self):
        engine = self._engine_with_player()
        s = _CapturingSession()
        await engine.on_connect(s)
        s.sent.clear()                           # drop the banner / arrival lines
        await engine.narrate_reflection("npc_hermit")
        self.assertIn("gaze turning inward", s.texts())
        # and the moment is recorded to the chronicle as an ambient beat
        self.assertTrue(any("gaze turning inward" in line
                            for line in engine.chronicle.render(50).splitlines()))

    async def test_no_tell_for_bodiless_or_unknown_agent(self):
        engine = self._engine_with_player()
        s = _CapturingSession()
        await engine.on_connect(s)
        s.sent.clear()
        await engine.narrate_reflection("director")     # bodiless — no room
        await engine.narrate_reflection("nobody")        # unknown id
        self.assertEqual(s.texts(), "")

    async def test_no_tell_when_no_player_present(self):
        engine = self._engine_with_player()   # NPC at "a", but nobody connected
        await engine.narrate_reflection("npc_hermit")     # must not raise, sends nothing
        self.assertEqual(engine.players, {})

    async def test_reflector_fires_the_tell_end_to_end(self):
        engine = self._engine_with_player()
        s = _CapturingSession()
        await engine.on_connect(s)
        mem = engine.minds["npc_hermit"].memory
        r = Reflector(engine, period_ticks=1, importance_threshold=10,
                      include_director=False, tell=True)
        await r.tick(0.0)
        await _drain(engine)                     # init watermark
        s.sent.clear()
        for _ in range(3):
            mem.add("The player swore an oath and broke it.", kind="speech")
        await r.tick(0.0)
        await _drain(engine)
        self.assertIn("gaze turning inward", s.texts())

    async def test_tell_off_stays_silent(self):
        engine = self._engine_with_player()
        s = _CapturingSession()
        await engine.on_connect(s)
        mem = engine.minds["npc_hermit"].memory
        r = Reflector(engine, period_ticks=1, importance_threshold=10,
                      include_director=False, tell=False)
        await r.tick(0.0)
        await _drain(engine)
        s.sent.clear()
        for _ in range(3):
            mem.add("The player swore an oath and broke it.", kind="speech")
        await r.tick(0.0)
        await _drain(engine)
        self.assertNotIn("gaze turning inward", s.texts())
        self.assertEqual(len([e for e in mem.entries if e.kind == "reflection"]), 1)


# --- the persona seam --------------------------------------------------------

class ReflectionSubjectTests(unittest.TestCase):
    def test_npc_subject_carries_name_and_persona(self):
        npc = Npc(id="n", name="the Hermit", description="", location_id="a",
                  persona={"backstory": "A wary recluse.", "traits": ["wary"]})
        from loom.ai import NpcMind
        subj = NpcMind(npc, FakeProvider()).reflection_subject()
        self.assertIn("the Hermit", subj)
        self.assertIn("A wary recluse.", subj)
        self.assertIn("wary", subj)

    def test_director_subject_carries_name_and_tone(self):
        from loom.ai import DirectorMind
        d = DirectorMind(persona={"tone": "hushed and watchful"}, name="the Weaver")
        subj = d.reflection_subject()
        self.assertIn("the Weaver", subj)
        self.assertIn("hushed and watchful", subj)


if __name__ == "__main__":
    unittest.main()
