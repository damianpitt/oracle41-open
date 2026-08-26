"""Exercise one bounded page of every wallet-data provider operation.

This service supports explicit live validation without logging wallet results or credentials.
It checks normalized response ownership and chain identity after each provider call.
"""

from __future__ import annotations

from dataclasses import dataclass

from oracle41_open.core.models import Chain, ProviderResponseError
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.providers.data_provider import DataProvider

_OPERATIONS = (
    "native balance",
    "token balances",
    "wallet activity",
    "token history",
)


@dataclass(frozen=True)
class ProviderLiveValidationReport:
    """Confirm completed operations without retaining returned wallet data."""

    provider_id: str
    chain: Chain
    operations: tuple[str, ...]


class ProviderLiveValidationService:
    """Run the shared wallet-data contract against one live adapter."""

    def validate(
        self,
        provider_id: str,
        provider: DataProvider,
        wallet_address: str,
        token_address: str,
        chain: Chain,
    ) -> ProviderLiveValidationReport:
        wallet = _validated_address(wallet_address, "wallet")
        token = _validated_address(token_address, "token")

        # Keep every request bounded to its first page to control API use.
        provider.get_native_balance(wallet, chain)
        balances = provider.get_token_balances(wallet, chain)
        activity = provider.get_activity(wallet, chain)
        token_history = provider.get_token_transfers(
            wallet,
            token,
            chain,
            include_approvals=False,
        )

        for source in (
            balances.source_provider,
            activity.source_provider,
            token_history.source_provider,
        ):
            if source != provider_id:
                raise ProviderResponseError(
                    f"{provider_id} returned unexpected source provenance."
                )
        for item in (*activity.items, *token_history.items):
            if item.chain is not chain:
                raise ProviderResponseError(
                    f"{provider_id} returned activity for an unexpected chain."
                )
        return ProviderLiveValidationReport(
            provider_id=provider_id,
            chain=chain,
            operations=_OPERATIONS,
        )


def _validated_address(value: str, label: str) -> str:
    normalized = AddressValidator.normalized(value)
    if not AddressValidator.is_valid(normalized):
        raise ValueError(
            f"Live validation {label} address must use 0x and 40 hex characters."
        )
    return normalized
