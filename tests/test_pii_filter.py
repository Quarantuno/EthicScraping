"""Tests for pipeline/pii_filter.py, in particular the CREDIT_CARD/PHONE
false-positive fix found by running `main.py run` against real Wikipedia
articles: DOIs, ISSNs, arXiv ids, and patent application numbers were
being redacted as if they were credit cards / phone numbers, because a
bare "13-16 digits" or "6-11 digits with separators" regex matches those
just as well as a real one. See the module docstring for the fix
(Luhn checksum + citation-keyword context + plausible digit-count
range).
"""
import unittest

from pipeline.pii_filter import redact_pii


class TestEmailAndIbanAndIpv4(unittest.TestCase):
    def test_redacts_email(self):
        redacted, counts = redact_pii("Scrivimi a mario.rossi@example.com per info.")
        self.assertNotIn("mario.rossi@example.com", redacted)
        self.assertEqual(counts["EMAIL"], 1)

    def test_redacts_iban(self):
        redacted, counts = redact_pii("IBAN: IT60X0542811101000000123456")
        self.assertIn("[REDACTED_IBAN]", redacted)
        self.assertEqual(counts["IBAN"], 1)

    def test_redacts_ipv4(self):
        redacted, counts = redact_pii("server interno: 192.168.1.100")
        self.assertIn("[REDACTED_IPV4]", redacted)
        self.assertEqual(counts["IPV4"], 1)


class TestPhoneNumbers(unittest.TestCase):
    def test_does_not_redact_doi_with_journal_path_between_keyword_and_number(self):
        # Real DOIs have the journal/article path between "DOI" and the
        # actual number, e.g. "DOI: 10.1016/j.patter.2024.101074" -- a
        # citation-context check anchored immediately before the match
        # misses this; it must search further back.
        text = ("in Patterns, vol. 5, n. 11, 8 novembre 2024, p. 101074, "
                "DOI: 10.1016/j.patter.2024.101074. URL consultato il 10 gennaio")
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["PHONE"], 0)

    def test_does_not_redact_wikipedia_permalink_oldid(self):
        text = ('Recuperato da "https://it.wikipedia.org/w/index.php?'
                'title=Etica_dei_dati&oldid=123456789"')
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["PHONE"], 0)

    def test_does_not_redact_ad_tracking_url_parameter(self):
        text = "locale=en-it&gad_source=1&gad_campaignid=987654321&gclid=abc123"
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["PHONE"], 0)

    def test_redacts_local_style_phone_number(self):
        redacted, counts = redact_pii("Chiamami al 333-1234567 per informazioni.")
        self.assertIn("[REDACTED_PHONE]", redacted)
        self.assertEqual(counts["PHONE"], 1)

    def test_redacts_international_style_phone_number(self):
        redacted, counts = redact_pii("Contact: +39 02 1234 5678")
        self.assertEqual(counts["PHONE"], 1)

    def test_does_not_redact_bare_year_range(self):
        text = "potrebbe succedere entro il 2030-2035, secondo le stime."
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["PHONE"], 0)

    def test_does_not_redact_issn(self):
        text = "pubblicato con ISSN 0148-6195 (WC - ACNP)."
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["PHONE"], 0)

    def test_does_not_leave_partial_redaction_on_long_digit_runs(self):
        # Regression: the original PHONE regex had no boundary at the
        # end, so it could consume only PART of a longer digit run,
        # leaving stray digits glued to the [REDACTED_PHONE] marker
        # (e.g. "...arXiv.[REDACTED_PHONE]9" from a real run's output).
        text = "riferimento arXiv.240501234569 nel testo."
        redacted, counts = redact_pii(text)
        self.assertNotRegex(redacted, r"\[REDACTED_PHONE\]\d")
        self.assertNotRegex(redacted, r"\d\[REDACTED_PHONE\]")

    def test_does_not_bridge_across_newline_into_unrelated_number(self):
        # Regression found scraping docs.python.org: a version-history
        # table has "Derived from: 1.2" on one line and "Year: 1995-1999"
        # on the next. The old regex's separator class was \s (matches
        # newlines too), so it read "2\n1995-1999" as a single 9-digit
        # "phone number" -- swallowing the whole next table cell into the
        # redaction. Separators must be intra-line only ([ \t]).
        text = "1.3 thru 1.5.2\n1.2\n1995-1999\nCNRI\nyes"
        redacted, counts = redact_pii(text)
        self.assertEqual(counts["PHONE"], 0)
        self.assertIn("1995-1999", redacted)
        self.assertIn("1.2", redacted)

    def test_does_not_redact_proquest_document_id(self):
        # Real Wikipedia reference: "ProQuest 2028196789." -- a document
        # id from the ProQuest database, same shape as a phone number.
        text = "in Georgetown Journal of International Affairs. ProQuest 2028196789."
        redacted, counts = redact_pii(text)
        self.assertEqual(counts["PHONE"], 0)
        self.assertIn("2028196789", redacted)

    def test_does_not_redact_jstor_or_pmid_or_hdl_id(self):
        for text in (
            "disponibile su JSTOR 40213851 (consultato il 2024).",
            "PMID 34567890123, ricerca pubblicata nel 2020.",
            "hdl 11858 123456789 per l'accesso al documento.",
        ):
            redacted, counts = redact_pii(text)
            self.assertEqual(counts["PHONE"], 0, msg=f"falso positivo su: {text!r}")


class TestCreditCardNumbers(unittest.TestCase):
    def test_does_not_redact_isbn_with_dash_prefix_before_keyword_reach(self):
        # ISBN starts with a literal "978-" prefix; a naive check could
        # accidentally treat that dash as a word boundary and start
        # matching right after it, "losing" the ISBN keyword that's
        # further back. Must still be caught.
        text = ("First trade paperback edition, PublicAffairs, 2020, "
                "ISBN 978-1541618619-4.")
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["CREDIT_CARD"], 0)

    def test_does_not_redact_url_parameter_value(self):
        text = "tracking: campaignid=1234567890123&other=x"
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["CREDIT_CARD"], 0)

    def test_redacts_luhn_valid_card_number(self):
        redacted, counts = redact_pii("Carta di test: 4111 1111 1111 1111")
        self.assertIn("[REDACTED_CREDIT_CARD]", redacted)
        self.assertEqual(counts["CREDIT_CARD"], 1)

    def test_does_not_redact_doi(self):
        text = "in Big Data & Society, vol. 3, n. 2, 2016, DOI: 10.1177/2053951716679679."
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["CREDIT_CARD"], 0)

    def test_does_not_redact_patent_application_number(self):
        text = ('la domanda di brevetto "Fusion.43" (UIBM n. 1020260000012345, '
                'depositata il 7 febbraio 2026)')
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["CREDIT_CARD"], 0)

    def test_does_not_redact_arxiv_id(self):
        text = "10.48550/arXiv.2405.012349, URL consultato il 3 marzo 2026."
        redacted, counts = redact_pii(text)
        self.assertEqual(redacted, text)
        self.assertEqual(counts["CREDIT_CARD"], 0)

    def test_random_long_number_without_luhn_checksum_is_not_flagged(self):
        # A random 16-digit run that happens NOT to be near a citation
        # keyword should still be rejected by the Luhn check alone.
        text = "codice di riferimento interno: 1234567890123456 nel registro."
        redacted, counts = redact_pii(text)
        self.assertEqual(counts["CREDIT_CARD"], 0)
        self.assertIn("1234567890123456", redacted)


if __name__ == "__main__":
    unittest.main()
