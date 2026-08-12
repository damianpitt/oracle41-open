"""Test ENS and address label resolution.

The cases cover normalization, cache hits, negative caching, expiry, and resolver failures.
They keep labels separate from the canonical wallet address.
"""

from __future__ import annotations

from typing import Any

import pytest

from oracle41_open.core.models import Chain, ProviderError, ValidationError
from oracle41_open.core.services.label_resolution_service import LabelResolutionService


def test_resolve_label_returns_known_address_labels_without_resolver_lookup() -> None:
    resolver = _SequenceResolver(responses=["ignored.eth"])
    service = LabelResolutionService(resolver=resolver, cache_store=_MemoryCache())

    zero = service.resolve_label("0x0000000000000000000000000000000000000000", Chain.ETHEREUM)
    dead = service.resolve_label("0x000000000000000000000000000000000000dEaD", Chain.ETHEREUM)

    assert zero == "Zero Address"
    assert dead == "Burn Address"
    assert resolver.calls == []


def test_resolve_label_uses_cached_positive_result() -> None:
    resolver = _SequenceResolver(responses=["vitalik.eth"])
    cache = _MemoryCache()
    service = LabelResolutionService(resolver=resolver, cache_store=cache, cache_ttl_seconds=777)
    address = "0x742d35Cc6634C0532925A3B844Bc454E4438f44e"

    first = service.resolve_label(address, Chain.ETHEREUM)
    second = service.resolve_label(address, Chain.ETHEREUM)

    assert first == "vitalik.eth"
    assert second == "vitalik.eth"
    assert resolver.calls == [("0x742d35cc6634c0532925a3b844bc454e4438f44e", Chain.ETHEREUM)]
    assert cache.set_calls == [
        (
            "labels.ens.v1.ethereum.0x742d35cc6634c0532925a3b844bc454e4438f44e",
            {"version": 1, "label": "vitalik.eth"},
            777,
        )
    ]


def test_resolve_label_negative_result_is_cached() -> None:
    resolver = _SequenceResolver(responses=[None, "should-not-be-used.eth"])
    service = LabelResolutionService(resolver=resolver, cache_store=_MemoryCache())
    address = "0x1111111111111111111111111111111111111111"

    first = service.resolve_label(address, Chain.ETHEREUM)
    second = service.resolve_label(address, Chain.ETHEREUM)

    assert first is None
    assert second is None
    assert resolver.calls == [("0x1111111111111111111111111111111111111111", Chain.ETHEREUM)]


def test_resolve_label_treats_provider_error_as_empty_and_caches_it() -> None:
    resolver = _SequenceResolver(responses=[ProviderError("rate-limited"), "should-not-be-used.eth"])
    service = LabelResolutionService(resolver=resolver, cache_store=_MemoryCache())
    address = "0x2222222222222222222222222222222222222222"

    first = service.resolve_label(address, Chain.ETHEREUM)
    second = service.resolve_label(address, Chain.ETHEREUM)

    assert first is None
    assert second is None
    assert resolver.calls == [("0x2222222222222222222222222222222222222222", Chain.ETHEREUM)]


def test_resolve_label_rejects_invalid_address() -> None:
    resolver = _SequenceResolver(responses=["unused.eth"])
    service = LabelResolutionService(resolver=resolver)

    with pytest.raises(ValidationError):
        service.resolve_label("0x1234", Chain.ETHEREUM)

    assert resolver.calls == []


def test_resolve_labels_filters_invalid_and_deduplicates_addresses() -> None:
    resolver = _SequenceResolver(responses=["label-a.eth"])
    service = LabelResolutionService(resolver=resolver)

    resolved = service.resolve_labels(
        addresses=[
            "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "0x000000000000000000000000000000000000dEaD",
            "not-an-address",
        ],
        chain=Chain.ETHEREUM,
    )

    assert resolved == {
        "0x000000000000000000000000000000000000dead": "Burn Address",
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": "label-a.eth",
    }
    assert resolver.calls == [("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Chain.ETHEREUM)]


def test_resolve_input_preserves_ens_name_and_caches_address() -> None:
    resolver = _SequenceNameResolver(
        responses=["0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"]
    )
    service = LabelResolutionService(
        name_resolver=resolver,
        cache_store=_MemoryCache(),
    )

    first = service.resolve_input(" Example.ETH ", Chain.ETHEREUM)
    second = service.resolve_input("example.eth", Chain.ETHEREUM)

    assert first.address == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert first.input_name == "example.eth"
    assert second == first
    assert resolver.calls == [("example.eth", Chain.ETHEREUM)]


def test_resolve_input_accepts_address_without_network_lookup() -> None:
    resolver = _SequenceNameResolver(responses=[])
    service = LabelResolutionService(name_resolver=resolver)

    resolution = service.resolve_input(
        "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        Chain.BASE,
    )

    assert resolution.address == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert resolution.input_name is None
    assert resolver.calls == []


def test_resolve_input_rejects_unresolved_or_invalid_names() -> None:
    resolver = _SequenceNameResolver(responses=[None])
    service = LabelResolutionService(name_resolver=resolver)

    with pytest.raises(ValidationError, match="could not be resolved"):
        service.resolve_input("missing.eth", Chain.ETHEREUM)
    with pytest.raises(ValidationError, match="address or a valid ENS name"):
        service.resolve_input("not a wallet", Chain.ETHEREUM)


class _MemoryCache:
    def __init__(self) -> None:
        self.storage: dict[str, Any] = {}
        self.set_calls: list[tuple[str, Any, int | None]] = []

    def get(self, key: str) -> Any | None:
        return self.storage.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        self.storage[key] = value
        self.set_calls.append((key, value, ttl_seconds))


class _SequenceResolver:
    def __init__(self, responses: list[str | None | Exception]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, Chain]] = []

    def resolve_label(self, address: str, chain: Chain) -> str | None:
        self.calls.append((address, chain))
        if not self._responses:
            return None
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class _SequenceNameResolver:
    def __init__(self, responses: list[str | None | Exception]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, Chain]] = []

    def resolve_address(self, name: str, chain: Chain) -> str | None:
        self.calls.append((name, chain))
        if not self._responses:
            return None
        next_item = self._responses.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item
