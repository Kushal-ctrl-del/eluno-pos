"""Read-only, Shopify-backed day-end report calculations."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, Mapping, Optional
from app.domain.sales_channel import report_channel


class ReportError(ValueError):
    """Raised when Shopify data cannot produce a reconciled report."""


@dataclass(frozen=True)
class DayEndReport:
    store_code: str
    business_date: str
    bill_count: int
    gross_sales_paise: int
    taxable_value_paise: int
    cgst_paise: int
    sgst_paise: int
    total_tax_paise: int
    discounts_paise: int
    refund_count: int
    refund_amount_paise: int
    payment_method_paise: Mapping[str, int]
    sales_channel_paise: Mapping[str, int]


def calculate_day_end_report(
    orders: Iterable[Dict[str, Any]], store_code: str, business_date: str
) -> DayEndReport:
    totals = {
        "bill_count": 0, "gross": 0, "taxable": 0, "cgst": 0, "sgst": 0,
        "tax": 0, "discounts": 0, "refund_count": 0, "refund_amount": 0,
    }
    payment_methods: Dict[str, int] = {}
    sales_channels: Dict[str, int] = {}
    for order in orders:
        if _note(order, "store_code") != store_code:
            continue
        if _business_date(order) != business_date or _is_cancelled(order):
            continue
        order_totals = _reconcile_order(order)
        totals["bill_count"] += 1
        for key in ("gross", "taxable", "cgst", "sgst", "tax", "discounts"):
            totals[key] += order_totals[key]
        totals["refund_count"] += order_totals["refund_count"]
        totals["refund_amount"] += order_totals["refund_amount"]
        for method, amount in order_totals["payments"].items():
            payment_methods[method] = payment_methods.get(method, 0) + amount
        resolved_channel = report_channel(_note(order, "channel"))
        sales_channels[resolved_channel] = (
            sales_channels.get(resolved_channel, 0)
            + sum(order_totals["payments"].values())
        )
    return DayEndReport(
        store_code=store_code,
        business_date=business_date,
        bill_count=totals["bill_count"],
        gross_sales_paise=totals["gross"],
        taxable_value_paise=totals["taxable"],
        cgst_paise=totals["cgst"],
        sgst_paise=totals["sgst"],
        total_tax_paise=totals["tax"],
        discounts_paise=totals["discounts"],
        refund_count=totals["refund_count"],
        refund_amount_paise=totals["refund_amount"],
        payment_method_paise=payment_methods,
        sales_channel_paise=sales_channels,
    )


def _reconcile_order(order: Dict[str, Any]) -> Dict[str, Any]:
    gross = discounts = taxable = cgst = sgst = tax = 0
    for line in _list(order, "line_items"):
        quantity = _integer(line.get("quantity"), "line quantity")
        gross_line = _money(line.get("price"), "line price") * quantity
        discount_line = _money(line.get("total_discount", "0.00"), "line discount")
        line_tax = 0
        line_cgst = 0
        line_sgst = 0
        for tax_line in _list(line, "tax_lines"):
            amount = _money(tax_line.get("price"), "tax line price")
            title = str(tax_line.get("title", "")).upper()
            line_tax += amount
            if title == "CGST":
                line_cgst += amount
            elif title == "SGST":
                line_sgst += amount
        gross += gross_line
        discounts += discount_line
        taxable += gross_line - discount_line - line_tax
        cgst += line_cgst
        sgst += line_sgst
        tax += line_tax
    _equals(gross, order, "total_line_items_price", "gross line items")
    _equals(discounts, order, "total_discounts", "discounts")
    _equals(tax, order, "total_tax", "tax")
    _equals(gross - discounts, order, "total_price", "paid order total")
    if cgst + sgst != tax:
        raise ReportError("CGST and SGST do not reconcile to total tax.")

    payments: Dict[str, int] = {}
    sale_total = 0
    for transaction in _list(order, "transactions"):
        if transaction.get("kind") != "sale" or transaction.get("status", "success") != "success":
            continue
        amount = _money(transaction.get("amount"), "sale transaction amount")
        sale_total += amount
        method = _note(order, "payment_method") or str(
            transaction.get("source") or transaction.get("gateway") or "unknown"
        )
        payments[method] = payments.get(method, 0) + amount
    if sale_total != gross - discounts:
        raise ReportError("Successful sale transactions do not reconcile to order total.")

    refund_count = 0
    refund_amount = 0
    for refund in _list(order, "refunds"):
        refund_transactions = [
            transaction for transaction in _list(refund, "transactions")
            if transaction.get("kind") == "refund"
            and transaction.get("status", "success") == "success"
        ]
        amount = sum(_money(item.get("amount"), "refund transaction amount")
                     for item in refund_transactions)
        line_amount = sum(
            _money(line.get("subtotal"), "refund subtotal")
            + _money(line.get("total_tax"), "refund tax")
            for line in _list(refund, "refund_line_items")
        )
        if amount != line_amount:
            raise ReportError("Refund transaction does not reconcile to refunded lines.")
        refund_count += 1
        refund_amount += amount
    return {
        "gross": gross, "taxable": taxable, "cgst": cgst, "sgst": sgst,
        "tax": tax, "discounts": discounts, "refund_count": refund_count,
        "refund_amount": refund_amount, "payments": payments,
    }


def _equals(value: int, order: Dict[str, Any], field: str, label: str) -> None:
    if value != _money(order.get(field), field):
        raise ReportError(f"{label} do not reconcile with Shopify order totals.")


def _money(value: Any, label: str) -> int:
    try:
        amount = Decimal(str(value))
        if amount < 0 or amount.quantize(Decimal("0.01")) != amount:
            raise InvalidOperation
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        raise ReportError(f"Invalid {label}.")


def _integer(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ReportError(f"Invalid {label}.")
    if result < 0:
        raise ReportError(f"Invalid {label}.")
    return result


def _list(value: Dict[str, Any], field: str) -> list:
    result = value.get(field, [])
    if not isinstance(result, list):
        raise ReportError(f"Shopify order field {field} is malformed.")
    return result


def _note(order: Dict[str, Any], name: str) -> Optional[str]:
    for attribute in _list(order, "note_attributes"):
        if isinstance(attribute, dict) and attribute.get("name") == name:
            return str(attribute.get("value"))
    return None


def _business_date(order: Dict[str, Any]) -> str:
    timestamp = order.get("processed_at") or order.get("created_at")
    if not isinstance(timestamp, str) or len(timestamp) < 10:
        raise ReportError("Shopify order has no valid sale timestamp.")
    return timestamp[:10]


def _is_cancelled(order: Dict[str, Any]) -> bool:
    return bool(order.get("cancelled_at")) or str(
        order.get("financial_status", "")
    ).lower() in {"cancelled", "canceled", "voided"}
