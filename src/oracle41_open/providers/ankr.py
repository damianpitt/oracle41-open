"""Load wallet, token, activity, and approval data from Ankr.

The adapter provides behavior compatible with the Alchemy path while preserving Ankr pagination and response rules.
Remote failures are mapped to safe structured provider errors.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

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

_ERC20_APPROVAL_EVENT_TOPIC = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)
_APPROVAL_FOR_ALL_EVENT_TOPIC = (
    "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
)
_APPROVAL_LOOKBACK_BLOCKS = 100_000


class AnkrProvider(DataProvider):
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
            raise ProviderError("Ankr API key is empty.")
        self._api_key = cleaned
        self._rpc_client = rpc_client or JSONRPCClient(http_client=HTTPClient())
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        url = self._rpc_endpoint(chain)
        result = self._rpc_call(url=url, method="eth_getBalance", params=[address, "latest"])
        if not isinstance(result, str):
            raise ProviderError("Invalid native balance response format from Ankr.")
        return _normalize_integer_amount(result, decimals=18)

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        url = self._multichain_endpoint()
        options: dict[str, Any] = {
            "walletAddress": address,
            "blockchain": [chain.ankr_blockchain_code],
            "pageSize": 50,
        }
        if page_key:
            options["pageToken"] = page_key

        result = self._rpc_call(url=url, method="ankr_getAccountBalance", params=[options])
        if not isinstance(result, dict):
            raise ProviderError("Invalid token balance response format from Ankr.")

        raw_assets = result.get("assets")
        if not isinstance(raw_assets, list):
            raw_assets = []

        balances: list[TokenBalance] = []
        for raw_asset in raw_assets:
            mapped = _map_ankr_asset_to_balance(raw_asset)
            if mapped is not None:
                balances.append(mapped)

        raw_next_page_key = result.get("nextPageToken")
        next_page_key = raw_next_page_key if isinstance(raw_next_page_key, str) and raw_next_page_key else None
        return TokenBalancePage(balances=balances, next_page_key=next_page_key)

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        return self._load_transfers(
            address=address,
            chain=chain,
            cursor=cursor,
            from_block=from_block,
            token_address=None,
        )

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        normalized_wallet = _normalized_address(address)
        normalized_token = _normalized_address(token_address)
        if normalized_wallet is None:
            raise ProviderError("Invalid wallet address for Ankr token transfer query.")
        if normalized_token is None:
            raise ProviderError("Invalid token address for Ankr token transfer query.")
        transfer_cursor, transfer_initialized, approval_to_block, approval_initialized = (
            self._decode_token_cursor(cursor)
        )
        if not transfer_initialized or transfer_cursor is not None:
            page = self._load_transfers(
                address=normalized_wallet,
                chain=chain,
                cursor=transfer_cursor,
                from_block=None,
                token_address=normalized_token,
            )
        else:
            page = ActivityPage(items=[], next_cursor=None, source_provider="ankr")
        next_approval_to_block: int | None = None
        query_from_block: int | None = None
        query_to_block: int | None = None
        if include_approvals and (not approval_initialized or approval_to_block is not None):
            approvals, next_approval_to_block, query_from_block, query_to_block = (
                self._fetch_approval_activity(
                wallet_address=normalized_wallet,
                token_address=normalized_token,
                chain=chain,
                base_items=page.items,
                to_block=approval_to_block,
                )
            )
            items = _dedupe_and_sort(page.items + approvals)
        else:
            items = page.items
        return ActivityPage(
            items=items,
            next_cursor=self._encode_token_cursor(
                transfer_cursor=page.next_cursor,
                approval_to_block=next_approval_to_block,
                include_approvals=include_approvals,
            ),
            source_provider="ankr",
            query_from_block=query_from_block,
            query_to_block=query_to_block,
        )

    def _load_transfers(
        self,
        address: str,
        chain: Chain,
        cursor: str | None,
        from_block: int | None,
        token_address: str | None,
    ) -> ActivityPage:
        url = self._multichain_endpoint()
        options: dict[str, Any] = {
            "blockchain": [chain.ankr_blockchain_code],
            "address": address,
            "descOrder": True,
            "pageSize": 50,
        }
        if cursor:
            options["pageToken"] = cursor
        if from_block is not None and from_block > 0:
            options["fromBlock"] = from_block
        if token_address is not None:
            options["contractAddress"] = token_address

        result = self._rpc_call(url=url, method="ankr_getTokenTransfers", params=[options])
        if not isinstance(result, dict):
            raise ProviderError("Invalid transfer response format from Ankr.")

        raw_transfers = result.get("transfers")
        if not isinstance(raw_transfers, list):
            raw_transfers = []

        items: list[ActivityItem] = []
        for raw_transfer in raw_transfers:
            mapped = _map_ankr_transfer_to_item(raw_transfer, chain=chain)
            if mapped is not None:
                items.append(mapped)

        deduped_sorted = _dedupe_and_sort(items)
        raw_next_page_key = result.get("nextPageToken")
        next_cursor = raw_next_page_key if isinstance(raw_next_page_key, str) and raw_next_page_key else None
        return ActivityPage(
            items=deduped_sorted,
            next_cursor=next_cursor,
            source_provider="ankr",
            query_from_block=from_block,
        )

    def _fetch_approval_activity(
        self,
        wallet_address: str,
        token_address: str,
        chain: Chain,
        base_items: list[ActivityItem],
        to_block: int | None,
    ) -> tuple[list[ActivityItem], int | None, int, int]:
        url = self._rpc_endpoint(chain)
        token_symbol = self._infer_token_symbol(base_items, token_address=token_address)
        token_verified = self._infer_token_verified(base_items, token_address=token_address)
        token_decimals = self._load_token_decimals(url=url, token_address=token_address)
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
                    "Ankr approval history could not be loaded completely."
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
            mapped = self._map_approval_log(
                raw_log=raw_log,
                chain=chain,
                token_address=token_address,
                token_symbol=token_symbol,
                token_decimals=token_decimals,
                token_verified=token_verified,
                block_timestamp_by_hex=block_timestamp_by_hex,
            )
            if mapped is not None:
                approvals.append(mapped)
        return approvals, next_to_block, first_block, last_block

    def _approval_block_range(
        self,
        url: str,
        to_block: int | None,
    ) -> tuple[str, str, int | None, int, int]:
        last_block = to_block
        if last_block is None:
            result = self._rpc_call(url=url, method="eth_blockNumber", params=[])
            last_block = _parse_int(result)
            if last_block is None:
                raise ProviderResponseError("Ankr returned an invalid latest block number.")
        first_block = max(0, last_block - _APPROVAL_LOOKBACK_BLOCKS + 1)
        next_to_block = first_block - 1 if first_block > 0 else None
        return hex(first_block), hex(last_block), next_to_block, first_block, last_block

    def _decode_token_cursor(
        self,
        cursor: str | None,
    ) -> tuple[str | None, bool, int | None, bool]:
        if cursor is None or not cursor.strip():
            return None, False, None, False
        try:
            decoded = json_loads(cursor)
        except ValueError:
            return cursor, False, None, False
        if not isinstance(decoded, dict):
            return cursor, False, None, False
        raw_transfer = decoded.get("transfer")
        transfer_cursor = raw_transfer if isinstance(raw_transfer, str) and raw_transfer else None
        if "approval_to" not in decoded:
            return transfer_cursor, False, None, False
        raw_approval_to = decoded.get("approval_to")
        approval_to = (
            raw_approval_to
            if isinstance(raw_approval_to, int) and not isinstance(raw_approval_to, bool)
            else None
        )
        return transfer_cursor, True, approval_to, True

    def _encode_token_cursor(
        self,
        transfer_cursor: str | None,
        approval_to_block: int | None,
        include_approvals: bool,
    ) -> str | None:
        if transfer_cursor is None and approval_to_block is None:
            return None
        if not include_approvals:
            return transfer_cursor
        payload = {"transfer": transfer_cursor, "approval_to": approval_to_block}
        return json_dumps(payload, pretty=False).decode("utf-8")

    def _load_token_decimals(self, url: str, token_address: str) -> int:
        try:
            result = self._rpc_call(
                url=url,
                method="eth_call",
                params=[
                    {"to": token_address, "data": "0x313ce567"},
                    "latest",
                ],
            )
        except ProviderError:
            return 18
        if not isinstance(result, str):
            return 18
        parsed = _parse_int(result)
        if parsed is None:
            return 18
        return max(0, min(parsed, 36))

    def _infer_token_symbol(self, items: list[ActivityItem], token_address: str) -> str:
        for item in items:
            if item.contract_address != token_address:
                continue
            symbol = item.asset_symbol.strip()
            if symbol and symbol.upper() != "UNKNOWN":
                return symbol
        return "UNKNOWN"

    def _infer_token_verified(self, items: list[ActivityItem], token_address: str) -> bool | None:
        seen_unknown = False
        for item in items:
            if item.contract_address != token_address:
                continue
            if item.is_verified is None:
                seen_unknown = True
                continue
            return item.is_verified
        if seen_unknown:
            return None
        return None

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
                result = self._rpc_call(url=url, method="eth_getBlockByNumber", params=[block_hex, False])
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
        token_verified: bool | None,
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
        tx_hash = _clean_text(raw_log.get("transactionHash"))
        if tx_hash is None:
            return None
        log_index = _clean_text(raw_log.get("logIndex")) or "0x0"
        raw_value = _clean_text(raw_log.get("data")) or "0x0"
        if normalized_topic0 == _APPROVAL_FOR_ALL_EVENT_TOPIC:
            approval_enabled = _parse_approval_for_all_flag(raw_value)
            value_decimal = Decimal("1") if approval_enabled else Decimal("0")
        else:
            value_decimal = _normalize_integer_amount(raw_value, token_decimals)

        block_hex = _clean_text(raw_log.get("blockNumber"))
        block_number = _parse_int(block_hex)
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
            return ProviderTimeoutError(f"Ankr RPC call timed out for {method}.")
        if isinstance(error, JSONRPCNetworkError):
            return ProviderNetworkError(f"Ankr RPC network failure for {method}: {error}")
        if isinstance(error, JSONRPCHTTPError):
            if error.status_code in {408, 504}:
                return ProviderTimeoutError(f"Ankr RPC timed out for {method} (HTTP {error.status_code}).")
            if error.status_code in {401, 403}:
                return ProviderAuthError(f"Ankr RPC authentication failed (HTTP {error.status_code}).")
            if error.status_code == 429:
                return ProviderRateLimitError("Ankr RPC rate-limited request (HTTP 429).")
            return ProviderResponseError(f"Ankr RPC returned HTTP {error.status_code} for {method}.")
        if isinstance(error, JSONRPCRemoteError):
            lowered = str(error).lower()
            if error.code == -32005 or "rate limit" in lowered:
                return ProviderRateLimitError(f"Ankr RPC rate-limited request for {method}.")
            if "timeout" in lowered or "timed out" in lowered:
                return ProviderTimeoutError(f"Ankr RPC timed out for {method}.")
            if "invalid api key" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
                return ProviderAuthError(f"Ankr RPC authentication failed for {method}.")
            return ProviderResponseError(f"Ankr RPC returned remote error for {method}: {error}")
        if isinstance(error, JSONRPCPayloadError):
            return ProviderResponseError(f"Ankr RPC returned invalid payload for {method}.")
        return ProviderResponseError(f"Ankr RPC call failed for {method}: {error}")

    def _rpc_endpoint(self, chain: Chain) -> str:
        return f"https://rpc.ankr.com/{chain.ankr_rpc_path}/{self._api_key}"

    def _multichain_endpoint(self) -> str:
        return f"https://rpc.ankr.com/multichain/{self._api_key}"


def _map_ankr_asset_to_balance(raw_asset: Any) -> TokenBalance | None:
    if not isinstance(raw_asset, dict):
        return None
    contract_address = _normalized_address(raw_asset.get("contractAddress"))
    if contract_address is None:
        return None

    symbol = _clean_text(raw_asset.get("tokenSymbol")) or "UNKNOWN"
    name = _clean_text(raw_asset.get("tokenName")) or symbol
    decimals = _parse_nonnegative_int(raw_asset.get("tokenDecimals"), default=18, max_value=36)
    is_verified = not bool(raw_asset.get("isSpam", False))

    raw_balance = _clean_text(raw_asset.get("balanceRawInteger"))
    if raw_balance:
        balance_decimal = _normalize_integer_amount(raw_balance, decimals)
    else:
        balance_decimal = _parse_decimal(raw_asset.get("balance")) or Decimal("0")

    token = Token(
        contract_address=contract_address,
        symbol=symbol,
        name=name,
        decimals=decimals,
        is_verified=is_verified,
    )
    return TokenBalance(token=token, balance_decimal=balance_decimal)


def _map_ankr_transfer_to_item(raw_transfer: Any, chain: Chain) -> ActivityItem | None:
    if not isinstance(raw_transfer, dict):
        return None

    tx_hash = _clean_text(raw_transfer.get("transactionHash")) or _clean_text(raw_transfer.get("hash"))
    if tx_hash is None:
        return None
    log_index = _clean_text(raw_transfer.get("logIndex")) or "0x0"
    block_number = _parse_int(raw_transfer.get("blockHeight")) or _parse_int(raw_transfer.get("blockNumber"))
    timestamp = _parse_timestamp(raw_transfer.get("blockTimestamp"))
    if timestamp is None:
        return None

    from_address = _clean_text(raw_transfer.get("fromAddress")) or _clean_text(raw_transfer.get("from"))
    to_address = _clean_text(raw_transfer.get("toAddress")) or _clean_text(raw_transfer.get("to"))
    if from_address is None:
        from_address = "0x0000000000000000000000000000000000000000"
    if to_address is None:
        to_address = "0x0000000000000000000000000000000000000000"

    contract_address = _normalized_address(raw_transfer.get("contractAddress"))
    symbol = _clean_text(raw_transfer.get("tokenSymbol")) or _clean_text(raw_transfer.get("symbol"))
    if symbol is None:
        symbol = chain.native_symbol if contract_address is None else "UNKNOWN"
    category = _parse_ankr_transfer_category(raw_transfer, contract_address=contract_address)
    default_decimals = 0 if category in {ActivityCategory.ERC721, ActivityCategory.ERC1155} else 18
    decimals = _parse_nonnegative_int(raw_transfer.get("tokenDecimals"), default=default_decimals, max_value=36)
    raw_value = _clean_text(raw_transfer.get("valueRawInteger")) or _clean_text(raw_transfer.get("value"))
    if raw_value is None:
        raw_value = "0"
    value_decimal = _parse_decimal(raw_transfer.get("value"))
    if value_decimal is None:
        if category is ActivityCategory.ERC721:
            parsed_quantity = _parse_integer_like_decimal(raw_value)
            if parsed_quantity is None or parsed_quantity == Decimal("0"):
                raw_value = "1"
                value_decimal = Decimal("1")
            else:
                value_decimal = parsed_quantity
        else:
            value_decimal = _normalize_integer_amount(raw_value, decimals)

    is_verified = None
    if "isSpam" in raw_transfer:
        is_verified = not bool(raw_transfer.get("isSpam"))

    return ActivityItem(
        block_number=block_number,
        tx_hash=tx_hash,
        log_index=log_index,
        timestamp=timestamp,
        from_address=from_address,
        to_address=to_address,
        asset_symbol=symbol,
        contract_address=contract_address,
        raw_value=raw_value,
        value_decimal=value_decimal,
        value_usd=None,
        is_verified=is_verified,
        category=category,
        chain=chain,
    )


def _dedupe_and_sort(items: list[ActivityItem]) -> list[ActivityItem]:
    by_id: dict[str, ActivityItem] = {}
    for item in items:
        current = by_id.get(item.id)
        if current is None or item.timestamp > current.timestamp:
            by_id[item.id] = item
    return sorted(by_id.values(), key=lambda item: item.timestamp, reverse=True)


def _parse_timestamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        try:
            return datetime.fromtimestamp(raw, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, float):
        try:
            return datetime.fromtimestamp(int(raw), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    if trimmed.isdigit():
        try:
            return datetime.fromtimestamp(int(trimmed), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    normalized = trimmed
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_integer_amount(raw_value: str, decimals: int) -> Decimal:
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
    return Decimal(integer_value) / (Decimal(10) ** decimals)


def _parse_decimal(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_nonnegative_int(raw: Any, default: int, max_value: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, min(value, max_value))


def _parse_int(raw: Any) -> int | None:
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


def _normalized_address(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip().lower()
    if not trimmed.startswith("0x") or len(trimmed) != 42:
        return None
    return trimmed


def _clean_text(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    return trimmed or None


def _parse_ankr_transfer_category(raw_transfer: dict[str, Any], contract_address: str | None) -> ActivityCategory:
    if contract_address is None:
        return ActivityCategory.EXTERNAL

    for field in ("tokenType", "contractType", "tokenStandard"):
        token_type = _clean_text(raw_transfer.get(field))
        if token_type is None:
            continue
        lowered = token_type.lower()
        if "1155" in lowered:
            return ActivityCategory.ERC1155
        if "721" in lowered:
            return ActivityCategory.ERC721
        if "20" in lowered:
            return ActivityCategory.ERC20

    if raw_transfer.get("tokenId") is not None:
        return ActivityCategory.ERC721

    return ActivityCategory.ERC20


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
