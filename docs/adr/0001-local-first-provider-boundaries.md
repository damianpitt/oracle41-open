# ADR 0001: Local-First Provider Boundaries

- Status: Accepted
- Date: 2026-08-02

## Context

Oracle41 Open reads wallet data from third-party providers while keeping user workspace data on the local machine. Provider failures must not be mistaken for valid zero balances or empty histories.

## Decision

- GUI views call core services, not provider adapters.
- API keys are read from the system keyring or explicit environment variables.
- Request URLs and credentials are excluded from user-facing transport errors.
- Live-provider failover is allowed only between configured live providers.
- Demonstration data is used only when no live provider is configured.
- The Debian package carries its Python runtime to avoid target-system ABI mismatches.

## Consequences

The application can run without a backend service and without storing private keys. Distribution artifacts are larger, but their Python runtime is consistent with bundled native extensions. Provider incompleteness must be reported instead of replaced with demonstration data.
