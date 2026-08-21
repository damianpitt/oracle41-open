# Configuration

## Provider Keys

Open the Settings tab and enter one or both provider keys:

- Alchemy API key
- Ankr API key

The keys are stored using the system keyring when available. They are not included in backup exports.

The Wallet Data Providers section controls which configured provider is enabled and which one has priority 1. Priority 1 is tried first for a new request. A disabled provider is not added to the automatic wallet-data, pricing, or transaction-inspection runtime. Restart the application after changing enablement or priority.

If the first provider returns a structured provider error, a new request can try the next enabled provider. A continuation page does not fail over because its cursor belongs to the provider that created it.

Headless or temporary environments can provide keys without writing to the keyring:

```bash
export ORACLE41_ALCHEMY_API_KEY="..."
export ORACLE41_ANKR_API_KEY="..."
oracle41-open
```

A key stored in the system keyring takes precedence over the corresponding environment variable.

### Planned Provider Expansion

Version `0.4.0a2` provides ordered selection for Alchemy and Ankr. Moralis and GoldRush remain planned and are not available in the current release.

Later M6.2 updates will add:

- Credential fields and validation for Moralis and GoldRush.
- One ordered list across all four wallet-data providers.
- Chain and feature capability details for each provider.
- Recorded fixtures and shared conformance tests for the new adapters.

Provider selection will apply to indexed wallet balances and history. Custom JSON-RPC endpoints will remain separate because standard JSON-RPC does not provide a complete address-history index by itself.

## Custom JSON-RPC Endpoints

Settings can store one standard JSON-RPC endpoint per chain. Custom endpoints take priority for Transaction Inspector and are kept in the OS keyring because URLs may contain credentials. They are excluded from backup exports, and changes require an application restart.

Headless or temporary environments can use:

```bash
export ORACLE41_RPC_ETHEREUM_URL="https://..."
export ORACLE41_RPC_OPTIMISM_URL="https://..."
export ORACLE41_RPC_POLYGON_URL="https://..."
export ORACLE41_RPC_BASE_URL="https://..."
export ORACLE41_RPC_ARBITRUM_URL="https://..."
```

A keyring endpoint takes precedence over its environment variable. Treat endpoint URLs as secrets when they contain embedded API keys.

Without a live key, the application uses local stub providers so the interface can be explored without network access.

### Internal Transaction Traces

Transaction Inspector checks each configured endpoint for one of two common trace methods:

- Geth-compatible `debug_traceTransaction` with the call tracer
- Parity-compatible `trace_transaction`

Many public endpoints disable these methods or place them behind a paid plan. Oracle41 Open reports this as unsupported instead of showing an empty execution history. Temporary provider errors are reported as unavailable and retried later. Partial responses remain marked as partial.

## Contract ABIs

The Contract ABIs section in Settings accepts a chain, contract or proxy address, optional name, and ABI JSON file. Local files are stored in SQLite and explicitly marked as unverified. A saved ABI is used only for matching addresses.

The Fetch Verified ABI action queries the public Blockscout API v2 instance for the selected chain. A result is stored only when Blockscout marks the contract verified, and its explorer reference is retained as signature provenance. This lookup is user-initiated; it is not performed automatically while browsing transactions.

ABI replacement changes the decoder fingerprint. The next transaction inspection re-decodes cached raw data with the new ABI. Removing an ABI does not delete raw transaction, receipt, log, or revert bytes.

## ENS Input

Overview, Activity, and Token Detail accept either a hexadecimal wallet address or an ENS name ending in `.eth`. Resolution runs outside the GUI thread and successful and unsuccessful lookups are cached. The resolved address is used for provider and ledger operations while the entered ENS name is retained for display context.

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

Backups contain local settings and all SQLite state, including canonical events, transaction receipts, raw logs, internal traces, normalized actions, decoded calls/events/reverts, contract ABIs, proxy resolutions, signature provenance, fees, and synchronization checkpoints. They do not contain provider API keys, custom RPC URLs, or the provider cache. Restore should be used with the application closed or with no concurrent state-changing operation.
