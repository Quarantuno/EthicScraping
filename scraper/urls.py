"""URL helpers shared by the crawler and the fetcher."""
from __future__ import annotations

from typing import Optional
from urllib.parse import unquote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

# Query parameters that only exist to track where a click came from and
# never change which page is served.
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "igshid", "mkt_tok",
}

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication and the crawl state:
    lowercase scheme and host, no default port, no fragment, no
    tracking parameters, empty path -> "/", no user:password@. Other
    query parameters are kept byte-for-byte as they were, in their
    original order: re-encoding or reordering them can, occasionally,
    change what a server returns."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        # Malformed port in some random href: don't crash the crawl over
        # it, just keep the URL as-is minus its fragment.
        return url.strip().split("#", 1)[0]
    if ":" in host:
        host = f"[{host}]"  # IPv6 literal
    netloc = host  # built from hostname only: drops any user:password@
    if port and _DEFAULT_PORTS.get(scheme) != port:
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    kept = []
    for piece in parsed.query.split("&") if parsed.query else []:
        key = unquote(piece.split("=", 1)[0]).lower()
        if key in TRACKING_PARAMS or key.startswith(TRACKING_PARAM_PREFIXES):
            continue
        kept.append(piece)
    query = "&".join(kept)
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))



def extract_canonical(html: Optional[str], base_url: str) -> Optional[str]:
    """The page's own <link rel="canonical">, resolved against base_url
    and normalized, or None if it has none (or a non-http(s) one).
    Recorded as provenance, and used by the dataset writer so the same
    article published under two URLs (print views, ?page=1, mirrors on
    the same site...) isn't kept twice."""
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        if "canonical" in [r.lower() for r in rel]:
            absolute = urljoin(base_url, link["href"].strip())
            if absolute.startswith(("http://", "https://")):
                return normalize_url(absolute)
            return None
    return None
