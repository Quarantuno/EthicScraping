"""Best-effort detection of a page's license / reuse terms.

This is deliberately conservative: if we can't find a positive signal
that reuse is permitted, we report the license as unknown rather than
guessing "open". Downstream, the pipeline can be configured to keep
only records with a known, permissive license.
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup

CC_PATTERN = re.compile(r"creativecommons\.org/licenses/([a-z\-]+)/([\d.]+)", re.I)
PUBLIC_DOMAIN_PATTERN = re.compile(r"creativecommons\.org/publicdomain", re.I)

KNOWN_OPEN_DOMAINS = {
    "wikipedia.org": "CC BY-SA 4.0 (Wikipedia)",
    "wikimedia.org": "CC BY-SA 4.0 (Wikimedia)",
    "gutenberg.org": "Public Domain (Project Gutenberg)",
}


def _find_rel_license(soup: BeautifulSoup):
    """rel="license" is valid on both <a> (body) and <link> (head)."""
    return soup.find(["a", "link"], attrs={"rel": "license"})


def detect_license(html: str, url: str) -> dict:
    """Returns {"license", "source", "confidence"}.
    confidence is one of "high", "medium", "unknown".
    """
    for domain, label in KNOWN_OPEN_DOMAINS.items():
        if domain in url:
            return {"license": label, "source": "known_domain", "confidence": "high"}

    soup = BeautifulSoup(html, "lxml")

    rel_license = _find_rel_license(soup)
    if rel_license and rel_license.get("href"):
        href = rel_license["href"]
        if PUBLIC_DOMAIN_PATTERN.search(href):
            return {"license": "Public Domain", "source": "rel_license_link",
                     "confidence": "high"}
        m = CC_PATTERN.search(href)
        if m:
            return {"license": f"CC {m.group(1).upper()} {m.group(2)}",
                     "source": "rel_license_link", "confidence": "high"}
        return {"license": href, "source": "rel_license_link", "confidence": "medium"}

    meta_license = soup.find("meta", attrs={"name": re.compile("license", re.I)})
    if meta_license and meta_license.get("content"):
        return {"license": meta_license["content"], "source": "meta_tag",
                 "confidence": "medium"}

    body_text = soup.get_text(" ", strip=True)[:5000]
    m = CC_PATTERN.search(body_text)
    if m:
        return {"license": f"CC {m.group(1).upper()} {m.group(2)}",
                 "source": "body_text_match", "confidence": "medium"}

    return {"license": None, "source": None, "confidence": "unknown"}
