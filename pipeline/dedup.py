"""Exact-content deduplication via normalized text hashing.

Simple by design for phase 1: catches identical/near-identical pages
(the common case when a seed and its own mirrors get crawled). A
near-duplicate detector (simhash/minhash) can be added later for
fuzzier matches without changing the interface below.
"""
from __future__ import annotations

import hashlib
import re


def normalize_for_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return normalized


def content_hash(text: str) -> str:
    normalized = normalize_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Deduplicator:
    def __init__(self):
        self._seen: set[str] = set()

    def is_duplicate(self, text: str) -> bool:
        h = content_hash(text)
        if h in self._seen:
            return True
        self._seen.add(h)
        return False
