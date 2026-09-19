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
from .ner_pii import redact_named_entities
from .dedup import Deduplicator, content_hash
from .quality_filter import compute_quality_metrics, passes_quality_filter
from .language_detector import detect_language


class DatasetWriter:
    def __init__(self, output_path: str, min_license_confidence: Optional[str] = None,
                 min_word_count: int = 50, near_duplicate_threshold: Optional[int] = 8,
                 use_ner: bool = False, ner_model: str = "it_core_news_sm",
                 respect_tdm_optout: bool = True,
                 use_quality_filter: bool = True, min_alpha_ratio: float = 0.5,
                 min_unique_line_ratio: float = 0.4, max_long_word_ratio: float = 0.05,
                 use_language_filter: bool = False, allowed_languages: Optional[list] = None):
        """
        min_license_confidence: None (keep everything, flagged), or one of
            "medium" / "high" to DROP records whose license confidence is
            lower than the threshold. Use "high" for a strictly
            "only-clearly-licensed-content" dataset.
        near_duplicate_threshold: max Hamming distance (out of 64 bits) for
            two pages to be treated as near-duplicates and dropped. None
            disables fuzzy dedup (exact-match dedup only).
        use_ner: if True, also redacts person names / places found via
            spaCy NER, on top of the regex-based redaction. Requires spaCy
            and `ner_model` to be installed; degrades to a no-op (with a
            warning already logged by ner_pii) if they aren't.
        respect_tdm_optout: if True (default), DROP pages whose publisher
            reserved text-and-data-mining rights (Art. 4(3) Directive
            2019/790, detected by scraper/tdm_rights.py) UNLESS the page
            already has a clearly identified permissive license (license
            confidence "high" -- in that case the license itself already
            grants the needed permission). See COMPLIANCE.md.
        use_quality_filter: if True (default), DROP pages that fail the
            lightweight, dependency-free noise heuristics in
            pipeline/quality_filter.py (mostly-boilerplate/repeated
            lines, garbled/non-alphabetic text, absurdly long "words").
            Every written record still carries a `quality_score` (0-1)
            regardless of whether this filter is on.
        min_alpha_ratio / min_unique_line_ratio / max_long_word_ratio:
            thresholds for the quality filter above; see
            pipeline/quality_filter.py for what each one means.
        use_language_filter: if True, DROP pages whose detected language
            isn't in `allowed_languages`. Requires the optional
            `langdetect` package; degrades to a no-op (nothing filtered,
            `language` always None) with a logged warning if it isn't
            installed. Every written record carries a `language` field
            regardless of whether this filter is on.
        allowed_languages: list of language codes (e.g. ["it", "en"])
            used only when use_language_filter is True.
        """
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.min_license_confidence = min_license_confidence
        self.min_word_count = min_word_count
        self.dedup = Deduplicator(near_duplicate_threshold=near_duplicate_threshold)
        self.use_ner = use_ner
        self.ner_model = ner_model
        self.respect_tdm_optout = respect_tdm_optout
        self.use_quality_filter = use_quality_filter
        self.min_alpha_ratio = min_alpha_ratio
        self.min_unique_line_ratio = min_unique_line_ratio
        self.max_long_word_ratio = max_long_word_ratio
        self.use_language_filter = use_language_filter
        self.allowed_languages = set(allowed_languages or [])
        self.stats = {"written": 0, "skipped_license": 0, "skipped_duplicate": 0,
                      "skipped_too_short": 0, "skipped_fetch": 0,
                      "skipped_tdm_reservation": 0, "skipped_quality": 0,
                      "skipped_language": 0}

    _CONFIDENCE_RANK = {"unknown": 0, "medium": 1, "high": 2}

    def _passes_license_filter(self, license_info: dict) -> bool:
        if self.min_license_confidence is None:
            return True
        required = self._CONFIDENCE_RANK.get(self.min_license_confidence, 0)
        actual = self._CONFIDENCE_RANK.get(license_info.get("confidence", "unknown"), 0)
        return actual >= required

    def _blocked_by_tdm_optout(self, license_info: dict, tdm_reservation: dict) -> bool:
        if not self.respect_tdm_optout:
            return False
        if not (tdm_reservation or {}).get("reserved"):
            return False
        # A clearly identified permissive license already grants the
        # needed permission independently of the TDM exception.
        return license_info.get("confidence") != "high"

    def process(self, result: FetchResult) -> Optional[dict]:
        """Turns one successful FetchResult into a dataset record, applying
        cleaning, PII redaction, dedup, license and TDM-opt-out filtering.
        Returns the record dict if it was written, or None if it was
        skipped."""
        if result.status != "ok" or not result.html:
            self.stats["skipped_fetch"] += 1
            return None

        license_info = result.license_info or {}

        if not self._passes_license_filter(license_info):
            self.stats["skipped_license"] += 1
            return None

        if self._blocked_by_tdm_optout(license_info, result.tdm_reservation or {}):
            self.stats["skipped_tdm_reservation"] += 1
            return None

        extracted = extract_text(result.html)
        if extracted["word_count"] < self.min_word_count:
            self.stats["skipped_too_short"] += 1
            return None

        quality_metrics = compute_quality_metrics(extracted["text"])
        if self.use_quality_filter and not passes_quality_filter(
                quality_metrics,
                min_alpha_ratio=self.min_alpha_ratio,
                min_unique_line_ratio=self.min_unique_line_ratio,
                max_long_word_ratio=self.max_long_word_ratio):
            self.stats["skipped_quality"] += 1
            return None

        detected_language = detect_language(extracted["text"])
        if self.use_language_filter and self.allowed_languages and \
                detected_language not in self.allowed_languages:
            self.stats["skipped_language"] += 1
            return None

        redacted_text, pii_counts = redact_pii(extracted["text"])

        if self.use_ner:
            redacted_text, ner_counts = redact_named_entities(
                redacted_text, model_name=self.ner_model
            )
            for label, count in ner_counts.items():
                pii_counts[label] = pii_counts.get(label, 0) + count

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
            "license": license_info.get("license"),
            "license_source": license_info.get("source"),
            "license_confidence": license_info.get("confidence"),
            "tdm_reservation": (result.tdm_reservation or {}).get("reserved", False),
            "language": detected_language,
            "quality_score": quality_metrics["quality_score"],
            "pii_redactions": {k: v for k, v in pii_counts.items() if v > 0},
            "fetched_at": result.fetched_at,
            "collected_at": time.time(),
        }

        with self.output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.stats["written"] += 1
        return record
