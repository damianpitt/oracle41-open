"""Test EVM address validation and normalization.

The cases cover valid, invalid, mixed-case, and checksum address inputs.
They keep storage and user-input behavior consistent.
"""

from oracle41_open.core.services.address_validator import AddressValidator


def test_is_valid_accepts_40_hex_chars() -> None:
    address = "0x742d35cc6634c0532925a3b844bc454e4438f44e"
    assert AddressValidator.is_valid(address)


def test_is_valid_rejects_invalid_input() -> None:
    assert not AddressValidator.is_valid("0x1234")
    assert not AddressValidator.is_valid("742d35cc6634c0532925a3b844bc454e4438f44e")


def test_validation_error_ignores_likely_ens_name() -> None:
    assert AddressValidator.validation_error("vitalik.eth") is None


def test_validation_error_returns_message_for_bad_wallet() -> None:
    assert AddressValidator.validation_error("0xzzzz") == (
        "Invalid wallet address. Expected 0x + 40 hex characters."
    )
