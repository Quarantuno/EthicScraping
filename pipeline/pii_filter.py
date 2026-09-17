"""Lightweight, dependency-free PII redaction.

This is a regex-based first line of defense: it catches the common,
structurally-recognizable categories of personal data (emails, phone
numbers, IBANs, credit-card-like numbers, IP addresses) before text
ever reaches the dataset. It is NOT a substitute for a proper NLP-based
PII/NER pass (e.g. Presidio + spaCy) on data destined for anything
beyond experimentation -- see the README for how to plug one in.
"""
from __future__ import annotations

import re
from typing import Tuple

PATTERNS = {
    "EMAIL": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "PHONE": re.compile(
        r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}"
    ),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "IPV4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
}

# PHONE is deliberately broad and can over-match; run it last so more
# specific patterns (IBAN, credit card) get first claim on a span.
ORDERED_LABELS = ["EMAIL", "IBAN", "CREDIT_CARD", "IPV4", "PHONE"]


def redact_pii(text: str) -> Tuple[str, dict]:
    """Returns (redacted_text, counts) where counts maps label -> n found."""
    counts = {label: 0 for label in ORDERED_LABELS}
    redacted = text

    for label in ORDERED_LABELS:
        pattern = PATTERNS[label]

        def _sub(match: re.Match, label=label) -> str:
            counts[label] += 1
            return f"[REDACTED_{label}]"

        redacted = pattern.sub(_sub, redacted)

    return redacted, counts
