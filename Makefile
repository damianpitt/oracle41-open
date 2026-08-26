PYTHON ?= python3

.PHONY: dev-setup test lint typecheck check validate-providers-live

dev-setup:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest tests -q

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src

check: lint typecheck test

validate-providers-live:
	$(PYTHON) -m oracle41_open.app.main --validate-providers-live
