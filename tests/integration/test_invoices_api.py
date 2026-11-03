import socket
from pathlib import Path
import subprocess
import sys
import time
import unittest

from app.adapters.http_client import JsonHttpClient
from app.adapters.shopify_orders import ShopifyOrderClient
from app.application.invoices import InvoiceService
from app.config import Settings


class InvoiceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        mock_path = Path(__file__).parents[2] / "starter-kit" / "mock-apis" / "mock_server.py"
        cls.process = subprocess.Popen(
            [sys.executable, str(mock_path), "--no-flake", "--port", str(cls.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.http = JsonHttpClient(timeout_seconds=1)
        cls.settings = Settings(
            shopify_base_url=f"http://127.0.0.1:{cls.port}/admin/api/2026-10"
        )
        cls.shopify = ShopifyOrderClient(cls.settings, cls.http)
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

    def test_invoice_service_reads_created_order_and_location(self):
        response = self.shopify.create_order({
            "taxes_included": True,
            "customer": {
                "id": 80001005, "first_name": "Karthik", "last_name": "Reddy",
                "phone": "+919000000005",
            },
            "location_id": 70000002,
            "source_name": "invoice_test",
            "line_items": [{
                "variant_id": 91000001, "price": "100.00", "quantity": 1,
                "total_discount": "0.00",
                "tax_lines": [],
                "properties": [
                    {"name": "line_type", "value": "frame"},
                    {"name": "hsn", "value": "90031900"},
                ],
            }],
            "transactions": [{
                "kind": "sale", "amount": "100.00",
                "gateway": "terminal", "authorization": "term-invoice-1",
            }],
            "note_attributes": [
                {"name": "invoice_no", "value": "ELU/HSR/FY26/TEST"},
                {"name": "store_code", "value": "HSR"},
                {"name": "staff_id", "value": "S105"},
                {"name": "payment_method", "value": "card"},
            ],
        })
        rendered = InvoiceService(self.shopify).render(response["order"]["id"])
        self.assertIn("Eluno HSR Layout", rendered)
        self.assertIn("ELU/HSR/FY26/TEST", rendered)
        self.assertIn("CARD 100.00 ref term-invoice-1", rendered)
