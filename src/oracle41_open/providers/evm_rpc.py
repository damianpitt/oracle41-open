"""Inspect transactions through EVM JSON-RPC endpoints.

The provider loads receipts and internal traces, discovers trace dialects, resolves common proxy forms, and replays reverted calls.
Endpoint failover uses structured errors without exposing endpoint secrets.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import (
    Chain,
    ContractReadResult,
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
    TraceDialect,
    TraceStatus,
    TransactionInspection,
    TransactionTrace,
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
from oracle41_open.providers.trace_mapper import map_debug_call_trace, map_parity_trace

_EIP_1967_IMPLEMENTATION_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)
_EIP_1967_BEACON_SLOT = (
    "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"
)
_BEACON_IMPLEMENTATION_CALL = "0x5c60da1b"
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
        self._trace_dialects: dict[Chain, TraceDialect | None] = {}
        self._historical_state_support: dict[Chain, bool] = {}

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        configured = chain in self._endpoints
        trace_support = (
            self._trace_dialects[chain] is not None
            if chain in self._trace_dialects
            else None
        )
        return ProviderCapabilities(
            transaction_lookup=configured,
            receipts=configured,
            traces=trace_support if configured else False,
            archive_queries=(
                self._historical_state_support.get(chain) if configured else False
            ),
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

    def read_contract(
        self,
        contract_address: str,
        call_data: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        """Run one read-only contract call against an explicit historical block."""
        address = _require_address(contract_address, "contract")
        data = _require_hex_data(call_data, "contract call data")
        if block_number < 0:
            raise ProviderResponseError("Contract read block number must not be negative.")
        endpoint = self._endpoint_for(chain)
        raw_result = self._historical_rpc_call(
            chain,
            endpoint,
            "eth_call",
            [{"to": address, "data": data}, hex(block_number)],
        )
        return ContractReadResult(
            chain=chain,
            contract_address=address,
            block_number=block_number,
            data=_require_hex_data(raw_result, "contract call result"),
            source_provider=self._source_name,
            fetched_at=datetime.now(tz=UTC),
        )

    def resolve_proxy(
        self,
        contract_address: str,
        chain: Chain,
        block_number: int,
    ) -> ProxyResolution:
        address = _require_address(contract_address, "contract")
        endpoint = self._endpoint_for(chain)
        block_tag = hex(block_number)
        try:
            raw_code = self._rpc_call(endpoint, "eth_getCode", [address, block_tag])
        except ProviderError as error:
            self._record_historical_state_failure(chain, error)
            raise
        self._historical_state_support[chain] = True
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

        raw_slot = self._historical_rpc_call(
            chain,
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

        raw_beacon_slot = self._historical_rpc_call(
            chain,
            endpoint,
            "eth_getStorageAt",
            [address, _EIP_1967_BEACON_SLOT, block_tag],
        )
        beacon_slot = _require_fixed_hex_data(
            raw_beacon_slot,
            "EIP-1967 beacon slot",
            32,
        )
        beacon_address = "0x" + beacon_slot[-40:]
        if beacon_address != "0x" + "00" * 20:
            try:
                raw_implementation = self._rpc_call(
                    endpoint,
                    "eth_call",
                    [{"to": beacon_address, "data": _BEACON_IMPLEMENTATION_CALL}, block_tag],
                )
                beacon_result = _require_fixed_hex_data(
                    raw_implementation,
                    "EIP-1967 beacon implementation",
                    32,
                )
            except ProviderError as error:
                self._record_historical_state_failure(chain, error)
                return ProxyResolution(
                    chain=chain,
                    proxy_address=address,
                    status=ProxyResolutionStatus.UNAVAILABLE,
                    proxy_kind=ProxyKind.EIP_1967_BEACON,
                    implementation_address=None,
                    block_number=block_number,
                    source_provider=self._source_name,
                    resolved_at=datetime.now(tz=UTC),
                    error=str(error),
                    beacon_address=beacon_address,
                )
            beacon_implementation = "0x" + beacon_result[-40:]
            if beacon_implementation == "0x" + "00" * 20:
                return ProxyResolution(
                    chain=chain,
                    proxy_address=address,
                    status=ProxyResolutionStatus.UNAVAILABLE,
                    proxy_kind=ProxyKind.EIP_1967_BEACON,
                    implementation_address=None,
                    block_number=block_number,
                    source_provider=self._source_name,
                    resolved_at=datetime.now(tz=UTC),
                    error="The beacon returned an empty implementation address.",
                    beacon_address=beacon_address,
                )
            return self._proxy_resolution(
                chain,
                address,
                block_number,
                ProxyKind.EIP_1967_BEACON,
                beacon_implementation,
                beacon_address=beacon_address,
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
            if _is_historical_state_unavailable(error):
                self._historical_state_support[inspection.chain] = False
                raise
            cause = error.__cause__
            if isinstance(cause, JSONRPCRemoteError):
                # A contract revert still proves that the endpoint read the requested block state.
                self._historical_state_support[inspection.chain] = True
                return _extract_revert_data(cause.data)
            raise
        self._historical_state_support[inspection.chain] = True
        return None

    def get_transaction_trace(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionTrace:
        normalized_hash = _require_hash(tx_hash)
        endpoint = self._endpoint_for(chain)
        if chain in self._trace_dialects:
            known_dialect = self._trace_dialects[chain]
            if known_dialect is None:
                return self._unsupported_trace(normalized_hash, chain)
            return self._load_trace(endpoint, normalized_hash, chain, known_dialect)

        for dialect in (TraceDialect.DEBUG_CALL_TRACER, TraceDialect.PARITY_TRACE):
            try:
                trace = self._load_trace(endpoint, normalized_hash, chain, dialect)
            except ProviderResponseError as error:
                if _is_unsupported_rpc_method(error):
                    continue
                raise
            self._trace_dialects[chain] = dialect
            return trace

        self._trace_dialects[chain] = None
        return self._unsupported_trace(normalized_hash, chain)

    def _load_trace(
        self,
        endpoint: str,
        tx_hash: str,
        chain: Chain,
        dialect: TraceDialect,
    ) -> TransactionTrace:
        if dialect is TraceDialect.DEBUG_CALL_TRACER:
            payload = self._rpc_call(
                endpoint,
                "debug_traceTransaction",
                [tx_hash, {"tracer": "callTracer", "timeout": "20s"}],
            )
            mapped = map_debug_call_trace(payload)
        else:
            payload = self._rpc_call(endpoint, "trace_transaction", [tx_hash])
            mapped = map_parity_trace(payload)
        return TransactionTrace(
            chain=chain,
            tx_hash=tx_hash,
            status=mapped.status,
            calls=mapped.calls,
            raw_json=json_dumps(payload, pretty=False).decode("utf-8"),
            source_provider=self._source_name,
            fetched_at=datetime.now(tz=UTC),
            dialect=dialect,
            error=mapped.error,
        )

    def _unsupported_trace(self, tx_hash: str, chain: Chain) -> TransactionTrace:
        return TransactionTrace(
            chain=chain,
            tx_hash=tx_hash,
            status=TraceStatus.UNSUPPORTED,
            calls=(),
            raw_json=None,
            source_provider=self._source_name,
            fetched_at=datetime.now(tz=UTC),
            error="The configured endpoint does not support a recognized trace method.",
        )

    def _endpoint_for(self, chain: Chain) -> str:
        endpoint = self._endpoints.get(chain)
        if endpoint is None:
            raise ProviderResponseError(
                f"{self._source_name} has no configured endpoint for {chain.display_name}."
            )
        return endpoint

    def _record_historical_state_failure(
        self,
        chain: Chain,
        error: ProviderError,
    ) -> None:
        if _is_historical_state_unavailable(error):
            self._historical_state_support[chain] = False

    def _historical_rpc_call(
        self,
        chain: Chain,
        endpoint: str,
        method: str,
        params: list[Any],
    ) -> object:
        """Run a block-specific state query and learn only clear capability results."""
        try:
            result = self._rpc_call(endpoint, method, params)
        except ProviderError as error:
            self._record_historical_state_failure(chain, error)
            raise
        self._historical_state_support[chain] = True
        return result

    def _proxy_resolution(
        self,
        chain: Chain,
        proxy_address: str,
        block_number: int,
        proxy_kind: ProxyKind,
        implementation_address: str,
        beacon_address: str | None = None,
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
            beacon_address=beacon_address,
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

    def read_contract(
        self,
        contract_address: str,
        call_data: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        return self._first_success(
            chain,
            "historical contract read",
            lambda provider: provider.read_contract(
                contract_address,
                call_data,
                chain,
                block_number,
            ),
            capability="transaction_lookup",
        )

    def resolve_proxy(
        self,
        contract_address: str,
        chain: Chain,
        block_number: int,
    ) -> ProxyResolution:
        errors: list[str] = []
        unavailable: list[ProxyResolution] = []
        for provider in self._providers:
            if provider.capabilities(chain).proxy_resolution is not True:
                continue
            try:
                resolution = provider.resolve_proxy(contract_address, chain, block_number)
            except ProviderError as error:
                errors.append(str(error))
                continue
            if resolution.status is ProxyResolutionStatus.UNAVAILABLE:
                unavailable.append(resolution)
                continue
            return resolution
        if unavailable:
            return unavailable[0]
        if not errors:
            raise ProviderResponseError(
                f"No proxy resolution provider is configured for {chain.display_name}."
            )
        raise ProviderError("All proxy resolution providers failed: " + "; ".join(errors))

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

    def get_transaction_trace(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionTrace:
        errors: list[str] = []
        unsupported: list[TransactionTrace] = []
        for provider in self._providers:
            if provider.capabilities(chain).traces is False:
                continue
            try:
                trace = provider.get_transaction_trace(tx_hash, chain)
            except ProviderError as error:
                errors.append(str(error))
                continue
            if trace.status is TraceStatus.UNSUPPORTED:
                unsupported.append(trace)
                continue
            return trace
        if errors:
            raise ProviderError("All transaction trace providers failed: " + "; ".join(errors))
        if unsupported:
            return unsupported[0]
        return TransactionTrace(
            chain=chain,
            tx_hash=tx_hash.lower(),
            status=TraceStatus.UNSUPPORTED,
            calls=(),
            raw_json=None,
            source_provider="not-configured",
            fetched_at=datetime.now(tz=UTC),
            error=f"No transaction trace provider is configured for {chain.display_name}.",
        )

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


def _is_unsupported_rpc_method(error: ProviderResponseError) -> bool:
    cause = error.__cause__
    if not isinstance(cause, JSONRPCRemoteError):
        return False
    message = str(cause).lower()
    return cause.code == -32601 or any(
        text in message
        for text in (
            "method not found",
            "method is not available",
            "method not supported",
            "unsupported method",
            "method disabled",
            "method is disabled",
            "method not enabled",
        )
    )


def _is_historical_state_unavailable(error: ProviderError) -> bool:
    """Recognize explicit pruning errors without guessing from temporary failures."""
    cause = error.__cause__
    message = str(cause if isinstance(cause, JSONRPCRemoteError) else error).lower()
    return any(
        text in message
        for text in (
            "archive node",
            "historical state unavailable",
            "historical state is not available",
            "missing trie node",
            "old state is not available",
            "pruned state",
            "state is not available",
        )
    )


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
