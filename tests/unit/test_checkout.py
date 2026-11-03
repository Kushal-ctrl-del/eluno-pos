from datetime import date
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from app.adapters.http_client import HttpClientError
from app.adapters.terminal_client import TerminalClient
from app.application.checkout import CheckoutError, CheckoutRequest, CheckoutService
from app.config import Settings
from app.domain.cart import Cart, CartLine
from app.domain.customer import Customer
from app.domain.offers import AppliedOffer, DiscountAllocation, OfferEvaluation
from app.domain.prescription import EyePrescription, Prescription
from app.domain.tax import TaxRate, calculate_tax
from app.persistence.database import Database


class FakeHttp:
    def __init__(self):
        self.calls = []
        self.fail_once = True

    def request_json(self, method, url, *, payload=None, expected_statuses=(200,)):
        self.calls.append((method, url, payload))
        if self.fail_once:
            self.fail_once = False
            raise HttpClientError("temporary", status_code=503)
        return {"charge_id": "chg_1", "status": "captured", "order_ref": payload["order_ref"]}

    def get_json(self, url):
        return {"charge_id": "chg_1", "status": "captured"}


class FakeTerminal:
    def __init__(self):
        self.calls = []

    def charge(self, amount_paise, method, order_ref):
        self.calls.append((amount_paise, method, order_ref))
        return {"charge_id": "chg_1", "status": "captured"}

    def get_charge(self, charge_id):
        return {"charge_id": charge_id, "status": "captured", "order_ref": "eluno:co_1"}


class UncertainTerminal(FakeTerminal):
    def charge(self, amount_paise, method, order_ref):
        self.calls.append((amount_paise, method, order_ref))
        if len(self.calls) == 1:
            raise TimeoutError("terminal timeout")
        return {"charge_id": "chg_recovered", "status": "captured"}


class FakeShopify:
    def __init__(self, response=None, error=None, matches=None):
        self.response = response
        self.error = error
        self.matches = matches or []
        self.payloads = []

    def create_order(self, payload):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.response or {"order": {"id": 123}}

    def find_orders(self, checkout_id):
        return self.matches


def request(checkout_id="co_1"):
    cart = Cart((
        CartLine("frame", "frame", 91000001, 10000, pair_ref="p1", hsn="90031900"),
        CartLine("lens", "lens", 93000001, 10000, pair_ref="p1", hsn="90015000"),
    ))
    tax = calculate_tax(
        cart,
        (
            TaxRate("90031900", 1800, date(2025, 1, 1)),
            TaxRate("90015000", 500, date(2025, 1, 1)),
        ),
        as_of=date(2026, 11, 2),
    )
    eye = EyePrescription(-1.0, 0.0, None, None)
    prescription = Prescription(
        "RX1", 1, "+910", date(2026, 1, 1), date(2027, 1, 1),
        "in_store", eye, eye,
    )
    return CheckoutRequest(
        checkout_id, Customer(1, "+910", (), 0), prescription, cart,
        OfferEvaluation((), (), 0), tax, "HSR", 1, "staff-1", "ELU/HSR/",
        "upi",
    )


