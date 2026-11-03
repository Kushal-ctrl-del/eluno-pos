import socket
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from app.adapters.http_client import JsonHttpClient
from app.adapters.shopify_client import ShopifyClient
from app.adapters.smt_client import SmtClient
from app.application.customer_prescription import CustomerPrescriptionService
from app.config import Settings
from app.domain.prescription import LensSelection


class CustomerPrescriptionApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        mock_path = Path(__file__).parents[2] / "starter-kit" / "mock-apis" / "mock_server.py"
        cls.process = subprocess.Popen(
            [sys.executable, str(mock_path), "--no-flake", "--port", str(cls.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.settings = Settings(
            shopify_base_url=f"http://127.0.0.1:{cls.port}/admin/api/2026-10",
            smt_base_url=f"http://127.0.0.1:{cls.port}/smt",
            terminal_base_url=f"http://127.0.0.1:{cls.port}/terminal",
        )
        cls.http = JsonHttpClient(timeout_seconds=1)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                cls.http.get_json(f"http://127.0.0.1:{cls.port}/health")
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("mock server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)

    def test_customer_latest_prescription_and_lens_validation(self):
        shopify = ShopifyClient(self.settings, self.http)
        smt = SmtClient(self.settings, self.http)
        service = CustomerPrescriptionService(
            shopify,
            smt,
            shopify,
            today=date(2026, 11, 2),
        )
        customer, rx = service.load_customer_with_latest_prescription("+919000000005")
        self.assertEqual(customer.customer_id, 80001005)
        self.assertEqual(rx.rx_id, "RX5007")
        self.assertEqual(rx.valid_until, date(2027, 7, 10))

        issues = service.validate_lens_selection(
            rx,
            91000083,
            LensSelection("progressive", 1.6, "blue_cut"),
        )
        self.assertEqual(issues, [])

    def test_expired_customer_prescriptions_are_rejected(self):
        shopify = ShopifyClient(self.settings, self.http)
        smt = SmtClient(self.settings, self.http)
        service = CustomerPrescriptionService(
            shopify,
            smt,
            shopify,
            today=date(2026, 11, 2),
        )
        with self.assertRaisesRegex(Exception, "expired"):
            service.load_customer_with_latest_prescription("+919000000000")
