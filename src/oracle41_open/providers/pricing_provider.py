"""Define the token pricing provider contract.

Pricing services use this protocol to request current values without knowing the vendor implementation.
Cache and stale-price policy remain in the core pricing service.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from oracle41_open.core.models import Chain


class PricingProvider(Protocol):
    def get_native_price(self, chain: Chain) -> Decimal | None:
        ...

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        ...
