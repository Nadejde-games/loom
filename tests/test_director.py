"""The game-master director, offline: the mind maps perception to a validated
stage_event (and knows when to do nothing), and the orchestrator's cadence is
slow, lazy (no model call when idle or unattended), non-blocking, and never
overlaps itself. All deterministic — canned providers, no GPU, no network."""
import asyncio
import unittest

from loom.world import World, Location
from loom.engine import Engine
from loom.ai import FakeProvider
from loom.ai.director import DirectorMind, Director, ACT_GATE_TEMPERATURE
from loom.action import default_registry
from loom.protocol import Channel

# A well-formed director turn: one ambient beat into room "room".
STAGE = ('{"speech":"let the wood breathe","actions":[{"name":"stage_event",'
         '"args":{"location":"room","text":"A cold wind gutters the lanterns."}}]}')
WATCH = '{"speech":"","actions":[]}'
STAGE_LINE = "A cold wind gutters the lanterns."

# A world-shaping director turn: raise a standing condition over room "room".
SET_STORM = ('{"speech":"the sky turns","actions":[{"name":"set_condition",'
             '"args":{"location":"room","tag":"storm",'
             '"text":"A cold rain begins to fall."}}]}')
STORM_LINE = "A cold rain begins to fall."

# Act-gate decision replies (B8): the tiny wait/act envelope the decision pass
# returns, ahead of any compose reply.
DECIDE_ACT = '{"decision":"act","reason":"the moment calls for it"}'
DECIDE_WAIT = '{"decision":"wait","reason":"let it breathe"}'


class CannedProvider:
    """Returns scripted replies in order (the last repeats); records the schema
    handed to each call, so a test can prove the constraint was narrowed."""
    name = "canned"

    def __init__(self, *replies):
        self.replies = list(replies) or [WATCH]
        self.calls = 0
        self.schemas = []
        self.messages = []          # the messages handed to each call (nudge spy)
        self.system_prompts = []    # the system prompt handed to each call
        self.temperatures = []      # the per-call temperature (act-gate spy)

    async def complete(self, system, messages, schema=None, temperature=None):
        self.schemas.append(schema)
        self.messages.append(messages)
        self.system_prompts.append(system)
        self.temperatures.append(temperature)
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


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
        return "\n".join(d for (c, d) in self.sent if c == Channel.TEXT)


class FakeLoop:
    def __init__(self):
        self.systems = []

    def add_system(self, fn):
        self.systems.append(fn)


def _mind(*replies):
    return DirectorMind(persona={"tone": "hushed"}, provider=CannedProvider(*replies),
                        registry=default_registry(), offered=["stage_event"])


def _build(period=3, reply=STAGE, min_new_events=1, cooldown_pulses=1,
           lull_pulses=0):
    """A one-room world with a director attached, its own canned provider.

    Restraint defaults to permissive (1, 1) so the cadence-mechanics tests below
    exercise the timing wheel without the B8 rate-limit interfering; the restraint
    suite sets stricter values explicitly. ``lull_pulses`` defaults to 0 (off), so
    every prior test keeps its exact behaviour; the lull suite sets it explicitly."""
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    engine = Engine(world, FakeProvider(), start_location="room")
    canned = CannedProvider(reply)
    director = engine.attach_director(FakeLoop(), provider=canned,
                                      period_ticks=period,
                                      min_new_events=min_new_events,
                                      cooldown_pulses=cooldown_pulses,
                                      lull_pulses=lull_pulses)
    return engine, director, canned


def _build_gated(*replies, min_new_events=1, cooldown_pulses=1, lull_pulses=0):
    """A one-room, act-gated director (B8): its canned provider serves the decision
    reply first, then any compose reply, in order — mirroring the two-pass turn
    (decide -> maybe observe). Restraint defaults permissive so the gate itself,
    not the deterministic ceiling, is what these tests exercise."""
    world = World()
    world.add_location(Location(id="room", name="Room", description="A bare room."))
    engine = Engine(world, FakeProvider(), start_location="room")
    canned = CannedProvider(*replies)
    director = engine.attach_director(FakeLoop(), provider=canned, period_ticks=1,
                                      min_new_events=min_new_events,
                                      cooldown_pulses=cooldown_pulses,
                                      lull_pulses=lull_pulses, act_gate=True)
    return engine, director, canned


