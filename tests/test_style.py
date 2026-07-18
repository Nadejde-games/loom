"""B3: semantic styling primitives, and the reference client's rendering.

The engine tags spans with semantic roles (loom.style); the client owns the theme.
These prove the wire helpers (build, flatten, merge) and that the terminal renders
a styled line to ANSI when colour is on and degrades to plain prose when it is off
or a role is unknown — so styling never breaks a simpler reader."""
import unittest

from loom.style import Style, span, styled, join_styled, plain


class SpanBuildTests(unittest.TestCase):
    def test_span_carries_a_role(self):
        self.assertEqual(span("Odd", Style.NAME), {"t": "Odd", "s": "name"})

    def test_span_without_a_role_is_default(self):
        self.assertEqual(span("plain"), {"t": "plain"})

    def test_styled_coerces_bare_strings_and_drops_empties(self):
        self.assertEqual(styled("a", "", None, span("b", Style.ITEM)),
                         [{"t": "a"}, {"t": "b", "s": "item"}])

    def test_styled_merges_adjacent_default_spans(self):
        # Keeps the wire compact: two plain runs become one span.
        self.assertEqual(styled("Exits: ", "north"), [{"t": "Exits: north"}])

    def test_styled_does_not_merge_across_a_role(self):
        self.assertEqual(
            styled(span("Odd", Style.NAME), ": ", span("hi", Style.SPEECH)),
            [{"t": "Odd", "s": "name"}, {"t": ": "},
             {"t": "hi", "s": "speech"}])

    def test_styled_splices_a_nested_styled_line(self):
        inner = join_styled(["a", "b"], Style.ITEM)
        self.assertEqual(styled("Items: ", inner),
                         [{"t": "Items: "}, {"t": "a", "s": "item"},
                          {"t": ", "}, {"t": "b", "s": "item"}])

    def test_join_styled_tags_each_and_separates(self):
        self.assertEqual(
            join_styled(["north", "south"], Style.EXIT, sep=" | "),
            [{"t": "north", "s": "exit"}, " | ", {"t": "south", "s": "exit"}])


class PlainTests(unittest.TestCase):
    def test_plain_passes_a_string_through(self):
        self.assertEqual(plain("You can't go that way."), "You can't go that way.")

    def test_plain_flattens_a_styled_line(self):
        line = styled(span("Odd", Style.NAME), ": ", span("Well met.", Style.SPEECH))
        self.assertEqual(plain(line), "Odd: Well met.")

    def test_plain_of_none_is_empty(self):
        self.assertEqual(plain(None), "")


class ClientRenderTests(unittest.TestCase):
    """The reference terminal's payload rendering (client/terminal.py)."""

    def _render_with_colour(self, data, colour):
        from client import terminal
        saved = terminal._COLOR
        terminal._COLOR = colour
        try:
            return terminal._render(data)
        finally:
            terminal._COLOR = saved

    def test_string_payload_passes_through(self):
        self.assertEqual(self._render_with_colour("hello", True), "hello")

    def test_colour_off_degrades_a_styled_line_to_plain(self):
        line = styled(span("Odd", Style.NAME), ": ", span("hi", Style.SPEECH))
        self.assertEqual(self._render_with_colour(line, False), "Odd: hi")

    def test_colour_on_wraps_known_roles_and_resets(self):
        out = self._render_with_colour([span("Odd", Style.NAME)], True)
        self.assertTrue(out.startswith("\033["))     # an SGR opener
        self.assertTrue(out.endswith("\033[0m"))      # and a reset
        self.assertIn("Odd", out)

    def test_colour_on_leaves_unknown_or_default_roles_plain(self):
        # An unstyled span, and an unknown role, render as bare text even in colour.
        line = [span("plain "), span("x", "no-such-role")]
        self.assertEqual(self._render_with_colour(line, True), "plain x")


if __name__ == "__main__":
    unittest.main()
