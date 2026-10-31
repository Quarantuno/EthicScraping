"""Tests for scraper/fetcher.py's retry-with-backoff and Content-Type
filtering. Robots.txt checking is bypassed by patching RobotsChecker's
own methods directly (rather than mocking requests.get for it), so that
mocking requests.get for the actual page fetch doesn't also have to
account for the separate robots.txt request against the very same
global `requests` module -- no real network traffic either way,
consistent with the rest of this project's test suite."""
import io
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import requests

from scraper.fetcher import EthicalFetcher, decode_html, parse_retry_after
from scraper.robots import RobotsChecker


@contextmanager
def _allow_all_robots():
    with patch.object(RobotsChecker, "can_fetch", return_value=True), \
            patch.object(RobotsChecker, "crawl_delay", return_value=None):
        yield


def _make_fetcher(**kwargs):
    kwargs.setdefault("max_retries", 2)
    kwargs.setdefault("backoff_seconds", 0.0)  # keep tests instant
    kwargs.setdefault("default_delay", 0.0)
    kwargs.setdefault("check_tdm_reservation", False)  # unrelated to these tests
    return EthicalFetcher(user_agent="TestBot/1.0 (contact@example.com)", **kwargs)


def _html_response(content_type="text/html; charset=utf-8"):
    resp = MagicMock(status_code=200, text="<html><body>hello</body></html>")
    resp.headers = {"Content-Type": content_type}
    return resp


class TestRetryWithBackoff(unittest.TestCase):
    def test_succeeds_on_first_try_without_retrying(self):
        with _allow_all_robots(), patch(
            "scraper.fetcher.requests.get", return_value=_html_response()
        ) as mock_get:
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(mock_get.call_count, 1)

    def test_retries_after_connection_error_then_succeeds(self):
        with _allow_all_robots(), patch(
            "scraper.fetcher.requests.get",
            side_effect=[requests.exceptions.ConnectionError("boom"), _html_response()],
        ) as mock_get:
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(mock_get.call_count, 2)

    def test_retries_after_transient_5xx_then_succeeds(self):
        with _allow_all_robots(), patch(
            "scraper.fetcher.requests.get",
            side_effect=[MagicMock(status_code=503), _html_response()],
        ) as mock_get:
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(mock_get.call_count, 2)

    def test_does_not_retry_on_4xx(self):
        with _allow_all_robots(), patch(
            "scraper.fetcher.requests.get", return_value=MagicMock(status_code=404)
        ) as mock_get:
            result = _make_fetcher().fetch("https://example.com/missing")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.status_code, 404)
        self.assertEqual(mock_get.call_count, 1)

    def test_gives_up_after_max_retries_on_persistent_5xx(self):
        with _allow_all_robots(), patch(
            "scraper.fetcher.requests.get", return_value=MagicMock(status_code=502)
        ) as mock_get:
            result = _make_fetcher(max_retries=2).fetch("https://example.com/flaky")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(mock_get.call_count, 3)  # 1 initial + 2 retries
        self.assertEqual(result.attempts, 3)

    def test_gives_up_after_max_retries_on_persistent_connection_error(self):
        with _allow_all_robots(), patch(
            "scraper.fetcher.requests.get",
            side_effect=requests.exceptions.ConnectionError("still down"),
        ) as mock_get:
            result = _make_fetcher(max_retries=1).fetch("https://example.com/down")
        self.assertEqual(result.status, "error")
        self.assertIn("still down", result.error)
        self.assertEqual(mock_get.call_count, 2)  # 1 initial + 1 retry


def _status_response(code, retry_after=None):
    resp = MagicMock(status_code=code)
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return resp


class TestParseRetryAfter(unittest.TestCase):
    def test_delay_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)

    def test_http_date(self):
        # 2015-10-21 07:28:30 GMT is 30s after "now" below.
        now = 1445412480.0  # 2015-10-21 07:28:00 GMT
        self.assertAlmostEqual(
            parse_retry_after("Wed, 21 Oct 2015 07:28:30 GMT", now=now), 30.0, places=3)

    def test_http_date_in_the_past_is_zero(self):
        self.assertEqual(parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"), 0.0)

    def test_missing_or_garbage_is_none(self):
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after(""))
        self.assertIsNone(parse_retry_after("soon-ish"))
        self.assertIsNone(parse_retry_after(MagicMock()))


