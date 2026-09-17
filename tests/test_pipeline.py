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
from pipeline.ner_pii import redact_named_entities, is_available, reset_cache
from pipeline.dedup import Deduplicator, content_hash, simhash, hamming_distance
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


class TestNerPii(unittest.TestCase):
    """spaCy e' una dipendenza opzionale e non e' installata in questo
    ambiente: qui verifichiamo che il degrado sia sicuro (nessuna
    eccezione, testo invariato) quando il modello non c'e', che e'
    esattamente lo scenario di chi non ha fatto `pip install spacy`."""

    def setUp(self):
        reset_cache()

    def test_reports_unavailable_without_spacy_or_model(self):
        self.assertFalse(is_available("un-modello-che-non-esiste"))

    def test_degrades_to_noop_when_model_unavailable(self):
        text = "Mario Rossi vive a Milano."
        redacted, counts = redact_named_entities(text, model_name="un-modello-che-non-esiste")
        self.assertEqual(redacted, text)
        self.assertEqual(counts, {})


class TestDedup(unittest.TestCase):
    def test_detects_exact_and_whitespace_variant_duplicates(self):
        dedup = Deduplicator()
        self.assertFalse(dedup.is_duplicate("Testo identico"))
        self.assertTrue(dedup.is_duplicate("testo   identico"))  # case/space-insensitive
        self.assertFalse(dedup.is_duplicate("Testo diverso"))

    def test_content_hash_is_deterministic(self):
        self.assertEqual(content_hash("Ciao Mondo"), content_hash("ciao   mondo"))

    # Testo "da pagina web" realistico (>100 parole): il SimHash e' calibrato
    # su documenti di questa taglia, non su singole frasi brevi.
    _LONG_ORIGINAL = (
        "Questo articolo parla di intelligenza artificiale e di come i modelli "
        "linguistici vengano addestrati su grandi quantita' di testo raccolto dal "
        "web in modo responsabile e trasparente. La raccolta dei dati deve sempre "
        "rispettare le regole del sito, la licenza dei contenuti e la privacy delle "
        "persone coinvolte. Un buon dataset per l'addestramento tiene traccia della "
        "provenienza di ogni documento, cosi' da poter verificare in ogni momento da "
        "dove arrivano le informazioni usate per insegnare al modello a rispondere. "
        "Questo approccio riduce il rischio di includere contenuti protetti da "
        "copyright o dati personali non autorizzati, e rende il processo piu' "
        "trasparente per chiunque voglia verificarlo in futuro."
    )
    _LONG_UNRELATED = (
        "La ricetta della carbonara prevede uova, guanciale, pecorino romano e "
        "pepe nero, senza panna ne' aglio, cotta a fuoco basso. Il segreto sta "
        "nella mantecatura fuori dal fuoco, per evitare che l'uovo si strapazzi a "
        "contatto con la pasta ancora troppo calda. Un buon piatto di carbonara si "
        "riconosce dalla cremosita' del condimento e dal giusto equilibrio tra il "
        "grasso del guanciale e la sapidita' del formaggio, senza bisogno di panna "
        "o altri ingredienti che nella ricetta originale romana non compaiono."
    )

    def test_simhash_flags_near_duplicate_text_as_close(self):
        near_dup = self._LONG_ORIGINAL.replace("responsabile", "consapevole")

        dist_near = hamming_distance(simhash(self._LONG_ORIGINAL), simhash(near_dup))
        dist_far = hamming_distance(simhash(self._LONG_ORIGINAL), simhash(self._LONG_UNRELATED))
        self.assertLess(dist_near, dist_far)

    def test_deduplicator_drops_near_duplicate_pages(self):
        dedup = Deduplicator(near_duplicate_threshold=8)
        near_dup = self._LONG_ORIGINAL.replace("responsabile", "consapevole")

        self.assertFalse(dedup.is_duplicate(self._LONG_ORIGINAL))
        self.assertTrue(dedup.is_duplicate(near_dup))
        self.assertFalse(dedup.is_duplicate(self._LONG_UNRELATED))

    def test_deduplicator_can_disable_fuzzy_matching(self):
        dedup = Deduplicator(near_duplicate_threshold=None)
        near_dup = self._LONG_ORIGINAL.replace("responsabile", "consapevole")
        self.assertFalse(dedup.is_duplicate(self._LONG_ORIGINAL))
        self.assertFalse(dedup.is_duplicate(near_dup))  # solo hash esatto: non e' un duplicato


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
