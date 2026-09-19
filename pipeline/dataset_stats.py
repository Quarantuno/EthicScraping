"""Computes human-readable statistics for an existing dataset JSONL file
(record counts, word-count distribution, top domains, domain
concentration, license mix, language mix, text-quality distribution, PII
redaction totals, residual TDM reservations), used by `main.py stats`.

Reuses the domain/license/PII/TDM breakdowns already implemented for the
AI Act training-data-summary generator (compliance/generate_training_summary.py)
rather than duplicating that logic -- those are generic dataset
statistics, not only useful for the compliance summary.

`language` and `quality_score` are recent additions to the record schema
(pipeline/dataset_writer.py); records written before those existed
simply won't have the keys, so every stat here treats a missing key the
same as an explicit None/unknown rather than crashing on older datasets.
"""
from __future__ import annotations

import statistics
from collections import Counter
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


def quality_score_stats(records: List[dict]) -> dict:
    """Distribution of the `quality_score` field (0-1, higher is
    better) written by pipeline/quality_filter.py. Older datasets
    written before this field existed simply contribute no samples."""
    scores = [r.get("quality_score") for r in records if r.get("quality_score") is not None]
    if not scores:
        return {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0}
    return {
        "count": len(scores),
        "min": round(min(scores), 3),
        "max": round(max(scores), 3),
        "mean": round(statistics.mean(scores), 3),
        "median": round(statistics.median(scores), 3),
    }


def language_breakdown(records: List[dict]) -> "list[tuple[str, int]]":
    """Groups records by detected language; missing/None language
    (langdetect not installed, detection failed, or an older dataset
    written before this field existed) is reported as "sconosciuta"."""
    counts = Counter(r.get("language") or "sconosciuta" for r in records)
    return counts.most_common()


def domain_concentration_index(records: List[dict]) -> float:
    """Herfindahl-Hirschman-style concentration index over ALL domains
    (not just the "top" ones): sum of each domain's squared share of the
    dataset. Close to 0 = spread across many domains; close to 1 = a
    single domain dominates. A simple, dependency-free proxy for how
    diverse the dataset's sources actually are, beyond just counting
    distinct domains."""
    counts = Counter(r.get("domain") or "unknown" for r in records)
    total = sum(counts.values())
    if not total:
        return 0.0
    return round(sum((c / total) ** 2 for c in counts.values()), 4)


def compute_stats(records: List[dict], top_percent: float = 10.0) -> dict:
    empty_domains = {"total_domains": 0, "total_records": 0, "top": [],
                      "remaining_domains": 0, "remaining_records": 0}
    return {
        "total_records": len(records),
        "word_counts": word_count_stats(records),
        "quality": quality_score_stats(records),
        "domains": domain_breakdown(records, top_percent) if records else empty_domains,
        "domain_concentration_index": domain_concentration_index(records) if records else 0.0,
        "languages": language_breakdown(records) if records else [],
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

    quality = stats["quality"]
    lines.append("")
    if quality["count"] > 0:
        lines.append(
            f"Punteggio qualita' testo (0-1, piu' alto e' meglio) su "
            f"{quality['count']} record: min={quality['min']}  media={quality['mean']}  "
            f"mediana={quality['median']}  max={quality['max']}"
        )
    else:
        lines.append(
            "Punteggio qualita' testo: non disponibile per nessun record "
            "(dataset generato prima di questa funzionalita')."
        )

    domains = stats["domains"]
    lines.append("")
    lines.append(f"Domini distinti: {domains['total_domains']}")
    lines.append(
        f"Indice di concentrazione domini (HHI, 0=molto distribuito, "
        f"1=un solo dominio): {stats['domain_concentration_index']}"
    )
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
    lines.append("Lingue rilevate:")
    for label, n in stats["languages"]:
        lines.append(f"  {label:<20} {n:>6}")

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
