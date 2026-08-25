"""Test provider credential validation.

The cases cover accepted keys, authentication failures, and safe user-facing messages.
No live credentials or network calls are used.
"""

from __future__ import annotations

from oracle41_open.core.models import ProviderAuthError, ProviderError
from oracle41_open.core.services.provider_key_validation_service import ProviderKeyValidationService


def test_provider_key_validation_service_trims_and_validates_alchemy_key() -> None:
    calls: list[str] = []

    def probe_alchemy(key: str) -> None:
        calls.append(key)

    service = ProviderKeyValidationService(
        alchemy_probe=probe_alchemy,
        ankr_probe=lambda _: None,
    )

    result = service.validate_alchemy_key("  alchemy-test-key  ")

    assert result.provider == "Alchemy"
    assert result.is_valid
    assert result.message == "Alchemy key validated."
    assert calls == ["alchemy-test-key"]


def test_provider_key_validation_service_validates_moralis_key() -> None:
    calls: list[str] = []
    service = ProviderKeyValidationService(
        alchemy_probe=lambda _: None,
        ankr_probe=lambda _: None,
        moralis_probe=calls.append,
    )

    result = service.validate_moralis_key("  moralis-test-key  ")

    assert result.provider == "Moralis"
    assert result.is_valid
    assert result.message == "Moralis key validated."
    assert calls == ["moralis-test-key"]


def test_provider_key_validation_service_rejects_empty_key() -> None:
    service = ProviderKeyValidationService(
        alchemy_probe=lambda _: None,
        ankr_probe=lambda _: None,
    )

    result = service.validate_ankr_key("   ")

    assert result.provider == "Ankr"
    assert not result.is_valid
    assert result.message == "Ankr key is empty."


def test_provider_key_validation_service_maps_auth_errors() -> None:
    def probe_ankr(_: str) -> None:
        raise ProviderAuthError("HTTP 401")

    service = ProviderKeyValidationService(
        alchemy_probe=lambda _: None,
        ankr_probe=probe_ankr,
    )

    result = service.validate_ankr_key("ankr-key")

    assert not result.is_valid
    assert result.message == "Ankr key rejected: HTTP 401"


def test_provider_key_validation_service_maps_provider_errors() -> None:
    def probe_alchemy(_: str) -> None:
        raise ProviderError("rate limited")

    service = ProviderKeyValidationService(
        alchemy_probe=probe_alchemy,
        ankr_probe=lambda _: None,
    )

    result = service.validate_alchemy_key("alchemy-key")

    assert not result.is_valid
    assert result.message == "Alchemy key validation failed: rate limited"


def test_provider_key_validation_service_maps_unexpected_errors() -> None:
    def probe_alchemy(_: str) -> None:
        raise RuntimeError("unexpected failure")

    service = ProviderKeyValidationService(
        alchemy_probe=probe_alchemy,
        ankr_probe=lambda _: None,
    )

    result = service.validate_alchemy_key("alchemy-key")

    assert not result.is_valid
    assert result.message == "Alchemy key validation hit unexpected error: unexpected failure"
