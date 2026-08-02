# Configuration

## Provider Keys

Open the Settings tab and enter one or both provider keys:

- Alchemy API key: primary provider when configured
- Ankr API key: fallback provider or primary provider when Alchemy is absent

The keys are stored using the system keyring when available. They are not included in backup exports.

Headless or temporary environments can provide keys without writing to the keyring:

```bash
export ORACLE41_ALCHEMY_API_KEY="..."
export ORACLE41_ANKR_API_KEY="..."
oracle41-open
```

A key stored in the system keyring takes precedence over the corresponding environment variable.

Without a live key, the application uses local stub providers so the interface can be explored without network access.

## Preferences

The Settings tab controls:

- Default chain
- Unverified and low-signal token visibility
- Dust-token filtering and threshold
- Wallet token-page cap
- Wallet, activity, and token-detail cache TTLs
- Maximum stale pricing age
- Maximum cache size

## Local Files

The application uses platformdirs to resolve these paths:

- Configuration: `user_config_dir("oracle41-open")`
- Application data: `user_data_dir("oracle41-open")`
- Cache: `user_cache_dir("oracle41-open")`

The resolved paths can differ when `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, or `XDG_CACHE_HOME` are set.

## Backup and Restore

Backups contain local settings and SQLite state. They do not contain provider API keys or the provider cache. Restore should be used with the application closed or with no concurrent state-changing operation.
