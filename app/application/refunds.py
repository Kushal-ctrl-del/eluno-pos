"""Full-refund orchestration with explicit recovery states."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from app.adapters.shopify_orders import ShopifyOrderClient
from app.adapters.terminal_client import TerminalClient
from app.persistence.database import Database
from app.persistence.audit import AuditTrail


class RefundError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RefundService:
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

    def refund_full(
        self,
        *,
        refund_request_id: str,
        order_id: int,
        reason: str,
        checkout_id: Optional[str] = None,
        staff_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._refund_selected(
            refund_request_id=refund_request_id,
            order_id=order_id,
            reason=reason,
            selected_lines=None,
            checkout_id=checkout_id,
            staff_id=staff_id,
        )

    def refund_partial(
        self,
        *,
        refund_request_id: str,
        order_id: int,
        reason: str,
        selected_lines: List[Dict[str, Any]],
        checkout_id: Optional[str] = None,
        staff_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Refund selected remaining quantities through Shopify."""
        return self._refund_selected(
            refund_request_id=refund_request_id,
            order_id=order_id,
            reason=reason,
            selected_lines=selected_lines,
            checkout_id=checkout_id,
            staff_id=staff_id,
        )

    def _refund_selected(
        self,
        *,
        refund_request_id: str,
        order_id: int,
        reason: str,
        selected_lines: Optional[List[Dict[str, Any]]],
        checkout_id: Optional[str] = None,
        staff_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = self._get(refund_request_id)
        if existing is not None:
            # A repeated request is observational only. This prevents a
            # second terminal refund after any ambiguous external outcome.
            return dict(existing)
        if not reason:
            raise RefundError("reason_required", "Refund reason is required.")

        order = self._shopify.get_order(order_id)
        sale = _sale_transaction(order)
        charge_id = _charge_id(sale)
        if not charge_id:
            raise RefundError(
                "charge_reference_missing",
                "Original sale has no terminal charge reference.",
            )
        refund_lines = _select_refund_lines(order, selected_lines)
        if not refund_lines:
            raise RefundError("already_refunded", "No refundable lines remain on the order.")
        calculated_response = self._shopify.calculate_refund(order_id, refund_lines)
        calculated = calculated_response.get("refund")
        if not isinstance(calculated, dict):
            raise RefundError("refund_calculation_invalid", "Shopify returned no refund calculation.")
        calculated_transactions = calculated.get("transactions") or []
        if not calculated_transactions:
            raise RefundError("refund_calculation_invalid", "Shopify returned no refundable amount.")
        amount_paise = _parse_money(calculated_transactions[0].get("amount"))
        if amount_paise <= 0:
            raise RefundError("refund_amount_invalid", "Refund amount must be positive.")
        self._insert(
            refund_request_id,
            checkout_id,
            order_id,
            "calculated",
            amount_paise,
            charge_id,
            calculated,
        )
        self._audit.record(
            actor_id=staff_id, action="refund_initiated",
            entity_type="refund", entity_ref=refund_request_id,
            reason=reason,
            metadata={"shopify_order_id": str(order_id),
                      "amount_paise": amount_paise,
                      "line_count": len(refund_lines)},
        )

        try:
            terminal_response = self._terminal.refund(charge_id, amount_paise, reason)
        except Exception as error:
            self._update(
                refund_request_id,
                "terminal_refund_uncertain",
                last_error=str(error),
            )
            self._audit.record(
                actor_id=staff_id, action="refund_terminal_uncertain",
                entity_type="refund", entity_ref=refund_request_id,
                reason=reason, metadata={"amount_paise": amount_paise},
            )
            raise RefundError(
                "terminal_refund_uncertain",
                "Terminal refund outcome is uncertain; do not retry blindly.",
            ) from error
        if terminal_response.get("status") != "processed" or not terminal_response.get("refund_id"):
            self._update(
                refund_request_id,
                "terminal_refund_failed",
                last_error="Terminal did not confirm the refund.",
            )
            self._audit.record(
                actor_id=staff_id, action="refund_terminal_failed",
                entity_type="refund", entity_ref=refund_request_id,
                reason=reason, metadata={"amount_paise": amount_paise},
            )
            raise RefundError("terminal_refund_failed", "Terminal did not confirm the refund.")
        terminal_refund_id = str(terminal_response["refund_id"])
        self._update(
            refund_request_id,
            "terminal_refunded",
            terminal_refund_id=terminal_refund_id,
        )
        payload = {
            "refund_line_items": calculated.get("refund_line_items", refund_lines),
            "transactions": [{
                "kind": "refund",
                "amount": _money(amount_paise),
                "gateway": sale.get("gateway", "terminal"),
                "parent_id": sale["id"],
            }],
            "note": reason,
            "note_attributes": [
                {"name": "refund_request_id", "value": refund_request_id},
                {"name": "terminal_refund_id", "value": terminal_refund_id},
            ],
        }
        try:
            response = self._shopify.create_refund(order_id, payload)
        except Exception as error:
            self._update(
                refund_request_id,
                "shopify_refund_reconciliation_required",
                last_error=str(error),
            )
            self._audit.record(
                actor_id=staff_id, action="refund_shopify_uncertain",
                entity_type="refund", entity_ref=refund_request_id,
                reason=reason, metadata={"amount_paise": amount_paise},
            )
            raise RefundError(
                "shopify_refund_uncertain",
                "Terminal was refunded but Shopify refund creation is uncertain.",
            ) from error
        refund = response.get("refund") if isinstance(response, dict) else None
        if not isinstance(refund, dict) or not refund.get("id"):
            self._update(
                refund_request_id,
                "shopify_refund_reconciliation_required",
                last_error="Malformed Shopify refund response.",
            )
            self._audit.record(
                actor_id=staff_id, action="refund_shopify_uncertain",
                entity_type="refund", entity_ref=refund_request_id,
                reason=reason, metadata={"amount_paise": amount_paise},
            )
            raise RefundError("shopify_refund_uncertain", "Shopify returned no confirmed refund.")
        self._update(
            refund_request_id,
            "completed",
            shopify_refund_id=str(refund["id"]),
        )
        self._audit.record(
            actor_id=staff_id, action="refund_terminal_completed",
            entity_type="refund", entity_ref=refund_request_id,
            reason=reason, metadata={"terminal_refund_id": terminal_refund_id,
                                      "amount_paise": amount_paise},
        )
        self._audit.record(
            actor_id=staff_id, action="refund_completed",
            entity_type="refund", entity_ref=refund_request_id,
            reason=reason, metadata={"shopify_refund_id": str(refund["id"]),
                                      "amount_paise": amount_paise},
        )
        result = self._get(refund_request_id)
        return dict(result)

    def reconcile(self, refund_request_id: str) -> Dict[str, Any]:
        record = self._get(refund_request_id)
        if record is None:
            raise RefundError("refund_missing", "Refund request does not exist.")
        if record["status"] == "completed":
            return dict(record)
        order = self._shopify.get_order(int(record["shopify_order_id"]))
        for refund in order.get("refunds", []):
            if _note_value(refund.get("note_attributes", []), "refund_request_id") == refund_request_id:
                _verify_refund_details(record, refund)
                refund_id = str(refund["id"])
                self._update(refund_request_id, "completed", shopify_refund_id=refund_id)
                self._audit.record(
                    actor_id=None, action="refund_reconciled",
                    entity_type="refund", entity_ref=refund_request_id,
                    metadata={"shopify_refund_id": refund_id,
                              "amount_paise": int(record["amount_paise"])},
                )
                return dict(self._get(refund_request_id))
        raise RefundError(
            "shopify_refund_not_found",
            "No matching Shopify refund was found; do not blindly retry the financial write.",
        )

    def resolve_terminal_uncertainty(
        self, refund_request_id: str, terminal_refund_id: str
    ) -> Dict[str, Any]:
        """Record operator evidence that an uncertain terminal refund completed."""
        record = self._get(refund_request_id)
        if record is None:
            raise RefundError("refund_missing", "Refund request does not exist.")
        if record["status"] == "completed":
            return dict(record)
        if record["status"] != "terminal_refund_uncertain":
            raise RefundError(
                "terminal_resolution_not_allowed",
                "Manual terminal resolution is only allowed for an uncertain refund.",
            )
        self._update(
            refund_request_id,
            "terminal_refunded",
            terminal_refund_id=terminal_refund_id,
        )
        self._audit.record(
            actor_id=None, action="refund_terminal_manually_resolved",
            entity_type="refund", entity_ref=refund_request_id,
            metadata={"terminal_refund_id": terminal_refund_id,
                      "operator_assertion": True},
        )
        return dict(self._get(refund_request_id))

    def _get(self, refund_request_id: str) -> Optional[Any]:
        with self._database.connection() as connection:
            return connection.execute(
                "SELECT * FROM refund_records WHERE refund_request_id = ?",
                (refund_request_id,),
            ).fetchone()

    def _insert(
        self, request_id: str, checkout_id: Optional[str], order_id: int,
        status: str, amount: int, charge_id: str, calculated: Dict[str, Any],
    ) -> None:
        now = _now()
        with self._database.connection() as connection:
            connection.execute(
                """INSERT INTO refund_records
                (refund_request_id, checkout_id, shopify_order_id, status,
                 amount_paise, charge_id, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (request_id, checkout_id, str(order_id), status, amount, charge_id,
                 json.dumps(calculated), now, now),
            )

    def _update(self, request_id: str, status: str, **fields: Any) -> None:
        allowed = ("terminal_refund_id", "shopify_refund_id", "last_error")
        assignments = ["status = ?", "updated_at = ?"]
        values = [status, _now()]
        for field in allowed:
            if field in fields:
                assignments.append(field + " = ?")
                values.append(fields[field])
        values.append(request_id)
        with self._database.connection() as connection:
            connection.execute(
                "UPDATE refund_records SET " + ", ".join(assignments)
                + " WHERE refund_request_id = ?",
                values,
            )


def _sale_transaction(order: Dict[str, Any]) -> Dict[str, Any]:
    sales = [
        transaction for transaction in order.get("transactions", [])
        if transaction.get("kind") == "sale" and transaction.get("status") == "success"
    ]
    if not sales:
        raise RefundError("sale_transaction_missing", "Completed order has no successful sale transaction.")
    return sales[0]


def _charge_id(sale: Dict[str, Any]) -> Optional[str]:
    return sale.get("charge_id") or sale.get("authorization")


def _refund_line_requests(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    already = {}
    for refund in order.get("refunds", []):
        for line in refund.get("refund_line_items", []):
            already[line["line_item_id"]] = already.get(line["line_item_id"], 0) + int(line["quantity"])
    result = []
    for line in order.get("line_items", []):
        remaining = int(line["quantity"]) - already.get(line["id"], 0)
        if remaining <= 0:
            continue
        line_type = _note_value(line.get("properties", []), "line_type")
        if line_type == "frame":
            restock_type = "return"
            location_id = order.get("location_id")
        elif line_type == "lens":
            restock_type = "no_restock"
            location_id = None
        else:
            raise RefundError("line_type_missing", f"Cannot determine refund handling for {line['id']}.")
        item = {
            "line_item_id": line["id"],
            "quantity": remaining,
            "restock_type": restock_type,
        }
        if location_id is not None:
            item["location_id"] = location_id
        result.append(item)
    return result


def _select_refund_lines(
    order: Dict[str, Any], selected_lines: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    remaining_by_id = _remaining_quantities(order)
    if selected_lines is None:
        selected_lines = [
            {"line_item_id": line_id, "quantity": quantity}
            for line_id, quantity in remaining_by_id.items()
            if quantity > 0
        ]
    if not selected_lines:
        raise RefundError("refund_lines_required", "At least one refundable line is required.")
    order_lines = {line["id"]: line for line in order.get("line_items", [])}
    result = []
    seen = set()
    for selected in selected_lines:
        try:
            line_id = int(selected["line_item_id"])
            quantity = int(selected["quantity"])
        except (KeyError, TypeError, ValueError) as error:
            raise RefundError("refund_line_invalid", "Refund line selection is malformed.") from error
        if line_id in seen:
            raise RefundError("refund_line_duplicate", f"Line {line_id} was selected more than once.")
        seen.add(line_id)
        line = order_lines.get(line_id)
        if line is None:
            raise RefundError("refund_line_not_found", f"Line {line_id} is not on the order.")
        remaining = remaining_by_id.get(line_id, 0)
        if quantity <= 0 or quantity > remaining:
            raise RefundError(
                "refund_quantity_invalid",
                f"Line {line_id} has only {remaining} refundable units remaining.",
            )
        line_type = _note_value(line.get("properties", []), "line_type")
        if line_type == "frame":
            restock_type = "return"
            location_id = order.get("location_id")
        elif line_type == "lens":
            restock_type = "no_restock"
            location_id = None
        else:
            raise RefundError("line_type_missing", f"Cannot determine refund handling for {line_id}.")
        item = {
            "line_item_id": line_id,
            "quantity": quantity,
            "restock_type": restock_type,
        }
        if location_id is not None:
            item["location_id"] = location_id
        result.append(item)
    return result


def _remaining_quantities(order: Dict[str, Any]) -> Dict[int, int]:
    refunded: Dict[int, int] = {}
    for refund in order.get("refunds", []):
        for line in refund.get("refund_line_items", []):
            line_id = int(line["line_item_id"])
            refunded[line_id] = refunded.get(line_id, 0) + int(line["quantity"])
    return {
        int(line["id"]): int(line["quantity"]) - refunded.get(int(line["id"]), 0)
        for line in order.get("line_items", [])
    }


def _note_value(attributes: Any, name: str) -> Optional[str]:
    if not isinstance(attributes, list):
        return None
    for attribute in attributes:
        if attribute.get("name") == name:
            return str(attribute.get("value"))
    return None


def _parse_money(value: Any) -> int:
    text = str(value)
    if "." not in text:
        raise RefundError("money_invalid", "External money value must contain paise.")
    whole, fractional = text.split(".", 1)
    if len(fractional) != 2 or not whole.lstrip("-").isdigit() or not fractional.isdigit():
        raise RefundError("money_invalid", f"Invalid decimal amount: {text}.")
    sign = -1 if whole.startswith("-") else 1
    return sign * (abs(int(whole)) * 100 + int(fractional))


def _money(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


def _verify_refund_details(record: Any, refund: Dict[str, Any]) -> None:
    try:
        expected = json.loads(record["payload_json"])
        expected_lines = expected["refund_line_items"]
        actual_lines = refund["refund_line_items"]
        expected_tx = expected["transactions"][0]
        actual_tx = refund["transactions"][0]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RefundError(
            "shopify_refund_mismatch",
            "Shopify refund response is missing expected details.",
        ) from error
    if _parse_money(actual_tx.get("amount")) != int(record["amount_paise"]):
        raise RefundError(
            "shopify_refund_mismatch",
            "Shopify refund amount does not match the calculated amount.",
        )
    if (
        actual_tx.get("kind") != "refund"
        or str(actual_tx.get("parent_id")) != str(expected_tx.get("parent_id"))
    ):
        raise RefundError(
            "shopify_refund_mismatch",
            "Shopify refund transaction linkage is incorrect.",
        )
    expected_by_line = {_line_key(line): line for line in expected_lines}
    actual_by_line = {_line_key(line): line for line in actual_lines}
    if set(expected_by_line) != set(actual_by_line):
        raise RefundError(
            "shopify_refund_mismatch",
            "Shopify refund line items do not match the calculated refund.",
        )
    for key, expected_line in expected_by_line.items():
        actual_line = actual_by_line[key]
        for field in ("quantity", "restock_type", "location_id"):
            if actual_line.get(field) != expected_line.get(field):
                raise RefundError(
                    "shopify_refund_mismatch",
                    f"Shopify refund line {key} has incorrect {field}.",
                )
        expected_total = _parse_money(expected_line["subtotal"]) + _parse_money(
            expected_line["total_tax"]
        )
        actual_total = _parse_money(actual_line["subtotal"]) + _parse_money(
            actual_line["total_tax"]
        )
        if expected_total != actual_total:
            raise RefundError(
                "shopify_refund_mismatch",
                f"Shopify refund line {key} amount or tax is incorrect.",
            )


def _line_key(line: Dict[str, Any]) -> str:
    return str(line["line_item_id"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
