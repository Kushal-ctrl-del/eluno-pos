"""GST calculation rules for GST-inclusive cart prices."""

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from app.domain.cart import Cart


class TaxValidationError(ValueError):
    """Raised when a cart cannot be taxed from the supplied tax data."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TaxRate:
    hsn: str
    rate_bps: int
    effective_from: date
    effective_to: Optional[date] = None

    def __post_init__(self) -> None:
        if not self.hsn:
            raise ValueError("hsn is required")
        if self.rate_bps < 0:
            raise ValueError("rate_bps cannot be negative")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot precede effective_from")


@dataclass(frozen=True)
class TaxLine:
    line_id: str
    hsn: str
    rate_bps: int
    gross_paise: int
    discount_paise: int
    discounted_gross_paise: int
    taxable_paise: int
    gst_paise: int
    cgst_paise: int
    sgst_paise: int


@dataclass(frozen=True)
class TaxCalculation:
    lines: Tuple[TaxLine, ...]

    @property
    def gross_paise(self) -> int:
        return sum(line.gross_paise for line in self.lines)

    @property
    def discount_paise(self) -> int:
        return sum(line.discount_paise for line in self.lines)

    @property
    def taxable_paise(self) -> int:
        return sum(line.taxable_paise for line in self.lines)

    @property
    def gst_paise(self) -> int:
        return sum(line.gst_paise for line in self.lines)

    @property
    def cgst_paise(self) -> int:
        return sum(line.cgst_paise for line in self.lines)

    @property
    def sgst_paise(self) -> int:
        return sum(line.sgst_paise for line in self.lines)

    @property
    def total_paise(self) -> int:
        return self.taxable_paise + self.gst_paise


def parse_tax_rate(raw: dict) -> TaxRate:
    try:
        return TaxRate(
            hsn=str(raw["hsn"]),
            rate_bps=int(raw["rate_bps"]),
            effective_from=date.fromisoformat(str(raw["effective_from"])),
            effective_to=(
                date.fromisoformat(str(raw["effective_to"]))
                if raw.get("effective_to") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Malformed tax rate definition.") from error


def calculate_tax(
    cart: Cart,
    tax_rates: Iterable[TaxRate],
    *,
    as_of: date,
    discounts: Iterable[object] = (),
) -> TaxCalculation:
    """Back GST out of discounted inclusive prices, line by line."""
    rates_by_hsn: Dict[str, List[TaxRate]] = {}
    for rate in tax_rates:
        rates_by_hsn.setdefault(rate.hsn, []).append(rate)

    lines_by_id = {line.line_id: line for line in cart.lines}
    if len(lines_by_id) != len(cart.lines):
        raise TaxValidationError("duplicate_line_id", "Cart contains duplicate line IDs.")
    discounts_by_line: Dict[str, int] = {line_id: 0 for line_id in lines_by_id}
    for allocation in discounts:
        try:
            line_id = str(allocation.line_id)
            amount = int(allocation.amount_paise)
        except (AttributeError, TypeError, ValueError) as error:
            raise TaxValidationError(
                "malformed_discount", "Discount allocation is malformed."
            ) from error
        if line_id not in lines_by_id:
            raise TaxValidationError(
                "unknown_discount_line",
                f"Discount allocation references unknown line {line_id}.",
            )
        if amount < 0:
            raise TaxValidationError("negative_discount", "Discount amounts cannot be negative.")
        discounts_by_line[line_id] += amount

    result = []
    for line in cart.lines:
        if not line.hsn:
            raise TaxValidationError("hsn_missing", f"Cart line {line.line_id} has no HSN.")
        rate = _rate_for(line.hsn, rates_by_hsn, as_of)
        discount = discounts_by_line[line.line_id]
        gross = line.gross_paise
        if discount > gross:
            raise TaxValidationError(
                "discount_exceeds_line",
                f"Discount for line {line.line_id} exceeds its gross price.",
            )
        discounted = gross - discount
        taxable = _round_half_up(discounted * 10000, 10000 + rate.rate_bps)
        gst = discounted - taxable
        cgst = _round_half_up(gst, 2)
        sgst = gst - cgst
        if taxable + cgst + sgst != discounted:
            raise TaxValidationError(
                "tax_reconciliation_failed",
                f"Tax for line {line.line_id} does not reconcile to the discounted price.",
            )
        result.append(TaxLine(
            line_id=line.line_id,
            hsn=line.hsn,
            rate_bps=rate.rate_bps,
            gross_paise=gross,
            discount_paise=discount,
            discounted_gross_paise=discounted,
            taxable_paise=taxable,
            gst_paise=gst,
            cgst_paise=cgst,
            sgst_paise=sgst,
        ))
    return TaxCalculation(tuple(result))


def _rate_for(hsn: str, rates: Dict[str, List[TaxRate]], as_of: date) -> TaxRate:
    candidates = [
        rate for rate in rates.get(hsn, ())
        if rate.effective_from <= as_of
        and (rate.effective_to is None or as_of <= rate.effective_to)
    ]
    if not candidates:
        raise TaxValidationError(
            "tax_rate_unsupported",
            f"No effective GST rate is available for HSN {hsn} on {as_of.isoformat()}.",
        )
    latest_date = max(rate.effective_from for rate in candidates)
    latest = [rate for rate in candidates if rate.effective_from == latest_date]
    if len(latest) != 1:
        raise TaxValidationError(
            "duplicate_tax_rate",
            f"Multiple effective GST rates supplied for HSN {hsn} on {as_of.isoformat()}.",
        )
    return latest[0]


def _round_half_up(numerator: int, denominator: int) -> int:
    if denominator <= 0 or numerator < 0:
        raise ValueError("rounding inputs must be non-negative with a positive denominator")
    return (numerator * 2 + denominator) // (denominator * 2)
