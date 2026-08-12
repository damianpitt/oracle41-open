from __future__ import annotations

import json

import pytest

from oracle41_open.core.models import Chain, ProviderRateLimitError
from oracle41_open.providers.blockscout import BlockscoutABIProvider
from oracle41_open.providers.http_client import HTTPRequest, HTTPResponse

_ADDRESS = "0x1111111111111111111111111111111111111111"
_ABI = [{"type": "error", "name": "Denied", "inputs": []}]


def test_blockscout_provider_loads_attributed_verified_abi() -> None:
    client = _FakeHTTPClient(
        HTTPResponse(
            status_code=200,
            data=json.dumps(
                {"is_verified": True, "name": "Vault", "abi": _ABI}
            ).encode(),
            headers={},
        )
    )
    provider = BlockscoutABIProvider(
        {Chain.ETHEREUM: "https://explorer.example"},
        http_client=client,  # type: ignore[arg-type]
    )

    result = provider.fetch_verified_abi(Chain.ETHEREUM, _ADDRESS)

    assert result is not None
    assert json.loads(result.abi_json) == _ABI
    assert result.contract_name == "Vault"
    assert result.source_name == "Blockscout Ethereum"
    assert result.source_version == "api-v2"
    assert result.reference == f"https://explorer.example/address/{_ADDRESS}"
    assert client.requests[0].url.endswith(f"/api/v2/smart-contracts/{_ADDRESS}")


def test_blockscout_provider_accepts_string_abi_and_rejects_unverified_contract() -> None:
    verified = _FakeHTTPClient(
        HTTPResponse(
            status_code=200,
            data=json.dumps({"is_verified": True, "abi": json.dumps(_ABI)}).encode(),
            headers={},
        )
    )
    unverified = _FakeHTTPClient(
        HTTPResponse(
            status_code=200,
            data=b'{"is_verified":false,"abi":[]}',
            headers={},
        )
    )

    assert BlockscoutABIProvider(
        http_client=verified,  # type: ignore[arg-type]
    ).fetch_verified_abi(Chain.BASE, _ADDRESS) is not None
    assert BlockscoutABIProvider(
        http_client=unverified,  # type: ignore[arg-type]
    ).fetch_verified_abi(Chain.BASE, _ADDRESS) is None


def test_blockscout_provider_retries_rate_limits_without_leaking_url() -> None:
    client = _FakeHTTPClient(HTTPResponse(status_code=429, data=b"{}", headers={}))
    provider = BlockscoutABIProvider(
        {Chain.ETHEREUM: "https://explorer.example/private-key"},
        http_client=client,  # type: ignore[arg-type]
        retry_attempts=2,
        retry_initial_delay_seconds=0,
    )

    with pytest.raises(ProviderRateLimitError) as captured:
        provider.fetch_verified_abi(Chain.ETHEREUM, _ADDRESS)

    assert len(client.requests) == 2
    assert "private-key" not in str(captured.value)


class _FakeHTTPClient:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        return self.response
