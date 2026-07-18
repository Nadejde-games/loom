"""B2/B3: composing a turn's speech and deeds into one styled room beat.

Pure, world-free rendering — the action seam is untouched. ``compose_beat`` returns
a *styled line* (a list of semantic spans, B3); ``plain`` flattens it to the prose a
terminal reads (the B2 action-first, speech-attribution form). These prove both: the
prose is unchanged, and the spans carry the right semantic roles."""
import unittest

from loom.compose import compose_beat
from loom.style import Style, plain


def roles(line):
    """The (role, text) of each span, for asserting on the styling."""
    return [(sp.get("s"), sp["t"]) for sp in line]


class ComposeProseTests(unittest.TestCase):
    """The flattened prose — the B2 contract, unchanged by B3 styling."""

    def test_speech_only_keeps_the_name_colon_form(self):
        self.assertEqual(plain(compose_beat("Odd", "The hills know my name.", [])),
                         "Odd: The hills know my name.")

    def test_one_deed_fuses_action_first(self):
        self.assertEqual(
            plain(compose_beat("Odd the Hermit",
                               "The hills know my name better than yours",
                               ["Odd the Hermit shakes his head slowly"])),
            'Odd the Hermit shakes his head slowly and says, '
            '"The hills know my name better than yours."')

    def test_deed_trailing_period_is_dropped_before_the_join(self):
        self.assertEqual(
            plain(compose_beat("Wren", "Take this, you'll need it",
                               ["Wren gives the map to Odd."])),
            'Wren gives the map to Odd and says, "Take this, you\'ll need it."')

    def test_move_departure_fuses_and_keeps_its_own_comma(self):
        self.assertEqual(
            plain(compose_beat("Wren", "This way — follow me",
                               ["Wren leaves, heading north."])),
            'Wren leaves, heading north and says, "This way — follow me."')

    def test_speech_terminal_punctuation_is_not_doubled(self):
        self.assertEqual(
            plain(compose_beat("Odd", "Who goes there?", ["Odd turns"])),
            'Odd turns and says, "Who goes there?"')

    def test_many_deeds_elide_the_repeated_name_and_use_an_oxford_join(self):
        self.assertEqual(
            plain(compose_beat("Wren", "Come, quickly",
                               ["Wren nods once", "Wren leaves, heading north."])),
            'Wren nods once, leaves, heading north, and says, "Come, quickly."')

    def test_a_custom_deed_without_the_name_is_left_whole(self):
        self.assertEqual(
            plain(compose_beat("Odd", "So it begins",
                               ["Odd raises a hand", "the ground trembles."])),
            'Odd raises a hand, the ground trembles, and says, "So it begins."')

    def test_deeds_only_no_speech_returns_the_clause_with_a_period(self):
        self.assertEqual(plain(compose_beat("Odd", "", ["Odd nods slowly"])),
                         "Odd nods slowly.")

    def test_bodiless_speech_is_just_the_line(self):
        self.assertEqual(
            plain(compose_beat("", "A cold wind gutters the lanterns.", [])),
            "A cold wind gutters the lanterns.")

    def test_nothing_to_say_or_do_is_empty(self):
        self.assertEqual(compose_beat("Odd", "", []), [])

    def test_blank_deeds_are_ignored(self):
        self.assertEqual(plain(compose_beat("Odd", "Hello.", ["", "   "])),
                         "Odd: Hello.")


class ComposeStyleTests(unittest.TestCase):
    """The semantic spans a rich client themes (B3)."""

    def test_speech_only_tags_name_and_speech(self):
        self.assertEqual(
            roles(compose_beat("Odd", "Well met.", [])),
            [(Style.NAME, "Odd"), (None, ": "), (Style.SPEECH, "Well met.")])

    def test_fused_beat_tags_name_gesture_and_speech(self):
        # The leading name is NAME even inside the deed clause; the gesture is
        # EMOTE; the join is default; the quoted line is SPEECH.
        self.assertEqual(
            roles(compose_beat("Odd", "The hills know my name",
                               ["Odd shakes his head slowly"])),
            [(Style.NAME, "Odd"),
             (Style.EMOTE, " shakes his head slowly"),
             (None, " and says, "),
             (Style.SPEECH, '"The hills know my name."')])

    def test_bodiless_line_is_a_single_speech_span(self):
        self.assertEqual(roles(compose_beat("", "A cold wind rises.", [])),
                         [(Style.SPEECH, "A cold wind rises.")])

    def test_custom_deed_without_the_name_is_one_emote_span(self):
        self.assertEqual(roles(compose_beat("Odd", "", ["the ground trembles."])),
                         [(Style.EMOTE, "the ground trembles"), (None, ".")])


if __name__ == "__main__":
    unittest.main()
