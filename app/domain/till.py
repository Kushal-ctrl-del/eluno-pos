"""POS-owned cash till rules."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


MOVEMENT_TYPES = ("cash_sale", "cash_refund", "paid_in", "paid_out")


class TillError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TillMovement:
    movement_type: str
    amount_paise: int
    reference: str
    reason: Optional[str] = None

    @property
    def cash_delta_paise(self) -> int:
        if self.movement_type in ("cash_refund", "paid_out"):
            return -self.amount_paise
        return self.amount_paise

    def __post_init__(self) -> None:
        if self.movement_type not in MOVEMENT_TYPES:
            raise TillError("movement_type_invalid", "Unsupported till movement type.")
        if self.amount_paise <= 0:
            raise TillError("amount_invalid", "Till movement amount must be positive.")
        if not self.reference:
            raise TillError("reference_required", "Till movement reference is required.")
        if self.movement_type in ("cash_refund", "paid_in", "paid_out") and not self.reason:
            raise TillError("reason_required", "A reason is required for this movement.")


@dataclass(frozen=True)
class TillClose:
    session_id: str
    expected_cash_paise: int
    counted_cash_paise: int
    variance_paise: int
    variance_flagged: bool


def expected_cash(opening_float_paise: int, movements: List[TillMovement]) -> int:
    if opening_float_paise < 0:
        raise TillError("opening_float_invalid", "Opening float cannot be negative.")
    return opening_float_paise + sum(item.cash_delta_paise for item in movements)


def close_result(
    session_id: str,
    opening_float_paise: int,
    movements: List[TillMovement],
    counted_cash_paise: int,
) -> TillClose:
    if counted_cash_paise < 0:
        raise TillError("counted_cash_invalid", "Counted cash cannot be negative.")
    expected = expected_cash(opening_float_paise, movements)
    variance = counted_cash_paise - expected
    return TillClose(
        session_id=session_id,
        expected_cash_paise=expected,
        counted_cash_paise=counted_cash_paise,
        variance_paise=variance,
        variance_flagged=abs(variance) > 20000,
    )
