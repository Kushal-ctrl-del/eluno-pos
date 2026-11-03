"""Small JSON HTTP client used by external-system adapters."""

from dataclasses import dataclass
import json
import time
from typing import Any, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpClientError(Exception):
    """A clear, structured error for transport, HTTP, or JSON failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: str = "",
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class JsonHttpClient:
    timeout_seconds: float = 5.0

    def get_json(self, url: str) -> Any:
        for attempt in range(2):
            try:
                return self.request_json("GET", url)
            except HttpClientError as error:
                if error.status_code != 429 or attempt or error.retry_after_seconds is None:
                    raise
                time.sleep(error.retry_after_seconds)
        raise AssertionError("unreachable")

    def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        expected_statuses: Tuple[int, ...] = (200,),
    ) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status_code = response.status
                body = response.read().decode("utf-8")
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise HttpClientError(
                f"HTTP {error.code} from {method} {url}",
                status_code=error.code,
                body=body,
                retry_after_seconds=_retry_after(error.headers.get("Retry-After")),
            ) from error
        except URLError as error:
            raise HttpClientError(f"Unable to reach {method} {url}: {error.reason}") from error
        except TimeoutError as error:
            raise HttpClientError(f"Timed out calling {method} {url}") from error

        if status_code not in expected_statuses:
            raise HttpClientError(
                f"Unexpected HTTP {status_code} from {method} {url}",
                status_code=status_code,
                body=body,
            )
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise HttpClientError(
                f"Invalid JSON from {method} {url}",
                status_code=status_code,
                body=body,
            ) from error


def _retry_after(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
