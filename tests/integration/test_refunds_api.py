import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from app.adapters.http_client import JsonHttpClient
from app.adapters.shopify_orders import ShopifyOrderClient
from app.adapters.terminal_client import TerminalClient
from app.application.refunds import RefundService
from app.config import Settings
from app.persistence.database import Database


class RefundApiTests(unittest.TestCase):
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

    def test_full_refund_uses_mock_terminal_and_shopify_contracts(self):
        terminal = TerminalClient(self.settings, self.http)
        shopify = ShopifyOrderClient(self.settings, self.http)
        charge = terminal.charge(10000, "upi", "integration-refund-1")
        response = shopify.create_order({
            "taxes_included": True,
            "customer": {"id": 80001005},
            "location_id": 70000001,
            "source_name": "eluno_pos_test",
            "line_items": [
                {
                    "variant_id": 91000001,
                    "price": "60.00",
                    "quantity": 1,
                    "total_discount": "0.00",
                    "tax_lines": [{"title": "CGST", "price": "0.00", "rate": 0.09},
                                  {"title": "SGST", "price": "0.00", "rate": 0.09}],
                    "properties": [{"name": "line_type", "value": "frame"}],
                },
                {
                    "variant_id": 91000083,
                    "price": "40.00",
                    "quantity": 1,
                    "total_discount": "0.00",
                    "tax_lines": [{"title": "CGST", "price": "0.00", "rate": 0.025},
                                  {"title": "SGST", "price": "0.00", "rate": 0.025}],
                    "properties": [{"name": "line_type", "value": "lens"}],
                },
            ],
            "transactions": [{
                "kind": "sale",
                "amount": "100.00",
                "gateway": "terminal",
                "charge_id": charge["charge_id"],
            }],
            "note_attributes": [{"name": "invoice_no", "value": "ELU/TEST/000001"}],
        })
        order_id = response["order"]["id"]
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "refund.sqlite3"))
            database.initialize()
            result = RefundService(database, terminal, shopify).refund_full(
                refund_request_id="integration-rf-1",
                order_id=order_id,
                reason="integration_test",
            )
        self.assertEqual(result["status"], "completed")

    def test_order_inventory_behaviour_decrements_frame_stock(self):
        shopify = ShopifyOrderClient(self.settings, self.http)
        inventory_url = (
            f"{self.settings.shopify_base_url}/inventory_levels.json"
            "?inventory_item_ids=92000001&location_ids=70000001"
        )
        before = self.http.get_json(inventory_url)["inventory_levels"][0]["available"]
        response = shopify.create_order({
            "taxes_included": True,
            "customer": {"id": 80001005},
            "location_id": 70000001,
            "inventory_behaviour": "decrement_obeying_policy",
            "line_items": [{
                "variant_id": 91000001,
                "price": "60.00",
                "quantity": 1,
                "total_discount": "0.00",
                "tax_lines": [],
                "properties": [
                    {"name": "line_type", "value": "frame"},
                    {"name": "hsn", "value": "90031900"},
                ],
            }],
            "transactions": [],
            "note_attributes": [{"name": "checkout_ref", "value": "inventory-check-1"}],
        })
        self.assertIn("id", response["order"])
        after = self.http.get_json(inventory_url)["inventory_levels"][0]["available"]
        self.assertEqual(after, before - 1)

    def test_partial_frame_refund_uses_mock_calculation_and_restock_contract(self):
        terminal = TerminalClient(self.settings, self.http)
        shopify = ShopifyOrderClient(self.settings, self.http)
        charge = terminal.charge(6000, "card", "integration-partial-1")
        response = shopify.create_order({
            "taxes_included": True,
            "customer": {"id": 80001005},
            "location_id": 70000001,
            "source_name": "eluno_pos_test",
            "line_items": [{
                "variant_id": 91000001, "price": "60.00", "quantity": 1,
                "total_discount": "0.00",
                "tax_lines": [{"title": "CGST", "price": "0.00", "rate": 0.09},
                              {"title": "SGST", "price": "0.00", "rate": 0.09}],
                "properties": [{"name": "line_type", "value": "frame"}],
            }, {
                "variant_id": 91000083, "price": "40.00", "quantity": 1,
                "total_discount": "0.00",
                "tax_lines": [{"title": "CGST", "price": "0.00", "rate": 0.025},
                              {"title": "SGST", "price": "0.00", "rate": 0.025}],
                "properties": [{"name": "line_type", "value": "lens"}],
            }],
            "transactions": [{
                "kind": "sale", "amount": "100.00", "gateway": "terminal",
                "charge_id": charge["charge_id"],
            }],
            "note_attributes": [{"name": "invoice_no", "value": "ELU/TEST/000002"}],
        })
        order = response["order"]
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "refund.sqlite3"))
            database.initialize()
            result = RefundService(database, terminal, shopify).refund_partial(
                refund_request_id="integration-partial-rf-1",
                order_id=order["id"],
                reason="frame_return",
                selected_lines=[{
                    "line_item_id": order["line_items"][0]["id"], "quantity": 1,
                }],
            )
        self.assertEqual(result["status"], "completed")
        persisted = shopify.get_order(order["id"])["refunds"][0]
        self.assertEqual(len(persisted["refund_line_items"]), 1)
        self.assertEqual(persisted["refund_line_items"][0]["restock_type"], "return")
        self.assertEqual(persisted["refund_line_items"][0]["location_id"], 70000001)
