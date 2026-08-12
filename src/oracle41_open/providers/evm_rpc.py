from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

from oracle41_open.core.models import (
    Chain,
    ProviderAuthError,
    ProviderCapabilities,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProxyKind,
    ProxyResolution,
    ProxyResolutionStatus,
    RawTransactionLog,
    TransactionInspection,
)
from oracle41_open.providers.http_client import HTTPClient
from oracle41_open.providers.jsonrpc import (
    JSONRPCClient,
    JSONRPCClientError,
    JSONRPCHTTPError,
    JSONRPCNetworkError,
    JSONRPCPayloadError,
    JSONRPCRemoteError,
    JSONRPCTimeoutError,
)
from oracle41_open.providers.retry import retry_with_backoff

_EIP_1967_IMPLEMENTATION_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)
_EIP_1167_PATTERN = re.compile(
    r"363d3d373d3d3d363d73([0-9a-f]{40})5af43d82803e903d91602b57fd5bf3"
)

_Result = TypeVar("_Result")


class EVMJSONRPCProvider:
    def __init__(
        self,
        endpoints: Mapping[Chain, str],
        source_name: str = "json-rpc",
        rpc_client: JSONRPCClient | None = None,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 0.25,
        retry_backoff_multiplier: float = 2.0,
        retry_max_delay_seconds: float = 2.0,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        self._endpoints = {
            chain: endpoint.strip()
            for chain, endpoint in endpoints.items()
            if endpoint.strip()
        }
        self._source_name = source_name.strip() or "json-rpc"
        self._rpc_client = rpc_client or JSONRPCClient(http_client=HTTPClient())
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        configured = chain in self._endpoints
        return ProviderCapabilities(
            transaction_lookup=configured,
            receipts=configured,
            traces=None,
            archive_queries=None,
            proxy_resolution=configured,
            revert_replay=configured,
        )

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        normalized_hash = _require_hash(tx_hash)
        endpoint = self._endpoints.get(chain)
        if endpoint is None:
            raise ProviderResponseError(
                f"{self._source_name} has no configured endpoint for {chain.display_name}."
            )

        raw_transaction = self._rpc_call(
            endpoint,
            "eth_getTransactionByHash",
            [normalized_hash],
        )
        raw_receipt = self._rpc_call(
            endpoint,
            "eth_getTransactionReceipt",
            [normalized_hash],
        )
        if raw_transaction is None or raw_receipt is None:
            raise ProviderResponseError(
                "Transaction or receipt is unavailable; it may be pending or unknown to the endpoint."
            )
        if not isinstance(raw_transaction, dict) or not isinstance(raw_receipt, dict):
            raise ProviderResponseError("JSON-RPC returned an invalid transaction receipt payload.")
        return self._map_inspection(chain, normalized_hash, raw_transaction, raw_receipt)

    def resolve_proxy(
        self,
        contract_address: str,
        chain: Chain,
        block_number: int,
    ) -> ProxyResolution:
        address = _require_address(contract_address, "contract")
        endpoint = self._endpoint_for(chain)
        block_tag = hex(block_number)
        raw_code = self._rpc_call(endpoint, "eth_getCode", [address, block_tag])
        code = _require_hex_data(raw_code, "contract code")
        minimal_match = _EIP_1167_PATTERN.search(code.removeprefix("0x"))
        if minimal_match is not None:
            return self._proxy_resolution(
                chain,
                address,
                block_number,
                ProxyKind.EIP_1167,
                "0x" + minimal_match.group(1),
            )

        raw_slot = self._rpc_call(
            endpoint,
            "eth_getStorageAt",
            [address, _EIP_1967_IMPLEMENTATION_SLOT, block_tag],
        )
        slot = _require_fixed_hex_data(raw_slot, "EIP-1967 implementation slot", 32)
        implementation = "0x" + slot[-40:]
        if implementation != "0x" + "00" * 20:
            return self._proxy_resolution(
                chain,
                address,
                block_number,
                ProxyKind.EIP_1967,
                implementation,
            )
        return ProxyResolution(
            chain=chain,
            proxy_address=address,
            status=ProxyResolutionStatus.NOT_PROXY,
            proxy_kind=ProxyKind.NONE,
            implementation_address=None,
            block_number=block_number,
            source_provider=self._source_name,
            resolved_at=datetime.now(tz=UTC),
        )

    def get_revert_data(self, inspection: TransactionInspection) -> str | None:
        if inspection.status is not False or inspection.to_address is None:
            return None
        endpoint = self._endpoint_for(inspection.chain)
        call = {
            "from": inspection.from_address,
            "to": inspection.to_address,
            "data": inspection.input_data,
            "value": hex(inspection.value_wei),
            "gas": hex(inspection.gas_limit),
        }
        try:
            self._rpc_call(endpoint, "eth_call", [call, hex(inspection.block_number)])
        except ProviderResponseError as error:
            cause = error.__cause__
            if isinstance(cause, JSONRPCRemoteError):
                return _extract_revert_data(cause.data)
            raise
        return None

    def _endpoint_for(self, chain: Chain) -> str:
        endpoint = self._endpoints.get(chain)
        if endpoint is None:
            raise ProviderResponseError(
                f"{self._source_name} has no configured endpoint for {chain.display_name}."
            )
        return endpoint

    def _proxy_resolution(
        self,
        chain: Chain,
        proxy_address: str,
        block_number: int,
        proxy_kind: ProxyKind,
        implementation_address: str,
    ) -> ProxyResolution:
        return ProxyResolution(
            chain=chain,
            proxy_address=proxy_address,
            status=ProxyResolutionStatus.RESOLVED,
            proxy_kind=proxy_kind,
            implementation_address=_require_address(implementation_address, "implementation"),
            block_number=block_number,
            source_provider=self._source_name,
            resolved_at=datetime.now(tz=UTC),
        )

    def _map_inspection(
        self,
        chain: Chain,
        tx_hash: str,
        transaction: dict[str, Any],
        receipt: dict[str, Any],
    ) -> TransactionInspection:
        receipt_hash = _require_hash(receipt.get("transactionHash"))
        transaction_hash = _require_hash(transaction.get("hash"))
        if receipt_hash != tx_hash or transaction_hash != tx_hash:
            raise ProviderResponseError("JSON-RPC transaction hash does not match the request.")

        raw_logs = receipt.get("logs")
        if not isinstance(raw_logs, list):
            raise ProviderResponseError("JSON-RPC receipt logs are missing or invalid.")
        logs = tuple(_map_log(raw_log) for raw_log in raw_logs)
        status = _optional_hex_int(receipt.get("status"), "status")
        if status not in {None, 0, 1}:
            raise ProviderResponseError("JSON-RPC receipt status is invalid.")

        return TransactionInspection(
            chain=chain,
            tx_hash=tx_hash,
            block_number=_required_hex_int(receipt.get("blockNumber"), "block number"),
            block_hash=_require_hash(receipt.get("blockHash")),
            transaction_index=_required_hex_int(
                receipt.get("transactionIndex"), "transaction index"
            ),
            from_address=_require_address(transaction.get("from"), "sender"),
            to_address=_optional_address(transaction.get("to"), "recipient"),
            contract_address=_optional_address(receipt.get("contractAddress"), "contract"),
            nonce=_required_hex_int(transaction.get("nonce"), "nonce"),
            value_wei=_required_hex_int(transaction.get("value"), "value"),
            input_data=_require_hex_data(transaction.get("input"), "input data"),
            gas_limit=_required_hex_int(transaction.get("gas"), "gas limit"),
            gas_price=_optional_hex_int(transaction.get("gasPrice"), "gas price"),
            max_fee_per_gas=_optional_hex_int(
                transaction.get("maxFeePerGas"), "maximum fee per gas"
            ),
            max_priority_fee_per_gas=_optional_hex_int(
                transaction.get("maxPriorityFeePerGas"), "maximum priority fee per gas"
            ),
            status=None if status is None else status == 1,
            gas_used=_required_hex_int(receipt.get("gasUsed"), "gas used"),
            cumulative_gas_used=_required_hex_int(
                receipt.get("cumulativeGasUsed"), "cumulative gas used"
            ),
            effective_gas_price=_required_hex_int(
                receipt.get("effectiveGasPrice"), "effective gas price"
            ),
            transaction_type=_optional_hex_int(receipt.get("type"), "transaction type"),
            logs_bloom=_require_hex_data(receipt.get("logsBloom"), "logs bloom"),
            logs=logs,
            source_provider=self._source_name,
            fetched_at=datetime.now(tz=UTC),
        )

    def _rpc_call(self, endpoint: str, method: str, params: list[Any]) -> Any:
        def operation() -> Any:
            try:
                return self._rpc_client.call(url=endpoint, method=method, params=params)
            except (ProviderError, JSONRPCClientError) as error:
                raise _map_rpc_error(self._source_name, method, error) from error

        return retry_with_backoff(
            operation=operation,
            should_retry=_is_retryable,
            attempts=self._retry_attempts,
            initial_delay_seconds=self._retry_initial_delay_seconds,
            backoff_multiplier=self._retry_backoff_multiplier,
            max_delay_seconds=self._retry_max_delay_seconds,
            sleep_func=self._sleep_func,
        )


class FailoverTransactionDataProvider:
    def __init__(self, providers: list[EVMJSONRPCProvider]) -> None:
        self._providers = list(providers)

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        available = [provider.capabilities(chain) for provider in self._providers]
        return ProviderCapabilities(
            transaction_lookup=any(item.transaction_lookup for item in available),
            receipts=any(item.receipts for item in available),
            traces=_merge_optional_capability(item.traces for item in available),
            archive_queries=_merge_optional_capability(
                item.archive_queries for item in available
            ),
            proxy_resolution=_merge_optional_capability(
                item.proxy_resolution for item in available
            ),
            revert_replay=_merge_optional_capability(item.revert_replay for item in available),
        )

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        errors: list[str] = []
        for provider in self._providers:
            if not provider.capabilities(chain).receipts:
                continue
            try:
                return provider.get_transaction_inspection(tx_hash, chain)
            except ProviderError as error:
                errors.append(str(error))
        if not errors:
            raise ProviderResponseError(
                f"No transaction receipt provider is configured for {chain.display_name}."
            )
        raise ProviderError("All transaction receipt providers failed: " + "; ".join(errors))

    def resolve_proxy(
        self,
        contract_address: str,
        chain: Chain,
        block_number: int,
    ) -> ProxyResolution:
        return self._first_success(
            chain,
            "proxy resolution",
            lambda provider: provider.resolve_proxy(contract_address, chain, block_number),
            capability="proxy_resolution",
        )

    def get_revert_data(self, inspection: TransactionInspection) -> str | None:
        if inspection.status is not False:
            return None
        errors: list[str] = []
        attempted = False
        successful_replay = False
        for provider in self._providers:
            if provider.capabilities(inspection.chain).revert_replay is not True:
                continue
            attempted = True
            try:
                revert_data = provider.get_revert_data(inspection)
            except ProviderError as error:
                errors.append(str(error))
                continue
            successful_replay = True
            if revert_data is not None:
                return revert_data
        if successful_replay:
            return None
        if not attempted:
            raise ProviderResponseError(
                f"No revert replay provider is configured for {inspection.chain.display_name}."
            )
        raise ProviderError("All revert replay providers failed: " + "; ".join(errors))

    def _first_success(
        self,
        chain: Chain,
        operation_name: str,
        operation: Callable[[EVMJSONRPCProvider], _Result],
        capability: str = "receipts",
    ) -> _Result:
        errors: list[str] = []
        for provider in self._providers:
            if getattr(provider.capabilities(chain), capability) is not True:
                continue
            try:
                return operation(provider)
            except ProviderError as error:
                errors.append(str(error))
        if not errors:
            raise ProviderResponseError(
                f"No {operation_name} provider is configured for {chain.display_name}."
            )
        raise ProviderError(f"All {operation_name} providers failed: " + "; ".join(errors))


def _map_log(raw_log: object) -> RawTransactionLog:
    if not isinstance(raw_log, dict):
        raise ProviderResponseError("JSON-RPC receipt contains an invalid log entry.")
    raw_topics = raw_log.get("topics")
    if not isinstance(raw_topics, list):
        raise ProviderResponseError("JSON-RPC receipt log topics are invalid.")
    topics = tuple(_require_hex_data(topic, "log topic") for topic in raw_topics)
    return RawTransactionLog(
        log_index=_required_hex_int(raw_log.get("logIndex"), "log index"),
        address=_require_address(raw_log.get("address"), "log address"),
        topics=topics,
        data=_require_hex_data(raw_log.get("data"), "log data"),
        removed=raw_log.get("removed") is True,
    )


def _required_hex_int(value: object, field: str) -> int:
    parsed = _optional_hex_int(value, field)
    if parsed is None:
        raise ProviderResponseError(f"JSON-RPC {field} is missing.")
    return parsed


def _optional_hex_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ProviderResponseError(f"JSON-RPC {field} is not hexadecimal.")
    try:
        return int(value, 16)
    except ValueError as error:
        raise ProviderResponseError(f"JSON-RPC {field} is not hexadecimal.") from error


def _require_hash(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderResponseError("JSON-RPC transaction hash is missing.")
    normalized = value.strip().lower()
    if len(normalized) != 66 or not normalized.startswith("0x"):
        raise ProviderResponseError("JSON-RPC transaction hash is invalid.")
    try:
        int(normalized[2:], 16)
    except ValueError as error:
        raise ProviderResponseError("JSON-RPC transaction hash is invalid.") from error
    return normalized


def _require_address(value: object, field: str) -> str:
    address = _optional_address(value, field)
    if address is None:
        raise ProviderResponseError(f"JSON-RPC {field} address is missing.")
    return address


def _optional_address(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderResponseError(f"JSON-RPC {field} address is invalid.")
    normalized = value.strip().lower()
    if len(normalized) != 42 or not normalized.startswith("0x"):
        raise ProviderResponseError(f"JSON-RPC {field} address is invalid.")
    try:
        int(normalized[2:], 16)
    except ValueError as error:
        raise ProviderResponseError(f"JSON-RPC {field} address is invalid.") from error
    return normalized


def _require_hex_data(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ProviderResponseError(f"JSON-RPC {field} is missing.")
    normalized = value.strip().lower()
    if not normalized.startswith("0x") or len(normalized) % 2 != 0:
        raise ProviderResponseError(f"JSON-RPC {field} is invalid hexadecimal data.")
    try:
        bytes.fromhex(normalized[2:])
    except ValueError as error:
        raise ProviderResponseError(f"JSON-RPC {field} is invalid hexadecimal data.") from error
    return normalized


def _require_fixed_hex_data(value: object, field: str, byte_length: int) -> str:
    normalized = _require_hex_data(value, field)
    if len(normalized) != 2 + byte_length * 2:
        raise ProviderResponseError(f"JSON-RPC {field} has an invalid length.")
    return normalized


def _extract_revert_data(value: object) -> str | None:
    if isinstance(value, str):
        try:
            normalized = _require_hex_data(value, "revert data")
        except ProviderResponseError:
            return None
        return normalized if len(normalized) >= 10 else None
    if isinstance(value, dict):
        for key in ("data", "result", "return"):
            nested = _extract_revert_data(value.get(key))
            if nested is not None:
                return nested
    return None


def _map_rpc_error(source_name: str, method: str, error: Exception) -> ProviderError:
    if isinstance(error, ProviderError):
        return error
    if isinstance(error, JSONRPCTimeoutError):
        return ProviderTimeoutError(f"{source_name} timed out for {method}.")
    if isinstance(error, JSONRPCNetworkError):
        return ProviderNetworkError(f"{source_name} network failure for {method}: {error}")
    if isinstance(error, JSONRPCHTTPError):
        if error.status_code in {408, 504}:
            return ProviderTimeoutError(f"{source_name} timed out for {method}.")
        if error.status_code in {401, 403}:
            return ProviderAuthError(f"{source_name} authentication failed.")
        if error.status_code == 429:
            return ProviderRateLimitError(f"{source_name} rate-limited the request.")
        return ProviderResponseError(f"{source_name} returned HTTP {error.status_code}.")
    if isinstance(error, JSONRPCRemoteError):
        lowered = str(error).lower()
        if error.code == -32005 or "rate limit" in lowered:
            return ProviderRateLimitError(f"{source_name} rate-limited {method}.")
        if "timeout" in lowered or "timed out" in lowered:
            return ProviderTimeoutError(f"{source_name} timed out for {method}.")
        if any(token in lowered for token in ("unauthorized", "forbidden", "api key")):
            return ProviderAuthError(f"{source_name} authentication failed for {method}.")
        return ProviderResponseError(f"{source_name} returned a remote error for {method}: {error}")
    if isinstance(error, JSONRPCPayloadError):
        return ProviderResponseError(f"{source_name} returned an invalid payload for {method}.")
    return ProviderResponseError(f"{source_name} failed for {method}: {error}")


def _is_retryable(error: Exception) -> bool:
    return isinstance(
        error,
        (ProviderRateLimitError, ProviderTimeoutError, ProviderNetworkError),
    )


def _merge_optional_capability(values: Iterable[bool | None]) -> bool | None:
    items = list(values)
    if any(value is True for value in items):
        return True
    if items and all(value is False for value in items):
        return False
    return None
