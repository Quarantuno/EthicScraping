"""Test per scraper.license_detector, in particolare per il nuovo
fallback che riconosce un link a creativecommons.org anche quando non ha
l'attributo semantico rel="license" -- pattern molto comune su siti reali
(footer con `<a href="https://creativecommons.org/licenses/by/4.0/">CC
BY 4.0</a>` senza rel), che il solo controllo rel="license" o il match sul
testo visibile (body_text_match) non intercettano, perche' BeautifulSoup
.get_text() non include mai il valore degli attributi href.
"""
import unittest

from scraper.license_detector import detect_license


class TestRelLicenseLink(unittest.TestCase):
    def test_detects_rel_license_on_link_tag(self):
        html = """
        <html><head>
          <link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">
        </head><body><p>contenuto</p></body></html>
        """
        info = detect_license(html, "https://esempio.it/pagina")
        self.assertEqual(info["source"], "rel_license_link")
        self.assertEqual(info["confidence"], "high")
        self.assertIn("BY-SA", info["license"])

    def test_detects_rel_license_public_domain(self):
        html = """
        <html><body>
          <a rel="license" href="https://creativecommons.org/publicdomain/zero/1.0/">CC0</a>
        </body></html>
        """
        info = detect_license(html, "https://esempio.it/pagina")
        self.assertEqual(info["confidence"], "high")
        self.assertEqual(info["license"], "Public Domain")


class TestPlainHrefCcLink(unittest.TestCase):
    """Il caso reale piu' comune: un link alla pagina della licenza senza
    rel="license" -- es. il footer di molti siti CC-licenziati."""

    def test_detects_footer_link_without_rel_attribute(self):
        html = """
        <html><body>
          <main><p>Un articolo qualsiasi, abbastanza lungo da sembrare
          contenuto vero e non solo un frammento di prova per il test.</p></main>
          <footer>
            Unless otherwise noted, content on this site is licensed under a
            <a href="https://creativecommons.org/licenses/by/4.0/">Creative
            Commons Attribution 4.0 International license</a>.
          </footer>
        </body></html>
        """
        info = detect_license(html, "https://blog-esempio.org/articolo")
        self.assertEqual(info["source"], "href_cc_link")
        self.assertEqual(info["confidence"], "medium")
        self.assertIn("CC BY 4.0", info["license"])

    def test_detects_public_domain_href_without_rel(self):
        html = """
        <html><body>
          <p>Testo.</p>
          <a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0 1.0</a>
        </body></html>
        """
        info = detect_license(html, "https://esempio.org/x")
        self.assertEqual(info["source"], "href_cc_link")
        self.assertEqual(info["license"], "Public Domain")

    def test_rel_license_link_takes_priority_over_plain_href(self):
        # Se c'e' sia un link rel="license" che un altro link CC generico
        # altrove nella pagina, vince il segnale semantico (confidence high).
        html = """
        <html><head>
          <link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">
        </head><body>
          <footer><a href="https://creativecommons.org/licenses/by/4.0/">altro link</a></footer>
        </body></html>
        """
        info = detect_license(html, "https://esempio.it/pagina")
        self.assertEqual(info["source"], "rel_license_link")
        self.assertEqual(info["confidence"], "high")

    def test_does_not_false_positive_on_unrelated_link_text(self):
        # Un link che parla DI creativecommons.org in generale (non punta a
        # una vera pagina /licenses/ o /publicdomain/) non deve scattare.
        html = """
        <html><body>
          <p>Per saperne di piu' vai su <a href="https://creativecommons.org/about/">
          Creative Commons</a>.</p>
        </body></html>
        """
        info = detect_license(html, "https://esempio.it/pagina")
        self.assertEqual(info["confidence"], "unknown")


class TestExistingPathsUnaffected(unittest.TestCase):
    def test_known_domain_still_wins_over_everything(self):
        info = detect_license("<html></html>", "https://it.wikipedia.org/wiki/Test")
        self.assertEqual(info["source"], "known_domain")
        self.assertIn("Wikipedia", info["license"])

    def test_meta_tag_still_used_when_no_link_present(self):
        html = '<html><head><meta name="license" content="All rights reserved"></head></html>'
        info = detect_license(html, "https://esempio.it/x")
        self.assertEqual(info["source"], "meta_tag")

    def test_body_text_match_still_used_as_last_resort(self):
        # L'URL della licenza compare come testo visibile puro (non in un
        # href): niente rel="license", niente <a href> che punti li'.
        html = "<html><body><p>Licenza: creativecommons.org/licenses/by/4.0 (testo semplice)</p></body></html>"
        info = detect_license(html, "https://esempio.it/x")
        self.assertEqual(info["source"], "body_text_match")

    def test_unknown_when_no_signal_at_all(self):
        info = detect_license("<html><body>nulla</body></html>", "https://sito-ignoto.it/x")
        self.assertEqual(info["confidence"], "unknown")
        self.assertIsNone(info["license"])


if __name__ == "__main__":
    unittest.main()
