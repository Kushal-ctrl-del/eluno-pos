from datetime import date
import unittest

from app.application.customer_prescription import CustomerPrescriptionService
from app.domain.customer import Customer
from app.domain.prescription import (
    EyePrescription,
    LensLimits,
    LensSelection,
    LensVariant,
    Prescription,
    PrescriptionWorkflowError,
    select_latest_valid,
    validate_lens,
)


def prescription(rx_id: str, valid_until: date, *, axis=None, add=2.0) -> Prescription:
    eye = EyePrescription(sph=-3.25, cyl=-0.5, axis=axis, add=add)
    return Prescription(rx_id, 10, "+910", date(2026, 1, 1), valid_until, "external", eye, eye)


class PrescriptionRulesTests(unittest.TestCase):
    def test_selects_latest_valid_and_preserves_external_axis(self) -> None:
        selected = select_latest_valid(
            [
                prescription("old", date(2026, 1, 1), axis=87),
                prescription("new", date(2027, 1, 1), axis=92),
            ],
            date(2026, 11, 2),
        )
        self.assertEqual(selected.rx_id, "new")
        self.assertEqual(selected.od.axis, 92)
        self.assertEqual(selected.os.axis, 92)

    def test_missing_and_expired_prescriptions_are_structured(self) -> None:
        with self.assertRaises(PrescriptionWorkflowError) as missing:
            select_latest_valid([], date(2026, 11, 2))
        self.assertEqual(missing.exception.code, "prescription_missing")

        with self.assertRaises(PrescriptionWorkflowError) as expired:
            select_latest_valid([prescription("old", date(2026, 11, 1))], date(2026, 11, 2))
        self.assertEqual(expired.exception.code, "prescription_expired")

    def test_lens_validation_reports_actionable_issues(self) -> None:
        lens = LensVariant(
            1,
            "LN-TEST",
            "progressive",
            1.56,
            "standard",
            LensLimits(-2.0, 2.0, 0.25, True, 1.0, 2.0),
        )
        issues = validate_lens(
            lens,
            LensSelection("progressive", 1.56, "standard"),
            prescription("rx", date(2027, 1, 1), axis=87, add=None),
        )
        codes = {issue.code for issue in issues}
        self.assertIn("sphere_out_of_range", codes)
        self.assertIn("cylinder_out_of_range", codes)
        self.assertIn("add_required", codes)
        self.assertEqual(
            next(issue for issue in issues if issue.code == "sphere_out_of_range").eye,
            "od",
        )


class CustomerPrescriptionServiceTests(unittest.TestCase):
    def test_malformed_store_response_is_not_silently_ignored(self) -> None:
        class Customers:
            def find_by_phone(self, phone):
                return Customer(10, phone, (), 0)

        class Prescriptions:
            def find_by_phone(self, phone):
                raise ValueError("malformed")

        class Catalog:
            def get_lens_variant(self, variant_id):
                raise AssertionError("not reached")

        service = CustomerPrescriptionService(Customers(), Prescriptions(), Catalog())
        with self.assertRaises(PrescriptionWorkflowError) as raised:
            service.load_customer_with_latest_prescription("+910")
        self.assertEqual(raised.exception.code, "prescription_malformed")
