import copy
import unittest

from app.application.invoices import InvoiceError, render_invoice


def location():
    return {
        "id": 70000002,
        "name": "Eluno HSR Layout",
        "address1": "HSR Layout, Bengaluru",
    }


def order():
    return {
        "id": 95000001,
        "name": "#1001",
        "processed_at": "2026-10-02T12:30:00+05:30",
        "location_id": 70000002,
        "customer": {
            "first_name": "Karthik", "last_name": "Reddy",
            "phone": "+919000000005",
        },
        "total_line_items_price": "20497.00",
        "total_discounts": "1424.85",
        "total_tax": "2062.14",
        "total_price": "19072.15",
        "note_attributes": [
            {"name": "invoice_no", "value": "ELU/HSR/FY26/00001"},
            {"name": "store_code", "value": "HSR"},
            {"name": "staff_id", "value": "S105"},
            {"name": "channel", "value": "whatsapp_booked"},
            {"name": "payment_method", "value": "upi"},
        ],
        "discount_applications": [{"title": "PGX_CAMPAIGN15"}],
        "line_items": [
            {
                "title": "Eluno Atelier Rectangle 03",
                "variant_title": "Tortoise",
                "quantity": 1, "price": "10998.00", "total_discount": "0.00",
                "properties": [
                    {"name": "line_type", "value": "frame"},
                    {"name": "hsn", "value": "90031900"},
                ],
                "tax_lines": [
                    {"title": "CGST", "price": "838.83"},
                    {"title": "SGST", "price": "838.83"},
                ],
            },
            {
                "title": "Progressive 1.60", "variant_title": "blue cut lens",
                "quantity": 1, "price": "9499.00", "total_discount": "1424.85",
                "properties": [
                    {"name": "line_type", "value": "lens"},
                    {"name": "hsn", "value": "90015000"},
                    {"name": "rx_id", "value": "RX5007"},
                ],
                "tax_lines": [
                    {"title": "CGST", "price": "192.24"},
                    {"title": "SGST", "price": "192.24"},
                ],
            },
        ],
        "transactions": [{
            "kind": "sale", "status": "success", "amount": "19072.15",
            "authorization": "term_97000001", "gateway": "terminal",
        }],
        "refunds": [],
    }


class InvoiceTests(unittest.TestCase):
    def test_rendered_invoice_uses_shopify_totals_and_required_details(self):
        rendered = render_invoice(order(), location())
        for expected in (
            "TAX INVOICE", "ELU/HSR/FY26/00001", "Eluno HSR Layout",
            "Karthik Reddy", "RX5007", "PGX_CAMPAIGN15",
            "UPI 19,072.15 ref term_97000001", "10,998.00",
            "1,424.85", "17,010.01", "1,031.07", "19,072.15",
            "9%", "838.83", "2.5%", "192.24",
            "Amount in words: Nineteen thousand seventy-two rupees and fifteen paise",
            "Prices are inclusive of GST",
            "Lenses are made to order against the prescription on file",
        ):
            self.assertIn(expected, rendered)

    def test_refund_details_are_printed_without_changing_sale_totals(self):
        value = order()
        value["refunds"] = [{
            "id": 990,
            "refund_line_items": [{
                "line_item_id": 1, "quantity": 1,
                "subtotal": "10159.17", "total_tax": "1697.66",
                "restock_type": "return",
            }],
            "transactions": [{"kind": "refund", "amount": "11856.83"}],
        }]
        rendered = render_invoice(value, location())
        self.assertIn("REFUND / CREDIT NOTE DETAILS", rendered)
        self.assertIn("Refund 990 : 11,856.83", rendered)
        self.assertIn("tax reversal 1,697.66", rendered)

    def test_missing_invoice_or_inconsistent_total_is_rejected(self):
        value = order()
        value["note_attributes"] = [
            item for item in value["note_attributes"]
            if item["name"] != "invoice_no"
        ]
        with self.assertRaisesRegex(InvoiceError, "invoice number"):
            render_invoice(value, location())
        value = order()
        value["total_price"] = "19072.16"
        with self.assertRaisesRegex(InvoiceError, "reconcile"):
            render_invoice(value, location())

    def test_malformed_line_data_is_rejected(self):
        value = order()
        value["line_items"][0]["tax_lines"][0]["price"] = "bad"
        with self.assertRaises(InvoiceError):
            render_invoice(value, location())
