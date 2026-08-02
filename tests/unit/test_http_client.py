import httpx
import pytest

from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPRequest,
)


def test_network_errors_do_not_expose_request_url() -> None:
    secret_url = "https://provider.example/v2/sensitive-api-key"

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection failed for {request.url}", request=request)

    transport = httpx.MockTransport(fail)
    client = HTTPClient(client=httpx.Client(transport=transport))

    with pytest.raises(HTTPClientNetworkError) as captured:
        client.send(HTTPRequest(url=secret_url))

    assert "sensitive-api-key" not in str(captured.value)
    assert secret_url not in str(captured.value)
