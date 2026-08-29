.PHONY: setup test test-cov lint fmt typecheck security import-lint check infra-dev seed run-api clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/pre-commit install

test:
	$(VENV)/bin/pytest tests/unit -q

test-cov:
	$(VENV)/bin/pytest tests/unit --cov --cov-report=term-missing

lint:
	$(VENV)/bin/ruff check src tests

fmt:
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

typecheck:
	$(VENV)/bin/mypy src/core src/api src/functions

security:
	$(VENV)/bin/bandit -r src -q

import-lint:
	$(VENV)/bin/lint-imports

check: lint typecheck import-lint test-cov security

# --- Phase 2+ targets: require an Azure subscription and az CLI login ---
infra-dev:
	az deployment sub create \
		--location "$${AZURE_LOCATION:-centralindia}" \
		--template-file infra/main.bicep \
		--parameters infra/main.dev.bicepparam

seed:
	$(PY) scripts/seed_data.py

run-api:
	$(VENV)/bin/uvicorn api.main:app --reload --port 8000 --app-dir src

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
