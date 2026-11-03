import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from app.composition import build_application
from app.config import Settings
from app.entrypoints.web import PosHandler


class WebApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock_port = _free_port()
        mock_path = Path(__file__).parents[2] / "starter-kit" / "mock-apis" / "mock_server.py"
        cls.mock = subprocess.Popen(
            [sys.executable, str(mock_path), "--no-flake", "--port", str(cls.mock_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.database = tempfile.TemporaryDirectory()
        cls.web_port = _free_port()
        settings = Settings(
            shopify_base_url=f"http://127.0.0.1:{cls.mock_port}/admin/api/2026-10",
            smt_base_url=f"http://127.0.0.1:{cls.mock_port}/smt",
            terminal_base_url=f"http://127.0.0.1:{cls.mock_port}/terminal",
            database_path=str(Path(cls.database.name) / "web.sqlite3"),
        )
        PosHandler.application = build_application(settings)
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.web_port), PosHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                _get(f"http://127.0.0.1:{cls.web_port}/api/products?type=Frame")
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("web server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        cls.mock.terminate()
        cls.mock.wait(timeout=5)
        cls.database.cleanup()

    def test_seeded_customer_quote_validates_lens_and_tax(self):
        body = {
            "phone": "+919000000005",
            "lines": [
                {"line_id": "frame-1", "variant_id": 91000001, "line_type": "frame", "pair_ref": "p1"},
                {"line_id": "lens-1", "variant_id": 91000041, "line_type": "lens", "pair_ref": "p1"},
            ],
        }
        response = _post(self._url("/api/quote"), body)
        self.assertEqual(response["customer"]["id"], 80001005)
        self.assertEqual(response["prescription"]["rx_id"], "RX5007")
        self.assertEqual(response["tax"]["total_paise"], 1269720)
        self.assertEqual(response["offers"]["applied"][0]["offer_id"], "FESTIVE10")

    def test_seeded_quote_can_complete_terminal_and_shopify_checkout(self):
        body = {
            "phone": "+919000000005",
            "checkout_id": "integration-web-checkout",
            "lines": [
                {"line_id": "frame-1", "variant_id": 91000001, "line_type": "frame", "pair_ref": "p1"},
                {"line_id": "lens-1", "variant_id": 91000041, "line_type": "lens", "pair_ref": "p1"},
            ],
            "payment_method": "upi",
            "sales_channel": "walk_in",
            "store_code": "HSR",
            "location_id": 70000002,
            "staff_id": "S105",
            "invoice_prefix": "ELU/HSR/FY26/",
        }
        result = _post(self._url("/api/checkout"), body)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["invoice_number"])
        self.assertTrue(result["shopify_order_id"])
        self.assertTrue(result["charge_id"])

    def test_checkout_recovery_read_and_manual_resolution_do_not_write_financials(self):
        with PosHandler.application.database.connection() as connection:
            connection.execute(
                """INSERT INTO checkout_records
                   (checkout_id, store_code, invoice_number, status,
                    terminal_order_ref, terminal_charge_id, payment_amount_paise,
                    payment_method, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("web-recovery-test", "HSR", "ELU/HSR/FY26/099999",
                 "shopify_reconciliation_required", "eluno:web-recovery-test",
                 "chg-recovery-test", 1000, "upi", "now", "now"),
            )
        listed = _get(self._url("/api/checkout/recovery"))
        self.assertTrue(any(
            item["checkout_id"] == "web-recovery-test"
            for item in listed["checkouts"]
        ))
        status = _get(self._url("/api/checkout/status?id=web-recovery-test"))
        self.assertEqual(status["checkout"]["status"], "shopify_reconciliation_required")
        resolution = _post(self._url("/api/checkout/manual-resolution"), {
            "checkout_id": "web-recovery-test",
            "actor_id": "S105",
            "reason": "Held for operator follow-up.",
        })
        self.assertTrue(resolution["resolution"]["financial_state_unchanged"])
        status = _get(self._url("/api/checkout/status?id=web-recovery-test"))
        self.assertEqual(status["checkout"]["status"], "shopify_reconciliation_required")

    def _url(self, path):
        return f"http://127.0.0.1:{self.web_port}{path}"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url):
    with urlopen(url, timeout=2) as response:
        return json.loads(response.read())


def _post(url, body):
    request = Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())
