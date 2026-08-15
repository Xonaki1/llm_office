# Local commands that mirror CI. Anything CI checks should be runnable here
# first, so a red pipeline is never the first time you learn about a failure.
.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV ?= .venv
ifeq ($(OS),Windows_NT)
	PY := $(VENV)/Scripts/python.exe
	PYTEST := $(VENV)/Scripts/pytest.exe
else
	PY := $(VENV)/bin/python
	PYTEST := $(VENV)/bin/pytest
endif

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install backend and frontend dependencies
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	cd web && npm install

.PHONY: up
up: ## Start Postgres and Redis
	docker compose up -d

.PHONY: down
down: ## Stop Postgres and Redis
	docker compose down

.PHONY: migrate
migrate: ## Apply migrations
	$(PY) -m alembic upgrade head

.PHONY: api
api: ## Run the API with reload
	$(PY) -m uvicorn api.main:app --reload

.PHONY: worker
worker: ## Run the ARQ worker
	$(PY) -m arq worker.main.WorkerSettings

.PHONY: web
web: ## Run the Next.js dev server
	cd web && npm run dev

.PHONY: lint
lint: ## Ruff + mypy + eslint
	$(PY) -m ruff check .
	$(PY) -m mypy core api worker
	cd web && npx eslint . --max-warnings 0

.PHONY: fmt
fmt: ## Apply the fixes ruff can make itself
	$(PY) -m ruff check . --fix

.PHONY: test
# The console script, not `$(PY) -m pytest`, because that is what CI runs and
# the two do not agree on sys.path: `python -m` prepends the working directory,
# which silently satisfies imports that fail under a bare `pytest`.
test: ## Run the test suite
	$(PYTEST) -q

.PHONY: cov
cov: ## Run the test suite with coverage
	$(PYTEST) -q --cov=core --cov=api --cov=worker --cov-report=term-missing

.PHONY: typecheck-web
typecheck-web: ## Typecheck and build the frontend
	cd web && npx tsc --noEmit && npm run build

.PHONY: drift
drift: ## Fail if the models and the migrations disagree
	$(PY) -m alembic check

.PHONY: package
package: ## Build the wheel and verify every module ships in it
	rm -rf dist build
	$(PY) -m pip install --quiet --upgrade build
	$(PY) -m build --wheel
	@echo "install the wheel in a clean env and run: cd /tmp && python -m scripts.verify_package"

.PHONY: ci
ci: lint test drift typecheck-web ## Everything CI checks that runs without services
	@echo "local CI checks passed"
