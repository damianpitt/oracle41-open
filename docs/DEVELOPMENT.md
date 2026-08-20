# Development Guide

## Environment

Use a virtual environment with Python 3.11 or 3.12:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Checks

Run the complete local quality gate:

```bash
make check
```

The gate runs:

- `pytest` for unit and fixture-backed integration tests
- `ruff check .` for linting and import ordering
- `mypy src` for strict type checking

## Testing Rules

- Provider tests must use deterministic HTTP fixtures or in-memory fakes.
- Do not put real API keys in tests, fixtures, logs, or issue reports.
- Add regression coverage for cache freshness, pagination, filtering, and provider error behavior.
- Verify database migrations, idempotent event ingestion, checkpoint recovery, and export provenance.
- Give every protocol adapter complete, partial, malformed, and unknown recorded fixtures at an exact block.
- Keep core/provider tests independent of a display server where possible.
- GUI changes should preserve the service boundary and loading/error states.

## Code Documentation

- Start every Python file with a short module docstring.
- Explain what the file owns, what it does, and any important boundary or safety rule.
- Use simple English and keep the header short enough to scan before the imports.
- Add comments before non-obvious logic when the reason is not clear from the code.
- Explain why a special case exists. Do not write comments that only repeat the next line.
- Update a header when the main responsibility of its file changes.

Protocol adapter contributions must also follow [PROTOCOL_ADAPTERS.md](PROTOCOL_ADAPTERS.md).

## GUI Changes

Network-backed operations must use `BackgroundTaskRunner`. Do not call provider or service methods that may perform network I/O directly from a button handler. Widget updates must happen through QObject-bound slots or signals on the GUI thread. Cancellation suppresses late task results; workers are not terminated unsafely, and a ledger transaction already committed by a canceled task remains valid.

When adding a new view:

1. Add a service-level operation first.
2. Add unit tests for validation and result behavior.
3. Add the view with explicit loading, success, and error states.
4. Add fixture coverage for provider behavior where relevant.
5. Run `make check` before opening a pull request.

## Local Data

Tests should use temporary paths for SQLite, settings, and cache data. Never test against a personal installation database.