async def _drain(engine):
    for _ in range(10):
        if not engine._tasks:
            return
        await asyncio.gather(*list(engine._tasks))


class DirectorMindTests(unittest.TestCase):
    def test_maps_perception_to_stage_event(self):
        mind = _mind(STAGE)
        turn = asyncio.run(mind.observe("- Wren arrived", "- room (Room): Wren"))
        self.assertEqual(len(turn.actions), 1)
        a = turn.actions[0]
        self.assertEqual(a.name, "stage_event")
        self.assertEqual(a.args["location"], "room")
        self.assertFalse(turn.is_silent)

    def test_empty_turn_is_watch(self):
        mind = _mind(WATCH)
        turn = asyncio.run(mind.observe("- nothing", "- room (Room): Wren"))
        self.assertEqual(turn.actions, [])
        self.assertTrue(turn.is_silent)

    def test_remembers_its_own_musing(self):
        mind = _mind(STAGE)
        asyncio.run(mind.observe("- x", "- room (Room): Wren"))
        mems = [m.text for m in mind.memory.recent()]
        self.assertTrue(any("let the wood breathe" in m for m in mems))

    def test_invalid_action_triggers_retry_and_recovers(self):
        bad = ('{"speech":"oops","actions":[{"name":"stage_event",'
               '"args":{"location":"room"}}]}')            # missing required text
        mind = _mind(bad, STAGE)
        turn = asyncio.run(mind.observe("- x", "- room (Room): Wren"))
        self.assertEqual(mind.provider.calls, 2)           # it retried once
        self.assertEqual([a.name for a in turn.actions], ["stage_event"])

    def test_constraint_is_narrowed_to_director_actions(self):
        mind = _mind(WATCH)
        asyncio.run(mind.observe("- x", "- room (Room): Wren"))
        schema = mind.provider.schemas[0]
        self.assertIsNotNone(schema)                       # constrained decoding on
        branches = schema["properties"]["actions"]["items"]["oneOf"]
        consts = {b["properties"]["name"]["const"] for b in branches}
        self.assertEqual(consts, {"stage_event"})          # only its own actions


class DirectorMindGateTests(unittest.TestCase):
    """B8 act-gate at the mind level: ``decide`` returns a wait/act judgment from a
    cheap, low-temperature, tightly-constrained pass, and fails toward silence."""

    def test_act_decision_returns_true_with_reason(self):
        mind = _mind(DECIDE_ACT)
        act, reason = asyncio.run(mind.decide("- x", "- room (Room): Wren"))
        self.assertTrue(act)
        self.assertEqual(reason, "the moment calls for it")

    def test_wait_decision_returns_false(self):
        mind = _mind(DECIDE_WAIT)
        act, _ = asyncio.run(mind.decide("- x", "- room (Room): Wren"))
        self.assertFalse(act)

    def test_unparseable_decision_fails_toward_wait(self):
        # A restraint gate that cannot read the model's answer must not act.
        mind = _mind("on balance, I would rather not commit to anything")
        act, _ = asyncio.run(mind.decide("- x", "- room (Room): Wren"))
        self.assertFalse(act)

    def test_decision_pass_is_low_temp_and_constrained(self):
        mind = _mind(DECIDE_WAIT)
        asyncio.run(mind.decide("- x", "- room (Room): Wren"))
        self.assertEqual(mind.provider.temperatures[0], ACT_GATE_TEMPERATURE)
        schema = mind.provider.schemas[0]
        self.assertEqual(schema["properties"]["decision"]["enum"], ["wait", "act"])

    def test_decision_prompt_is_lean_no_action_catalogue(self):
        # The gate only judges wait-or-act, so it carries none of compose's action
        # catalogue or envelope examples — that is what keeps it cheap.
        mind = _mind(DECIDE_WAIT)
        asyncio.run(mind.decide("- happened", "- room (Room): Wren"))
        system = mind.provider.system_prompts[0]
        self.assertNotIn("Available actions", system)
        self.assertIn("happened", system)                  # it still reads perception
        self.assertIn("Wren", system)


