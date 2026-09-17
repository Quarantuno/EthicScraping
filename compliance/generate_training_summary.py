#!/usr/bin/env python3
"""Generates a Markdown draft of the "web-scraped data" parts of the EU
AI Act's mandatory training data summary for providers of general-purpose
AI models (Regulation (EU) 2024/1689 "AI Act", Article 53(1)(d); template
published by the European Commission, in effect since 2 August 2025).

This fills in a DRAFT of the pieces the official template asks about
web-scraped sources -- domain breakdown (including the "top 10% of
domains by data volume" the template requires, 5% for SMEs), license
mix, and how TDM opt-outs / PII were handled -- using what the pipeline
already tracks per record. It does NOT fill in Section 1 "General
Information" (model name/version, modalities, publication date, total
training compute, etc.): that's about the *model*, not this dataset
export, and only you know it once training is actually done.

This is engineering support, not legal advice or an official filing.
The European Commission's own template and a lawyer are the authority
on what your specific submission needs. See ../COMPLIANCE.md.

Usage:
    python compliance/generate_training_summary.py \\
        --input dataset/output_approved.jsonl \\
        --output compliance/training_data_summary.md
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import List


def load_records(path: str) -> List[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def domain_breakdown(records: List[dict], top_percent: float = 10.0) -> dict:
    counts = Counter(r.get("domain") or "unknown" for r in records)
    total = sum(counts.values())
    ranked = counts.most_common()

    cutoff = max(1, math.ceil(len(ranked) * top_percent / 100))
    top = ranked[:cutoff]
    remaining_domains = ranked[cutoff:]
    remaining_records = sum(c for _, c in remaining_domains)

    return {
        "total_domains": len(ranked),
        "total_records": total,
        "top": [
            {"domain": d, "records": c, "share": round(100 * c / total, 2) if total else 0.0}
            for d, c in top
        ],
        "remaining_domains": len(remaining_domains),
        "remaining_records": remaining_records,
    }


def license_breakdown(records: List[dict]) -> "list[tuple[str, int]]":
    counts = Counter(
        (r.get("license") or "unknown", r.get("license_confidence") or "unknown")
        for r in records
    )
    return [(f"{lic} (confidence: {conf})", n) for (lic, conf), n in counts.most_common()]


def pii_redaction_totals(records: List[dict]) -> dict:
    totals: dict = {}
    for r in records:
        for label, n in (r.get("pii_redactions") or {}).items():
            totals[label] = totals.get(label, 0) + n
    return totals


def tdm_reservation_stats(records: List[dict]) -> dict:
    reserved = sum(1 for r in records if r.get("tdm_reservation") is True)
    return {"records_with_tdm_reservation_flag": reserved, "total_records": len(records)}


def render_markdown(input_path: str, domains: dict, licenses: "list[tuple[str, int]]",
                     pii: dict, tdm: dict, top_percent: float) -> str:
    lines = []
    lines.append("# Training Data Summary (draft) — web-scraped data section")
    lines.append("")
    lines.append(
        f"Auto-generated from `{input_path}` by "
        f"`compliance/generate_training_summary.py`. Draft only, covering "
        f"the web-scraped-sources part of the EU AI Act's official GPAI "
        f"training-data-summary template — not a substitute for the "
        f"official template or legal review. See `COMPLIANCE.md`."
    )
    lines.append("")
    lines.append("## Section 2 — List of Data Sources (web-scraped subset)")
    lines.append("")
    lines.append(f"- Total records in this export: **{domains['total_records']}**")
    lines.append(f"- Distinct domains: **{domains['total_domains']}**")
    lines.append(
        f"- Top {top_percent:g}% of domains by record count (the template "
        f"asks for the top 10% of domains by data volume, or 5% if you "
        f"qualify as an SME — pass `--top-percent 5` for that):"
    )
    lines.append("")
    lines.append("| Domain | Records | Share |")
    lines.append("|---|---|---|")
    for row in domains["top"]:
        lines.append(f"| {row['domain']} | {row['records']} | {row['share']}% |")
    if domains["remaining_domains"] > 0 and domains["total_records"]:
        remaining_share = round(100 * domains["remaining_records"] / domains["total_records"], 2)
        lines.append(
            f"| *(+{domains['remaining_domains']} more domains)* | "
            f"{domains['remaining_records']} | {remaining_share}% |"
        )
    lines.append("")
    lines.append("### License breakdown")
    lines.append("")
    lines.append("| License (confidence) | Records |")
    lines.append("|---|---|")
    for label, n in licenses:
        lines.append(f"| {label} | {n} |")
    lines.append("")
    lines.append("## Section 3 — Data Processing Aspects (draft notes)")
    lines.append("")
    lines.append(
        "- **Text-and-data-mining opt-outs (Art. 4(3) Directive (EU) "
        "2019/790):** built with `respect_tdm_optout` honoring "
        "machine-readable TDM reservations (TDMRep) unless a page already "
        "carried a clearly identified permissive license. Records still "
        f"flagged `tdm_reservation: true` in this export: "
        f"**{tdm['records_with_tdm_reservation_flag']} / {tdm['total_records']}** "
        f"(should be 0 if that setting was left on for this run; a nonzero "
        f"count means it was turned off and needs manual review before use)."
    )
    lines.append(
        "- **Personal data / PII:** automated redaction applied before "
        "records were written (regex-based always; NER-based optionally). "
        "Redaction counts across this export:"
    )
    if pii:
        for label, n in sorted(pii.items()):
            lines.append(f"  - {label}: {n}")
    else:
        lines.append("  - none recorded")
    lines.append(
        "- **Licensing:** `license` / `license_source` / "
        "`license_confidence` per record come from `scraper/"
        "license_detector.py`'s best-effort detection; `unknown` means no "
        "positive signal was found, not that the content is free to use."
    )
    lines.append("")
    lines.append("## Section 1 — General Information (fill in yourself)")
    lines.append("")
    lines.append(
        "Not generated here: model name/version, modalities, publication "
        "date, training compute, language coverage, etc. belong to the "
        "model you train, not this dataset export."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Dataset JSONL (idealmente *_approved.jsonl)")
    parser.add_argument("--output", default=str(Path(__file__).parent / "training_data_summary.md"))
    parser.add_argument("--top-percent", type=float, default=10.0,
                         help="Percentuale di domini principali da elencare (10 default, 5 se SME)")
    args = parser.parse_args(argv)

    records = load_records(args.input)
    if not records:
        print(f"Nessun record trovato in {args.input}.")
        return 1

    domains = domain_breakdown(records, args.top_percent)
    licenses = license_breakdown(records)
    pii = pii_redaction_totals(records)
    tdm = tdm_reservation_stats(records)

    markdown = render_markdown(args.input, domains, licenses, pii, tdm, args.top_percent)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")

    print(f"Riepilogo scritto in {out_path} ({len(records)} record, {domains['total_domains']} domini).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
