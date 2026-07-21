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


def _cmd(schema):
    """The inner command-object schema (flat: a verb enum + object phrases)."""
    return schema["properties"]["command"]


class CommandSchemaTests(unittest.TestCase):
    """The command grammar (B1b): a FLAT constrained shape over the verb table —
    verb as an enum, not a ``oneOf`` over per-verb branches (which a weaker model
    collapses to the simplest branch under strict decoding; see command_schema)."""

    def setUp(self):
        self.cmd = _cmd(command.command_schema(default_verbs()))

    def test_top_level_wraps_a_command_object(self):
        schema = command.command_schema(default_verbs())
        self.assertEqual(schema["required"], ["command"])
        self.assertEqual(self.cmd["type"], "object")
        self.assertFalse(self.cmd["additionalProperties"])

    def test_verb_is_an_enum_over_the_verbs(self):
        verbs = set(self.cmd["properties"]["verb"]["enum"])
        self.assertIn("give", verbs)
        self.assertIn("look", verbs)
        self.assertIn("go", verbs)

    def test_verb_and_dobj_required_iobj_optional(self):
        # dobj required so the object is actually filled; iobj offered, not required.
        self.assertEqual(set(self.cmd["required"]), {"verb", "dobj"})
        self.assertIn("iobj", self.cmd["properties"])

    def test_object_phrases_are_strings(self):
        self.assertEqual(self.cmd["properties"]["dobj"], {"type": "string"})
        self.assertEqual(self.cmd["properties"]["iobj"], {"type": "string"})

    def test_allowed_narrows_the_verbs(self):
        cmd = _cmd(command.command_schema(default_verbs(), ["take", "give"]))
        self.assertEqual(set(cmd["properties"]["verb"]["enum"]), {"take", "give"})


class DescribeVerbsTests(unittest.TestCase):
    def test_usage_lines_reflect_arity(self):
        text = command.describe_verbs(default_verbs())
        self.assertIn("give <item> to <recipient>", text)
        self.assertIn("take <item> [from <source>]", text)
        self.assertIn("go <direction>", text)
        self.assertIn("say <words>", text)

    def test_allowed_narrows_the_catalogue(self):
        text = command.describe_verbs(default_verbs(), ["take"])
        self.assertIn("take <item>", text)
        self.assertNotIn("give", text)


