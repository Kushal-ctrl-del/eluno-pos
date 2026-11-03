import socket
from datetime import date
from pathlib import Path
import subprocess
import sys
import time
import unittest

from app.adapters.http_client import JsonHttpClient
from app.adapters.shopify_orders import ShopifyOrderClient
from app.application.reporting import ReportingService
from app.config import Settings


class ReportingApiTests(unittest.TestCase):
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

    def _create_order(self, amount, method, channel):
        response = self.shopify.create_order({
            "taxes_included": True,
            "customer": {"id": 80001005},
            "location_id": 70000001,
            "source_name": "reporting_test",
            "line_items": [{
                "variant_id": 91000001, "price": f"{amount // 100}.{amount % 100:02d}",
                "quantity": 1, "total_discount": "0.00",
                "tax_lines": [{"title": "CGST", "price": "0.00", "rate": 0.09},
                              {"title": "SGST", "price": "0.00", "rate": 0.09}],
                "properties": [{"name": "line_type", "value": "frame"}],
            }],
            "transactions": [{
                "kind": "sale", "amount": f"{amount // 100}.{amount % 100:02d}",
                "gateway": "terminal", "source": method,
            }],
            "note_attributes": [
                {"name": "store_code", "value": "RPT"},
                {"name": "payment_method", "value": method},
                {"name": "channel", "value": channel},
            ],
        })["order"]
        return response

    def test_report_reads_multiple_orders_and_refund_from_mock(self):
        first = self._create_order(10000, "upi", "walk_in")
        second = self._create_order(20000, "card", "whatsapp_booked")
        self.shopify.create_refund(first["id"], {
            "refund_line_items": [{
                "line_item_id": first["line_items"][0]["id"],
                "quantity": 1, "restock_type": "no_restock",
            }],
            "transactions": [{
                "kind": "refund", "amount": "100.00",
                "gateway": "terminal", "parent_id": first["transactions"][0]["id"],
            }],
        })
        business_date = first["created_at"][:10]
        report = ReportingService(self.shopify).day_end("RPT", date.fromisoformat(business_date))
        self.assertEqual(report.bill_count, 2)
        self.assertEqual(report.gross_sales_paise, 30000)
        self.assertEqual(report.refund_count, 1)
        self.assertEqual(report.refund_amount_paise, 10000)
        self.assertEqual(report.payment_method_paise, {"upi": 10000, "card": 20000})
        self.assertEqual(
            report.sales_channel_paise,
            {"walk_in": 10000, "whatsapp_booked": 20000},
        )
