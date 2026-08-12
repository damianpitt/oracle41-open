from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oracle41_open._json import loads as json_loads
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
)


class JSONRPCClientError(RuntimeError):
    """Base JSON-RPC client error."""


class JSONRPCTimeoutError(JSONRPCClientError):
    """JSON-RPC request timed out."""


class JSONRPCNetworkError(JSONRPCClientError):
    """JSON-RPC request hit network/transport issues."""


class JSONRPCHTTPError(JSONRPCClientError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class JSONRPCRemoteError(JSONRPCClientError):
    def __init__(
        self,
        message: str,
        code: int | None = None,
        data: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class JSONRPCPayloadError(JSONRPCClientError):
    """JSON-RPC response payload is malformed."""


@dataclass
class JSONRPCClient:
    http_client: HTTPClient

    def call(self, url: str, method: str, params: list[Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or [],
        }
        try:
            response = self.http_client.send(
                HTTPRequest(
                    url=url,
                    method="POST",
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
        except HTTPClientTimeoutError as error:
            raise JSONRPCTimeoutError(str(error)) from error
        except HTTPClientNetworkError as error:
            raise JSONRPCNetworkError(str(error)) from error
        if response.status_code >= 400:
            raise JSONRPCHTTPError(
                response.status_code,
                f"JSON-RPC call failed with HTTP {response.status_code}",
            )
        try:
            decoded = json_loads(response.data)
        except ValueError as error:
            raise JSONRPCPayloadError("Invalid JSON-RPC response payload.") from error
        if not isinstance(decoded, dict):
            raise JSONRPCPayloadError("Invalid JSON-RPC response payload.")
        if "error" in decoded:
            rpc_error = decoded.get("error")
            code: int | None = None
            message = "Unknown JSON-RPC error"
            if isinstance(rpc_error, dict):
                raw_code = rpc_error.get("code")
                if isinstance(raw_code, int):
                    code = raw_code
                raw_message = rpc_error.get("message")
                if isinstance(raw_message, str) and raw_message.strip():
                    message = raw_message.strip()
                error_data = rpc_error.get("data")
            else:
                message = str(rpc_error)
                error_data = None
            raise JSONRPCRemoteError(message=message, code=code, data=error_data)
        return decoded.get("result")
