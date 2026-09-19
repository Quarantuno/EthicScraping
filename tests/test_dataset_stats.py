"""Tests for pipeline/dataset_stats.py, used by `main.py stats`."""
import unittest

from pipeline.dataset_stats import (
    compute_stats,
    domain_concentration_index,
    language_breakdown,
    quality_score_stats,
    render_text,
    word_count_stats,
)


def _record(url, word_count, license_="cc-by", confidence="high", pii=None, tdm=False,
            language=None, quality_score=None):
    return {
        "url": url,
        "domain": url.split("/")[2],
        "word_count": word_count,
        "license": license_,
        "license_confidence": confidence,
        "pii_redactions": pii or {},
        "tdm_reservation": tdm,
        "language": language,
        "quality_score": quality_score,
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


class TestQualityScoreStats(unittest.TestCase):
    def test_empty_records_do_not_crash(self):
        result = quality_score_stats([])
        self.assertEqual(result["count"], 0)

    def test_records_without_the_field_are_excluded_not_zeroed(self):
        # Simulates a dataset written before quality_score existed.
        old_style_records = [{"url": "https://a.example/1"}]
        result = quality_score_stats(old_style_records)
        self.assertEqual(result["count"], 0)

    def test_computes_distribution(self):
        records = [
            _record("https://a.example/1", 100, quality_score=0.9),
            _record("https://a.example/2", 100, quality_score=0.5),
            _record("https://a.example/3", 100, quality_score=0.1),
        ]
        result = quality_score_stats(records)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["min"], 0.1)
        self.assertEqual(result["max"], 0.9)
        self.assertEqual(result["median"], 0.5)


class TestLanguageBreakdown(unittest.TestCase):
    def test_groups_by_language(self):
        records = [
            _record("https://a.example/1", 100, language="it"),
            _record("https://a.example/2", 100, language="it"),
            _record("https://a.example/3", 100, language="en"),
        ]
        result = language_breakdown(records)
        self.assertIn(("it", 2), result)
        self.assertIn(("en", 1), result)

    def test_missing_or_none_language_is_grouped_as_unknown(self):
        records = [
            _record("https://a.example/1", 100, language=None),
            {"url": "https://a.example/2", "domain": "a.example"},  # no "language" key at all
        ]
        result = language_breakdown(records)
        self.assertEqual(dict(result), {"sconosciuta": 2})


class TestDomainConcentrationIndex(unittest.TestCase):
    def test_empty_records_returns_zero(self):
        self.assertEqual(domain_concentration_index([]), 0.0)

    def test_single_domain_is_maximally_concentrated(self):
        records = [_record(f"https://a.example/{i}", 100) for i in range(5)]
        self.assertEqual(domain_concentration_index(records), 1.0)

    def test_evenly_spread_domains_have_low_concentration(self):
        records = [_record(f"https://{d}.example/1", 100) for d in "abcdefghij"]
        index = domain_concentration_index(records)
        self.assertAlmostEqual(index, 0.1, places=4)  # 10 equal shares -> HHI = 1/10


class TestComputeStatsAndRenderTextExtras(unittest.TestCase):
    def test_render_text_includes_quality_language_and_concentration(self):
        records = [
            _record("https://a.example/1", 100, language="it", quality_score=0.9),
            _record("https://a.example/2", 100, language="it", quality_score=0.7),
        ]
        stats = compute_stats(records)
        text = render_text(stats)
        self.assertIn("Punteggio qualita", text)
        self.assertIn("Indice di concentrazione domini", text)
        self.assertIn("Lingue rilevate", text)
        self.assertIn("it", text)

    def test_render_text_handles_dataset_without_quality_or_language_fields(self):
        # An "old-style" dataset written before this feature existed.
        old_records = [{"url": "https://a.example/1", "domain": "a.example",
                        "word_count": 100, "pii_redactions": {}}]
        stats = compute_stats(old_records)
        text = render_text(stats)  # must not raise
        self.assertIn("non disponibile per nessun record", text)


if __name__ == "__main__":
    unittest.main()
