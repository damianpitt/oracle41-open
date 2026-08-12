"""Provide the shared synchronous HTTP transport.

The wrapper normalizes responses and converts timeout or network exceptions into URL-safe transport errors.
Provider modules remain responsible for interpreting HTTP status codes and payloads.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class HTTPClientError(RuntimeError):
    """Base transport error for HTTP client."""


class HTTPClientTimeoutError(HTTPClientError):
    """HTTP request timed out."""


class HTTPClientNetworkError(HTTPClientError):
    """HTTP request failed due to transport/network issue."""


@dataclass(frozen=True)
class HTTPRequest:
    url: str
    method: str = "GET"
    headers: dict[str, str] | None = None
    json: object | None = None


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    data: bytes
    headers: dict[str, str]


class HTTPClient:
    def __init__(self, timeout_seconds: float = 20.0, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def send(self, request: HTTPRequest) -> HTTPResponse:
        try:
            response = self._client.request(
                method=request.method,
                url=request.url,
                headers=request.headers,
                json=request.json,
            )
        except httpx.TimeoutException as error:
            raise HTTPClientTimeoutError(f"HTTP {request.method} request timed out.") from error
        except httpx.HTTPError as error:
            raise HTTPClientNetworkError(f"HTTP {request.method} request failed.") from error
        headers = {key.lower(): value for key, value in response.headers.items()}
        return HTTPResponse(
            status_code=response.status_code,
            data=response.content,
            headers=headers,
        )
