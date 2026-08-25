"""Load wallet balances and decoded transaction history from GoldRush.

The adapter uses the read-only GoldRush Foundational REST API with bearer authentication.
It converts balances and decoded token logs to core models, owns page cursors, and retries transient failures.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

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
    HTTPResponse,
)
from oracle41_open.providers.retry import retry_with_backoff

_API_ROOT = "https://api.covalenthq.com/v1"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
_APPROVAL_FOR_ALL_TOPIC = (
    "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
)


class GoldRushProvider(DataProvider):
    """Provide normalized wallet data through GoldRush Foundational APIs."""

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
            raise ProviderError("GoldRush API key is empty.")
        self._api_key = cleaned
        self._http_client = http_client or HTTPClient()
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        payload = self._get_data(
            f"{_API_ROOT}/{_chain_name(chain)}/address/{address}/balances_native/",
            {},
            "native balance",
        )
        items = _object_list(payload.get("items"), "native balance")
        native = next((item for item in items if item.get("native_token") is True), None)
        if native is None and items:
            native = items[0]
        if native is None or not isinstance(native.get("balance"), str):
            raise ProviderResponseError("GoldRush returned an invalid native balance.")
        decimals = _nonnegative_int(native.get("contract_decimals"), 18, 36)
        return _decimal_amount(native["balance"], decimals, "native balance")

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        if page_key is not None:
            raise ProviderResponseError("GoldRush token balances do not use page cursors.")
        payload = self._get_data(
            f"{_API_ROOT}/{_chain_name(chain)}/address/{address}/balances_v2/",
            {"no-spam": "true", "nft": "false"},
            "token balances",
        )
        balances: list[TokenBalance] = []
        for raw in _object_list(payload.get("items"), "token balances"):
            if raw.get("native_token") is True or raw.get("nft_data") is not None:
                continue
            contract = _address(raw.get("contract_address"))
            raw_balance = raw.get("balance")
            if contract is None or not isinstance(raw_balance, str):
                continue
            decimals = _nonnegative_int(raw.get("contract_decimals"), 0, 36)
            balances.append(
                TokenBalance(
                    token=Token(
                        contract_address=contract,
                        symbol=_text(raw.get("contract_ticker_symbol")) or "UNKNOWN",
                        name=_text(raw.get("contract_name")) or "Unknown",
                        decimals=decimals,
                        is_verified=raw.get("is_spam") is not True,
                    ),
                    balance_decimal=_decimal_amount(raw_balance, decimals, "token balance"),
                )
            )
        return TokenBalancePage(
            balances=balances,
            next_page_key=None,
            source_provider="goldrush",
        )

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        page_number = _page_number(cursor)
        payload = self._transaction_page(address, chain, page_number)
        items: list[ActivityItem] = []
        for transaction in _object_list(payload.get("items"), "wallet activity"):
            block_number = _optional_int(transaction.get("block_height"))
            if from_block is not None and block_number is not None and block_number < from_block:
                continue
            items.extend(_map_transaction(transaction, chain, include_native=True))
        return ActivityPage(
            items=_deduplicate(items),
            next_cursor=_next_page_cursor(payload, page_number),
            source_provider="goldrush",
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
        token = _address(token_address)
        if token is None:
            raise ProviderError("Invalid token address for GoldRush token history.")
        page_number = _page_number(cursor)
        payload = self._transaction_page(address, chain, page_number)
        allowed = {
            ActivityCategory.ERC20,
            ActivityCategory.ERC721,
            ActivityCategory.ERC1155,
        }
        if include_approvals:
            allowed.add(ActivityCategory.APPROVAL)
        items = [
            item
            for transaction in _object_list(payload.get("items"), "token history")
            for item in _map_transaction(transaction, chain, include_native=False)
            if item.contract_address == token and item.category in allowed
        ]
        return ActivityPage(
            items=_deduplicate(items),
            next_cursor=_next_page_cursor(payload, page_number),
            source_provider="goldrush",
        )

    def _transaction_page(
        self,
        address: str,
        chain: Chain,
        page_number: int,
    ) -> dict[str, Any]:
        return self._get_data(
            (
                f"{_API_ROOT}/{_chain_name(chain)}/address/{address}/"
                f"transactions_v3/page/{page_number}/"
            ),
            {"block-signed-at-asc": "false", "no-logs": "false"},
            "transaction history",
        )

    def _get_data(
        self,
        endpoint: str,
        params: dict[str, object],
        operation_name: str,
    ) -> dict[str, Any]:
        response = self._send(endpoint, params, operation_name)
        try:
            payload = json_loads(response.data)
        except ValueError as error:
            raise ProviderResponseError(
                f"GoldRush returned invalid JSON for {operation_name}."
            ) from error
        if not isinstance(payload, dict):
            raise ProviderResponseError(f"GoldRush returned invalid {operation_name} data.")
        _raise_for_payload_error(payload, operation_name)
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise ProviderResponseError(f"GoldRush returned invalid {operation_name} data.")
        return data

    def _send(
        self,
        endpoint: str,
        params: dict[str, object],
        operation_name: str,
    ) -> HTTPResponse:
        query = urlencode(params)
        request = HTTPRequest(
            url=f"{endpoint}?{query}" if query else endpoint,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )

        def operation() -> HTTPResponse:
            try:
                response = self._http_client.send(request)
            except HTTPClientTimeoutError as error:
                raise ProviderTimeoutError(f"GoldRush {operation_name} timed out.") from error
            except HTTPClientNetworkError as error:
                raise ProviderNetworkError(
                    f"GoldRush {operation_name} failed on the network."
                ) from error
            _raise_for_status(response.status_code, operation_name)
            return response

        return retry_with_backoff(
            operation=operation,
            should_retry=lambda error: isinstance(
                error,
                (ProviderNetworkError, ProviderRateLimitError, ProviderTimeoutError),
            ),
            attempts=self._retry_attempts,
            initial_delay_seconds=self._retry_initial_delay_seconds,
            backoff_multiplier=self._retry_backoff_multiplier,
            max_delay_seconds=self._retry_max_delay_seconds,
            sleep_func=self._sleep_func,
        )


def _map_transaction(
    raw: dict[str, Any],
    chain: Chain,
    include_native: bool,
) -> list[ActivityItem]:
    items: list[ActivityItem] = []
    for log in _object_list(raw.get("log_events"), "transaction logs"):
        mapped = _map_log_event(log, raw, chain)
        if mapped is not None:
            items.extend(mapped)
    if include_native:
        native = _map_native_transaction(raw, chain)
        if native is not None:
            items.append(native)
    return items


def _map_log_event(
    log: dict[str, Any],
    transaction: dict[str, Any],
    chain: Chain,
) -> list[ActivityItem] | None:
    contract = _address(log.get("sender_address"))
    tx_hash = _text(log.get("tx_hash") or transaction.get("tx_hash"))
    timestamp = _timestamp(log.get("block_signed_at") or transaction.get("block_signed_at"))
    if contract is None or tx_hash is None or timestamp is None:
        return None
    event_name, params = _decoded_event(log)
    topic = _first_topic(log)
    supports = {
        value.upper()
        for value in log.get("supports_erc", [])
        if isinstance(value, str)
    }
    if event_name == "TransferSingle":
        return _map_erc1155_transfer(log, transaction, chain, contract, tx_hash, timestamp, params)
    if event_name == "TransferBatch":
        return _map_erc1155_batch(log, transaction, chain, contract, tx_hash, timestamp, params)
    if event_name == "Transfer" or topic == _TRANSFER_TOPIC:
        return _map_transfer(log, transaction, chain, contract, tx_hash, timestamp, params, supports)
    if event_name in {"Approval", "ApprovalForAll"} or topic in {
        _APPROVAL_TOPIC,
        _APPROVAL_FOR_ALL_TOPIC,
    }:
        mapped = _map_approval(log, transaction, chain, contract, tx_hash, timestamp, params)
        return [mapped] if mapped is not None else None
    return None


def _map_transfer(
    log: dict[str, Any],
    transaction: dict[str, Any],
    chain: Chain,
    contract: str,
    tx_hash: str,
    timestamp: datetime,
    params: dict[str, object],
    supports: set[str],
) -> list[ActivityItem] | None:
    from_address = _address(params.get("from")) or ""
    to_address = _address(params.get("to")) or ""
    if "ERC721" in supports or "tokenId" in params or "token_id" in params:
        raw_value = str(params.get("tokenId") or params.get("token_id") or "0")
        category = ActivityCategory.ERC721
        decimals = 0
        amount = Decimal(1)
    else:
        raw_value = str(params.get("value") or params.get("amount") or "0")
        category = ActivityCategory.ERC20
        decimals = _nonnegative_int(log.get("sender_contract_decimals"), 0, 36)
        amount = _decimal_amount(raw_value, decimals, "transfer event")
    return [
        _activity_item(
            log,
            transaction,
            chain,
            contract,
            tx_hash,
            timestamp,
            from_address,
            to_address,
            raw_value,
            amount,
            category,
        )
    ]


def _map_erc1155_transfer(
    log: dict[str, Any],
    transaction: dict[str, Any],
    chain: Chain,
    contract: str,
    tx_hash: str,
    timestamp: datetime,
    params: dict[str, object],
) -> list[ActivityItem] | None:
    from_address = _address(params.get("from")) or ""
    to_address = _address(params.get("to")) or ""
    raw_value = str(params.get("value") or params.get("amount") or "0")
    return [
        _activity_item(
            log,
            transaction,
            chain,
            contract,
            tx_hash,
            timestamp,
            from_address,
            to_address,
            raw_value,
            _decimal_amount(raw_value, 0, "ERC-1155 transfer"),
            ActivityCategory.ERC1155,
        )
    ]


def _map_erc1155_batch(
    log: dict[str, Any],
    transaction: dict[str, Any],
    chain: Chain,
    contract: str,
    tx_hash: str,
    timestamp: datetime,
    params: dict[str, object],
) -> list[ActivityItem] | None:
    from_address = _address(params.get("from")) or ""
    to_address = _address(params.get("to")) or ""
    token_ids = _string_list(params.get("ids"))
    values = _string_list(params.get("values"))
    if not values:
        return None

    items: list[ActivityItem] = []
    for index, raw_value in enumerate(values):
        token_id = token_ids[index] if index < len(token_ids) else str(index)
        items.append(
            _activity_item(
                log,
                transaction,
                chain,
                contract,
                tx_hash,
                timestamp,
                from_address,
                to_address,
                raw_value,
                _decimal_amount(raw_value, 0, "ERC-1155 batch transfer"),
                ActivityCategory.ERC1155,
                log_suffix=f"batch-{token_id}-{index}",
            )
        )
    return items


def _map_approval(
    log: dict[str, Any],
    transaction: dict[str, Any],
    chain: Chain,
    contract: str,
    tx_hash: str,
    timestamp: datetime,
    params: dict[str, object],
) -> ActivityItem | None:
    owner = _address(params.get("owner"))
    spender = _address(params.get("spender") or params.get("operator"))
    if owner is None or spender is None:
        return None
    decimals = _nonnegative_int(log.get("sender_contract_decimals"), 0, 36)
    approved = params.get("approved")
    if isinstance(approved, bool):
        raw_value = "1" if approved else "0"
        value = Decimal(raw_value)
    else:
        raw_value = str(params.get("value") or "0")
        value = _decimal_amount(raw_value, decimals, "approval event")
    return _activity_item(
        log,
        transaction,
        chain,
        contract,
        tx_hash,
        timestamp,
        owner,
        spender,
        raw_value,
        value,
        ActivityCategory.APPROVAL,
    )


def _activity_item(
    log: dict[str, Any],
    transaction: dict[str, Any],
    chain: Chain,
    contract: str,
    tx_hash: str,
    timestamp: datetime,
    from_address: str,
    to_address: str,
    raw_value: str,
    value: Decimal,
    category: ActivityCategory,
    log_suffix: str | None = None,
) -> ActivityItem:
    log_index = str(log.get("log_offset", "0"))
    if log_suffix is not None:
        log_index = f"{log_index}-{log_suffix}"
    return ActivityItem(
        block_number=_optional_int(log.get("block_height") or transaction.get("block_height")),
        tx_hash=tx_hash,
        log_index=log_index,
        timestamp=timestamp,
        from_address=from_address,
        to_address=to_address,
        asset_symbol=_text(log.get("sender_contract_ticker_symbol")) or "TOKEN",
        contract_address=contract,
        raw_value=raw_value,
        value_decimal=value,
        value_usd=None,
        is_verified=None,
        category=category,
        chain=chain,
    )


def _map_native_transaction(raw: dict[str, Any], chain: Chain) -> ActivityItem | None:
    tx_hash = _text(raw.get("tx_hash"))
    timestamp = _timestamp(raw.get("block_signed_at"))
    raw_value = raw.get("value")
    if tx_hash is None or timestamp is None or not isinstance(raw_value, str):
        return None
    try:
        value = _decimal_amount(raw_value, 18, "native transaction")
    except ProviderResponseError:
        return None
    if value == 0:
        return None
    return ActivityItem(
        block_number=_optional_int(raw.get("block_height")),
        tx_hash=tx_hash,
        log_index="native",
        timestamp=timestamp,
        from_address=_address(raw.get("from_address")) or "",
        to_address=_address(raw.get("to_address")) or "",
        asset_symbol=chain.native_symbol,
        contract_address=None,
        raw_value=raw_value,
        value_decimal=value,
        value_usd=_optional_decimal(raw.get("value_quote")),
        is_verified=True,
        category=ActivityCategory.EXTERNAL,
        chain=chain,
    )


def _decoded_event(log: dict[str, Any]) -> tuple[str | None, dict[str, object]]:
    decoded = log.get("decoded")
    if not isinstance(decoded, dict):
        return None, {}
    params: dict[str, object] = {}
    for raw_param in decoded.get("params", []):
        if not isinstance(raw_param, dict):
            continue
        name = _text(raw_param.get("name"))
        if name is not None:
            params[name] = raw_param.get("value")
    return _text(decoded.get("name")), params


def _first_topic(log: dict[str, Any]) -> str | None:
    topics = log.get("raw_log_topics")
    if not isinstance(topics, list) or not topics or not isinstance(topics[0], str):
        return None
    return topics[0].lower()


def _next_page_cursor(payload: dict[str, Any], current_page: int) -> str | None:
    links = payload.get("links")
    if isinstance(links, dict) and _text(links.get("next")) is not None:
        return str(current_page + 1)
    pagination = payload.get("pagination")
    if isinstance(pagination, dict) and pagination.get("has_more") is True:
        return str(current_page + 1)
    return None


def _page_number(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdigit():
        raise ProviderResponseError("The GoldRush page cursor is invalid.")
    page = int(cursor)
    if page < 0:
        raise ProviderResponseError("The GoldRush page cursor is invalid.")
    return page


def _raise_for_status(status_code: int, operation_name: str) -> None:
    if status_code in {401, 403}:
        raise ProviderAuthError(f"GoldRush {operation_name} was not authorized.")
    if status_code == 402:
        raise ProviderResponseError("GoldRush API credits are exhausted for this account.")
    if status_code == 429:
        raise ProviderRateLimitError(f"GoldRush rate-limited {operation_name}.")
    if status_code in {408, 504}:
        raise ProviderTimeoutError(f"GoldRush {operation_name} timed out.")
    if status_code >= 500:
        raise ProviderNetworkError(f"GoldRush {operation_name} is temporarily unavailable.")
    if status_code >= 400:
        raise ProviderResponseError(
            f"GoldRush {operation_name} returned HTTP {status_code}."
        )


def _raise_for_payload_error(payload: dict[str, Any], operation_name: str) -> None:
    if payload.get("error") is not True:
        return
    error_code = _optional_int(payload.get("error_code"))
    if error_code in {401, 403}:
        raise ProviderAuthError(f"GoldRush {operation_name} was not authorized.")
    if error_code == 402:
        raise ProviderResponseError("GoldRush API credits are exhausted for this account.")
    if error_code == 429:
        raise ProviderRateLimitError(f"GoldRush rate-limited {operation_name}.")
    raise ProviderResponseError(f"GoldRush returned an error for {operation_name}.")


def _object_list(value: object, operation_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProviderResponseError(f"GoldRush returned invalid {operation_name} items.")
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int))]


def _decimal_amount(value: str, decimals: int, operation_name: str) -> Decimal:
    try:
        return Decimal(value) / (Decimal(10) ** decimals)
    except (InvalidOperation, ValueError) as error:
        raise ProviderResponseError(
            f"GoldRush returned an invalid numeric value for {operation_name}."
        ) from error


def _optional_decimal(value: object) -> Decimal | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _timestamp(value: object) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _address(value: object) -> str | None:
    text = _text(value)
    if text is None or not text.lower().startswith("0x") or len(text) != 42:
        return None
    return text.lower()


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _optional_int(value: object) -> int | None:
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: object, default: int, maximum: int) -> int:
    parsed = _optional_int(value)
    if parsed is None or parsed < 0 or parsed > maximum:
        return default
    return parsed


def _chain_name(chain: Chain) -> str:
    return {
        Chain.ETHEREUM: "eth-mainnet",
        Chain.OPTIMISM: "optimism-mainnet",
        Chain.POLYGON: "matic-mainnet",
        Chain.BASE: "base-mainnet",
        Chain.ARBITRUM: "arbitrum-mainnet",
    }[chain]


def _deduplicate(items: list[ActivityItem]) -> list[ActivityItem]:
    unique = {item.id: item for item in items}
    return sorted(unique.values(), key=lambda item: (item.timestamp, item.id), reverse=True)
