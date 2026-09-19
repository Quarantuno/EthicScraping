"""Computes human-readable statistics for an existing dataset JSONL file
(record counts, word-count distribution, top domains, license mix, PII
redaction totals, residual TDM reservations), used by `main.py stats`.

Reuses the domain/license/PII/TDM breakdowns already implemented for the
AI Act training-data-summary generator (compliance/generate_training_summary.py)
rather than duplicating that logic -- those are generic dataset
statistics, not only useful for the compliance summary.
"""
from __future__ import annotations

import statistics
from typing import List

from compliance.generate_training_summary import (
    domain_breakdown,
    license_breakdown,
    pii_redaction_totals,
    tdm_reservation_stats,
)


def word_count_stats(records: List[dict]) -> dict:
    counts = [r.get("word_count", 0) for r in records if r.get("word_count") is not None]
    if not counts:
        return {"count": 0, "min": 0, "max": 0, "mean": 0.0, "median": 0.0, "total": 0}
    return {
        "count": len(counts),
        "min": min(counts),
        "max": max(counts),
        "mean": round(statistics.mean(counts), 1),
        "median": statistics.median(counts),
        "total": sum(counts),
    }


def compute_stats(records: List[dict], top_percent: float = 10.0) -> dict:
    empty_domains = {"total_domains": 0, "total_records": 0, "top": [],
                      "remaining_domains": 0, "remaining_records": 0}
    return {
        "total_records": len(records),
        "word_counts": word_count_stats(records),
        "domains": domain_breakdown(records, top_percent) if records else empty_domains,
        "licenses": license_breakdown(records) if records else [],
        "pii": pii_redaction_totals(records) if records else {},
        "tdm": tdm_reservation_stats(records) if records else
               {"records_with_tdm_reservation_flag": 0, "total_records": 0},
    }


def render_text(stats: dict, top_n_domains: int = 10) -> str:
    lines = [f"Record totali: {stats['total_records']}"]
    if stats["total_records"] == 0:
        return "\n".join(lines)

    wc = stats["word_counts"]
    lines.append(
        f"Parole per record: min={wc['min']}  media={wc['mean']}  "
        f"mediana={wc['median']}  max={wc['max']}  totale={wc['total']}"
    )

    domains = stats["domains"]
    lines.append("")
    lines.append(f"Domini distinti: {domains['total_domains']}")
    shown = domains["top"][:top_n_domains]
    lines.append(f"Top {len(shown)} domini per numero di record:")
    for row in shown:
        lines.append(f"  {row['domain']:<40} {row['records']:>6}  ({row['share']}%)")
    if domains["remaining_domains"] > 0:
        lines.append(
            f"  ... e altri {domains['remaining_domains']} domini "
            f"({domains['remaining_records']} record)"
        )

    lines.append("")
    lines.append("Licenze:")
    for label, n in stats["licenses"]:
        lines.append(f"  {label:<45} {n:>6}")

    lines.append("")
    pii = stats["pii"]
    if pii:
        lines.append("PII redatte (totali per tipo):")
        for label, n in sorted(pii.items()):
            lines.append(f"  {label:<20} {n:>6}")
    else:
        lines.append("PII redatte: nessuna")

    tdm = stats["tdm"]
    lines.append("")
    lines.append(
        f"Record con reservation TDM residua: "
        f"{tdm['records_with_tdm_reservation_flag']} / {tdm['total_records']}"
    )

    return "\n".join(lines)
