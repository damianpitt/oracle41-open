"""Collect exact-block Compound V3 Comet positions.

Each configured Comet market is collected independently. The service reads the base supply and
borrow balances, Compound's account safety checks, collateral configuration, and wallet collateral
balances. It saves progress after market discovery and after each collateral asset, so an interrupted
run can continue without repeating completed reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from oracle41_open.core.models import (
    Chain,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
    ProtocolCollectionCheckpoint,
    ProtocolRawEvidence,
    ProviderError,
    ProviderResponseError,
    ValidationError,
)
from oracle41_open.core.protocols import (
    CompoundV3Market,
    ProtocolAdapterRegistry,
    compound_v3_markets,
    production_protocol_registry,
)
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.protocol_collection import (
    ContractStateReader,
    ProtocolSnapshotRepository,
    SnapshotReadTracker,
    call_data,
    decode_call_result,
    evidence_values,
)

_MAX_COLLATERAL_ASSETS = 15
_ASSET_INFO_TYPE = "(uint8,address,address,uint64,uint64,uint64,uint64,uint128)"
_CONFIG_EVIDENCE_KIND = "compound_v3_collateral_config"


class CompoundV3PositionService:
    """Collect and store positions for the configured Comet markets on one chain."""

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
        """Collect every configured Compound V3 market on one chain."""
        return tuple(
            self.load_market_positions(
                wallet_address,
                market,
                block_number,
                force_refresh=force_refresh,
            )
            for market in compound_v3_markets(chain)
        )

    def load_market_positions(
        self,
        wallet_address: str,
        market: CompoundV3Market,
        block_number: int,
        force_refresh: bool = False,
    ) -> ProtocolAdapterResult:
        """Collect one Comet market, reusing stored state when possible."""
        wallet = AddressValidator.normalized(wallet_address)
        if not AddressValidator.is_valid(wallet):
            raise ValidationError("A valid hexadecimal wallet address is required.")
        if block_number < 0:
            raise ValidationError("Protocol snapshot block number must not be negative.")

        if self._repository is not None and not force_refresh:
            stored = self._repository.get_snapshot(
                wallet,
                market.chain,
                market.protocol_id,
                block_number,
            )
            if stored is not None:
                return stored.result

        checkpoint = self._load_checkpoint(wallet, market, block_number)
        if checkpoint is None:
            tracker = SnapshotReadTracker(self._provider, market.chain, block_number)
            reserves, evidence = self._discover_market(tracker, market, wallet)
            next_reserve_index = 0
            self._save_checkpoint(
                wallet,
                market,
                block_number,
                reserves,
                next_reserve_index,
                evidence,
                tracker,
            )
        else:
            tracker = SnapshotReadTracker(
                self._provider,
                market.chain,
                block_number,
                source_provider=checkpoint.source_provider,
                observed_at=checkpoint.observed_at,
            )
            reserves = checkpoint.reserves
            evidence = list(checkpoint.raw_evidence)
            next_reserve_index = checkpoint.next_reserve_index

        for reserve_index in range(next_reserve_index, len(reserves)):
            symbol, asset = reserves[reserve_index]
            evidence.extend(
                self._load_collateral_evidence(
                    tracker,
                    market,
                    wallet,
                    symbol,
                    asset,
                    evidence,
                )
            )
            self._save_checkpoint(
                wallet,
                market,
                block_number,
                reserves,
                reserve_index + 1,
                evidence,
                tracker,
            )

        result = self._registry.analyze(
            ProtocolAdapterContext(
                wallet_address=wallet,
                chain=market.chain,
                block_number=block_number,
                contract_addresses=(market.comet,),
                actions=(),
                token_balances=(),
                decoded_events=(),
                raw_evidence=tuple(evidence),
                source_provider=tracker.source_provider,
                observed_at=tracker.observed_at,
            )
        )
        if self._repository is not None:
            self._repository.save_snapshot(
                wallet,
                market.chain,
                market.protocol_id,
                block_number,
                result,
                tracker.source_provider,
                tracker.observed_at,
            )
        return result

    def _discover_market(
        self,
        tracker: SnapshotReadTracker,
        market: CompoundV3Market,
        wallet: str,
    ) -> tuple[tuple[tuple[str, str], ...], list[ProtocolRawEvidence]]:
        comet = market.comet
        base_contract = self._address_call(tracker, comet, "baseToken()", "Compound base token")
        base_scale = self._uint_call(tracker, comet, "baseScale()", "Compound base scale")
        supplied = self._uint_call(
            tracker,
            comet,
            "balanceOf(address)",
            "Compound base supply balance",
            ("address",),
            (wallet,),
        )
        borrowed = self._uint_call(
            tracker,
            comet,
            "borrowBalanceOf(address)",
            "Compound base borrow balance",
            ("address",),
            (wallet,),
        )
        collateralized = self._bool_call(
            tracker,
            comet,
            "isBorrowCollateralized(address)",
            "Compound collateralized check",
            wallet,
        )
        liquidatable = self._bool_call(
            tracker,
            comet,
            "isLiquidatable(address)",
            "Compound liquidatable check",
            wallet,
        )
        asset_count = self._uint_call(
            tracker,
            comet,
            "numAssets()",
            "Compound collateral asset count",
        )
        if asset_count > _MAX_COLLATERAL_ASSETS:
            raise ProviderResponseError(
                f"Compound returned more than {_MAX_COLLATERAL_ASSETS} collateral assets."
            )

        evidence = [
            ProtocolRawEvidence(
                kind="compound_v3_base_position",
                reference=f"eth_call:base-position:block:{tracker.block_number}",
                contract_address=comet,
                tx_hash=None,
                signature="balanceOf(address),borrowBalanceOf(address)",
                values=evidence_values(
                    {
                        "base_contract": base_contract,
                        "base_symbol": market.base_symbol,
                        "base_scale": str(base_scale),
                        "supplied_base": str(supplied),
                        "borrowed_base": str(borrowed),
                        "is_borrow_collateralized": str(collateralized).lower(),
                        "is_liquidatable": str(liquidatable).lower(),
                    }
                ),
            )
        ]
        reserves: list[tuple[str, str]] = []
        seen: set[str] = set()
        for asset_index in range(asset_count):
            info_result = tracker.read(
                comet,
                call_data("getAssetInfo(uint8)", ("uint8",), (asset_index,)),
            )
            info = cast(
                tuple[object, ...],
                decode_call_result(
                    info_result.data,
                    (_ASSET_INFO_TYPE,),
                    "Compound collateral configuration",
                )[0],
            )
            asset = AddressValidator.normalized(str(info[1]))
            price_feed = AddressValidator.normalized(str(info[2]))
            if (
                not AddressValidator.is_valid(asset)
                or not AddressValidator.is_valid(price_feed)
                or asset in seen
            ):
                raise ProviderResponseError(
                    "Compound returned an invalid or duplicate collateral asset."
                )
            seen.add(asset)
            symbol, issue = self._load_symbol(tracker, market, asset)
            if issue is not None:
                evidence.append(issue)
            evidence.append(
                ProtocolRawEvidence(
                    kind=_CONFIG_EVIDENCE_KIND,
                    reference=f"eth_call:getAssetInfo:block:{tracker.block_number}",
                    contract_address=comet,
                    tx_hash=None,
                    signature="getAssetInfo(uint8)",
                    values=evidence_values(
                        {
                            "asset_contract": asset,
                            "symbol": symbol,
                            "price_feed": price_feed,
                            "scale": str(info[3]),
                            "borrow_collateral_factor_wad": str(info[4]),
                            "liquidate_collateral_factor_wad": str(info[5]),
                            "liquidation_factor_wad": str(info[6]),
                            "supply_cap": str(info[7]),
                        }
                    ),
                )
            )
            reserves.append((symbol, asset))
        return tuple(reserves), evidence

    def _load_collateral_evidence(
        self,
        tracker: SnapshotReadTracker,
        market: CompoundV3Market,
        wallet: str,
        symbol: str,
        asset: str,
        evidence: list[ProtocolRawEvidence],
    ) -> tuple[ProtocolRawEvidence, ...]:
        config = next(
            (
                item
                for item in evidence
                if item.kind == _CONFIG_EVIDENCE_KIND
                and item.value("asset_contract") == asset
            ),
            None,
        )
        if config is None:
            raise ProviderResponseError("Compound checkpoint is missing collateral configuration.")
        try:
            result = tracker.read(
                market.comet,
                call_data(
                    "userCollateral(address,address)",
                    ("address", "address"),
                    (wallet, asset),
                ),
            )
            balance, reserved = decode_call_result(
                result.data,
                ("uint128", "uint128"),
                "Compound collateral balance",
            )
        except ProviderError as error:
            return (self._collection_issue(market, asset, "collateral balance", error),)

        values = {item.name: item.value for item in config.values}
        values.update(symbol=symbol, balance=str(balance), reserved=str(reserved))
        return (
            ProtocolRawEvidence(
                kind="compound_v3_collateral_position",
                reference=f"eth_call:userCollateral:block:{tracker.block_number}",
                contract_address=market.comet,
                tx_hash=None,
                signature="userCollateral(address,address)",
                values=evidence_values(values),
            ),
        )

    def _load_symbol(
        self,
        tracker: SnapshotReadTracker,
        market: CompoundV3Market,
        asset: str,
    ) -> tuple[str, ProtocolRawEvidence | None]:
        fallback = f"{asset[:8]}...{asset[-4:]}"
        try:
            result = tracker.read(asset, call_data("symbol()"))
            symbol = str(
                decode_call_result(result.data, ("string",), "Collateral token symbol")[0]
            ).strip()
            if not symbol:
                raise ProviderResponseError("Collateral token symbol is empty.")
            return symbol, None
        except ProviderError as error:
            return fallback, self._collection_issue(market, asset, "collateral symbol", error)

    @staticmethod
    def _address_call(
        tracker: SnapshotReadTracker,
        contract: str,
        signature: str,
        operation: str,
    ) -> str:
        result = tracker.read(contract, call_data(signature))
        address = AddressValidator.normalized(
            str(decode_call_result(result.data, ("address",), operation)[0])
        )
        if not AddressValidator.is_valid(address):
            raise ProviderResponseError(f"{operation} returned an invalid address.")
        return address

    @staticmethod
    def _uint_call(
        tracker: SnapshotReadTracker,
        contract: str,
        signature: str,
        operation: str,
        input_types: tuple[str, ...] = (),
        values: tuple[object, ...] = (),
    ) -> int:
        result = tracker.read(contract, call_data(signature, input_types, values))
        return cast(int, decode_call_result(result.data, ("uint256",), operation)[0])

    @staticmethod
    def _bool_call(
        tracker: SnapshotReadTracker,
        contract: str,
        signature: str,
        operation: str,
        wallet: str,
    ) -> bool:
        result = tracker.read(
            contract,
            call_data(signature, ("address",), (wallet,)),
        )
        return bool(decode_call_result(result.data, ("bool",), operation)[0])

    def _load_checkpoint(
        self,
        wallet: str,
        market: CompoundV3Market,
        block_number: int,
    ) -> ProtocolCollectionCheckpoint | None:
        if self._repository is None:
            return None
        return self._repository.get_checkpoint(
            wallet,
            market.chain,
            market.protocol_id,
            block_number,
        )

    def _save_checkpoint(
        self,
        wallet: str,
        market: CompoundV3Market,
        block_number: int,
        reserves: tuple[tuple[str, str], ...],
        next_reserve_index: int,
        evidence: list[ProtocolRawEvidence],
        tracker: SnapshotReadTracker,
    ) -> None:
        if self._repository is None:
            return
        self._repository.save_checkpoint(
            ProtocolCollectionCheckpoint(
                wallet_address=wallet,
                chain=market.chain,
                protocol_id=market.protocol_id,
                block_number=block_number,
                reserves=reserves,
                next_reserve_index=next_reserve_index,
                raw_evidence=tuple(evidence),
                source_provider=tracker.source_provider,
                observed_at=tracker.observed_at,
                updated_at=datetime.now(tz=UTC),
            )
        )

    @staticmethod
    def _collection_issue(
        market: CompoundV3Market,
        asset: str,
        stage: str,
        error: ProviderError,
    ) -> ProtocolRawEvidence:
        return ProtocolRawEvidence(
            kind="compound_v3_collection_issue",
            reference=f"eth_call:{stage.replace(' ', '_')}",
            contract_address=market.comet,
            tx_hash=None,
            signature=None,
            values=evidence_values(
                {
                    "asset_contract": asset,
                    "stage": stage,
                    "error_type": type(error).__name__,
                }
            ),
        )
