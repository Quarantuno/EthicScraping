"""Tests for scraper/robots.py, in particular what happens when
robots.txt can't be read (RFC 9309 section 2.3.1): 4xx means "no rules,
allowed", but 5xx or an unreachable server means "assume complete
disallow" -- the opposite of what an earlier version did. requests.get
is mocked, no network traffic."""
import unittest
from unittest.mock import MagicMock, patch

import requests

from scraper.robots import RobotsChecker

UA = "TestBot/1.0 (contact@example.com)"
URL = "https://example.com/some/page"


def _resp(status_code, text=""):
    return MagicMock(status_code=status_code, text=text)


def _checker_with(response=None, side_effect=None):
    checker = RobotsChecker(user_agent=UA)
    patcher = patch("scraper.robots.requests.get", return_value=response, side_effect=side_effect)
    return checker, patcher


class TestRobotsRules(unittest.TestCase):
    def test_parsed_rules_are_obeyed(self):
        checker, patcher = _checker_with(_resp(200, "User-agent: *\nDisallow: /some/\n"))
        with patcher:
            self.assertFalse(checker.can_fetch(URL))
            self.assertTrue(checker.can_fetch("https://example.com/other"))
        self.assertEqual(checker.status["https://example.com"], "parsed")

    def test_crawl_delay_is_read(self):
        checker, patcher = _checker_with(_resp(200, "User-agent: *\nCrawl-delay: 7\n"))
        with patcher:
            self.assertEqual(checker.crawl_delay(URL), 7.0)

    def test_robots_is_fetched_once_per_domain(self):
        checker, patcher = _checker_with(_resp(404))
        with patcher as mock_get:
            checker.can_fetch(URL)
            checker.can_fetch("https://example.com/another")
            checker.crawl_delay(URL)
        self.assertEqual(mock_get.call_count, 1)


class TestRobotsUnavailable(unittest.TestCase):
    def test_404_means_no_rules_so_allowed(self):
        checker, patcher = _checker_with(_resp(404))
        with patcher:
            self.assertTrue(checker.can_fetch(URL))
        self.assertEqual(checker.status["https://example.com"], "missing")

    def test_410_means_no_rules_so_allowed(self):
        checker, patcher = _checker_with(_resp(410))
        with patcher:
            self.assertTrue(checker.can_fetch(URL))


class TestRobotsRefusedOrUnreachable(unittest.TestCase):
    def test_403_is_treated_as_a_refusal(self):
        checker, patcher = _checker_with(_resp(403))
        with patcher:
            self.assertFalse(checker.can_fetch(URL))
        self.assertEqual(checker.status["https://example.com"], "refused")

    def test_401_is_treated_as_a_refusal(self):
        checker, patcher = _checker_with(_resp(401))
        with patcher:
            self.assertFalse(checker.can_fetch(URL))

    def test_429_is_treated_as_a_refusal_not_as_missing(self):
        checker, patcher = _checker_with(_resp(429))
        with patcher:
            self.assertFalse(checker.can_fetch(URL))

    def test_5xx_means_complete_disallow(self):
        checker, patcher = _checker_with(_resp(503))
        with patcher:
            self.assertFalse(checker.can_fetch(URL))
            self.assertFalse(checker.can_fetch("https://example.com/"))
        self.assertEqual(checker.status["https://example.com"], "server_error")

    def test_network_error_means_complete_disallow(self):
        checker, patcher = _checker_with(side_effect=requests.exceptions.ConnectionError("down"))
        with patcher:
            self.assertFalse(checker.can_fetch(URL))
        self.assertEqual(checker.status["https://example.com"], "unreachable")

    def test_timeout_means_complete_disallow(self):
        checker, patcher = _checker_with(side_effect=requests.exceptions.Timeout("slow"))
        with patcher:
            self.assertFalse(checker.can_fetch(URL))

    def test_disallow_is_per_domain_only(self):
        responses = {
            "https://down.example/robots.txt": _resp(500),
            "https://fine.example/robots.txt": _resp(404),
        }
        checker = RobotsChecker(user_agent=UA)
        with patch("scraper.robots.requests.get", side_effect=lambda url, **kw: responses[url]):
            self.assertFalse(checker.can_fetch("https://down.example/page"))
            self.assertTrue(checker.can_fetch("https://fine.example/page"))


if __name__ == "__main__":
    unittest.main()
