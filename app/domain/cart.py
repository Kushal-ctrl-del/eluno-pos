"""Pure cart value objects for frame and lens pairs."""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CartLine:
    line_id: str
    line_type: str
    variant_id: int
    unit_price_paise: int
    quantity: int = 1
    pair_ref: Optional[str] = None
    lens_type: Optional[str] = None
    lens_index: Optional[float] = None
    coating: Optional[str] = None
    hsn: Optional[str] = None

    def __post_init__(self) -> None:
        if self.line_type not in ("frame", "lens"):
            raise ValueError("line_type must be frame or lens")
        if self.unit_price_paise < 0:
            raise ValueError("unit_price_paise cannot be negative")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.line_type == "lens" and self.pair_ref is None:
            raise ValueError("lens lines require pair_ref")

    @property
    def gross_paise(self) -> int:
        return self.unit_price_paise * self.quantity


@dataclass(frozen=True)
class Cart:
    lines: Tuple[CartLine, ...]

    @property
    def pair_refs(self) -> Tuple[str, ...]:
        return tuple(sorted({line.pair_ref for line in self.lines if line.pair_ref is not None}))

    @property
    def pair_count(self) -> int:
        return len(self.pair_refs)

    def line(self, line_id: str) -> CartLine:
        for line in self.lines:
            if line.line_id == line_id:
                return line
        raise KeyError(line_id)
