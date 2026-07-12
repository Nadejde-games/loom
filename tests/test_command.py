"""The player-command parser (B1). Pure syntax: these tests need no world, no
provider, no engine — just the verb table. That is the point of keeping the
parser world-free (it names scopes symbolically); resolution is tested at the
engine level in test_engine.
"""
import unittest

from loom import command
from loom.command import parse, default_verbs


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.verbs = default_verbs()

    def p(self, text):
        return parse(text, self.verbs)

    # --- verb canonicalisation & synonyms ---
    def test_synonyms_map_to_one_action(self):
        for surface in ("take", "get", "grab"):
            with self.subTest(surface=surface):
                r = self.p(f"{surface} lantern")
                self.assertEqual(r.verb.target, "take_item")
                self.assertEqual(r.dobj, "lantern")

    def test_give_synonym_hand(self):
        r = self.p("hand map to Wren")
        self.assertEqual(r.verb.target, "give_item")
        self.assertEqual(r.dobj, "map")
        self.assertEqual(r.iobj, "Wren")

    # --- multi-word verbs (matched greedily before single words) ---
    def test_pick_up_is_take(self):
        r = self.p("pick up the lantern")
        self.assertEqual(r.verb.target, "take_item")
        self.assertEqual(r.surface, "pick up")
        self.assertEqual(r.dobj, "lantern")       # article stripped

    def test_put_down_is_drop(self):
        r = self.p("put down lantern")
        self.assertEqual(r.verb.target, "drop_item")
        self.assertEqual(r.dobj, "lantern")

    def test_look_at_is_examine(self):
        r = self.p("look at the brass key")
        self.assertEqual(r.verb.target, "examine")
        self.assertEqual(r.dobj, "brass key")

    def test_bare_look_is_not_examine(self):
        r = self.p("look")
        self.assertEqual(r.verb.target, "look")
        self.assertEqual(r.dobj, "")

    # --- preposition split & articles ---
    def test_give_splits_on_to(self):
        r = self.p("give the brass key to the old man")
        self.assertEqual(r.dobj, "brass key")
        self.assertEqual(r.iobj, "old man")

    def test_take_splits_on_from(self):
        r = self.p("take map from Wren")
        self.assertEqual(r.verb.target, "take_item")
        self.assertEqual(r.dobj, "map")
        self.assertEqual(r.iobj, "Wren")

    def test_take_without_source_has_no_iobj(self):
        r = self.p("take lantern")
        self.assertEqual(r.dobj, "lantern")
        self.assertEqual(r.iobj, "")

    # --- directions ---
    def test_bare_direction(self):
        r = self.p("n")
        self.assertEqual(r.verb.target, "go")
        self.assertEqual(r.dobj, "north")         # alias expanded

    def test_full_direction_word(self):
        self.assertEqual(self.p("north").dobj, "north")

    def test_go_direction(self):
        r = self.p("go up")
        self.assertEqual(r.verb.target, "go")
        self.assertEqual(r.dobj, "up")

    def test_move_is_go_synonym(self):
        self.assertEqual(self.p("move down").dobj, "down")

    # --- free text (say) ---
    def test_say_keeps_remainder_and_case(self):
        r = self.p("say Hello There, Wren")
        self.assertEqual(r.verb.kind, "text")
        self.assertEqual(r.words, "Hello There, Wren")

    def test_say_does_not_split_on_to(self):
        # 'to' inside speech must stay in the words, not become an object.
        r = self.p("say I want to leave")
        self.assertEqual(r.words, "I want to leave")

    # --- misc queries & errors ---
    def test_inventory_synonyms(self):
        for s in ("inventory", "inv", "i"):
            self.assertEqual(self.p(s).verb.target, "inventory")

    def test_unknown_verb(self):
        r = self.p("frobnicate the widget")
        self.assertIsNone(r.verb)
        self.assertEqual(r.unknown, "frobnicate")

    def test_empty_line(self):
        r = self.p("   ")
        self.assertIsNone(r.verb)
        self.assertEqual(r.unknown, "")

    def test_case_insensitive_verb(self):
        self.assertEqual(self.p("TAKE lantern").verb.target, "take_item")


if __name__ == "__main__":
    unittest.main()
