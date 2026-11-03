"""Read-only connectivity probes for the three mock services."""

from dataclasses import dataclass
from typing import Callable, List, Tuple

from app.adapters.http_client import HttpClientError, JsonHttpClient
from app.config import Settings


@dataclass(frozen=True)
class HealthResult:
    service: str
    ok: bool
    detail: str


def check_services(settings: Settings, client: JsonHttpClient) -> List[HealthResult]:
    probes: List[Tuple[str, Callable[[], None]]] = [
        ("Shopify", lambda: client.get_json(f"{settings.shopify_base_url}/locations.json")),
        ("SMT", lambda: client.get_json(f"{settings.smt_base_url}/staff?store=HSR")),
        ("Terminal", lambda: _check_terminal(client, settings)),
    ]
    results = []
    for service, probe in probes:
        try:
            probe()
            results.append(HealthResult(service, True, "reachable"))
        except HttpClientError as error:
            results.append(HealthResult(service, False, str(error)))
    return results


def _check_terminal(client: JsonHttpClient, settings: Settings) -> None:
    try:
        client.get_json(f"{settings.terminal_base_url}/charges/health-check")
    except HttpClientError as error:
        if error.status_code == 404:
            return
        raise
