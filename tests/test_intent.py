"""The free-text intent parser (B1b). Offline: a scripted provider stands in for
the model, so we prove the wiring (constrained call -> tolerant parse -> a
canonical command tuple) without a GPU. The model's *behavior* — does it map
sensible phrasings onto the right verb — is the live harness's job.
"""
import asyncio
import unittest

from loom.command import command_schema, describe_verbs, default_verbs
from loom.ai.intent import interpret


def run(coro):
    return asyncio.run(coro)


class ScriptedProvider:
    """Returns a fixed reply; records the schema it was constrained with."""
    def __init__(self, reply):
        self.reply = reply
        self.schemas = []

    async def complete(self, system, messages, schema=None):
        self.schemas.append(schema)
        return self.reply


SCHEMA = command_schema(default_verbs())
CATALOGUE = describe_verbs(default_verbs())
CONTEXT = "Here with you: Wren\nOn the ground: a rusty lantern"


def _interpret(reply, text="do the thing"):
    p = ScriptedProvider(reply)
    return run(interpret(p, SCHEMA, CATALOGUE, CONTEXT, text)), p


class InterpretTests(unittest.TestCase):
    def test_maps_a_full_command(self):
        res, p = _interpret('{"command":{"verb":"give","dobj":"map","iobj":"Wren"}}')
        self.assertEqual(res, ("give", "map", "Wren"))
        self.assertIs(p.schemas[0], SCHEMA)          # the constraint was applied

    def test_missing_objects_become_empty_strings(self):
        res, _ = _interpret('{"command":{"verb":"look"}}')
        self.assertEqual(res, ("look", "", ""))

    def test_tolerates_unwrapped_command(self):
        res, _ = _interpret('{"verb":"take","dobj":"lantern"}')
        self.assertEqual(res, ("take", "lantern", ""))

    def test_non_json_returns_none(self):
        # An unconstrained backend that just chatted (or the FakeProvider).
        res, _ = _interpret("I think you should look around first.")
        self.assertIsNone(res)

    def test_missing_verb_returns_none(self):
        res, _ = _interpret('{"command":{"dobj":"lantern"}}')
        self.assertIsNone(res)

    def test_strips_whitespace_on_objects(self):
        res, _ = _interpret('{"command":{"verb":"take","dobj":"  lantern  "}}')
        self.assertEqual(res, ("take", "lantern", ""))


if __name__ == "__main__":
    unittest.main()
