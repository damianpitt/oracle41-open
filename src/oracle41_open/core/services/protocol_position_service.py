"""Collect block-specific protocol evidence and normalize DeFi positions.

M6.3B uses standard read-only EVM calls to collect Aave V3 reserve and account snapshots.
Every call targets one explicit block and one provider. Failed optional calls become partial evidence;
reserve discovery remains mandatory because an incomplete reserve list could hide debt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, cast

from eth_abi.abi import decode as abi_decode
from eth_abi.abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils.crypto import keccak

from oracle41_open.core.models import (
    Chain,
    ContractReadResult,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
    ProtocolCollectionCheckpoint,
    ProtocolEvidenceValue,
    ProtocolRawEvidence,
    ProviderError,
    ProviderResponseError,
    StoredProtocolSnapshot,
    ValidationError,
)
from oracle41_open.core.protocols import (
    ProtocolAdapterRegistry,
    aave_v3_deployment,
    production_protocol_registry,
)
from oracle41_open.core.services.address_validator import AddressValidator

_MAX_AAVE_RESERVES = 128
_ZERO_ADDRESS = "0x" + "00" * 20
_AAVE_V3_PROTOCOL_ID = "aave-v3"


class ContractStateReader(Protocol):
    def read_contract(
        self,
        contract_address: str,
        call_data: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        ...


class ProtocolSnapshotRepository(Protocol):
    """Describe the durable methods used by protocol collection."""

    def get_snapshot(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
    ) -> StoredProtocolSnapshot | None:
        ...

    def get_checkpoint(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
    ) -> ProtocolCollectionCheckpoint | None:
        ...

    def save_checkpoint(self, checkpoint: ProtocolCollectionCheckpoint) -> None:
        ...

    def save_snapshot(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
        result: ProtocolAdapterResult,
        source_provider: str,
        observed_at: datetime,
    ) -> StoredProtocolSnapshot:
        ...


class ProtocolPositionService:
    """Load provider-neutral snapshots and pass them to the production registry."""

    def __init__(
        self,
        provider: ContractStateReader,
        registry: ProtocolAdapterRegistry | None = None,
        repository: ProtocolSnapshotRepository | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry or production_protocol_registry()
        self._repository = repository

    def load_aave_v3_positions(
        self,
        wallet_address: str,
        chain: Chain,
        block_number: int,
        force_refresh: bool = False,
    ) -> ProtocolAdapterResult:
        wallet = AddressValidator.normalized(wallet_address)
        if not AddressValidator.is_valid(wallet):
            raise ValidationError("A valid hexadecimal wallet address is required.")
        if block_number < 0:
            raise ValidationError("Protocol snapshot block number must not be negative.")

        if self._repository is not None and not force_refresh:
            stored = self._repository.get_snapshot(
                wallet,
                chain,
                _AAVE_V3_PROTOCOL_ID,
                block_number,
            )
            if stored is not None:
                return stored.result

        deployment = aave_v3_deployment(chain)
        checkpoint = self._load_checkpoint(wallet, chain, block_number)
        if checkpoint is None:
            tracker = _SnapshotReadTracker(self._provider, chain, block_number)
            reserves = self._load_reserves(tracker, deployment.protocol_data_provider)
            evidence: list[ProtocolRawEvidence] = []
            next_reserve_index = 0
            self._save_checkpoint(wallet, chain, block_number, reserves, 0, evidence, tracker)
        else:
            tracker = _SnapshotReadTracker(
                self._provider,
                chain,
                block_number,
                source_provider=checkpoint.source_provider,
                observed_at=checkpoint.observed_at,
            )
            reserves = checkpoint.reserves
            evidence = list(checkpoint.raw_evidence)
            next_reserve_index = checkpoint.next_reserve_index

        for reserve_index in range(next_reserve_index, len(reserves)):
            symbol, reserve = reserves[reserve_index]
            evidence.extend(
                self._load_reserve_evidence(
                    tracker,
                    deployment.protocol_data_provider,
                    symbol,
                    reserve,
                    wallet,
                )
            )
            self._save_checkpoint(
                wallet,
                chain,
                block_number,
                reserves,
                reserve_index + 1,
                evidence,
                tracker,
            )

        evidence.extend(
            self._load_account_evidence(
                tracker,
                deployment.pool_addresses_provider,
                deployment.pool,
                wallet,
            )
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
                _AAVE_V3_PROTOCOL_ID,
                block_number,
                result,
                tracker.source_provider,
                tracker.observed_at,
            )
        return result

    def _load_checkpoint(
        self,
        wallet: str,
        chain: Chain,
        block_number: int,
    ) -> ProtocolCollectionCheckpoint | None:
        if self._repository is None:
            return None
        return self._repository.get_checkpoint(
            wallet,
            chain,
            _AAVE_V3_PROTOCOL_ID,
            block_number,
        )

    def _save_checkpoint(
        self,
        wallet: str,
        chain: Chain,
        block_number: int,
        reserves: tuple[tuple[str, str], ...],
        next_reserve_index: int,
        evidence: list[ProtocolRawEvidence],
        tracker: _SnapshotReadTracker,
    ) -> None:
        if self._repository is None:
            return
        self._repository.save_checkpoint(
            ProtocolCollectionCheckpoint(
                wallet_address=wallet,
                chain=chain,
                protocol_id=_AAVE_V3_PROTOCOL_ID,
                block_number=block_number,
                reserves=reserves,
                next_reserve_index=next_reserve_index,
                raw_evidence=tuple(evidence),
                source_provider=tracker.source_provider,
                observed_at=tracker.observed_at,
                updated_at=datetime.now(tz=UTC),
            )
        )

    def _load_reserves(
        self,
        tracker: _SnapshotReadTracker,
        data_provider: str,
    ) -> tuple[tuple[str, str], ...]:
        result = tracker.read(data_provider, _call_data("getAllReservesTokens()"))
        decoded = _decode(result.data, ("(string,address)[]",), "Aave reserve list")
        raw_reserves = cast(tuple[tuple[str, str], ...], decoded[0])
        if len(raw_reserves) > _MAX_AAVE_RESERVES:
            raise ProviderResponseError(
                f"Aave returned more than {_MAX_AAVE_RESERVES} reserves; collection stopped."
            )

        reserves: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_symbol, raw_address in raw_reserves:
            symbol = str(raw_symbol).strip()
            address = AddressValidator.normalized(str(raw_address))
            if not symbol or not AddressValidator.is_valid(address) or address in seen:
                raise ProviderResponseError("Aave returned an invalid or duplicate reserve entry.")
            seen.add(address)
            reserves.append((symbol, address))
        return tuple(reserves)

    def _load_reserve_evidence(
        self,
        tracker: _SnapshotReadTracker,
        data_provider: str,
        symbol: str,
        reserve: str,
        wallet: str,
    ) -> tuple[ProtocolRawEvidence, ...]:
        values: dict[str, str] = {
            "reserve_contract": reserve,
            "symbol": symbol,
        }
        issues: list[ProtocolRawEvidence] = []

        try:
            user_result = tracker.read(
                data_provider,
                _call_data(
                    "getUserReserveData(address,address)",
                    ("address", "address"),
                    (reserve, wallet),
                ),
            )
            user_data = _decode(
                user_result.data,
                ("uint256",) * 7 + ("uint40", "bool"),
                "Aave user reserve data",
            )
            values.update(
                current_a_token_balance=str(user_data[0]),
                current_stable_debt=str(user_data[1]),
                current_variable_debt=str(user_data[2]),
                collateral_enabled=str(bool(user_data[8])).lower(),
            )
        except ProviderError as error:
            issues.append(_collection_issue(data_provider, reserve, "user reserve data", error))

        try:
            config_result = tracker.read(
                data_provider,
                _call_data(
                    "getReserveConfigurationData(address)",
                    ("address",),
                    (reserve,),
                ),
            )
            config = _decode(
                config_result.data,
                ("uint256",) * 5 + ("bool",) * 5,
                "Aave reserve configuration",
            )
            values["decimals"] = str(config[0])
        except ProviderError as error:
            issues.append(_collection_issue(data_provider, reserve, "reserve configuration", error))

        if _has_nonzero_position(values):
            try:
                token_result = tracker.read(
                    data_provider,
                    _call_data(
                        "getReserveTokensAddresses(address)",
                        ("address",),
                        (reserve,),
                    ),
                )
                token_addresses = _decode(
                    token_result.data,
                    ("address", "address", "address"),
                    "Aave reserve token addresses",
                )
                values.update(
                    a_token_contract=str(token_addresses[0]).lower(),
                    stable_debt_token_contract=str(token_addresses[1]).lower(),
                    variable_debt_token_contract=str(token_addresses[2]).lower(),
                )
            except ProviderError as error:
                issues.append(_collection_issue(data_provider, reserve, "reserve token addresses", error))

        evidence = ProtocolRawEvidence(
            kind="aave_v3_reserve_position",
            reference=f"eth_call:getUserReserveData:block:{tracker.block_number}",
            contract_address=data_provider,
            tx_hash=None,
            signature="getUserReserveData(address,address)",
            values=_evidence_values(values),
        )
        return (evidence, *issues)

    def _load_account_evidence(
        self,
        tracker: _SnapshotReadTracker,
        addresses_provider: str,
        pool: str,
        wallet: str,
    ) -> tuple[ProtocolRawEvidence, ...]:
        values: dict[str, str] = {}
        issues: list[ProtocolRawEvidence] = []
        try:
            account_result = tracker.read(
                pool,
                _call_data("getUserAccountData(address)", ("address",), (wallet,)),
            )
            account = _decode(
                account_result.data,
                ("uint256",) * 6,
                "Aave user account data",
            )
            values.update(
                total_collateral_base=str(account[0]),
                total_debt_base=str(account[1]),
                available_borrows_base=str(account[2]),
                liquidation_threshold_bps=str(account[3]),
                ltv_bps=str(account[4]),
                health_factor_wad=str(account[5]),
            )
        except ProviderError as error:
            issues.append(_collection_issue(pool, None, "account health data", error))

        try:
            oracle_result = tracker.read(
                addresses_provider,
                _call_data("getPriceOracle()"),
            )
            oracle = str(_decode(oracle_result.data, ("address",), "Aave oracle address")[0]).lower()
            if not AddressValidator.is_valid(oracle) or oracle == _ZERO_ADDRESS:
                raise ProviderResponseError("Aave returned an invalid price oracle address.")
            unit_result = tracker.read(oracle, _call_data("BASE_CURRENCY_UNIT()"))
            base_unit = _decode(unit_result.data, ("uint256",), "Aave base currency unit")[0]
            values["base_currency_unit"] = str(base_unit)
        except ProviderError as error:
            issues.append(_collection_issue(addresses_provider, None, "base currency unit", error))

        account_evidence = ProtocolRawEvidence(
            kind="aave_v3_account_data",
            reference=f"eth_call:getUserAccountData:block:{tracker.block_number}",
            contract_address=pool,
            tx_hash=None,
            signature="getUserAccountData(address)",
            values=_evidence_values(values),
        )
        return (account_evidence, *issues)


class _SnapshotReadTracker:
    """Require all successful calls in one snapshot to use the same provider."""

    def __init__(
        self,
        provider: ContractStateReader,
        chain: Chain,
        block_number: int,
        source_provider: str | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        self._provider = provider
        self._chain = chain
        self.block_number = block_number
        self._source_provider = source_provider
        self._observed_at = observed_at

    @property
    def source_provider(self) -> str:
        if self._source_provider is None:
            raise ProviderResponseError("Protocol snapshot did not complete any contract reads.")
        return self._source_provider

    @property
    def observed_at(self) -> datetime:
        if self._observed_at is None:
            raise ProviderResponseError("Protocol snapshot has no observation time.")
        return self._observed_at

    def read(self, contract: str, data: str) -> ContractReadResult:
        result = self._provider.read_contract(
            contract,
            data,
            self._chain,
            self.block_number,
        )
        if (
            result.chain is not self._chain
            or result.contract_address.lower() != contract.lower()
            or result.block_number != self.block_number
        ):
            raise ProviderResponseError("Contract provider returned a result outside the snapshot context.")
        if self._source_provider is not None and result.source_provider != self._source_provider:
            raise ProviderResponseError(
                "Protocol snapshot reads changed provider; retry to keep provenance consistent."
            )
        self._source_provider = result.source_provider
        if self._observed_at is None or result.fetched_at > self._observed_at:
            self._observed_at = result.fetched_at
        return result


def _call_data(
    signature: str,
    input_types: tuple[str, ...] = (),
    values: tuple[object, ...] = (),
) -> str:
    selector = keccak(text=signature)[:4]
    arguments = abi_encode(input_types, values) if input_types else b""
    return "0x" + (selector + arguments).hex()


def _decode(data: str, output_types: tuple[str, ...], operation: str) -> tuple[object, ...]:
    try:
        raw = bytes.fromhex(data.removeprefix("0x"))
        return tuple(abi_decode(output_types, raw, strict=True))
    except (DecodingError, ValueError) as error:
        raise ProviderResponseError(f"{operation} returned malformed ABI data.") from error


def _has_nonzero_position(values: dict[str, str]) -> bool:
    return any(
        int(values.get(name, "0")) > 0
        for name in (
            "current_a_token_balance",
            "current_stable_debt",
            "current_variable_debt",
        )
    )


def _collection_issue(
    contract: str,
    reserve: str | None,
    stage: str,
    error: ProviderError,
) -> ProtocolRawEvidence:
    values = {"stage": stage, "error_type": type(error).__name__}
    if reserve is not None:
        values["reserve_contract"] = reserve
    return ProtocolRawEvidence(
        kind="aave_v3_collection_issue",
        reference=f"eth_call:{stage.replace(' ', '_')}",
        contract_address=contract,
        tx_hash=None,
        signature=None,
        values=_evidence_values(values),
    )


def _evidence_values(values: dict[str, str]) -> tuple[ProtocolEvidenceValue, ...]:
    return tuple(
        ProtocolEvidenceValue(name=name, value=value)
        for name, value in sorted(values.items())
    )
