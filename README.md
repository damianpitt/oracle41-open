# Oracle41 Open

Oracle41 Open is a Linux-first desktop application for read-only EVM wallet analytics. It gives users a local workspace for inspecting wallet balances, activity, token details, portfolios, notes, snapshots, and exports without handling private keys or signing transactions.

## Alpha Status

Version `0.4.0a1` is an alpha release. It introduces the versioned protocol-adapter foundation for supplied assets, debt, collateral, liquidity, staking, vesting, and rewards. A reference adapter and unknown-protocol fallback pass the same recorded-fixture conformance suite. Production protocol integrations and position views are not included yet. Validate live provider behavior and installation on your target distribution before relying on it.

## Features

- Wallet overview with native and ERC-20 balances
- Portfolio pricing enrichment with cached last-known values
- Ethereum, Optimism, Polygon, Base, and Arbitrum support
- Activity feed with durable history, resumable pagination, lookback, and filters
- ERC-20, ERC-721, and ERC-1155 token detail flows with paginated approval history
- Alchemy and Ankr providers with failover support
- Retry/backoff and structured rate-limit, timeout, and authentication errors
- ENS wallet input and address-label resolution with caching
- Token filtering for unverified, low-signal, and dust assets
- Watchlists and multi-wallet portfolio aggregation
- Notes, tags, saved views, and snapshot comparison
- Cache telemetry, diagnostics, refresh, and clear-cache controls
- Versioned CSV and JSON exports with completeness and provider provenance
- Backup and restore for local settings and SQLite state
- Transaction inspection with receipt status, gas, fees, raw logs, and provenance
- Deterministic ERC-20, ERC-721, and ERC-1155 call/event decoding from a local registry
- Local user ABI management and optional verified ABI retrieval from Blockscout
- EIP-1967 implementation, EIP-1967 beacon, and EIP-1167 proxy resolution with block-specific caching
- Custom and Solidity built-in revert decoding with raw-byte preservation
- Expandable internal call trees from Geth-compatible and Parity-compatible trace endpoints
- Per-chain trace capability discovery with explicit completeness states
- Learned historical-state capability reporting for block-specific proxy and revert queries
- Normalized transfers, approvals, simple swaps, deployments, contract calls, and unknown actions
- Versioned action CSV/JSON exports with participants, assets, confidence, and source evidence
- Action-set completeness and missing-evidence reasons based on trace availability
- Optional Blockscout transaction context with contract names, creation details, verification state, and source links
- Versioned protocol-position models, capability registry, fixture schema, reference adapter, and evidence-preserving unknown fallback
- Self-contained AMD64 and ARM64 Debian packages with desktop launcher and AppStream metadata

The complete M5 transaction-understanding test matrix and optional-provider fallbacks are documented in [docs/M5_VALIDATION.md](docs/M5_VALIDATION.md).

## Privacy and Scope

- The application is read-only.
- It does not request, store, or use private keys.
- It does not sign or broadcast transactions.
- Provider API keys are stored through the operating-system keyring when available.
- Settings, SQLite state, and cache data are stored locally.
- Backups intentionally exclude provider API keys.
- Custom JSON-RPC endpoint URLs are stored in the keyring and excluded from backups.
- Network requests are made only to configured data/pricing providers, the ENS resolver, and Blockscout when a user requests a verified ABI or inspects a transaction.

## Architecture

| Part | Purpose |
| --- | --- |
| Desktop interface | PySide6 screens for viewing wallets, activity, tokens, and settings. |
| Core | Business rules, data models, validation, filtering, and analysis. |
| Providers | Connections to Alchemy, Ankr, JSON-RPC endpoints, Blockscout, and ENS. |
| Local storage | SQLite data, settings, cache files, notes, saved views, and backups. |
| Exports | CSV and JSON files created from the data shown by the application. |

The desktop interface asks the core services for data. The core services decide whether to use saved data or contact a provider. This keeps provider-specific code out of the interface and makes each part easier to test or replace.

