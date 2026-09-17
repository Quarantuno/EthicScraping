"""Detects EU rights reservations for text and data mining (TDM) under
Article 4(3) of Directive (EU) 2019/790 (the "DSM Copyright Directive").

Article 4 lets anyone -- including an AI training pipeline -- reproduce
and extract lawfully accessible copyrighted content for TDM purposes,
UNLESS the rightsholder has "expressly reserved" that right "in an
appropriate manner, such as machine-readable means in the case of
content made publicly available online". This module checks for that
machine-readable signal, using the TDMRep specification
(https://www.edrlab.org/open-standards/tdmrep/), which is the emerging
standard for it and is deliberately NOT robots.txt (TDMRep's own
rationale: robots.txt was built for search-engine indexing, not
per-purpose AI-training opt-outs, and would require enumerating every
possible crawler user agent).

This is a best-effort technical signal, not a legal determination: it
tells you a machine-readable opt-out was (or wasn't) found, not whether
your specific use is lawful. That still needs a human -- ideally a
lawyer -- for anything beyond "does the code respect the signals it can
find". See COMPLIANCE.md for the broader picture (GDPR, AI Act).

Two signal sources are checked:
1. HTML `<meta name="tdm-reservation" content="1">` in the page itself
   -- free, no extra request, checked on content already fetched.
2. `/.well-known/tdmrep.json` at the domain root -- one extra request
   per domain, cached like robots.txt.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


def _status(reserved: bool, policy_url: Optional[str] = None,
            source: Optional[str] = None) -> dict:
    return {
        "reserved": reserved,
        "policy_url": policy_url if reserved else None,
        "source": source if reserved else None,
    }


def check_meta_tag(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("meta", attrs={"name": "tdm-reservation"})
    if tag is None:
        return _status(False)

    content = (tag.get("content") or "").strip().lower()
    reserved = content in ("1", "true", "yes")
    if not reserved:
        return _status(False)

    policy_tag = soup.find("meta", attrs={"name": "tdm-policy"})
    policy_url = policy_tag.get("content") if policy_tag else None
    return _status(True, policy_url=policy_url, source="meta_tag")


class TdmRepChecker:
    """Fetches and caches /.well-known/tdmrep.json per domain (same
    caching pattern as scraper/robots.py's RobotsChecker)."""

    def __init__(self, user_agent: str, timeout: int = 10):
        self.user_agent = user_agent
        self.timeout = timeout
        self._cache: dict = {}

    def check(self, url: str) -> dict:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        if domain not in self._cache:
            self._cache[domain] = self._fetch(domain)

        return self._cache[domain]

    def _fetch(self, domain: str) -> dict:
        well_known_url = f"{domain}/.well-known/tdmrep.json"
        try:
            resp = requests.get(
                well_known_url, timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
            if resp.status_code != 200:
                return _status(False)
            data = resp.json()
        except (requests.RequestException, ValueError):
            return _status(False)

        if not isinstance(data, dict):
            return _status(False)

        reserved = bool(data.get("tdm-reservation", False))
        if not reserved:
            return _status(False)

        return _status(True, policy_url=data.get("tdm-policy"),
                       source="well_known_json")


def combined_status(html: str, url: str,
                     well_known_checker: Optional[TdmRepChecker] = None) -> dict:
    """Meta tag first (free), then the well-known JSON file if a checker
    was given (adds one request per domain, cached)."""
    meta_status = check_meta_tag(html)
    if meta_status["reserved"]:
        return meta_status

    if well_known_checker is not None:
        well_known_status = well_known_checker.check(url)
        if well_known_status["reserved"]:
            return well_known_status

    return _status(False)
