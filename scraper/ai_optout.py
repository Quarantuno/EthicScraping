"""Detects explicit "don't use this for AI" opt-out signals sent through
the robots meta tag or the X-Robots-Tag HTTP header.

    <meta name="robots" content="noai, noimageai">
    X-Robots-Tag: noai

`noai` / `noimageai` are not part of any formal standard (they were
popularized by art-hosting sites in 2022 and are now used much more
widely), and unlike TDMRep (see tdm_rights.py) they have no specific
legal standing under Art. 4(3) of Directive (EU) 2019/790. But they are
an unambiguous, machine-readable statement of what the publisher wants,
and the whole point of this project is to respect what people want done
with their content, not just the legal minimum. So:

- `noai` means: don't use this page's content for AI training. It is
  what the text pipeline acts on (see DatasetWriter's respect_noai).
- `noimageai` is detected and reported too, but it only concerns
  images -- this pipeline only ever keeps text, so on its own it doesn't
  cause a page to be dropped.

Deliberately conservative interpretation: a directive scoped to a
specific crawler ("otherbot: noai", or a <meta name="otherbot">) is
honored as well, even though strictly it only addresses that other bot.
The publisher has expressed an AI-training opt-out; which AI crawler
they happened to name isn't a loophole worth using.

"none" (= noindex, nofollow) is a search-indexing directive, not an AI
opt-out, and is not treated as one here. The crawler separately honors
"nofollow" (see crawler.py).
"""
from __future__ import annotations

from typing import Iterable, Optional, Set

from bs4 import BeautifulSoup

AI_OPTOUT_DIRECTIVES = {"noai"}
DETECTED_DIRECTIVES = {"noai", "noimageai"}


def parse_directives(value: Optional[str]) -> Set[str]:
    """Splits a robots directive list ("noindex, noai" or, for headers,
    "otherbot: noai, nofollow") into a set of lowercase directive names,
    dropping any "<user-agent>:" scoping prefix."""
    if not value or not isinstance(value, str):
        return set()
    directives = set()
    for part in value.split(","):
        token = part.strip().lower()
        if ":" in token:
            # "otherbot: noai" -> "noai". Values like "unavailable_after:
            # <date>" lose their argument here, which is fine: we only
            # look for bare directive names.
            token = token.split(":", 1)[1].strip()
        # A header may also carry several space-separated directives.
        for sub in token.split():
            if sub:
                directives.add(sub)
    return directives


def _is_robots_meta_name(name: str) -> bool:
    name = name.strip().lower()
    return name == "robots" or name.endswith("bot")


def meta_directives(html: Optional[str]) -> Set[str]:
    if not html:
        return set()
    soup = BeautifulSoup(html, "lxml")
    found: Set[str] = set()
    for tag in soup.find_all("meta", attrs={"name": True}):
        if _is_robots_meta_name(tag.get("name", "")):
            found |= parse_directives(tag.get("content"))
    return found


def _status(opted_out: bool, directives: Iterable[str] = (), source: Optional[str] = None) -> dict:
    return {
        "opted_out": opted_out,
        "directives": sorted(set(directives) & DETECTED_DIRECTIVES),
        "source": source,
    }


def combined_status(html: Optional[str], x_robots_tag: Optional[str] = None) -> dict:
    """Header first (cheaper, and sent before the body), then meta tags.
    `opted_out` is True only for `noai`; `directives` lists every
    AI-related directive found (including `noimageai`)."""
    header = parse_directives(x_robots_tag) & DETECTED_DIRECTIVES
    meta = meta_directives(html) & DETECTED_DIRECTIVES
    all_found = header | meta

    if header & AI_OPTOUT_DIRECTIVES:
        return _status(True, all_found, "x_robots_tag")
    if meta & AI_OPTOUT_DIRECTIVES:
        return _status(True, all_found, "meta_robots")
    if all_found:
        return _status(False, all_found, "x_robots_tag" if header else "meta_robots")
    return _status(False)
