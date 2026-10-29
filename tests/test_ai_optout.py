"""Tests for scraper/ai_optout.py: the "noai" / "noimageai" opt-out
signals in <meta name="robots"> and the X-Robots-Tag header."""
import unittest

from scraper.ai_optout import combined_status, meta_directives, parse_directives


def _page(meta=""):
    return f"<html><head>{meta}</head><body><p>testo</p></body></html>"


class TestParseDirectives(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(parse_directives("noindex, NoAI ,nofollow"), {"noindex", "noai", "nofollow"})

    def test_user_agent_scoped_prefix_is_dropped(self):
        self.assertEqual(parse_directives("otherbot: noai"), {"noai"})

    def test_empty_and_non_string(self):
        self.assertEqual(parse_directives(None), set())
        self.assertEqual(parse_directives(""), set())
        self.assertEqual(parse_directives(42), set())


class TestMetaDirectives(unittest.TestCase):
    def test_robots_meta(self):
        html = _page('<meta name="robots" content="noai, noimageai">')
        self.assertEqual(meta_directives(html), {"noai", "noimageai"})

    def test_bot_specific_meta_is_honored_too(self):
        html = _page('<meta name="someaibot" content="noai">')
        self.assertIn("noai", meta_directives(html))

    def test_unrelated_meta_is_ignored(self):
        html = _page('<meta name="description" content="noai is a word in this sentence">')
        self.assertEqual(meta_directives(html), set())


class TestCombinedStatus(unittest.TestCase):
    def test_no_signal(self):
        status = combined_status(_page(), None)
        self.assertFalse(status["opted_out"])
        self.assertEqual(status["directives"], [])
        self.assertIsNone(status["source"])

    def test_noai_in_meta(self):
        status = combined_status(_page('<meta name="robots" content="noai">'))
        self.assertTrue(status["opted_out"])
        self.assertEqual(status["source"], "meta_robots")

    def test_noai_in_header_wins_as_source(self):
        status = combined_status(_page('<meta name="robots" content="noai">'), "noai")
        self.assertTrue(status["opted_out"])
        self.assertEqual(status["source"], "x_robots_tag")

    def test_noimageai_alone_is_reported_but_not_an_optout_for_text(self):
        status = combined_status(_page('<meta name="robots" content="noimageai">'))
        self.assertFalse(status["opted_out"])
        self.assertEqual(status["directives"], ["noimageai"])

    def test_none_and_noindex_are_not_ai_optouts(self):
        status = combined_status(_page('<meta name="robots" content="none, noindex">'), "noarchive")
        self.assertFalse(status["opted_out"])
        self.assertEqual(status["directives"], [])

    def test_missing_html(self):
        self.assertFalse(combined_status(None, None)["opted_out"])
        self.assertTrue(combined_status(None, "noai")["opted_out"])


if __name__ == "__main__":
    unittest.main()
