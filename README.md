# Oracle41 Open

Oracle41 Open is a Linux-first desktop application for read-only EVM wallet analytics. It gives users a local workspace for inspecting wallet balances, activity, token details, portfolios, notes, snapshots, and exports without handling private keys or signing transactions.

This project ports a macOS wallet analytics application to Linux. It is implemented in Python and PySide6, not translated line by line from Swift.

## Alpha Status

Version `0.1.0` is an alpha release. It includes wallet analytics, provider integrations, local persistence, exports, tests, and Debian packaging. Validate live provider behavior and Debian installation on each supported Ubuntu or Debian version before relying on it.

## Features

- Wallet overview with native and ERC-20 balances
- Portfolio pricing enrichment with cached last-known values
- Ethereum, Optimism, Polygon, Base, and Arbitrum support
- Activity feed with transfers, pagination, lookback, and filters
- ERC-20, ERC-721, and ERC-1155 token detail flows with recent approval events
- Alchemy and Ankr providers with failover support
- Retry/backoff and structured rate-limit, timeout, and authentication errors
- ENS and address-label resolution with caching
- Token filtering for unverified, low-signal, and dust assets
- Watchlists and multi-wallet portfolio aggregation
- Notes, tags, saved views, and snapshot comparison
- Cache telemetry, diagnostics, refresh, and clear-cache controls
- CSV and JSON exports for activity, portfolios, watchlists, and snapshots
- Backup and restore for local settings and SQLite state
- Self-contained Debian package with desktop launcher and AppStream metadata for Ubuntu/Debian

## Privacy and Scope

- The application is read-only.
- It does not request, store, or use private keys.
- It does not sign or broadcast transactions.
- Provider API keys are stored through the operating-system keyring when available.
- Settings, SQLite state, and cache data are stored locally.
- Backups intentionally exclude provider API keys.
- Network requests are made only to configured data and pricing providers.

## Architecture

```text
PySide6 GUI
    |
    v
Core services and models
    |                 \
    v                  v
Provider adapters    SQLite/settings/cache storage
    |
    v
Alchemy / Ankr / local stub providers

Export services consume service results and write CSV or JSON.
```

The GUI does not call providers directly. Service operations that may access the network run through the shared Qt background task runner so provider latency does not block the event loop.

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

Provider keys can be configured from the Settings tab.

## Install the Debian Package

On Ubuntu or Debian, download the matching `.deb` release asset and either double-click it in the file manager or install it with:

```bash
sudo apt install ./oracle41-open_<version>_<arch>.deb
```

The package includes its Python runtime, creates the `oracle41-open` command, and installs a desktop application entry. The first release target is AMD64 Ubuntu 22.04 or newer and Debian 12 or newer.

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

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the development workflow and [CONTRIBUTING.md](CONTRIBUTING.md) for pull requests.

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
- Linux desktop packaging still needs validation on each supported distribution release.

## License

Oracle41 Open is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
