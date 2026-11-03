"""Customer value objects used by application workflows."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Customer:
    customer_id: int
    phone: str
    tags: Tuple[str, ...]
    total_spent_paise: int
