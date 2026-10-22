"""Describe reviewed ERC-4626 vaults and normalize their read-only evidence.

The registry matches an exact network and vault contract, never a ticker or a guessed interface.
Positions contain the underlying asset once; shares and redemption checks stay in metadata.
Conversion values are accounting estimates, not executable withdrawal quotes or risk ratings.
"""

from __future__ import annotations

from dataclasses import dataclass

from oracle41_open.core.models import (
    Chain,
    ProtocolAdapterCapabilities,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
    ProtocolAdapterStatus,
    ProtocolAsset,
    ProtocolAssetRole,
    ProtocolContract,
    ProtocolPosition,
    ProtocolPositionCompleteness,
    ProtocolPositionKind,
    ProtocolPositionProvenance,
    ProtocolRiskSnapshot,
    ProtocolRiskState,
)


@dataclass(frozen=True)
class ERC4626Vault:
    """Pin a reviewed share contract to its expected underlying asset and public source."""

    chain: Chain
    vault_id: str
    name: str
    address: str
    asset_address: str
    share_symbol: str
    asset_symbol: str
    source_reference: str

    @property
    def protocol_id(self) -> str:
        return f"erc4626-{self.vault_id}"

    @property
    def adapter_id(self) -> str:
        return f"oracle41.erc4626.{self.chain.value}.{self.vault_id}"


# Official mainnet deployment linked from the issuer repository, reviewed 2026-10-23.
# Bridged share tokens are not assumed to implement the vault interface on their destination chain.
_VAULTS = (
    ERC4626Vault(
        chain=Chain.ETHEREUM,
        vault_id="sdai",
        name="Savings DAI",
        address="0x83f20f44975d03b1b09e64809b757c47f942beea",
        asset_address="0x6b175474e89094c44da98b954eedeac495271d0f",
        share_symbol="sDAI",
        asset_symbol="DAI",
        source_reference="https://github.com/sky-ecosystem/sdai#deployments",
    ),
)


def erc4626_vaults(chain: Chain | None = None) -> tuple[ERC4626Vault, ...]:
    """Return only explicitly reviewed deployments, optionally filtered by network."""
    return tuple(vault for vault in _VAULTS if chain is None or vault.chain is chain)


def erc4626_vault(chain: Chain, vault_id: str) -> ERC4626Vault:
    """Reject unknown deployments before issuing any contract reads."""
    for vault in erc4626_vaults(chain):
        if vault.vault_id == vault_id.strip().lower():
            return vault
    raise ValueError(f"ERC-4626 vault {vault_id!r} is not configured for {chain.display_name}.")


