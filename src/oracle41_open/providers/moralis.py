"""Load wallet balances, activity, transfers, and active approvals from Moralis.

The adapter uses Moralis Data API REST endpoints and converts their responses to Oracle41 models.
It owns all pagination cursors, retries transient failures, and keeps the API key in a request header.
"""

from __future__ import annotations

import base64
import binascii
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

_DATA_API_ROOT = "https://deep-index.moralis.io/api/v2.2"
_CURSOR_PREFIX = "o41-moralis-token-v1:"


class MoralisProvider(DataProvider):
    """Provide normalized wallet data through the Moralis Data API."""

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
            raise ProviderError("Moralis API key is empty.")
        self._api_key = cleaned
        self._http_client = http_client or HTTPClient()
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        payload = self._get_object(
            f"{_DATA_API_ROOT}/{address}/balance",
            {"chain": _chain_code(chain)},
            "native balance",
        )
        raw_balance = payload.get("balance")
        if not isinstance(raw_balance, str):
            raise ProviderResponseError("Moralis returned an invalid native balance.")
        return _decimal_amount(raw_balance, 18, "native balance")

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        params: dict[str, object] = {
            "chain": _chain_code(chain),
            "exclude_spam": "true",
            "limit": 100,
        }
        if page_key:
            params["cursor"] = page_key
        payload = self._get_object(
            f"{_DATA_API_ROOT}/wallets/{address}/tokens",
            params,
            "token balances",
        )

        balances: list[TokenBalance] = []
        for raw in _object_list(payload.get("result"), "token balances"):
            if raw.get("native_token") is True or raw.get("nativeToken") is True:
                continue
            contract = _address(raw.get("token_address") or raw.get("tokenAddress"))
            raw_balance = raw.get("balance") or raw.get("balanceRaw")
            if contract is None or not isinstance(raw_balance, str):
                continue
            decimals = _nonnegative_int(raw.get("decimals"), 0, 36)
            balances.append(
                TokenBalance(
                    token=Token(
                        contract_address=contract,
                        symbol=_text(raw.get("symbol")) or "UNKNOWN",
                        name=_text(raw.get("name")) or "Unknown",
                        decimals=decimals,
                        is_verified=(
                            _bool(raw.get("verified_contract")) is True
                            or raw.get("verifiedContract") is True
                        ),
                    ),
                    balance_decimal=_decimal_amount(raw_balance, decimals, "token balance"),
                )
            )
        return TokenBalancePage(
            balances=balances,
            next_page_key=_cursor(payload.get("cursor")),
            source_provider="moralis",
        )

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        params: dict[str, object] = {
            "chain": _chain_code(chain),
            "include_internal_transactions": "true",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        if from_block is not None:
            params["from_block"] = max(0, from_block)
        payload = self._get_object(
            f"{_DATA_API_ROOT}/wallets/{address}/history",
            params,
            "wallet activity",
        )
        items: list[ActivityItem] = []
        for transaction in _object_list(payload.get("result"), "wallet activity"):
            items.extend(_map_history_transaction(transaction, chain))
        return ActivityPage(
            items=_deduplicate(items),
            next_cursor=_cursor(payload.get("cursor")),
            source_provider="moralis",
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
            raise ProviderError("Invalid token address for Moralis token history.")

        cursors = _decode_token_cursor(cursor, include_approvals)
        first_page = cursor is None
        items: list[ActivityItem] = []
        next_cursors: dict[str, str | None] = {"erc20": None, "nft": None, "approvals": None}

        if first_page or cursors["erc20"] is not None:
            payload = self._get_object(
                f"{_DATA_API_ROOT}/{address}/erc20/transfers",
                _transfer_params(chain, token, cursors["erc20"]),
                "ERC-20 transfers",
            )
            items.extend(
                mapped
                for raw in _object_list(payload.get("result"), "ERC-20 transfers")
                if (mapped := _map_erc20_transfer(raw, chain)) is not None
            )
            next_cursors["erc20"] = _cursor(payload.get("cursor"))

        if first_page or cursors["nft"] is not None:
            payload = self._get_object(
                f"{_DATA_API_ROOT}/{address}/nft/transfers",
                _transfer_params(chain, token, cursors["nft"]),
                "NFT transfers",
            )
            items.extend(
                mapped
                for raw in _object_list(payload.get("result"), "NFT transfers")
                if (mapped := _map_nft_transfer(raw, chain)) is not None
            )
            next_cursors["nft"] = _cursor(payload.get("cursor"))

        if include_approvals and (first_page or cursors["approvals"] is not None):
            payload = self._get_object(
                f"{_DATA_API_ROOT}/wallets/{address}/approvals",
                _approval_params(chain, cursors["approvals"]),
                "active approvals",
            )
            for raw in _object_list(payload.get("result"), "active approvals"):
                mapped = _map_approval(raw, address, chain)
                if mapped is not None and mapped.contract_address == token:
                    items.append(mapped)
            next_cursors["approvals"] = _cursor(payload.get("cursor"))

        return ActivityPage(
            items=_deduplicate(items),
            next_cursor=_encode_token_cursor(next_cursors, include_approvals),
            source_provider="moralis",
        )

    def _get_object(
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
                f"Moralis returned invalid JSON for {operation_name}."
            ) from error
        if not isinstance(payload, dict):
            raise ProviderResponseError(f"Moralis returned invalid {operation_name} data.")
        return payload

    def _send(
        self,
        endpoint: str,
        params: dict[str, object],
        operation_name: str,
    ) -> HTTPResponse:
        query = urlencode(params, doseq=True)
        request = HTTPRequest(
            url=f"{endpoint}?{query}",
            headers={"Accept": "application/json", "X-API-Key": self._api_key},
        )

        def operation() -> HTTPResponse:
            try:
                response = self._http_client.send(request)
            except HTTPClientTimeoutError as error:
                raise ProviderTimeoutError(f"Moralis {operation_name} timed out.") from error
            except HTTPClientNetworkError as error:
                raise ProviderNetworkError(
                    f"Moralis {operation_name} failed on the network."
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


def _map_history_transaction(raw: dict[str, Any], chain: Chain) -> list[ActivityItem]:
    items: list[ActivityItem] = []
    for transfer in _object_list(raw.get("erc20_transfers"), "wallet activity transfers"):
        mapped = _map_erc20_transfer(transfer, chain, transaction=raw)
        if mapped is not None:
            items.append(mapped)
    for transfer in _object_list(raw.get("nft_transfers"), "wallet activity NFTs"):
        mapped = _map_nft_transfer(transfer, chain, transaction=raw)
        if mapped is not None:
            items.append(mapped)
    for transfer in _object_list(raw.get("native_transfers"), "wallet activity native transfers"):
        mapped = _map_native_transfer(transfer, chain, transaction=raw)
        if mapped is not None:
            items.append(mapped)
    if not items and _text(raw.get("hash")) and _text(raw.get("value")):
        mapped = _map_native_transfer(raw, chain, transaction=raw)
        if mapped is not None:
            items.append(mapped)
    return items


def _map_erc20_transfer(
    raw: dict[str, Any],
    chain: Chain,
    transaction: dict[str, Any] | None = None,
) -> ActivityItem | None:
    context = transaction or raw
    contract = _address(raw.get("address") or raw.get("token_address"))
    tx_hash = _text(raw.get("transaction_hash") or context.get("hash"))
    timestamp = _timestamp(raw.get("block_timestamp") or context.get("block_timestamp"))
    if contract is None or tx_hash is None or timestamp is None:
        return None
    decimals = _nonnegative_int(raw.get("token_decimals"), 0, 36)
    raw_value = str(raw.get("value", "0"))
    return ActivityItem(
        block_number=_optional_int(raw.get("block_number") or context.get("block_number")),
        tx_hash=tx_hash,
        log_index=str(raw.get("log_index", "0")),
        timestamp=timestamp,
        from_address=_address(raw.get("from_address")) or "",
        to_address=_address(raw.get("to_address")) or "",
        asset_symbol=_text(raw.get("token_symbol")) or "TOKEN",
        contract_address=contract,
        raw_value=raw_value,
        value_decimal=_decimal_amount(raw_value, decimals, "ERC-20 transfer"),
        value_usd=None,
        is_verified=_bool(raw.get("verified_contract")),
        category=ActivityCategory.ERC20,
        chain=chain,
    )


def _map_nft_transfer(
    raw: dict[str, Any],
    chain: Chain,
    transaction: dict[str, Any] | None = None,
) -> ActivityItem | None:
    context = transaction or raw
    contract = _address(raw.get("token_address"))
    tx_hash = _text(raw.get("transaction_hash") or context.get("hash"))
    timestamp = _timestamp(raw.get("block_timestamp") or context.get("block_timestamp"))
    if contract is None or tx_hash is None or timestamp is None:
        return None
    category = (
        ActivityCategory.ERC1155
        if (_text(raw.get("contract_type")) or "").upper() == "ERC1155"
        else ActivityCategory.ERC721
    )
    amount = str(raw.get("amount") or raw.get("value") or "1")
    return ActivityItem(
        block_number=_optional_int(raw.get("block_number") or context.get("block_number")),
        tx_hash=tx_hash,
        log_index=str(raw.get("log_index", "0")),
        timestamp=timestamp,
        from_address=_address(raw.get("from_address")) or "",
        to_address=_address(raw.get("to_address")) or "",
        asset_symbol=_text(raw.get("token_symbol")) or "NFT",
        contract_address=contract,
        raw_value=amount,
        value_decimal=_decimal_amount(amount, 0, "NFT transfer"),
        value_usd=None,
        is_verified=_bool(raw.get("verified_collection")),
        category=category,
        chain=chain,
    )


def _map_native_transfer(
    raw: dict[str, Any],
    chain: Chain,
    transaction: dict[str, Any],
) -> ActivityItem | None:
    tx_hash = _text(raw.get("transaction_hash") or transaction.get("hash"))
    timestamp = _timestamp(raw.get("block_timestamp") or transaction.get("block_timestamp"))
    if tx_hash is None or timestamp is None:
        return None
    raw_value = str(raw.get("value", "0"))
    from_address = _address(raw.get("from_address") or transaction.get("from_address")) or ""
    to_address = _address(raw.get("to_address") or transaction.get("to_address")) or ""
    return ActivityItem(
        block_number=_optional_int(raw.get("block_number") or transaction.get("block_number")),
        tx_hash=tx_hash,
        log_index=str(
            raw.get("log_index", f"native:{from_address}:{to_address}:{raw_value}")
        ),
        timestamp=timestamp,
        from_address=from_address,
        to_address=to_address,
        asset_symbol=_text(raw.get("token_symbol")) or chain.native_symbol,
        contract_address=None,
        raw_value=raw_value,
        value_decimal=_decimal_amount(raw_value, 18, "native transfer"),
        value_usd=None,
        is_verified=True,
        category=(
            ActivityCategory.INTERNAL_TRANSFER
            if _bool(raw.get("internal_transaction")) is True
            else ActivityCategory.EXTERNAL
        ),
        chain=chain,
    )


def _map_approval(raw: dict[str, Any], wallet: str, chain: Chain) -> ActivityItem | None:
    token = raw.get("token")
    spender = raw.get("spender")
    if not isinstance(token, dict) or not isinstance(spender, dict):
        return None
    contract = _address(token.get("address"))
    spender_address = _address(spender.get("address"))
    tx_hash = _text(raw.get("transaction_hash"))
    timestamp = _timestamp(raw.get("block_timestamp"))
    if contract is None or spender_address is None or tx_hash is None or timestamp is None:
        return None
    raw_value = str(raw.get("value", "0"))
    return ActivityItem(
        block_number=_optional_int(raw.get("block_number")),
        tx_hash=tx_hash,
        log_index=f"approval:{spender_address}",
        timestamp=timestamp,
        from_address=_address(wallet) or wallet.lower(),
        to_address=spender_address,
        asset_symbol=_text(token.get("symbol")) or "TOKEN",
        contract_address=contract,
        raw_value=raw_value,
        value_decimal=_decimal_amount(raw_value, 0, "active approval"),
        value_usd=None,
        is_verified=_bool(token.get("verified_contract")),
        category=ActivityCategory.APPROVAL,
        chain=chain,
    )


def _transfer_params(chain: Chain, token: str, cursor: str | None) -> dict[str, object]:
    params: dict[str, object] = {
        "chain": _chain_code(chain),
        "contract_addresses": [token],
        "limit": 100,
    }
    if cursor:
        params["cursor"] = cursor
    return params


def _approval_params(chain: Chain, cursor: str | None) -> dict[str, object]:
    params: dict[str, object] = {"chain": _chain_code(chain), "limit": 100}
    if cursor:
        params["cursor"] = cursor
    return params


def _encode_token_cursor(cursors: dict[str, str | None], approvals: bool) -> str | None:
    if not any(cursors.values()):
        return None
    payload = json.dumps(
        {"approvals_enabled": approvals, **cursors},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _CURSOR_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_token_cursor(cursor: str | None, approvals: bool) -> dict[str, str | None]:
    empty: dict[str, str | None] = {"erc20": None, "nft": None, "approvals": None}
    if cursor is None:
        return empty
    if not cursor.startswith(_CURSOR_PREFIX):
        raise ProviderResponseError("The Moralis token-history cursor is invalid.")
    encoded = cursor.removeprefix(_CURSOR_PREFIX)
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderResponseError("The Moralis token-history cursor is invalid.") from error
    if not isinstance(payload, dict) or payload.get("approvals_enabled") is not approvals:
        raise ProviderResponseError("The Moralis token-history cursor does not match this request.")
    result: dict[str, str | None] = {}
    for key in empty:
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise ProviderResponseError("The Moralis token-history cursor is invalid.")
        result[key] = value
    return result


def _raise_for_status(status_code: int, operation_name: str) -> None:
    if status_code in {401, 403}:
        raise ProviderAuthError(f"Moralis {operation_name} was not authorized.")
    if status_code == 429:
        raise ProviderRateLimitError(f"Moralis rate-limited {operation_name}.")
    if status_code in {408, 504}:
        raise ProviderTimeoutError(f"Moralis {operation_name} timed out.")
    if status_code >= 400:
        raise ProviderResponseError(
            f"Moralis {operation_name} returned HTTP {status_code}."
        )


def _object_list(value: object, operation_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProviderResponseError(f"Moralis returned invalid {operation_name} items.")
    return [item for item in value if isinstance(item, dict)]


def _decimal_amount(value: str, decimals: int, operation_name: str) -> Decimal:
    try:
        return Decimal(value) / (Decimal(10) ** decimals)
    except (InvalidOperation, ValueError) as error:
        raise ProviderResponseError(
            f"Moralis returned an invalid numeric value for {operation_name}."
        ) from error


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


def _cursor(value: object) -> str | None:
    return _text(value)


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


def _bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _chain_code(chain: Chain) -> str:
    return {
        Chain.ETHEREUM: "eth",
        Chain.OPTIMISM: "optimism",
        Chain.POLYGON: "polygon",
        Chain.BASE: "base",
        Chain.ARBITRUM: "arbitrum",
    }[chain]


def _deduplicate(items: list[ActivityItem]) -> list[ActivityItem]:
    unique = {item.id: item for item in items}
    return sorted(unique.values(), key=lambda item: (item.timestamp, item.id), reverse=True)
