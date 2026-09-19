"""Integration tests for the quality-filter and language-filter wiring in
pipeline/dataset_writer.py (on top of the existing DatasetWriter tests in
test_pipeline.py). Reuses that module's SAMPLE_HTML fixture.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.dataset_writer import DatasetWriter
from scraper.license_detector import detect_license
from scraper.fetcher import FetchResult
from tests.test_pipeline import SAMPLE_HTML


# Realistic boilerplate: the same short menu line repeated as separate
# <p> elements (as it would appear on a page where a nav-like block
# slipped past NOISE_TAGS, e.g. inside <main>), so text_extractor's
# newline-per-element output actually contains repeated identical lines.
NOISY_BOILERPLATE_HTML = """
<html>
<head><title>Solo menu</title></head>
<body>
  <main>
    {rows}
  </main>
</body>
</html>
""".format(rows="\n".join(["<p>Home Chi siamo Contatti</p>"] * 30))


class TestDatasetWriterQualityAndLanguage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.output_path = os.path.join(self.tmpdir, "out.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _fake_result(self, html=SAMPLE_HTML, url="https://esempio-sito.it/pagina"):
        return FetchResult(
            url=url, status="ok", html=html, status_code=200,
            license_info=detect_license(html, url), fetched_at=1234567890.0,
        )

    def test_written_record_carries_quality_score_and_language_fields(self):
        writer = DatasetWriter(self.output_path, min_word_count=10)
        record = writer.process(self._fake_result())
        self.assertIsNotNone(record)
        self.assertIn("quality_score", record)
        self.assertGreaterEqual(record["quality_score"], 0.0)
        self.assertLessEqual(record["quality_score"], 1.0)
        # langdetect isn't installed in this environment -> always None,
        # but the field must still be present (see language_detector.py).
        self.assertIn("language", record)

    def test_quality_filter_drops_boilerplate_by_default(self):
        writer = DatasetWriter(self.output_path, min_word_count=10)
        result = self._fake_result(html=NOISY_BOILERPLATE_HTML,
                                    url="https://esempio-sito.it/menu")
        self.assertIsNone(writer.process(result))
        self.assertEqual(writer.stats["skipped_quality"], 1)

    def test_quality_filter_can_be_disabled(self):
        writer = DatasetWriter(self.output_path, min_word_count=10, use_quality_filter=False)
        result = self._fake_result(html=NOISY_BOILERPLATE_HTML,
                                    url="https://esempio-sito.it/menu")
        record = writer.process(result)
        self.assertIsNotNone(record)
        self.assertEqual(writer.stats["skipped_quality"], 0)

    def test_language_filter_drops_pages_in_unwanted_language(self):
        writer = DatasetWriter(self.output_path, min_word_count=10,
                                use_language_filter=True, allowed_languages=["en"])
        with patch("pipeline.dataset_writer.detect_language", return_value="it"):
            result = writer.process(self._fake_result())
        self.assertIsNone(result)
        self.assertEqual(writer.stats["skipped_language"], 1)

    def test_language_filter_keeps_pages_in_allowed_language(self):
        writer = DatasetWriter(self.output_path, min_word_count=10,
                                use_language_filter=True, allowed_languages=["it", "en"])
        with patch("pipeline.dataset_writer.detect_language", return_value="it"):
            record = writer.process(self._fake_result())
        self.assertIsNotNone(record)
        self.assertEqual(record["language"], "it")
        self.assertEqual(writer.stats["skipped_language"], 0)

    def test_language_filter_off_by_default_ignores_detected_language(self):
        writer = DatasetWriter(self.output_path, min_word_count=10)
        with patch("pipeline.dataset_writer.detect_language", return_value="fr"):
            record = writer.process(self._fake_result())
        self.assertIsNotNone(record)
        self.assertEqual(record["language"], "fr")


if __name__ == "__main__":
    unittest.main()
