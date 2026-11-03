"""Payment-first checkout orchestration with durable recovery state."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
import uuid
from typing import Any, Optional, Tuple

from app.adapters.shopify_orders import ShopifyOrderClient, build_order_payload
from app.adapters.terminal_client import TerminalClient
from app.domain.cart import Cart
from app.domain.customer import Customer
from app.domain.offers import OfferEvaluation
from app.domain.prescription import Prescription
from app.domain.tax import TaxCalculation
from app.persistence.database import Database
from app.persistence.audit import AuditTrail
from app.domain.sales_channel import validate_channel


class CheckoutError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CheckoutRequest:
    checkout_id: str
    customer: Customer
    prescription: Prescription
    cart: Cart
    offers: OfferEvaluation
    tax: TaxCalculation
    store_code: str
    location_id: int
    staff_id: str
    invoice_prefix: str
    payment_method: str
    active_till: bool = True
    session_id: Optional[str] = None
    checkout_date: date = field(default_factory=date.today)
    sales_channel: str = "walk_in"


@dataclass(frozen=True)
class CheckoutResult:
    checkout_id: str
    status: str
    invoice_number: str
    charge_id: Optional[str] = None
    shopify_order_id: Optional[str] = None


class CheckoutService:
    def __init__(
        self,
        database: Database,
        terminal: TerminalClient,
        shopify: ShopifyOrderClient,
        audit: AuditTrail = None,
    ) -> None:
        self._database = database
        self._terminal = terminal
        self._shopify = shopify
        self._audit = audit or AuditTrail(database)

    def execute(self, request: CheckoutRequest) -> CheckoutResult:
        self._validate(request)
        order_ref = "eluno:" + request.checkout_id
        now = _now()
        with self._database.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM checkout_records WHERE checkout_id = ?",
                (request.checkout_id,),
            ).fetchone()
            if existing is not None:
                if existing["status"] == "completed":
                    return CheckoutResult(
                        request.checkout_id,
                        "completed",
                        existing["invoice_number"],
                        existing["terminal_charge_id"],
                        existing["shopify_order_id"],
                    )
                raise CheckoutError(
                    "checkout_in_progress",
                    "Checkout already exists and must be reconciled before retrying.",
                )
            invoice_number = _allocate_invoice(
                connection, request.store_code, request.invoice_prefix, now
            )
            connection.execute(
                """INSERT INTO checkout_records
                (checkout_id, store_code, invoice_number, status, terminal_order_ref,
                 payment_amount_paise, payment_method, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (request.checkout_id, request.store_code, invoice_number,
                 "payment_pending", order_ref, request.tax.total_paise,
                 request.payment_method, now, now),
            )
        self._audit.record(
            actor_id=request.staff_id, action="checkout_started",
            entity_type="checkout", entity_ref=request.checkout_id,
            metadata={"store_code": request.store_code,
                      "invoice_number": invoice_number,
                      "amount_paise": request.tax.total_paise,
                      "payment_method": request.payment_method},
        )

        try:
            charge = self._terminal.charge(
                request.tax.total_paise, request.payment_method, order_ref
            )
        except Exception as error:
            self._update(
                request.checkout_id, "payment_uncertain",
                last_error=str(error),
                last_reconciliation_result="terminal_payment_uncertain",
            )
            self._audit.record(
                actor_id=request.staff_id, action="payment_uncertain",
                entity_type="checkout", entity_ref=request.checkout_id,
                metadata={"order_ref": order_ref, "amount_paise": request.tax.total_paise},
            )
            raise CheckoutError(
                "payment_uncertain",
                "Terminal payment outcome is uncertain. Retry recovery with the same order reference; do not create a new checkout.",
            ) from error
        if charge.get("status") != "captured" or not charge.get("charge_id"):
            self._update(
                request.checkout_id,
                "payment_failed",
                last_error="Terminal did not capture payment.",
            )
            self._audit.record(
                actor_id=request.staff_id, action="payment_failed",
                entity_type="checkout", entity_ref=request.checkout_id,
                metadata={"order_ref": order_ref, "amount_paise": request.tax.total_paise},
            )
            raise CheckoutError("payment_failed", "Terminal did not confirm payment.")
        charge_id = str(charge["charge_id"])
        self._update(
            request.checkout_id,
            "payment_captured",
            terminal_charge_id=charge_id,
        )
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT invoice_number FROM checkout_records WHERE checkout_id = ?",
                (request.checkout_id,),
            ).fetchone()
        payload = build_order_payload(
            cart=request.cart,
            customer=request.customer,
            prescription=request.prescription,
            tax=request.tax,
            store_code=request.store_code,
            location_id=request.location_id,
            staff_id=request.staff_id,
            invoice_number=row["invoice_number"],
            checkout_id=request.checkout_id,
            charge_id=charge_id,
            payment_method=request.payment_method,
            discount_allocations=_allocations(request.offers),
            sales_channel=request.sales_channel,
        )
        try:
            response = self._shopify.create_order(payload)
        except Exception as error:
            self._update(
                request.checkout_id,
                "shopify_reconciliation_required",
                last_error=str(error),
                last_reconciliation_result="shopify_order_uncertain",
            )
            self._audit.record(
                actor_id=request.staff_id, action="payment_captured",
                entity_type="checkout", entity_ref=request.checkout_id,
                metadata={"charge_id": charge_id, "amount_paise": request.tax.total_paise},
            )
            self._audit.record(
                actor_id=request.staff_id, action="shopify_order_uncertain",
                entity_type="checkout", entity_ref=request.checkout_id,
                metadata={"charge_id": charge_id, "invoice_number": invoice_number},
            )
            raise CheckoutError(
                "shopify_uncertain",
                "Payment succeeded but Shopify order creation is uncertain; reconcile before retrying.",
            ) from error
        order = response.get("order") if isinstance(response, dict) else None
        if not isinstance(order, dict) or not order.get("id"):
            self._update(
                request.checkout_id,
                "shopify_reconciliation_required",
                last_error="Malformed Shopify response.",
                last_reconciliation_result="shopify_response_invalid",
            )
            self._audit.record(
                actor_id=request.staff_id, action="payment_captured",
                entity_type="checkout", entity_ref=request.checkout_id,
                metadata={"charge_id": charge_id, "amount_paise": request.tax.total_paise},
            )
            raise CheckoutError("shopify_uncertain", "Shopify returned no confirmed order.")
        self._update(
            request.checkout_id,
            "completed",
            shopify_order_id=str(order["id"]),
        )
        self._audit.record(
            actor_id=request.staff_id, action="payment_captured",
            entity_type="checkout", entity_ref=request.checkout_id,
            metadata={"charge_id": charge_id, "amount_paise": request.tax.total_paise},
        )
        for applied in request.offers.applied:
            if applied.offer_id == "MANAGER_OVERRIDE":
                self._audit.record(
                    actor_id=request.staff_id, action="manager_discount_applied",
                    entity_type="checkout", entity_ref=request.checkout_id,
                    reason=request.offers.manager_reason_code,
                    metadata={"offer_id": applied.offer_id,
                              "discount_paise": applied.amount_paise,
                              "invoice_number": row["invoice_number"]},
                )
        self._audit.record(
            actor_id=request.staff_id, action="checkout_completed",
            entity_type="checkout", entity_ref=request.checkout_id,
            metadata={"shopify_order_id": str(order["id"]),
                      "invoice_number": row["invoice_number"]},
        )
        return CheckoutResult(
            request.checkout_id,
            "completed",
            row["invoice_number"],
            charge_id,
            str(order["id"]),
        )

    def reconcile(self, checkout_id: str) -> CheckoutResult:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM checkout_records WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
        if row is None:
            raise CheckoutError("checkout_missing", "Checkout does not exist.")
        if row["status"] == "completed":
            return CheckoutResult(
                checkout_id,
                "completed",
                row["invoice_number"],
                row["terminal_charge_id"],
                row["shopify_order_id"],
            )
        matches = self._shopify.find_orders(checkout_id)
        if len(matches) > 1:
            raise CheckoutError(
                "duplicate_shopify_orders",
                "More than one Shopify order matches the checkout reference.",
            )
        if matches:
            order = matches[0]
            self._update(
                checkout_id, "completed", shopify_order_id=str(order["id"]),
                last_reconciliation_result="shopify_order_found", last_error=None,
            )
            self._audit.record(
                actor_id=None, action="checkout_reconciled",
                entity_type="checkout", entity_ref=checkout_id,
                metadata={"shopify_order_id": str(order["id"])},
            )
            return CheckoutResult(
                checkout_id,
                "completed",
                row["invoice_number"],
                row["terminal_charge_id"],
                str(order["id"]),
            )
        self._update(
            checkout_id, row["status"],
            last_reconciliation_result="shopify_order_not_found",
            last_error="No Shopify order matched the checkout reference.",
        )
        raise CheckoutError(
            "shopify_order_not_found",
            "No Shopify order matches the checkout reference; creation remains unsafe without a confirmed payment/order policy.",
        )

    def reconcile_terminal(self, checkout_id: str, charge_id: str) -> str:
        """Resolve an uncertain terminal result without issuing another charge."""
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT terminal_order_ref FROM checkout_records WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
        if row is None:
            raise CheckoutError("checkout_missing", "Checkout does not exist.")
        charge = self._terminal.get_charge(charge_id)
        expected_ref = "eluno:" + checkout_id
        if charge.get("order_ref") != expected_ref:
            raise CheckoutError(
                "charge_reference_mismatch",
                "Terminal charge does not belong to this checkout.",
            )
        if charge.get("status") != "captured":
            self._update(
                checkout_id,
                "payment_failed",
                last_error="Terminal charge was not captured.",
            )
            raise CheckoutError("payment_failed", "Terminal charge was not captured.")
        self._update(
            checkout_id, "payment_captured", terminal_charge_id=charge_id,
            last_reconciliation_result="terminal_charge_confirmed", last_error=None,
        )
        self._audit.record(
            actor_id=None, action="payment_reconciled",
            entity_type="checkout", entity_ref=checkout_id,
            metadata={"charge_id": charge_id},
        )
        return "payment_captured"

    def list_uncertain(self) -> list:
        with self._database.connection() as connection:
            rows = connection.execute(
                """SELECT c.*, r.resolution_id, r.actor_id AS resolution_actor_id,
                   r.reason AS resolution_reason, r.resolved_at
                   FROM checkout_records c
                   LEFT JOIN checkout_manual_resolutions r
                     ON r.checkout_id = c.checkout_id
                   WHERE c.status IN ('payment_uncertain',
                                      'shopify_reconciliation_required')
                   ORDER BY c.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def get_status(self, checkout_id: str) -> dict:
        with self._database.connection() as connection:
            row = connection.execute(
                """SELECT c.*, r.resolution_id, r.actor_id AS resolution_actor_id,
                   r.reason AS resolution_reason, r.resolved_at
                   FROM checkout_records c
                   LEFT JOIN checkout_manual_resolutions r
                     ON r.checkout_id = c.checkout_id
                   WHERE c.checkout_id = ?""",
                (checkout_id,),
            ).fetchone()
        if row is None:
            raise CheckoutError("checkout_missing", "Checkout does not exist.")
        return dict(row)

    def record_manual_resolution(
        self, checkout_id: str, actor_id: str, reason: str
    ) -> dict:
        if not actor_id.strip():
            raise CheckoutError("actor_required", "Operator identity is required.")
        if not reason.strip():
            raise CheckoutError("reason_required", "A manual resolution reason is required.")
        with self._database.connection() as connection:
            checkout = connection.execute(
                "SELECT status FROM checkout_records WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
            if checkout is None:
                raise CheckoutError("checkout_missing", "Checkout does not exist.")
            if checkout["status"] not in (
                "payment_uncertain", "shopify_reconciliation_required"
            ):
                raise CheckoutError(
                    "manual_resolution_not_allowed",
                    "Manual resolution is only available for uncertain checkouts.",
                )
            existing = connection.execute(
                "SELECT * FROM checkout_manual_resolutions WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
            created = existing is None
            if created:
                resolution_id = str(uuid.uuid4())
                resolved_at = _now()
                connection.execute(
                    """INSERT INTO checkout_manual_resolutions
                       (resolution_id, checkout_id, actor_id, reason, resolved_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (resolution_id, checkout_id, actor_id.strip(), reason.strip(), resolved_at),
                )
            else:
                resolution_id = existing["resolution_id"]
                resolved_at = existing["resolved_at"]
        if created:
            self._audit.record(
                actor_id=actor_id.strip(),
                action="checkout_manual_resolution_recorded",
                entity_type="checkout",
                entity_ref=checkout_id,
                reason=reason.strip(),
                metadata={"resolution_id": resolution_id, "financial_state_unchanged": True},
            )
        return {
            "resolution_id": resolution_id,
            "checkout_id": checkout_id,
            "actor_id": actor_id.strip(),
            "reason": reason.strip(),
            "resolved_at": resolved_at,
            "financial_state_unchanged": True,
        }

    def retry_uncertain_payment(self, checkout_id: str) -> str:
        """Retry a timed-out terminal request using its persisted stable reference."""
        with self._database.connection() as connection:
            row = connection.execute(
                """SELECT status, terminal_order_ref, payment_amount_paise,
                payment_method FROM checkout_records WHERE checkout_id = ?""",
                (checkout_id,),
            ).fetchone()
        if row is None:
            raise CheckoutError("checkout_missing", "Checkout does not exist.")
        if row["status"] != "payment_uncertain":
            raise CheckoutError(
                "payment_recovery_not_allowed",
                "Payment recovery is only allowed for an uncertain terminal outcome.",
            )
        try:
            charge = self._terminal.charge(
                row["payment_amount_paise"],
                row["payment_method"],
                row["terminal_order_ref"],
            )
        except Exception as error:
            self._update(checkout_id, "payment_uncertain", last_error=str(error))
            raise CheckoutError("payment_uncertain", "Terminal payment outcome remains uncertain.") from error
        if charge.get("status") != "captured" or not charge.get("charge_id"):
            self._update(checkout_id, "payment_uncertain", last_error="Terminal did not confirm payment.")
            raise CheckoutError("payment_uncertain", "Terminal did not confirm payment.")
        self._update(
            checkout_id,
            "payment_captured",
            terminal_charge_id=str(charge["charge_id"]),
        )
        self._audit.record(
            actor_id=None, action="payment_recovery_captured",
            entity_type="checkout", entity_ref=checkout_id,
            metadata={"charge_id": str(charge["charge_id"]),
                      "order_ref": row["terminal_order_ref"]},
        )
        return "payment_captured"

    def _validate(self, request: CheckoutRequest) -> None:
        try:
            validate_channel(request.sales_channel)
        except ValueError as error:
            raise CheckoutError("sales_channel_invalid", str(error)) from error
        if not request.customer or not request.prescription:
            raise CheckoutError(
                "customer_prescription_required",
                "Customer and valid prescription are required.",
            )
        if request.prescription.valid_until < request.checkout_date:
            raise CheckoutError("prescription_expired", "Prescription is expired.")
        if not request.store_code or not request.staff_id or request.location_id <= 0:
            raise CheckoutError(
                "store_context_required",
                "Store, location and staff are required.",
            )
        if not request.active_till:
            raise CheckoutError("active_till_required", "An active till/session is required.")
        if not request.cart.lines:
            raise CheckoutError("cart_empty", "Cart must contain at least one line.")
        cart_line_ids = {line.line_id for line in request.cart.lines}
        tax_line_ids = {line.line_id for line in request.tax.lines}
        if cart_line_ids != tax_line_ids:
            raise CheckoutError("tax_lines_missing", "Every cart line must have a tax calculation.")
        cart_by_id = {line.line_id: line for line in request.cart.lines}
        if request.tax.total_paise != (
            sum(line.gross_paise for line in request.cart.lines)
            - request.offers.total_discount_paise
        ):
            raise CheckoutError("tax_invalid", "Tax calculation does not reconcile to cart totals.")
        allocations = _allocations(request.offers)
        allocated_by_line = {}
        for allocation in allocations:
            if allocation.amount_paise < 0:
                raise CheckoutError("offers_invalid", "Offer allocation cannot be negative.")
            allocated_by_line[allocation.line_id] = (
                allocated_by_line.get(allocation.line_id, 0) + allocation.amount_paise
            )
        for tax_line in request.tax.lines:
            cart_line = cart_by_id[tax_line.line_id]
            if tax_line.gross_paise != cart_line.gross_paise or tax_line.hsn != cart_line.hsn:
                raise CheckoutError(
                    "tax_cart_mismatch",
                    f"Tax input does not match the original cart line {tax_line.line_id}.",
                )
            if allocated_by_line.get(tax_line.line_id, 0) != tax_line.discount_paise:
                raise CheckoutError(
                    "discount_tax_mismatch",
                    f"Discount allocation does not match tax input for {tax_line.line_id}.",
                )

    def _update(self, checkout_id: str, status: str, **fields: Any) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        values = [status, _now()]
        for field in (
            "terminal_charge_id", "shopify_order_id", "last_error",
            "last_reconciliation_result",
        ):
            if field in fields:
                assignments.append(f"{field} = ?")
                values.append(fields[field])
        values.append(checkout_id)
        with self._database.connection() as connection:
            connection.execute(
                "UPDATE checkout_records SET " + ", ".join(assignments)
                + " WHERE checkout_id = ?",
                values,
            )
            if "last_error" in fields:
                connection.execute(
                    """INSERT INTO reconciliation_state
                    (reconciliation_id, checkout_id, operation, state, last_error,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        checkout_id,
                        "payment" if status.startswith("payment_") else "shopify_order",
                        status,
                        fields["last_error"],
                        _now(),
                        _now(),
                    ),
                )
            if status == "payment_failed":
                connection.execute(
                    """INSERT INTO reconciliation_state
                    (reconciliation_id, checkout_id, operation, state, details_json,
                     last_error, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        checkout_id,
                        "invoice",
                        "cancelled",
                        json.dumps({"reason": "payment_failed"}),
                        fields.get("last_error"),
                        _now(),
                        _now(),
                    ),
                )


def _allocate_invoice(connection: Any, store_code: str, prefix: str, now: str) -> str:
    row = connection.execute(
        "SELECT next_number FROM invoice_sequences WHERE store_code = ?",
        (store_code,),
    ).fetchone()
    if row is None:
        number = 1
        connection.execute(
            """INSERT INTO invoice_sequences
            (store_code, invoice_prefix, next_number, updated_at)
            VALUES (?, ?, ?, ?)""",
            (store_code, prefix, 2, now),
        )
    else:
        number = row["next_number"]
        connection.execute(
            """UPDATE invoice_sequences
            SET next_number = ?, updated_at = ? WHERE store_code = ?""",
            (number + 1, now, store_code),
        )
    return f"{prefix}{number:06d}"


def _allocations(evaluation: OfferEvaluation) -> Tuple[object, ...]:
    return tuple(
        allocation
        for applied in evaluation.applied
        for allocation in applied.allocations
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
