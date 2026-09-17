"""Unit tests per i moduli di pipeline e scraper che non richiedono rete.

Nota: questo ambiente non ha accesso alla rete (egress bloccato), quindi
qui testiamo la logica pura (parsing HTML, redazione PII, dedup, scrittura
dataset) con fixture locali, senza fare fetch reali. Un test di
integrazione end-to-end con fetch veri va lanciato in un ambiente con
accesso alla rete, es.: `python main.py run --config config/sources.yaml`.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.text_extractor import extract_text
from pipeline.pii_filter import redact_pii
from pipeline.dedup import Deduplicator, content_hash
from pipeline.dataset_writer import DatasetWriter
from scraper.license_detector import detect_license
from scraper.fetcher import FetchResult


SAMPLE_HTML = """
<html>
<head>
  <title>Pagina di prova</title>
  <link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">
</head>
<body>
  <nav>menu da ignorare</nav>
  <main>
    <h1>Titolo articolo</h1>
    <p>Questo e' un paragrafo di prova con abbastanza parole per superare
    la soglia minima di lunghezza richiesta dal dataset writer, cosi'
    possiamo verificare che il record venga effettivamente scritto nel
    file JSONL di output senza essere scartato per essere troppo corto,
    e che venga contato correttamente il numero di parole totali.</p>
    <p>Contattaci a mario.rossi@example.com o al numero 333-1234567.</p>
  </main>
  <footer>footer da ignorare</footer>
</body>
</html>
"""


class TestTextExtractor(unittest.TestCase):
    def test_strips_noise_and_keeps_main_content(self):
        result = extract_text(SAMPLE_HTML)
        self.assertEqual(result["title"], "Pagina di prova")
        self.assertIn("Titolo articolo", result["text"])
        self.assertNotIn("menu da ignorare", result["text"])
        self.assertNotIn("footer da ignorare", result["text"])
        self.assertGreater(result["word_count"], 10)


class TestPiiFilter(unittest.TestCase):
    def test_redacts_email_and_phone(self):
        text = "Scrivimi a mario.rossi@example.com o chiamami al 333-1234567."
        redacted, counts = redact_pii(text)
        self.assertNotIn("mario.rossi@example.com", redacted)
        self.assertIn("[REDACTED_EMAIL]", redacted)
        self.assertEqual(counts["EMAIL"], 1)
        self.assertGreaterEqual(counts["PHONE"], 1)


class TestDedup(unittest.TestCase):
    def test_detects_exact_and_whitespace_variant_duplicates(self):
        dedup = Deduplicator()
        self.assertFalse(dedup.is_duplicate("Testo identico"))
        self.assertTrue(dedup.is_duplicate("testo   identico"))  # case/space-insensitive
        self.assertFalse(dedup.is_duplicate("Testo diverso"))

    def test_content_hash_is_deterministic(self):
        self.assertEqual(content_hash("Ciao Mondo"), content_hash("ciao   mondo"))


class TestLicenseDetector(unittest.TestCase):
    def test_detects_rel_license_cc_link(self):
        info = detect_license(SAMPLE_HTML, "https://esempio-sito.it/pagina")
        self.assertEqual(info["confidence"], "high")
        self.assertIn("BY-SA", info["license"])

    def test_known_domain_wikipedia(self):
        info = detect_license("<html></html>", "https://it.wikipedia.org/wiki/Test")
        self.assertEqual(info["confidence"], "high")
        self.assertIn("Wikipedia", info["license"])

    def test_unknown_when_no_signal(self):
        info = detect_license("<html><body>nulla</body></html>", "https://sito-ignoto.it/x")
        self.assertEqual(info["confidence"], "unknown")
        self.assertIsNone(info["license"])


class TestDatasetWriter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.output_path = os.path.join(self.tmpdir, "out.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _fake_result(self, url="https://esempio-sito.it/pagina"):
        return FetchResult(
            url=url,
            status="ok",
            html=SAMPLE_HTML,
            status_code=200,
            license_info=detect_license(SAMPLE_HTML, url),
            fetched_at=1234567890.0,
        )

    def test_writes_record_with_redacted_pii_and_provenance(self):
        writer = DatasetWriter(self.output_path, min_word_count=10)
        record = writer.process(self._fake_result())
        self.assertIsNotNone(record)
        self.assertNotIn("mario.rossi@example.com", record["text"])
        self.assertEqual(record["pii_redactions"].get("EMAIL"), 1)
        self.assertEqual(record["license_confidence"], "high")
        self.assertEqual(writer.stats["written"], 1)

        with open(self.output_path, encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["id"], record["id"])

    def test_skips_duplicate_pages(self):
        writer = DatasetWriter(self.output_path, min_word_count=10)
        self.assertIsNotNone(writer.process(self._fake_result("https://esempio-sito.it/a")))
        self.assertIsNone(writer.process(self._fake_result("https://esempio-sito.it/b")))
        self.assertEqual(writer.stats["skipped_duplicate"], 1)

    def test_license_confidence_filter_drops_unknown_license(self):
        writer = DatasetWriter(self.output_path, min_word_count=10,
                                min_license_confidence="high")
        low_license_html = SAMPLE_HTML.replace(
            '<link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">', ""
        )
        result = FetchResult(
            url="https://sito-senza-licenza.it/pagina",
            status="ok",
            html=low_license_html,
            license_info=detect_license(low_license_html, "https://sito-senza-licenza.it/pagina"),
        )
        self.assertIsNone(writer.process(result))
        self.assertEqual(writer.stats["skipped_license"], 1)

    def test_skips_result_below_min_word_count(self):
        writer = DatasetWriter(self.output_path, min_word_count=10000)
        self.assertIsNone(writer.process(self._fake_result()))
        self.assertEqual(writer.stats["skipped_too_short"], 1)


if __name__ == "__main__":
    unittest.main()
