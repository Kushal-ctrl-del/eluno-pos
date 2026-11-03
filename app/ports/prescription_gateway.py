"""Prescription provider port."""

from typing import List, Protocol

from app.domain.prescription import Prescription


class PrescriptionGateway(Protocol):
    def find_by_phone(self, phone: str) -> List[Prescription]:
        ...
