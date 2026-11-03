import os
import unittest
from unittest.mock import patch

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_are_local_mock_urls(self) -> None:
        settings = Settings.from_env()
        self.assertEqual(settings.shopify_base_url, "http://localhost:8787/admin/api/2026-10")
        self.assertEqual(settings.smt_base_url, "http://localhost:8787/smt")
        self.assertEqual(settings.terminal_base_url, "http://localhost:8787/terminal")

    def test_environment_overrides_are_trimmed(self) -> None:
        values = {
            "ELUNO_SHOPIFY_BASE_URL": "http://shopify.test/",
            "ELUNO_SMT_BASE_URL": "http://smt.test/",
            "ELUNO_TERMINAL_BASE_URL": "http://terminal.test/",
            "ELUNO_DATABASE_PATH": "test.sqlite3",
            "ELUNO_HTTP_TIMEOUT_SECONDS": "2.5",
        }
        with patch.dict(os.environ, values, clear=False):
            settings = Settings.from_env()
        self.assertEqual(settings.shopify_base_url, "http://shopify.test")
        self.assertEqual(settings.smt_base_url, "http://smt.test")
        self.assertEqual(settings.terminal_base_url, "http://terminal.test")
        self.assertEqual(settings.database_path, "test.sqlite3")
        self.assertEqual(settings.http_timeout_seconds, 2.5)