class DirectorCadenceTests(unittest.TestCase):
    def test_fires_when_due_and_broadcasts_to_the_room(self):
        async def go():
            engine, director, _ = _build(period=3)
            s = FakeSession()
            await engine.on_connect(s)                      # player in room; seq=1
            await director.tick(1.0)                        # tick 1 — not due
            await director.tick(1.0)                        # tick 2 — not due
            await _drain(engine)
            self.assertNotIn(STAGE_LINE, s.texts())         # nothing staged yet
            await director.tick(1.0)                        # tick 3 — due
            await _drain(engine)
            self.assertIn(STAGE_LINE, s.texts())            # the beat reached the room
        asyncio.run(go())

    def test_skips_when_nothing_has_happened(self):
        async def go():
            engine, director, _ = _build(period=1)
            s = FakeSession()
            await engine.on_connect(s)                      # seq=1 (an arrival)
            await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(s.texts().count(STAGE_LINE), 1)  # one beat
            await director.tick(1.0)                        # nothing new since
            await _drain(engine)
            self.assertEqual(s.texts().count(STAGE_LINE), 1)  # no second beat
        asyncio.run(go())

    def test_skips_when_no_players_present(self):
        async def go():
            engine, director, canned = _build(period=1)
            engine.chronicle.record("a tree falls in the wood")  # activity, no one here
            await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(canned.calls, 0)              # model never consulted
            self.assertEqual(director._last_seq, 0)        # no beat ever ran
        asyncio.run(go())

    def test_not_due_before_the_period(self):
        async def go():
            engine, director, canned = _build(period=5)
            s = FakeSession()
            await engine.on_connect(s)
            for _ in range(4):                             # 4 < period
                await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(canned.calls, 0)              # not yet consulted
            self.assertNotIn(STAGE_LINE, s.texts())
        asyncio.run(go())

    def test_does_not_overlap_itself(self):
        async def go():
            gate = asyncio.Event()

            class Gated:
                name = "gated"

                def __init__(self):
                    self.calls = 0

                async def complete(self, system, messages, schema=None):
                    self.calls += 1
                    await gate.wait()
                    return STAGE

            engine, director, _ = _build(period=1)
            director.mind.provider = Gated()
            s = FakeSession()
            await engine.on_connect(s)                      # seq=1
            await director.tick(1.0)                        # due -> a beat starts
            await asyncio.sleep(0)                          # let it block on the gate
            self.assertTrue(director._running)
            self.assertEqual(len(engine._tasks), 1)
            await director.tick(1.0)                        # due again, but busy
            self.assertEqual(len(engine._tasks), 1)         # no second beat spawned
            gate.set()
            await _drain(engine)
            self.assertFalse(director._running)             # cleared when done
            self.assertIn(STAGE_LINE, s.texts())
        asyncio.run(go())


class DirectorRestraintTests(unittest.TestCase):
    """B8: the orchestrator holds intervention frequency down deterministically —
    a beat needs enough new activity AND enough breathing room since the last one,
    so the model is not consulted (and does not stage) on every pulse."""

    def test_waits_for_enough_new_events(self):
        async def go():
            engine, director, canned = _build(period=1, min_new_events=3,
                                              cooldown_pulses=1)
            s = FakeSession()
            await engine.on_connect(s)                     # 1 event (arrival)
            await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(canned.calls, 0)              # 1 < 3 -> no beat, no call
            engine.chronicle.record("a stir in the leaves")
            engine.chronicle.record("a distant call")      # now 3 new events
            await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(canned.calls, 1)              # threshold met -> a beat
        asyncio.run(go())

    def test_cooldown_spaces_beats_apart(self):
        async def go():
            engine, director, canned = _build(period=1, min_new_events=1,
                                              cooldown_pulses=3)
            s = FakeSession()
            await engine.on_connect(s)
            await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(canned.calls, 1)              # first beat allowed
            for _ in range(2):                             # plenty of events, but...
                engine.chronicle.record("something happens")
                await director.tick(1.0)
                await _drain(engine)
                self.assertEqual(canned.calls, 1)          # ...still within cooldown
            engine.chronicle.record("something happens")
            await director.tick(1.0)                        # 3rd pulse since the beat
            await _drain(engine)
            self.assertEqual(canned.calls, 2)              # cooldown elapsed -> a beat
        asyncio.run(go())

    def test_quiet_world_never_beats(self):
        async def go():
            # A lone arrival (1 event) under a stricter event floor: the director
            # stays its hand — no over-narration of an empty room. (Lull off.)
            engine, director, canned = _build(period=1, min_new_events=5,
                                              cooldown_pulses=1)
            s = FakeSession()
            await engine.on_connect(s)
            for _ in range(4):
                await director.tick(1.0)
                await _drain(engine)
            self.assertEqual(canned.calls, 0)
        asyncio.run(go())