class ParseLineTests(unittest.TestCase):
    """B11: compound & chained commands. The splitter is pure syntax (world-free),
    so it is tested here; the `all`-expansion and per-command dispatch (which need
    the world) are tested at the engine level."""
    def setUp(self):
        self.verbs = default_verbs()

    def pl(self, text):
        return command.parse_line(text, self.verbs)

    def _shape(self, text):
        """A compact, assertion-friendly view of a parsed line."""
        out = []
        for p in self.pl(text):
            if p.verb is None:
                out.append(("?", p.unknown))
            elif p.verb.kind == "text":
                out.append((p.verb.canonical, p.words))
            elif p.all_objects:
                out.append((p.verb.target, "ALL", p.iobj))
            else:
                out.append((p.verb.target, p.dobj, p.iobj))
        return out

    # --- the single-command path is unchanged (identity) ---
    def test_single_command_is_one_parse(self):
        self.assertEqual(self._shape("take lantern"),
                         [("take_item", "lantern", "")])

    def test_empty_line_is_no_commands(self):
        self.assertEqual(self.pl("   "), [])

    def test_single_matches_parse(self):
        # parse_line's one segment == what parse alone yields, field for field.
        one = command.parse("give map to Wren", self.verbs)
        got = self.pl("give map to Wren")
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0].verb.target, got[0].dobj, got[0].iobj),
                         (one.verb.target, one.dobj, one.iobj))

    # --- conjoined objects -> repeated actions ---
    def test_conjoined_objects_repeat_the_action(self):
        self.assertEqual(self._shape("take lantern and key"),
                         [("take_item", "lantern", ""),
                          ("take_item", "key", "")])

    def test_conjoined_objects_by_comma(self):
        self.assertEqual(self._shape("take lantern, key, rope"),
                         [("take_item", "lantern", ""),
                          ("take_item", "key", ""),
                          ("take_item", "rope", "")])

    def test_conjunction_distributes_over_indirect_object(self):
        self.assertEqual(self._shape("give sword and shield to Odd"),
                         [("give_item", "sword", "Odd"),
                          ("give_item", "shield", "Odd")])

    def test_conjoined_objects_strip_articles(self):
        self.assertEqual(self._shape("take the lantern and the brass key"),
                         [("take_item", "lantern", ""),
                          ("take_item", "brass key", "")])

    # --- verb-led promotion: and + verb chains; and + noun conjoins ---
    def test_and_before_a_verb_starts_a_new_command(self):
        self.assertEqual(self._shape("look at Wren and say what is this place?"),
                         [("examine", "Wren", ""),
                          ("say", "what is this place?")])

    def test_and_before_go_chains(self):
        self.assertEqual(self._shape("take gold and go north"),
                         [("take_item", "gold", ""),
                          ("go", "north", "")])

    # --- free-text verbs swallow their remainder verbatim ---
    def test_say_swallows_and_verbatim(self):
        self.assertEqual(self._shape("say hello and goodbye"),
                         [("say", "hello and goodbye")])

    def test_say_swallows_to_end_of_line(self):
        # A free-text verb is the last command on its line — periods included.
        self.assertEqual(self._shape("say hi. go north"),
                         [("say", "hi. go north")])

    def test_chain_into_a_final_say(self):
        self.assertEqual(self._shape("look and say I see"),
                         [("look", "", ""), ("say", "I see")])

    # --- unconditional separators: . ; then ---
    def test_period_chains_directions(self):
        self.assertEqual(self._shape("n. e. take lamp"),
                         [("go", "north", ""),
                          ("go", "east", ""),
                          ("take_item", "lamp", "")])

    def test_then_and_semicolon_chain(self):
        self.assertEqual(self._shape("look then go north; look"),
                         [("look", "", ""),
                          ("go", "north", ""),
                          ("look", "", "")])

    # --- bare all / everything ---
    def test_all_is_a_quantifier_not_a_name(self):
        [p] = self.pl("take all")
        self.assertTrue(p.all_objects)
        self.assertEqual(p.verb.target, "take_item")

    def test_everything_is_all(self):
        self.assertTrue(self.pl("drop everything")[0].all_objects)

    def test_all_from_source_keeps_the_source(self):
        [p] = self.pl("take all from chest")
        self.assertTrue(p.all_objects)
        self.assertEqual(p.iobj, "chest")

    def test_all_is_not_conjunction_expanded(self):
        self.assertEqual(len(self.pl("take all")), 1)

    # --- the runaway fuse ---
    def test_runaway_cap_truncates_and_flags(self):
        line = " and ".join(["go north"] * (command.MAX_COMMANDS + 5))
        parses = self.pl(line)
        self.assertEqual(len(parses), command.MAX_COMMANDS)
        self.assertTrue(parses[-1].truncated)

    def test_within_cap_is_not_flagged(self):
        parses = self.pl("look and look and look")
        self.assertFalse(parses[-1].truncated)

    # --- the B1b fallback gets the segment's own text, not the whole line ---
    def test_unknown_segment_carries_its_own_source(self):
        # An unknown verb only becomes its own segment past an unconditional
        # separator (`and` + a non-verb would conjoin, not chain).
        parses = self.pl("look then frobnicate the gizmo")
        self.assertEqual(parses[0].verb.canonical, "look")
        self.assertIsNone(parses[1].verb)
        self.assertEqual(parses[1].source, "frobnicate the gizmo")


if __name__ == "__main__":
    unittest.main()
