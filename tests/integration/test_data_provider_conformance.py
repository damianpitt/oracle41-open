"""Run every available wallet-data adapter through one recorded-fixture contract.

Each provider fixture supplies its native response shapes for the same normalized wallet outcomes.
The shared assertions cover every DataProvider method, pagination markers, source provenance, chain identity, and NFT categories without using live credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from oracle41_open.core.models import Chain
from oracle41_open.providers.alchemy import AlchemyProvider
from oracle41_open.providers.ankr import AnkrProvider
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.goldrush import GoldRushProvider
from oracle41_open.providers.http_client import HTTPRequest, HTTPResponse
from oracle41_open.providers.moralis import MoralisProvider

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "conformance"
_FIXTURE_NAMES = (
    "alchemy_wallet_data_v1.json",
    "ankr_wallet_data_v1.json",
    "moralis_wallet_data_v1.json",
    "goldrush_wallet_data_v1.json",
)
_WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_TOKEN = "0x9999999999999999999999999999999999999999"


@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_provider_conformance_native_balance(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    provider, client = _provider_for(fixture, "native_balance")

    balance = provider.get_native_balance(_WALLET, Chain.ETHEREUM)

    assert balance == Decimal(fixture.expected_string("native_balance"))
    client.assert_consumed()


@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_provider_conformance_token_balances(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    provider, client = _provider_for(fixture, "token_balances")

    page = provider.get_token_balances(_WALLET, Chain.ETHEREUM)

    assert page.source_provider == fixture.provider_id
    has_next = fixture.expected_bool("token_balance_has_next")
    assert (page.next_page_key is not None) is has_next
    assert len(page.balances) == 1
    assert page.balances[0].token.symbol == fixture.expected_string(
        "token_balance_symbol"
    )
    assert page.balances[0].balance_decimal == Decimal(
        fixture.expected_string("token_balance")
    )
    client.assert_consumed()


@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_provider_conformance_wallet_activity(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    provider, client = _provider_for(fixture, "activity")

    page = provider.get_activity(
        _WALLET,
        Chain.ETHEREUM,
        from_block=100,
    )

    assert page.source_provider == fixture.provider_id
    assert page.query_from_block == 100
    assert isinstance(page.next_cursor, str)
    assert len(page.items) == 1
    assert page.items[0].tx_hash == fixture.expected_string("activity_hash")
    assert page.items[0].value_decimal == Decimal(
        fixture.expected_string("activity_value")
    )
    assert page.items[0].chain is Chain.ETHEREUM
    client.assert_consumed()


@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_provider_conformance_token_history_and_nfts(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    provider, client = _provider_for(fixture, "token_history")

    page = provider.get_token_transfers(
        _WALLET,
        _TOKEN,
        Chain.ETHEREUM,
        include_approvals=False,
    )

    assert page.source_provider == fixture.provider_id
    assert {
        item.category.value for item in page.items
    } == set(fixture.expected_strings("token_history_categories"))
    assert all(item.chain is Chain.ETHEREUM for item in page.items)
    assert all(item.contract_address == _TOKEN for item in page.items)
    client.assert_consumed()


@dataclass(frozen=True)
class _ConformanceFixture:
    provider_id: str
    operations: dict[str, Any]
    expected: dict[str, str | bool | list[str]]

    def expected_string(self, key: str) -> str:
        value = self.expected.get(key)
        if not isinstance(value, str):
            raise AssertionError(f"Fixture expected value must be a string: {key}.")
        return value

    def expected_strings(self, key: str) -> list[str]:
        value = self.expected.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise AssertionError(f"Fixture expected value must be a string list: {key}.")
        return value

    def expected_bool(self, key: str) -> bool:
        value = self.expected.get(key)
        if not isinstance(value, bool):
            raise AssertionError(f"Fixture expected value must be a boolean: {key}.")
        return value


class _RecordedProviderClient:
    """Return recorded RPC or REST results in the adapter's request order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)

    def call(
        self,
        url: str,
        method: str,
        params: list[Any] | None = None,
    ) -> Any:
        _ = url
        _ = params
        if not self._responses:
            raise AssertionError(f"No recorded response remains for {method}.")
        response = self._responses.pop(0)
        expected_method = response.get("method")
        if method != expected_method:
            raise AssertionError(f"Expected {expected_method}, received {method}.")
        return response.get("result")

    def send(self, request: HTTPRequest) -> HTTPResponse:
        path = urlparse(request.url).path
        if not self._responses:
            raise AssertionError(f"No recorded response remains for {path}.")
        response = self._responses.pop(0)
        expected_path = response.get("method")
        if path != expected_path:
            raise AssertionError(f"Expected {expected_path}, received {path}.")
        return HTTPResponse(
            status_code=200,
            data=json.dumps(response.get("result")).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    def assert_consumed(self) -> None:
        assert self._responses == []


def _load_fixture(file_name: str) -> _ConformanceFixture:
    raw = json.loads((_FIXTURE_ROOT / file_name).read_text(encoding="utf-8"))
    if raw.get("format") != "oracle41-data-provider-conformance":
        raise AssertionError("Unsupported provider conformance fixture format.")
    if raw.get("version") != 1:
        raise AssertionError("Unsupported provider conformance fixture version.")
    provider_id = raw.get("provider_id")
    operations = raw.get("operations")
    expected = raw.get("expected")
    if not isinstance(provider_id, str):
        raise AssertionError("Fixture provider_id must be a string.")
    if not isinstance(operations, dict):
        raise AssertionError("Fixture operations must be an object.")
    if not isinstance(expected, dict):
        raise AssertionError("Fixture expected values must be an object.")
    return _ConformanceFixture(
        provider_id=provider_id,
        operations=operations,
        expected=expected,
    )


def _provider_for(
    fixture: _ConformanceFixture,
    operation_name: str,
) -> tuple[DataProvider, _RecordedProviderClient]:
    operation = fixture.operations.get(operation_name)
    if not isinstance(operation, dict):
        raise AssertionError(f"Fixture operation is missing: {operation_name}.")
    responses = operation.get("responses")
    if not isinstance(responses, list) or not all(
        isinstance(response, dict) for response in responses
    ):
        raise AssertionError(f"Fixture responses are invalid: {operation_name}.")

    client = _RecordedProviderClient(responses)
    if fixture.provider_id == "alchemy":
        return AlchemyProvider(api_key="fixture-key", rpc_client=client), client
    if fixture.provider_id == "ankr":
        return AnkrProvider(api_key="fixture-key", rpc_client=client), client
    if fixture.provider_id == "moralis":
        return MoralisProvider(api_key="fixture-key", http_client=client), client
    if fixture.provider_id == "goldrush":
        return GoldRushProvider(api_key="fixture-key", http_client=client), client
    raise AssertionError(f"No provider factory exists for {fixture.provider_id}.")