class CheckoutTests(unittest.TestCase):
    def database(self):
        directory = tempfile.TemporaryDirectory()
        database = Database(str(Path(directory.name) / "pos.sqlite3"))
        database.initialize()
        self.addCleanup(directory.cleanup)
        return database

    def test_terminal_503_retries_same_request_and_order_ref(self):
        http = FakeHttp()
        terminal = TerminalClient(
            Settings(terminal_base_url="http://terminal"),
            http,
        )
        result = terminal.charge(2500, "upi", "eluno:co_1")
        self.assertEqual(result["status"], "captured")
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(http.calls[0][2], http.calls[1][2])
        self.assertEqual(http.calls[0][2]["order_ref"], "eluno:co_1")

    def test_success_persists_payment_and_shopify_order(self):
        database = self.database()
        shopify = FakeShopify()
        service = CheckoutService(database, FakeTerminal(), shopify)
        result = service.execute(request())
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.invoice_number, "ELU/HSR/000001")
        payload = shopify.payloads[0]
        self.assertTrue(payload["taxes_included"])
        self.assertEqual(payload["customer"], {"id": 1})
        self.assertEqual(payload["note_attributes"][-1], {"name": "checkout_ref", "value": "co_1"})
        self.assertEqual(payload["line_items"][0]["properties"][2]["value"], "90031900")
        self.assertEqual(payload["line_items"][1]["properties"][3]["value"], "RX1")
        self.assertEqual(payload["inventory_behaviour"], "decrement_obeying_policy")
        self.assertEqual(
            [tax_line["rate"] for tax_line in payload["line_items"][0]["tax_lines"]],
            [0.09, 0.09],
        )

    def test_checkout_validates_and_persists_supported_sales_channel(self):
        database = self.database()
        shopify = FakeShopify()
        service = CheckoutService(database, FakeTerminal(), shopify)
        result = service.execute(replace(request(), sales_channel="whatsapp_booked"))
        self.assertEqual(result.status, "completed")
        attributes = shopify.payloads[0]["note_attributes"]
        self.assertIn({"name": "channel", "value": "whatsapp_booked"}, attributes)
        with self.assertRaisesRegex(CheckoutError, "Sales channel"):
            service.execute(replace(request("co_bad"), sales_channel="website"))

    def test_manager_discount_reason_is_carried_to_audit(self):
        database = self.database()
        shopify = FakeShopify()
        service = CheckoutService(database, FakeTerminal(), shopify)
        allocation = DiscountAllocation("MANAGER_OVERRIDE", "frame", 500)
        evaluated = OfferEvaluation(
            (AppliedOffer("MANAGER_OVERRIDE", 500, (allocation,)),),
            (), 500, "customer_complaint",
        )
        checkout_request = replace(
            request("co_manager"),
            offers=evaluated,
            tax=calculate_tax(
                request().cart,
                (
                    TaxRate("90031900", 1800, date(2025, 1, 1)),
                    TaxRate("90015000", 500, date(2025, 1, 1)),
                ),
                as_of=date(2026, 11, 2),
                discounts=(allocation,),
            ),
        )
        service.execute(checkout_request)
        with database.connection() as connection:
            row = connection.execute(
                "SELECT reason FROM audit_events WHERE action = 'manager_discount_applied'"
            ).fetchone()
        self.assertEqual(row["reason"], "customer_complaint")
        with database.connection() as connection:
            row = connection.execute(
                "SELECT status, terminal_charge_id, shopify_order_id FROM checkout_records"
            ).fetchone()
        self.assertEqual(tuple(row), ("completed", "chg_1", "123"))

    def test_duplicate_checkout_does_not_charge_again(self):
        database = self.database()
        terminal = FakeTerminal()
        service = CheckoutService(database, terminal, FakeShopify())
        first = service.execute(request())
        second = service.execute(request())
        self.assertEqual(first.shopify_order_id, second.shopify_order_id)
        self.assertEqual(len(terminal.calls), 1)

    def test_shopify_uncertain_failure_is_not_retried_and_can_reconcile(self):
        database = self.database()
        shopify = FakeShopify(error=TimeoutError("timeout"))
        service = CheckoutService(database, FakeTerminal(), shopify)
        with self.assertRaisesRegex(CheckoutError, "uncertain"):
            service.execute(request())
        self.assertEqual(len(shopify.payloads), 1)
        with database.connection() as connection:
            self.assertEqual(
                connection.execute("SELECT status FROM checkout_records").fetchone()["status"],
                "shopify_reconciliation_required",
            )

        shopify.error = None
        shopify.matches = [{"id": 777, "note_attributes": [
            {"name": "checkout_ref", "value": "co_1"},
        ]}]
        result = service.reconcile("co_1")
        self.assertEqual(result.shopify_order_id, "777")

    def test_checkout_requires_active_till(self):
        database = self.database()
        service = CheckoutService(database, FakeTerminal(), FakeShopify())
        invalid = request()
        invalid = CheckoutRequest(
            invalid.checkout_id, invalid.customer, invalid.prescription, invalid.cart,
            invalid.offers, invalid.tax, invalid.store_code, invalid.location_id,
            invalid.staff_id, invalid.invoice_prefix, invalid.payment_method, False,
        )
        with self.assertRaisesRegex(CheckoutError, "active till"):
            service.execute(invalid)

    def test_terminal_reconciliation_checks_charge_reference_without_recharging(self):
        database = self.database()
        service = CheckoutService(database, FakeTerminal(), FakeShopify())
        service.execute(request("co_1"))
        # This operation only queries a known charge ID and verifies its
        # stable order reference; it never issues another charge.
        self.assertEqual(service.reconcile_terminal("co_1", "chg_1"), "payment_captured")

    def test_timeout_without_charge_id_recovers_with_same_order_ref(self):
        database = self.database()
        terminal = UncertainTerminal()
        service = CheckoutService(database, terminal, FakeShopify())
        with self.assertRaises(CheckoutError) as raised:
            service.execute(request("co_timeout"))
        self.assertEqual(raised.exception.code, "payment_uncertain")
        self.assertEqual(service.retry_uncertain_payment("co_timeout"), "payment_captured")
        self.assertEqual(terminal.calls[0][2], terminal.calls[1][2])
        self.assertEqual(terminal.calls[0][2], "eluno:co_timeout")

    def test_failed_payment_reserves_and_accounts_for_invoice(self):
        class FailedTerminal(FakeTerminal):
            def charge(self, amount_paise, method, order_ref):
                return {"status": "failed"}

        database = self.database()
        service = CheckoutService(database, FailedTerminal(), FakeShopify())
        with self.assertRaises(CheckoutError):
            service.execute(request("co_failed"))
        with database.connection() as connection:
            row = connection.execute(
                "SELECT invoice_number, status FROM checkout_records"
            ).fetchone()
            cancellation = connection.execute(
                "SELECT operation, state, details_json FROM reconciliation_state"
            ).fetchall()
        self.assertEqual((row["invoice_number"], row["status"]), ("ELU/HSR/000001", "payment_failed"))
        self.assertTrue(any(item["operation"] == "invoice" and item["state"] == "cancelled" for item in cancellation))

    def test_checkout_date_and_original_cart_totals_are_validated(self):
        database = self.database()
        service = CheckoutService(database, FakeTerminal(), FakeShopify())
        expired = replace(request("co_expired"), checkout_date=date(2027, 1, 2))
        with self.assertRaisesRegex(CheckoutError, "expired"):
            service.execute(expired)
        altered_tax = replace(
            request("co_mismatch").tax,
            lines=(
                replace(request("co_mismatch").tax.lines[0], gross_paise=99999),
                request("co_mismatch").tax.lines[1],
            ),
        )
        mismatch = replace(request("co_mismatch"), tax=altered_tax)
        with self.assertRaisesRegex(CheckoutError, "original cart"):
            service.execute(mismatch)

    def test_uncertain_checkout_status_and_manual_resolution_are_audited(self):
        database = self.database()
        service = CheckoutService(database, FakeTerminal(), FakeShopify())
        with database.connection() as connection:
            connection.execute(
                """INSERT INTO checkout_records
                   (checkout_id, store_code, invoice_number, status,
                    terminal_order_ref, terminal_charge_id, payment_amount_paise,
                    payment_method, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("uncertain-1", "HSR", "ELU/HSR/000001",
                 "shopify_reconciliation_required", "eluno:uncertain-1",
                 "chg_uncertain", 1000, "upi", "now", "now"),
            )
        self.assertEqual(service.list_uncertain()[0]["checkout_id"], "uncertain-1")
        status = service.get_status("uncertain-1")
        self.assertEqual(status["status"], "shopify_reconciliation_required")
        resolution = service.record_manual_resolution(
            "uncertain-1", "S105", "Operator confirmed customer contact and held sale.",
        )
        self.assertTrue(resolution["financial_state_unchanged"])
        self.assertEqual(service.get_status("uncertain-1")["status"],
                         "shopify_reconciliation_required")
        with database.connection() as connection:
            event = connection.execute(
                """SELECT actor_id, action, reason FROM audit_events
                   WHERE entity_ref = ? ORDER BY rowid DESC LIMIT 1""",
                ("uncertain-1",),
            ).fetchone()
        self.assertEqual(tuple(event), (
            "S105", "checkout_manual_resolution_recorded",
            "Operator confirmed customer contact and held sale.",
        ))