class DirectorActGateTests(unittest.TestCase):
    """B8 act-gate at the orchestrator level: a warranted pulse first pays for the
    cheap wait/act decision, and composes only on 'act'. Layered AFTER the
    deterministic ceiling/floor, and off by default — so nothing regresses."""

    def test_off_by_default(self):
        _, director, _ = _build()
        self.assertFalse(director.act_gate)

    def test_act_decision_composes_the_beat(self):
        async def go():
            # Decision 'act' -> both the decision call and the compose call run, and
            # the beat reaches the room, exactly as an ungated warranted pulse would.
            engine, director, canned = _build_gated(DECIDE_ACT, STAGE)
            s = FakeSession()
            await engine.on_connect(s)                     # 1 event >= floor(1)
            await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(canned.calls, 2)              # decide + compose
            self.assertIn(STAGE_LINE, s.texts())
        asyncio.run(go())

    def test_wait_decision_stays_silent_and_pays_only_for_the_decision(self):
        async def go():
            engine, director, canned = _build_gated(DECIDE_WAIT)
            s = FakeSession()
            await engine.on_connect(s)                     # seq=1
            await director.tick(1.0)
            await _drain(engine)
            self.assertEqual(canned.calls, 1)              # only the decision, no compose
            self.assertNotIn(STAGE_LINE, s.texts())        # nothing staged
            # A wait marks the events seen (so they do not re-trigger)...
            self.assertEqual(director._last_seq, engine.chronicle.seq)
            # ...but is NOT a beat: the cooldown is not restarted (still warmed).
            self.assertGreaterEqual(director._pulses_since_beat,
                                    director.cooldown_pulses)
        asyncio.run(go())

    def test_wait_does_not_re_litigate_the_same_events(self):
        async def go():
            # After a wait, the identical events do not warrant a fresh decision —
            # the gate is not re-consulted until something new happens.
            engine, director, canned = _build_gated(DECIDE_WAIT)
            s = FakeSession()
            await engine.on_connect(s)
            await director.tick(1.0); await _drain(engine)
            self.assertEqual(canned.calls, 1)
            await director.tick(1.0); await _drain(engine)  # nothing new since
            self.assertEqual(canned.calls, 1)              # not consulted again
        asyncio.run(go())

    def test_wait_then_a_new_event_warrants_a_fresh_decision_and_beat(self):
        async def go():
            engine, director, canned = _build_gated(DECIDE_WAIT, DECIDE_ACT, STAGE)
            s = FakeSession()
            await engine.on_connect(s)                     # seq=1
            await director.tick(1.0); await _drain(engine)  # decide -> wait
            self.assertEqual(canned.calls, 1)
            engine.chronicle.record("a new stir in the dark")  # seq=2, genuinely new
            await director.tick(1.0); await _drain(engine)  # decide -> act -> compose
            self.assertEqual(canned.calls, 3)
            self.assertIn(STAGE_LINE, s.texts())
        asyncio.run(go())

    def test_decision_call_is_low_temp_lean_and_constrained(self):
        async def go():
            engine, director, canned = _build_gated(DECIDE_WAIT)
            s = FakeSession()
            await engine.on_connect(s)
            await director.tick(1.0); await _drain(engine)
            self.assertEqual(canned.temperatures[0], ACT_GATE_TEMPERATURE)
            self.assertEqual(canned.schemas[0]["properties"]["decision"]["enum"],
                             ["wait", "act"])
            self.assertNotIn("Available actions", canned.system_prompts[0])
        asyncio.run(go())

    def test_unparseable_decision_defaults_to_wait(self):
        async def go():
            engine, director, canned = _build_gated("hmm, hard to say either way")
            s = FakeSession()
            await engine.on_connect(s)
            await director.tick(1.0); await _drain(engine)
            self.assertEqual(canned.calls, 1)              # no compose followed
            self.assertNotIn(STAGE_LINE, s.texts())
        asyncio.run(go())

    def test_lull_path_is_not_gated(self):
        async def go():
            # The lull is a liveliness *floor*, not a thing to restrain: with the gate
            # on, a lull beat still composes, and the gate's decision pass never runs
            # (only observe does). A strict event floor forces the lull path, not
            # activity. This is the scoping that lets the gate default on without
            # killing B9 (the model judges a quiet scene 'leave it').
            engine, director, canned = _build_gated(STAGE, min_new_events=5,
                                                    cooldown_pulses=1, lull_pulses=2)
            s = FakeSession()
            await engine.on_connect(s)                     # 1 event, below the floor(5)
            await director.tick(1.0); await _drain(engine)  # pulses 1 -> 2 >= lull(2)
            self.assertEqual(canned.calls, 1)              # observe only — no decide
            self.assertIn(STAGE_LINE, s.texts())           # the gentle floor beat landed
        asyncio.run(go())


