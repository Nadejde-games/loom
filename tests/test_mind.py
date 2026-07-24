"""The mind's turn pipeline: tolerant JSON parsing, validation, and the one
bounded retry — proven with scripted providers, no network, no GPU.
"""
import asyncio
import unittest

from loom.world.entity import Npc
from loom.action import default_registry
from loom.ai import FakeProvider
from loom.ai.mind import NpcMind, Scene, DECIDE_TEMPERATURE


def run(coro):
    return asyncio.run(coro)


class ScriptedProvider:
    """Returns queued replies in order; records what it was asked."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.schemas = []        # the constraint schema passed on each call (or None)
        self.temperatures = []   # the per-call temperature (act-gate spy)

    async def complete(self, system, messages, schema=None, temperature=None):
        self.calls.append((system, messages))
        self.schemas.append(schema)
        self.temperatures.append(temperature)
        return self.replies.pop(0)


def mind(provider):
    npc = Npc(id="odd", name="Odd", persona={"voice": "terse"})
    return NpcMind(npc, provider, registry=default_registry())


class ParseTests(unittest.TestCase):
    def test_plain_json(self):
        turn = run(mind(ScriptedProvider(
            ['{"speech":"Hi.","actions":[]}'])).converse("W", "hello"))
        self.assertEqual(turn.speech, "Hi.")
        self.assertEqual(turn.actions, [])

    def test_fenced_json_with_action(self):
        reply = ('```json\n{"speech":"Hi.","actions":'
                 '[{"name":"emote","args":{"text":"nods"}}]}\n```')
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertEqual(turn.speech, "Hi.")
        self.assertEqual([a.name for a in turn.actions], ["emote"])
        self.assertEqual(turn.actions[0].args, {"text": "nods"})

    def test_prose_wrapped_json(self):
        reply = 'Sure! {"speech":"Hi.","actions":[]} hope that helps'
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertEqual(turn.speech, "Hi.")

    def test_total_garbage_degrades_to_speech(self):
        reply = "just a plain sentence, no json here"
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertEqual(turn.speech, reply)
        self.assertEqual(turn.actions, [])

    def test_trailing_extra_brace(self):
        # The exact malformed shape Qwen3.5 emitted live: one stray '}' appended.
        reply = ('{"speech":"Careful.","actions":'
                 '[{"name":"emote","args":{"text":"nods"}}]}}')
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertEqual(turn.speech, "Careful.")
        self.assertEqual([a.name for a in turn.actions], ["emote"])

    def test_unclosed_object_is_recovered(self):
        # The B6 shape: a missing closing brace. The old hand-rolled extractor
        # (raw_decode / rfind '}') could not recover this and leaked the raw text
        # as speech; json_repair reconstructs the envelope, speech and action intact.
        reply = ('{"speech":"Hi.","actions":'
                 '[{"name":"emote","args":{"text":"nods"}]}')
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertEqual(turn.speech, "Hi.")
        self.assertEqual([a.name for a in turn.actions], ["emote"])

    def test_early_closed_object_with_trailing_actions(self):
        # The shape a weak (4b) model emitted live: the speech object closed early with a
        # stray '"}', then the actions trailed OUTSIDE it. json_repair returns a LIST of the
        # two top-level values; the extractor must recover the envelope (speech + actions),
        # not leak the whole raw JSON as spoken text.
        reply = ('{"speech": "I\'d be delighted to show you,"}", '
                 '"actions": [{"name": "emote", "args": {"text": "beams"}}]}')
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertEqual(turn.speech, "I'd be delighted to show you,")
        self.assertEqual([a.name for a in turn.actions], ["emote"])

    def test_json_looking_garbage_retries_never_leaks(self):
        # A reply that still looks like an envelope but cannot be extracted must NOT be
        # spoken raw — it errors, the one bounded retry fires, and the clean retry wins.
        turn = run(mind(ScriptedProvider(
            ["{", '{"speech":"There you are.","actions":[]}'])).converse("W", "hello"))
        self.assertEqual(turn.speech, "There you are.")     # the retry's line, not "{"

    def test_actions_capped(self):
        many = ",".join(['{"name":"emote","args":{"text":"nods"}}'] * 5)
        reply = '{"speech":"Hi.","actions":[' + many + ']}'
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertEqual(len(turn.actions), 3)   # MAX_ACTIONS


class RetryTests(unittest.TestCase):
    def test_invalid_then_valid_recovers(self):
        bad = '{"speech":"Hm.","actions":[{"name":"teleport","args":{}}]}'
        good = '{"speech":"Hm.","actions":[{"name":"emote","args":{"text":"nods"}}]}'
        p = ScriptedProvider([bad, good])
        turn = run(mind(p).converse("W", "hello"))
        self.assertEqual(len(p.replies), 0)                 # it retried
        self.assertEqual([a.name for a in turn.actions], ["emote"])

    def test_correction_names_the_error(self):
        bad = '{"speech":"Hm.","actions":[{"name":"teleport","args":{}}]}'
        good = '{"speech":"Hm.","actions":[]}'
        p = ScriptedProvider([bad, good])
        run(mind(p).converse("W", "hello"))
        # Second call carries the correction with the invalid action's name.
        _, retry_messages = p.calls[1]
        correction = retry_messages[-1]["content"]
        self.assertIn("teleport", correction)

    def test_invalid_twice_drops_action_keeps_speech(self):
        bad = '{"speech":"Hm.","actions":[{"name":"teleport","args":{}}]}'
        turn = run(mind(ScriptedProvider([bad, bad])).converse("W", "hello"))
        self.assertEqual(turn.speech, "Hm.")
        self.assertEqual(turn.actions, [])

    def test_list_args_are_invalid_and_dropped(self):
        # The other live shape: args as a bare list instead of a named object.
        bad = '{"speech":"Hm.","actions":[{"name":"emote","args":["nods"]}]}'
        turn = run(mind(ScriptedProvider([bad, bad])).converse("W", "hello"))
        self.assertEqual(turn.speech, "Hm.")
        self.assertEqual(turn.actions, [])

    def test_valid_turn_does_not_retry(self):
        good = '{"speech":"Hi.","actions":[{"name":"emote","args":{"text":"nods"}}]}'
        p = ScriptedProvider([good, "unused"])
        run(mind(p).converse("W", "hello"))
        self.assertEqual(len(p.calls), 1)                   # no retry consumed


class FakeProviderTests(unittest.TestCase):
    def test_emits_envelope_with_emote(self):
        turn = run(mind(FakeProvider()).converse("W", "let's dance"))
        self.assertTrue(turn.speech)
        self.assertEqual([a.name for a in turn.actions], ["emote"])

    def test_speech_only_when_no_trigger(self):
        turn = run(mind(FakeProvider()).converse("W", "hello there"))
        self.assertTrue(turn.speech)
        self.assertEqual(turn.actions, [])


class SilenceTests(unittest.TestCase):
    def test_empty_envelope_is_silent(self):
        turn = run(mind(ScriptedProvider(
            ['{"speech":"","actions":[]}'])).converse("W", "hello"))
        self.assertTrue(turn.is_silent)
        self.assertEqual(turn.speech, "")
        self.assertEqual(turn.actions, [])

    def test_speech_is_not_silent(self):
        turn = run(mind(ScriptedProvider(
            ['{"speech":"Hm.","actions":[]}'])).converse("W", "hello"))
        self.assertFalse(turn.is_silent)

    def test_action_only_is_not_silent(self):
        reply = '{"speech":"","actions":[{"name":"emote","args":{"text":"nods"}}]}'
        turn = run(mind(ScriptedProvider([reply])).converse("W", "hello"))
        self.assertFalse(turn.is_silent)

    def test_prompt_offers_silence(self):
        prompt = mind(FakeProvider())._system_prompt()
        self.assertIn('"speech": "", "actions": []', prompt)

    def test_prompt_has_a_silent_example(self):
        # Lever 1: a modeled empty-turn example, not just prose permission.
        prompt = mind(FakeProvider())._system_prompt()
        self.assertIn("Example (choosing silence", prompt)

    def test_disposition_renders(self):
        # Lever 4: a persona disposition line (the silence prior).
        npc = Npc(id="odd", name="Odd", persona={"disposition": "deeply reticent"})
        prompt = NpcMind(npc, FakeProvider(),
                         registry=default_registry())._system_prompt()
        self.assertIn("Disposition: deeply reticent", prompt)

    def test_addressed_vs_overheard_framing(self):
        # Lever 2: addressed -> "says to you"; unaddressed -> "overhear ... room".
        p1 = ScriptedProvider(['{"speech":"Hi.","actions":[]}'])
        run(mind(p1).converse("W", "hello", addressed=True))
        self.assertIn("says to you", p1.calls[0][1][0]["content"])

        p2 = ScriptedProvider(['{"speech":"","actions":[]}'])
        run(mind(p2).converse("W", "hello", addressed=False))
        content = p2.calls[0][1][0]["content"]
        self.assertIn("overhear", content)
        self.assertIn("to the room", content)


class SceneTests(unittest.TestCase):
    def test_scene_renders_into_prompt(self):
        m = mind(FakeProvider())
        prompt = m._system_prompt(Scene(location="Room A",
                                        description="A bare room.",
                                        exits=["north", "down"],
                                        others=["Wanderer-1"]))
        self.assertIn("Room A", prompt)
        self.assertIn("Exits you can take: north, down", prompt)
        self.assertIn("Also here with you: Wanderer-1", prompt)

    def test_scene_renders_items_it_can_see(self):
        m = mind(FakeProvider())
        prompt = m._system_prompt(Scene(location="Room A",
                                        items=["a rusty lantern", "a brass key"]))
        self.assertIn("a rusty lantern, a brass key", prompt)
        self.assertIn("On the ground here", prompt)

    def test_scene_renders_own_inventory(self):
        m = mind(FakeProvider())
        prompt = m._system_prompt(Scene(location="Room A",
                                        inventory=["a worn hill-map"]))
        self.assertIn("You are carrying: a worn hill-map", prompt)

    def test_no_exits_is_stated(self):
        m = mind(FakeProvider())
        prompt = m._system_prompt(Scene(location="Cell", exits=[]))
        self.assertIn("no exits you can take", prompt)

    def test_perceived_exit_drives_move_action(self):
        # With a real exit in view and a departure cue, the fake proposes move
        # bound to that exit — the perception → action path, offline.
        turn = run(mind(FakeProvider()).converse(
            "W", "please leave", scene=Scene(location="Room A", exits=["north"])))
        self.assertEqual([a.name for a in turn.actions], ["move"])
        self.assertEqual(turn.actions[0].args, {"direction": "north"})

    def test_no_scene_stays_backward_compatible(self):
        turn = run(mind(FakeProvider()).converse("W", "hello there"))
        self.assertTrue(turn.speech)
        self.assertEqual(turn.actions, [])


class NoRegistryTests(unittest.TestCase):
    def test_plain_text_provider_without_registry(self):
        npc = Npc(id="odd", name="Odd", persona={})
        m = NpcMind(npc, FakeProvider())            # no registry -> prose mode
        turn = run(m.converse("W", "hello there"))
        self.assertTrue(turn.speech)
        self.assertEqual(turn.actions, [])

    def test_hear_and_respond_returns_speech(self):
        reply = run(mind(FakeProvider()).hear_and_respond("W", "hello there"))
        self.assertIsInstance(reply, str)
        self.assertTrue(reply)


class ConstraintTests(unittest.TestCase):
    """Constrained decoding wiring: the mind hands the provider the turn-envelope
    grammar when actions are in play, and withholds it when there is no registry.
    The grammar's *content* is proven in test_constrained_decoding."""

    def test_schema_forwarded_when_registry_present(self):
        p = ScriptedProvider(['{"speech":"Hi.","actions":[]}'])
        run(mind(p).converse("W", "hello"))
        self.assertEqual(p.schemas[0], default_registry().json_schema())

    def test_no_schema_without_registry(self):
        npc = Npc(id="odd", name="Odd", persona={})
        p = ScriptedProvider(["Just a plain spoken line."])
        run(NpcMind(npc, p).converse("W", "hello"))   # registry=None -> prose mode
        self.assertIsNone(p.schemas[0])

    def test_retry_also_carries_the_schema(self):
        bad = '{"speech":"Hm.","actions":[{"name":"teleport","args":{}}]}'
        good = '{"speech":"Hm.","actions":[]}'
        p = ScriptedProvider([bad, good])
        run(mind(p).converse("W", "hello"))
        self.assertEqual(len(p.schemas), 2)                 # it retried
        self.assertEqual(p.schemas[1], default_registry().json_schema())


