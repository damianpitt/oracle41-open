"""Resolve ENS names and human-readable address labels.

The service validates input, caches successful and failed lookups, and keeps network resolution behind a small interface.
A label never changes the normalized address used for provider and storage operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import Chain, ProviderError, ValidationError
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
)

_KNOWN_LABELS: dict[str, str] = {
    "0x0000000000000000000000000000000000000000": "Zero Address",
    "0x000000000000000000000000000000000000dead": "Burn Address",
}


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...


class ENSLabelResolver(Protocol):
    def resolve_label(self, address: str, chain: Chain) -> str | None:
        ...


class ENSNameResolver(Protocol):
    def resolve_address(self, name: str, chain: Chain) -> str | None:
        ...


@dataclass(frozen=True)
class AddressResolution:
    address: str
    input_name: str | None = None


class ENSIdeasLabelResolver:
    def __init__(self, http_client: HTTPClient | None = None, base_url: str = "https://api.ensideas.com") -> None:
        self._http_client = http_client or HTTPClient(timeout_seconds=8.0)
        self._base_url = base_url.rstrip("/")

    def resolve_label(self, address: str, chain: Chain) -> str | None:
        _ = chain
        request = HTTPRequest(
            method="GET",
            url=f"{self._base_url}/ens/resolve/{address}",
            headers={"accept": "application/json"},
        )
        try:
            response = self._http_client.send(request)
        except (HTTPClientTimeoutError, HTTPClientNetworkError) as error:
            raise ProviderError(f"ENS label request failed: {error}") from error

        if response.status_code == 404:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderError(f"ENS label request failed with HTTP {response.status_code}.")

        try:
            payload = json_loads(response.data)
        except ValueError as error:
            raise ProviderError("ENS label response was invalid JSON.") from error
        if not isinstance(payload, dict):
            return None

        raw_name = payload.get("name")
        if not isinstance(raw_name, str):
            return None
        label = raw_name.strip()
        if not label:
            return None
        return label

    def resolve_address(self, name: str, chain: Chain) -> str | None:
        _ = chain
        request = HTTPRequest(
            method="GET",
            url=f"{self._base_url}/ens/resolve/{quote(name, safe='')}",
            headers={"accept": "application/json"},
        )
        try:
            response = self._http_client.send(request)
        except (HTTPClientTimeoutError, HTTPClientNetworkError) as error:
            raise ProviderError(f"ENS address request failed: {error}") from error

        if response.status_code == 404:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderError(f"ENS address request failed with HTTP {response.status_code}.")
        try:
            payload = json_loads(response.data)
        except ValueError as error:
            raise ProviderError("ENS address response was invalid JSON.") from error
        if not isinstance(payload, dict):
            return None
        raw_address = payload.get("address")
        if not isinstance(raw_address, str):
            return None
        normalized = AddressValidator.normalized(raw_address)
        return normalized if AddressValidator.is_valid(normalized) else None


class LabelResolutionService:
    def __init__(
        self,
        resolver: ENSLabelResolver | None = None,
        name_resolver: ENSNameResolver | None = None,
        cache_store: CacheStore | None = None,
        cache_ttl_seconds: int = 86_400,
    ) -> None:
        default_resolver = ENSIdeasLabelResolver()
        self._resolver = resolver or default_resolver
        self._name_resolver = name_resolver or default_resolver
        self._cache_store = cache_store
        self._cache_ttl_seconds = max(0, cache_ttl_seconds)

    def resolve_label(self, address: str, chain: Chain) -> str | None:
        normalized = AddressValidator.normalized(address)
        if not AddressValidator.is_valid(normalized):
            raise ValidationError("Invalid wallet address. Expected 0x + 40 hex characters.")

        known = _KNOWN_LABELS.get(normalized)
        if known is not None:
            return known

        cache_key = self._cache_key(chain, normalized)
        cached = self._load_cached_label(cache_key)
        if not isinstance(cached, _CacheMiss):
            return cached

        label: str | None
        try:
            label = self._resolver.resolve_label(normalized, chain)
        except ProviderError:
            label = None
        self._save_cached_label(cache_key, label)
        return label

    def resolve_labels(self, addresses: list[str], chain: Chain) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for normalized in sorted({AddressValidator.normalized(address) for address in addresses}):
            if not AddressValidator.is_valid(normalized):
                continue
            label = self.resolve_label(normalized, chain)
            if label is not None:
                resolved[normalized] = label
        return resolved

    def resolve_input(self, value: str, chain: Chain) -> AddressResolution:
        normalized = AddressValidator.normalized(value)
        if AddressValidator.is_valid(normalized):
            return AddressResolution(address=normalized)

        name = value.strip().lower()
        if not _is_valid_ens_name(name):
            raise ValidationError("Enter a wallet address or a valid ENS name ending in .eth.")
        cache_key = f"labels.ens-address.v1.{chain.value}.{name}"
        cached = self._load_cached_address(cache_key)
        if not isinstance(cached, _CacheMiss):
            if cached is None:
                raise ValidationError(f"ENS name could not be resolved: {name}")
            return AddressResolution(address=cached, input_name=name)

        try:
            address = self._name_resolver.resolve_address(name, chain)
        except ProviderError as error:
            raise ValidationError(f"ENS name resolution failed: {name}") from error
        if address is None:
            self._save_cached_address(cache_key, None)
            raise ValidationError(f"ENS name could not be resolved: {name}")
        normalized_address = AddressValidator.normalized(address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError(f"ENS resolver returned an invalid address for {name}.")
        self._save_cached_address(cache_key, normalized_address)
        return AddressResolution(address=normalized_address, input_name=name)

    def _cache_key(self, chain: Chain, address: str) -> str:
        return f"labels.ens.v1.{chain.value}.{address}"

    def _load_cached_label(self, cache_key: str) -> str | None | _CacheMiss:
        if self._cache_store is None:
            return _CACHE_MISS
        payload = self._cache_store.get(cache_key)
        if not isinstance(payload, dict):
            return _CACHE_MISS
        if payload.get("version") != 1:
            return _CACHE_MISS
        raw_label = payload.get("label")
        if raw_label is None:
            return None
        if isinstance(raw_label, str):
            trimmed = raw_label.strip()
            return trimmed or None
        return _CACHE_MISS

    def _save_cached_label(self, cache_key: str, label: str | None) -> None:
        if self._cache_store is None:
            return
        payload: dict[str, object] = {
            "version": 1,
            "label": label,
        }
        self._cache_store.set(cache_key, payload, ttl_seconds=self._cache_ttl_seconds)

    def _load_cached_address(self, cache_key: str) -> str | None | _CacheMiss:
        if self._cache_store is None:
            return _CACHE_MISS
        payload = self._cache_store.get(cache_key)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return _CACHE_MISS
        raw_address = payload.get("address")
        if raw_address is None:
            return None
        if not isinstance(raw_address, str):
            return _CACHE_MISS
        normalized = AddressValidator.normalized(raw_address)
        return normalized if AddressValidator.is_valid(normalized) else _CACHE_MISS

    def _save_cached_address(self, cache_key: str, address: str | None) -> None:
        if self._cache_store is None:
            return
        self._cache_store.set(
            cache_key,
            {"version": 1, "address": address},
            ttl_seconds=self._cache_ttl_seconds,
        )


class _CacheMiss:
    pass


_CACHE_MISS = _CacheMiss()


def _is_valid_ens_name(value: str) -> bool:
    if not value.endswith(".eth") or len(value) > 255:
        return False
    labels = value.split(".")
    forbidden = {"/", "?", "#", "\\"}
    return all(
        label
        and len(label) <= 63
        and not any(character.isspace() or character in forbidden for character in label)
        for label in labels
    )
