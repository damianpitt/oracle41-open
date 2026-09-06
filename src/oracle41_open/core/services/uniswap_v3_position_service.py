"""Collect exact-block Uniswap V3 concentrated-liquidity positions.

The service enumerates position-manager NFTs owned directly by a wallet, then reads each NFT and
its pool at one block. It records pool fee growth, boundary ticks, token metadata, and principal
state for deterministic normalization. Checkpoints are saved after discovery and after each NFT.
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
    ProtocolAdapterRegistry,
    production_protocol_registry,
    uniswap_v3_deployment,
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

_PROTOCOL_ID = "uniswap-v3"
_MAX_OWNED_POSITIONS = 512
_ZERO_ADDRESS = "0x" + "00" * 20
_POSITION_OUTPUT_TYPES = (
    "uint96",
    "address",
    "address",
    "address",
    "uint24",
    "int24",
    "int24",
    "uint128",
    "uint256",
    "uint256",
    "uint128",
    "uint128",
)
_TICK_OUTPUT_TYPES = (
    "uint128",
    "int128",
    "uint256",
    "uint256",
    "int56",
    "uint160",
    "uint32",
    "bool",
)


class UniswapV3PositionService:
    """Collect and store all directly owned Uniswap V3 position NFTs."""

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
    ) -> ProtocolAdapterResult:
        """Load one wallet snapshot, reusing a finished exact-block result when possible."""
        wallet = AddressValidator.normalized(wallet_address)
        if not AddressValidator.is_valid(wallet):
            raise ValidationError("A valid hexadecimal wallet address is required.")
        if block_number < 0:
            raise ValidationError("Protocol snapshot block number must not be negative.")

        if self._repository is not None and not force_refresh:
            stored = self._repository.get_snapshot(wallet, chain, _PROTOCOL_ID, block_number)
            if stored is not None:
                return stored.result

        deployment = uniswap_v3_deployment(chain)
        checkpoint = self._load_checkpoint(wallet, chain, block_number)
        if checkpoint is None:
            tracker = SnapshotReadTracker(self._provider, chain, block_number)
            position_ids = self._discover_position_ids(
                tracker,
                deployment.position_manager,
                wallet,
            )
            reserves = tuple((str(token_id), deployment.position_manager) for token_id in position_ids)
            evidence = [
                ProtocolRawEvidence(
                    kind="uniswap_v3_collection",
                    reference=f"eth_call:owned-positions:block:{block_number}",
                    contract_address=deployment.position_manager,
                    tx_hash=None,
                    signature="balanceOf(address),tokenOfOwnerByIndex(address,uint256)",
                    values=evidence_values(
                        {
                            "position_count": str(len(position_ids)),
                            "token_ids": ",".join(str(item) for item in position_ids),
                        }
                    ),
                )
            ]
            next_index = 0
            self._save_checkpoint(wallet, chain, block_number, reserves, next_index, evidence, tracker)
        else:
            tracker = SnapshotReadTracker(
                self._provider,
                chain,
                block_number,
                source_provider=checkpoint.source_provider,
                observed_at=checkpoint.observed_at,
            )
            reserves = checkpoint.reserves
            evidence = list(checkpoint.raw_evidence)
            next_index = checkpoint.next_reserve_index

        token_metadata = self._known_token_metadata(evidence)
        for index in range(next_index, len(reserves)):
            token_id = int(reserves[index][0])
            evidence.extend(
                self._load_position_evidence(
                    tracker,
                    deployment.factory,
                    deployment.position_manager,
                    token_id,
                    token_metadata,
                )
            )
            self._save_checkpoint(
                wallet,
                chain,
                block_number,
                reserves,
                index + 1,
                evidence,
                tracker,
            )

        result = self._registry.analyze(
            ProtocolAdapterContext(
                wallet_address=wallet,
                chain=chain,
                block_number=block_number,
                contract_addresses=deployment.contracts,
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
                chain,
                _PROTOCOL_ID,
                block_number,
                result,
                tracker.source_provider,
                tracker.observed_at,
            )
        return result

    def _discover_position_ids(
        self,
        tracker: SnapshotReadTracker,
        manager: str,
        wallet: str,
    ) -> tuple[int, ...]:
        balance_result = tracker.read(
            manager,
            call_data("balanceOf(address)", ("address",), (wallet,)),
        )
        count = cast(
            int,
            decode_call_result(
                balance_result.data,
                ("uint256",),
                "Uniswap V3 owned-position count",
            )[0],
        )
        if count > _MAX_OWNED_POSITIONS:
            raise ProviderResponseError(
                f"Uniswap V3 returned more than {_MAX_OWNED_POSITIONS} owned positions."
            )

        token_ids = []
        seen: set[int] = set()
        for index in range(count):
            result = tracker.read(
                manager,
                call_data(
                    "tokenOfOwnerByIndex(address,uint256)",
                    ("address", "uint256"),
                    (wallet, index),
                ),
            )
            token_id = cast(
                int,
                decode_call_result(
                    result.data,
                    ("uint256",),
                    "Uniswap V3 owned-position token ID",
                )[0],
            )
            if token_id in seen:
                raise ProviderResponseError("Uniswap V3 returned a duplicate position token ID.")
            seen.add(token_id)
            token_ids.append(token_id)
        return tuple(token_ids)

    def _load_position_evidence(
        self,
        tracker: SnapshotReadTracker,
        factory: str,
        manager: str,
        token_id: int,
        token_metadata: dict[str, tuple[str, int]],
    ) -> tuple[ProtocolRawEvidence, ...]:
        try:
            result = tracker.read(
                manager,
                call_data("positions(uint256)", ("uint256",), (token_id,)),
            )
            position = decode_call_result(
                result.data,
                _POSITION_OUTPUT_TYPES,
                "Uniswap V3 position",
            )
            token0 = self._valid_address(position[2], "Uniswap V3 token0")
            token1 = self._valid_address(position[3], "Uniswap V3 token1")
            fee = cast(int, position[4])
            pool = self._pool_address(tracker, factory, token0, token1, fee)
            pool_values = self._pool_values(
                tracker,
                pool,
                cast(int, position[5]),
                cast(int, position[6]),
            )
        except ProviderError as error:
            return (self._collection_issue(manager, token_id, "position state", error),)

        values = {
            "token_id": str(token_id),
            "token0": token0,
            "token1": token1,
            "fee": str(fee),
            "tick_lower": str(position[5]),
            "tick_upper": str(position[6]),
            "liquidity": str(position[7]),
            "fee_growth_inside0_last_x128": str(position[8]),
            "fee_growth_inside1_last_x128": str(position[9]),
            "tokens_owed0": str(position[10]),
            "tokens_owed1": str(position[11]),
            "pool": pool,
            **pool_values,
        }
        issues: list[ProtocolRawEvidence] = []
        for token_index, token in enumerate((token0, token1)):
            cached = token_metadata.get(token)
            if cached is None:
                symbol, symbol_issue = self._token_symbol(tracker, manager, token_id, token)
                decimals, decimals_issue = self._token_decimals(
                    tracker, manager, token_id, token
                )
                if symbol_issue is None and decimals_issue is None and decimals is not None:
                    token_metadata[token] = (symbol, decimals)
            else:
                symbol, decimals = cached
                symbol_issue = None
                decimals_issue = None
            values[f"symbol{token_index}"] = symbol
            if decimals is not None:
                values[f"decimals{token_index}"] = str(decimals)
            if symbol_issue is not None:
                issues.append(symbol_issue)
            if decimals_issue is not None:
                issues.append(decimals_issue)

        return (
            ProtocolRawEvidence(
                kind="uniswap_v3_position",
                reference=f"eth_call:positions:block:{tracker.block_number}:token:{token_id}",
                contract_address=manager,
                tx_hash=None,
                signature="positions(uint256)",
                values=evidence_values(values),
            ),
            *issues,
        )

    @staticmethod
    def _known_token_metadata(
        evidence: list[ProtocolRawEvidence],
    ) -> dict[str, tuple[str, int]]:
        """Reuse complete token metadata already present in a resumed checkpoint."""
        metadata: dict[str, tuple[str, int]] = {}
        for item in evidence:
            if item.kind != "uniswap_v3_position":
                continue
            for index in (0, 1):
                token = AddressValidator.normalized(item.value(f"token{index}") or "")
                symbol = (item.value(f"symbol{index}") or "").strip()
                try:
                    decimals = int(item.value(f"decimals{index}") or "")
                except ValueError:
                    continue
                if AddressValidator.is_valid(token) and symbol and 0 <= decimals <= 255:
                    metadata[token] = (symbol, decimals)
        return metadata

    def _pool_address(
        self,
        tracker: SnapshotReadTracker,
        factory: str,
        token0: str,
        token1: str,
        fee: int,
    ) -> str:
        result = tracker.read(
            factory,
            call_data(
                "getPool(address,address,uint24)",
                ("address", "address", "uint24"),
                (token0, token1, fee),
            ),
        )
        pool = self._valid_address(
            decode_call_result(result.data, ("address",), "Uniswap V3 pool address")[0],
            "Uniswap V3 pool",
        )
        if pool == _ZERO_ADDRESS:
            raise ProviderResponseError("Uniswap V3 position points to no deployed pool.")
        return pool

    def _pool_values(
        self,
        tracker: SnapshotReadTracker,
        pool: str,
        tick_lower: int,
        tick_upper: int,
    ) -> dict[str, str]:
        slot0 = decode_call_result(
            tracker.read(pool, call_data("slot0()")).data,
            ("uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"),
            "Uniswap V3 slot0",
        )
        global0 = self._uint_call(tracker, pool, "feeGrowthGlobal0X128()")
        global1 = self._uint_call(tracker, pool, "feeGrowthGlobal1X128()")
        lower = self._tick_call(tracker, pool, tick_lower)
        upper = self._tick_call(tracker, pool, tick_upper)
        return {
            "sqrt_price_x96": str(slot0[0]),
            "current_tick": str(slot0[1]),
            "fee_growth_global0_x128": str(global0),
            "fee_growth_global1_x128": str(global1),
            "fee_growth_outside0_lower_x128": str(lower[2]),
            "fee_growth_outside1_lower_x128": str(lower[3]),
            "fee_growth_outside0_upper_x128": str(upper[2]),
            "fee_growth_outside1_upper_x128": str(upper[3]),
        }

    @staticmethod
    def _uint_call(tracker: SnapshotReadTracker, contract: str, signature: str) -> int:
        result = tracker.read(contract, call_data(signature))
        return cast(
            int,
            decode_call_result(result.data, ("uint256",), f"Uniswap V3 {signature}")[0],
        )

    @staticmethod
    def _tick_call(
        tracker: SnapshotReadTracker,
        pool: str,
        tick: int,
    ) -> tuple[object, ...]:
        result = tracker.read(pool, call_data("ticks(int24)", ("int24",), (tick,)))
        decoded = decode_call_result(result.data, _TICK_OUTPUT_TYPES, "Uniswap V3 tick state")
        if not bool(decoded[7]):
            raise ProviderResponseError("Uniswap V3 position boundary tick is not initialized.")
        return decoded

    def _token_symbol(
        self,
        tracker: SnapshotReadTracker,
        manager: str,
        token_id: int,
        token: str,
    ) -> tuple[str, ProtocolRawEvidence | None]:
        fallback = f"{token[:8]}...{token[-4:]}"
        try:
            result = tracker.read(token, call_data("symbol()"))
            symbol = str(decode_call_result(result.data, ("string",), "Token symbol")[0]).strip()
            if not symbol:
                raise ProviderResponseError("Token symbol is empty.")
            return symbol, None
        except ProviderError as error:
            return fallback, self._collection_issue(manager, token_id, "token symbol", error)

    def _token_decimals(
        self,
        tracker: SnapshotReadTracker,
        manager: str,
        token_id: int,
        token: str,
    ) -> tuple[int | None, ProtocolRawEvidence | None]:
        try:
            result = tracker.read(token, call_data("decimals()"))
            decimals = cast(int, decode_call_result(result.data, ("uint8",), "Token decimals")[0])
            return decimals, None
        except ProviderError as error:
            return None, self._collection_issue(manager, token_id, "token decimals", error)

    @staticmethod
    def _valid_address(value: object, operation: str) -> str:
        address = AddressValidator.normalized(str(value))
        if not AddressValidator.is_valid(address):
            raise ProviderResponseError(f"{operation} returned an invalid address.")
        return address

    def _load_checkpoint(
        self,
        wallet: str,
        chain: Chain,
        block_number: int,
    ) -> ProtocolCollectionCheckpoint | None:
        if self._repository is None:
            return None
        return self._repository.get_checkpoint(wallet, chain, _PROTOCOL_ID, block_number)

    def _save_checkpoint(
        self,
        wallet: str,
        chain: Chain,
        block_number: int,
        reserves: tuple[tuple[str, str], ...],
        next_index: int,
        evidence: list[ProtocolRawEvidence],
        tracker: SnapshotReadTracker,
    ) -> None:
        if self._repository is None:
            return
        self._repository.save_checkpoint(
            ProtocolCollectionCheckpoint(
                wallet_address=wallet,
                chain=chain,
                protocol_id=_PROTOCOL_ID,
                block_number=block_number,
                reserves=reserves,
                next_reserve_index=next_index,
                raw_evidence=tuple(evidence),
                source_provider=tracker.source_provider,
                observed_at=tracker.observed_at,
                updated_at=datetime.now(tz=UTC),
            )
        )

    @staticmethod
    def _collection_issue(
        manager: str,
        token_id: int,
        stage: str,
        error: ProviderError,
    ) -> ProtocolRawEvidence:
        return ProtocolRawEvidence(
            kind="uniswap_v3_collection_issue",
            reference=f"eth_call:{stage.replace(' ', '_')}",
            contract_address=manager,
            tx_hash=None,
            signature=None,
            values=evidence_values(
                {
                    "token_id": str(token_id),
                    "stage": stage,
                    "error_type": type(error).__name__,
                }
            ),
        )
