from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.contract_abi_service import VerifiedABIResult
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
)
from oracle41_open.providers.retry import retry_with_backoff

_DEFAULT_ENDPOINTS = {
    Chain.ETHEREUM: "https://eth.blockscout.com",
    Chain.OPTIMISM: "https://optimism.blockscout.com",
    Chain.POLYGON: "https://polygon.blockscout.com",
    Chain.BASE: "https://base.blockscout.com",
    Chain.ARBITRUM: "https://arbitrum.blockscout.com",
}


class BlockscoutABIProvider:
    def __init__(
        self,
        endpoints: Mapping[Chain, str] | None = None,
        http_client: HTTPClient | None = None,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 0.25,
        retry_backoff_multiplier: float = 2.0,
        retry_max_delay_seconds: float = 2.0,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        self._endpoints = {
            chain: endpoint.rstrip("/")
            for chain, endpoint in (endpoints or _DEFAULT_ENDPOINTS).items()
            if endpoint.strip()
        }
        self._http_client = http_client or HTTPClient()
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def fetch_verified_abi(
        self,
        chain: Chain,
        contract_address: str,
    ) -> VerifiedABIResult | None:
        address = AddressValidator.normalized(contract_address)
        if not AddressValidator.is_valid(address):
            raise ProviderResponseError("Blockscout contract address is invalid.")
        endpoint = self._endpoints.get(chain)
        if endpoint is None:
            raise ProviderResponseError(
                f"Blockscout ABI lookup is not configured for {chain.display_name}."
            )
        reference = f"{endpoint}/address/{address}"
        api_url = f"{endpoint}/api/v2/smart-contracts/{address}"

        def operation() -> VerifiedABIResult | None:
            try:
                response = self._http_client.send(HTTPRequest(url=api_url))
            except HTTPClientTimeoutError as error:
                raise ProviderTimeoutError("Blockscout ABI lookup timed out.") from error
            except HTTPClientNetworkError as error:
                raise ProviderNetworkError("Blockscout ABI lookup failed on the network.") from error
            if response.status_code == 404:
                return None
            if response.status_code in {401, 403}:
                raise ProviderAuthError("Blockscout ABI lookup was not authorized.")
            if response.status_code == 429:
                raise ProviderRateLimitError("Blockscout rate-limited the ABI lookup.")
            if response.status_code in {408, 504}:
                raise ProviderTimeoutError("Blockscout ABI lookup timed out.")
            if response.status_code >= 400:
                raise ProviderResponseError(
                    f"Blockscout ABI lookup returned HTTP {response.status_code}."
                )
            return _parse_verified_contract(response.data, chain, reference)

        return retry_with_backoff(
            operation=operation,
            should_retry=lambda error: isinstance(
                error,
                (ProviderRateLimitError, ProviderTimeoutError, ProviderNetworkError),
            ),
            attempts=self._retry_attempts,
            initial_delay_seconds=self._retry_initial_delay_seconds,
            backoff_multiplier=self._retry_backoff_multiplier,
            max_delay_seconds=self._retry_max_delay_seconds,
            sleep_func=self._sleep_func,
        )


def _parse_verified_contract(
    raw_payload: bytes,
    chain: Chain,
    reference: str,
) -> VerifiedABIResult | None:
    try:
        payload = json_loads(raw_payload)
    except ValueError as error:
        raise ProviderResponseError("Blockscout returned invalid contract metadata.") from error
    if not isinstance(payload, dict):
        raise ProviderResponseError("Blockscout returned invalid contract metadata.")
    if payload.get("is_verified") is not True:
        return None
    raw_abi = payload.get("abi")
    if isinstance(raw_abi, str):
        try:
            raw_abi = json.loads(raw_abi)
        except json.JSONDecodeError as error:
            raise ProviderResponseError("Blockscout returned an invalid contract ABI.") from error
    if not isinstance(raw_abi, list):
        raise ProviderResponseError("Blockscout returned no usable contract ABI.")
    raw_name = payload.get("name")
    contract_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    return VerifiedABIResult(
        abi_json=json.dumps(raw_abi, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        contract_name=contract_name,
        source_name=f"Blockscout {chain.display_name}",
        source_version="api-v2",
        reference=reference,
    )
