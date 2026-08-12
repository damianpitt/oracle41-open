# Oracle41 Open

Oracle41 Open is a Linux-first desktop application for read-only EVM wallet analytics. It gives users a local workspace for inspecting wallet balances, activity, token details, portfolios, notes, snapshots, and exports without handling private keys or signing transactions.

## Alpha Status

Version `0.3.0a4` is an alpha release. It adds local and Blockscout-verified contract ABIs, EIP-1967/EIP-1167 proxy resolution, and deterministic custom-error decoding to Transaction Inspector. Unknown, malformed, and raw provider payloads remain available. Validate live provider behavior and installation on your target distribution before relying on it.

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
- EIP-1967 and EIP-1167 implementation resolution with block-specific caching
- Custom and Solidity built-in revert decoding with raw-byte preservation
- Self-contained AMD64 and ARM64 Debian packages with desktop launcher and AppStream metadata

## Privacy and Scope

- The application is read-only.
- It does not request, store, or use private keys.
- It does not sign or broadcast transactions.
- Provider API keys are stored through the operating-system keyring when available.
- Settings, SQLite state, and cache data are stored locally.
- Backups intentionally exclude provider API keys.
- Custom JSON-RPC endpoint URLs are stored in the keyring and excluded from backups.
- Network requests are made only to configured data/pricing providers, the ENS resolver, and Blockscout when a user requests a verified ABI.

## Architecture

```text
PySide6 GUI
    |
    v
Core services and models
    |                 \
    v                  v
Provider adapters    SQLite event ledger/settings/cache
    |
    v
Alchemy / Ankr / local stub providers

Export services consume service results and write CSV or JSON.
```

The GUI does not call providers directly. Service operations that may access the network run through the shared Qt background task runner so provider latency does not block the event loop.

Activity and Token Detail store normalized transactions, events, asset movements, approvals, provider provenance, queried block windows, and resume checkpoints in SQLite. A partial synchronization can continue after restart without duplicating canonical events. Transaction Inspector enriches those canonical transactions with receipt status, calldata, decoded calls, events, reverts, ABI provenance, proxy implementation context, gas usage, raw logs, and receipt-derived network fees.

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

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the development workflow, [docs/ROADMAP.md](docs/ROADMAP.md) for product direction, and [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests.

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
- The application currently targets EVM-compatible chains supported by the configured providers.
- Debian compatibility targets and derivative distributions still require clean-system validation beyond the Ubuntu CI runners.
- ENS wallet input is available in Overview, Activity, and Token Detail; local metadata editors continue to use resolved hexadecimal addresses.
- Decoding currently covers bundled common token-standard signatures; unknown contract interactions remain available as raw calldata and logs.

## License

Oracle41 Open is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
