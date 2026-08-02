from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    Token,
    TokenBalance,
    TokenBalancePage,
)
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.pricing_provider import PricingProvider


class StubDataProvider(DataProvider):
    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        _ = address
        _ = chain
        return Decimal("1.2345")

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        _ = address
        _ = chain
        _ = page_key
        balances = [
            TokenBalance(
                token=Token(
                    contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    symbol="USDC",
                    name="USD Coin",
                    decimals=6,
                    is_verified=True,
                ),
                balance_decimal=Decimal("250.00"),
            ),
            TokenBalance(
                token=Token(
                    contract_address="0xdac17f958d2ee523a2206206994597c13d831ec7",
                    symbol="USDT",
                    name="Tether USD",
                    decimals=6,
                    is_verified=True,
                ),
                balance_decimal=Decimal("101.75"),
            ),
        ]
        return TokenBalancePage(balances=balances, next_page_key=None)

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        _ = from_block
        normalized_address = address.strip().lower()
        if cursor == "page-2":
            return ActivityPage(
                items=[
                    ActivityItem(
                        block_number=19_000_200,
                        tx_hash="0xccc333",
                        log_index="0x2",
                        timestamp=datetime(2026, 1, 16, 12, 30, tzinfo=UTC),
                        from_address="0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
                        to_address=normalized_address,
                        asset_symbol="MATIC" if chain is Chain.POLYGON else "ETH",
                        contract_address=None,
                        raw_value="10000000000000000",
                        value_decimal=Decimal("0.01"),
                        value_usd=None,
                        is_verified=True,
                        category=ActivityCategory.EXTERNAL,
                        chain=chain,
                    )
                ],
                next_cursor=None,
            )

        return ActivityPage(
            items=[
                ActivityItem(
                    block_number=19_000_000,
                    tx_hash="0xaaa111",
                    log_index="0x0",
                    timestamp=datetime(2026, 1, 17, 8, 15, tzinfo=UTC),
                    from_address=normalized_address,
                    to_address="0x1111111111111111111111111111111111111111",
                    asset_symbol="USDC",
                    contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    raw_value="25000000",
                    value_decimal=Decimal("25"),
                    value_usd=None,
                    is_verified=True,
                    category=ActivityCategory.ERC20,
                    chain=chain,
                ),
                ActivityItem(
                    block_number=19_000_010,
                    tx_hash="0xbbb222",
                    log_index="0x1",
                    timestamp=datetime(2026, 1, 17, 6, 40, tzinfo=UTC),
                    from_address="0x2222222222222222222222222222222222222222",
                    to_address=normalized_address,
                    asset_symbol="MATIC" if chain is Chain.POLYGON else "ETH",
                    contract_address=None,
                    raw_value="25000000000000000",
                    value_decimal=Decimal("0.025"),
                    value_usd=None,
                    is_verified=True,
                    category=ActivityCategory.EXTERNAL,
                    chain=chain,
                ),
            ],
            next_cursor="page-2",
        )

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        normalized_address = address.strip().lower()
        normalized_token = token_address.strip().lower()
        if cursor == "token-page-2":
            return ActivityPage(
                items=[
                    ActivityItem(
                        block_number=19_005_010,
                        tx_hash="0xddd444",
                        log_index="0x1",
                        timestamp=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
                        from_address=normalized_address,
                        to_address="0x5555555555555555555555555555555555555555",
                        asset_symbol="USDC",
                        contract_address=normalized_token,
                        raw_value="12500000",
                        value_decimal=Decimal("12.5"),
                        value_usd=None,
                        is_verified=True,
                        category=ActivityCategory.ERC20,
                        chain=chain,
                    )
                ],
                next_cursor=None,
            )

        items = [
            ActivityItem(
                block_number=19_005_000,
                tx_hash="0xccc333",
                log_index="0x0",
                timestamp=datetime(2026, 1, 16, 10, 0, tzinfo=UTC),
                from_address="0x6666666666666666666666666666666666666666",
                to_address=normalized_address,
                asset_symbol="USDC",
                contract_address=normalized_token,
                raw_value="30000000",
                value_decimal=Decimal("30"),
                value_usd=None,
                is_verified=True,
                category=ActivityCategory.ERC20,
                chain=chain,
            )
        ]
        if include_approvals and cursor is None:
            items.append(
                ActivityItem(
                    block_number=19_004_990,
                    tx_hash="0xbbb222",
                    log_index="0x2",
                    timestamp=datetime(2026, 1, 16, 8, 45, tzinfo=UTC),
                    from_address=normalized_address,
                    to_address="0x7777777777777777777777777777777777777777",
                    asset_symbol="USDC",
                    contract_address=normalized_token,
                    raw_value="0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
                    value_decimal=Decimal("0"),
                    value_usd=None,
                    is_verified=True,
                    category=ActivityCategory.APPROVAL,
                    chain=chain,
                )
            )
        return ActivityPage(items=items, next_cursor="token-page-2")


class StubPricingProvider(PricingProvider):
    def get_native_price(self, chain: Chain) -> Decimal | None:
        if chain is Chain.POLYGON:
            return Decimal("1.05")
        return Decimal("3200.00")

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        _ = chain
        prices: dict[str, Decimal] = {}
        for contract in contract_addresses:
            normalized = contract.strip().lower()
            if normalized.endswith("6eb48") or normalized.endswith("31ec7"):  # USDC
                prices[normalized] = Decimal("1")
        return prices


class UnavailablePricingProvider(PricingProvider):
    """Represent live data mode without a configured pricing provider."""

    def get_native_price(self, chain: Chain) -> Decimal | None:
        _ = chain
        return None

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        _ = chain
        _ = contract_addresses
        return {}
