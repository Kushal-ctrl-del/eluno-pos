from datetime import date
import tempfile
import unittest
from pathlib import Path

from app.application.refunds import RefundError, RefundService
from app.domain.cart import Cart, CartLine
from app.domain.customer import Customer
from app.domain.offers import OfferEvaluation
from app.domain.prescription import EyePrescription, Prescription
from app.domain.tax import TaxRate, calculate_tax
from app.persistence.database import Database


class FakeTerminal:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def refund(self, charge_id, amount_paise, reason):
        self.calls.append((charge_id, amount_paise, reason))
        if self.error:
            raise self.error
        return {"refund_id": "trf_1", "status": "processed"}


class FakeShopify:
    def __init__(self, error=None):
        self.error = error
        self.create_calls = []
        self.refund = None
        self.order = {
            "id": 501,
            "location_id": 30000001,
            "transactions": [{
                "id": 700,
                "kind": "sale",
                "status": "success",
                "gateway": "terminal",
                "authorization": "chg_1",
                "amount": "110.00",
            }],
            "line_items": [
                {
                    "id": 801, "variant_id": 91000001, "price": "60.00",
                    "quantity": 1, "total_discount": "0.00",
                    "tax_lines": [{"price": "9.15"}],
                    "properties": [{"name": "line_type", "value": "frame"}],
                },
                {
                    "id": 802, "variant_id": 93000001, "price": "50.00",
                    "quantity": 1, "total_discount": "0.00",
                    "tax_lines": [{"price": "2.38"}],
                    "properties": [{"name": "line_type", "value": "lens"}],
                },
            ],
            "refunds": [],
        }

    def get_order(self, order_id):
        return self.order

    def calculate_refund(self, order_id, refund_line_items):
        self.calculated_lines = refund_line_items
        values = {801: ("50.85", "9.15"), 802: ("47.62", "2.38")}
        amount = sum(
            int(round(float(values[line["line_item_id"]][0]) * 100))
            + int(round(float(values[line["line_item_id"]][1]) * 100))
            for line in refund_line_items
        )
        return {
            "refund": {
                "refund_line_items": [
                    dict(line, subtotal=values[line["line_item_id"]][0],
                         total_tax=values[line["line_item_id"]][1])
                    for line in refund_line_items
                ],
                "transactions": [{
                    "amount": f"{amount // 100}.{amount % 100:02d}",
                    "parent_id": 700,
                    "gateway": "terminal",
                }],
            }
        }

    def create_refund(self, order_id, payload):
        self.create_calls.append(payload)
        if self.error:
            raise self.error
        self.refund = {
            "id": 901,
            "note_attributes": payload["note_attributes"],
            "refund_line_items": payload["refund_line_items"],
            "transactions": [{
                "kind": "refund",
                "amount": payload["transactions"][0]["amount"],
                "parent_id": payload["transactions"][0]["parent_id"],
            }],
        }
        self.order["refunds"].append(self.refund)
        return {"refund": self.refund}


