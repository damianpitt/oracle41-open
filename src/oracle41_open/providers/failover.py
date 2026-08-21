"""Route wallet-data requests through an ordered provider pool.

Fresh requests try enabled providers in priority order after structured provider failures.
Continuation cursors include their provider owner and operation, so later pages cannot be mixed between vendors.
Programming errors are raised immediately instead of being hidden by failover.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TypeVar

from oracle41_open.core.models import (
    ActivityPage,
    Chain,
    ProviderError,
    ProviderResponseError,
    TokenBalancePage,
)
from oracle41_open.providers.data_provider import DataProvider

_CURSOR_PREFIX = "o41-provider-cursor-v1:"
_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class ProviderPoolEntry:
    """Pair a stable provider ID with one configured adapter."""

    provider_id: str
    provider: DataProvider


class OrderedDataProviderPool:
    """Use configured wallet-data providers in explicit priority order."""

    def __init__(self, entries: Iterable[ProviderPoolEntry]) -> None:
        self._entries = tuple(entries)
        if not self._entries:
            raise ValueError("The provider pool requires at least one provider.")

        provider_ids = [entry.provider_id for entry in self._entries]
        if any(not provider_id.strip() for provider_id in provider_ids):
            raise ValueError("Provider pool IDs cannot be empty.")
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("Provider pool IDs must be unique.")
        self._entries_by_id = {entry.provider_id: entry for entry in self._entries}

    @property
    def provider_ids(self) -> tuple[str, ...]:
        """Return configured providers in request order."""

        return tuple(entry.provider_id for entry in self._entries)

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        _, result = self._run_fresh_request(
            "native balance",
            lambda provider: provider.get_native_balance(address, chain),
        )
        return result

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        operation = "token_balances"
        if page_key is not None:
            owner, raw_cursor = self._decode_cursor(page_key, operation)
            page = self._run_continuation(
                owner,
                "token balances",
                lambda provider: provider.get_token_balances(
                    address,
                    chain,
                    page_key=raw_cursor,
                ),
            )
        else:
            entry, page = self._run_fresh_request(
                "token balances",
                lambda provider: provider.get_token_balances(address, chain),
            )
            owner = entry.provider_id
        return replace(
            page,
            next_page_key=self._encode_cursor(owner, operation, page.next_page_key),
            source_provider=owner,
        )

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        operation = "activity"
        if cursor is not None:
            owner, raw_cursor = self._decode_cursor(cursor, operation)
            page = self._run_continuation(
                owner,
                "activity",
                lambda provider: provider.get_activity(
                    address,
                    chain,
                    cursor=raw_cursor,
                    from_block=from_block,
                ),
            )
        else:
            entry, page = self._run_fresh_request(
                "activity",
                lambda provider: provider.get_activity(
                    address,
                    chain,
                    from_block=from_block,
                ),
            )
            owner = entry.provider_id
        return replace(
            page,
            next_cursor=self._encode_cursor(owner, operation, page.next_cursor),
            source_provider=owner,
        )

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        operation = "token_transfers"
        if cursor is not None:
            owner, raw_cursor = self._decode_cursor(cursor, operation)
            page = self._run_continuation(
                owner,
                "token transfers",
                lambda provider: provider.get_token_transfers(
                    address,
                    token_address,
                    chain,
                    cursor=raw_cursor,
                    include_approvals=include_approvals,
                ),
            )
        else:
            entry, page = self._run_fresh_request(
                "token transfers",
                lambda provider: provider.get_token_transfers(
                    address,
                    token_address,
                    chain,
                    include_approvals=include_approvals,
                ),
            )
            owner = entry.provider_id
        return replace(
            page,
            next_cursor=self._encode_cursor(owner, operation, page.next_cursor),
            source_provider=owner,
        )

    def _run_fresh_request(
        self,
        operation: str,
        call: Callable[[DataProvider], _ResultT],
    ) -> tuple[ProviderPoolEntry, _ResultT]:
        failures: list[str] = []
        for entry in self._entries:
            try:
                return entry, call(entry.provider)
            except ProviderError as error:
                failures.append(f"{entry.provider_id}: {error}")

        detail = "; ".join(failures)
        raise ProviderError(f"All enabled providers failed for {operation}. {detail}")

    def _run_continuation(
        self,
        owner: str,
        operation: str,
        call: Callable[[DataProvider], _ResultT],
    ) -> _ResultT:
        entry = self._entries_by_id.get(owner)
        if entry is None:
            raise ProviderResponseError(
                f"Cannot continue {operation}: its source provider is not enabled. Start a new load."
            )
        try:
            return call(entry.provider)
        except ProviderError as error:
            # Another provider cannot safely consume this provider's cursor.
            raise type(error)(
                f"{owner} could not continue {operation}. Retry this page or start a new load. {error}"
            ) from error

    @staticmethod
    def _encode_cursor(owner: str, operation: str, cursor: str | None) -> str | None:
        if cursor is None:
            return None
        payload = json.dumps(
            {"cursor": cursor, "operation": operation, "owner": owner},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return f"{_CURSOR_PREFIX}{encoded}"

    @staticmethod
    def _decode_cursor(cursor: str, expected_operation: str) -> tuple[str, str]:
        if not cursor.startswith(_CURSOR_PREFIX):
            raise ProviderResponseError(
                "This saved page cursor predates safe provider routing. Start a new load."
            )

        encoded = cursor.removeprefix(_CURSOR_PREFIX)
        try:
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderResponseError("The provider page cursor is invalid.") from error

        if not isinstance(payload, dict):
            raise ProviderResponseError("The provider page cursor is invalid.")
        owner = payload.get("owner")
        operation = payload.get("operation")
        raw_cursor = payload.get("cursor")
        if not isinstance(owner, str) or not owner:
            raise ProviderResponseError("The provider page cursor is invalid.")
        if not isinstance(operation, str) or not operation:
            raise ProviderResponseError("The provider page cursor is invalid.")
        if not isinstance(raw_cursor, str) or not raw_cursor:
            raise ProviderResponseError("The provider page cursor is invalid.")
        if operation != expected_operation:
            raise ProviderResponseError("The provider page cursor belongs to a different operation.")
        return owner, raw_cursor


class FailoverDataProvider(OrderedDataProviderPool):
    """Keep the old two-provider constructor for downstream compatibility."""

    def __init__(self, primary: DataProvider, fallback: DataProvider) -> None:
        super().__init__(
            (
                ProviderPoolEntry(provider_id="primary", provider=primary),
                ProviderPoolEntry(provider_id="fallback", provider=fallback),
            )
        )
