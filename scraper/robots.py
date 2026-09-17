"""Robots.txt compliance checking with per-domain caching.

Never bypass robots.txt: if a site disallows a path for our user agent,
we skip it, full stop. This keeps the scraper on the right side of the
sites we visit and is the first line of "consapevolezza" (informed,
consenting data use) baked into the pipeline.
"""
from __future__ import annotations

import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import requests


@dataclass
class RobotsPolicy:
    allowed: bool
    crawl_delay: Optional[float]
    fetched_at: float = field(default_factory=time.time)


class RobotsChecker:
    """Fetches and caches robots.txt per domain, and answers can_fetch()."""

    def __init__(self, user_agent: str, timeout: int = 10):
        self.user_agent = user_agent
        self.timeout = timeout
        self._parsers: dict[str, robotparser.RobotFileParser] = {}

    def _get_parser(self, url: str) -> robotparser.RobotFileParser:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        if domain not in self._parsers:
            rp = robotparser.RobotFileParser()
            robots_url = f"{domain}/robots.txt"
            try:
                resp = requests.get(
                    robots_url, timeout=self.timeout,
                    headers={"User-Agent": self.user_agent},
                )
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # No robots.txt found -> default to allow, still rate-limited
                    rp.parse([])
            except requests.RequestException:
                rp.parse([])
            self._parsers[domain] = rp
        return self._parsers[domain]

    def can_fetch(self, url: str) -> bool:
        rp = self._get_parser(url)
        return rp.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> Optional[float]:
        rp = self._get_parser(url)
        delay = rp.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None