class TestTooManyRequests(unittest.TestCase):
    def test_429_is_retried_then_succeeds(self):
        with _allow_all_robots(), patch("scraper.fetcher.time.sleep"), patch(
            "scraper.fetcher.requests.get",
            side_effect=[_status_response(429), _html_response()],
        ) as mock_get:
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")
        self.assertEqual(mock_get.call_count, 2)

    def test_retry_after_is_honored_when_longer_than_backoff(self):
        with _allow_all_robots(), patch("scraper.fetcher.time.sleep") as mock_sleep, patch(
            "scraper.fetcher.requests.get",
            side_effect=[_status_response(429, "7"), _html_response()],
        ):
            result = _make_fetcher(backoff_seconds=1.0).fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")
        slept = [c.args[0] for c in mock_sleep.call_args_list]
        self.assertIn(7.0, slept)

    def test_retry_after_on_503_is_honored_too(self):
        with _allow_all_robots(), patch("scraper.fetcher.time.sleep") as mock_sleep, patch(
            "scraper.fetcher.requests.get",
            side_effect=[_status_response(503, "4"), _html_response()],
        ):
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")
        self.assertIn(4.0, [c.args[0] for c in mock_sleep.call_args_list])

    def test_retry_after_beyond_cap_gives_up_without_waiting(self):
        with _allow_all_robots(), patch("scraper.fetcher.time.sleep") as mock_sleep, patch(
            "scraper.fetcher.requests.get", return_value=_status_response(429, "3600"),
        ) as mock_get:
            fetcher = _make_fetcher(max_retry_after_seconds=60)
            result = fetcher.fetch("https://example.com/page")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.status_code, 429)
        self.assertEqual(mock_get.call_count, 1)
        self.assertNotIn(3600.0, [c.args[0] for c in mock_sleep.call_args_list])

    def test_429_raises_the_domain_delay_for_later_requests(self):
        with _allow_all_robots(), patch("scraper.fetcher.time.sleep"), patch(
            "scraper.fetcher.requests.get",
            side_effect=[_status_response(429, "9"), _html_response()],
        ):
            fetcher = _make_fetcher()
            fetcher.fetch("https://example.com/page")
        self.assertEqual(fetcher.rate_limiter.get_delay("https://example.com/other"), 9.0)
        # Other domains are unaffected.
        self.assertEqual(fetcher.rate_limiter.get_delay("https://other.example/"), 0.0)

    def test_retry_after_raise_is_capped(self):
        with _allow_all_robots(), patch("scraper.fetcher.time.sleep"), patch(
            "scraper.fetcher.requests.get", return_value=_status_response(429, "3600"),
        ):
            fetcher = _make_fetcher(max_retry_after_seconds=60)
            fetcher.fetch("https://example.com/page")
        self.assertEqual(fetcher.rate_limiter.get_delay("https://example.com/"), 60.0)

    def test_persistent_429_gives_up_after_max_retries(self):
        with _allow_all_robots(), patch("scraper.fetcher.time.sleep"), patch(
            "scraper.fetcher.requests.get", return_value=_status_response(429),
        ) as mock_get:
            result = _make_fetcher(max_retries=2).fetch("https://example.com/page")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.status_code, 429)
        self.assertEqual(mock_get.call_count, 3)


class TestAiOptoutWiring(unittest.TestCase):
    def test_x_robots_tag_noai_is_reported(self):
        resp = _html_response()
        resp.headers = {"Content-Type": "text/html", "X-Robots-Tag": "noai, noimageai"}
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.ai_optout["opted_out"])
        self.assertEqual(result.ai_optout["source"], "x_robots_tag")
        self.assertEqual(result.x_robots_tag, "noai, noimageai")

    def test_plain_page_is_not_opted_out(self):
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=_html_response()):
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertFalse(result.ai_optout["opted_out"])


class TrackingBytesIO(io.BytesIO):
    """Records how many bytes were actually read from the 'network'."""

    def __init__(self, data):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, *args, **kwargs):
        chunk = super().read(*args, **kwargs)
        self.bytes_read += len(chunk)
        return chunk


def _real_response(body: bytes, content_type="text/html", status=200, extra_headers=None):
    """A genuine requests.Response streaming from memory, so the real
    streaming / size-cap / decoding code paths run."""
    resp = requests.Response()
    resp.status_code = status
    resp.headers["Content-Type"] = content_type
    for k, v in (extra_headers or {}).items():
        resp.headers[k] = v
    resp.raw = TrackingBytesIO(body)
    resp.url = "https://example.com/page"
    return resp


