from __future__ import annotations

from dataclasses import dataclass

KeyringBackendError: type[Exception] = Exception

try:
    import keyring
    from keyring.errors import KeyringError as KeyringBackendError
except ModuleNotFoundError:  # pragma: no cover - fallback branch
    keyring = None  # type: ignore[assignment]


_FALLBACK_SECRETS: dict[tuple[str, str], str] = {}


@dataclass
class SecretStore:
    service_name: str

    def set_secret(self, key: str, value: str) -> bool:
        if keyring is None:
            _FALLBACK_SECRETS[(self.service_name, key)] = value
            return True
        try:
            keyring.set_password(self.service_name, key, value)
        except KeyringBackendError:
            return False
        return True

    def get_secret(self, key: str) -> str | None:
        if keyring is None:
            return _FALLBACK_SECRETS.get((self.service_name, key))
        try:
            return keyring.get_password(self.service_name, key)
        except KeyringBackendError:
            return None

    def delete_secret(self, key: str) -> bool:
        if keyring is None:
            _FALLBACK_SECRETS.pop((self.service_name, key), None)
            return True
        try:
            keyring.delete_password(self.service_name, key)
        except KeyringBackendError:
            return False
        return True
