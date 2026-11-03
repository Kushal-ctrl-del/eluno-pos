"""Dependency-free local HTTP API and static frontend for Eluno POS."""

import json
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from app.composition import build_application
from app.adapters.shopify_orders import build_order_payload
from app.application.checkout import CheckoutError, CheckoutRequest
from app.domain.cart import Cart, CartLine
from app.domain.offers import OfferContext, evaluate_offers, parse_offer
from app.domain.prescription import LensSelection
from app.domain.sales_channel import SUPPORTED_CHANNELS
from app.domain.tax import calculate_tax, parse_tax_rate


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "web"


class PosHandler(BaseHTTPRequestHandler):
    application = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._api_get(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._headers(0, "text/plain")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _api_get(self, path: str, query: Dict[str, Any]) -> None:
        try:
            if path == "/api/health":
                self._json({"services": [result.__dict__ for result in self.application.health_check()]})
            elif path == "/api/stores":
                self._json({"stores": self.application.product_catalog.list_locations()})
            elif path == "/api/customer":
                phone = _one(query, "phone")
                if not phone:
                    raise ValueError("phone is required")
                customer, prescription = self.application.customer_prescription.load_customer_with_latest_prescription(phone)
                self._json({
                    "customer": {
                        "id": customer.customer_id,
                        "phone": customer.phone,
                        "tags": list(customer.tags),
                        "total_spent_paise": customer.total_spent_paise,
                    },
                    "prescription": _prescription_json(prescription),
                })
            elif path == "/api/products":
                product_type = _one(query, "type") or ""
                self._json({"products": _catalog_json(
                    self.application.product_catalog.list_products(product_type)
                )})
            elif path == "/api/order":
                order_id = int(_one(query, "id") or 0)
                self._json({"order": self.application.shopify_orders.get_order(order_id)})
            elif path == "/api/orders":
                orders = self.application.shopify_orders.list_orders(limit=250)
                term = (_one(query, "q") or "").lower()
                if term:
                    orders = [order for order in orders if term in json.dumps(order).lower()]
                self._json({"orders": orders})
            elif path == "/api/invoice":
                self._text(self.application.invoices.render(int(_one(query, "id") or 0)))
            elif path == "/api/refund":
                self._json({"refund": _db_row(self.application.database, "refund_records", "refund_request_id", _one(query, "request"))})
            elif path == "/api/audit":
                self._json({"events": self.application.audit.list_events()})
            elif path == "/api/checkout/recovery":
                self._json({"checkouts": self.application.checkout.list_uncertain()})
            elif path == "/api/checkout/status":
                checkout_id = _one(query, "id")
                if not checkout_id:
                    raise ValueError("checkout id is required")
                self._json({"checkout": self.application.checkout.get_status(checkout_id)})
            elif path == "/api/till/session":
                self._json({"session": self.application.till.get_session(_one(query, "id") or "")})
            elif path == "/api/till/open":
                self._json({"session": _open_till_from_db(self.application.database, _one(query, "store") or "HSR")})
            elif path == "/api/report":
                store = _one(query, "store")
                business_date = _one(query, "date")
                if not store or not business_date:
                    raise ValueError("store and date are required")
                report = self.application.reporting.day_end(store, date.fromisoformat(business_date))
                result = _report_json(report)
                result["till"] = _latest_till(self.application.database, store)
                self._json(result)
            else:
                self._error(404, "not_found", "API route not found")
        except CheckoutError as error:
            self._error(409, error.code, str(error))
        except LookupError as error:
            self._error(404, "not_found", str(error))
        except (ValueError, TypeError) as error:
            self._error(400, "invalid_request", str(error))
        except Exception as error:
            self._error(502, "upstream_error", str(error))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._error(404, "not_found", "API route not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if parsed.path == "/api/quote":
                self._json(_quote_response(self.application, body))
            elif parsed.path == "/api/checkout":
                self._json(_checkout_response(self.application, body))
            elif parsed.path == "/api/checkout/reconcile":
                result = self.application.checkout.reconcile(
                    str(body.get("checkout_id") or "")
                )
                self._json({"checkout": result.__dict__})
            elif parsed.path == "/api/checkout/terminal-reconcile":
                status = self.application.checkout.reconcile_terminal(
                    str(body.get("checkout_id") or ""),
                    str(body.get("charge_id") or ""),
                )
                self._json({"status": status})
            elif parsed.path == "/api/checkout/manual-resolution":
                resolution = self.application.checkout.record_manual_resolution(
                    str(body.get("checkout_id") or ""),
                    str(body.get("actor_id") or ""),
                    str(body.get("reason") or ""),
                )
                self._json({"resolution": resolution})
            elif parsed.path == "/api/refund":
                self._json({"refund": _refund_response(self.application, body)})
            elif parsed.path == "/api/refund/reconcile":
                self._json({"refund": self.application.refunds.reconcile(str(body.get("refund_request_id") or ""))})
            elif parsed.path == "/api/till/open":
                self._json({"session": self.application.till.open_session(
                    store_code=str(body.get("store_code") or "HSR"),
                    location_id=int(body.get("location_id") or 70000002),
                    opened_by=str(body.get("staff_id") or "S105"),
                    opening_float_paise=int(body.get("opening_float_paise") or 0),
                    session_id=str(body.get("session_id") or ""),
                )})
            elif parsed.path == "/api/till/movement":
                self._json({"session": self.application.till.record_movement(
                    session_id=str(body.get("session_id") or ""),
                    movement_type=str(body.get("movement_type") or ""),
                    amount_paise=int(body.get("amount_paise") or 0),
                    reference=str(body.get("reference") or ""),
                    reason=str(body.get("reason") or ""),
                )})
            elif parsed.path == "/api/till/close":
                result = self.application.till.close_session(
                    session_id=str(body.get("session_id") or ""),
                    closed_by=str(body.get("staff_id") or "S105"),
                    counted_cash_paise=int(body.get("counted_cash_paise") or 0),
                )
                self._json({"session": result.__dict__})
            else:
                self._error(404, "not_found", "API route not found")
        except CheckoutError as error:
            self._error(409, error.code, str(error))
        except LookupError as error:
            self._error(404, "not_found", str(error))
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._error(400, "invalid_request", str(error))
        except Exception as error:
            self._error(502, "upstream_error", str(error))

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in candidate.parents and candidate != WEB_ROOT:
            self._error(404, "not_found", "File not found")
            return
        if not candidate.is_file():
            candidate = WEB_ROOT / "index.html"
        content_type = "text/html; charset=utf-8"
        if candidate.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif candidate.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        body = candidate.read_bytes()
        self.send_response(200)
        self._headers(len(body), content_type)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, body: Any, status: int = 200) -> None:
        encoded = json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self._headers(len(encoded), "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(encoded)

    def _text(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self._headers(len(encoded), "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json({"error": {"code": code, "message": message}}, status)

    def _headers(self, length: int, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")


def _one(query: Dict[str, Any], key: str) -> Optional[str]:
    values = query.get(key, [])
    return str(values[0]).strip() if values else None


def _prescription_json(prescription: Any) -> Dict[str, Any]:
    return {
        "rx_id": prescription.rx_id,
        "tested_on": prescription.tested_on.isoformat(),
        "valid_until": prescription.valid_until.isoformat(),
        "od": prescription.od.__dict__,
        "os": prescription.os.__dict__,
    }


def _catalog_json(products: Any) -> list:
    result = []
    for product in products:
        for variant in product.get("variants", []):
            eluno = variant.get("eluno", {})
            result.append({
                "product_id": product.get("id"),
                "name": product.get("title"),
                "product_type": product.get("product_type"),
                "variant_id": variant.get("id"),
                "sku": variant.get("sku"),
                "variant_title": variant.get("title"),
                "price": variant.get("price"),
                "attributes": {
                    key: eluno[key] for key in
                    ("line_type", "hsn", "category", "material", "size", "lens_type",
                     "index", "coating", "rx_limits") if key in eluno
                },
            })
    return result


def _quote_response(application: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    quote = _build_quote(application, body)
    return {
        "customer": {
            "id": quote["customer"].customer_id,
            "phone": quote["customer"].phone,
            "tags": list(quote["customer"].tags),
        },
        "prescription": _prescription_json(quote["prescription"]),
        "lines": [
            {
                "line_id": line.line_id,
                "line_type": line.line_type,
                "variant_id": line.variant_id,
                "quantity": line.quantity,
                "pair_ref": line.pair_ref,
                "unit_price_paise": line.unit_price_paise,
                "gross_paise": line.gross_paise,
                "lens_type": line.lens_type,
                "lens_index": line.lens_index,
                "coating": line.coating,
                "hsn": line.hsn,
            } for line in quote["cart"].lines
        ],
        "offers": {
            "applied": [
                {"offer_id": item.offer_id, "amount_paise": item.amount_paise,
                 "allocations": [allocation.__dict__ for allocation in item.allocations]}
                for item in quote["offers"].applied
            ],
            "rejected": [item.__dict__ for item in quote["offers"].rejected],
            "total_discount_paise": quote["offers"].total_discount_paise,
        },
        "tax": {
            "gross_paise": quote["tax"].gross_paise,
            "discount_paise": quote["tax"].discount_paise,
            "taxable_paise": quote["tax"].taxable_paise,
            "cgst_paise": quote["tax"].cgst_paise,
            "sgst_paise": quote["tax"].sgst_paise,
            "total_paise": quote["tax"].total_paise,
            "lines": [line.__dict__ for line in quote["tax"].lines],
        },
    }


def _checkout_response(application: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    quote = _build_quote(application, body)
    request = CheckoutRequest(
        checkout_id=str(body.get("checkout_id") or ""),
        customer=quote["customer"],
        prescription=quote["prescription"],
        cart=quote["cart"],
        offers=quote["offers"],
        tax=quote["tax"],
        store_code=str(body.get("store_code") or "HSR"),
        location_id=int(body.get("location_id") or 70000002),
        staff_id=str(body.get("staff_id") or "S105"),
        invoice_prefix=str(body.get("invoice_prefix") or "ELU/HSR/FY26/"),
        payment_method=str(body.get("payment_method") or ""),
        active_till=bool(body.get("active_till", True)),
        session_id=body.get("session_id"),
        checkout_date=quote["checkout_date"],
        sales_channel=str(body.get("sales_channel") or "walk_in"),
    )
    result = application.checkout.execute(request)
    return result.__dict__


def _build_quote(application: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    phone = str(body.get("phone") or "").strip()
    if not phone:
        raise ValueError("phone is required")
    customer, prescription = application.customer_prescription.load_customer_with_latest_prescription(phone)
    checkout_date = date.fromisoformat(str(body.get("checkout_date") or date.today().isoformat()))
    raw_lines = body.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("At least one cart line is required.")
    products = application.product_catalog.list_products("", 250)
    variants = {
        int(variant["id"]): (product, variant)
        for product in products for variant in product.get("variants", [])
    }
    cart_lines = []
    for index, raw in enumerate(raw_lines, 1):
        variant_id = int(raw["variant_id"])
        product, variant = variants.get(variant_id, (None, None))
        if variant is None:
            raise ValueError(f"Variant {variant_id} was not found in Shopify.")
        eluno = variant.get("eluno", {})
        line_type = str(raw.get("line_type") or eluno.get("line_type") or "").lower()
        if line_type not in ("frame", "lens") or line_type != str(eluno.get("line_type", "")).lower():
            raise ValueError(f"Variant {variant_id} has an invalid line type.")
        pair_ref = str(raw.get("pair_ref") or "p1")
        lens_type = eluno.get("lens_type") if line_type == "lens" else None
        lens_index = float(eluno["index"]) if line_type == "lens" else None
        coating = eluno.get("coating") if line_type == "lens" else None
        if line_type == "lens":
            issues = application.customer_prescription.validate_lens_selection(
                prescription, variant_id, LensSelection(lens_type, lens_index, coating)
            )
            if issues:
                raise ValueError("; ".join(issue.message for issue in issues))
        cart_lines.append(CartLine(
            line_id=str(raw.get("line_id") or f"line-{index}"),
            line_type=line_type,
            variant_id=variant_id,
            unit_price_paise=int((Decimal(str(variant["price"])) * 100).quantize(Decimal("1"))),
            quantity=int(raw.get("quantity") or 1),
            pair_ref=pair_ref,
            lens_type=lens_type,
            lens_index=lens_index,
            coating=coating,
            hsn=str(eluno.get("hsn") or ""),
        ))
    cart = Cart(tuple(cart_lines))
    offers = [
        parse_offer(item) for item in json.loads(
            (ROOT / "starter-kit" / "data" / "offers.json").read_text(encoding="utf-8")
        )
    ]
    context = OfferContext(
        customer_tags=customer.tags,
        staff_role=body.get("staff_role"),
        reason_code=body.get("reason_code"),
        manager_discount_bps=body.get("manager_discount_bps"),
    )
    evaluation = evaluate_offers(cart, offers, as_of=checkout_date, context=context)
    rates_data = json.loads(
        (ROOT / "starter-kit" / "data" / "tax_rates.json").read_text(encoding="utf-8")
    )
    tax = calculate_tax(
        cart, [parse_tax_rate(item) for item in rates_data["rates"]],
        as_of=checkout_date, discounts=_allocations(evaluation),
    )
    return {
        "customer": customer, "prescription": prescription, "cart": cart,
        "offers": evaluation, "tax": tax, "checkout_date": checkout_date,
    }


def _allocations(evaluation: Any) -> list:
    return [allocation for offer in evaluation.applied for allocation in offer.allocations]


def _report_json(report: Any) -> Dict[str, Any]:
    return {
        "store_code": report.store_code,
        "business_date": report.business_date,
        "bill_count": report.bill_count,
        "gross_sales_paise": report.gross_sales_paise,
        "taxable_value_paise": report.taxable_value_paise,
        "cgst_paise": report.cgst_paise,
        "sgst_paise": report.sgst_paise,
        "total_tax_paise": report.total_tax_paise,
        "discounts_paise": report.discounts_paise,
        "refund_count": report.refund_count,
        "refund_amount_paise": report.refund_amount_paise,
        "payment_method_paise": report.payment_method_paise,
        "sales_channel_paise": report.sales_channel_paise,
    }


def _db_row(database: Any, table: str, key: str, value: Optional[str]) -> Any:
    if not value:
        raise ValueError("request is required")
    with database.connection() as connection:
        row = connection.execute(f"SELECT * FROM {table} WHERE {key} = ?", (value,)).fetchone()
    if row is None:
        raise LookupError("record not found")
    return dict(row)


def _open_till_from_db(database: Any, store_code: str) -> Any:
    with database.connection() as connection:
        row = connection.execute(
            "SELECT session_id FROM till_sessions WHERE store_code = ? AND closed_at IS NULL",
            (store_code,),
        ).fetchone()
    if row is None:
        return None
    return row["session_id"]


def _latest_till(database: Any, store_code: str) -> Any:
    with database.connection() as connection:
        row = connection.execute(
            """SELECT session_id, opened_at, closed_at, opening_float_paise,
                      expected_cash_paise, counted_cash_paise, variance_paise,
                      variance_flagged
               FROM till_sessions WHERE store_code = ?
               ORDER BY opened_at DESC LIMIT 1""",
            (store_code,),
        ).fetchone()
    return dict(row) if row is not None else None


def _refund_response(application: Any, body: Dict[str, Any]) -> Any:
    selected = body.get("selected_lines")
    kwargs = {
        "refund_request_id": str(body.get("refund_request_id") or ""),
        "order_id": int(body.get("order_id") or 0),
        "reason": str(body.get("reason") or ""),
        "checkout_id": body.get("checkout_id"),
        "staff_id": body.get("staff_id") or "S105",
    }
    if selected:
        return application.refunds.refund_partial(selected_lines=selected, **kwargs)
    return application.refunds.refund_full(**kwargs)


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    application = build_application()
    PosHandler.application = application
    server = ThreadingHTTPServer((host, port), PosHandler)
    print(f"Eluno POS running at http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="eluno-pos-web")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(args.host, args.port)
