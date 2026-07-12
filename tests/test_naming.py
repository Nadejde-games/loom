"""Name resolution: a noun phrase + a scope -> the entity meant, or an honest
ambiguous / no-match. Pure, offline. Duck-typed on name/aliases/id, so a tiny
stand-in stands in for any world entity."""
import unittest

from loom.naming import resolve, Resolved, Ambiguous, NoMatch


class Thing:
    def __init__(self, id, name, aliases=()):
        self.id = id
        self.name = name
        self.aliases = list(aliases)


KEY = Thing("brass_key", "an ornate brass key", ["key", "brass key", "brass"])
LANTERN = Thing("lantern", "a rusty lantern", ["lantern", "lamp"])
ODD = Thing("hermit", "Odd the Hermit")
SCOPE = [KEY, LANTERN, ODD]


class ResolveTests(unittest.TestCase):
    def _one(self, phrase, scope=SCOPE):
        r = resolve(phrase, scope)
        self.assertIsInstance(r, Resolved)
        return r.entity

    def test_exact_full_name(self):
        self.assertIs(self._one("an ornate brass key"), KEY)

    def test_alias(self):
        self.assertIs(self._one("lamp"), LANTERN)

    def test_whole_word_inside_name(self):
        self.assertIs(self._one("lantern"), LANTERN)

    def test_case_and_space_insensitive(self):
        self.assertIs(self._one("  ODD   the Hermit "), ODD)

    def test_prefix_abbreviation(self):
        self.assertIs(self._one("bra"), KEY)          # -> "brass key" / "brass"

    def test_first_name_resolves_person(self):
        self.assertIs(self._one("Odd"), ODD)

    def test_no_match(self):
        r = resolve("dragon", SCOPE)
        self.assertIsInstance(r, NoMatch)
        self.assertEqual(r.phrase, "dragon")

    def test_empty_phrase_is_no_match(self):
        self.assertIsInstance(resolve("   ", SCOPE), NoMatch)

    def test_empty_scope_is_no_match(self):
        self.assertIsInstance(resolve("key", []), NoMatch)

    def test_ambiguous_two_keys(self):
        iron = Thing("iron_key", "an iron key", ["key"])
        r = resolve("key", [KEY, iron])
        self.assertIsInstance(r, Ambiguous)
        self.assertEqual({e.id for e in r.candidates}, {"brass_key", "iron_key"})

    def test_exact_outranks_word_so_no_false_ambiguity(self):
        # "key" is merely a word of one thing and the full name of another;
        # the exact match wins outright rather than reporting ambiguity.
        worded = Thing("bk", "brass key")   # "key" is only a word here -> 2
        plain = Thing("k", "key")           # exact name -> 3
        r = resolve("key", [worded, plain])
        self.assertIsInstance(r, Resolved)
        self.assertIs(r.entity, plain)

    def test_id_is_resolvable(self):
        self.assertIs(self._one("brass_key"), KEY)


if __name__ == "__main__":
    unittest.main()
