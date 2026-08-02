from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from oracle41_open.core.models import Chain, ProviderAuthError, ProviderError
from oracle41_open.providers.alchemy import AlchemyPricingProvider
from oracle41_open.providers.ankr import AnkrProvider

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class ProviderKeyValidationResult:
    provider: str
    is_valid: bool
    message: str


class ProviderKeyValidationService:
    def __init__(
        self,
        alchemy_probe: Callable[[str], None] | None = None,
        ankr_probe: Callable[[str], None] | None = None,
    ) -> None:
        self._alchemy_probe = alchemy_probe or _probe_alchemy_key
        self._ankr_probe = ankr_probe or _probe_ankr_key

    def validate_alchemy_key(self, key: str) -> ProviderKeyValidationResult:
        return self._validate(provider="Alchemy", raw_key=key, probe=self._alchemy_probe)

    def validate_ankr_key(self, key: str) -> ProviderKeyValidationResult:
        return self._validate(provider="Ankr", raw_key=key, probe=self._ankr_probe)

    def _validate(
        self,
        provider: str,
        raw_key: str,
        probe: Callable[[str], None],
    ) -> ProviderKeyValidationResult:
        trimmed = raw_key.strip()
        if not trimmed:
            return ProviderKeyValidationResult(
                provider=provider,
                is_valid=False,
                message=f"{provider} key is empty.",
            )

        try:
            probe(trimmed)
        except ProviderAuthError as error:
            return ProviderKeyValidationResult(
                provider=provider,
                is_valid=False,
                message=f"{provider} key rejected: {error}",
            )
        except ProviderError as error:
            return ProviderKeyValidationResult(
                provider=provider,
                is_valid=False,
                message=f"{provider} key validation failed: {error}",
            )
        except Exception as error:
            return ProviderKeyValidationResult(
                provider=provider,
                is_valid=False,
                message=f"{provider} key validation hit unexpected error: {error}",
            )

        return ProviderKeyValidationResult(
            provider=provider,
            is_valid=True,
            message=f"{provider} key validated.",
        )


def _probe_alchemy_key(key: str) -> None:
    provider = AlchemyPricingProvider(
        api_key=key,
        retry_attempts=1,
        retry_initial_delay_seconds=0.0,
        retry_max_delay_seconds=0.0,
    )
    _ = provider.get_simple_prices(["ETH"])


def _probe_ankr_key(key: str) -> None:
    provider = AnkrProvider(
        api_key=key,
        retry_attempts=1,
        retry_initial_delay_seconds=0.0,
        retry_max_delay_seconds=0.0,
    )
    _ = provider.get_native_balance(address=_ZERO_ADDRESS, chain=Chain.ETHEREUM)
