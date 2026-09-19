"""Single-page fetcher: robots-aware, rate-limited, license- and
TDM-rights-aware.

Every fetch either succeeds with full provenance metadata attached, or
comes back with a clear reason it was skipped (blocked by robots.txt,
blocked by our own deny-list, wrong content type, or a network/HTTP
error after retries). Nothing is fetched silently or "just in case".
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

from .robots import RobotsChecker
from .rate_limiter import RateLimiter
from .license_detector import detect_license
from .tdm_rights import TdmRepChecker, combined_status as tdm_combined_status

logger = logging.getLogger(__name__)

# Content types we know how to treat as text for the pipeline. Anything
# else (PDFs, images, JSON APIs, video, ...) is discarded before we ever
# try to decode/parse it as HTML.
DEFAULT_ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}

# Retries only make sense for transient failures: connection resets,
# timeouts, and 5xx server errors. A 4xx (not found, forbidden, ...) or a
# robots/deny-list block is not going to change if we ask again.
RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


@dataclass
class FetchResult:
    url: str
    # "ok" | "blocked_robots" | "blocked_denylist" | "skipped_content_type"
    # | "error"
    status: str
    html: Optional[str] = None
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    license_info: Optional[dict] = None
    tdm_reservation: Optional[dict] = None
    fetched_at: Optional[float] = None
    error: Optional[str] = None
    attempts: int = 1


class EthicalFetcher:
    """Wraps requests.get with robots.txt + deny-list + rate-limit checks,
    retries transient failures with exponential backoff, discards
    non-text/HTML responses before parsing, and attaches
    provenance/license/TDM-rights metadata to every successful fetch."""

    def __init__(self, user_agent: str, deny_domains: Optional[list] = None,
                 default_delay: float = 2.0, timeout: int = 15,
                 check_tdm_reservation: bool = True,
                 max_retries: int = 2, backoff_seconds: float = 2.0,
                 allowed_content_types: Optional[set] = None):
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
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = backoff_seconds
        self.allowed_content_types = allowed_content_types or set(DEFAULT_ALLOWED_CONTENT_TYPES)

    def _is_denied(self, url: str) -> bool:
        domain = urlparse(url).netloc.lower()
        return any(domain == d or domain.endswith("." + d) for d in self.deny_domains)

    def _content_type_allowed(self, content_type: str) -> bool:
        # Strip "; charset=..." or similar parameters before comparing.
        base = content_type.split(";", 1)[0].strip().lower()
        if not base:
            # Missing header: don't block on it, some servers just omit it.
            return True
        return base in self.allowed_content_types

    def _request_with_retries(self, url: str):
        """Returns (response, attempts) on success, or raises the last
        requests.RequestException after exhausting retries. A response
        with a non-retryable status code (anything but the 5xx set) is
        returned immediately without consuming retries."""
        last_exc = None
        attempts = 0
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                resp = requests.get(
                    url, timeout=self.timeout,
                    headers={"User-Agent": self.user_agent},
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    sleep_for = self.backoff_seconds * (2 ** attempt)
                    logger.warning(
                        "Errore di rete su %s (tentativo %d/%d): %s. Riprovo tra %.1fs.",
                        url, attempts, self.max_retries + 1, exc, sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise

            if resp.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                sleep_for = self.backoff_seconds * (2 ** attempt)
                logger.warning(
                    "HTTP %d (transitorio) su %s (tentativo %d/%d). Riprovo tra %.1fs.",
                    resp.status_code, url, attempts, self.max_retries + 1, sleep_for,
                )
                time.sleep(sleep_for)
                continue

            return resp, attempts

        # Only reachable if every attempt raised (loop exits via return or raise).
        raise last_exc

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
            resp, attempts = self._request_with_retries(url)
        except requests.RequestException as exc:
            return FetchResult(url=url, status="error", error=str(exc), attempts=self.max_retries + 1)

        if resp.status_code != 200:
            return FetchResult(
                url=url, status="error", status_code=resp.status_code,
                error=f"HTTP {resp.status_code}", attempts=attempts,
            )

        content_type = resp.headers.get("Content-Type", "")
        if not self._content_type_allowed(content_type):
            return FetchResult(
                url=url, status="skipped_content_type", status_code=resp.status_code,
                content_type=content_type, attempts=attempts,
            )

        license_info = detect_license(resp.text, url)
        tdm_reservation = tdm_combined_status(resp.text, url, self.tdm_checker)

        return FetchResult(
            url=url,
            status="ok",
            html=resp.text,
            status_code=resp.status_code,
            content_type=content_type,
            license_info=license_info,
            tdm_reservation=tdm_reservation,
            fetched_at=time.time(),
            attempts=attempts,
        )
