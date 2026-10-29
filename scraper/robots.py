"""Robots.txt compliance checking with per-domain caching.

Never bypass robots.txt: if a site disallows a path for our user agent,
we skip it, full stop. This keeps the scraper on the right side of the
sites we visit and is the first line of "consapevolezza" (informed,
consenting data use) baked into the pipeline.

What happens when robots.txt can't be read follows RFC 9309 (the
Robots Exclusion Protocol standard), section 2.3.1:

- 2xx: parse it and obey it.
- 4xx ("unavailable"): the RFC says a crawler MAY access everything,
  as if no robots.txt existed. We do that for the plain "it isn't
  there" cases (404, 410, ...), but NOT for 401/403: a server that
  refuses to even show our bot its robots.txt is telling us something
  about our bot specifically, and "MAY" leaves room to be stricter.
  429 (Too Many Requests) is also treated as "unreachable" below
  rather than "missing" -- it's a rate-limit signal, not an absence.
- 5xx, or the request fails entirely (DNS error, timeout, connection
  reset): the RFC says the crawler MUST assume complete disallow. An
  earlier version of this module treated these as "allow everything",
  which is exactly backwards -- a temporarily broken server is the
  worst moment to start crawling it with no rules at all.
"""
from __future__ import annotations

import logging
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("scrapellm.robots")

_ALLOW_ALL = []
_DISALLOW_ALL = ["User-agent: *", "Disallow: /"]

# 4xx codes treated as "the server is refusing us" or "slow down",
# not "there is no robots.txt": see the module docstring.
_DENY_4XX = {401, 403, 429}


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
        self._parsers: Dict[str, robotparser.RobotFileParser] = {}
        # Why each domain ended up with the rules it has, for logging
        # and for tests: "parsed", "missing" (4xx -> allow all),
        # "refused" (401/403/429 -> disallow all), "server_error"
        # (5xx -> disallow all), "unreachable" (network error ->
        # disallow all).
        self.status: Dict[str, str] = {}

    def _fetch_rules(self, domain: str):
        robots_url = f"{domain}/robots.txt"
        try:
            resp = requests.get(
                robots_url, timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
        except requests.RequestException as exc:
            logger.warning(
                "robots.txt di %s irraggiungibile (%s): per RFC 9309 assumo "
                "'disallow' totale per questo dominio in questa run.", domain, exc,
            )
            return _DISALLOW_ALL, "unreachable"

        code = resp.status_code
        if 200 <= code < 300:
            return resp.text.splitlines(), "parsed"
        if code in _DENY_4XX:
            logger.warning(
                "robots.txt di %s ha risposto HTTP %d: lo tratto come rifiuto "
                "esplicito, nessuna pagina di questo dominio verra' scaricata.",
                domain, code,
            )
            return _DISALLOW_ALL, "refused"
        if 400 <= code < 500:
            # No robots.txt -> allowed, but still rate-limited as usual.
            return _ALLOW_ALL, "missing"
        logger.warning(
            "robots.txt di %s ha risposto HTTP %d: per RFC 9309 assumo "
            "'disallow' totale per questo dominio in questa run.", domain, code,
        )
        return _DISALLOW_ALL, "server_error"

    def _get_parser(self, url: str) -> robotparser.RobotFileParser:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        if domain not in self._parsers:
            lines, status = self._fetch_rules(domain)
            rp = robotparser.RobotFileParser()
            rp.parse(lines)
            self._parsers[domain] = rp
            self.status[domain] = status
        return self._parsers[domain]

    def can_fetch(self, url: str) -> bool:
        rp = self._get_parser(url)
        return rp.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> Optional[float]:
        rp = self._get_parser(url)
        delay = rp.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None
