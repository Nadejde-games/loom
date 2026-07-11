"""The salience gate (B4): directed address decides who engages, cheaply and
deterministically, before any LLM call. Pure, offline, no provider.
"""
import unittest

from loom.salience import (
    SalienceGate, SalienceContext, default_gate, is_addressed,
)
from loom.world.entity import Npc


ODD = Npc(id="odd", name="Odd the Hermit", persona={})
WREN = Npc(id="wren", name="Wren the Wayfinder", persona={})
PRESENT = [ODD.name, WREN.name]


def engages(npc, utterance, present=PRESENT):
    return default_gate().should_engage(
        SalienceContext(npc=npc, speaker_name="Wanderer",
                        utterance=utterance, present_npcs=present))


class DirectedAddressTests(unittest.TestCase):
    def test_named_npc_engages_bystander_stays_out(self):
        self.assertTrue(engages(WREN, "Wren, show me the hills"))
        self.assertFalse(engages(ODD, "Wren, show me the hills"))

    def test_full_name_addresses(self):
        self.assertTrue(engages(WREN, "Wren the Wayfinder, lead on"))
        self.assertFalse(engages(ODD, "Wren the Wayfinder, lead on"))

    def test_address_is_case_insensitive(self):
        self.assertTrue(engages(WREN, "hey WREN, over here"))
        self.assertFalse(engages(ODD, "hey WREN, over here"))

    def test_addressing_the_other_by_first_name(self):
        # "Odd" names the hermit; the guide stays out.
        self.assertTrue(engages(ODD, "Odd, are you there?"))
        self.assertFalse(engages(WREN, "Odd, are you there?"))


class UndirectedTests(unittest.TestCase):
    def test_no_name_lets_everyone_decide(self):
        # Nobody named: the gate defers; both are let through to choose in-character.
        self.assertTrue(engages(ODD, "hello, is anyone here?"))
        self.assertTrue(engages(WREN, "hello, is anyone here?"))

    def test_sole_npc_engages(self):
        self.assertTrue(engages(ODD, "hello there", present=[ODD.name]))

    def test_naming_someone_not_present_is_not_directed(self):
        # "Bramble" is not in the room: not a directed address, so all engage.
        self.assertTrue(engages(ODD, "have you seen Bramble?"))
        self.assertTrue(engages(WREN, "have you seen Bramble?"))


class IsAddressedTests(unittest.TestCase):
    def test_named_is_addressed(self):
        self.assertTrue(is_addressed("Wren the Wayfinder", "Wren, over here"))
        self.assertTrue(is_addressed("Wren the Wayfinder", "hello wren"))

    def test_unnamed_is_not_addressed(self):
        self.assertFalse(is_addressed("Wren the Wayfinder", "hello, anyone?"))


class SwappableTests(unittest.TestCase):
    def test_custom_gate_overrides_policy(self):
        class MuteGate(SalienceGate):
            def should_engage(self, ctx):
                return False
        self.assertFalse(MuteGate().should_engage(
            SalienceContext(npc=WREN, speaker_name="W",
                            utterance="Wren!", present_npcs=PRESENT)))


if __name__ == "__main__":
    unittest.main()
