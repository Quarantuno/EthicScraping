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
arXiv ids and patent application numbers as if they were credit cards
and phone numbers -- encyclopedic and academic text is full of long
citation numbers that structurally look just like one. So both labels
here run a plain regex to find CANDIDATE spans, then validate each
candidate: CREDIT_CARD requires a real Luhn checksum pass (the same
check real card issuers use, and one a DOI/ISSN/patent number will only
pass by chance), and both additionally reject a candidate immediately
preceded by a known citation/identifier keyword (doi, issn, isbn,
arxiv, uibm, orcid, isni). Still best-effort, not perfect -- see above.
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
PHONE_CANDIDATE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}(?!\d)"
)

# Bibliographic/identifier keywords that commonly precede a long number
# in encyclopedic and academic text (DOI, ISSN, ISBN, arXiv ids, patent
# application numbers, ORCID/ISNI researcher/author ids). A number
# immediately preceded by one of these is essentially never a real
# phone number or credit card. Checked case-insensitively in a short
# window before the candidate match.
_CITATION_CONTEXT_RE = re.compile(
    r"(doi|issn|isbn|arxiv|uibm|orcid|isni)\s*[:.\-]?\s*$", re.IGNORECASE
)


def _preceded_by_citation_context(full_text: str, start: int, window: int = 20) -> bool:
    return bool(_CITATION_CONTEXT_RE.search(full_text[max(0, start - window):start]))


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
    return _luhn_valid(digits)


def _is_real_phone(match: "re.Match[str]") -> bool:
    digits = re.sub(r"\D", "", match.group())
    if len(digits) < 9 or len(digits) > 13:
        return False
    if _preceded_by_citation_context(match.string, match.start()):
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
