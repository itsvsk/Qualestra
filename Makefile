.PHONY: bootstrap install test test-integration lint typecheck check release-check up health down benchmark demo reliability

bootstrap:
	./scripts/bootstrap.sh

install:
	uv sync --locked --extra dev

test:
	uv run pytest

test-integration:
	uv run pytest tests/integration tests/e2e

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy sentinelqa apps atlas scripts

check: lint typecheck test

release-check:
	uv run python scripts/release_check.py

up:
	docker compose up --build --wait

health:
	curl --fail --silent http://localhost:8000/health/ready
	curl --fail --silent http://localhost:5173/health/ready
	curl --fail --silent http://localhost:3000/health/ready

down:
	docker compose down

benchmark:
	uv run python scripts/run_benchmark.py --profile all --seeds 41021,41022,41023

demo:
	ATLAS_FAULT_PROFILE=all docker compose up --build --wait

reliability:
	docker compose --profile reliability run --rm k6
