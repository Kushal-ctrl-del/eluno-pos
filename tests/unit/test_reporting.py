from datetime import date
import unittest

from app.domain.reporting import ReportError, calculate_day_end_report


def order(order_id, payment_method, *, cancelled=False, refunded=False):
    result = {
        "id": order_id,
        "processed_at": "2026-10-10T12:00:00+05:30",
        "financial_status": "paid",
        "cancelled_at": "2026-10-10T13:00:00+05:30" if cancelled else None,
        "note_attributes": [
            {"name": "store_code", "value": "HSR"},
            {"name": "payment_method", "value": payment_method},
            {"name": "channel", "value": "walk_in" if payment_method == "upi" else "online_pickup"},
        ],
        "total_line_items_price": "118.00",
        "total_discounts": "18.00",
        "total_tax": "15.25",
        "total_price": "100.00",
        "line_items": [{
            "price": "118.00", "quantity": 1, "total_discount": "18.00",
            "tax_lines": [
                {"title": "CGST", "price": "7.62"},
                {"title": "SGST", "price": "7.63"},
            ],
        }],
        "transactions": [{
            "kind": "sale", "status": "success", "amount": "100.00",
        }],
        "refunds": [],
    }
    if refunded:
        result["refunds"] = [{
            "refund_line_items": [{
                "subtotal": "84.75", "total_tax": "15.25",
            }],
            "transactions": [{
                "kind": "refund", "status": "success", "amount": "100.00",
            }],
        }]
    return result


class ReportingTests(unittest.TestCase):
    def test_report_reconciles_multiple_orders_and_refunds(self):
        first = order(1, "upi", refunded=True)
        second = order(2, "card")
        report = calculate_day_end_report([first, second], "HSR", "2026-10-10")
        self.assertEqual(report.bill_count, 2)
        self.assertEqual(report.gross_sales_paise, 23600)
        self.assertEqual(report.taxable_value_paise, 16950)
        self.assertEqual(report.cgst_paise, 1524)
        self.assertEqual(report.sgst_paise, 1526)
        self.assertEqual(report.total_tax_paise, 3050)
        self.assertEqual(report.discounts_paise, 3600)
        self.assertEqual(report.refund_count, 1)
        self.assertEqual(report.refund_amount_paise, 10000)
        self.assertEqual(report.payment_method_paise, {"upi": 10000, "card": 10000})
        self.assertEqual(
            report.sales_channel_paise,
            {"walk_in": 10000, "online_pickup": 10000},
        )

    def test_cancelled_order_is_excluded(self):
        report = calculate_day_end_report(
            [order(1, "upi", cancelled=True)], "HSR", "2026-10-10"
        )
        self.assertEqual(report.bill_count, 0)
        self.assertEqual(report.gross_sales_paise, 0)

    def test_mismatch_is_rejected(self):
        invalid = order(1, "upi")
        invalid["total_price"] = "101.00"
        with self.assertRaises(ReportError):
            calculate_day_end_report([invalid], "HSR", "2026-10-10")

    def test_refund_is_attributed_to_original_sale_date_and_store(self):
        report = calculate_day_end_report(
            [order(1, "upi", refunded=True)], "HSR", date(2026, 10, 10).isoformat()
        )
        self.assertEqual(report.refund_amount_paise, 10000)

    def test_missing_or_legacy_channel_is_reported_as_unknown(self):
        missing = order(1, "upi")
        missing["note_attributes"] = [
            item for item in missing["note_attributes"] if item["name"] != "channel"
        ]
        legacy = order(2, "card")
        legacy["note_attributes"] = [
            item for item in legacy["note_attributes"]
            if item["name"] != "channel"
        ] + [{"name": "channel", "value": "website"}]
        report = calculate_day_end_report([missing, legacy], "HSR", "2026-10-10")
        self.assertEqual(report.sales_channel_paise, {"unknown": 20000})
