"""The mind's turn pipeline: tolerant JSON parsing, validation, and the one
bounded retry — proven with scripted providers, no network, no GPU.
"""
import asyncio
import unittest

from loom.world.entity import Npc
from loom.action import default_registry
from loom.ai import FakeProvider
from loom.ai.mind import NpcMind, Scene


def run(coro):
    return asyncio.run(coro)


class ScriptedProvider:
    """Returns queued replies in order; records what it was asked."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.schemas = []   # the constraint schema passed on each call (or None)

    async def complete(self, system, messages, schema=None):
        self.calls.append((system, messages))
        self.schemas.append(schema)
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


if __name__ == "__main__":
    unittest.main()
