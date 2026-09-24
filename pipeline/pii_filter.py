"""Lightweight, dependency-free PII redaction.

This is a regex-based first line of defense: it catches the common,
structurally-recognizable categories of personal data (emails, phone
numbers, IBANs, credit-card-like numbers, IP addresses) before text
ever reaches the dataset. It is NOT a substitute for a proper NLP-based
PII/NER pass (e.g. Presidio + spaCy) on data destined for anything
beyond experimentation -- see the README for how to plug one in.

CREDIT_CARD and PHONE need more than a bare digit-count regex: a real
run against Wikipedia articles showed a naive "13-16 digits" /
"6-11 digits with optional separators" pattern redacting DOIs, ISSNs,
arXiv ids, patent application numbers, and Wikipedia's own revision-id
and ad-tracking URL parameters as if they were credit cards and phone
numbers -- encyclopedic pages are full of long numeric ids that
structurally look just like one. So both labels here run a plain regex
to find CANDIDATE spans, then validate each candidate: CREDIT_CARD
requires a real Luhn checksum pass (the same check real card issuers
use, and one a DOI/ISSN/patent number will only pass by chance); both
additionally reject a candidate that has a citation/identifier keyword
(doi, issn, isbn, arxiv, uibm, orcid, isni) ANYWHERE in the text just
before it -- not only immediately before, since a real DOI reads like
"DOI: 10.1016/j.patter.2024.101074" with the journal/article path
sitting between the keyword and the actual number -- or that is
immediately preceded by "=" (a URL query-parameter value, e.g.
"...&oldid=123456789" or "gad_campaignid=987654321", never personal
data). Still best-effort, not perfect -- see above.
"""
from __future__ import annotations

import re
from typing import Tuple

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Candidate spans only -- see module docstring. The (?<!\d)/(?!\d)
# boundaries matter: without them a long digit run (e.g. a 12-digit
# arXiv/patent number) gets only PARTIALLY consumed by the fixed-width
# groups below, leaving stray leftover digits next to the redaction
# marker instead of either redacting the whole thing or none of it.
CREDIT_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# Separators are space/tab only ([ \t], NOT the full \s class): \s also
# matches newlines, which let this regex bridge across unrelated table
# cells or lines (e.g. a version fragment on one line plus a year range
# on the next -- "1.2\n1995-1999" -- got read as one 9-digit "phone
# number", swallowing the whole next line into the redaction). A real
# phone number is always written on a single line, so restricting to
# intra-line separators only removes that whole class of false positive
# without affecting genuine matches (which never spanned lines anyway).
PHONE_CANDIDATE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[ \t.-]?)?(?:\(?\d{2,4}\)?[ \t.-]?)?\d{3,4}[ \t.-]?\d{3,4}(?!\d)"
)

# Bibliographic/identifier keywords that commonly appear near a long
# number in encyclopedic and academic text: DOI, ISSN, ISBN, arXiv ids,
# patent application numbers, ORCID/ISNI researcher/author ids, academic
# database/repository ids (ProQuest, JSTOR, PMID, Handle System), and
# library authority-control ids (LCCN, GND, VIAF, SBN, BNF, NDL, BNE,
# J9U -- the systems listed in Wikipedia's "Controllo di autorita'"/
# "Authority control" sidebar template), plus Bibcode/PMC from journal
# citation footers. Each addition here was found the same way: a real
# page (a Wikipedia reference list, an authority-control box) with an
# identifier from that system, structurally identical to a phone number
# or credit card once the keyword isn't recognized.
# Checked in BOTH directions (see _preceded_by/_followed_by below), not
# anchored to immediate adjacency: a real citation reads "DOI:
# 10.1016/j.patter.2024.101074" (keyword before, with a journal path in
# between) but also "374 20150363. Bibcode: ... doi: ... PMID: ..."
# (the identifying keywords all come AFTER the number, cross-referencing
# the same citation) or "LCCN (EN) sh85037298" (keyword immediately
# before). Checked case-insensitively.
_CITATION_CONTEXT_RE = re.compile(r"(doi|issn|isbn|arxiv|uibm|orcid|isni|proquest|jstor|pmid|hdl|lccn|gnd|viaf|sbn|bnf|ndl|bne|j9u|bibcode|pmc)", re.IGNORECASE)