class DirectorLullTests(unittest.TestCase):
    """B9 lull trigger: opt-in (``lull_pulses`` > 0). The director stirs a *quiet*
    room with one gentle beat once it has stayed quiet long enough — a liveliness
    floor beside the activity ceiling — without touching the activity path or the
    off-by-default restraint behaviour."""

    def test_off_by_default_no_lull_however_long_it_stays_quiet(self):
        async def go():
            # No lull_pulses -> off. Many quiet pulses, still no beat (the B8 guard
            # is preserved exactly; enabling the lull is what relaxes it).
            engine, director, canned = _build(period=1, min_new_events=5,
                                              cooldown_pulses=1)
            s = FakeSession()
            await engine.on_connect(s)
            for _ in range(8):
                await director.tick(1.0)
                await _drain(engine)
            self.assertEqual(canned.calls, 0)
        asyncio.run(go())

    def test_lull_stirs_a_quiet_world_after_its_window(self):
        async def go():
            # Below the event floor forever (quiet), but the lull window is 3.
            engine, director, canned = _build(period=1, min_new_events=5,
                                              cooldown_pulses=1, lull_pulses=3)
            s = FakeSession()
            await engine.on_connect(s)                     # 1 event, below the floor
            await director.tick(1.0); await _drain(engine)  # pulses_since_beat -> 2
            self.assertEqual(canned.calls, 0)               # not yet the lull window
            await director.tick(1.0); await _drain(engine)  # -> 3 >= lull -> a beat
            self.assertEqual(canned.calls, 1)
            self.assertIn(STAGE_LINE, s.texts())            # a real gentle beat landed
        asyncio.run(go())

    def test_a_lull_beat_asks_for_a_gentle_touch(self):
        async def go():
            engine, director, canned = _build(period=1, min_new_events=5,
                                              cooldown_pulses=1, lull_pulses=2)
            s = FakeSession()
            await engine.on_connect(s)
            await director.tick(1.0); await _drain(engine)  # warmed 1 -> 2 -> lull beat
            self.assertEqual(canned.calls, 1)
            nudge = " ".join(m["content"] for m in canned.messages[-1])
            self.assertIn("gone quiet", nudge)              # the gentler lull nudge
        asyncio.run(go())

    def test_activity_path_still_fires_before_the_lull_window(self):
        async def go():
            # Lull set high; enough events -> the activity path fires at once, and
            # its nudge is the ordinary one, not the lull's.
            engine, director, canned = _build(period=1, min_new_events=1,
                                              cooldown_pulses=1, lull_pulses=99)
            s = FakeSession()
            await engine.on_connect(s)                      # 1 event >= floor(1)
            await director.tick(1.0); await _drain(engine)
            self.assertEqual(canned.calls, 1)
            nudge = " ".join(m["content"] for m in canned.messages[-1])
            self.assertNotIn("gone quiet", nudge)
        asyncio.run(go())

    def test_lull_still_needs_an_audience(self):
        async def go():
            # No player present: the lull never fires, however long it stays quiet.
            engine, director, canned = _build(period=1, min_new_events=5,
                                              cooldown_pulses=1, lull_pulses=2)
            for _ in range(6):
                await director.tick(1.0); await _drain(engine)
            self.assertEqual(canned.calls, 0)
        asyncio.run(go())


