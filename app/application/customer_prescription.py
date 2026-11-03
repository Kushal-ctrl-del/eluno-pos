"""Application workflows for customer, prescription, and lens validation."""

from datetime import date
from typing import List, Optional, Tuple

from app.domain.customer import Customer
from app.domain.prescription import (
    LensSelection,
    LensVariant,
    Prescription,
    PrescriptionWorkflowError,
    ValidationIssue,
    select_latest_valid,
    validate_lens,
)
from app.ports.catalog_gateway import CatalogGateway
from app.ports.customer_gateway import CustomerGateway
from app.ports.prescription_gateway import PrescriptionGateway


class CustomerPrescriptionService:
    def __init__(
        self,
        customer_gateway: CustomerGateway,
        prescription_gateway: PrescriptionGateway,
        catalog_gateway: CatalogGateway,
        *,
        today: Optional[date] = None,
    ) -> None:
        self._customers = customer_gateway
        self._prescriptions = prescription_gateway
        self._catalog = catalog_gateway
        self._today = today

    def load_customer_with_latest_prescription(self, phone: str) -> Tuple[Customer, Prescription]:
        customer = self._customers.find_by_phone(phone)
        try:
            prescriptions = self._prescriptions.find_by_phone(phone)
        except ValueError as error:
            raise PrescriptionWorkflowError(
                "prescription_malformed",
                "The store tool returned a malformed prescription.",
            ) from error
        latest = select_latest_valid(prescriptions, self._today or date.today())
        if latest.customer_id is not None and latest.customer_id != customer.customer_id:
            raise PrescriptionWorkflowError(
                "prescription_customer_mismatch",
                "The latest prescription belongs to a different customer.",
                details={"rx_id": latest.rx_id},
            )
        return customer, latest

    def validate_lens_selection(
        self,
        prescription: Prescription,
        variant_id: int,
        selection: LensSelection,
    ) -> List[ValidationIssue]:
        lens = self._catalog.get_lens_variant(variant_id)
        return validate_lens(lens, selection, prescription)