class ERC4626Adapter:
    """Build one underlying position from reviewed, block-specific vault evidence."""

    def __init__(self, vault: ERC4626Vault) -> None:
        self.vault = vault
        self._capabilities = ProtocolAdapterCapabilities(
            adapter_id=vault.adapter_id,
            adapter_version="1",
            protocol_id=vault.protocol_id,
            protocol_name=vault.name,
            chains=frozenset({vault.chain}),
            position_kinds=frozenset({ProtocolPositionKind.SUPPLIED}),
            contracts=(ProtocolContract(vault.chain, vault.address),),
        )

    @property
    def capabilities(self) -> ProtocolAdapterCapabilities:
        return self._capabilities

    def supports(self, context: ProtocolAdapterContext) -> bool:
        return context.chain is self.vault.chain and self.vault.address in {
            address.lower() for address in context.contract_addresses
        }

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        """Preserve source evidence and refuse missing or contradictory conversion inputs."""
        records = tuple(
            item
            for item in context.raw_evidence
            if item.kind == "erc4626_position"
            and item.contract_address is not None
            and item.contract_address.lower() == self.vault.address
        )
        warnings: list[str] = []
        positions: tuple[ProtocolPosition, ...] = ()
        provenance = ProtocolPositionProvenance(
            self.vault.adapter_id,
            "1",
            context.source_provider,
            f"{self.vault.source_reference}; eth_call:block:{context.block_number}",
            context.observed_at,
        )
        if len(records) != 1:
            warnings.append("Exactly one ERC-4626 position record is required.")
        else:
            record = records[0]
            values = {item.name: item.value for item in record.values}
            shares = _uint(values.get("raw_shares"))
            share_decimals = _uint(values.get("share_decimals"), maximum=255)
            asset_decimals = _uint(values.get("asset_decimals"), maximum=255)
            amount = _uint(values.get("converted_assets"))
            preview = _uint(values.get("preview_redeem_assets"))
            maximum = _uint(values.get("max_redeem_shares"))
            if (
                shares is None
                or share_decimals is None
                or asset_decimals is None
                or not _same_address(values.get("vault_contract"), self.vault.address)
                or not _same_address(values.get("asset_contract"), self.vault.asset_address)
            ):
                warnings.append("ERC-4626 share or underlying-asset evidence is invalid.")
            else:
                for issue in context.raw_evidence:
                    if (
                        issue.kind == "erc4626_collection_issue"
                        and issue.contract_address == self.vault.address
                    ):
                        warnings.append(
                            f"ERC-4626 {issue.value('stage')} unavailable "
                            f"({issue.value('error_type')})."
                        )
                if shares > 0:
                    if amount is None:
                        warnings.append("ERC-4626 underlying conversion is unavailable.")
                    if preview is None:
                        warnings.append("ERC-4626 redemption preview is unavailable.")
                    if maximum is None or maximum > shares:
                        warnings.append("ERC-4626 redemption limit is missing or invalid.")
                    elif maximum < shares:
                        warnings.append("The vault currently limits redemption of these shares.")
                    # Shares remain metadata: pricing both shares and assets would count value twice.
                    assets = (
                        ()
                        if amount is None
                        else (
                            ProtocolAsset(
                                ProtocolAssetRole.UNDERLYING,
                                "ERC-20",
                                self.vault.asset_address,
                                self.vault.asset_symbol,
                                None,
                                str(amount),
                                asset_decimals,
                            ),
                        )
                    )
                    positions = (
                        ProtocolPosition(
                            schema_version=1,
                            position_id=f"{context.chain.value}:{self.vault.protocol_id}:"
                            f"{context.wallet_address.lower()}",
                            wallet_address=context.wallet_address.lower(),
                            chain=context.chain,
                            block_number=context.block_number,
                            protocol_id=self.vault.protocol_id,
                            protocol_name=self.vault.name,
                            kind=ProtocolPositionKind.SUPPLIED,
                            label=f"{self.vault.name} vault underlying {self.vault.asset_symbol}",
                            assets=assets,
                            contract_addresses=(self.vault.address, self.vault.asset_address),
                            completeness=ProtocolPositionCompleteness.PARTIAL
                            if warnings
                            else ProtocolPositionCompleteness.COMPLETE,
                            warnings=tuple(warnings),
                            provenance=provenance,
                            metadata=record.values,
                        ),
                    )
        return ProtocolAdapterResult(
            schema_version=1,
            status=ProtocolAdapterStatus.PARTIAL if warnings else ProtocolAdapterStatus.MATCHED,
            adapter_id=self.vault.adapter_id,
            adapter_version="1",
            protocol_id=self.vault.protocol_id,
            protocol_name=self.vault.name,
            positions=positions,
            source_actions=context.actions,
            source_balances=context.token_balances,
            source_events=context.decoded_events,
            raw_evidence=context.raw_evidence,
            warnings=tuple(warnings),
            risk_snapshot=ProtocolRiskSnapshot(
                wallet_address=context.wallet_address.lower(),
                chain=context.chain,
                block_number=context.block_number,
                protocol_id=self.vault.protocol_id,
                total_collateral_base=None,
                total_debt_base=None,
                available_borrow_base=None,
                liquidation_threshold_bps=None,
                ltv_bps=None,
                health_factor_wad=None,
                base_currency_unit=None,
                state=ProtocolRiskState.NOT_APPLICABLE,
                provenance=provenance,
            ),
        )


def _uint(value: str | None, maximum: int = 2**256 - 1) -> int | None:
    """Accept raw integer text only, without silently rounding or accepting signs."""
    if value is None or len(value) > 78 or not value.isascii() or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed <= maximum else None


def _same_address(value: str | None, expected: str) -> bool:
    return value is not None and value.lower() == expected
