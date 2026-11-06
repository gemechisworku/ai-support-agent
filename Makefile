# Everyday commands. `make help` lists them. Python tooling runs through uv (`brew install uv`).
SHELL := /bin/bash
UV ?= uv
PY := $(UV) run
TASKS := python3 scripts/tasks.py

.DEFAULT_GOAL := help
.PHONY: help setup up down reset-db fmt lint typecheck boundaries test test-int test-all check \
        docker-build docker-test tasks-validate tasks-status tasks-next issues-sync issues-dry-run

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

setup: ## Install deps, create .env, start Postgres
	$(UV) sync
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")
	$(MAKE) up

.PHONY: doctor
doctor: ## Check local prerequisites; model servers optional until M2 (ARGS=--strict to require them)
	@bash scripts/doctor.sh $(ARGS)

up: ## Start Linux services (Postgres) and wait until healthy
	docker compose up -d --wait postgres

down: ## Stop all compose services
	docker compose --profile "*" down

reset-db: ## Delete the Postgres volume and recreate the databases (asks first)
	@read -p "Delete all local Postgres data? [y/N] " a && [ "$$a" = y ]
	docker compose down --volumes && $(MAKE) up

fmt: ## Auto-format and fix lint
	$(PY) ruff format . && $(PY) ruff check --fix .

lint: ## Ruff format check + lint
	$(PY) ruff format --check . && $(PY) ruff check .

typecheck: ## mypy on src/
	$(PY) mypy

boundaries: ## Enforce module import boundaries (import-linter)
	$(PY) lint-imports

test: ## Unit tests (no services needed)
	$(PY) pytest

test-int: up ## Integration tests (needs Postgres)
	$(PY) pytest -m integration

test-all: up ## Unit + integration tests with coverage
	$(PY) pytest -m "not eval and not live" --cov --cov-report=term-missing

.PHONY: migrate migrate-test
migrate: up ## Upgrade the bank and kb databases to head (Alembic, one env per database)
	$(PY) alembic -n bank upgrade head && $(PY) alembic -n kb upgrade head

migrate-test: up ## Upgrade bank_test and kb_test to head
	BANK_DB=bank_test KB_DB=kb_test $(MAKE) migrate

check: lint typecheck boundaries tasks-validate test-all ## Everything CI runs

docker-build: ## Build the runtime image
	docker build -f docker/app.Dockerfile --target runtime -t supportagent-app:dev .

docker-test: ## Run unit + integration tests inside Docker
	docker compose --profile test run --rm --build test

tasks-validate: ## Validate specs, tasks and traceability
	$(TASKS) validate

tasks-status: ## Print progress (specs/STATUS.md itself is regenerated on main by CI)
	$(TASKS) status

tasks-next: ## List tasks ready to start (ARGS="--milestone M1")
	$(TASKS) next $(ARGS)

.PHONY: milestone-pr
milestone-pr: ## Create (draft) or update the PR for branch milestone/$(M), e.g. make milestone-pr M=M1
	@test -n "$(M)" || (echo "usage: make milestone-pr M=M1" && exit 1)
	$(TASKS) milestone-pr $(M)

issues-dry-run: ## Show what issues-sync would create
	$(TASKS) sync-issues --dry-run

issues-sync: ## Create GitHub labels, milestones and issues for tasks (ARGS=--update refreshes bodies)
	$(TASKS) sync-issues $(ARGS)
