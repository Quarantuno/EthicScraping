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
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import urlparse

import requests

from .robots import RobotsChecker
from .rate_limiter import RateLimiter
from .license_detector import detect_license
from .tdm_rights import TdmRepChecker, combined_status as tdm_combined_status
from .ai_optout import combined_status as ai_optout_status

logger = logging.getLogger(__name__)

# Content types we know how to treat as text for the pipeline. Anything
# else (PDFs, images, JSON APIs, video, ...) is discarded before we ever
# try to decode/parse it as HTML.
DEFAULT_ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}

# Retries only make sense for transient failures: connection resets,
# timeouts, 5xx server errors, and 429 Too Many Requests (the server
# explicitly asking us to slow down and come back later). A 4xx like
# 404/403, or a robots/deny-list block, is not going to change if we
# ask again.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Status codes for which a Retry-After header is meaningful (RFC 9110
# section 10.2.3).
RETRY_AFTER_STATUS_CODES = {429, 503}


def parse_retry_after(value, now: Optional[float] = None) -> Optional[float]:
    """Parses a Retry-After header value into a number of seconds to
    wait. Accepts both forms allowed by RFC 9110: delay-seconds ("120")
    and an HTTP-date ("Wed, 21 Oct 2015 07:28:00 GMT"). Returns None if
    the header is missing or unparseable (callers then fall back to our
    own exponential backoff)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


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
    # Explicit "noai" opt-out (meta robots / X-Robots-Tag), see
    # scraper/ai_optout.py. None when the page wasn't fetched OK.
    ai_optout: Optional[dict] = None
    # Raw X-Robots-Tag header, kept so the crawler can honor "nofollow"
    # sent as a header rather than a meta tag.
    x_robots_tag: Optional[str] = None
    fetched_at: Optional[float] = None
    error: Optional[str] = None
    attempts: int = 1


class EthicalFetcher:
    """Wraps requests.get with robots.txt + deny-list + rate-limit checks,
    retries transient failures with exponential backoff (honoring the
    server's own Retry-After when it sends one), discards non-text/HTML
    responses before parsing, and attaches provenance/license/TDM-rights/
    AI-opt-out metadata to every successful fetch."""

    def __init__(self, user_agent: str, deny_domains: Optional[list] = None,
                 default_delay: float = 2.0, timeout: int = 15,
                 check_tdm_reservation: bool = True,
                 max_retries: int = 2, backoff_seconds: float = 2.0,
                 allowed_content_types: Optional[set] = None,
                 max_retry_after_seconds: float = 120.0):
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
        # If a server asks us to wait longer than this, we don't sit
        # blocked for an hour: we give up on that URL for this run
        # (reported as an error with the real status code) -- but the
        # domain's delay is still raised, so we stay slower with it.
        self.max_retry_after_seconds = max_retry_after_seconds

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

    def _slow_down_domain(self, url: str, retry_after: Optional[float]) -> None:
        """A 429/503 is the server telling us we're going too fast: from
        now on, wait at least as long as it asked (capped), or double our
        current delay if it didn't say. RateLimiter.set_delay only ever
        makes us slower, never faster."""
        if retry_after is not None:
            new_delay = min(retry_after, self.max_retry_after_seconds)
        else:
            new_delay = min(self.rate_limiter.get_delay(url) * 2,
                            self.max_retry_after_seconds)
        self.rate_limiter.set_delay(url, new_delay)
        logger.info("Rallento su %s: nuovo intervallo minimo tra richieste %.1fs.",
                    urlparse(url).netloc, self.rate_limiter.get_delay(url))

    @staticmethod
    def _header(resp, name: str):
        headers = getattr(resp, "headers", None)
        if not isinstance(headers, dict) and not hasattr(headers, "items"):
            return None
        try:
            value = headers.get(name)
        except Exception:
            return None
        return value if isinstance(value, str) else None

    def _request_with_retries(self, url: str):
        """Returns (response, attempts) on success, or raises the last
        requests.RequestException after exhausting retries. A response
        with a non-retryable status code is returned immediately without
        consuming retries; a retryable one whose Retry-After exceeds
        max_retry_after_seconds is also returned immediately (we stop
        retrying rather than block the whole run)."""
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

            if resp.status_code in RETRYABLE_STATUS_CODES:
                retry_after = None
                if resp.status_code in RETRY_AFTER_STATUS_CODES:
                    retry_after = parse_retry_after(self._header(resp, "Retry-After"))
                    self._slow_down_domain(url, retry_after)

                if attempt >= self.max_retries:
                    return resp, attempts
                if retry_after is not None and retry_after > self.max_retry_after_seconds:
                    logger.warning(
                        "HTTP %d su %s con Retry-After=%.0fs (oltre il massimo di %.0fs): "
                        "rinuncio a questo URL per questa run.",
                        resp.status_code, url, retry_after, self.max_retry_after_seconds,
                    )
                    return resp, attempts

                backoff = self.backoff_seconds * (2 ** attempt)
                sleep_for = max(backoff, retry_after) if retry_after is not None else backoff
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

        content_type = self._header(resp, "Content-Type") or ""
        if not self._content_type_allowed(content_type):
            return FetchResult(
                url=url, status="skipped_content_type", status_code=resp.status_code,
                content_type=content_type, attempts=attempts,
            )

        x_robots_tag = self._header(resp, "X-Robots-Tag")
        license_info = detect_license(resp.text, url)
        tdm_reservation = tdm_combined_status(resp.text, url, self.tdm_checker)
        ai_optout = ai_optout_status(resp.text, x_robots_tag)

        return FetchResult(
            url=url,
            status="ok",
            html=resp.text,
            status_code=resp.status_code,
            content_type=content_type,
            license_info=license_info,
            tdm_reservation=tdm_reservation,
            ai_optout=ai_optout,
            x_robots_tag=x_robots_tag,
            fetched_at=time.time(),
            attempts=attempts,
        )
