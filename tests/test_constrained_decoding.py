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
from loom.ai.provider import (OpenAICompatibleProvider, VLLMProvider,
                              OllamaProvider, OpenRouterProvider)


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


class SchemaSubsetTests(unittest.TestCase):
    """json_schema(names) narrows the grammar to the same subset describe(names)
    shows — so an actor offered fewer actions is constrained to exactly those."""

    def test_narrows_to_named_actions(self):
        branches = _branches(default_registry().json_schema(["emote", "move"]))
        self.assertEqual(set(branches), {"emote", "move"})

    def test_none_lists_all(self):
        branches = _branches(default_registry().json_schema())
        self.assertEqual(set(branches),
                         {"emote", "move", "give_item", "take_item", "drop_item",
                          "stage_event", "set_condition", "clear_condition",
                          "spawn_item", "offer_quest"})


class CapturingProvider(OpenAICompatibleProvider):
    """An OpenAI-compatible provider whose network POST is captured, not sent —
    so we can prove exactly what the constraint puts on the wire."""
    def __init__(self):
        super().__init__(base_url="http://unused/v1", model="m")
        self.captured = None

    async def _post(self, payload):
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


class _CapturingVLLM(VLLMProvider):
    """A VLLMProvider whose POST is captured, not sent — to prove its config on
    the wire without a live server."""
    async def _post(self, payload):
        self.captured = payload
        return {"choices": [{"message": {"content": '{"speech":"","actions":[]}'}}]}


class VLLMProviderTests(unittest.TestCase):
    """The vLLM backend is a thin config over the OpenAI waist: thinking suppressed
    by default, constrained decoding inherited unchanged (vLLM honors the standard
    response_format the base emits), and auth optional."""

    def test_defaults_and_reasoning_off(self):
        p = _CapturingVLLM()
        self.assertEqual(p.name, "vllm:qwen-local")
        self.assertEqual(p.url, "http://localhost:8000/v1/chat/completions")
        run(p.complete("sys", [{"role": "user", "content": "hi"}]))
        # Thinking is suppressed by default (Qwen3.x reasons into content otherwise).
        self.assertEqual(p.captured["reasoning_effort"], "none")

    def test_think_true_omits_reasoning_effort(self):
        p = _CapturingVLLM(think=True)
        run(p.complete("sys", [{"role": "user", "content": "hi"}]))
        self.assertNotIn("reasoning_effort", p.captured)

    def test_inherits_standard_constrained_decoding(self):
        p = _CapturingVLLM()
        schema = {"type": "object"}
        run(p.complete("sys", [{"role": "user", "content": "hi"}], schema=schema))
        rf = p.captured["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertIs(rf["json_schema"]["schema"], schema)

    def test_no_auth_header_without_api_key(self):
        # A vLLM started without --api-key needs no token; none is sent by default.
        p = _CapturingVLLM()
        self.assertIsNone(p.api_key)

    def test_ollama_and_vllm_carry_distinct_names(self):
        self.assertEqual(OllamaProvider(model="m").name, "ollama:m")
        self.assertEqual(VLLMProvider(model="m").name, "vllm:m")


class _CapturingOpenRouter(OpenRouterProvider):
    """An OpenRouterProvider whose POST is captured, not sent — to prove its config
    on the wire (base_url, auth, reasoning lever, constraint) without a live call."""
    async def _post(self, payload):
        self.captured = payload
        return {"choices": [{"message": {"content": '{"speech":"","actions":[]}'}}]}


class OpenRouterProviderTests(unittest.TestCase):
    """The OpenRouter backend is a thin config over the OpenAI waist: the hosted
    ``/api/v1`` base, a Bearer key, thinking suppressed by OpenRouter's portable
    ``reasoning:{enabled:false}`` lever, and constrained decoding inherited unchanged."""

    def test_defaults_url_name_and_reasoning_off(self):
        p = _CapturingOpenRouter(api_key="k")
        self.assertEqual(p.name, "openrouter:qwen/qwen3.6-35b-a3b")
        self.assertEqual(p.url,
                         "https://openrouter.ai/api/v1/chat/completions")
        run(p.complete("sys", [{"role": "user", "content": "hi"}]))
        # Thinking suppressed by default via OpenRouter's reasoning toggle.
        self.assertEqual(p.captured["reasoning"], {"enabled": False})

    def test_think_true_omits_reasoning(self):
        p = _CapturingOpenRouter(api_key="k", think=True)
        run(p.complete("sys", [{"role": "user", "content": "hi"}]))
        self.assertNotIn("reasoning", p.captured)

    def test_inherits_standard_constrained_decoding(self):
        p = _CapturingOpenRouter(api_key="k")
        schema = {"type": "object"}
        run(p.complete("sys", [{"role": "user", "content": "hi"}], schema=schema))
        rf = p.captured["response_format"]
        self.assertEqual(rf["type"], "json_schema")
        self.assertTrue(rf["json_schema"]["strict"])
        self.assertIs(rf["json_schema"]["schema"], schema)

    def test_api_key_becomes_a_bearer_header(self):
        # A remote call must authenticate — the key rides as a Bearer token.
        p = _CapturingOpenRouter(api_key="sk-or-test")
        self.assertEqual(p.api_key, "sk-or-test")

    def test_backends_carry_distinct_names(self):
        self.assertEqual(OpenRouterProvider(model="x", api_key="k").name,
                         "openrouter:x")

    def test_throttle_401_is_retryable_but_real_auth_is_not(self):
        # OpenRouter disguises a burst rate-limit as a 401 with a cookie-auth message;
        # that is retried like a 429. A genuine bad-key 401 is not (it fails on call 1).
        p = OpenRouterProvider(model="m", api_key="k")
        self.assertTrue(p._is_retryable(
            401, '{"error":{"message":"No cookie auth credentials found"}}'))
        self.assertTrue(p._is_retryable(401, "No user or org id found in auth cookie"))
        self.assertTrue(p._is_retryable(429, "rate limited"))
        self.assertFalse(p._is_retryable(401, '{"error":{"message":"Invalid API key"}}'))
        self.assertFalse(p._is_retryable(400, "bad request"))
        self.assertEqual(p.retries, 5)   # widened default so a burst run rides throttle

    def test_pacing_is_idle_by_default_and_adapts_to_throttle(self):
        # Unique model name so the class-level pace state can't collide with others.
        p = OpenRouterProvider(model="pace-test-adapt", api_key="k")
        self.assertEqual(p._pace_state()["interval"], 0.0)   # idle -> no delay
        run(p._pace_wait())                                   # idle wait is a no-op
        p._pace_note(True)                                    # a throttle -> back off
        self.assertGreater(p._pace_state()["interval"], 0.0)
        p._pace_note(True)                                    # again -> larger, capped
        self.assertLessEqual(p._pace_state()["interval"], OpenRouterProvider._PACE_MAX)
        for _ in range(30):
            p._pace_note(False)                               # clean calls -> decay off
        self.assertEqual(p._pace_state()["interval"], 0.0)


if __name__ == "__main__":
    unittest.main()
