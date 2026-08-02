from __future__ import annotations


class Oracle41Error(Exception):
    """Base app exception."""


class ValidationError(Oracle41Error):
    """Raised when user input cannot be validated."""


class ProviderError(Oracle41Error):
    """Raised when a provider cannot satisfy a request."""


class ProviderAuthError(ProviderError):
    """Raised when provider authentication/authorization fails."""


class ProviderRateLimitError(ProviderError):
    """Raised when provider rate-limits requests."""


class ProviderTimeoutError(ProviderError):
    """Raised when provider calls time out."""


class ProviderNetworkError(ProviderError):
    """Raised when provider transport fails for network reasons."""


class ProviderResponseError(ProviderError):
    """Raised when provider returns invalid/unsupported response payloads."""
