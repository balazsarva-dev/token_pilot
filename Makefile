# Run `make help` for the list. Backend commands run inside backend/ via uv.
BACKEND = cd backend &&
ENV = set -a && [ -f .env ] && . ./.env; set +a;
TENANT ?= local
CLI = uv run python -m tokenpilot.cli --tenant $(TENANT)

.PHONY: help install db-up db-down db-reset migrate seed import sync report audit demo web web-dev test lint format run check

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Create the virtualenv and install dependencies (Python 3.12)
	$(BACKEND) uv sync --python 3.12

db-up: ## Start local Postgres (port 5433) and wait until healthy
	docker compose up -d --wait db

db-down: ## Stop local Postgres (data is kept)
	docker compose down

db-reset: ## Delete local Postgres data and start fresh
	docker compose down -v && docker compose up -d --wait db

migrate: ## Apply database migrations to the dev database
	$(BACKEND) $(ENV) uv run alembic upgrade head

seed: ## Load the model catalog and prices (backend/tokenpilot/pricing/prices.toml)
	$(BACKEND) $(ENV) $(CLI) seed-prices

import: ## Import CSV exports, e.g. make import FILES="../test_ai_costs/*.csv"
	$(BACKEND) $(ENV) $(CLI) import $(FILES)

sync: ## Pull new days from the admin APIs (keys in backend/.env); DAYS=45 re-syncs 45 days
	$(BACKEND) $(ENV) $(CLI) sync $(if $(DAYS),--days $(DAYS))

report: ## Print spend by model / key / project and the daily reconciliation
	$(BACKEND) $(ENV) $(CLI) report

audit: ## Savings audit, e.g. make audit TENANT=demo (MIN=0 shows even tiny savings)
	$(BACKEND) $(ENV) $(CLI) audit $(if $(MIN),--min-savings $(MIN))

demo: ## Load 30 days of synthetic demo usage into tenant "demo" (then: make audit TENANT=demo)
	$(BACKEND) $(ENV) uv run python -m tokenpilot.cli demo

web: ## Install and build the audit page (served by make run on http://localhost:5001)
	cd frontend && npm ci && npm run build

web-dev: ## Live-reloading page on http://localhost:5173 (needs make run in another terminal)
	cd frontend && npm run dev

test: ## Run the test suite (needs db-up)
	$(BACKEND) uv run pytest -q

lint: ## Check code style and common bugs
	$(BACKEND) uv run ruff check . && uv run ruff format --check .

format: ## Auto-format and auto-fix
	$(BACKEND) uv run ruff format . && uv run ruff check --fix .

run: ## Run the API and the built audit page on http://localhost:5001
	$(BACKEND) $(ENV) uv run flask --app "tokenpilot.web.app:create_app()" run --port 5001

check: lint test ## Everything CI would run (backend + frontend)
	cd frontend && npm run check
