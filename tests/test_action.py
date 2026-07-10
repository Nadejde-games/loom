"""The action registry: schema validation and the built-in emote handler.
Pure, offline, no provider — this is the safety layer the golden rule rests on.
"""
import unittest

from loom.action import (
    ActionRegistry, ActionSpec, ActionContext, Param, default_registry,
)


class _Actor:
    name = "Odd"
    id = "odd"


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.reg = default_registry()

    def test_valid_emote(self):
        self.assertEqual(self.reg.validate("emote", {"text": "nods"}), [])

    def test_unknown_action(self):
        errs = self.reg.validate("teleport", {"text": "x"})
        self.assertTrue(errs and "unknown action" in errs[0])

    def test_missing_required(self):
        errs = self.reg.validate("emote", {})
        self.assertTrue(any("missing required" in e for e in errs))

    def test_unknown_arg(self):
        errs = self.reg.validate("emote", {"text": "nods", "loudness": 11})
        self.assertTrue(any('unknown arg "loudness"' in e for e in errs))

    def test_wrong_type(self):
        errs = self.reg.validate("emote", {"text": 5})
        self.assertTrue(any("must be str" in e for e in errs))

    def test_args_not_object(self):
        self.assertTrue(self.reg.validate("emote", ["nods"]))

    def test_non_string_name(self):
        self.assertTrue(self.reg.validate(None, {"text": "x"}))


class TypeSystemTests(unittest.TestCase):
    def _reg(self, param):
        r = ActionRegistry()
        r.register(ActionSpec("act", "test action", {"v": param}, lambda ctx: None))
        return r

    def test_int_rejects_bool(self):
        self.assertTrue(self._reg(Param("int")).validate("act", {"v": True}))

    def test_int_accepts_int(self):
        self.assertEqual(self._reg(Param("int")).validate("act", {"v": 3}), [])

    def test_enum_ok_and_bad(self):
        r = self._reg(Param("enum", choices=("left", "right")))
        self.assertEqual(r.validate("act", {"v": "left"}), [])
        self.assertTrue(r.validate("act", {"v": "up"}))

    def test_optional_arg_may_be_absent(self):
        self.assertEqual(self._reg(Param("str", required=False)).validate("act", {}), [])


class RegistryTests(unittest.TestCase):
    def test_describe_lists_emote(self):
        text = default_registry().describe()
        self.assertIn("emote(", text)
        self.assertIn("text: str", text)

    def test_membership_and_names(self):
        reg = default_registry()
        self.assertIn("emote", reg)
        self.assertEqual(reg.names(), ["emote"])


class EmoteHandlerTests(unittest.TestCase):
    def test_narration_and_memory(self):
        spec = default_registry().get("emote")
        res = spec.handler(ActionContext(world=None, actor=_Actor(),
                                         args={"text": "nods slowly"}))
        self.assertEqual(res.narration, "Odd nods slowly")
        self.assertEqual(res.actor_memory, "I nods slowly.")


if __name__ == "__main__":
    unittest.main()
