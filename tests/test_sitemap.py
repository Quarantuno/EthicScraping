"""Tests for scraper/sitemap.py. Follows the same mocking convention as
tests/test_fetcher.py: RobotsChecker's own methods are patched directly
(rather than mocking a separate robots.txt request through requests.get),
and requests.get is mocked for the sitemap fetch itself -- no real
network traffic, nothing environment-dependent."""
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import requests

from scraper.robots import RobotsChecker
from scraper.sitemap import SitemapFetcher


@contextmanager
def _allow_all_robots():
    with patch.object(RobotsChecker, "can_fetch", return_value=True), \
            patch.object(RobotsChecker, "crawl_delay", return_value=None):
        yield


def _make_fetcher(**kwargs):
    kwargs.setdefault("default_delay", 0.0)
    return SitemapFetcher(user_agent="TestBot/1.0 (contact@example.com)", **kwargs)


def _xml_response(body: str):
    resp = MagicMock(status_code=200, content=body.encode("utf-8"))
    resp.raise_for_status = MagicMock()
    return resp


URLSET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://esempio.it/pagina-1</loc></url>
  <url><loc>https://esempio.it/pagina-2</loc></url>
</urlset>"""

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://esempio.it/sitemap-a.xml</loc></sitemap>
  <sitemap><loc>https://esempio.it/sitemap-b.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP_A_XML = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://esempio.it/a-1</loc></url>
</urlset>"""

SITEMAP_B_XML = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://esempio.it/b-1</loc></url>
</urlset>"""

# Real-world sitemaps aren't always this well-formed: some omit the
# xmlns declaration entirely. _local_name() strips namespaces
# regardless, so this should parse the same as the namespaced version.
URLSET_XML_NO_NAMESPACE = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://esempio.it/senza-namespace</loc></url>
</urlset>"""


class TestPlainUrlset(unittest.TestCase):
    def test_returns_all_urls_from_a_plain_sitemap(self):
        with _allow_all_robots(), patch(
            "scraper.sitemap.requests.get", return_value=_xml_response(URLSET_XML)
        ) as mock_get:
            urls = _make_fetcher().discover("https://esempio.it/sitemap.xml")
        self.assertEqual(urls, ["https://esempio.it/pagina-1", "https://esempio.it/pagina-2"])
        self.assertEqual(mock_get.call_count, 1)

    def test_works_without_a_declared_namespace(self):
        with _allow_all_robots(), patch(
            "scraper.sitemap.requests.get", return_value=_xml_response(URLSET_XML_NO_NAMESPACE)
        ):
            urls = _make_fetcher().discover("https://esempio.it/sitemap.xml")
        self.assertEqual(urls, ["https://esempio.it/senza-namespace"])


class TestSitemapIndex(unittest.TestCase):
    def test_follows_sub_sitemaps_and_combines_urls(self):
        responses = {
            "https://esempio.it/sitemap.xml": _xml_response(SITEMAP_INDEX_XML),
            "https://esempio.it/sitemap-a.xml": _xml_response(SITEMAP_A_XML),
            "https://esempio.it/sitemap-b.xml": _xml_response(SITEMAP_B_XML),
        }
        with _allow_all_robots(), patch(
            "scraper.sitemap.requests.get",
            side_effect=lambda url, **kw: responses[url],
        ) as mock_get:
            urls = _make_fetcher().discover("https://esempio.it/sitemap.xml")
        self.assertEqual(sorted(urls), ["https://esempio.it/a-1", "https://esempio.it/b-1"])
        self.assertEqual(mock_get.call_count, 3)

    def test_max_sitemaps_caps_how_many_sub_sitemaps_are_followed(self):
        responses = {
            "https://esempio.it/sitemap.xml": _xml_response(SITEMAP_INDEX_XML),
            "https://esempio.it/sitemap-a.xml": _xml_response(SITEMAP_A_XML),
            "https://esempio.it/sitemap-b.xml": _xml_response(SITEMAP_B_XML),
        }
        with _allow_all_robots(), patch(
            "scraper.sitemap.requests.get",
            side_effect=lambda url, **kw: responses[url],
        ):
            # 1 (the index) + max_sitemaps(1) sub-sitemap = only sitemap-a
            # gets visited; sitemap-b is silently skipped, not an error.
            urls = _make_fetcher(max_sitemaps=2).discover("https://esempio.it/sitemap.xml")
        self.assertEqual(urls, ["https://esempio.it/a-1"])


class TestErrorHandling(unittest.TestCase):
    def test_blocked_by_robots_returns_no_urls(self):
        with patch.object(RobotsChecker, "can_fetch", return_value=False), patch(
            "scraper.sitemap.requests.get"
        ) as mock_get:
            urls = _make_fetcher().discover("https://esempio.it/sitemap.xml")
        self.assertEqual(urls, [])
        mock_get.assert_not_called()

    def test_network_error_returns_no_urls_without_raising(self):
        with _allow_all_robots(), patch(
            "scraper.sitemap.requests.get",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            urls = _make_fetcher().discover("https://esempio.it/sitemap.xml")
        self.assertEqual(urls, [])

    def test_invalid_xml_returns_no_urls_without_raising(self):
        with _allow_all_robots(), patch(
            "scraper.sitemap.requests.get",
            return_value=_xml_response("questo non e' XML valido <<<"),
        ):
            urls = _make_fetcher().discover("https://esempio.it/sitemap.xml")
        self.assertEqual(urls, [])

    def test_unexpected_root_element_returns_no_urls(self):
        with _allow_all_robots(), patch(
            "scraper.sitemap.requests.get",
            return_value=_xml_response("<rss><channel></channel></rss>"),
        ):
            urls = _make_fetcher().discover("https://esempio.it/feed.xml")
        self.assertEqual(urls, [])


if __name__ == "__main__":
    unittest.main()
