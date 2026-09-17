"""Per-domain politeness rate limiting.

Even when robots.txt gives no explicit crawl-delay, we never hammer a
domain: a sane default delay is always applied between requests to the
same host.
"""
from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlparse


class RateLimiter:
    def __init__(self, default_delay: float = 2.0):
        self.default_delay = default_delay
        self._last_request: dict[str, float] = {}
        self._domain_delay: dict[str, float] = {}

    def set_delay(self, url: str, delay: float) -> None:
        """Let a site's own robots.txt crawl-delay override our default
        (only ever making us slower, never faster)."""
        domain = urlparse(url).netloc
        current = self._domain_delay.get(domain, self.default_delay)
        self._domain_delay[domain] = max(current, delay)

    def wait(self, url: str) -> None:
        domain = urlparse(url).netloc
        delay = self._domain_delay.get(domain, self.default_delay)
        last = self._last_request.get(domain)
        if last is not None:
            elapsed = time.time() - last
            remaining = delay - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[domain] = time.time()
