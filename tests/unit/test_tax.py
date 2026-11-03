from datetime import date
import json
from pathlib import Path
import unittest

from app.domain.cart import Cart, CartLine
from app.domain.offers import DiscountAllocation
from app.domain.tax import TaxRate, TaxValidationError, calculate_tax, parse_tax_rate


ROOT = Path(__file__).parents[2]
TAX_DATA = json.loads((ROOT / "starter-kit" / "data" / "tax_rates.json").read_text())
RATES = tuple(parse_tax_rate(raw) for raw in TAX_DATA["rates"])


def line(line_id, price, hsn, quantity=1):
    return CartLine(line_id, "frame", 1, price, quantity=quantity, hsn=hsn)


class TaxEngineTests(unittest.TestCase):
    def test_seed_rates_are_date_effective_and_mixed_hsn_cart_is_taxed(self):
        result = calculate_tax(
            Cart((line("frame", 10000, "90031900"), line("lens", 10000, "90015000"))),
            RATES,
            as_of=date(2026, 11, 2),
        )
        self.assertEqual(
            [(item.rate_bps, item.gst_paise) for item in result.lines],
            [(1800, 1525), (500, 476)],
        )
        self.assertEqual(result.total_paise, 20000)

    def test_discount_is_applied_before_tax_backout(self):
        result = calculate_tax(
            Cart((line("frame", 10000, "90031900"),)),
            RATES,
            as_of=date(2026, 11, 2),
            discounts=(DiscountAllocation("OFFER", "frame", 1000),),
        )
        item = result.lines[0]
        self.assertEqual(item.discounted_gross_paise, 9000)
        self.assertEqual((item.taxable_paise, item.gst_paise), (7627, 1373))

    def test_zero_discount_and_quantity_preserve_gross(self):
        result = calculate_tax(
            Cart((line("frame", 1, "90031900", quantity=3),)),
            RATES,
            as_of=date(2026, 11, 2),
        )
        item = result.lines[0]
        self.assertEqual(item.discount_paise, 0)
        self.assertEqual(item.taxable_paise + item.gst_paise, 3)

    def test_rounding_boundaries_use_half_up_and_split_remainder(self):
        rate = TaxRate("TEST", 10000, date(2026, 1, 1))
        result = calculate_tax(
            Cart((line("line", 1, "TEST"),)),
            (rate,),
            as_of=date(2026, 11, 2),
        )
        item = result.lines[0]
        self.assertEqual(item.taxable_paise, 1)
        self.assertEqual((item.cgst_paise, item.sgst_paise), (0, 0))

        result = calculate_tax(
            Cart((line("line", 3, "TEST"),)),
            (rate,),
            as_of=date(2026, 11, 2),
        )
        item = result.lines[0]
        self.assertEqual((item.taxable_paise, item.gst_paise), (2, 1))
        self.assertEqual((item.cgst_paise, item.sgst_paise), (1, 0))

    def test_missing_and_unsupported_tax_data_are_clear(self):
        with self.assertRaisesRegex(TaxValidationError, "no HSN"):
            calculate_tax(Cart((line("frame", 100, None),)), RATES, as_of=date(2026, 11, 2))
        with self.assertRaisesRegex(TaxValidationError, "No effective GST rate"):
            calculate_tax(
                Cart((line("frame", 100, "UNKNOWN"),)),
                RATES,
                as_of=date(2026, 11, 2),
            )

    def test_discount_and_tax_totals_reconcile_without_money_creation(self):
        result = calculate_tax(
            Cart((line("a", 999, "90031900"), line("b", 777, "90015000"))),
            RATES,
            as_of=date(2026, 11, 2),
            discounts=(
                DiscountAllocation("OFFER", "a", 99),
                DiscountAllocation("OTHER", "b", 111),
            ),
        )
        for item in result.lines:
            self.assertEqual(
                item.taxable_paise + item.cgst_paise + item.sgst_paise,
                item.discounted_gross_paise,
            )
            self.assertEqual(item.cgst_paise + item.sgst_paise, item.gst_paise)
        self.assertEqual(result.total_paise, result.gross_paise - result.discount_paise)
