"""Retrieve verified contract and transaction context from Blockscout API v2.

The provider loads attributed ABIs, readable contract details, and optional decoded transaction context.
Explorer data stays separate from canonical JSON-RPC evidence, and temporary failures are retried safely.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    EnrichmentStatus,
    ExplorerAddressContext,
    ExplorerCapabilities,
    ExplorerDecodedParameter,
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    TransactionEnrichment,
)
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.contract_abi_service import VerifiedABIResult
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
    HTTPResponse,
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
            for chain, endpoint in (
                endpoints if endpoints is not None else _DEFAULT_ENDPOINTS
            ).items()
            if endpoint.strip()
        }
        self._http_client = http_client or HTTPClient()
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def capabilities(self, chain: Chain) -> ExplorerCapabilities:
        configured = chain in self._endpoints
        return ExplorerCapabilities(
            transaction_context=configured,
            contract_context=configured,
        )

    def fetch_transaction_enrichment(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionEnrichment:
        normalized_hash = _require_hash(tx_hash)
        endpoint = self._endpoints.get(chain)
        fetched_at = datetime.now(tz=UTC)
        if endpoint is None:
            return _empty_enrichment(
                chain,
                normalized_hash,
                EnrichmentStatus.UNSUPPORTED,
                "Blockscout",
                None,
                fetched_at,
                f"Blockscout enrichment is not configured for {chain.display_name}.",
            )

        reference = f"{endpoint}/tx/{normalized_hash}"
        response = self._send(
            f"{endpoint}/api/v2/transactions/{normalized_hash}",
            "transaction enrichment",
        )
        if response.status_code == 404:
            return _empty_enrichment(
                chain,
                normalized_hash,
                EnrichmentStatus.NOT_FOUND,
                f"Blockscout {chain.display_name}",
                reference,
                fetched_at,
                "The transaction was not found by this Blockscout instance.",
            )
        _raise_for_status(response.status_code, "transaction enrichment")
        payload = _load_object(response.data, "transaction enrichment")

        target_context = _parse_address_context(payload.get("to"), endpoint)
        created_context = _parse_address_context(payload.get("created_contract"), endpoint)
        context_to_expand = created_context or target_context
        context_error: str | None = None
        if context_to_expand is not None and context_to_expand.is_contract is not False:
            try:
                context_to_expand = self._fetch_address_context(
                    chain,
                    context_to_expand.address,
                ) or context_to_expand
            except (
                ProviderNetworkError,
                ProviderRateLimitError,
                ProviderResponseError,
                ProviderTimeoutError,
            ) as error:
                context_error = f"Contract creation details were unavailable: {error}"
        if created_context is not None:
            created_context = context_to_expand
        elif target_context is not None:
            target_context = context_to_expand

        decoded = payload.get("decoded_input")
        decoded_object = decoded if isinstance(decoded, dict) else {}
        return TransactionEnrichment(
            chain=chain,
            tx_hash=normalized_hash,
            status=EnrichmentStatus.AVAILABLE,
            method_name=_optional_text(payload.get("method")),
            transaction_types=_text_tuple(payload.get("transaction_types")),
            decoded_method_call=_optional_text(decoded_object.get("method_call")),
            decoded_method_id=_optional_text(decoded_object.get("method_id")),
            decoded_parameters=_parse_decoded_parameters(decoded_object.get("parameters")),
            target_context=target_context,
            created_contract_context=created_context,
            source_name=f"Blockscout {chain.display_name}",
            source_version="api-v2",
            source_reference=reference,
            fetched_at=fetched_at,
            error=context_error,
        )

    def _fetch_address_context(
        self,
        chain: Chain,
        address: str,
    ) -> ExplorerAddressContext | None:
        endpoint = self._endpoints.get(chain)
        if endpoint is None:
            return None
        response = self._send(
            f"{endpoint}/api/v2/addresses/{address}",
            "contract context",
        )
        if response.status_code == 404:
            return None
        _raise_for_status(response.status_code, "contract context")
        return _parse_address_context(
            _load_object(response.data, "contract context"),
            endpoint,
        )

    def _send(self, url: str, operation_name: str) -> HTTPResponse:
        def operation() -> HTTPResponse:
            try:
                response = self._http_client.send(HTTPRequest(url=url))
            except HTTPClientTimeoutError as error:
                raise ProviderTimeoutError(f"Blockscout {operation_name} timed out.") from error
            except HTTPClientNetworkError as error:
                raise ProviderNetworkError(
                    f"Blockscout {operation_name} failed on the network."
                ) from error
            _raise_for_retryable_status(response.status_code, operation_name)
            return response

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


def _empty_enrichment(
    chain: Chain,
    tx_hash: str,
    status: EnrichmentStatus,
    source_name: str,
    source_reference: str | None,
    fetched_at: datetime,
    error: str,
) -> TransactionEnrichment:
    return TransactionEnrichment(
        chain=chain,
        tx_hash=tx_hash,
        status=status,
        method_name=None,
        transaction_types=(),
        decoded_method_call=None,
        decoded_method_id=None,
        decoded_parameters=(),
        target_context=None,
        created_contract_context=None,
        source_name=source_name,
        source_version="api-v2",
        source_reference=source_reference,
        fetched_at=fetched_at,
        error=error,
    )


def _parse_address_context(
    raw: object,
    endpoint: str,
) -> ExplorerAddressContext | None:
    if not isinstance(raw, dict):
        return None
    address = _optional_address(raw.get("hash"))
    if address is None:
        return None
    return ExplorerAddressContext(
        address=address,
        name=_optional_text(raw.get("name")),
        implementation_name=_optional_text(raw.get("implementation_name")),
        ens_name=_optional_text(raw.get("ens_domain_name")),
        is_contract=raw.get("is_contract") if isinstance(raw.get("is_contract"), bool) else None,
        is_verified=raw.get("is_verified") if isinstance(raw.get("is_verified"), bool) else None,
        creator_address=_optional_address(raw.get("creator_address_hash")),
        creation_tx_hash=_optional_hash(raw.get("creation_transaction_hash")),
        source_reference=f"{endpoint}/address/{address}",
    )


def _parse_decoded_parameters(raw: object) -> tuple[ExplorerDecodedParameter, ...]:
    if not isinstance(raw, list):
        return ()
    parameters: list[ExplorerDecodedParameter] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _optional_text(item.get("name")) or "argument"
        type_name = _optional_text(item.get("type")) or "unknown"
        value = item.get("value")
        if value is None:
            rendered_value = "null"
        elif isinstance(value, (dict, list)):
            rendered_value = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        else:
            rendered_value = str(value)
        indexed = item.get("indexed")
        if not isinstance(indexed, bool):
            indexed = item.get("indexed?")
        parameters.append(
            ExplorerDecodedParameter(
                name=name,
                type_name=type_name,
                value=rendered_value,
                indexed=indexed if isinstance(indexed, bool) else None,
            )
        )
    return tuple(parameters)


def _load_object(raw_payload: bytes, operation_name: str) -> dict[str, object]:
    try:
        payload = json_loads(raw_payload)
    except ValueError as error:
        raise ProviderResponseError(
            f"Blockscout returned invalid {operation_name} JSON."
        ) from error
    if not isinstance(payload, dict):
        raise ProviderResponseError(f"Blockscout returned invalid {operation_name} data.")
    return payload


def _raise_for_retryable_status(status_code: int, operation_name: str) -> None:
    if status_code == 429:
        raise ProviderRateLimitError(f"Blockscout rate-limited {operation_name}.")
    if status_code in {408, 504}:
        raise ProviderTimeoutError(f"Blockscout {operation_name} timed out.")


def _raise_for_status(status_code: int, operation_name: str) -> None:
    _raise_for_retryable_status(status_code, operation_name)
    if status_code in {401, 403}:
        raise ProviderAuthError(f"Blockscout {operation_name} was not authorized.")
    if status_code >= 400:
        raise ProviderResponseError(
            f"Blockscout {operation_name} returned HTTP {status_code}."
        )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value if (text := _optional_text(item)) is not None)


def _optional_address(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if len(normalized) != 42 or not normalized.startswith("0x"):
        return None
    try:
        int(normalized[2:], 16)
    except ValueError:
        return None
    return normalized


def _optional_hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return _require_hash(value)
    except ProviderResponseError:
        return None


def _require_hash(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderResponseError("Blockscout transaction hash is missing.")
    normalized = value.strip().lower()
    if len(normalized) != 66 or not normalized.startswith("0x"):
        raise ProviderResponseError("Blockscout transaction hash is invalid.")
    try:
        int(normalized[2:], 16)
    except ValueError as error:
        raise ProviderResponseError("Blockscout transaction hash is invalid.") from error
    return normalized
