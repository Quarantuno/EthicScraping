"""Tests for pipeline/dataset_stats.py, used by `main.py stats`."""
import unittest

from pipeline.dataset_stats import compute_stats, render_text, word_count_stats


def _record(url, word_count, license_="cc-by", confidence="high", pii=None, tdm=False):
    return {
        "url": url,
        "domain": url.split("/")[2],
        "word_count": word_count,
        "license": license_,
        "license_confidence": confidence,
        "pii_redactions": pii or {},
        "tdm_reservation": tdm,
    }


class TestWordCountStats(unittest.TestCase):
    def test_empty_records_do_not_crash(self):
        result = word_count_stats([])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["total"], 0)

    def test_computes_min_max_mean_median(self):
        records = [_record("https://a.example/1", 100), _record("https://a.example/2", 200),
                   _record("https://a.example/3", 300)]
        result = word_count_stats(records)
        self.assertEqual(result["min"], 100)
        self.assertEqual(result["max"], 300)
        self.assertEqual(result["mean"], 200.0)
        self.assertEqual(result["median"], 200)
        self.assertEqual(result["total"], 600)


class TestComputeStatsAndRenderText(unittest.TestCase):
    def test_empty_dataset(self):
        stats = compute_stats([])
        self.assertEqual(stats["total_records"], 0)
        text = render_text(stats)
        self.assertIn("Record totali: 0", text)

    def test_mixed_dataset_produces_expected_sections(self):
        records = [
            _record("https://a.example/1", 100, pii={"EMAIL": 2}),
            _record("https://a.example/2", 200, license_="unknown", confidence="unknown"),
            _record("https://b.example/1", 150, tdm=True),
        ]
        stats = compute_stats(records, top_percent=50.0)
        self.assertEqual(stats["total_records"], 3)
        self.assertEqual(stats["domains"]["total_domains"], 2)
        self.assertEqual(stats["pii"], {"EMAIL": 2})
        self.assertEqual(stats["tdm"]["records_with_tdm_reservation_flag"], 1)

        text = render_text(stats)
        self.assertIn("Record totali: 3", text)
        self.assertIn("Domini distinti: 2", text)
        self.assertIn("PII redatte", text)
        self.assertIn("a.example", text)

    def test_render_text_handles_no_pii(self):
        records = [_record("https://a.example/1", 100)]
        stats = compute_stats(records)
        text = render_text(stats)
        self.assertIn("PII redatte: nessuna", text)


if __name__ == "__main__":
    unittest.main()
