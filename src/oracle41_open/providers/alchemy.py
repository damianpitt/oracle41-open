"""Load wallet, token, activity, approval, and price data from Alchemy.

The adapter handles pagination, bounded log scans, retries, response validation, and conversion to core models.
Remote failures are mapped to safe structured provider errors.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    ProviderAuthError,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    Token,
    TokenBalance,
    TokenBalancePage,
)
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
)
from oracle41_open.providers.jsonrpc import (
    JSONRPCClient,
    JSONRPCClientError,
    JSONRPCHTTPError,
    JSONRPCNetworkError,
    JSONRPCPayloadError,
    JSONRPCRemoteError,
    JSONRPCTimeoutError,
)
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.providers.retry import retry_with_backoff

_ERC20_APPROVAL_EVENT_TOPIC = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)
_APPROVAL_FOR_ALL_EVENT_TOPIC = (
    "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
)
_APPROVAL_LOOKBACK_BLOCKS = 100_000


class AlchemyProvider(DataProvider):
    def __init__(
        self,
        api_key: str,
        rpc_client: JSONRPCClient | None = None,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 0.25,
        retry_backoff_multiplier: float = 2.0,
        retry_max_delay_seconds: float = 2.0,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        cleaned = api_key.strip()
        if not cleaned:
            raise ProviderError("Alchemy API key is empty.")
        self._api_key = cleaned
        self._rpc_client = rpc_client or JSONRPCClient(http_client=HTTPClient())
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        url = self._rpc_url(chain)
        result = self._rpc_call(url=url, method="eth_getBalance", params=[address, "latest"])
        if not isinstance(result, str):
            raise ProviderError("Invalid native balance response format from Alchemy.")
        return self._normalize_amount(result, decimals=18)

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        url = self._rpc_url(chain)
        params: list[Any] = [address, "erc20"]
        if page_key:
            params.append({"pageKey": page_key})
        result = self._rpc_call(url=url, method="alchemy_getTokenBalances", params=params)
        if not isinstance(result, dict):
            raise ProviderError("Invalid token balance response format from Alchemy.")

        raw_entries = result.get("tokenBalances")
        if not isinstance(raw_entries, list):
            raw_entries = []

        metadata_cache: dict[str, dict[str, Any]] = {}
        balances: list[TokenBalance] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            contract = self._normalized_address(raw_entry.get("contractAddress"))
            if contract is None:
                continue
            raw_balance = raw_entry.get("tokenBalance")
            if not isinstance(raw_balance, str) or raw_balance in {"", "0", "0x0"}:
                continue

            metadata = metadata_cache.get(contract)
            if metadata is None:
                metadata = self._load_token_metadata(url, contract)
                metadata_cache[contract] = metadata

            normalized_balance = self._normalize_amount(raw_balance, decimals=metadata["decimals"])
            token = Token(
                contract_address=contract,
                symbol=metadata["symbol"],
                name=metadata["name"],
                decimals=metadata["decimals"],
                is_verified=metadata["is_verified"],
            )
            balances.append(TokenBalance(token=token, balance_decimal=normalized_balance))

        raw_next_page_key = result.get("pageKey")
        next_page_key = raw_next_page_key if isinstance(raw_next_page_key, str) and raw_next_page_key else None
        return TokenBalancePage(
            balances=balances,
            next_page_key=next_page_key,
            source_provider="alchemy",
        )

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        url = self._rpc_url(chain)
        from_cursor, to_cursor = self._decode_activity_cursor(cursor)
        outgoing, next_from_cursor = self._fetch_activity_transfers(
            url=url,
            chain=chain,
            from_address=address,
            to_address=None,
            page_key=from_cursor,
            from_block=from_block,
            categories=["external", "erc20"],
            contract_addresses=None,
        )
        incoming, next_to_cursor = self._fetch_activity_transfers(
            url=url,
            chain=chain,
            from_address=None,
            to_address=address,
            page_key=to_cursor,
            from_block=from_block,
            categories=["external", "erc20"],
            contract_addresses=None,
        )

        items = self._merge_activity_items(outgoing + incoming)
        next_cursor = self._encode_activity_cursor(next_from_cursor, next_to_cursor)
        return ActivityPage(
            items=items,
            next_cursor=next_cursor,
            source_provider="alchemy",
            query_from_block=from_block or 0,
        )

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        normalized_token = self._normalized_address(token_address)
        normalized_wallet = self._normalized_address(address)
        if normalized_token is None:
            raise ProviderError("Invalid token address for token transfer query.")
        if normalized_wallet is None:
            raise ProviderError("Invalid wallet address for token transfer query.")

        url = self._rpc_url(chain)
        from_cursor, to_cursor, transfer_initialized, approval_to_block, approval_initialized = (
            self._decode_token_cursor(cursor)
        )
        outgoing: list[ActivityItem] = []
        next_from_cursor: str | None = None
        if not transfer_initialized or from_cursor is not None:
            outgoing, next_from_cursor = self._fetch_activity_transfers(
                url=url,
                chain=chain,
                from_address=normalized_wallet,
                to_address=None,
                page_key=from_cursor,
                from_block=None,
                categories=["erc20", "erc721", "erc1155"],
                contract_addresses=[normalized_token],
            )
        incoming: list[ActivityItem] = []
        next_to_cursor: str | None = None
        if not transfer_initialized or to_cursor is not None:
            incoming, next_to_cursor = self._fetch_activity_transfers(
                url=url,
                chain=chain,
                from_address=None,
                to_address=normalized_wallet,
                page_key=to_cursor,
                from_block=None,
                categories=["erc20", "erc721", "erc1155"],
                contract_addresses=[normalized_token],
            )
        items = outgoing + incoming
        next_approval_to_block: int | None = None
        query_from_block: int | None = None
        query_to_block: int | None = None
        if include_approvals and (not approval_initialized or approval_to_block is not None):
            approvals, next_approval_to_block, query_from_block, query_to_block = (
                self._fetch_approval_activity(
                url=url,
                wallet_address=normalized_wallet,
                token_address=normalized_token,
                chain=chain,
                to_block=approval_to_block,
                )
            )
            items.extend(approvals)
        items = self._merge_activity_items(items)
        next_cursor = self._encode_token_cursor(
            next_from_cursor,
            next_to_cursor,
            next_approval_to_block,
            include_approvals=include_approvals,
        )
        return ActivityPage(
            items=items,
            next_cursor=next_cursor,
            source_provider="alchemy",
            query_from_block=query_from_block,
            query_to_block=query_to_block,
        )

    def _load_token_metadata(self, url: str, contract_address: str) -> dict[str, Any]:
        try:
            result = self._rpc_call(
                url=url,
                method="alchemy_getTokenMetadata",
                params=[contract_address],
            )
        except ProviderError:
            return {
                "symbol": "UNKNOWN",
                "name": "Unknown",
                "decimals": 0,
                "is_verified": False,
            }

        if not isinstance(result, dict):
            return {
                "symbol": "UNKNOWN",
                "name": "Unknown",
                "decimals": 0,
                "is_verified": False,
            }

        symbol = self._clean_text(result.get("symbol")) or "UNKNOWN"
        name = self._clean_text(result.get("name")) or "Unknown"
        decimals = self._parse_nonnegative_int(result.get("decimals"), default=0, max_value=36)
        is_verified = symbol != "UNKNOWN" or name != "Unknown"
        return {
            "symbol": symbol,
            "name": name,
            "decimals": decimals,
            "is_verified": is_verified,
        }

    def _rpc_call(self, url: str, method: str, params: list[Any]) -> Any:
        def operation() -> Any:
            try:
                return self._rpc_client.call(url=url, method=method, params=params)
            except (ProviderError, JSONRPCClientError) as error:
                raise self._map_rpc_error(method=method, error=error) from error

        return retry_with_backoff(
            operation=operation,
            should_retry=_is_retryable_provider_error,
            attempts=self._retry_attempts,
            initial_delay_seconds=self._retry_initial_delay_seconds,
            backoff_multiplier=self._retry_backoff_multiplier,
            max_delay_seconds=self._retry_max_delay_seconds,
            sleep_func=self._sleep_func,
        )

    def _map_rpc_error(self, method: str, error: Exception) -> ProviderError:
        if isinstance(error, ProviderError):
            return error
        if isinstance(error, JSONRPCTimeoutError):
            return ProviderTimeoutError(f"Alchemy RPC call timed out for {method}.")
        if isinstance(error, JSONRPCNetworkError):
            return ProviderNetworkError(f"Alchemy RPC network failure for {method}: {error}")
        if isinstance(error, JSONRPCHTTPError):
            if error.status_code in {408, 504}:
                return ProviderTimeoutError(f"Alchemy RPC timed out for {method} (HTTP {error.status_code}).")
            if error.status_code in {401, 403}:
                return ProviderAuthError(f"Alchemy RPC authentication failed (HTTP {error.status_code}).")
            if error.status_code == 429:
                return ProviderRateLimitError("Alchemy RPC rate-limited request (HTTP 429).")
            return ProviderResponseError(f"Alchemy RPC returned HTTP {error.status_code} for {method}.")
        if isinstance(error, JSONRPCRemoteError):
            lowered = str(error).lower()
            if error.code == -32005 or "rate limit" in lowered:
                return ProviderRateLimitError(f"Alchemy RPC rate-limited request for {method}.")
            if "timeout" in lowered or "timed out" in lowered:
                return ProviderTimeoutError(f"Alchemy RPC timed out for {method}.")
            if "invalid api key" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
                return ProviderAuthError(f"Alchemy RPC authentication failed for {method}.")
            return ProviderResponseError(f"Alchemy RPC returned remote error for {method}: {error}")
        if isinstance(error, JSONRPCPayloadError):
            return ProviderResponseError(f"Alchemy RPC returned invalid payload for {method}.")
        return ProviderResponseError(f"Alchemy RPC call failed for {method}: {error}")

    def _fetch_activity_transfers(
        self,
        url: str,
        chain: Chain,
        from_address: str | None,
        to_address: str | None,
        page_key: str | None,
        from_block: int | None,
        categories: list[str],
        contract_addresses: list[str] | None,
    ) -> tuple[list[ActivityItem], str | None]:
        payload: dict[str, Any] = {
            "category": categories,
            "withMetadata": True,
            "excludeZeroValue": False,
            "maxCount": "0x32",
        }
        if from_address:
            payload["fromAddress"] = from_address
        if to_address:
            payload["toAddress"] = to_address
        if contract_addresses:
            payload["contractAddresses"] = contract_addresses
        if page_key:
            payload["pageKey"] = page_key
        if from_block is not None and from_block > 0:
            payload["fromBlock"] = hex(from_block)
        else:
            payload["fromBlock"] = "0x0"

        result = self._rpc_call(url=url, method="alchemy_getAssetTransfers", params=[payload])
        if not isinstance(result, dict):
            raise ProviderError("Invalid activity response format from Alchemy.")

        raw_transfers = result.get("transfers")
        if not isinstance(raw_transfers, list):
            raw_transfers = []

        items: list[ActivityItem] = []
        for raw_transfer in raw_transfers:
            mapped = self._map_activity_transfer(raw_transfer, chain=chain)
            if mapped is not None:
                items.append(mapped)

        raw_next_page_key = result.get("pageKey")
        next_page_key = raw_next_page_key if isinstance(raw_next_page_key, str) and raw_next_page_key else None
        return items, next_page_key

    def _fetch_approval_activity(
        self,
        url: str,
        wallet_address: str,
        token_address: str,
        chain: Chain,
        to_block: int | None,
    ) -> tuple[list[ActivityItem], int | None, int, int]:
        metadata = self._load_token_metadata(url, token_address)
        owner_topic = _topic_address(wallet_address)
        spender_topic = _topic_address(wallet_address)
        from_block_hex, to_block_hex, next_to_block, first_block, last_block = (
            self._approval_block_range(url, to_block)
        )

        raw_logs: list[dict[str, Any]] = []
        for query in (
            {
                "address": token_address,
                "fromBlock": from_block_hex,
                "toBlock": to_block_hex,
                "topics": [_ERC20_APPROVAL_EVENT_TOPIC, owner_topic],
            },
            {
                "address": token_address,
                "fromBlock": from_block_hex,
                "toBlock": to_block_hex,
                "topics": [_ERC20_APPROVAL_EVENT_TOPIC, None, spender_topic],
            },
            {
                "address": token_address,
                "fromBlock": from_block_hex,
                "toBlock": to_block_hex,
                "topics": [_APPROVAL_FOR_ALL_EVENT_TOPIC, owner_topic],
            },
            {
                "address": token_address,
                "fromBlock": from_block_hex,
                "toBlock": to_block_hex,
                "topics": [_APPROVAL_FOR_ALL_EVENT_TOPIC, None, spender_topic],
            },
        ):
            try:
                result = self._rpc_call(url=url, method="eth_getLogs", params=[query])
            except ProviderError as error:
                raise ProviderResponseError(
                    "Alchemy approval history could not be loaded completely."
                ) from error
            if not isinstance(result, list):
                continue
            for raw_log in result:
                if isinstance(raw_log, dict):
                    raw_logs.append(raw_log)

        if not raw_logs:
            return [], next_to_block, first_block, last_block

        block_timestamp_by_hex = self._load_block_timestamps(
            url=url,
            block_hexes=[raw_log.get("blockNumber") for raw_log in raw_logs],
        )
        approvals: list[ActivityItem] = []
        for raw_log in raw_logs:
            parsed = self._map_approval_log(
                raw_log=raw_log,
                chain=chain,
                token_address=token_address,
                token_symbol=metadata["symbol"],
                token_decimals=metadata["decimals"],
                token_verified=metadata["is_verified"],
                block_timestamp_by_hex=block_timestamp_by_hex,
            )
            if parsed is not None:
                approvals.append(parsed)
        return approvals, next_to_block, first_block, last_block

    def _approval_block_range(
        self,
        url: str,
        to_block: int | None,
    ) -> tuple[str, str, int | None, int, int]:
        last_block = to_block
        if last_block is None:
            result = self._rpc_call(url=url, method="eth_blockNumber", params=[])
            last_block = _parse_block_number(result)
            if last_block is None:
                raise ProviderResponseError("Alchemy returned an invalid latest block number.")
        first_block = max(0, last_block - _APPROVAL_LOOKBACK_BLOCKS + 1)
        next_to_block = first_block - 1 if first_block > 0 else None
        return hex(first_block), hex(last_block), next_to_block, first_block, last_block

    def _load_block_timestamps(self, url: str, block_hexes: list[Any]) -> dict[str, datetime]:
        normalized_hexes: list[str] = []
        for raw in block_hexes:
            if not isinstance(raw, str):
                continue
            value = raw.strip().lower()
            if not value.startswith("0x") or len(value) <= 2:
                continue
            normalized_hexes.append(value)

        unique_hexes = list(dict.fromkeys(normalized_hexes))
        if len(unique_hexes) > 30:
            unique_hexes = unique_hexes[:30]

        timestamps: dict[str, datetime] = {}
        for block_hex in unique_hexes:
            try:
                result = self._rpc_call(
                    url=url,
                    method="eth_getBlockByNumber",
                    params=[block_hex, False],
                )
            except ProviderError:
                continue
            if not isinstance(result, dict):
                continue
            timestamp = _parse_block_timestamp_hex(result.get("timestamp"))
            if timestamp is not None:
                timestamps[block_hex] = timestamp
        return timestamps

    def _map_approval_log(
        self,
        raw_log: dict[str, Any],
        chain: Chain,
        token_address: str,
        token_symbol: str,
        token_decimals: int,
        token_verified: bool,
        block_timestamp_by_hex: dict[str, datetime],
    ) -> ActivityItem | None:
        topics = raw_log.get("topics")
        if not isinstance(topics, list) or len(topics) < 3:
            return None
        topic0 = topics[0]
        if not isinstance(topic0, str):
            return None
        normalized_topic0 = topic0.lower()
        if normalized_topic0 not in {_ERC20_APPROVAL_EVENT_TOPIC, _APPROVAL_FOR_ALL_EVENT_TOPIC}:
            return None

        owner = _address_from_topic(topics[1]) or "0x0000000000000000000000000000000000000000"
        spender = _address_from_topic(topics[2]) or "0x0000000000000000000000000000000000000000"

        tx_hash = self._clean_text(raw_log.get("transactionHash"))
        if tx_hash is None:
            return None
        log_index = self._clean_text(raw_log.get("logIndex")) or "0x0"
        raw_value = self._clean_text(raw_log.get("data")) or "0x0"
        if normalized_topic0 == _APPROVAL_FOR_ALL_EVENT_TOPIC:
            approval_enabled = _parse_approval_for_all_flag(raw_value)
            value_decimal = Decimal("1") if approval_enabled else Decimal("0")
        else:
            value_decimal = self._normalize_amount(raw_value, token_decimals)

        block_hex = self._clean_text(raw_log.get("blockNumber"))
        block_number = _parse_block_number(block_hex)
        timestamp = (
            block_timestamp_by_hex.get(block_hex.lower()) if isinstance(block_hex, str) else None
        )
        if timestamp is None:
            return None

        return ActivityItem(
            block_number=block_number,
            tx_hash=tx_hash,
            log_index=log_index,
            timestamp=timestamp,
            from_address=owner,
            to_address=spender,
            asset_symbol=token_symbol,
            contract_address=token_address,
            raw_value=raw_value,
            value_decimal=value_decimal,
            value_usd=None,
            is_verified=token_verified,
            category=ActivityCategory.APPROVAL,
            chain=chain,
        )

    def _map_activity_transfer(self, raw_transfer: Any, chain: Chain) -> ActivityItem | None:
        if not isinstance(raw_transfer, dict):
            return None

        tx_hash = self._clean_text(raw_transfer.get("hash"))
        if tx_hash is None:
            return None
        log_index = self._clean_text(raw_transfer.get("logIndex")) or "0x0"
        block_number = _parse_block_number(raw_transfer.get("blockNum"))

        metadata = raw_transfer.get("metadata")
        block_timestamp: str | None = None
        if isinstance(metadata, dict):
            block_timestamp = self._clean_text(metadata.get("blockTimestamp"))
        timestamp = _parse_timestamp(block_timestamp)
        if timestamp is None:
            return None

        from_address = self._clean_text(raw_transfer.get("from")) or "0x0000000000000000000000000000000000000000"
        to_address = self._clean_text(raw_transfer.get("to")) or "0x0000000000000000000000000000000000000000"
        asset_symbol = self._clean_text(raw_transfer.get("asset")) or "UNKNOWN"

        category = _parse_activity_category(self._clean_text(raw_transfer.get("category")))
        raw_contract = raw_transfer.get("rawContract")
        contract_address: str | None = None
        raw_value = "0"
        decimals = 18
        if isinstance(raw_contract, dict):
            contract_address = self._normalized_address(raw_contract.get("address"))
            raw_value_field = self._clean_text(raw_contract.get("value"))
            if raw_value_field is not None:
                raw_value = raw_value_field
            decimals = self._parse_nonnegative_int(raw_contract.get("decimal"), default=18, max_value=36)

        value_decimal = _parse_activity_decimal(raw_transfer.get("value"))
        if value_decimal is None:
            if category is ActivityCategory.ERC721:
                parsed_quantity = _parse_integer_like_decimal(raw_value)
                if parsed_quantity is None or parsed_quantity == Decimal("0"):
                    raw_value = "1"
                    value_decimal = Decimal("1")
                else:
                    value_decimal = parsed_quantity
            elif category is ActivityCategory.ERC1155:
                erc1155_quantity = _parse_erc1155_quantity(raw_transfer.get("erc1155Metadata"))
                if erc1155_quantity is None:
                    value_decimal = self._normalize_amount(raw_value, decimals)
                else:
                    raw_value, value_decimal = erc1155_quantity
            else:
                value_decimal = self._normalize_amount(raw_value, decimals)

        return ActivityItem(
            block_number=block_number,
            tx_hash=tx_hash,
            log_index=log_index,
            timestamp=timestamp,
            from_address=from_address,
            to_address=to_address,
            asset_symbol=asset_symbol,
            contract_address=contract_address,
            raw_value=raw_value,
            value_decimal=value_decimal,
            value_usd=None,
            is_verified=None,
            category=category,
            chain=chain,
        )

    def _merge_activity_items(self, items: list[ActivityItem]) -> list[ActivityItem]:
        by_id: dict[str, ActivityItem] = {}
        for item in items:
            existing = by_id.get(item.id)
            if existing is None or item.timestamp > existing.timestamp:
                by_id[item.id] = item
        return sorted(by_id.values(), key=lambda item: item.timestamp, reverse=True)

    def _decode_activity_cursor(self, cursor: str | None) -> tuple[str | None, str | None]:
        if cursor is None or not cursor.strip():
            return None, None
        try:
            decoded = json_loads(cursor)
        except ValueError:
            return cursor, None
        if not isinstance(decoded, dict):
            return cursor, None
        from_cursor = decoded.get("from")
        to_cursor = decoded.get("to")
        safe_from = from_cursor if isinstance(from_cursor, str) and from_cursor else None
        safe_to = to_cursor if isinstance(to_cursor, str) and to_cursor else None
        return safe_from, safe_to

    def _encode_activity_cursor(self, from_cursor: str | None, to_cursor: str | None) -> str | None:
        if from_cursor is None and to_cursor is None:
            return None
        payload = {"from": from_cursor, "to": to_cursor}
        return json_dumps(payload, pretty=False).decode("utf-8")

    def _decode_token_cursor(
        self,
        cursor: str | None,
    ) -> tuple[str | None, str | None, bool, int | None, bool]:
        from_cursor, to_cursor = self._decode_activity_cursor(cursor)
        if cursor is None or not cursor.strip():
            return from_cursor, to_cursor, False, None, False
        try:
            decoded = json_loads(cursor)
        except ValueError:
            return from_cursor, to_cursor, False, None, False
        if not isinstance(decoded, dict):
            return from_cursor, to_cursor, False, None, False
        transfer_initialized = (
            decoded.get("transfer_initialized") is True or "approval_to" in decoded
        )
        if "approval_to" not in decoded:
            return from_cursor, to_cursor, transfer_initialized, None, False
        raw_approval_to = decoded.get("approval_to")
        approval_to = (
            raw_approval_to
            if isinstance(raw_approval_to, int) and not isinstance(raw_approval_to, bool)
            else None
        )
        return from_cursor, to_cursor, transfer_initialized, approval_to, True

    def _encode_token_cursor(
        self,
        from_cursor: str | None,
        to_cursor: str | None,
        approval_to_block: int | None,
        include_approvals: bool,
    ) -> str | None:
        if from_cursor is None and to_cursor is None and approval_to_block is None:
            return None
        payload: dict[str, object] = {
            "from": from_cursor,
            "to": to_cursor,
            "transfer_initialized": True,
        }
        if include_approvals:
            payload["approval_to"] = approval_to_block
        return json_dumps(payload, pretty=False).decode("utf-8")

    def _rpc_url(self, chain: Chain) -> str:
        return f"https://{chain.alchemy_network_path}.g.alchemy.com/v2/{self._api_key}"

    def _normalize_amount(self, raw_value: str, decimals: int) -> Decimal:
        raw = raw_value.strip().lower()
        try:
            if raw.startswith("0x"):
                integer_value = int(raw[2:] or "0", 16)
            else:
                integer_value = int(Decimal(raw))
        except (ValueError, InvalidOperation):
            return Decimal("0")

        if decimals <= 0:
            return Decimal(integer_value)
        divisor = Decimal(10) ** decimals
        return Decimal(integer_value) / divisor

    def _clean_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        trimmed = value.strip()
        return trimmed or None

    def _parse_nonnegative_int(self, value: Any, default: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(0, min(parsed, max_value))

    def _normalized_address(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        trimmed = value.strip().lower()
        if not trimmed.startswith("0x") or len(trimmed) != 42:
            return None
        return trimmed


class AlchemyPricingProvider(PricingProvider):
    def __init__(
        self,
        api_key: str,
        http_client: HTTPClient | None = None,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 0.25,
        retry_backoff_multiplier: float = 2.0,
        retry_max_delay_seconds: float = 2.0,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        cleaned = api_key.strip()
        if not cleaned:
            raise ProviderError("Alchemy API key is empty.")
        self._api_key = cleaned
        self._http_client = http_client or HTTPClient()
        self._base_url = f"https://api.g.alchemy.com/prices/v1/{self._api_key}"
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def get_native_price(self, chain: Chain) -> Decimal | None:
        quotes = self.get_simple_prices([chain.native_pricing_symbol])
        return quotes.get(chain.native_pricing_symbol.upper())

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        addresses = sorted(
            {
                address.strip().lower()
                for address in contract_addresses
                if isinstance(address, str) and address.strip().startswith("0x") and len(address.strip()) == 42
            }
        )
        if not addresses:
            return {}

        result: dict[str, Decimal] = {}
        for chunk in _chunked(addresses, size=25):
            payload = {
                "addresses": [
                    {
                        "network": chain.alchemy_network_path,
                        "address": address,
                    }
                    for address in chunk
                ]
            }
            response_json = self._send_json(
                HTTPRequest(
                    url=f"{self._base_url}/tokens/by-address",
                    method="POST",
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
            entries = response_json.get("data")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("error") is not None:
                    continue
                address = entry.get("address")
                if not isinstance(address, str):
                    continue
                quote = _first_usd_quote(entry.get("prices"))
                if quote is not None:
                    result[address.lower()] = quote
        return result

    def get_simple_prices(self, ids: list[str]) -> dict[str, Decimal]:
        symbols = sorted({symbol.strip().upper() for symbol in ids if isinstance(symbol, str) and symbol.strip()})
        if not symbols:
            return {}

        result: dict[str, Decimal] = {}
        for chunk in _chunked(symbols, size=25):
            query = urlencode([("symbols", symbol) for symbol in chunk])
            response_json = self._send_json(
                HTTPRequest(
                    url=f"{self._base_url}/tokens/by-symbol?{query}",
                    method="GET",
                    headers={"content-type": "application/json"},
                )
            )
            entries = response_json.get("data")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("error") is not None:
                    continue
                symbol = entry.get("symbol")
                if not isinstance(symbol, str):
                    continue
                quote = _first_usd_quote(entry.get("prices"))
                if quote is not None:
                    result[symbol.upper()] = quote
        return result

    def _send_json(self, request: HTTPRequest) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            try:
                response = self._http_client.send(request)
            except HTTPClientTimeoutError as error:
                raise ProviderTimeoutError("Alchemy pricing request timed out.") from error
            except HTTPClientNetworkError as error:
                raise ProviderNetworkError(f"Alchemy pricing network failure: {error}") from error

            if response.status_code in {401, 403}:
                raise ProviderAuthError(f"Alchemy pricing authentication failed (HTTP {response.status_code}).")
            if response.status_code == 429:
                raise ProviderRateLimitError("Alchemy pricing request rate-limited (HTTP 429).")
            if response.status_code < 200 or response.status_code >= 300:
                raise ProviderResponseError(
                    f"Alchemy pricing request failed with HTTP {response.status_code}."
                )
            try:
                decoded = json_loads(response.data)
            except ValueError as error:
                raise ProviderResponseError("Invalid Alchemy pricing response format.") from error
            if not isinstance(decoded, dict):
                raise ProviderResponseError("Invalid Alchemy pricing response format.")
            return decoded

        return retry_with_backoff(
            operation=operation,
            should_retry=_is_retryable_provider_error,
            attempts=self._retry_attempts,
            initial_delay_seconds=self._retry_initial_delay_seconds,
            backoff_multiplier=self._retry_backoff_multiplier,
            max_delay_seconds=self._retry_max_delay_seconds,
            sleep_func=self._sleep_func,
        )


def _first_usd_quote(raw_prices: Any) -> Decimal | None:
    if not isinstance(raw_prices, list):
        return None
    for raw_price in raw_prices:
        if not isinstance(raw_price, dict):
            continue
        currency = raw_price.get("currency")
        if not isinstance(currency, str) or currency.lower() != "usd":
            continue
        value = raw_price.get("value")
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
    return None


def _chunked(values: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return []
    return [values[index:index + size] for index in range(0, len(values), size)]


def _parse_activity_category(raw: str | None) -> ActivityCategory:
    if raw is None:
        return ActivityCategory.EXTERNAL
    for category in ActivityCategory:
        if category.value.lower() == raw.lower():
            return category
    return ActivityCategory.EXTERNAL


def _parse_activity_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_timestamp(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    normalized = raw.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_block_number(raw: Any) -> int | None:
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip().lower()
    if not trimmed:
        return None
    try:
        if trimmed.startswith("0x"):
            return int(trimmed[2:] or "0", 16)
        return int(trimmed)
    except ValueError:
        return None


def _parse_block_timestamp_hex(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip().lower()
    if not trimmed:
        return None
    try:
        if trimmed.startswith("0x"):
            epoch_seconds = int(trimmed[2:] or "0", 16)
        else:
            epoch_seconds = int(trimmed)
    except ValueError:
        return None
    try:
        return datetime.fromtimestamp(epoch_seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _topic_address(address: str) -> str:
    return "0x" + ("0" * 24) + address.strip().lower()[2:]


def _address_from_topic(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip().lower()
    if not trimmed.startswith("0x"):
        return None
    payload = trimmed[2:]
    if len(payload) != 64:
        return None
    return "0x" + payload[-40:]


def _parse_approval_for_all_flag(raw_value: str) -> bool:
    parsed = _parse_integer_like_decimal(raw_value)
    if parsed is not None:
        return parsed != Decimal("0")
    lowered = raw_value.strip().lower()
    return lowered in {"true", "yes"}


def _parse_erc1155_quantity(raw_metadata: Any) -> tuple[str, Decimal] | None:
    if not isinstance(raw_metadata, list):
        return None

    raw_values: list[str] = []
    total = Decimal("0")
    for raw_entry in raw_metadata:
        if not isinstance(raw_entry, dict):
            continue
        raw_quantity = raw_entry.get("value")
        parsed = _parse_integer_like_decimal(raw_quantity)
        if parsed is None:
            continue
        total += parsed
        raw_values.append(str(raw_quantity).strip())

    if not raw_values:
        return None
    if len(raw_values) == 1:
        return raw_values[0], total
    return "+".join(raw_values), total


def _parse_integer_like_decimal(raw: Any) -> Decimal | None:
    if isinstance(raw, int):
        return Decimal(raw)
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip().lower()
    if not trimmed:
        return None
    try:
        if trimmed.startswith("0x"):
            return Decimal(int(trimmed[2:] or "0", 16))
        return Decimal(trimmed)
    except (InvalidOperation, ValueError):
        return None


def _is_retryable_provider_error(error: Exception) -> bool:
    return isinstance(error, (ProviderTimeoutError, ProviderRateLimitError, ProviderNetworkError))
