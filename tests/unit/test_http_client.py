import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from io import BytesIO

from app.adapters.http_client import HttpClientError, JsonHttpClient


class HttpClientTests(unittest.TestCase):
    def test_http_error_contains_status_and_body(self) -> None:
        error = HTTPError(
            "http://test",
            429,
            "Too Many Requests",
            {},
            BytesIO(b'{"errors":"slow down"}'),
        )
        with patch("app.adapters.http_client.urlopen", side_effect=error):
            with self.assertRaises(HttpClientError) as raised:
                JsonHttpClient().get_json("http://test")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("slow down", raised.exception.body)

    def test_transport_error_is_clear(self) -> None:
        with patch(
            "app.adapters.http_client.urlopen",
            side_effect=URLError("connection refused"),
        ):
            with self.assertRaises(HttpClientError) as raised:
                JsonHttpClient().get_json("http://test")
        self.assertIn("Unable to reach", str(raised.exception))

    def test_invalid_json_is_reported(self) -> None:
        response = unittest.mock.Mock(status=200)
        response.read.return_value = b"not-json"
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *args: None
        with patch("app.adapters.http_client.urlopen", return_value=response):
            with self.assertRaises(HttpClientError) as raised:
                JsonHttpClient().get_json("http://test")
        self.assertIn("Invalid JSON", str(raised.exception))
