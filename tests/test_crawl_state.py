"""Tests for scraper/crawl_state.py (resumable crawling across separate
`main.py run` invocations)."""
import json
import os
import shutil
import tempfile
import unittest

from scraper.crawl_state import CrawlState


class TestCrawlState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmpdir, "state", "crawl.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_state_file_means_nothing_visited(self):
        state = CrawlState(self.state_path)
        self.assertFalse(state.is_visited("https://example.com/a"))
        self.assertEqual(state.visited, set())

    def test_mark_visited_persists_to_disk(self):
        state = CrawlState(self.state_path)
        state.mark_visited("https://example.com/a")
        self.assertTrue(state.is_visited("https://example.com/a"))
        self.assertTrue(os.path.exists(self.state_path))

        with open(self.state_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["visited"], ["https://example.com/a"])

    def test_state_survives_across_instances(self):
        first = CrawlState(self.state_path)
        first.mark_visited("https://example.com/a")
        first.mark_visited("https://example.com/b")

        second = CrawlState(self.state_path)
        self.assertTrue(second.is_visited("https://example.com/a"))
        self.assertTrue(second.is_visited("https://example.com/b"))
        self.assertFalse(second.is_visited("https://example.com/c"))

    def test_none_state_path_disables_persistence_without_erroring(self):
        state = CrawlState(None)
        state.mark_visited("https://example.com/a")  # should not raise
        self.assertTrue(state.is_visited("https://example.com/a"))

    def test_corrupted_state_file_is_ignored_not_fatal(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        state = CrawlState(self.state_path)  # should not raise
        self.assertEqual(state.visited, set())


if __name__ == "__main__":
    unittest.main()
