"""Turns raw HTML into clean, readable text for the dataset.

Strips scripts, styles, navigation, and other boilerplate that would
otherwise pollute training data with noise instead of signal.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

NOISE_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]


def extract_text(html: str) -> dict:
    """Returns {"title", "text", "word_count"}."""
    soup = BeautifulSoup(html, "lxml")

    for tag_name in NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    title = soup.title.get_text(strip=True) if soup.title else None

    # Prefer <main> or <article> when present; they usually hold the
    # actual content, cutting down on sidebar/menu leftovers.
    main = soup.find("main") or soup.find("article") or soup.body or soup

    text = main.get_text("\n", strip=True) if main else ""
    # Collapse repeated blank lines
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    clean_text = "\n".join(lines)

    return {
        "title": title,
        "text": clean_text,
        "word_count": len(clean_text.split()),
    }
