"""Manage read-only wallet watchlist entries.

The service validates addresses and labels before using the watchlist repository.
It stores public analysis targets only and never wallet credentials.
"""

from __future__ import annotations

from typing import Protocol

from oracle41_open.core.models import Chain, ValidationError, WatchlistEntry
from oracle41_open.core.services.address_validator import AddressValidator


class WatchlistRepository(Protocol):
    def upsert_entry(self, address: str, chain: Chain, label: str | None = None) -> WatchlistEntry:
        ...

    def get_entry(self, address: str, chain: Chain) -> WatchlistEntry | None:
        ...

    def list_entries(self, chain: Chain | None = None) -> list[WatchlistEntry]:
        ...

    def remove_entry(self, address: str, chain: Chain) -> bool:
        ...


class WatchlistService:
    def __init__(self, repository: WatchlistRepository) -> None:
        self._repository = repository

    def upsert_entry(self, address: str, chain: Chain, label: str | None = None) -> WatchlistEntry:
        normalized = self._normalize_address(address)
        return self._repository.upsert_entry(address=normalized, chain=chain, label=label)

    def get_entry(self, address: str, chain: Chain) -> WatchlistEntry | None:
        normalized = self._normalize_address(address)
        return self._repository.get_entry(address=normalized, chain=chain)

    def list_entries(self, chain: Chain | None = None) -> list[WatchlistEntry]:
        return self._repository.list_entries(chain=chain)

    def remove_entry(self, address: str, chain: Chain) -> bool:
        normalized = self._normalize_address(address)
        return self._repository.remove_entry(address=normalized, chain=chain)

    def _normalize_address(self, address: str) -> str:
        normalized = AddressValidator.normalized(address)
        if AddressValidator.is_valid(normalized):
            return normalized
        raise ValidationError(
            AddressValidator.validation_error(normalized)
            or "Invalid wallet address. Expected 0x + 40 hex characters."
        )
