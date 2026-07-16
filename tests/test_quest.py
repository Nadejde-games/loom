"""The quest subsystem (loom/quest.py): a per-player log with deterministic,
seam-bound completion. Pure, offline — no world, no provider, no GPU."""
import unittest

from loom.quest import QuestBook, Quest, REACH, ACTIVE, COMPLETE


class QuestBookTests(unittest.TestCase):
    def setUp(self):
        self.book = QuestBook()

    def test_offer_creates_an_active_quest_with_fresh_id(self):
        q = self.book.offer("p1", title="The Hill", summary="Climb it",
                            giver="the Director", destination="hill")
        self.assertIsInstance(q, Quest)
        self.assertEqual(q.status, ACTIVE)
        self.assertEqual(q.kind, REACH)
        self.assertEqual(q.destination, "hill")
        self.assertEqual(q.giver, "the Director")
        self.assertEqual(self.book.for_player("p1"), [q])

    def test_ids_are_unique_across_offers(self):
        a = self.book.offer("p1", title="A", summary="s", destination="x")
        b = self.book.offer("p1", title="B", summary="s", destination="y")
        self.assertNotEqual(a.id, b.id)

    def test_per_player_isolation(self):
        self.book.offer("p1", title="A", summary="s", destination="x")
        self.assertEqual(self.book.for_player("p2"), [])
        self.book.offer("p2", title="B", summary="s", destination="y")
        self.assertEqual([q.title for q in self.book.for_player("p1")], ["A"])
        self.assertEqual([q.title for q in self.book.for_player("p2")], ["B"])

    def test_duplicate_active_title_is_skipped(self):
        first = self.book.offer("p1", title="Same", summary="s", destination="x")
        dupe = self.book.offer("p1", title="Same", summary="s2", destination="x")
        self.assertIsNotNone(first)
        self.assertIsNone(dupe)                          # de-duped by title
        self.assertEqual(len(self.book.for_player("p1")), 1)

    def test_a_completed_title_may_be_offered_again(self):
        self.book.offer("p1", title="Loop", summary="s", destination="x")
        self.book.complete_reached("p1", "x")            # now complete
        again = self.book.offer("p1", title="Loop", summary="s", destination="x")
        self.assertIsNotNone(again)                      # dedupe is on ACTIVE only
        self.assertEqual(len(self.book.for_player("p1")), 2)

    def test_active_lists_only_open_quests(self):
        self.book.offer("p1", title="Open", summary="s", destination="x")
        self.book.offer("p1", title="Done", summary="s", destination="y")
        self.book.complete_reached("p1", "y")
        self.assertEqual([q.title for q in self.book.active("p1")], ["Open"])

    def test_complete_reached_marks_matching_destination(self):
        q = self.book.offer("p1", title="Go", summary="s", destination="hill")
        done = self.book.complete_reached("p1", "hill")
        self.assertEqual(done, [q])
        self.assertEqual(q.status, COMPLETE)
        self.assertTrue(q.done)

    def test_complete_reached_ignores_other_destinations(self):
        q = self.book.offer("p1", title="Go", summary="s", destination="hill")
        self.assertEqual(self.book.complete_reached("p1", "cave"), [])
        self.assertEqual(q.status, ACTIVE)

    def test_complete_reached_is_idempotent(self):
        self.book.offer("p1", title="Go", summary="s", destination="hill")
        self.assertEqual(len(self.book.complete_reached("p1", "hill")), 1)
        self.assertEqual(self.book.complete_reached("p1", "hill"), [])   # no re-fire

    def test_complete_reached_completes_all_matching(self):
        self.book.offer("p1", title="A", summary="s", destination="hill")
        self.book.offer("p1", title="B", summary="s", destination="hill")
        done = self.book.complete_reached("p1", "hill")
        self.assertEqual({q.title for q in done}, {"A", "B"})

    def test_empty_player_reads_clean(self):
        self.assertEqual(self.book.for_player("nobody"), [])
        self.assertEqual(self.book.active("nobody"), [])
        self.assertEqual(self.book.complete_reached("nobody", "hill"), [])


if __name__ == "__main__":
    unittest.main()
