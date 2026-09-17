"""Assembles a fetched page into a dataset record and appends it to a
JSONL file, with full provenance so every training example can be
traced back to where it came from and under what terms.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from scraper.fetcher import FetchResult

from .text_extractor import extract_text
from .pii_filter import redact_pii
from .dedup import Deduplicator, content_hash


class DatasetWriter:
    def __init__(self, output_path: str, min_license_confidence: Optional[str] = None,
                 min_word_count: int = 50):
        """
        min_license_confidence: None (keep everything, flagged), or one of
            "medium" / "high" to DROP records whose license confidence is
            lower than the threshold. Use "high" for a strictly
            "only-clearly-licensed-content" dataset.
        """
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.min_license_confidence = min_license_confidence
        self.min_word_count = min_word_count
        self.dedup = Deduplicator()
        self.stats = {"written": 0, "skipped_license": 0, "skipped_duplicate": 0,
                      "skipped_too_short": 0, "skipped_fetch": 0}

    _CONFIDENCE_RANK = {"unknown": 0, "medium": 1, "high": 2}

    def _passes_license_filter(self, license_info: dict) -> bool:
        if self.min_license_confidence is None:
            return True
        required = self._CONFIDENCE_RANK.get(self.min_license_confidence, 0)
        actual = self._CONFIDENCE_RANK.get(license_info.get("confidence", "unknown"), 0)
        return actual >= required

    def process(self, result: FetchResult) -> Optional[dict]:
        """Turns one successful FetchResult into a dataset record, applying
        cleaning, PII redaction, dedup and license filtering. Returns the
        record dict if it was written, or None if it was skipped."""
        if result.status != "ok" or not result.html:
            self.stats["skipped_fetch"] += 1
            return None

        if not self._passes_license_filter(result.license_info or {}):
            self.stats["skipped_license"] += 1
            return None

        extracted = extract_text(result.html)
        if extracted["word_count"] < self.min_word_count:
            self.stats["skipped_too_short"] += 1
            return None

        redacted_text, pii_counts = redact_pii(extracted["text"])

        if self.dedup.is_duplicate(redacted_text):
            self.stats["skipped_duplicate"] += 1
            return None

        record = {
            "id": str(uuid.uuid4()),
            "url": result.url,
            "domain": urlparse(result.url).netloc,
            "title": extracted["title"],
            "text": redacted_text,
            "word_count": len(redacted_text.split()),
            "content_hash": content_hash(redacted_text),
            "license": (result.license_info or {}).get("license"),
            "license_source": (result.license_info or {}).get("source"),
            "license_confidence": (result.license_info or {}).get("confidence"),
            "pii_redactions": {k: v for k, v in pii_counts.items() if v > 0},
            "fetched_at": result.fetched_at,
            "collected_at": time.time(),
        }

        with self.output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.stats["written"] += 1
        return record
