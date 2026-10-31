"""Tests for scraper/urls.py: <link rel="canonical"> extraction (URL
normalization itself is covered in tests/test_crawler.py)."""
import unittest

from scraper.urls import extract_canonical

BASE = "https://example.com/articolo?page=1"


def _page(head):
    return f"<html><head>{head}</head><body>x</body></html>"


class TestExtractCanonical(unittest.TestCase):
    def test_absolute_canonical_is_normalized(self):
        html = _page('<link rel="canonical" href="https://Example.com/articolo?utm_source=x#top">')
        self.assertEqual(extract_canonical(html, BASE), "https://example.com/articolo")

    def test_relative_canonical_is_resolved(self):
        self.assertEqual(extract_canonical(_page('<link rel="canonical" href="/articolo">'), BASE),
                         "https://example.com/articolo")

    def test_rel_with_several_values(self):
        html = _page('<link rel="alternate canonical" href="/a">')
        self.assertEqual(extract_canonical(html, BASE), "https://example.com/a")

    def test_none_when_absent_or_not_http(self):
        self.assertIsNone(extract_canonical(_page('<link rel="stylesheet" href="/s.css">'), BASE))
        self.assertIsNone(extract_canonical(_page('<link rel="canonical" href="javascript:void(0)">'), BASE))
        self.assertIsNone(extract_canonical(None, BASE))


if __name__ == "__main__":
    unittest.main()
