# M5 Validation Matrix

This page records the tests that close the M5 transaction understanding milestone. The tests use local fixtures and do not call live providers.

## Deterministic Output

| Requirement | Test coverage | Expected fallback |
| --- | --- | --- |
| The same receipt produces the same decoding through Alchemy and Ankr endpoints. | `tests/integration/test_provider_http_fixtures.py::test_shared_receipt_fixture_decodes_identically_across_providers` | Provider and fetch metadata remain provenance only. |
| The same evidence produces the same normalized actions. | The shared receipt test and `tests/unit/test_action_normalizer.py::test_same_evidence_is_provider_independent` | Unknown evidence becomes an explicit unknown action. |
| Every known signature identifies its source. | `tests/unit/test_abi_decoder.py::test_decodes_erc20_transfer_call_with_bundled_provenance` | Unknown signatures keep the raw selector or topic. |

## Incomplete and Invalid Data

| Condition | Test coverage | Visible result |
| --- | --- | --- |
| Unknown call or event | `test_unknown_and_malformed_calls_are_distinct`, `test_unknown_and_malformed_events_are_distinct_and_deterministic`, and `test_unknown_call_remains_explicit_and_keeps_raw_reference` | Raw selector, topic, and input remain inspectable. |
| Known signature with malformed arguments | `test_malformed_known_call_remains_explicit_with_signature_provenance` | The action stays explicit and keeps the known canonical signature. |
| Malformed receipt log | `test_evm_rpc_provider_rejects_malformed_logs` | Inspection fails with a structured provider response error instead of saving partial receipt data. |
| Missing or partial trace | Transaction inspection and action export completeness tests | Receipt actions remain available and missing internal evidence is stated. |
| Unsupported trace method | `test_trace_discovery_reports_unsupported_capability` | Trace support is marked unavailable without hiding receipt evidence. |

## Provider Failures and Capabilities

| Condition | Test coverage | Visible result |
| --- | --- | --- |
| Authentication failure | EVM JSON-RPC, Alchemy, and Ankr provider tests | A structured authentication error is shown without exposing endpoint secrets. |
| Rate limit or timeout | Provider retry and HTTP fixture tests | Retry policy runs first, then returns a structured error. |
| Temporary trace failure | `test_trace_failover_does_not_hide_temporary_failure_as_unsupported` | A temporary failure remains an error and is not cached as unsupported. |
| Historical state available | `test_evm_rpc_provider_resolves_eip1167_minimal_proxy` | Transaction Inspector reports historical state as available after a successful block-specific read. |
| Historical state pruned | `test_evm_rpc_provider_learns_when_historical_state_is_pruned` | Transaction Inspector reports historical state as unavailable. |
| Historical state not checked | Capability tests before proxy or revert replay | Transaction Inspector reports that support has not been checked. |

## Optional Dependencies

- Alchemy, Ankr, and custom JSON-RPC endpoints share the same canonical transaction models.
- Blockscout adds optional ABI and explorer context. Local decoding and actions work without it.
- TrueBlocks is not required. The current decision and future integration boundary are in [ADR 0002](adr/0002-trueblocks-optional-local-index.md).
- Internal traces and historical state are optional endpoint capabilities. Receipt inspection remains useful when either capability is missing.