class DirectorForeshadowTests(unittest.TestCase):
    """B9 off-screen staging: the foreshadow flag surfaces the empty rooms just
    ahead of the players and tells the director it may shape them; off by default,
    so the prompt and snapshot are unchanged when it is not enabled."""

    def test_off_by_default(self):
        engine, director, canned = _build()
        self.assertFalse(director.foreshadow)

    def test_default_prompt_has_no_foreshadow_guidance(self):
        mind = _mind(WATCH)
        asyncio.run(mind.observe("- x", "- room (Room): Wren"))
        system = mind.provider.system_prompts[-1]
        self.assertIn("only places with someone present", system)
        self.assertNotIn("[ahead, empty]", system)

    def test_foreshadow_prompt_invites_shaping_ahead(self):
        mind = _mind(WATCH)
        asyncio.run(mind.observe(
            "- x", "- room (Room): Wren\n- grove (Grove) [ahead, empty]",
            foreshadow=True))
        system = mind.provider.system_prompts[-1]
        self.assertIn("[ahead, empty]", system)          # the guidance references it
        self.assertIn("foreshadow", system.lower())
        self.assertNotIn("only places with someone present", system)

    def test_foreshadow_director_reads_the_room_ahead(self):
        # A two-room world, a player in one; with foreshadow on, the director's beat
        # sees the empty room ahead in the snapshot handed to its mind.
        async def go():
            world = World()
            world.add_location(Location(id="here", name="Here", description="x",
                                        exits={"north": "there"}))
            world.add_location(Location(id="there", name="There", description="y",
                                        exits={"south": "here"}))
            engine = Engine(world, FakeProvider(), start_location="here")
            canned = CannedProvider(WATCH)
            director = engine.attach_director(FakeLoop(), provider=canned,
                                              period_ticks=1, min_new_events=1,
                                              cooldown_pulses=1, foreshadow=True)
            s = FakeSession()
            await engine.on_connect(s)                    # a player stands in "here"
            await director.tick(1.0)
            await _drain(engine)
            self.assertGreaterEqual(canned.calls, 1)
            self.assertIn("there (There) [ahead, empty]", canned.system_prompts[-1])
        asyncio.run(go())


class EngineChronicleTests(unittest.TestCase):
    """The engine records the salient beats the director reads."""

    def test_records_player_arrival_and_speech(self):
        async def go():
            engine, _, _ = _build(period=99)               # director dormant here
            s = FakeSession()
            await engine.on_connect(s)
            self.assertTrue(any(e.kind == "arrival"
                                for e in engine.chronicle.recent()))
            await engine._say(s, engine.players[s.id], "is anyone there")
            self.assertTrue(any("is anyone there" in e.text
                                for e in engine.chronicle.recent()))
        asyncio.run(go())

    def test_records_an_action_beat(self):
        async def go():
            engine, director, _ = _build(period=1)
            s = FakeSession()
            await engine.on_connect(s)
            await director.tick(1.0)
            await _drain(engine)
            # the director's own staged beat is itself chronicled (kind=action)
            self.assertTrue(any(STAGE_LINE in e.text
                                for e in engine.chronicle.recent()))
        asyncio.run(go())


class DirectorConditionIntegrationTests(unittest.TestCase):
    """The world-shaping action end-to-end through the real engine path
    (_perform -> registry -> world): the condition persists, its onset is
    announced to the room, the beat is chronicled, and a later look still shows it."""

    def test_set_condition_persists_announces_and_chronicles(self):
        async def go():
            engine, director, _ = _build(period=1, reply=SET_STORM)
            s = FakeSession()
            await engine.on_connect(s)
            s.sent.clear()                         # drop the connect/look output
            await director.tick(1.0)
            await _drain(engine)
            # Stored as a standing condition on the world registry…
            self.assertEqual(engine.world.conditions.texts("room"), [STORM_LINE])
            # …announced once to the room the player is in…
            self.assertIn(STORM_LINE, s.texts())
            # …and recorded to the chronicle the director itself reads.
            self.assertTrue(any(STORM_LINE in e.text
                                for e in engine.chronicle.recent()))
            # …and it persists: a fresh look still shows it, after the announcement.
            s.sent.clear()
            await engine._look(s)
            self.assertIn(STORM_LINE, s.texts())
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
