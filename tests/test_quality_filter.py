"""Tests for pipeline/quality_filter.py's dependency-free noise
heuristics."""
import unittest

from pipeline.quality_filter import compute_quality_metrics, passes_quality_filter


GOOD_TEXT = (
    "Questo e' un paragrafo di prova scritto in prosa normale, con frasi "
    "di lunghezza ragionevole, punteggiatura corretta e nessuna riga "
    "ripetuta piu' volte. Serve solo a rappresentare un contenuto testuale "
    "pulito, come quello che ci aspettiamo di trovare su una pagina "
    "editoriale ben scritta, cosi' da verificare che le euristiche di "
    "qualita' lo considerino a posto senza scartarlo per errore."
)

BOILERPLATE_TEXT = "\n".join(["Home | Chi siamo | Contatti"] * 30)

GARBLED_TEXT = "3f9a$$##@@!!%%^^&&**(())__++==--3f9a$$##@@!!%%^^&&**"

LONG_TOKEN_TEXT = "prosa normale " * 20 + (
    "a" * 200 + " " + "b" * 200 + " " + "c" * 200
) * 3


class TestComputeQualityMetrics(unittest.TestCase):
    def test_empty_text_does_not_crash(self):
        metrics = compute_quality_metrics("")
        self.assertEqual(metrics["alpha_ratio"], 0.0)
        self.assertEqual(metrics["unique_line_ratio"], 1.0)

    def test_none_text_does_not_crash(self):
        metrics = compute_quality_metrics(None)
        self.assertIn("quality_score", metrics)

    def test_good_prose_scores_highly(self):
        metrics = compute_quality_metrics(GOOD_TEXT)
        self.assertGreaterEqual(metrics["alpha_ratio"], 0.7)
        self.assertEqual(metrics["unique_line_ratio"], 1.0)
        self.assertGreater(metrics["quality_score"], 0.7)

    def test_repeated_boilerplate_lines_lower_unique_line_ratio(self):
        metrics = compute_quality_metrics(BOILERPLATE_TEXT)
        self.assertLess(metrics["unique_line_ratio"], 0.1)

    def test_garbled_symbol_heavy_text_has_low_alpha_ratio(self):
        metrics = compute_quality_metrics(GARBLED_TEXT)
        self.assertLess(metrics["alpha_ratio"], 0.3)

    def test_absurdly_long_tokens_are_flagged(self):
        metrics = compute_quality_metrics(LONG_TOKEN_TEXT)
        self.assertGreater(metrics["long_word_ratio"], 0.0)


class TestPassesQualityFilter(unittest.TestCase):
    def test_good_prose_passes(self):
        metrics = compute_quality_metrics(GOOD_TEXT)
        self.assertTrue(passes_quality_filter(metrics))

    def test_boilerplate_fails_on_unique_line_ratio(self):
        metrics = compute_quality_metrics(BOILERPLATE_TEXT)
        self.assertFalse(passes_quality_filter(metrics))

    def test_garbled_text_fails_on_alpha_ratio(self):
        metrics = compute_quality_metrics(GARBLED_TEXT)
        self.assertFalse(passes_quality_filter(metrics))

    def test_thresholds_are_configurable(self):
        metrics = compute_quality_metrics(BOILERPLATE_TEXT)
        # With a permissive enough threshold, the same text passes.
        self.assertTrue(passes_quality_filter(metrics, min_unique_line_ratio=0.0))


if __name__ == "__main__":
    unittest.main()
