"""Test SQLite contract ABI and proxy storage.

The cases cover ABI replacement, listing, deletion, provenance, and block-specific proxy lookup.
They protect the schema-v5 persistence contract.
"""

from datetime import UTC, datetime
from pathlib import Path

from oracle41_open.core.models import (
    Chain,
    ContractABIRecord,
    ProxyKind,
    ProxyResolution,
    ProxyResolutionStatus,
    SignatureProvenance,
    SignatureSourceKind,
)
from oracle41_open.storage.db import ContractABIRepository, SQLiteDatabase

_ADDRESS = "0x1111111111111111111111111111111111111111"
_IMPLEMENTATION = "0x2222222222222222222222222222222222222222"
_NOW = datetime(2026, 8, 12, tzinfo=UTC)


def test_contract_abi_repository_roundtrip_replace_and_delete(tmp_path: Path) -> None:
    repository = ContractABIRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    first = _abi_record(content_hash="a" * 64, contract_name="Vault v1")
    replacement = _abi_record(content_hash="b" * 64, contract_name="Vault v2")

    repository.upsert_contract_abi(first)
    repository.upsert_contract_abi(replacement)

    assert repository.get_contract_abi(Chain.ETHEREUM, _ADDRESS) == replacement
    assert repository.list_contract_abis() == (replacement,)
    assert repository.delete_contract_abi(Chain.ETHEREUM, _ADDRESS)
    assert repository.get_contract_abi(Chain.ETHEREUM, _ADDRESS) is None


def test_proxy_resolution_repository_is_block_specific(tmp_path: Path) -> None:
    repository = ContractABIRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    resolution = ProxyResolution(
        chain=Chain.ETHEREUM,
        proxy_address=_ADDRESS,
        status=ProxyResolutionStatus.RESOLVED,
        proxy_kind=ProxyKind.EIP_1967,
        implementation_address=_IMPLEMENTATION,
        block_number=24_000_000,
        source_provider="test-rpc",
        resolved_at=_NOW,
    )

    repository.save_proxy_resolution(resolution)

    assert repository.get_proxy_resolution(Chain.ETHEREUM, _ADDRESS, 24_000_000) == resolution
    assert repository.get_proxy_resolution(Chain.ETHEREUM, _ADDRESS, 24_000_001) is None


def _abi_record(content_hash: str, contract_name: str) -> ContractABIRecord:
    provenance = SignatureProvenance(
        source_id=f"test:{content_hash[:4]}",
        source_name="Test ABI",
        source_kind=SignatureSourceKind.USER_ABI,
        version="1",
        is_verified=False,
    )
    return ContractABIRecord(
        chain=Chain.ETHEREUM,
        contract_address=_ADDRESS,
        contract_name=contract_name,
        abi_json="[]",
        content_hash=content_hash,
        provenance=provenance,
        imported_at=_NOW,
    )
