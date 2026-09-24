"""Sitemap-based seed discovery.

Hand-picking every seed URL doesn't scale past a handful of pages. Most
sites that actually want to be indexed publish a sitemap.xml (sometimes
a sitemap *index* that just lists other sitemaps); parsing it gives a
ready-made list of candidate URLs instead.

This still goes through the same ethical machinery as the rest of the
scraper -- robots.txt, rate limiting, an honest User-Agent -- a sitemap
fetch is a fetch like any other, and sitemap index files can point at
dozens of sub-sitemaps that would otherwise be hammered with no delay
at all.
"""
from __future__ import annotations

import logging
from typing import Optional
from xml.etree import ElementTree as ET

import requests

from .rate_limiter import RateLimiter
from .robots import RobotsChecker

logger = logging.getLogger(__name__)


def _local_name(tag: str) -> str:
    """Strips the XML namespace off a tag name ('{ns}loc' -> 'loc'), so
    parsing doesn't depend on which namespace URI (or lack of one) a
    given sitemap happens to declare -- real-world sitemaps are
    inconsistent about it."""
    return tag.rsplit("}", 1)[-1]


def _find_loc(element: ET.Element) -> Optional[str]:
    for child in element:
        if _local_name(child.tag) == "loc" and child.text:
            return child.text.strip()
    return None


class SitemapFetcher:
    """Fetches and parses one or more sitemaps, following sitemap-index
    references breadth-first, robots.txt-aware and rate-limited like the
    rest of the scraper."""

    def __init__(self, user_agent: str, timeout: int = 15,
                 default_delay: float = 2.0, max_sitemaps: int = 50):
        self.user_agent = user_agent
        self.timeout = timeout
        self.robots = RobotsChecker(user_agent=user_agent, timeout=timeout)
        self.rate_limiter = RateLimiter(default_delay=default_delay)
        # Safety cap: a malicious or misconfigured sitemap index could
        # otherwise send us chasing an unbounded number of sub-sitemaps.
        self.max_sitemaps = max_sitemaps

    def _fetch_xml(self, url: str) -> Optional[ET.Element]:
        if not self.robots.can_fetch(url):
            logger.warning("robots.txt vieta il fetch di %s, salto.", url)
            return None

        delay = self.robots.crawl_delay(url)
        if delay:
            self.rate_limiter.set_delay(url, delay)
        self.rate_limiter.wait(url)

        try:
            resp = requests.get(url, timeout=self.timeout,
                                 headers={"User-Agent": self.user_agent})
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Errore scaricando %s: %s", url, exc)
            return None

        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.warning("XML non valido in %s: %s", url, exc)
            return None

    def discover(self, sitemap_url: str) -> list[str]:
        """Returns the page URLs found starting from `sitemap_url`.

        If `sitemap_url` is a plain <urlset> sitemap, returns its <url>
        entries directly. If it's a <sitemapindex>, follows each listed
        sub-sitemap (up to self.max_sitemaps total fetches, breadth
        first) and returns the combined <url> entries from all of them.
        """
        seen_sitemaps: set[str] = set()
        to_visit = [sitemap_url]
        urls: list[str] = []

        while to_visit and len(seen_sitemaps) < self.max_sitemaps:
            current = to_visit.pop(0)
            if current in seen_sitemaps:
                continue
            seen_sitemaps.add(current)

            root = self._fetch_xml(current)
            if root is None:
                continue

            root_name = _local_name(root.tag)
            if root_name == "sitemapindex":
                for child in root:
                    if _local_name(child.tag) != "sitemap":
                        continue
                    loc = _find_loc(child)
                    if loc:
                        to_visit.append(loc)
            elif root_name == "urlset":
                for child in root:
                    if _local_name(child.tag) != "url":
                        continue
                    loc = _find_loc(child)
                    if loc:
                        urls.append(loc)
            else:
                logger.warning("%s: elemento radice inatteso <%s>, ignorato.",
                                current, root.tag)

        if len(seen_sitemaps) >= self.max_sitemaps and to_visit:
            logger.warning(
                "Raggiunto il limite di %d sub-sitemap (max_sitemaps): "
                "%d sitemap non ancora visitate sono state ignorate.",
                self.max_sitemaps, len(to_visit),
            )

        return urls
