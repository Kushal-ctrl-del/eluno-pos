"""Payment-terminal adapter for the documented mock contract."""

from typing import Any, Dict

from app.adapters.http_client import HttpClientError, JsonHttpClient
from app.config import Settings


class TerminalClient:
    def __init__(self, settings: Settings, http_client: JsonHttpClient) -> None:
        self._base_url = settings.terminal_base_url
        self._http = http_client

    def charge(self, amount_paise: int, method: str, order_ref: str) -> Dict[str, Any]:
        payload = {"amount_paise": amount_paise, "method": method, "order_ref": order_ref}
        try:
            return self._http.request_json(
                "POST", f"{self._base_url}/charges",
                payload=payload, expected_statuses=(201,),
            )
        except HttpClientError as error:
            if error.status_code != 503:
                raise
            return self._http.request_json(
                "POST", f"{self._base_url}/charges",
                payload=payload, expected_statuses=(201,),
            )

    def get_charge(self, charge_id: str) -> Dict[str, Any]:
        return self._http.get_json(f"{self._base_url}/charges/{charge_id}")

    def refund(self, charge_id: str, amount_paise: int, reason: str) -> Dict[str, Any]:
        return self._http.request_json(
            "POST",
            f"{self._base_url}/refunds",
            payload={
                "charge_id": charge_id,
                "amount_paise": amount_paise,
                "reason": reason,
            },
            expected_statuses=(201,),
        )
