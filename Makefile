.PHONY: develop install hooks security update-security-baseline trivy test test-integration test-all lint format typecheck up down build clean

develop: install hooks
	@echo ""
	@echo "Done. Start services with: make up"

install:
	uv sync --all-extras

hooks:
	git config core.hooksPath .githooks
	@echo "Git hooks installed. Pre-commit and pre-push security scans are now active."

security:
	uv run python scripts/security_scan.py

update-security-baseline:
	uv run python scripts/update_security_baseline.py

trivy:
	trivy fs --exit-code 1 --severity CRITICAL,HIGH --ignore-unfixed .

test:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration -m integration --no-cov

test-all:
	uv run pytest tests/ --cov-report=xml:coverage.xml

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests

typecheck:
	uv run mypy src

up:
	docker compose up --build

down:
	docker compose down

build:
	docker buildx build --platform linux/amd64,linux/arm64 -t fscrawler:latest .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf dist/ build/ htmlcov/ .mypy_cache/ 2>/dev/null || true