class OfferedSubsetTests(unittest.TestCase):
    """A mind offered a subset of the registry is told about, and constrained to,
    exactly that subset — the mechanism that keeps NPCs off the player-only verbs."""

    def _mind(self, provider, offered):
        npc = Npc(id="odd", name="Odd", persona={})
        return NpcMind(npc, provider, registry=default_registry(), offered=offered)

    def test_prompt_only_offers_the_subset(self):
        prompt = self._mind(FakeProvider(), ["emote", "move"])._system_prompt()
        self.assertIn("emote(", prompt)
        self.assertIn("move(", prompt)
        self.assertNotIn("give_item(", prompt)
        self.assertNotIn("take_item(", prompt)

    def test_forwarded_schema_is_the_subset(self):
        p = ScriptedProvider(['{"speech":"Hi.","actions":[]}'])
        run(self._mind(p, ["emote"]).converse("W", "hello"))
        self.assertEqual(p.schemas[0], default_registry().json_schema(["emote"]))


class ActGateTests(unittest.TestCase):
    """B5 two-pass act-gate (opt-in): a cheap low-temp pass decides the action
    authoritatively, while the proven blended turn supplies the speech (its own
    action discarded). The blended single-pass path (every test above) is unchanged;
    this proves the split path preserves speech behaviour and raises the deed."""

    D_MOVE = '{"speech":"","actions":[{"name":"move","args":{"direction":"north"}}]}'
    D_NONE = '{"speech":"","actions":[]}'

    def _gated(self, provider):
        npc = Npc(id="odd", name="Odd", persona={"voice": "terse"})
        return NpcMind(npc, provider, registry=default_registry(), act_gate=True)

    def test_decision_action_wins_blended_speech_is_kept(self):
        # The decision picks move; the blended turn's OWN action (an emote) is
        # discarded, but its spoken line is kept — the decision is authoritative.
        blended = ('{"speech":"This way — follow me.","actions":'
                   '[{"name":"emote","args":{"text":"grins"}}]}')
        p = ScriptedProvider([self.D_MOVE, blended])
        turn = run(self._gated(p).converse("W", "lead me north"))
        self.assertEqual(len(p.calls), 2)                    # decide, then blended
        self.assertEqual([a.name for a in turn.actions], ["move"])   # from the decision
        self.assertEqual(turn.actions[0].args, {"direction": "north"})
        self.assertEqual(turn.speech, "This way — follow me.")       # from the blended turn

    def test_decision_is_low_temp_blended_is_default_both_constrained(self):
        p = ScriptedProvider([self.D_MOVE, '{"speech":"Onward.","actions":[]}'])
        run(self._gated(p).converse("W", "lead me north"))
        schema = default_registry().json_schema()
        self.assertEqual(p.temperatures[0], DECIDE_TEMPERATURE)   # decision: low temp
        self.assertEqual(p.schemas[0], schema)                    # decision constrained
        self.assertIsNone(p.temperatures[1])                 # blended: default temp
        self.assertEqual(p.schemas[1], schema)               # blended still constrained

    def test_decision_pass_is_deed_focused_blended_keeps_silence_lever(self):
        p = ScriptedProvider([self.D_MOVE, '{"speech":"Onward.","actions":[]}'])
        run(self._gated(p).converse("W", "lead me north"))
        decision_system, blended_system = p.calls[0][0], p.calls[1][0]
        self.assertIn("Decide only what you DO", decision_system)  # pass 1: the deed
        self.assertIn("Available actions", decision_system)        # with the catalogue
        # pass 2 is the ordinary blended turn, with its B4 silence machinery intact.
        self.assertIn("Available actions", blended_system)
        self.assertIn("Example (choosing silence", blended_system)

    def test_silence_when_no_action_and_blended_stays_quiet(self):
        p = ScriptedProvider([self.D_NONE, '{"speech":"","actions":[]}'])
        turn = run(self._gated(p).converse("W", "what a grey sky", addressed=False))
        self.assertTrue(turn.is_silent)
        self.assertEqual(turn.actions, [])

    def test_gate_off_stays_single_pass(self):
        good = '{"speech":"Hi.","actions":[{"name":"emote","args":{"text":"nods"}}]}'
        p = ScriptedProvider([good, "unused"])
        turn = run(mind(p).converse("W", "hello"))           # mind() = gate off
        self.assertEqual(len(p.calls), 1)                    # one blended call
        self.assertEqual(turn.speech, "Hi.")
        self.assertEqual([a.name for a in turn.actions], ["emote"])

    def test_gated_with_fake_yields_move_and_speech(self):
        # End-to-end shape offline: the decision picks move (departure cue + a real
        # exit), the blended turn speaks — a guaranteed move plus a line.
        turn = run(self._gated(FakeProvider()).converse(
            "W", "please leave", scene=Scene(location="A", exits=["north"])))
        self.assertEqual([a.name for a in turn.actions], ["move"])
        self.assertTrue(turn.speech)


if __name__ == "__main__":
    unittest.main()
