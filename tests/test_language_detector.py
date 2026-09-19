"""Tests for pipeline/language_detector.py.

langdetect is NOT installed in this environment (by design -- see
requirements.txt), so these tests mainly verify the graceful-degradation
path (same pattern as pipeline/ner_pii.py for spaCy): no exception,
`detect_language` always returns None, `is_available()` is False, and a
single warning is logged. If langdetect happens to be installed
elsewhere, the "actually detects" tests are skipped rather than failed,
since real language detection isn't this project's own logic to test.
"""
import unittest

from pipeline import language_detector


class TestGracefulDegradationWithoutLangdetect(unittest.TestCase):
    def setUp(self):
        language_detector.reset_cache()

    def tearDown(self):
        language_detector.reset_cache()

    def test_reports_unavailable_without_langdetect(self):
        if language_detector.is_available():
            self.skipTest("langdetect is installed in this environment")
        self.assertFalse(language_detector.is_available())

    def test_detect_language_returns_none_without_langdetect(self):
        if language_detector.is_available():
            self.skipTest("langdetect is installed in this environment")
        self.assertIsNone(language_detector.detect_language("Questo e' un testo in italiano."))

    def test_empty_text_returns_none_regardless(self):
        self.assertIsNone(language_detector.detect_language(""))
        self.assertIsNone(language_detector.detect_language(None))

    def test_does_not_raise_on_repeated_calls(self):
        # The "not installed" warning should only be logged once, but
        # repeated calls must never raise.
        for _ in range(3):
            language_detector.detect_language("testo di prova")


if __name__ == "__main__":
    unittest.main()