Network work runs in the background. The interface stays responsive while Oracle41 Open loads wallet activity, prices, transaction details, or verified contract information.

Wallet history is saved in SQLite. If a sync stops early, it can continue later without adding the same event twice. Transaction Inspector adds receipts, fees, internal calls, decoded calls, wallet actions, event logs, revert reasons, proxy details, and optional explorer context while keeping the original raw data available.

More detail is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- Linux with Python 3.11 or newer when installing from source
- Qt-compatible desktop environment (the application installs the required PySide6 Essentials modules)
- Alchemy or Ankr API key for live data
- Optional system keyring integration for persistent provider keys

The application can run with local stub providers when no live provider key is configured.

## Install From Source

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the application:

```bash
oracle41-open
```

Provider keys, optional per-chain JSON-RPC endpoints, and contract ABIs can be configured from the Settings tab.

## Install the Debian Package

On Ubuntu or Debian, download the matching `.deb` release asset and either double-click it in the file manager or install it with:

```bash
sudo apt install ./oracle41-open_<version>_<arch>.deb
```

The package includes its Python runtime, creates the `oracle41-open` command, and installs a desktop application entry.

Oracle41 Open provides an AMD64 Debian package targeting Ubuntu 22.04 or newer and Debian 12 or newer. It also provides an ARM64 Debian package targeting Ubuntu 24.04 or newer and Debian 13 or newer. Other Debian-based distributions may work but have not yet been formally validated. ARM64 requires a 64-bit ARM operating system; ARM32 systems are not supported.

The release workflow currently performs native build, installation, and smoke testing on Ubuntu 22.04 AMD64 and Ubuntu 24.04 ARM64. See [docs/SUPPORTED_PLATFORMS.md](docs/SUPPORTED_PLATFORMS.md) for package selection and validation details.

## Development

```bash
make dev-setup
make check
```

Individual checks:

```bash
pytest
ruff check .
mypy src
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the development workflow, [docs/ROADMAP.md](docs/ROADMAP.md) for product direction, [docs/PROTOCOL_ADAPTERS.md](docs/PROTOCOL_ADAPTERS.md) for adapter development, [docs/PROVIDER_STRATEGY.md](docs/PROVIDER_STRATEGY.md) for current and planned data sources, and [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests.

## Build a Debian Package Locally

```bash
python3 -m pip install --constraint requirements/release-constraints.txt -e ".[packaging]"
export SOURCE_DATE_EPOCH="$(git log -1 --pretty=%ct)"
./scripts/release/build_deb.sh
```

The package and checksum are written to `dist/`. The complete release procedure is documented in [docs/RELEASE.md](docs/RELEASE.md).

## Data Locations

Oracle41 Open uses standard Linux user directories:

- Settings: `${XDG_CONFIG_HOME:-~/.config}/oracle41-open/settings.json`
- SQLite state: `${XDG_DATA_HOME:-~/.local/share}/oracle41-open/state.sqlite3`
- Cache: `${XDG_CACHE_HOME:-~/.cache}/oracle41-open/cache.json`

The exact resolved paths depend on the platformdirs configuration and environment.

## Known Alpha Limitations

- Provider APIs and rate limits vary by chain and account.
- Token metadata quality depends on provider responses and local filtering rules.
- Live provider integration tests use mocked HTTP fixtures; they do not exercise private API keys in CI.
- Protocol positions are an extension foundation in `0.4.0a1`; no production DeFi adapter or position screen is connected yet.
- The application currently targets EVM-compatible chains supported by the configured providers.
- Debian compatibility targets and derivative distributions still require clean-system validation beyond the Ubuntu CI runners.
- ENS wallet input is available in Overview, Activity, and Token Detail; local metadata editors continue to use resolved hexadecimal addresses.
- Decoding currently covers bundled common token-standard signatures; unknown contract interactions remain available as raw calldata and logs.

## License

Oracle41 Open is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
