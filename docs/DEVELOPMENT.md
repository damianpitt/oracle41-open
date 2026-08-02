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
- Keep core/provider tests independent of a display server where possible.
- GUI changes should preserve the service boundary and loading/error states.

## GUI Changes

Network-backed operations must use `BackgroundTaskRunner`. Do not call provider or service methods that may perform network I/O directly from a button handler. Widget updates must happen through QObject-bound slots or signals on the GUI thread.

When adding a new view:

1. Add a service-level operation first.
2. Add unit tests for validation and result behavior.
3. Add the view with explicit loading, success, and error states.
4. Add fixture coverage for provider behavior where relevant.
5. Run `make check` before opening a pull request.

## Local Data

Tests should use temporary paths for SQLite, settings, and cache data. Never test against a personal installation database.
