"""Test di scraper/tdm_rights.py: solo la parte senza rete (meta tag
detection + combined_status). TdmRepChecker._fetch fa una richiesta HTTP
vera e non viene testato qui (stesso approccio usato per RobotsChecker
in test_pipeline.py, dove il fetch reale e' fuori scope per gli unit
test)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.tdm_rights import check_meta_tag, combined_status  # noqa: E402


class TestCheckMetaTag(unittest.TestCase):
    def test_no_meta_tag_means_not_reserved(self):
        html = "<html><head><title>Pagina</title></head><body>Testo</body></html>"
        status = check_meta_tag(html)
        self.assertFalse(status["reserved"])
        self.assertIsNone(status["source"])

    def test_reserved_meta_tag_is_detected(self):
        html = (
            '<html><head>'
            '<meta name="tdm-reservation" content="1">'
            '<meta name="tdm-policy" content="https://esempio.it/tdm-policy">'
            '</head><body>Testo</body></html>'
        )
        status = check_meta_tag(html)
        self.assertTrue(status["reserved"])
        self.assertEqual(status["source"], "meta_tag")
        self.assertEqual(status["policy_url"], "https://esempio.it/tdm-policy")

    def test_meta_tag_content_zero_is_not_reserved(self):
        html = '<html><head><meta name="tdm-reservation" content="0"></head></html>'
        status = check_meta_tag(html)
        self.assertFalse(status["reserved"])

    def test_meta_tag_case_and_whitespace_insensitive(self):
        html = '<html><head><meta name="tdm-reservation" content="  TRUE  "></head></html>'
        status = check_meta_tag(html)
        self.assertTrue(status["reserved"])


class TestCombinedStatus(unittest.TestCase):
    def test_falls_back_to_not_reserved_without_checker(self):
        html = "<html><head></head><body>Testo</body></html>"
        status = combined_status(html, "https://esempio.it/pagina")
        self.assertFalse(status["reserved"])

    def test_meta_tag_reservation_short_circuits_well_known_check(self):
        html = '<html><head><meta name="tdm-reservation" content="1"></head></html>'

        class ExplodingChecker:
            def check(self, url):
                raise AssertionError("non dovrebbe essere chiamato se il meta tag basta")

        status = combined_status(html, "https://esempio.it/pagina", ExplodingChecker())
        self.assertTrue(status["reserved"])
        self.assertEqual(status["source"], "meta_tag")


if __name__ == "__main__":
    unittest.main()
