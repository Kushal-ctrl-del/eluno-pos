"""Store-management-tool adapter mapping prescription responses."""

from datetime import date
from typing import Any, List, Optional
from urllib.parse import quote

from app.adapters.http_client import JsonHttpClient
from app.config import Settings
from app.domain.prescription import EyePrescription, Prescription


class SmtClient:
    def __init__(self, settings: Settings, http_client: JsonHttpClient) -> None:
        self._base_url = settings.smt_base_url
        self._http = http_client

    def find_by_phone(self, phone: str) -> List[Prescription]:
        response = self._http.get_json(
            f"{self._base_url}/prescriptions?phone={quote(phone, safe='')}"
        )
        raw_items = response.get("prescriptions") if isinstance(response, dict) else None
        if not isinstance(raw_items, list):
            raise ValueError("SMT prescription response has no prescriptions list.")
        return [_map_prescription(raw) for raw in raw_items]


def _map_prescription(raw: Any) -> Prescription:
    try:
        return Prescription(
            rx_id=str(raw["rx_id"]),
            customer_id=_optional_int(raw.get("customer_id")),
            phone=str(raw["phone"]),
            tested_on=date.fromisoformat(str(raw["date"])),
            valid_until=date.fromisoformat(str(raw["valid_until"])),
            source=str(raw["source"]),
            od=_map_eye(raw["od"]),
            os=_map_eye(raw["os"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("SMT prescription response contains a malformed prescription.") from error


def _map_eye(raw: Any) -> EyePrescription:
    if not isinstance(raw, dict):
        raise ValueError("SMT eye prescription must be an object.")
    return EyePrescription(
        sph=float(raw["sph"]),
        cyl=float(raw["cyl"]),
        axis=None if raw.get("axis") is None else float(raw["axis"]),
        add=None if raw.get("add") is None else float(raw["add"]),
    )


def _optional_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)
