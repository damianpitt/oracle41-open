"""Test verified ABI retrieval from Blockscout.

The cases cover verified and unverified responses, string ABIs, attribution, retries, and safe errors.
No request reaches the public Blockscout service.
"""

from __future__ import annotations

import json

import pytest

from oracle41_open.core.models import Chain, EnrichmentStatus, ProviderRateLimitError
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


def test_blockscout_provider_loads_transaction_and_creation_context() -> None:
    transaction = {
        "method": "execute",
        "transaction_types": ["contract_call", "token_transfer"],
        "to": {
            "hash": _ADDRESS,
            "name": "Proxy",
            "is_contract": True,
            "is_verified": True,
        },
        "decoded_input": {
            "method_call": "execute(uint256 amount)",
            "method_id": "0x12345678",
            "parameters": [{"name": "amount", "type": "uint256", "value": "42"}],
        },
    }
    address = {
        "hash": _ADDRESS,
        "name": "Vault",
        "implementation_name": "VaultV2",
        "is_contract": True,
        "is_verified": True,
        "creator_address_hash": "0x" + "22" * 20,
        "creation_transaction_hash": "0x" + "33" * 32,
    }
    client = _FakeHTTPClient(
        HTTPResponse(200, json.dumps(transaction).encode(), {}),
        HTTPResponse(200, json.dumps(address).encode(), {}),
    )
    provider = BlockscoutABIProvider(
        {Chain.ETHEREUM: "https://explorer.example"},
        http_client=client,  # type: ignore[arg-type]
    )

    result = provider.fetch_transaction_enrichment(Chain.ETHEREUM, "0x" + "ab" * 32)

    assert result.status is EnrichmentStatus.AVAILABLE
    assert result.method_name == "execute"
    assert result.decoded_method_call == "execute(uint256 amount)"
    assert result.decoded_parameters[0].value == "42"
    assert result.target_context is not None
    assert result.target_context.name == "Vault"
    assert result.target_context.implementation_name == "VaultV2"
    assert result.target_context.is_verified
    assert result.target_context.creation_tx_hash == "0x" + "33" * 32
    assert result.source_reference == "https://explorer.example/tx/" + "0x" + "ab" * 32
    assert len(client.requests) == 2


def test_blockscout_enrichment_reports_not_found_and_unsupported() -> None:
    missing_provider = BlockscoutABIProvider(
        {Chain.ETHEREUM: "https://explorer.example"},
        http_client=_FakeHTTPClient(HTTPResponse(404, b"{}", {})),  # type: ignore[arg-type]
    )
    unsupported_provider = BlockscoutABIProvider({})

    missing = missing_provider.fetch_transaction_enrichment(
        Chain.ETHEREUM,
        "0x" + "ab" * 32,
    )
    unsupported = unsupported_provider.fetch_transaction_enrichment(
        Chain.BASE,
        "0x" + "ab" * 32,
    )

    assert missing.status is EnrichmentStatus.NOT_FOUND
    assert unsupported.status is EnrichmentStatus.UNSUPPORTED
    assert not unsupported_provider.capabilities(Chain.BASE).transaction_context


class _FakeHTTPClient:
    def __init__(self, *responses: HTTPResponse) -> None:
        self.responses = responses
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return self.responses[index]
