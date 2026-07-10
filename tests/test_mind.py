"""The mind's turn pipeline: tolerant JSON parsing, validation, and the one
bounded retry — proven with scripted providers, no network, no GPU.
"""
import asyncio
import unittest

from loom.world.entity import Npc
from loom.action import default_registry
from loom.ai import FakeProvider
from loom.ai.mind import NpcMind


def run(coro):
    return asyncio.run(coro)


class ScriptedProvider:
    """Returns queued replies in order; records what it was asked."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, system, messages):
        self.calls.append((system, messages))
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


if __name__ == "__main__":
    unittest.main()
