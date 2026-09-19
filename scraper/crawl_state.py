"""Persisted crawl state so `main.py run` can be interrupted (or simply
run again later) without re-fetching pages it already visited.

The state file is a flat JSON document: {"visited": [url, ...]}. It
intentionally does not track per-domain page counts: `max_pages_per_domain`
still applies per invocation, so a resumed run fetches up to that many
*new* pages per seed even if earlier runs already covered others.

A URL that ended in "error" (network failure, non-200 status, exhausted
retries) is deliberately NOT recorded as visited, so a transient failure
today gets retried automatically on the next run instead of being
skipped forever.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional, Set

logger = logging.getLogger("scrapellm.crawl_state")


class CrawlState:
    def __init__(self, state_path: Optional[str]):
        self.state_path = state_path
        self.visited: Set[str] = set()
        if state_path and os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.visited = set(data.get("visited", []))
                logger.info(
                    "Stato di crawling caricato da %s: %d URL gia' visitati.",
                    state_path, len(self.visited),
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "Impossibile leggere lo stato di crawling da %s (%s); riparto da zero.",
                    state_path, exc,
                )

    def is_visited(self, url: str) -> bool:
        return url in self.visited

    def mark_visited(self, url: str) -> None:
        self.visited.add(url)
        self._save()

    def _save(self) -> None:
        if not self.state_path:
            return
        parent = os.path.dirname(self.state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{self.state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"visited": sorted(self.visited)}, f, indent=2)
        os.replace(tmp_path, self.state_path)
