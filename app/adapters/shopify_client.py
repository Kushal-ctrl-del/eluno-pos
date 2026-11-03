"""Shopify adapter mapping Admin API responses into domain values."""

from decimal import Decimal
from typing import Any, Tuple
from urllib.parse import quote

from app.adapters.http_client import JsonHttpClient
from app.config import Settings
from app.domain.customer import Customer
from app.domain.prescription import LensLimits, LensVariant


class ShopifyClient:
    def __init__(self, settings: Settings, http_client: JsonHttpClient) -> None:
        self._base_url = settings.shopify_base_url
        self._http = http_client

    def find_by_phone(self, phone: str) -> Customer:
        response = self._http.get_json(
            f"{self._base_url}/customers/search.json?query=phone:{quote(phone, safe='')}"
        )
        customers = response.get("customers") if isinstance(response, dict) else None
        if not isinstance(customers, list):
            raise ValueError("Shopify customer response has no customers list.")
        if not customers:
            raise LookupError(f"No Shopify customer found for phone {phone}.")
        raw = customers[0]
        try:
            tags = tuple(tag.strip() for tag in raw.get("tags", "").split(",") if tag.strip())
            total_spent_paise = int((Decimal(str(raw["total_spent"])) * 100).quantize(Decimal("1")))
            return Customer(int(raw["id"]), str(raw["phone"]), tags, total_spent_paise)
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            raise ValueError("Shopify customer response is malformed.") from error

    def get_lens_variant(self, variant_id: int) -> LensVariant:
        response = self._http.get_json(f"{self._base_url}/variants/{variant_id}.json")
        raw = response.get("variant") if isinstance(response, dict) else None
        try:
            eluno = raw["eluno"]
            limits = eluno["rx_limits"]
            return LensVariant(
                variant_id=int(raw["id"]),
                sku=str(raw["sku"]),
                lens_type=str(eluno["lens_type"]),
                index=float(eluno["index"]),
                coating=str(eluno["coating"]),
                limits=LensLimits(
                    sph_min=float(limits["sph_min"]),
                    sph_max=float(limits["sph_max"]),
                    cyl_abs_max=float(limits["cyl_abs_max"]),
                    add_required=bool(limits["add_required"]),
                    add_min=_optional_float(limits.get("add_min")),
                    add_max=_optional_float(limits.get("add_max")),
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Shopify lens variant {variant_id} response is malformed.") from error

    def list_products(self, product_type: str = "", limit: int = 250) -> list:
        query = f"product_type={quote(product_type, safe='')}&limit={limit}" if product_type else f"limit={limit}"
        response = self._http.get_json(f"{self._base_url}/products.json?{query}")
        products = response.get("products") if isinstance(response, dict) else None
        if not isinstance(products, list):
            raise ValueError("Shopify product response has no products list.")
        return products

    def list_locations(self) -> list:
        response = self._http.get_json(f"{self._base_url}/locations.json")
        locations = response.get("locations") if isinstance(response, dict) else None
        if not isinstance(locations, list):
            raise ValueError("Shopify location response has no locations list.")
        return locations


def _optional_float(value: Any) -> Any:
    return None if value is None else float(value)
