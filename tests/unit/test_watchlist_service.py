"""Test watchlist validation and service behavior.

The cases cover add, update, list, remove, labels, duplicate wallets, and invalid addresses.
They confirm watchlists remain read-only public wallet records.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle41_open.core.models import Chain, ValidationError
from oracle41_open.core.services.watchlist_service import WatchlistService
from oracle41_open.storage.db import SQLiteDatabase, WatchlistRepository


def test_watchlist_service_upsert_list_get_and_remove(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    repository = WatchlistRepository(database)
    service = WatchlistService(repository=repository)
    address = "0x1111111111111111111111111111111111111111"

    first = service.upsert_entry(address=address.upper(), chain=Chain.ETHEREUM, label=" Main ")
    second = service.upsert_entry(address=address, chain=Chain.ETHEREUM, label="Treasury")

    assert first.address == address
    assert second.id == first.id
    assert second.label == "Treasury"

    loaded = service.get_entry(address=address, chain=Chain.ETHEREUM)
    assert loaded is not None
    assert loaded.id == second.id

    listed = service.list_entries(chain=Chain.ETHEREUM)
    assert [entry.address for entry in listed] == [address]

    assert service.remove_entry(address=address, chain=Chain.ETHEREUM)
    assert not service.remove_entry(address=address, chain=Chain.ETHEREUM)
    assert service.get_entry(address=address, chain=Chain.ETHEREUM) is None


def test_watchlist_service_rejects_invalid_wallet_address(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    repository = WatchlistRepository(database)
    service = WatchlistService(repository=repository)

    with pytest.raises(ValidationError, match="Invalid wallet address"):
        service.upsert_entry(address="0x1234", chain=Chain.ETHEREUM, label="invalid")
