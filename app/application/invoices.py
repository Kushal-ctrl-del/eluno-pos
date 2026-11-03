"""Printable invoice rendering from Shopify order truth."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from app.adapters.shopify_orders import ShopifyOrderClient


class InvoiceError(ValueError):
    """Raised when an order cannot be rendered as an accurate invoice."""


class InvoiceService:
    def __init__(self, shopify: ShopifyOrderClient) -> None:
        self._shopify = shopify

    def render(self, order_id: int) -> str:
        order = self._shopify.get_order(order_id)
        location_id = order.get("location_id")
        if location_id is None:
            raise InvoiceError("Shopify order has no billing location.")
        location = self._shopify.get_location(int(location_id))
        return render_invoice(order, location)


def render_invoice(order: Dict[str, Any], location: Dict[str, Any]) -> str:
    attributes = _attributes(order)
    invoice_no = attributes.get("invoice_no")
    store_code = attributes.get("store_code")
    if not invoice_no or not store_code:
        raise InvoiceError("Order is missing its Eluno invoice number or store code.")
    customer = order.get("customer")
    if not isinstance(customer, dict) or not customer.get("phone"):
        raise InvoiceError("Order is missing required customer details.")
    lines = order.get("line_items")
    if not isinstance(lines, list) or not lines:
        raise InvoiceError("Order has no line items.")
    location_name = location.get("name")
    address = location.get("address1")
    if not location_name or not address:
        raise InvoiceError("Shopify location is missing printable store details.")

    rendered_lines = [_render_line(line) for line in lines]
    sale = _sale_transaction(order)
    total = _money(order.get("total_price"), "order total")
    gross = sum(item["gross"] for item in rendered_lines)
    discounts = sum(item["discount"] for item in rendered_lines)
    taxable = sum(item["taxable"] for item in rendered_lines)
    cgst = sum(item["cgst"] for item in rendered_lines)
    sgst = sum(item["sgst"] for item in rendered_lines)
    tax = cgst + sgst
    if gross != _money(order.get("total_line_items_price"), "gross total"):
        raise InvoiceError("Invoice lines do not reconcile to Shopify gross total.")
    if discounts != _money(order.get("total_discounts"), "discount total"):
        raise InvoiceError("Invoice lines do not reconcile to Shopify discounts.")
    if taxable + tax != gross - discounts or total != gross - discounts:
        raise InvoiceError("Invoice totals do not reconcile to Shopify order totals.")
    sale_amount = _money(sale.get("amount"), "sale transaction amount")
    if sale_amount != total:
        raise InvoiceError("Sale transaction does not reconcile to Shopify order total.")

    width = 104
    separator = "-" * width
    rows = [
        "=" * width,
        _center("ELUNO", width),
        _center(f"{location_name} · {address}", width),
        _center("TAX INVOICE", width),
        "=" * width,
        f"Invoice no : {invoice_no:<38}Date        : {_date(order)}",
        f"Store      : {store_code:<38}Staff       : {attributes.get('staff_id', '—')}",
        f"Customer   : {_customer_name(customer):<38}Phone       : {customer['phone']}",
        f"Rx on file : {_rx_ids(lines):<38}Channel     : {attributes.get('channel', '—')}",
        separator,
        "#  Description                            HSN       Qty  MRP(incl)  Discount    Taxable         CGST         SGST      Total",
        separator,
    ]
    for index, item in enumerate(rendered_lines, 1):
        rows.append(
            f"{index:<3}{item['description'][:35]:<36}{item['hsn']:<10}"
            f"{item['quantity']:>3}  {_fmt(item['gross']):>9}  {_fmt(item['discount']):>8}"
            f"  {_fmt(item['taxable']):>9}  {item['cgst_rate']:>5}% {_fmt(item['cgst']):>9}"
            f"  {item['sgst_rate']:>5}% {_fmt(item['sgst']):>9}  {_fmt(item['total']):>9}"
        )
    rows.extend([
        separator,
        f"   TOTAL{'':49}{_fmt(gross):>9}  {_fmt(discounts):>8}"
        f"  {_fmt(taxable):>9}  {_fmt(cgst):>9}  {_fmt(sgst):>9}  {_fmt(total):>9}",
        "=" * width,
        f"Offers applied : {_offers(order)}",
        f"Payment        : {_payment(attributes, sale)}",
        f"Amount in words: {_amount_in_words(total)}",
        f"Shopify ref    : {order.get('name', '—')} (order {order.get('id', '—')})",
    ])
    refunds = _refund_summary(order)
    if refunds:
        rows.extend([
            separator,
            "REFUND / CREDIT NOTE DETAILS",
            *refunds,
        ])
    rows.extend([
        separator,
        "Prices are inclusive of GST. Intra-state supply: CGST + SGST.",
        "Taxable value is calculated from Shopify line price, discount and tax lines.",
        "Lenses are made to order against the prescription on file and are not returnable once dispensed; frames may be returned within 7 days.",
        "=" * width,
    ])
    return "\n".join(rows) + "\n"


def _render_line(line: Dict[str, Any]) -> Dict[str, Any]:
    quantity = _positive_int(line.get("quantity"), "line quantity")
    price = _money(line.get("price"), "line price") * quantity
    discount = _money(line.get("total_discount", "0.00"), "line discount")
    cgst = sgst = 0
    for tax_line in _list(line, "tax_lines"):
        amount = _money(tax_line.get("price"), "tax line")
        title = str(tax_line.get("title", "")).upper()
        if title == "CGST":
            cgst += amount
        elif title == "SGST":
            sgst += amount
    total_tax = cgst + sgst
    taxable = price - discount - total_tax
    if taxable < 0:
        raise InvoiceError("Invoice line has a negative taxable value.")
    properties = _properties(line)
    description = str(line.get("title") or "Item")
    if line.get("variant_title"):
        description += " " + str(line["variant_title"])
    if properties.get("line_type") == "lens" and properties.get("rx_id"):
        description += ", Rx " + properties["rx_id"]
    return {
        "description": description,
        "hsn": properties.get("hsn", "—"),
        "quantity": quantity,
        "gross": price,
        "discount": discount,
        "taxable": taxable,
        "cgst": cgst,
        "sgst": sgst,
        "cgst_rate": _tax_rate(line, "CGST", taxable),
        "sgst_rate": _tax_rate(line, "SGST", taxable),
        "total": price - discount,
    }


def _refund_summary(order: Dict[str, Any]) -> List[str]:
    result = []
    for refund in _list(order, "refunds"):
        transactions = [
            item for item in _list(refund, "transactions")
            if item.get("kind") == "refund"
        ]
        amount = sum(_money(item.get("amount"), "refund amount") for item in transactions)
        if amount <= 0:
            raise InvoiceError("Refund has no positive refund transaction.")
        result.append(f"Refund {refund.get('id', '—')} : {_fmt(amount)}")
        for line in _list(refund, "refund_line_items"):
            result.append(
                f"  Line {line.get('line_item_id', '—')} qty {line.get('quantity', '—')}"
                f" · tax reversal {_fmt(_money(line.get('total_tax'), 'refund tax'))}"
                f" · {line.get('restock_type', '—')}"
            )
    return result


def _tax_rate(line: Dict[str, Any], title: str, taxable: int) -> str:
    for tax_line in _list(line, "tax_lines"):
        if str(tax_line.get("title", "")).upper() == title:
            rate = tax_line.get("rate")
            if rate is not None:
                return _format_rate(rate)
            amount = _money(tax_line.get("price"), "tax line")
            if taxable:
                return _format_rate(Decimal(amount) / Decimal(taxable))
    return "—"


def _format_rate(value: Any) -> str:
    decimal = Decimal(str(value)) * 100
    return f"{decimal.quantize(Decimal('0.01')):f}".rstrip("0").rstrip(".")


def _amount_in_words(paise: int) -> str:
    rupees, remainder = divmod(paise, 100)
    words = _indian_number_words(rupees) + " rupees"
    if remainder:
        words += " and " + _indian_number_words(remainder) + " paise"
    return words[:1].upper() + words[1:]


def _indian_number_words(value: int) -> str:
    if value == 0:
        return "zero"
    ones = (
        "zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
        "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    )
    tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty",
            "seventy", "eighty", "ninety")

    def below_thousand(number: int) -> str:
        parts = []
        if number >= 100:
            parts.extend((ones[number // 100], "hundred"))
            number %= 100
        if number >= 20:
            if number % 10:
                parts.append(tens[number // 10] + "-" + ones[number % 10])
                return " ".join(parts)
            parts.append(tens[number // 10])
            number %= 10
        if number:
            parts.append(ones[number])
        return " ".join(parts)

    parts = []
    if value >= 10000000:
        parts.extend((below_thousand(value // 10000000), "crore"))
        value %= 10000000
    if value >= 100000:
        parts.extend((below_thousand(value // 100000), "lakh"))
        value %= 100000
    if value >= 1000:
        parts.extend((below_thousand(value // 1000), "thousand"))
        value %= 1000
    if value:
        parts.append(below_thousand(value))
    return " ".join(parts)


def _sale_transaction(order: Dict[str, Any]) -> Dict[str, Any]:
    for transaction in _list(order, "transactions"):
        if transaction.get("kind") == "sale" and transaction.get("status", "success") == "success":
            return transaction
    raise InvoiceError("Order has no successful sale transaction.")


def _payment(attributes: Dict[str, str], sale: Dict[str, Any]) -> str:
    method = attributes.get("payment_method") or sale.get("source") or sale.get("gateway")
    return f"{str(method).upper()} {_fmt(_money(sale.get('amount'), 'payment'))} ref {sale.get('authorization') or sale.get('charge_id', '—')}"


def _offers(order: Dict[str, Any]) -> str:
    applications = _list(order, "discount_applications")
    names = [str(item.get("title")) for item in applications if item.get("title")]
    return ", ".join(names) if names else "None"


def _customer_name(customer: Dict[str, Any]) -> str:
    name = " ".join(str(customer.get(field, "")).strip() for field in ("first_name", "last_name")).strip()
    return name or "—"


def _rx_ids(lines: List[Dict[str, Any]]) -> str:
    ids = [_properties(line).get("rx_id") for line in lines]
    return ", ".join(str(item) for item in ids if item) or "—"


def _date(order: Dict[str, Any]) -> str:
    value = order.get("processed_at") or order.get("created_at")
    if not isinstance(value, str) or len(value) < 10:
        raise InvoiceError("Order has no valid date.")
    return value[:10]


def _attributes(order: Dict[str, Any]) -> Dict[str, str]:
    return {
        str(item["name"]): str(item.get("value", ""))
        for item in _list(order, "note_attributes")
        if isinstance(item, dict) and item.get("name")
    }


def _properties(line: Dict[str, Any]) -> Dict[str, str]:
    return {
        str(item["name"]): str(item.get("value", ""))
        for item in _list(line, "properties")
        if isinstance(item, dict) and item.get("name")
    }


def _list(value: Dict[str, Any], field: str) -> list:
    result = value.get(field, [])
    if not isinstance(result, list):
        raise InvoiceError(f"Shopify field {field} is malformed.")
    return result


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise InvoiceError(f"Invalid {label}.")
    if result <= 0:
        raise InvoiceError(f"Invalid {label}.")
    return result


def _money(value: Any, label: str) -> int:
    try:
        amount = Decimal(str(value))
        if amount < 0 or amount.quantize(Decimal("0.01")) != amount:
            raise InvalidOperation
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        raise InvoiceError(f"Invalid {label}.")


def _fmt(paise: int) -> str:
    return f"{paise // 100:,.0f}.{paise % 100:02d}"


def _center(value: str, width: int) -> str:
    return value.center(width)