class RefundTests(unittest.TestCase):
    def database(self):
        directory = tempfile.TemporaryDirectory()
        database = Database(str(Path(directory.name) / "pos.sqlite3"))
        database.initialize()
        self.addCleanup(directory.cleanup)
        return database

    def service(self, terminal=None, shopify=None):
        return RefundService(
            self.database(), terminal or FakeTerminal(), shopify or FakeShopify()
        )

    def test_full_refund_calculates_tax_reversal_and_restock_rules(self):
        terminal = FakeTerminal()
        shopify = FakeShopify()
        service = self.service(terminal, shopify)
        result = service.refund_full(
            refund_request_id="rf_1", order_id=501, reason="customer_return"
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(terminal.calls, [("chg_1", 11000, "customer_return")])
        self.assertEqual(shopify.calculated_lines[0]["restock_type"], "return")
        self.assertEqual(shopify.calculated_lines[0]["location_id"], 30000001)
        self.assertEqual(shopify.calculated_lines[1]["restock_type"], "no_restock")
        self.assertNotIn("location_id", shopify.calculated_lines[1])
        payload = shopify.create_calls[0]
        self.assertEqual(payload["transactions"][0]["parent_id"], 700)
        self.assertEqual(payload["transactions"][0]["amount"], "110.00")

    def test_partial_frame_refund_reverses_only_selected_line_and_restocks_frame(self):
        terminal = FakeTerminal()
        shopify = FakeShopify()
        result = self.service(terminal, shopify).refund_partial(
            refund_request_id="rf_frame", order_id=501, reason="frame_return",
            selected_lines=[{"line_item_id": 801, "quantity": 1}],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(terminal.calls, [("chg_1", 6000, "frame_return")])
        self.assertEqual(shopify.calculated_lines, [{
            "line_item_id": 801, "quantity": 1, "restock_type": "return",
            "location_id": 30000001,
        }])
        self.assertEqual(len(shopify.create_calls[0]["refund_line_items"]), 1)

    def test_partial_lens_refund_never_restocks_lens(self):
        terminal = FakeTerminal()
        shopify = FakeShopify()
        result = self.service(terminal, shopify).refund_partial(
            refund_request_id="rf_lens", order_id=501, reason="lens_return",
            selected_lines=[{"line_item_id": 802, "quantity": 1}],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(terminal.calls[0][1], 5000)
        self.assertEqual(shopify.calculated_lines[0]["restock_type"], "no_restock")
        self.assertNotIn("location_id", shopify.calculated_lines[0])

    def test_partial_mixed_refund_applies_each_line_handling(self):
        terminal = FakeTerminal()
        shopify = FakeShopify()
        result = self.service(terminal, shopify).refund_partial(
            refund_request_id="rf_mixed", order_id=501, reason="mixed_return",
            selected_lines=[
                {"line_item_id": 801, "quantity": 1},
                {"line_item_id": 802, "quantity": 1},
            ],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(terminal.calls[0][1], 11000)
        self.assertEqual(
            [line["restock_type"] for line in shopify.calculated_lines],
            ["return", "no_restock"],
        )

    def test_partial_refund_rejects_duplicate_or_over_refunded_quantity(self):
        terminal = FakeTerminal()
        shopify = FakeShopify()
        service = self.service(terminal, shopify)
        with self.assertRaisesRegex(RefundError, "more than once"):
            service.refund_partial(
                refund_request_id="rf_duplicate", order_id=501, reason="return",
                selected_lines=[
                    {"line_item_id": 801, "quantity": 1},
                    {"line_item_id": 801, "quantity": 1},
                ],
            )
        service.refund_partial(
            refund_request_id="rf_first", order_id=501, reason="return",
            selected_lines=[{"line_item_id": 801, "quantity": 1}],
        )
        with self.assertRaisesRegex(RefundError, "only 0 refundable"):
            service.refund_partial(
                refund_request_id="rf_second", order_id=501, reason="return",
                selected_lines=[{"line_item_id": 801, "quantity": 1}],
            )

    def test_duplicate_request_does_not_refund_again(self):
        terminal = FakeTerminal()
        shopify = FakeShopify()
        service = self.service(terminal, shopify)
        first = service.refund_full(refund_request_id="rf_1", order_id=501, reason="return")
        second = service.refund_full(refund_request_id="rf_1", order_id=501, reason="return")
        self.assertEqual(first["shopify_refund_id"], second["shopify_refund_id"])
        self.assertEqual(len(terminal.calls), 1)
        self.assertEqual(len(shopify.create_calls), 1)

    def test_repeated_request_returns_uncertain_state_without_refunding_again(self):
        terminal = FakeTerminal(error=TimeoutError("timeout"))
        shopify = FakeShopify()
        service = self.service(terminal, shopify)
        with self.assertRaises(RefundError):
            service.refund_full(refund_request_id="rf_uncertain", order_id=501, reason="return")
        state = service.refund_full(
            refund_request_id="rf_uncertain", order_id=501, reason="return"
        )
        self.assertEqual(state["status"], "terminal_refund_uncertain")
        self.assertEqual(len(terminal.calls), 1)
        self.assertEqual(len(shopify.create_calls), 0)

    def test_uncertain_terminal_refund_can_be_manually_resolved(self):
        terminal = FakeTerminal(error=TimeoutError("timeout"))
        service = self.service(terminal, FakeShopify())
        with self.assertRaises(RefundError):
            service.refund_full(refund_request_id="rf_manual", order_id=501, reason="return")
        resolved = service.resolve_terminal_uncertainty("rf_manual", "trf_manual")
        self.assertEqual(resolved["status"], "terminal_refunded")
        repeated = service.refund_full(
            refund_request_id="rf_manual", order_id=501, reason="return"
        )
        self.assertEqual(repeated["terminal_refund_id"], "trf_manual")

    def test_terminal_failure_stops_before_shopify_write(self):
        terminal = FakeTerminal(error=TimeoutError("timeout"))
        shopify = FakeShopify()
        service = self.service(terminal, shopify)
        with self.assertRaisesRegex(RefundError, "uncertain"):
            service.refund_full(refund_request_id="rf_1", order_id=501, reason="return")
        self.assertEqual(shopify.create_calls, [])
        record = service._get("rf_1")
        self.assertEqual(record["status"], "terminal_refund_uncertain")

    def test_shopify_failure_after_terminal_success_requires_reconciliation(self):
        terminal = FakeTerminal()
        shopify = FakeShopify(error=TimeoutError("timeout"))
        service = self.service(terminal, shopify)
        with self.assertRaisesRegex(RefundError, "Shopify"):
            service.refund_full(refund_request_id="rf_1", order_id=501, reason="return")
        self.assertEqual(service._get("rf_1")["status"], "shopify_refund_reconciliation_required")
        shopify.error = None
        with self.assertRaises(RefundError):
            service.reconcile("rf_1")

    def test_reconciliation_finds_existing_shopify_refund(self):
        terminal = FakeTerminal()
        shopify = FakeShopify(error=TimeoutError("timeout"))
        service = self.service(terminal, shopify)
        with self.assertRaises(RefundError):
            service.refund_full(refund_request_id="rf_1", order_id=501, reason="return")
        shopify.error = None
        shopify.order["refunds"] = [{
            "id": 902,
            "note_attributes": [{"name": "refund_request_id", "value": "rf_1"}],
            "refund_line_items": [
                {"line_item_id": 801, "quantity": 1, "restock_type": "return",
                 "location_id": 30000001, "subtotal": "50.85", "total_tax": "9.15"},
                {"line_item_id": 802, "quantity": 1, "restock_type": "no_restock",
                 "location_id": None, "subtotal": "47.62", "total_tax": "2.38"},
            ],
            "transactions": [{
                "kind": "refund", "amount": "110.00", "parent_id": 700,
            }],
        }]
        result = service.reconcile("rf_1")
        self.assertEqual(result["shopify_refund_id"], "902")

    def test_reconciliation_rejects_matching_reference_with_wrong_amount(self):
        terminal = FakeTerminal()
        shopify = FakeShopify(error=TimeoutError("timeout"))
        service = self.service(terminal, shopify)
        with self.assertRaises(RefundError):
            service.refund_full(refund_request_id="rf_bad", order_id=501, reason="return")
        shopify.order["refunds"] = [{
            "id": 903,
            "note_attributes": [{"name": "refund_request_id", "value": "rf_bad"}],
            "refund_line_items": [
                {"line_item_id": 801, "quantity": 1, "restock_type": "return",
                 "location_id": 30000001, "subtotal": "40.00", "total_tax": "9.15"},
                {"line_item_id": 802, "quantity": 1, "restock_type": "no_restock",
                 "location_id": None, "subtotal": "47.62", "total_tax": "2.38"},
            ],
            "transactions": [{"kind": "refund", "amount": "99.99", "parent_id": 700}],
        }]
        with self.assertRaisesRegex(RefundError, "amount"):
            service.reconcile("rf_bad")