# How far to look for a citation keyword (in either direction) -- long
# enough to cover "DOI\n:\n10.1016/j.compbiomed." (journal abbreviations
# vary in length) without being so long it starts matching unrelated
# text several sentences away.
_CITATION_CONTEXT_WINDOW = 60


def _preceded_by_citation_context(full_text: str, start: int,
                                   window: int = _CITATION_CONTEXT_WINDOW) -> bool:
    return bool(_CITATION_CONTEXT_RE.search(full_text[max(0, start - window):start]))


def _followed_by_citation_context(full_text: str, end: int,
                                   window: int = _CITATION_CONTEXT_WINDOW) -> bool:
    """Same idea as _preceded_by_citation_context but looking forward:
    a citation's identifying keywords don't always come before the
    number. A real example: "...374 20150363. Bibcode: 2016RSPTA...
    doi: 10.1098/rsta.2016.0360. ISSN 1364-503X. PMID 28336805." -- the
    article number ("20150363") has no keyword before it at all, only a
    cluster of them (Bibcode/doi/ISSN/PMID) right after, all describing
    the same citation."""
    return bool(_CITATION_CONTEXT_RE.search(full_text[end:end + window]))


def _preceded_by_equals_sign(full_text: str, start: int) -> bool:
    """True if the match is the value side of a "key=value" pair, once
    whitespace/newlines between the key and the value are skipped (the
    text extractor inserts a newline per HTML element, so a URL like
    "...&oldid=123456789" can end up as "oldid\n=\n123456789"). Such a
    value is a URL parameter -- a page revision id, an ad-tracking
    click id, ... -- never personal data."""
    i = start - 1
    while i >= 0 and full_text[i].isspace():
        i -= 1
    return i >= 0 and full_text[i] == "="


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_real_credit_card(match: "re.Match[str]") -> bool:
    digits = re.sub(r"\D", "", match.group())
    if len(digits) < 13 or len(digits) > 19:
        return False
    if _preceded_by_citation_context(match.string, match.start()):
        return False
    if _followed_by_citation_context(match.string, match.end()):
        return False
    if _preceded_by_equals_sign(match.string, match.start()):
        return False
    return _luhn_valid(digits)


def _is_real_phone(match: "re.Match[str]") -> bool:
    digits = re.sub(r"\D", "", match.group())
    if len(digits) < 9 or len(digits) > 13:
        return False
    if _preceded_by_citation_context(match.string, match.start()):
        return False
    if _followed_by_citation_context(match.string, match.end()):
        return False
    if _preceded_by_equals_sign(match.string, match.start()):
        return False
    return True


def redact_pii(text: str) -> Tuple[str, dict]:
    """Returns (redacted_text, counts) where counts maps label -> n found."""
    counts = {"EMAIL": 0, "IBAN": 0, "CREDIT_CARD": 0, "IPV4": 0, "PHONE": 0}
    redacted = text

    def _sub_email(m):
        counts["EMAIL"] += 1
        return "[REDACTED_EMAIL]"

    redacted = EMAIL_PATTERN.sub(_sub_email, redacted)

    def _sub_iban(m):
        counts["IBAN"] += 1
        return "[REDACTED_IBAN]"

    redacted = IBAN_PATTERN.sub(_sub_iban, redacted)

    def _sub_credit_card(m):
        if _is_real_credit_card(m):
            counts["CREDIT_CARD"] += 1
            return "[REDACTED_CREDIT_CARD]"
        return m.group()  # candidate rejected -- leave the original text alone

    redacted = CREDIT_CARD_CANDIDATE.sub(_sub_credit_card, redacted)

    def _sub_ipv4(m):
        counts["IPV4"] += 1
        return "[REDACTED_IPV4]"

    redacted = IPV4_PATTERN.sub(_sub_ipv4, redacted)

    # PHONE runs last and is deliberately the broadest pattern, so more
    # specific labels above get first claim on a span.
    def _sub_phone(m):
        if _is_real_phone(m):
            counts["PHONE"] += 1
            return "[REDACTED_PHONE]"
        return m.group()

    redacted = PHONE_CANDIDATE.sub(_sub_phone, redacted)

    return redacted, counts
