# Oracle41 Open

Oracle41 Open is a Linux-first desktop application for read-only EVM wallet analytics. It gives users a local workspace for inspecting wallet balances, activity, token details, portfolios, notes, snapshots, and exports without handling private keys or signing transactions.

## Alpha Status

Version `0.4.0a8` is an alpha release. Alchemy, Ankr, Moralis, and GoldRush can be enabled and ordered in Settings. All four adapters pass the same recorded-fixture checks for normalized wallet operations. Oracle41 can collect and normalize block-specific Aave V3 supplied assets, collateral, debt, and account health data on every supported chain. Protocol positions are not yet saved or shown in the desktop interface.

## Features

- Wallet overview with native and ERC-20 balances
- Portfolio pricing enrichment with cached last-known values
- Ethereum, Optimism, Polygon, Base, and Arbitrum support
- Activity feed with durable history, resumable pagination, lookback, and filters
- ERC-20, ERC-721, and ERC-1155 token detail flows with paginated approval history
- Alchemy, Ankr, Moralis, and GoldRush wallet-data providers with user-controlled enablement, priority, and ordered failover
- Provider-owned pagination cursors that prevent mixed-vendor continuation pages
- Provider capability summaries with supported chains, wallet features, and validation destinations
- Non-secret credential source and last-validation diagnostics
- Shared recorded-fixture conformance tests for available wallet-data adapters
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
- Aave V3 supplied, collateral, and debt normalization with raw account-health metrics on every supported chain
- Exact-block Aave V3 snapshot collection through configured JSON-RPC transaction providers
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
| Providers | Connections to Alchemy, Ankr, Moralis, GoldRush, JSON-RPC endpoints, Blockscout, and ENS. |
| Local storage | SQLite data, settings, cache files, notes, saved views, and backups. |
| Exports | CSV and JSON files created from the data shown by the application. |

The desktop interface asks the core services for data. The core services decide whether to use saved data or contact a provider. This keeps provider-specific code out of the interface and makes each part easier to test or replace.

Network work runs in the background. The interface stays responsive while Oracle41 Open loads wallet activity, prices, transaction details, or verified contract information.

Wallet history is saved in SQLite. If a sync stops early, it can continue later without adding the same event twice. Transaction Inspector adds receipts, fees, internal calls, decoded calls, wallet actions, event logs, revert reasons, proxy details, and optional explorer context while keeping the original raw data available.

More detail is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Data Sources and Providers

Oracle41 Open uses separate provider roles. A wallet-data provider supplies indexed balances and history. A transaction provider supplies receipts, logs, contract reads, and optional execution traces through JSON-RPC. A pricing provider supplies market prices. One service does not need to fill every role.

| Provider | Wallet balances and history | Transaction inspection | Market pricing |
| --- | --- | --- | --- |
| Alchemy | Yes | Yes | Yes |
| Ankr | Yes | Yes | No |
| Moralis | Yes | No | No |
| GoldRush | Yes | No | No |
| Custom JSON-RPC endpoint | No complete wallet index | Yes | No |

Alchemy currently offers the broadest coverage from one account. Ankr can supply wallet data and transaction inspection but not Oracle41's dedicated market-price feed. Moralis and GoldRush specialize in indexed wallet analytics. A custom JSON-RPC endpoint can complete transaction inspection when Moralis or GoldRush supplies wallet history.

Common setups:

- **Alchemy only:** covers the complete current feature set with one provider.
- **Alchemy plus another wallet provider:** adds wallet-data failover while keeping transaction inspection and pricing available.
- **Ankr plus Alchemy:** provides full current coverage and indexed wallet-data failover.
- **Moralis or GoldRush plus custom JSON-RPC:** provides wallet analytics and transaction inspection, but dedicated market pricing remains unavailable.
- **Moralis or GoldRush only:** provides wallet balances and history; advanced transaction inspection and dedicated pricing are limited.

Execution traces and historical contract reads can still depend on the provider plan and chain. Oracle41 reports unavailable evidence instead of presenting an incomplete transaction as complete. See [docs/PROVIDER_STRATEGY.md](docs/PROVIDER_STRATEGY.md) for feature boundaries, failover rules, and provider-specific limitations.

### Opt-in Live Validation

Maintainers can test all four wallet-data adapters against one public wallet and token contract. The validator reads keys and test addresses from environment variables, performs only the first page of each operation, and does not print credentials, addresses, URLs, balances, or activity.

```bash
export ORACLE41_RUN_LIVE_PROVIDER_VALIDATION=1
export ORACLE41_LIVE_TEST_CHAIN="ethereum"
export ORACLE41_LIVE_TEST_WALLET="0x..."
export ORACLE41_LIVE_TEST_TOKEN="0x..."
export ORACLE41_ALCHEMY_API_KEY="..."
export ORACLE41_ANKR_API_KEY="..."
export ORACLE41_MORALIS_API_KEY="..."
export ORACLE41_GOLDRUSH_API_KEY="..."
make validate-providers-live
```

This command makes real provider requests and may consume API credits. It runs only on the local machine where it is explicitly enabled and never during normal CI. Full instructions are in [docs/PROVIDER_STRATEGY.md](docs/PROVIDER_STRATEGY.md).

## Requirements

- Linux with Python 3.11 or newer when installing from source
- Qt-compatible desktop environment (the application installs the required PySide6 Essentials modules)
- Alchemy, Ankr, Moralis, or GoldRush API key for live wallet data
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
- Aave V3 snapshots can be loaded and normalized, but the application does not store, price, aggregate, export, or display protocol positions yet.
- Moralis provides active ERC-20 approvals, not a complete archive of approvals that were later revoked.
- GoldRush filters block floors locally while paging wallet history, which may consume more API credits for older wallets.
- The application currently targets EVM-compatible chains supported by the configured providers.
- Debian compatibility targets and derivative distributions still require clean-system validation beyond the Ubuntu CI runners.
- ENS wallet input is available in Overview, Activity, and Token Detail; local metadata editors continue to use resolved hexadecimal addresses.
- Decoding currently covers bundled common token-standard signatures; unknown contract interactions remain available as raw calldata and logs.

## License

Oracle41 Open is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
