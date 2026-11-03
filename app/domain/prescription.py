"""Prescription selection and lens compatibility rules."""

from dataclasses import dataclass
from datetime import date
from typing import List, Optional


@dataclass(frozen=True)
class EyePrescription:
    sph: float
    cyl: float
    axis: Optional[float]
    add: Optional[float]


@dataclass(frozen=True)
class Prescription:
    rx_id: str
    customer_id: Optional[int]
    phone: str
    tested_on: date
    valid_until: date
    source: str
    od: EyePrescription
    os: EyePrescription


@dataclass(frozen=True)
class LensLimits:
    sph_min: float
    sph_max: float
    cyl_abs_max: float
    add_required: bool
    add_min: Optional[float]
    add_max: Optional[float]


@dataclass(frozen=True)
class LensVariant:
    variant_id: int
    sku: str
    lens_type: str
    index: float
    coating: str
    limits: LensLimits


@dataclass(frozen=True)
class LensSelection:
    lens_type: str
    index: float
    coating: str


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str
    eye: Optional[str] = None


class PrescriptionWorkflowError(Exception):
    """Structured failure selecting a prescription."""

    def __init__(self, code: str, message: str, *, details: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def select_latest_valid(
    prescriptions: List[Prescription],
    as_of: date,
) -> Prescription:
    if not prescriptions:
        raise PrescriptionWorkflowError(
            "prescription_missing",
            "No prescriptions were found for this customer.",
        )
    valid = [rx for rx in prescriptions if rx.valid_until >= as_of]
    if not valid:
        raise PrescriptionWorkflowError(
            "prescription_expired",
            "All prescriptions on file have expired.",
            details={"as_of": as_of.isoformat()},
        )
    return max(valid, key=lambda rx: rx.tested_on)


def validate_lens(
    lens: LensVariant,
    selection: LensSelection,
    prescription: Prescription,
) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if lens.lens_type != selection.lens_type:
        issues.append(ValidationIssue(
            "lens_type_mismatch",
            "lens_type",
            f"Selected variant is {lens.lens_type}, not {selection.lens_type}.",
        ))
    if lens.index != selection.index:
        issues.append(ValidationIssue(
            "lens_index_mismatch",
            "index",
            f"Selected variant has index {lens.index}, not {selection.index}.",
        ))
    if lens.coating != selection.coating:
        issues.append(ValidationIssue(
            "lens_coating_mismatch",
            "coating",
            f"Selected variant has coating {lens.coating}, not {selection.coating}.",
        ))

    for eye_name, eye in (("od", prescription.od), ("os", prescription.os)):
        if not lens.limits.sph_min <= eye.sph <= lens.limits.sph_max:
            issues.append(ValidationIssue(
                "sphere_out_of_range",
                "sph",
                f"Sphere {eye.sph} is outside {lens.limits.sph_min} to {lens.limits.sph_max}.",
                eye_name,
            ))
        if abs(eye.cyl) > lens.limits.cyl_abs_max:
            issues.append(ValidationIssue(
                "cylinder_out_of_range",
                "cyl",
                f"Cylinder {eye.cyl} exceeds the absolute limit {lens.limits.cyl_abs_max}.",
                eye_name,
            ))
        if lens.limits.add_required and eye.add is None:
            issues.append(ValidationIssue(
                "add_required",
                "add",
                f"ADD is required for {lens.lens_type} lenses.",
                eye_name,
            ))
        if eye.add is not None and lens.limits.add_min is not None and eye.add < lens.limits.add_min:
            issues.append(ValidationIssue(
                "add_out_of_range",
                "add",
                f"ADD {eye.add} is below the minimum {lens.limits.add_min}.",
                eye_name,
            ))
        if eye.add is not None and lens.limits.add_max is not None and eye.add > lens.limits.add_max:
            issues.append(ValidationIssue(
                "add_out_of_range",
                "add",
                f"ADD {eye.add} is above the maximum {lens.limits.add_max}.",
                eye_name,
            ))
    return issues
