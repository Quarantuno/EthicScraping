"""Local checks for evidence that should be reviewed before dataset use."""
import json
from collections import Counter
from urllib.parse import urlsplit


def _has_source_url(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return url.scheme in {"http", "https"} and bool(url.hostname)
    except ValueError:
        return False


def check_dataset(lines):
    counts = Counter()
    findings = []
    records_checked = 0
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        records_checked += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = None
        issues = []
        if not isinstance(record, dict):
            issues.append("invalid_record")
        else:
            if not _has_source_url(record.get("url")):
                issues.append("missing_source_url")
            license_name = record.get("license")
            if (not isinstance(license_name, str) or
                    license_name.strip().lower() in {"", "unknown", "sconosciuta"} or
                    record.get("license_confidence") != "high" or
                    not record.get("license_source")):
                issues.append("license_evidence_for_review")
            for field in ("tdm_reservation", "ai_optout"):
                value = record.get(field)
                if value is True:
                    issues.append(field)
                elif value is not False:
                    issues.append(f"missing_{field}_evidence")
        if issues:
            counts.update(issues)
            findings.append({"line": number, "issues": issues})
    return {
        "schema_version": 1,
        "records_checked": records_checked,
        "records_flagged": len(findings),
        "counts": dict(counts),
        "findings": findings,
    }
