"""Define bounded historical holdings requests and immutable report documents.

A report covers one wallet, token contract, and pair of end-of-block balances. New collection
derives price time from the closing header; the optional supplied time is legacy context only.
Saved JSON freezes inputs and results so later ledger or price changes cannot alter the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from oracle41_open._json import loads
from oracle41_open.core.models.chain import Chain
from oracle41_open.core.models.errors import ValidationError
from oracle41_open.core.models.historical_price import token_address, utc_time


@dataclass(frozen=True)
class HistoricalHoldingsRequest:
    wallet_address: str
    chain: Chain
    contract_address: str
    opening_block: int
    closing_block: int
    price_at: datetime | None = None
    max_price_age_seconds: int = 86_400
    require_finalized: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "wallet_address", token_address(self.wallet_address))
        object.__setattr__(self, "contract_address", token_address(self.contract_address))
        if self.price_at is not None:
            object.__setattr__(self, "price_at", utc_time(self.price_at))
        if not isinstance(self.require_finalized, bool):
            raise ValidationError("The finalized-block requirement must be boolean.")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < 2**63
            for value in (self.opening_block, self.closing_block)
        ):
            raise ValidationError(
                "Enter nonnegative block numbers within the SQLite integer range."
            )
        if self.closing_block < self.opening_block:
            raise ValidationError("Closing block cannot be earlier than the opening block.")
        if (
            not isinstance(self.max_price_age_seconds, int)
            or isinstance(self.max_price_age_seconds, bool)
            or self.max_price_age_seconds < 0
        ):
            raise ValidationError("Price age must be a nonnegative number of seconds.")


@dataclass(frozen=True)
class HistoricalHoldingsReportSummary:
    report_id: str
    created_at: datetime


@dataclass(frozen=True)
class HistoricalHoldingsReport:
    report_id: str
    created_at: datetime
    document_json: str

    @property
    def document(self) -> dict[str, Any]:
        """Return a fresh copy so callers cannot mutate the saved evidence in place."""
        payload = loads(self.document_json)
        if not isinstance(payload, dict):
            raise ValidationError("Invalid historical holdings document.")
        return payload
