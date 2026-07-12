"""Constrained decoding (Phase 2 hardening): the registry emits a turn-envelope
JSON Schema that grammar-constrains generation, and the provider forwards it.

These are the offline guarantees. The token-level *effect* — that a live model
cannot emit a malformed envelope — is a behavioral claim, proven by the live
harness, not here. What this file proves is mechanical: the emitted shape is
correct, it cannot drift from what ``validate()`` enforces (both read the same
``Param`` specs), and the provider puts the constraint on the wire only when asked.
"""
import asyncio
import re
import unittest

from loom.action import ActionRegistry, ActionSpec, Param, default_registry
from loom.ai.provider import OpenAICompatibleProvider


def run(coro):
    return asyncio.run(coro)


def _branches(schema):
    """The per-action oneOf branches of a turn schema, keyed by their name const."""
    items = schema["properties"]["actions"]["items"]
    return {b["properties"]["name"]["const"]: b for b in items["oneOf"]}


class EnvelopeShapeTests(unittest.TestCase):
    def setUp(self):
        self.schema = default_registry().json_schema()

    def test_top_level_is_a_closed_object(self):
        s = self.schema
        self.assertEqual(s["type"], "object")
        self.assertFalse(s["additionalProperties"])
        self.assertEqual(set(s["required"]), {"speech", "actions"})

    def test_speech_is_a_string(self):
        self.assertEqual(self.schema["properties"]["speech"], {"type": "string"})

    def test_actions_is_an_array_of_oneof_branches(self):
        actions = self.schema["properties"]["actions"]
        self.assertEqual(actions["type"], "array")
        self.assertIn("oneOf", actions["items"])

    def test_one_branch_per_registered_action(self):
        reg = default_registry()
        self.assertEqual(set(_branches(self.schema)), set(reg.names()))

    def test_branch_is_a_closed_object_with_name_const_and_args(self):
        emote = _branches(self.schema)["emote"]
        self.assertEqual(emote["type"], "object")
        self.assertFalse(emote["additionalProperties"])
        self.assertEqual(set(emote["required"]), {"name", "args"})
        self.assertEqual(emote["properties"]["name"], {"const": "emote"})
        args = emote["properties"]["args"]
        self.assertEqual(args["type"], "object")
        self.assertFalse(args["additionalProperties"])
        self.assertEqual(args["properties"]["text"], {"type": "string"})
        self.assertEqual(args["required"], ["text"])


class TypeMappingTests(unittest.TestCase):
    """Every Param type maps to the right JSON Schema fragment, and optional
    params are absent from ``required``."""

    def _schema(self):
        reg = ActionRegistry()
        reg.register(ActionSpec(
            name="cast", description="a spell",
            params={
                "spell": Param("enum", required=True, choices=("fire", "ice")),
                "power": Param("int", required=True),
                "ratio": Param("float", required=False),
                "loud": Param("bool", required=False),
            },
            handler=lambda ctx: None,
        ))
        return reg.json_schema()

    def test_types(self):
        args = _branches(self._schema())["cast"]["properties"]["args"]
        props = args["properties"]
        self.assertEqual(props["spell"], {"enum": ["fire", "ice"]})
        self.assertEqual(props["power"], {"type": "integer"})
        self.assertEqual(props["ratio"], {"type": "number"})
        self.assertEqual(props["loud"], {"type": "boolean"})

    def test_only_required_params_are_required(self):
        args = _branches(self._schema())["cast"]["properties"]["args"]
        self.assertEqual(set(args["required"]), {"spell", "power"})

    def test_empty_registry_forbids_any_action(self):
        actions = ActionRegistry().json_schema()["properties"]["actions"]
        self.assertEqual(actions, {"type": "array", "maxItems": 0})


class NoDriftTests(unittest.TestCase):
    """The emitter and ``validate()`` are two renderings of one source (the
    registered ``Param`` specs). These assert they agree, so the shape the model
    is *forced* to emit is exactly the shape the engine *checks* — no drift.
    """
    _MISSING = re.compile(r'missing required arg "([^"]+)"')

    def test_schema_required_equals_validate_required(self):
        reg = default_registry()
        branches = _branches(reg.json_schema())
        for name in reg.names():
            with self.subTest(action=name):
                schema_required = set(
                    branches[name]["properties"]["args"].get("required", []))
                # What validate() demands when handed no args at all.
                errs = reg.validate(name, {})
                validate_required = {m.group(1)
                                     for e in errs for m in [self._MISSING.search(e)]
                                     if m}
                self.assertEqual(schema_required, validate_required)

    def test_schema_props_equal_registered_params(self):
        reg = default_registry()
        branches = _branches(reg.json_schema())
        for name in reg.names():
            with self.subTest(action=name):
                spec = reg.get(name)
                props = branches[name]["properties"]["args"]["properties"]
                self.assertEqual(set(props), set(spec.params))


class CapturingProvider(OpenAICompatibleProvider):
    """An OpenAI-compatible provider whose network POST is captured, not sent —
    so we can prove exactly what the constraint puts on the wire."""
    def __init__(self):
        super().__init__(base_url="http://unused/v1", model="m")
        self.captured = None

    def _post(self, payload):
        self.captured = payload
        return {"choices": [{"message": {"content": '{"speech":"","actions":[]}'}}]}


class ProviderForwardingTests(unittest.TestCase):
    def test_schema_becomes_response_format(self):
        cp = CapturingProvider()
        schema = {"type": "object"}
        run(cp.complete("sys", [{"role": "user", "content": "hi"}], schema=schema))
        rf = cp.captured["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertIs(rf["json_schema"]["schema"], schema)

    def test_no_schema_no_response_format(self):
        cp = CapturingProvider()
        run(cp.complete("sys", [{"role": "user", "content": "hi"}]))
        self.assertNotIn("response_format", cp.captured)


if __name__ == "__main__":
    unittest.main()
