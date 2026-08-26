"""Test the bounded provider live-validation contract and safe command behavior.

All providers are local recordings. The tests prove opt-in gating, required configuration, normalized provenance checks, and redacted output without network calls.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from oracle41_open.core.models import (
    ActivityPage,
    Chain,
    ProviderAuthError,
    ProviderResponseError,
    TokenBalancePage,
)
from oracle41_open.core.services.provider_live_validation_service import (
    ProviderLiveValidationService,
)
from oracle41_open.providers.stub import StubDataProvider
from oracle41_open.tools.validate_live_providers import run_live_provider_validation

_WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_TOKEN = "0x9999999999999999999999999999999999999999"


class _RecordingProvider(StubDataProvider):
    def __init__(self, provider_id: str, fail: bool = False) -> None:
        self.provider_id = provider_id
        self.fail = fail
        self.calls: list[str] = []

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        self.calls.append("native")
        if self.fail:
            raise ProviderAuthError("credential rejected")
        return super().get_native_balance(address, chain)

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        self.calls.append("balances")
        page = super().get_token_balances(address, chain, page_key)
        return replace(page, source_provider=self.provider_id)

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        self.calls.append("activity")
        page = super().get_activity(address, chain, cursor, from_block)
        return replace(page, source_provider=self.provider_id)

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        self.calls.append("token_history")
        page = super().get_token_transfers(
            address,
            token_address,
            chain,
            cursor,
            include_approvals,
        )
        return replace(page, source_provider=self.provider_id)


def _environment() -> dict[str, str]:
    return {
        "ORACLE41_RUN_LIVE_PROVIDER_VALIDATION": "1",
        "ORACLE41_LIVE_TEST_WALLET": _WALLET,
        "ORACLE41_LIVE_TEST_TOKEN": _TOKEN,
        "ORACLE41_LIVE_TEST_CHAIN": "base",
        "ORACLE41_ALCHEMY_API_KEY": "alchemy-secret",
        "ORACLE41_ANKR_API_KEY": "ankr-secret",
        "ORACLE41_MORALIS_API_KEY": "moralis-secret",
        "ORACLE41_GOLDRUSH_API_KEY": "goldrush-secret",
    }


def test_live_validation_service_runs_each_operation_once() -> None:
    provider = _RecordingProvider("goldrush")

    report = ProviderLiveValidationService().validate(
        provider_id="goldrush",
        provider=provider,
        wallet_address=_WALLET.upper(),
        token_address=_TOKEN,
        chain=Chain.BASE,
    )

    assert report.provider_id == "goldrush"
    assert report.chain is Chain.BASE
    assert len(report.operations) == 4
    assert provider.calls == ["native", "balances", "activity", "token_history"]


def test_live_validation_service_rejects_bad_source_provenance() -> None:
    with pytest.raises(ProviderResponseError, match="unexpected source"):
        ProviderLiveValidationService().validate(
            provider_id="expected",
            provider=_RecordingProvider("different"),
            wallet_address=_WALLET,
            token_address=_TOKEN,
            chain=Chain.ETHEREUM,
        )


def test_live_command_requires_explicit_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    result = run_live_provider_validation(environment={})

    assert result == 2
    assert "disabled" in capsys.readouterr().out


def test_live_command_reports_missing_variable_names_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = run_live_provider_validation(
        environment={"ORACLE41_RUN_LIVE_PROVIDER_VALIDATION": "1"}
    )

    output = capsys.readouterr().out
    assert result == 2
    assert "ORACLE41_ALCHEMY_API_KEY" in output
    assert "secret" not in output


def test_live_command_runs_all_providers_without_printing_inputs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment = _environment()
    providers = {
        provider_id: _RecordingProvider(provider_id)
        for provider_id in ("alchemy", "ankr", "moralis", "goldrush")
    }

    result = run_live_provider_validation(environment=environment, providers=providers)

    output = capsys.readouterr().out
    assert result == 0
    assert output.count("PASS") == 4
    assert _WALLET not in output
    assert _TOKEN not in output
    assert all(secret not in output for name, secret in environment.items() if "KEY" in name)


def test_live_command_returns_failure_when_one_provider_rejects_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    providers = {
        provider_id: _RecordingProvider(provider_id, fail=provider_id == "ankr")
        for provider_id in ("alchemy", "ankr", "moralis", "goldrush")
    }

    result = run_live_provider_validation(
        environment=_environment(),
        providers=providers,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "ankr: FAILED" in output
    assert output.count("PASS") == 3
