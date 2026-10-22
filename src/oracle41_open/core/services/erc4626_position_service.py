"""Collect exact-block ERC-4626 holdings from explicitly reviewed vaults.

The collector verifies the underlying asset before reading a wallet's shares and conversion value.
Redemption previews and limits are kept separate from the accounting conversion. Finished results
reuse the existing SQLite protocol snapshots; failed mandatory reads never replace a saved result.
No method constructs a deposit, withdrawal, approval, or other transaction.
"""

from __future__ import annotations

from typing import cast

from oracle41_open.core.models import (
    Chain,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
    ProtocolRawEvidence,
    ProviderError,
    ProviderResponseError,
    ValidationError,
)
from oracle41_open.core.protocols.erc4626 import ERC4626Vault, erc4626_vault, erc4626_vaults
from oracle41_open.core.protocols.registry import (
    ProtocolAdapterRegistry,
    production_protocol_registry,
)
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.protocol_collection import (
    ContractStateReader,
    ProtocolSnapshotRepository,
    SnapshotContextError,
    SnapshotReadTracker,
    call_data,
    decode_call_result,
    evidence_values,
)


class ERC4626PositionService:
    """Collect configured vaults through the shared historical JSON-RPC reader."""

    def __init__(
        self,
        provider: ContractStateReader,
        registry: ProtocolAdapterRegistry | None = None,
        repository: ProtocolSnapshotRepository | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry or production_protocol_registry()
        self._repository = repository

    def load_positions(
        self,
        wallet_address: str,
        chain: Chain,
        block_number: int,
        force_refresh: bool = False,
    ) -> tuple[ProtocolAdapterResult, ...]:
        """Read only vaults listed for this chain; no token-name or interface discovery."""
        return tuple(
            self.load_vault_positions(
                wallet_address, chain, vault.vault_id, block_number, force_refresh
            )
            for vault in erc4626_vaults(chain)
        )

    def load_vault_positions(
        self,
        wallet_address: str,
        chain: Chain,
        vault_id: str,
        block_number: int,
        force_refresh: bool = False,
    ) -> ProtocolAdapterResult:
        """Read one deployment at one block, or reuse a finished snapshot of that block."""
        wallet = AddressValidator.normalized(wallet_address)
        if not AddressValidator.is_valid(wallet):
            raise ValidationError("A valid hexadecimal wallet address is required.")
        if block_number < 0:
            raise ValidationError("Protocol snapshot block number must not be negative.")
        vault = erc4626_vault(chain, vault_id)
        if self._repository is not None and not force_refresh:
            stored = self._repository.get_snapshot(wallet, chain, vault.protocol_id, block_number)
            if stored is not None:
                return stored.result

        tracker = SnapshotReadTracker(self._provider, chain, block_number)
        asset = str(self._read(tracker, vault.address, "asset()", "address")).lower()
        if asset != vault.asset_address:
            raise ProviderResponseError(
                "ERC-4626 underlying asset does not match the reviewed registry."
            )
        shares = cast(
            int,
            self._read(
                tracker,
                vault.address,
                "balanceOf(address)",
                "uint256",
                ("address",),
                (wallet,),
            ),
        )
        share_decimals = self._read(tracker, vault.address, "decimals()", "uint8")
        asset_decimals = self._read(tracker, asset, "decimals()", "uint8")
        values = {
            "vault_contract": vault.address,
            "asset_contract": asset,
            "share_symbol": vault.share_symbol,
            "asset_symbol": vault.asset_symbol,
            "raw_shares": str(shares),
            "share_decimals": str(share_decimals),
            "asset_decimals": str(asset_decimals),
            "valuation_method": "convertToAssets",
            "source_reference": vault.source_reference,
            "valuation_note": "Accounting estimate; not a withdrawal quote or guaranteed liquidity.",
        }
        issues: list[ProtocolRawEvidence] = []
        if shares > 0:
            # A failed conversion is unknown, never a fabricated zero asset balance.
            for signature, argument, input_type, key in (
                ("convertToAssets(uint256)", shares, "uint256", "converted_assets"),
                ("previewRedeem(uint256)", shares, "uint256", "preview_redeem_assets"),
                ("maxRedeem(address)", wallet, "address", "max_redeem_shares"),
            ):
                try:
                    values[key] = str(
                        self._read(
                            tracker,
                            vault.address,
                            signature,
                            "uint256",
                            (input_type,),
                            (argument,),
                        )
                    )
                except SnapshotContextError:
                    # Context/provider mismatches must never be hidden as optional call failures.
                    raise
                except ProviderError as error:
                    issues.append(self._issue(vault, signature, error))
        evidence = ProtocolRawEvidence(
            kind="erc4626_position",
            reference=f"eth_call:erc4626:block:{block_number}",
            contract_address=vault.address,
            tx_hash=None,
            signature="asset(),balanceOf(address),convertToAssets(uint256)",
            values=evidence_values(values),
        )
        result = self._registry.analyze(
            ProtocolAdapterContext(
                wallet_address=wallet,
                chain=chain,
                block_number=block_number,
                contract_addresses=(vault.address,),
                actions=(),
                token_balances=(),
                decoded_events=(),
                raw_evidence=(evidence, *issues),
                source_provider=tracker.source_provider,
                observed_at=tracker.observed_at,
            )
        )
        if self._repository is not None:
            self._repository.save_snapshot(
                wallet,
                chain,
                vault.protocol_id,
                block_number,
                result,
                tracker.source_provider,
                tracker.observed_at,
            )
        return result

    @staticmethod
    def _read(
        tracker: SnapshotReadTracker,
        address: str,
        signature: str,
        output_type: str,
        input_types: tuple[str, ...] = (),
        arguments: tuple[object, ...] = (),
    ) -> object:
        result = tracker.read(address, call_data(signature, input_types, arguments))
        return decode_call_result(result.data, (output_type,), f"ERC-4626 {signature}")[0]

    @staticmethod
    def _issue(vault: ERC4626Vault, signature: str, error: ProviderError) -> ProtocolRawEvidence:
        return ProtocolRawEvidence(
            kind="erc4626_collection_issue",
            reference=f"eth_call:{signature}",
            contract_address=vault.address,
            tx_hash=None,
            signature=signature,
            values=evidence_values({"stage": signature, "error_type": type(error).__name__}),
        )
