"""Run opt-in live wallet-data checks for all four providers.

The command reads credentials and public test addresses only from environment variables.
It refuses to run without explicit opt-in and never prints credentials, addresses, URLs, or wallet data.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from oracle41_open.core.models import (
    Chain,
    ProviderAuthError,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from oracle41_open.core.services.provider_live_validation_service import (
    ProviderLiveValidationService,
)
from oracle41_open.providers.alchemy import AlchemyProvider
from oracle41_open.providers.ankr import AnkrProvider
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.goldrush import GoldRushProvider
from oracle41_open.providers.moralis import MoralisProvider

_OPT_IN_NAME = "ORACLE41_RUN_LIVE_PROVIDER_VALIDATION"
_WALLET_NAME = "ORACLE41_LIVE_TEST_WALLET"
_TOKEN_NAME = "ORACLE41_LIVE_TEST_TOKEN"
_CHAIN_NAME = "ORACLE41_LIVE_TEST_CHAIN"
_KEY_NAMES = {
    "alchemy": "ORACLE41_ALCHEMY_API_KEY",
    "ankr": "ORACLE41_ANKR_API_KEY",
    "moralis": "ORACLE41_MORALIS_API_KEY",
    "goldrush": "ORACLE41_GOLDRUSH_API_KEY",
}


def run_live_provider_validation(
    environment: Mapping[str, str] | None = None,
    providers: Mapping[str, DataProvider] | None = None,
) -> int:
    """Validate configuration, run providers in order, and return a shell exit code."""

    values = environment if environment is not None else os.environ
    if values.get(_OPT_IN_NAME) != "1":
        print(
            "Live provider validation is disabled. "
            f"Set {_OPT_IN_NAME}=1 to run it explicitly."
        )
        return 2

    missing = [
        name
        for name in (_WALLET_NAME, _TOKEN_NAME, *_KEY_NAMES.values())
        if not values.get(name, "").strip()
    ]
    if missing:
        print("Live provider validation is missing required environment variables:")
        for name in missing:
            print(f"- {name}")
        return 2

    raw_chain = values.get(_CHAIN_NAME, Chain.ETHEREUM.value).strip().lower()
    try:
        chain = Chain(raw_chain)
    except ValueError:
        supported = ", ".join(item.value for item in Chain)
        print(f"Invalid {_CHAIN_NAME}. Supported values: {supported}.")
        return 2

    live_providers = providers or _build_providers(values)
    validator = ProviderLiveValidationService()
    failed = False
    for provider_id in _KEY_NAMES:
        try:
            report = validator.validate(
                provider_id=provider_id,
                provider=live_providers[provider_id],
                wallet_address=values[_WALLET_NAME],
                token_address=values[_TOKEN_NAME],
                chain=chain,
            )
        except ProviderError as error:
            failed = True
            print(f"{provider_id}: FAILED - {_safe_failure(error)}")
        except (KeyError, ValueError):
            failed = True
            print(f"{provider_id}: FAILED - invalid live validation input or adapter setup.")
        else:
            print(
                f"{provider_id}: PASS - {len(report.operations)} bounded wallet operations."
            )
    return 1 if failed else 0


def _build_providers(environment: Mapping[str, str]) -> dict[str, DataProvider]:
    return {
        "alchemy": AlchemyProvider(api_key=environment[_KEY_NAMES["alchemy"]]),
        "ankr": AnkrProvider(api_key=environment[_KEY_NAMES["ankr"]]),
        "moralis": MoralisProvider(api_key=environment[_KEY_NAMES["moralis"]]),
        "goldrush": GoldRushProvider(api_key=environment[_KEY_NAMES["goldrush"]]),
    }


def _safe_failure(error: ProviderError) -> str:
    if isinstance(error, ProviderAuthError):
        return "credential rejected"
    if isinstance(error, ProviderRateLimitError):
        return "rate limited"
    if isinstance(error, ProviderTimeoutError):
        return "request timed out"
    if isinstance(error, ProviderNetworkError):
        return "network unavailable"
    if isinstance(error, ProviderResponseError):
        return "unexpected provider response"
    return "provider validation failed"
