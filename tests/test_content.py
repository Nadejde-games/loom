"""The world loader: single file or a directory of files, plus world-level meta
capture. Pure, offline — the 'world is editable data' seam."""
import json
import os
import tempfile
import unittest

from loom.content import load_world

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_WORLD = os.path.join(HERE, "..", "game", "world", "world.json")


def _write(dir_path, name, data):
    with open(os.path.join(dir_path, name), "w", encoding="utf-8") as f:
        json.dump(data, f)


class LoadSingleFileTests(unittest.TestCase):
    def test_loads_the_demo_world(self):
        world, start = load_world(GAME_WORLD)
        self.assertEqual(start, "cave_mouth")
        self.assertIn("cave_mouth", world.locations)
        self.assertIn("hermit", world.entities)
        self.assertIn("lantern", world.entities)

    def test_captures_director_meta(self):
        world, _ = load_world(GAME_WORLD)
        director = world.meta.get("director")
        self.assertIsInstance(director, dict)
        self.assertIn("tone", director)
        self.assertTrue(director.get("goals"))

    def test_unknown_top_level_key_lands_in_meta(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "w.json")
            _write(d, "w.json", {
                "start_location": "a",
                "locations": [{"id": "a", "name": "A"}],
                "weather": {"season": "autumn"},   # not structural
            })
            world, start = load_world(path)
            self.assertEqual(start, "a")
            self.assertEqual(world.meta["weather"], {"season": "autumn"})

    def test_start_location_falls_back_to_first_location(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "w.json", {"locations": [{"id": "z"}, {"id": "y"}]})
            world, start = load_world(os.path.join(d, "w.json"))
            self.assertEqual(start, "z")


class LoadDirectoryTests(unittest.TestCase):
    def test_merges_entities_across_files(self):
        with tempfile.TemporaryDirectory() as d:
            # A global file (start + meta) plus two "region" files.
            _write(d, "00_world.json", {
                "start_location": "clearing",
                "director": {"tone": "hushed"},
            })
            _write(d, "10_forest.json", {
                "locations": [{"id": "clearing", "name": "Clearing",
                               "exits": {"east": "grove"}}],
                "npcs": [{"id": "wren", "name": "Wren", "location": "clearing"}],
            })
            _write(d, "20_grove.json", {
                "locations": [{"id": "grove", "name": "Grove"}],
                "items": [{"id": "acorn", "name": "an acorn", "holder": "grove"}],
            })
            world, start = load_world(d)               # the directory itself
            self.assertEqual(start, "clearing")
            self.assertEqual(set(world.locations), {"clearing", "grove"})
            self.assertIn("wren", world.entities)
            self.assertIn("acorn", world.entities)
            self.assertEqual(world.meta["director"], {"tone": "hushed"})

    def test_meta_blocks_shallow_merge_across_files(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "00.json", {"director": {"tone": "hushed"}})
            _write(d, "10.json", {"director": {"goals": ["watchful"]},
                                  "locations": [{"id": "a"}]})
            world, _ = load_world(d)
            self.assertEqual(world.meta["director"],
                             {"tone": "hushed", "goals": ["watchful"]})

    def test_first_declared_start_wins(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "00.json", {"start_location": "a",
                                  "locations": [{"id": "a"}]})
            _write(d, "10.json", {"start_location": "b",
                                  "locations": [{"id": "b"}]})
            _, start = load_world(d)
            self.assertEqual(start, "a")


if __name__ == "__main__":
    unittest.main()
