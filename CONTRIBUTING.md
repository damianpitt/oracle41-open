# Contributing to Oracle41 Open

Thanks for contributing.

## Ground Rules

- Keep changes scoped and reviewable.
- Prefer small PRs over large refactors.
- Preserve local-first and BYO key principles.
- Do not add telemetry that sends user data externally.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Development Workflow

1. Create an issue (or confirm an existing one).
2. Create a branch from `main`.
3. Implement and test.
4. Open a PR with a clear summary and screenshots for UI changes.

## Quality Checks (Required)

```bash
pytest
ruff check .
mypy src
```

## PR Checklist

- [ ] Tests added or updated where relevant
- [ ] Lint and type checks pass locally
- [ ] No secrets committed
- [ ] User-facing behavior documented in PR description
- [ ] Breaking changes clearly called out

## Coding Notes

- Keep `core/` free of UI framework imports.
- Keep provider adapters isolated in `providers/`.
- Handle network failures and rate limits explicitly.
- Use deterministic formatting and explicit error messages.

## Reporting Bugs

Please include:

- OS and Python version
- Repro steps
- Expected vs actual behavior
- Relevant logs/traceback
