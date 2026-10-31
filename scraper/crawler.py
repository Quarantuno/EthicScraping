"""Orchestrates crawling of configured seed URLs, optionally following
same-domain links up to a max-pages-per-domain cap.

Link-following never crosses domains: staying within the seed's domain
keeps scope predictable and matches what a site's robots.txt / license
decisions were actually made about.

When following links, the crawler also:
- honors "nofollow" -- page-wide (<meta name="robots" content="nofollow">,
  "none", or an X-Robots-Tag: nofollow header) and per link
  (<a rel="nofollow">). robots.txt says what we may fetch; nofollow is
  the publisher saying which links they don't vouch for / don't want
  crawled through, and an ethical crawler respects both;
- normalizes URLs (drops #fragments and well-known tracking parameters
  like utm_*, lowercases scheme/host, drops default ports), so the same
  page reached through two differently-decorated links is fetched once;
- skips links that obviously aren't HTML pages (.pdf, images, archives,
  media, ...), instead of spending a request -- and a slice of the
  site's patience -- just to discard them on Content-Type;
- can stop at a maximum link depth from the seed, and restrict which
  paths it follows with include/exclude regular expressions.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
from collections import OrderedDict, deque
from typing import Iterable, Iterator, List, Optional, Pattern, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .ai_optout import meta_directives, parse_directives
from .crawl_state import CrawlState
from .fetcher import EthicalFetcher, FetchResult
from .urls import TRACKING_PARAMS, TRACKING_PARAM_PREFIXES, normalize_url  # noqa: F401 (re-exported)

logger = logging.getLogger("scrapellm.crawler")

# File extensions that are never going to be an HTML page worth parsing.
NON_HTML_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tif", ".tiff",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".mp4", ".webm", ".avi", ".mov", ".mkv",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".epub",
    ".exe", ".dmg", ".msi", ".apk", ".iso", ".bin",
    ".css", ".js", ".json", ".woff", ".woff2", ".ttf", ".otf", ".eot",
}


def _looks_like_non_html(url: str) -> bool:
    path = urlparse(url).path.lower()
    dot = path.rfind(".")
    if dot == -1 or "/" in path[dot:]:
        return False
    return path[dot:] in NON_HTML_EXTENSIONS


def _compile(patterns: Optional[Iterable[str]]) -> List[Pattern]:
    return [re.compile(p) for p in (patterns or [])]


class Crawler:
    def __init__(self, fetcher: EthicalFetcher, max_pages_per_domain: int = 50,
                 follow_links: bool = False, max_depth: Optional[int] = None,
                 include_patterns: Optional[Iterable[str]] = None,
                 exclude_patterns: Optional[Iterable[str]] = None,
                 respect_nofollow: bool = True):
        """
        max_depth: how many link hops away from the seed to go (seed = 0,
            pages linked from it = 1, ...). None = no depth limit, only
            max_pages_per_domain applies.
        include_patterns / exclude_patterns: regular expressions matched
            (re.search) against each discovered link's full URL. With
            include_patterns, a link must match at least one; a link
            matching any exclude pattern is always skipped. Neither
            applies to the seed itself.
        respect_nofollow: honor page-wide and per-link "nofollow" (on by
            default; turning it off is only meant for debugging).
        """
        self.fetcher = fetcher
        self.max_pages_per_domain = max_pages_per_domain
        self.follow_links = follow_links
        self.max_depth = max_depth
        self.include_patterns = _compile(include_patterns)
        self.exclude_patterns = _compile(exclude_patterns)
        self.respect_nofollow = respect_nofollow

    @staticmethod
    def _page_is_nofollow(result: FetchResult) -> bool:
        directives = meta_directives(result.html) | parse_directives(result.x_robots_tag)
        return "nofollow" in directives or "none" in directives

    def _link_allowed(self, url: str) -> bool:
        if _looks_like_non_html(url):
            return False
        if any(p.search(url) for p in self.exclude_patterns):
            return False
        if self.include_patterns and not any(p.search(url) for p in self.include_patterns):
            return False
        return True

    def _extract_links(self, html: str, base_url: str) -> List[str]:
        soup = BeautifulSoup(html, "lxml")
        domain = urlparse(normalize_url(base_url)).netloc
        links = []
        for a in soup.find_all("a", href=True):
            if self.respect_nofollow:
                rel = a.get("rel") or []
                if isinstance(rel, str):
                    rel = rel.split()
                if "nofollow" in [r.lower() for r in rel]:
                    continue
            absolute = urljoin(base_url, a["href"])
            if not absolute.startswith(("http://", "https://")):
                continue
            normalized = normalize_url(absolute)
            if urlparse(normalized).netloc == domain:
                links.append(normalized)
        return links

    def crawl_seed(self, seed_url: str, state: Optional[CrawlState] = None) -> Iterator[FetchResult]:
        """Yields a FetchResult per newly-visited page for this seed.

        If `state` is given, URLs it already marks as visited (from a
        previous run) are skipped entirely -- not fetched, not yielded --
        so a run can be stopped and resumed later without re-hitting
        pages it already collected. See scraper/crawl_state.py.
        """
        seed = normalize_url(seed_url)
        domain = urlparse(seed).netloc
        queue = deque([(seed, 0)])
        enqueued: Set[str] = {seed}
        count = 0

        while queue and count < self.max_pages_per_domain:
            url, depth = queue.popleft()

            # The second check keeps crawl-state files written before URL
            # normalization existed working for un-normalized seeds.
            if state and (state.is_visited(url) or (url == seed and state.is_visited(seed_url))):
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

            if not (self.follow_links and result.html):
                continue
            if self.max_depth is not None and depth >= self.max_depth:
                continue
            if self.respect_nofollow and self._page_is_nofollow(result):
                logger.info("%s chiede 'nofollow': non seguo i suoi link.", url)
                continue

            for link in self._extract_links(result.html, url):
                if link in enqueued or urlparse(link).netloc != domain:
                    continue
                if not self._link_allowed(link):
                    continue
                enqueued.add(link)
                queue.append((link, depth + 1))


def group_seeds_by_domain(seeds: Iterable[str]) -> "OrderedDict[str, List[str]]":
    groups: "OrderedDict[str, List[str]]" = OrderedDict()
    for seed in seeds:
        groups.setdefault(urlparse(normalize_url(seed)).netloc, []).append(seed)
    return groups


def crawl_seeds(crawler: Crawler, seeds: Iterable[str], state: Optional[CrawlState] = None,
                max_parallel_domains: int = 1) -> Iterator[Tuple[str, FetchResult]]:
    """Yields (seed, FetchResult) for every page fetched from `seeds`.

    With max_parallel_domains > 1, seeds are grouped by domain and up to
    that many *domains* are crawled at the same time, one thread each.
    Politeness doesn't change: all the seeds of a given domain are
    crawled by the same thread, one request at a time, with that
    domain's own delay, so no site ever sees more than one request from
    us at once -- we just stop waiting on site A's delay before starting
    on site B. Results are yielded to the caller's thread, so writing
    the dataset stays single-threaded.

    max_parallel_domains <= 1 is the plain sequential crawl, in seed order.
    """
    seeds = list(seeds)
    groups = group_seeds_by_domain(seeds)
    workers = min(max(1, max_parallel_domains), len(groups) or 1)

    if workers <= 1:
        for seed in seeds:
            logger.info("Seed: %s", seed)
            for result in crawler.crawl_seed(seed, state=state):
                yield seed, result
        return

    pending: "queue.Queue[str]" = queue.Queue()
    for domain in groups:
        pending.put(domain)
    results: "queue.Queue" = queue.Queue(maxsize=100)
    stop = threading.Event()
    done_marker = object()

    def worker():
        try:
            while not stop.is_set():
                try:
                    domain = pending.get_nowait()
                except queue.Empty:
                    return
                for seed in groups[domain]:
                    logger.info("Seed: %s", seed)
                    for result in crawler.crawl_seed(seed, state=state):
                        if stop.is_set():
                            return
                        results.put((seed, result))
        except Exception as exc:  # surface it in the main thread's logs
            logger.exception("Errore nel crawl parallelo: %s", exc)
        finally:
            results.put(done_marker)

    threads = [threading.Thread(target=worker, name=f"crawl-{i}", daemon=True)
               for i in range(workers)]
    logger.info("Crawl in parallelo su %d domini alla volta (%d domini in totale).",
                workers, len(groups))
    for t in threads:
        t.start()
    finished = 0
    try:
        while finished < len(threads):
            item = results.get()
            if item is done_marker:
                finished += 1
                continue
            yield item
    finally:
        # Caller stopped early (or Ctrl+C): let workers wind down after
        # their current request instead of starting new ones.
        stop.set()
        while True:
            try:
                results.get_nowait()
            except queue.Empty:
                break
