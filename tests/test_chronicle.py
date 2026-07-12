"""The world chronicle: a bounded event feed with a monotonic cursor. Pure,
offline. This is the perception substrate the game-master director reads."""
import unittest

from loom.chronicle import Chronicle, ChronicleEvent


class ChronicleTests(unittest.TestCase):
    def test_record_advances_seq_and_returns_it(self):
        c = Chronicle()
        self.assertEqual(c.seq, 0)
        self.assertEqual(c.record("a arrived", location_id="room"), 1)
        self.assertEqual(c.record("a said hi", location_id="room"), 2)
        self.assertEqual(c.seq, 2)

    def test_empty_text_is_ignored_and_does_not_advance(self):
        c = Chronicle()
        self.assertEqual(c.record("   "), 0)
        self.assertEqual(c.record(""), 0)
        self.assertEqual(c.seq, 0)
        self.assertEqual(c.recent(), [])

    def test_recent_is_chronological_and_bounded(self):
        c = Chronicle(maxlen=3)
        for i in range(5):
            c.record(f"event {i}")
        texts = [e.text for e in c.recent()]
        self.assertEqual(texts, ["event 2", "event 3", "event 4"])   # oldest dropped
        # seq keeps growing even though old events fell out of the window.
        self.assertEqual(c.seq, 5)

    def test_recent_n_slices_the_tail(self):
        c = Chronicle()
        for i in range(4):
            c.record(f"e{i}")
        self.assertEqual([e.text for e in c.recent(2)], ["e2", "e3"])
        self.assertEqual(c.recent(0), [])
        self.assertEqual(len(c.recent()), 4)                         # None = all

    def test_since_returns_only_newer_events(self):
        c = Chronicle()
        c.record("old")
        mark = c.seq
        c.record("new one")
        c.record("new two")
        newer = c.since(mark)
        self.assertEqual([e.text for e in newer], ["new one", "new two"])
        self.assertTrue(all(e.seq > mark for e in newer))

    def test_seq_equality_is_the_laziness_gate(self):
        # The director's 'anything happened since I last looked?' test.
        c = Chronicle()
        c.record("something")
        last_seen = c.seq
        self.assertEqual(c.seq, last_seen)          # nothing new -> would skip
        c.record("then something else")
        self.assertNotEqual(c.seq, last_seen)       # new activity -> would run

    def test_render_lines(self):
        c = Chronicle()
        self.assertEqual(c.render(), "(nothing has happened yet)")
        c.record("Wren moved north")
        c.record("Odd nods slowly")
        self.assertEqual(c.render(), "- Wren moved north\n- Odd nods slowly")

    def test_event_carries_metadata(self):
        c = Chronicle()
        c.record("a said hi", location_id="clearing", kind="speech")
        e = c.recent()[-1]
        self.assertIsInstance(e, ChronicleEvent)
        self.assertEqual((e.location_id, e.kind, e.seq), ("clearing", "speech", 1))


if __name__ == "__main__":
    unittest.main()