class TestStreamingDownload(unittest.TestCase):
    def test_html_is_read_and_decoded(self):
        body = '<html><head><link rel="canonical" href="/page"></head><body>perché è così</body></html>'
        resp = _real_response(body.encode("utf-8"))  # no charset in the header
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp) as get:
            result = _make_fetcher().fetch("https://example.com/page?utm_source=x")
        self.assertEqual(result.status, "ok")
        self.assertIn("perché è così", result.html)
        self.assertEqual(result.canonical_url, "https://example.com/page")
        self.assertTrue(get.call_args.kwargs["stream"])

    def test_skipped_content_type_never_downloads_the_body(self):
        resp = _real_response(b"%PDF-1.4" + b"x" * 100000, content_type="application/pdf")
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher().fetch("https://example.com/doc")
        self.assertEqual(result.status, "skipped_content_type")
        self.assertEqual(resp.raw.bytes_read, 0)

    def test_declared_content_length_over_the_cap_is_skipped_without_reading(self):
        resp = _real_response(b"<html>" + b"x" * 5000, extra_headers={"Content-Length": "5006"})
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher(max_response_bytes=1000).fetch("https://example.com/big")
        self.assertEqual(result.status, "skipped_too_large")
        self.assertEqual(resp.raw.bytes_read, 0)

    def test_undeclared_size_is_capped_while_streaming(self):
        resp = _real_response(b"<html>" + b"x" * 500000)
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher(max_response_bytes=100000).fetch("https://example.com/big")
        self.assertEqual(result.status, "skipped_too_large")
        self.assertLess(resp.raw.bytes_read, 500000)

    def test_page_just_under_the_cap_is_kept(self):
        resp = _real_response(b"<html><body>" + b"a" * 900 + b"</body></html>")
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher(max_response_bytes=1000).fetch("https://example.com/ok")
        self.assertEqual(result.status, "ok")


class TestDecodeHtml(unittest.TestCase):
    def test_utf8_without_declared_charset(self):
        self.assertEqual(decode_html("città".encode("utf-8"), "text/html"), "città")

    def test_header_charset_wins(self):
        self.assertEqual(decode_html("città".encode("latin-1"), "text/html; charset=ISO-8859-1"), "città")

    def test_meta_charset_used_when_header_has_none(self):
        body = '<meta charset="windows-1252"><p>città</p>'.encode("cp1252")
        self.assertIn("città", decode_html(body, "text/html"))

    def test_undeclared_non_utf8_falls_back_without_raising(self):
        self.assertEqual(decode_html("città".encode("cp1252"), "text/html"), "città")

    def test_unknown_charset_is_ignored(self):
        self.assertEqual(decode_html("ok".encode(), "text/html; charset=nonsense-42"), "ok")


class TestContentTypeFiltering(unittest.TestCase):
    def test_html_is_processed_normally(self):
        with _allow_all_robots(), patch(
            "scraper.fetcher.requests.get", return_value=_html_response()
        ):
            result = _make_fetcher().fetch("https://example.com/page.html")
        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.html)

    def test_pdf_is_skipped_without_parsing(self):
        resp = MagicMock(status_code=200, text="%PDF-1.4 binary garbage")
        resp.headers = {"Content-Type": "application/pdf"}
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher().fetch("https://example.com/doc.pdf")
        self.assertEqual(result.status, "skipped_content_type")
        self.assertIsNone(result.html)
        self.assertEqual(result.content_type, "application/pdf")

    def test_json_is_skipped(self):
        resp = MagicMock(status_code=200, text='{"a": 1}')
        resp.headers = {"Content-Type": "application/json; charset=utf-8"}
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher().fetch("https://example.com/api/data")
        self.assertEqual(result.status, "skipped_content_type")

    def test_missing_content_type_header_is_not_blocked(self):
        resp = MagicMock(status_code=200, text="<html><body>hi</body></html>")
        resp.headers = {}
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            result = _make_fetcher().fetch("https://example.com/page")
        self.assertEqual(result.status, "ok")

    def test_custom_allowed_content_types_extends_defaults(self):
        resp = MagicMock(status_code=200, text="plain text content " * 20)
        resp.headers = {"Content-Type": "text/plain"}
        with _allow_all_robots(), patch("scraper.fetcher.requests.get", return_value=resp):
            fetcher = _make_fetcher(allowed_content_types={"text/html", "text/plain"})
            result = fetcher.fetch("https://example.com/notes.txt")
        self.assertEqual(result.status, "ok")


if __name__ == "__main__":
    unittest.main()
