"""Orchestrates crawling of configured seed URLs, optionally following
same-domain links up to a max-pages-per-domain cap.

Link-following never crosses domains: staying within the seed's domain
keeps scope predictable and matches what a site's robots.txt / license
decisions were actually made about.
"""
from __future__ import annotations

import logging
from typing import Iterator, Optional, Set, List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .crawl_state import CrawlState
from .fetcher import EthicalFetcher, FetchResult

logger = logging.getLogger("scrapellm.crawler")


class Crawler:
    def __init__(self, fetcher: EthicalFetcher, max_pages_per_domain: int = 50,
                 follow_links: bool = False):
        self.fetcher = fetcher
        self.max_pages_per_domain = max_pages_per_domain
        self.follow_links = follow_links

    def _extract_links(self, html: str, base_url: str) -> List[str]:
        soup = BeautifulSoup(html, "lxml")
        domain = urlparse(base_url).netloc
        links = []
        for a in soup.find_all("a", href=True):
            absolute = urljoin(base_url, a["href"])
            if urlparse(absolute).netloc == domain and absolute.startswith("http"):
                links.append(absolute.split("#")[0])
        return links

    def crawl_seed(self, seed_url: str, state: Optional[CrawlState] = None) -> Iterator[FetchResult]:
        """Yields a FetchResult per newly-visited page for this seed.

        If `state` is given, URLs it already marks as visited (from a
        previous run) are skipped entirely -- not fetched, not yielded --
        so a run can be stopped and resumed later without re-hitting
        pages it already collected. See scraper/crawl_state.py.
        """
        visited: Set[str] = set()
        queue = [seed_url]
        domain = urlparse(seed_url).netloc
        count = 0

        while queue and count < self.max_pages_per_domain:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            if state and state.is_visited(url):
                logger.debug("Salto %s: gia' visitato in una run precedente.", url)
                continue

            result = self.fetcher.fetch(url)
            yield result
            count += 1

            if state and result.status != "error":
                state.mark_visited(url)

            if result.status != "ok":
                logger.info("Skipped %s: %s", url, result.status)
                continue

            if self.follow_links and result.html:
                for link in self._extract_links(result.html, url):
                    if link not in visited and urlparse(link).netloc == domain:
                        queue.append(link)
