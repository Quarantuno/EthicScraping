"""Test di compliance/generate_training_summary.py: solo stdlib, nessuna
dipendenza pesante richiesta."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "compliance"))

from generate_training_summary import (  # noqa: E402
    domain_breakdown,
    license_breakdown,
    pii_redaction_totals,
    render_markdown,
    tdm_reservation_stats,
)


def _records():
    return [
        {"domain": "a.it", "license": "CC BY 4.0", "license_confidence": "high",
         "pii_redactions": {"EMAIL": 1}, "tdm_reservation": False},
        {"domain": "a.it", "license": "CC BY 4.0", "license_confidence": "high",
         "pii_redactions": {}, "tdm_reservation": False},
        {"domain": "b.it", "license": None, "license_confidence": "unknown",
         "pii_redactions": {"PHONE": 2}, "tdm_reservation": False},
        {"domain": "c.it", "license": None, "license_confidence": "unknown",
         "pii_redactions": {}, "tdm_reservation": True},
    ]


class TestDomainBreakdown(unittest.TestCase):
    def test_counts_and_shares(self):
        result = domain_breakdown(_records(), top_percent=50)
        self.assertEqual(result["total_records"], 4)
        self.assertEqual(result["total_domains"], 3)
        self.assertEqual(result["top"][0]["domain"], "a.it")
        self.assertEqual(result["top"][0]["records"], 2)
        self.assertEqual(result["top"][0]["share"], 50.0)

    def test_top_percent_zero_still_shows_at_least_one_domain(self):
        # ceil(3 * 0/100) = 0 -> forziamo almeno 1 dominio, mai una lista vuota
        result = domain_breakdown(_records(), top_percent=0)
        self.assertGreaterEqual(len(result["top"]), 1)

    def test_empty_records_do_not_crash(self):
        result = domain_breakdown([], top_percent=10)
        self.assertEqual(result["total_records"], 0)
        self.assertEqual(result["top"], [])


class TestLicenseBreakdown(unittest.TestCase):
    def test_groups_by_license_and_confidence(self):
        result = license_breakdown(_records())
        labels = dict(result)
        self.assertEqual(labels.get("CC BY 4.0 (confidence: high)"), 2)
        self.assertEqual(labels.get("unknown (confidence: unknown)"), 2)


class TestPiiRedactionTotals(unittest.TestCase):
    def test_sums_across_records(self):
        totals = pii_redaction_totals(_records())
        self.assertEqual(totals, {"EMAIL": 1, "PHONE": 2})

    def test_no_redactions_gives_empty_dict(self):
        self.assertEqual(pii_redaction_totals([{"pii_redactions": {}}]), {})


class TestTdmReservationStats(unittest.TestCase):
    def test_counts_flagged_records(self):
        stats = tdm_reservation_stats(_records())
        self.assertEqual(stats, {"records_with_tdm_reservation_flag": 1, "total_records": 4})


class TestRenderMarkdown(unittest.TestCase):
    def test_produces_all_expected_sections(self):
        records = _records()
        domains = domain_breakdown(records, top_percent=50)
        licenses = license_breakdown(records)
        pii = pii_redaction_totals(records)
        tdm = tdm_reservation_stats(records)
        md = render_markdown("dataset/output_approved.jsonl", domains, licenses, pii, tdm, 50)

        self.assertIn("Section 1 — General Information", md)
        self.assertIn("Section 2 — List of Data Sources", md)
        self.assertIn("Section 3 — Data Processing Aspects", md)
        self.assertIn("a.it", md)
        self.assertIn("1 / 4", md)  # tdm_reservation flag count


if __name__ == "__main__":
    unittest.main()
