"""Single-page fetcher: robots-aware, rate-limited, license- and
TDM-rights-aware.

Every fetch either succeeds with full provenance metadata attached, or
comes back with a clear reason it was skipped (blocked by robots.txt,
blocked by our own deny-list, or a network/HTTP error). Nothing is
fetched silently or "just in case".
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

from .robots import RobotsChecker
from .rate_limiter import RateLimiter
from .license_detector import detect_license
from .tdm_rights import TdmRepChecker, combined_status as tdm_combined_status


@dataclass
class FetchResult:
    url: str
    status: str  # "ok" | "blocked_robots" | "blocked_denylist" | "error"
    html: Optional[str] = None
    status_code: Optional[int] = None
    license_info: Optional[dict] = None
    tdm_reservation: Optional[dict] = None
    fetched_at: Optional[float] = None
    error: Optional[str] = None


class EthicalFetcher:
    """Wraps requests.get with robots.txt + deny-list + rate-limit checks,
    and attaches provenance/license/TDM-rights metadata to every
    successful fetch."""

    def __init__(self, user_agent: str, deny_domains: Optional[list] = None,
                 default_delay: float = 2.0, timeout: int = 15,
                 check_tdm_reservation: bool = True):
        self.user_agent = user_agent
        self.deny_domains = set(deny_domains or [])
        self.robots = RobotsChecker(user_agent=user_agent, timeout=timeout)
        self.rate_limiter = RateLimiter(default_delay=default_delay)
        self.timeout = timeout
        # check_tdm_reservation gates the extra /.well-known/tdmrep.json
        # request per domain (Art. 4(3) Directive 2019/790 opt-out
        # signal); the free HTML meta-tag check always runs regardless.
        self.tdm_checker = TdmRepChecker(user_agent=user_agent, timeout=timeout) \
            if check_tdm_reservation else None

    def _is_denied(self, url: str) -> bool:
        domain = urlparse(url).netloc.lower()
        return any(domain == d or domain.endswith("." + d) for d in self.deny_domains)

    def fetch(self, url: str) -> FetchResult:
        if self._is_denied(url):
            return FetchResult(url=url, status="blocked_denylist")

        if not self.robots.can_fetch(url):
            return FetchResult(url=url, status="blocked_robots")

        delay = self.robots.crawl_delay(url)
        if delay:
            self.rate_limiter.set_delay(url, delay)
        self.rate_limiter.wait(url)

        try:
            resp = requests.get(
                url, timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
        except requests.RequestException as exc:
            return FetchResult(url=url, status="error", error=str(exc))

        if resp.status_code != 200:
            return FetchResult(
                url=url, status="error", status_code=resp.status_code,
                error=f"HTTP {resp.status_code}",
            )

        license_info = detect_license(resp.text, url)
        tdm_reservation = tdm_combined_status(resp.text, url, self.tdm_checker)

        return FetchResult(
            url=url,
            status="ok",
            html=resp.text,
            status_code=resp.status_code,
            license_info=license_info,
            tdm_reservation=tdm_reservation,
            fetched_at=time.time(),
        )
