"""Shopify order adapter and payload mapping."""

from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

from app.adapters.http_client import JsonHttpClient
from app.config import Settings
from app.domain.cart import Cart
from app.domain.customer import Customer
from app.domain.prescription import Prescription
from app.domain.tax import TaxCalculation


class ShopifyOrderClient:
    def __init__(self, settings: Settings, http_client: JsonHttpClient) -> None:
        self._base_url = settings.shopify_base_url
        self._http = http_client

    def create_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._http.request_json(
            "POST", f"{self._base_url}/orders.json",
            payload={"order": payload}, expected_statuses=(201,),
        )

    def find_orders(self, checkout_id: str) -> List[Dict[str, Any]]:
        query = urlencode({"status": "any", "limit": "250"})
        response = self._http.get_json(f"{self._base_url}/orders.json?{query}")
        orders = response.get("orders") if isinstance(response, dict) else None
        if not isinstance(orders, list):
            raise ValueError("Shopify order response has no orders list.")
        return [
            order for order in orders
            if _note_value(order.get("note_attributes", []), "checkout_ref") == checkout_id
        ]

    def list_orders(
        self, *, created_at_min: str = "", created_at_max: str = "",
        location_id: Optional[int] = None, limit: int = 250
    ) -> List[Dict[str, Any]]:
        """Read orders in a time window.

        The supplied mock exposes a limit-only collection endpoint. Production
        Shopify integration must add cursor pagination before using this as an
        unbounded report source.
        """
        values = {"status": "any", "limit": str(limit)}
        if created_at_min:
            values["created_at_min"] = created_at_min
        if created_at_max:
            values["created_at_max"] = created_at_max
        if location_id is not None:
            values["location_id"] = str(location_id)
        query = urlencode(values)
        response = self._http.get_json(f"{self._base_url}/orders.json?{query}")
        orders = response.get("orders") if isinstance(response, dict) else None
        if not isinstance(orders, list):
            raise ValueError("Shopify order response has no orders list.")
        return orders

    def get_order(self, order_id: int) -> Dict[str, Any]:
        response = self._http.get_json(f"{self._base_url}/orders/{order_id}.json")
        order = response.get("order") if isinstance(response, dict) else None
        if not isinstance(order, dict):
            raise ValueError("Shopify order response has no order.")
        return order

    def get_location(self, location_id: int) -> Dict[str, Any]:
        response = self._http.get_json(f"{self._base_url}/locations.json")
        locations = response.get("locations") if isinstance(response, dict) else None
        if not isinstance(locations, list):
            raise ValueError("Shopify location response has no locations list.")
        for location in locations:
            if isinstance(location, dict) and int(location.get("id", -1)) == location_id:
                return location
        raise ValueError(f"Shopify location {location_id} was not found.")

    def calculate_refund(
        self, order_id: int, refund_line_items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self._http.request_json(
            "POST",
            f"{self._base_url}/orders/{order_id}/refunds/calculate.json",
            payload={"refund": {"refund_line_items": refund_line_items}},
            expected_statuses=(200,),
        )

    def create_refund(self, order_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._http.request_json(
            "POST",
            f"{self._base_url}/orders/{order_id}/refunds.json",
            payload={"refund": payload},
            expected_statuses=(201,),
        )


def build_order_payload(
    *,
    cart: Cart,
    customer: Customer,
    prescription: Prescription,
    tax: TaxCalculation,
    store_code: str,
    location_id: int,
    staff_id: str,
    invoice_number: str,
    checkout_id: str,
    charge_id: str,
    payment_method: str,
    discount_allocations: Iterable[object],
    sales_channel: str = "walk_in",
) -> Dict[str, Any]:
    allocations_by_line: Dict[str, List[object]] = {}
    for allocation in discount_allocations:
        allocations_by_line.setdefault(allocation.line_id, []).append(allocation)
    tax_by_line = {item.line_id: item for item in tax.lines}
    line_items = []
    for line in cart.lines:
        tax_line = tax_by_line[line.line_id]
        properties = [
            {"name": "line_type", "value": line.line_type},
            {"name": "pair_ref", "value": line.pair_ref or ""},
            {"name": "hsn", "value": line.hsn},
        ]
        if line.line_type == "lens":
            properties.extend([
                {"name": "rx_id", "value": prescription.rx_id},
                {"name": "prescription_od", "value": _eye_value(prescription.od)},
                {"name": "prescription_os", "value": _eye_value(prescription.os)},
            ])
        line_items.append({
            "variant_id": line.variant_id,
            "price": _money(line.unit_price_paise),
            "quantity": line.quantity,
            "total_discount": _money(tax_line.discount_paise),
            "discount_allocations": [
                {"amount": _money(item.amount_paise), "discount_application_index": index}
                for index, item in enumerate(allocations_by_line.get(line.line_id, []))
            ],
            "tax_lines": [
                {"title": "CGST", "price": _money(tax_line.cgst_paise), "rate": tax_line.rate_bps / 20000},
                {"title": "SGST", "price": _money(tax_line.sgst_paise), "rate": tax_line.rate_bps / 20000},
            ],
            "properties": properties,
        })
    return {
        "taxes_included": True,
        "customer": {"id": customer.customer_id},
        "location_id": location_id,
        "inventory_behaviour": "decrement_obeying_policy",
        "source_name": "eluno_pos",
        "line_items": line_items,
        "transactions": [{
            "kind": "sale",
            "amount": _money(tax.total_paise),
            "gateway": "terminal",
            "source": payment_method,
            "charge_id": charge_id,
        }],
        "note_attributes": [
            {"name": "invoice_no", "value": invoice_number},
            {"name": "store_code", "value": store_code},
            {"name": "staff_id", "value": staff_id},
            {"name": "payment_method", "value": payment_method},
            {"name": "channel", "value": sales_channel},
            {"name": "checkout_ref", "value": checkout_id},
        ],
    }


def _note_value(attributes: Any, name: str) -> Optional[str]:
    if not isinstance(attributes, list):
        return None
    for attribute in attributes:
        if isinstance(attribute, dict) and attribute.get("name") == name:
            return str(attribute.get("value"))
    return None


def _eye_value(eye: Any) -> str:
    return "sph={0};cyl={1};axis={2};add={3}".format(
        eye.sph, eye.cyl, eye.axis, eye.add
    )


def _money(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"
