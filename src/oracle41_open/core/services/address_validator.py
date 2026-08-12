"""Validate and normalize EVM wallet and contract addresses.

The helpers accept hexadecimal addresses, apply checksum rules where needed, and return consistent lowercase storage values.
No network lookup takes place in this module.
"""

from __future__ import annotations


class AddressValidator:
    @staticmethod
    def normalized(address: str) -> str:
        return address.strip().lower()

    @staticmethod
    def is_valid(address: str) -> bool:
        trimmed = AddressValidator.normalized(address)
        if not trimmed.startswith("0x"):
            return False
        hex_part = trimmed[2:]
        if len(hex_part) != 40:
            return False
        return all(char in "0123456789abcdef" for char in hex_part)

    @staticmethod
    def is_likely_ens_name(value: str) -> bool:
        trimmed = AddressValidator.normalized(value)
        if "." not in trimmed or not trimmed.endswith(".eth"):
            return False
        if trimmed.startswith(".") or trimmed.endswith("."):
            return False
        labels = trimmed.split(".")
        if len(labels) < 2:
            return False
        for label in labels:
            if not label:
                return False
            if not all(char.isalnum() or char == "-" for char in label):
                return False
        return True

    @staticmethod
    def validation_error(address: str) -> str | None:
        trimmed = AddressValidator.normalized(address)
        if not trimmed:
            return None
        if AddressValidator.is_likely_ens_name(trimmed):
            return None
        if AddressValidator.is_valid(trimmed):
            return None
        return "Invalid wallet address. Expected 0x + 40 hex characters."
